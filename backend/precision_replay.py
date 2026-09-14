"""Replay auditável e somente leitura das políticas de produtividade.

O módulo não consulta nem grava no banco. Recebe as linhas lidas pelo endpoint,
reconstrói o mesmo proxy humano usado nos notebooks 10–12 e reaplica R1/R95.
O manifesto fixa dias, catálogo e fingerprint do conjunto; se o banco mudar, a
resposta continua útil, mas denuncia que já não é o conjunto oficial.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import time
import unicodedata
from typing import Any

from .productivity import MIN_AMOSTRAS_IMPRODUTIVIDADE, ROTULOS_ACAO_INDEFINIDA


P = "PRODUTIVO"
I = "IMPRODUTIVO"
A = "ABSTEM"
_CLASSES = (P, I, A)
_RESOURCE = Path(__file__).with_name("resources") / "productivity_replay_v1.json"


def normalizar(valor: Any) -> str:
    if valor is None:
        return ""
    try:
        if math.isnan(valor):
            return ""
    except (TypeError, ValueError):
        pass
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "_", texto).strip("_")


def _bool(valor: Any) -> bool:
    if valor is True:
        return True
    return normalizar(valor) in {"true", "1", "sim", "yes"}


def scope_fingerprint(empresa: str, processo: str) -> str:
    escopo = f"{normalizar(empresa)}|{normalizar(processo)}"
    return hashlib.sha256(escopo.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def carregar_manifesto() -> dict[str, Any]:
    return json.loads(_RESOURCE.read_text(encoding="utf-8"))


def _classe_categoria(categoria: Any) -> str | None:
    categoria = normalizar(categoria)
    if categoria == "valor_agregado":
        return P
    if categoria == "desperdicio":
        return I
    return None


def preparar_avaliacao(
    eventos: list[dict[str, Any]], manifesto: dict[str, Any]
) -> list[dict[str, Any]]:
    """Seleciona o conjunto humano dos dias fixados, sem usar texto livre."""
    dias = set(manifesto["days"])
    catalogo = {
        normalizar(label): categoria
        for label, categoria in manifesto["frozen_categories"].items()
    }
    linhas: list[dict[str, Any]] = []
    for evento in eventos:
        dia = str(evento.get("gravado_em") or "")[:10]
        if dia not in dias:
            continue
        if normalizar(evento.get("origem_validacao")) != "humano":
            continue
        if not _bool(evento.get("validado_humano")):
            continue
        if not _bool(evento.get("validacao_correto")):
            continue

        label_pred = normalizar(evento.get("comportamento_label"))
        label_corrigido = normalizar(evento.get("label_corrigido"))
        label_true = label_corrigido or label_pred
        baseline = _classe_categoria(catalogo.get(label_pred))
        verdade = _classe_categoria(catalogo.get(label_true))
        if baseline is None or verdade is None:
            continue

        try:
            amostras = int(evento.get("n_amostras"))
        except (TypeError, ValueError):
            amostras = None
        try:
            inicio = float(evento.get("tempo_inicio_s") or 0)
            fim = float(evento.get("tempo_fim_s") or inicio)
        except (TypeError, ValueError):
            inicio, fim = 0.0, 0.0
        try:
            track = int(evento.get("pessoa_track_id") or 0)
        except (TypeError, ValueError):
            track = 0

        r1 = A if label_pred in ROTULOS_ACAO_INDEFINIDA else baseline
        atual = r1
        motivo = "Decisão preservada pela regra atual"
        if label_pred in ROTULOS_ACAO_INDEFINIDA:
            if normalizar(evento.get("papel_pessoa")) == "operador" and _bool(
                evento.get("maos_maquina")
            ):
                atual = P
                motivo = "Ação sem nome, mas com mãos na máquina"
            else:
                motivo = "Ação sem nome enviada para validação"
        if atual == I and (
            _bool(evento.get("em_duvida"))
            or (amostras is not None and amostras < MIN_AMOSTRAS_IMPRODUTIVIDADE)
        ):
            atual = A
            motivo = "Improdutividade sem evidência suficiente enviada para validação"

        linhas.append(
            {
                "event_id": str(evento.get("id") or ""),
                "video_id": str(evento.get("video_id") or ""),
                "video_name": str(evento.get("video_nome") or "Vídeo"),
                "day": dia,
                "version": int(evento.get("versao_instrumento") or 0),
                "label": label_pred,
                "truth": verdade,
                "baseline": baseline,
                "r1": r1,
                "current": atual,
                "reason": motivo,
                "sample_count": amostras,
                "in_doubt": _bool(evento.get("em_duvida")),
                "person_track_id": track,
                "start_s": inicio,
                "end_s": fim,
            }
        )
    linhas.sort(key=lambda x: (x["day"], x["video_id"], x["start_s"], x["event_id"]))
    return linhas


def _metricas_de_confusao(confusao: dict[str, dict[str, int]]) -> dict[str, Any]:
    def valor(verdade: str, predicao: str) -> int:
        return int(confusao.get(verdade, {}).get(predicao, 0))

    tp_i = valor(I, I)
    fp_i = valor(P, I)
    tp_p = valor(P, P)
    fp_p = valor(I, P)
    total = sum(valor(v, p) for v in (P, I) for p in _CLASSES)
    classificados = sum(valor(v, p) for v in (P, I) for p in (P, I))

    def pct(num: int, den: int) -> float | None:
        return round(100 * num / den, 2) if den else None

    return {
        "n": total,
        "precision_improductive_pct": pct(tp_i, tp_i + fp_i),
        "precision_productive_pct": pct(tp_p, tp_p + fp_p),
        "coverage_pct": pct(classificados, total),
        "claims_improductive": tp_i + fp_i,
        "false_improductive": fp_i,
        "confusion": confusao,
    }


def calcular_metricas(linhas: list[dict[str, Any]], campo: str) -> dict[str, Any]:
    confusao = {verdade: {pred: 0 for pred in _CLASSES} for verdade in (P, I)}
    for linha in linhas:
        confusao[linha["truth"]][linha[campo]] += 1
    return _metricas_de_confusao(confusao)


def _exemplos(linhas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seletores = (
        ("Falsa improdutividade evitada", lambda x: x["truth"] == P and x["r1"] == I and x["current"] == A),
        ("Dúvida enviada para validação", lambda x: x["truth"] == P and x["baseline"] == I and x["current"] == A),
        ("Produtividade recuperada por evidência", lambda x: x["truth"] == P and x["r1"] == A and x["current"] == P),
        ("Improdutividade correta preservada", lambda x: x["truth"] == I and x["current"] == I),
        ("Erro atual visível", lambda x: x["truth"] == P and x["current"] == I),
    )
    exemplos: list[dict[str, Any]] = []
    usados: set[str] = set()
    for grupo, seletor in seletores:
        for linha in (x for x in linhas if seletor(x)):
            if linha["event_id"] in usados:
                continue
            item = dict(linha)
            item["group"] = grupo
            exemplos.append(item)
            usados.add(linha["event_id"])
            if sum(1 for x in exemplos if x["group"] == grupo) >= 3:
                break
    return exemplos


def executar_replay(
    eventos: list[dict[str, Any]], *, code_version: str = "local"
) -> dict[str, Any]:
    inicio = time.perf_counter()
    manifesto = carregar_manifesto()
    linhas = preparar_avaliacao(eventos, manifesto)
    ids_hash = hashlib.sha256(
        "\n".join(sorted(x["event_id"] for x in linhas)).encode("utf-8")
    ).hexdigest()
    historico = _metricas_de_confusao(manifesto["first_measurement"]["confusion"])
    historico.update(
        {
            "id": "first",
            "label": manifesto["first_measurement"]["label"],
            "days": manifesto["first_measurement"]["days"],
            "note": manifesto["first_measurement"]["note"],
        }
    )
    r1 = calcular_metricas(linhas, "r1")
    r1.update({"id": "r1", "label": "Antes da combinação R1 + R95", "days": len(set(x["day"] for x in linhas))})
    atual = calcular_metricas(linhas, "current")
    atual.update({"id": "current", "label": "Regras atuais R1 + R95", "days": len(set(x["day"] for x in linhas))})
    contagem_versoes = Counter(x["version"] for x in linhas)
    integridade = (
        len(linhas) == int(manifesto["expected_events"])
        and ids_hash == manifesto["event_set_sha256"]
    )
    return {
        "ok": True,
        "status": "concluido",
        "elapsed_ms": round((time.perf_counter() - inicio) * 1000, 1),
        "code_version": code_version,
        "manifest": {
            "id": manifesto["id"],
            "title": manifesto["title"],
            "created_at": manifesto["created_at"],
            "sha256": hashlib.sha256(_RESOURCE.read_bytes()).hexdigest(),
            "source_sha256": manifesto["sources_sha256"],
        },
        "dataset": {
            "matches_frozen_manifest": integridade,
            "expected_events": manifesto["expected_events"],
            "actual_events": len(linhas),
            "event_set_sha256": ids_hash,
            "days": sorted(set(x["day"] for x in linhas)),
            "versions": {str(k): v for k, v in sorted(contagem_versoes.items())},
            "ground_truth": manifesto["ground_truth"],
        },
        "results": [historico, r1, atual],
        "examples": _exemplos(linhas),
    }
