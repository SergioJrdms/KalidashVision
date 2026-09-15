"""Small trainable text/context heads. CPU only; predictions are SHADOW data.

Human annotations remain authoritative and durable in the existing database.
Replaying a bounded fresh snapshot withdraws reopened/invalid annotations and
survives restarts without writing model files or introducing a new schema.
Softmax scores are NOT calibrated confidence or measured precision.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field

_CACHE = OrderedDict()
_LOCK = threading.RLock()
_SEM_ACAO = {"acao_indefinida", "acao_nao_identificada", "nao_nomeado",
             "posto_vazio", "operador_fora", "sem_leitura", "sem_decisao"}
_CATS = {"valor_agregado": "PRODUTIVO", "desperdicio": "IMPRODUTIVO"}


def _normalizar(text):
    text = unicodedata.normalize("NFKD", str(text or ""))
    return " ".join(re.findall(r"[a-z0-9]+", "".join(
        c for c in text.lower() if not unicodedata.combining(c))))


def atributos(evento, tarefa):
    # No camera/track identity, durations, frozen predictions or human targets.
    words = _normalizar(evento.get("descricao_bruta"))[:800].split()[:80]
    keys = {"t:" + w for w in words}
    keys.update("b:" + a + " " + b for a, b in zip(words, words[1:]))
    for key in ("maquina", "imovel", "trabalho", "orientacao", "maos_maquina"):
        if evento.get(key) is not None:
            keys.add(key + ":" + _normalizar(evento[key]))
    if tarefa == "lean":
        # Input activity is available at inference; NOT the corrected target.
        label = evento.get("label_corrigido") or evento.get("comportamento_label")
        if label:
            keys.add("atividade:" + _normalizar(label))
    scale = 1 / math.sqrt(max(1, len(keys)))
    return {k: scale for k in sorted(keys)}


@dataclass
class Classificador:
    classes: tuple
    pesos: dict = field(default_factory=dict)
    exemplos: int = 0

    def distribuicao(self, x):
        if len(self.classes) < 2:
            return {}
        logits = {c: sum(self.pesos.get(c, {}).get(k, 0) * v
                         for k, v in x.items()) for c in self.classes}
        peak = max(logits.values())
        values = {c: math.exp(v - peak) for c, v in logits.items()}
        total = sum(values.values())
        return {c: v / total for c, v in values.items()}

    def partial_fit(self, x, y, taxa=0.35):
        """One real softmax cross-entropy SGD update, not a lookup table."""
        if y not in self.classes or len(self.classes) < 2 or not x:
            return
        probs = self.distribuicao(x)
        for c in self.classes:
            weights = self.pesos.setdefault(c, {})
            error = float(c == y) - probs[c]
            for k, v in x.items():
                weights[k] = weights.get(k, 0) + taxa * error * v
        self.exemplos += 1


def treinar(eventos, comportamentos=()):
    """Separate targets; ambiguity excludes a sample, never a majority vote."""
    humanos = [e for e in eventos if e.get("origem_validacao") == "humano"
               and e.get("validado_humano") is True]
    invalidas = {_normalizar(e.get("descricao_bruta")) for e in humanos
                 if e.get("descricao_invalida") is True}
    categorias = {c["label"]: _CATS[c["categoria_lean"]] for c in comportamentos
                  if c.get("categoria_lean_origem") == "humano"
                  and c.get("categoria_lean") in _CATS and c.get("label")}
    samples = {"cards": {}, "lean": {}}
    for e in sorted(humanos, key=lambda r: (str(r.get("validado_em") or ""), str(r.get("id") or ""))):
        label = str(e.get("label_corrigido") or e.get("comportamento_label") or "")
        desc = _normalizar(e.get("descricao_bruta"))
        if (e.get("validacao_correto") is not True or e.get("descricao_invalida")
                or not desc or desc in invalidas or not label or label in _SEM_ACAO):
            continue
        targets = {"cards": label, "lean": e.get("produtividade_humana") or categorias.get(label)}
        for task, target in targets.items():
            if not target or (task == "lean" and target not in {"PRODUTIVO", "IMPRODUTIVO"}):
                continue
            x = atributos(e, task)
            key = tuple(x)
            entry = samples[task].setdefault(key, (x, set()))
            entry[1].add(target)
    # Catalog judgments train only activity features, not invented visual examples.
    for label, target in sorted(categorias.items()):
        if label not in _SEM_ACAO:
            x = atributos({"comportamento_label": label}, "lean")
            samples["lean"].setdefault(tuple(x), (x, set()))[1].add(target)
    models = {}
    for task, entries in samples.items():
        clean = [(x, next(iter(ys))) for x, ys in entries.values() if len(ys) == 1]
        classes = tuple(sorted({y for _, y in clean}))
        if task == "lean" and clean:
            classes = ("IMPRODUTIVO", "PRODUTIVO")
        model = Classificador(classes)
        # Bounded deterministic replay retains previous examples on new corrections.
        for _ in range(8):
            for x, y in clean:
                model.partial_fit(x, y)
        models[task] = model
    return models


def atualizar(empresa, processo, eventos, comportamentos=()):
    """Atomic cache replacement. Scope is the backend-authorized company/process."""
    digest = hashlib.sha256(json.dumps([eventos, list(comportamentos)],
        sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    key = (empresa, processo)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] == digest:
            _CACHE.move_to_end(key)
            return cached[1]
    models = treinar(eventos, comportamentos)
    with _LOCK:
        _CACHE[key] = (digest, models)
        _CACHE.move_to_end(key)
        while len(_CACHE) > 128:
            _CACHE.popitem(last=False)
    return models


def invalidar(empresa, processo):
    with _LOCK:
        _CACHE.pop((empresa, processo), None)


def sugerir(empresa, processo, evento):
    """Internal diagnostics ONLY. Never a human validation, KPI or presence vote."""
    with _LOCK:
        cached = _CACHE.get((empresa, processo))
    if not cached or evento.get("papel_pessoa") not in {"operador", "operador_fora"}:
        return None
    result = {"modo": "shadow", "versao": "linear-sgd-v1", "snapshot": cached[0]}
    for task, model in cached[1].items():
        probs = model.distribuicao(atributos(evento, task))
        result[task] = {"sugestao": max(probs, key=probs.get) if probs else None,
                        "scores_nao_calibrados": dict(sorted(probs.items(),
                            key=lambda item: item[1], reverse=True)[:3]),
                        "atualizacoes_sgd": model.exemplos}
    return result
