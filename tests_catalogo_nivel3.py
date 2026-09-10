#!/usr/bin/env python3
"""Fase 114 — suíte da regra do catálogo no nível 3.

Roda com a flag LIGADA e DESLIGADA na mesma execução (recarregando o módulo),
porque a garantia mais importante desta fase não é o ganho: é que **desligada,
nada muda**. Se um único caso divergir com a flag off, o patch não pode subir.

    python tests_catalogo_nivel3.py
"""
from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ── stubs das dependências pesadas (mesmo bloco das outras suítes) ──────
for _nome, _attrs in (
    ("cv2", {"pointPolygonTest": lambda *a, **k: 0.0}),
    ("numpy", {"array": lambda *a, **k: a[0] if a else None,
               "int32": int, "ndarray": object}),
    ("supabase", {"Client": object, "create_client": lambda *a, **k: None}),
    ("groq", {"Groq": object}),
):
    if _nome not in sys.modules:
        m = types.ModuleType(_nome)
        for k, v in _attrs.items():
            setattr(m, k, v)
        sys.modules[_nome] = m

ok = falhas = 0


def chk(cond, msg):
    global ok, falhas
    if cond:
        ok += 1
    else:
        falhas += 1
        print(f"  ✗ {msg}")


def carregar(flag: str):
    """Recarrega o pipeline com KV_CATALOGO_NIVEL3 no valor pedido."""
    if flag is None:
        os.environ.pop("KV_CATALOGO_NIVEL3", None)
    else:
        os.environ["KV_CATALOGO_NIVEL3"] = flag
    for k in ("KV_ORIENTACAO_VERIFICADA",):
        os.environ.pop(k, None)
    os.environ.setdefault("KV_FORA_DO_POSTO", "on")
    if "backend.pipeline" in sys.modules:
        return importlib.reload(sys.modules["backend.pipeline"])
    from backend import pipeline
    return pipeline


def ev(**kw):
    """Evento no posto (papel operador), que é quem chega ao nível 3."""
    base = {
        "papel_pessoa": "operador",
        "comportamento_label": "monitorar_maquina",
        "label_corrigido": None,
        "trabalho": None,
        "orientacao": None,
        "maos_maquina": None,
        "modo_operacao": None,
        "categoria_lean": None,
        "categoria_lean_origem": None,
        "origem_validacao": None,
        "validado_humano": None,
        "validacao_correto": None,
        "descricao_bruta": "parado observando a maquina",
        "tempo_inicio_s": 0.0,
        "tempo_fim_s": 60.0,
        "_cat_humana": None,
        "_cat_humana_origem": None,
    }
    base.update(kw)
    return base


# ═══════════════════════════════════════════════════════════════════════
# BLOCO 1 — FLAG DESLIGADA: nada muda. É a garantia que permite subir.
# ═══════════════════════════════════════════════════════════════════════
print("── flag DESLIGADA (padrão) ──")
pl = carregar(None)
chk(pl._CATALOGO_NIVEL3 is False, "a flag tinha de nascer desligada")

CASOS_OFF = [
    ("trabalho=True  → produtivo por julgamento",
     ev(trabalho=True, _cat_humana="desperdicio", _cat_humana_origem="humano"),
     "valor_agregado", "julgamento"),
    ("trabalho=False → desperdicio por julgamento",
     ev(trabalho=False, _cat_humana="valor_agregado", _cat_humana_origem="humano"),
     "desperdicio", "julgamento"),
    ("trabalho=None  → duvida",
     ev(trabalho=None, _cat_humana="valor_agregado", _cat_humana_origem="humano"),
     "desperdicio", "duvida"),
]
for nome, e, cat_esp, niv_esp in CASOS_OFF:
    cat, niv, _m, _s = pl.decidir_permanencia(e, "camera")
    chk(cat == cat_esp and niv == niv_esp,
        f"OFF {nome}: veio ({cat}, {niv}), esperava ({cat_esp}, {niv_esp})")
chk(pl.categoria_do_catalogo_humano(
        ev(_cat_humana="valor_agregado", _cat_humana_origem="humano")) is None,
    "desligada, o portao tem de devolver None sempre")

# ═══════════════════════════════════════════════════════════════════════
# BLOCO 2 — FLAG LIGADA: o catálogo humano decide, e SÓ ele.
# ═══════════════════════════════════════════════════════════════════════
print("\n── flag LIGADA ──")
pl = carregar("on")
chk(pl._CATALOGO_NIVEL3 is True, "a flag nao ligou")
chk(pl.ORIGEM_CATALOGO_HUMANO == "humano", "vocabulario do catalogo errado")
chk(pl.NIVEL_CATALOGO_HUMANO == "catalogo_humano", "nome do nivel errado")

# ⭐ o caso real: 32 dos 35 erros medidos
cat, niv, mot, _s = pl.decidir_permanencia(
    ev(trabalho=False, _cat_humana="valor_agregado", _cat_humana_origem="humano"),
    "camera")
chk((cat, niv) == ("valor_agregado", "catalogo_humano"),
    f"o caso dos 32 erros: veio ({cat}, {niv})")
chk("catálogo" in mot or "catalogo" in mot, "o motivo tem de citar o catalogo")

# o contrário também: catálogo diz desperdicio e o VLM diz que é trabalho
cat, niv, _m, _s = pl.decidir_permanencia(
    ev(trabalho=True, comportamento_label="celular",
       _cat_humana="desperdicio", _cat_humana_origem="humano"), "camera")
chk((cat, niv) == ("desperdicio", "catalogo_humano"),
    f"catalogo tem de valer nos DOIS sentidos: veio ({cat}, {niv})")

# ── o PORTÃO ────────────────────────────────────────────────────────────
PORTAO = [
    ("origem 'ia' NAO decide", "ia", "valor_agregado", "julgamento"),
    ("origem 'aprendido' NAO decide (nunca foi medida)", "aprendido",
     "valor_agregado", "julgamento"),
    ("origem 'fallback' NAO decide", "fallback", "valor_agregado", "julgamento"),
    ("origem nula NAO decide", None, "valor_agregado", "julgamento"),
    ("origem 'humano_rotulo' (vocab do EVENTO) NAO decide", "humano_rotulo",
     "valor_agregado", "julgamento"),
]
for nome, origem, cat_cat, niv_esp in PORTAO:
    cat, niv, _m, _s = pl.decidir_permanencia(
        ev(trabalho=False, _cat_humana=cat_cat, _cat_humana_origem=origem), "camera")
    chk(niv == niv_esp, f"portao: {nome} — caiu em `{niv}`")

# categoria fora do par válido não decide, mesmo com origem humana
for cat_ruim in ("apoio", "", None, "produtivo"):
    cat, niv, _m, _s = pl.decidir_permanencia(
        ev(trabalho=False, _cat_humana=cat_ruim, _cat_humana_origem="humano"),
        "camera")
    chk(niv == "julgamento",
        f"categoria invalida {cat_ruim!r} nao pode decidir — caiu em `{niv}`")

# ═══════════════════════════════════════════════════════════════════════
# BLOCO 3 — PRECEDÊNCIA: o catálogo não pode passar na frente de quem vem
# antes. É aqui que uma regra nova costuma estragar as antigas.
# ═══════════════════════════════════════════════════════════════════════
print("\n── precedência ──")

# nível 0 — correção humana continua ganhando
cat, niv, _m, _s = pl.decidir_permanencia(
    ev(trabalho=False, validado_humano=True, validacao_correto=True,
       label_corrigido="celular", _cat_humana="valor_agregado",
       _cat_humana_origem="humano"), "camera")
chk(niv == pl.NIVEL_HUMANO, f"correcao humana tem de vencer — veio `{niv}`")

# nível 1 — fora do posto não chega ao 2b
for papel in ("posto_vazio", "visitante"):
    cat, niv, _m, _s = pl.decidir_permanencia(
        ev(papel_pessoa=papel, trabalho=False, _cat_humana="valor_agregado",
           _cat_humana_origem="humano"), "camera")
    chk(niv != "catalogo_humano",
        f"papel={papel} nao pode ser decidido pelo catalogo do posto (`{niv}`)")

# identidade (papel nulo) não chega ao 2b
cat, niv, _m, _s = pl.decidir_permanencia(
    ev(papel_pessoa=None, trabalho=False, _cat_humana="valor_agregado",
       _cat_humana_origem="humano"), "camera")
chk(niv == "identidade",
    f"papel nulo tem de continuar em `identidade` — veio `{niv}`")

# ponte rolante (0b) continua na frente
os.environ["KV_PONTE_SEGMENTO"] = "on"
pl = carregar("on")
if getattr(pl, "_PONTE_SEGMENTO_DECIDE", False):
    cat, niv, _m, _s = pl.decidir_permanencia(
        ev(trabalho=False, categoria_lean_origem=pl.ORIGEM_PONTE_SEGMENTO,
           _cat_humana="desperdicio", _cat_humana_origem="humano"), "camera")
    chk(cat == "valor_agregado" and niv != "catalogo_humano",
        f"ponte rolante tem de vencer o catalogo — veio ({cat}, {niv})")

# ═══════════════════════════════════════════════════════════════════════
# BLOCO 4 — PRESENÇA INVARIANTE. A fase não pode mexer em permanência.
# ═══════════════════════════════════════════════════════════════════════
print("\n── presença não se move ──")
eventos = [
    ev(trabalho=False, _cat_humana="valor_agregado", _cat_humana_origem="humano",
       tempo_inicio_s=0, tempo_fim_s=60),
    ev(papel_pessoa="posto_vazio", tempo_inicio_s=60, tempo_fim_s=120),
    ev(papel_pessoa=None, tempo_inicio_s=120, tempo_fim_s=180),
]
pl_off = carregar(None)
p_off = pl_off.permanencia_do_dia(list(eventos), "camera")
pl_on = carregar("on")
p_on = pl_on.permanencia_do_dia(list(eventos), "camera")
chk(p_off == p_on,
    f"a permanencia MUDOU com a flag: {p_off} vs {p_on} — isto reprova o patch")

# ═══════════════════════════════════════════════════════════════════════
# BLOCO 5 — CatalogoLean: dict para todo efeito, origem de brinde.
# ═══════════════════════════════════════════════════════════════════════
print("\n── CatalogoLean ──")
comps = [
    {"label": "monitorar_maquina", "categoria_lean": "valor_agregado",
     "categoria_lean_origem": "humano"},
    {"label": "chute", "categoria_lean": "valor_agregado",
     "categoria_lean_origem": "ia"},
    {"label": None, "categoria_lean": "desperdicio",
     "categoria_lean_origem": "humano"},
]
C = pl.CatalogoLean(comps)
chk(isinstance(C, dict), "CatalogoLean tem de SER um dict")
chk(C["monitorar_maquina"] == "valor_agregado", "leitura como dict falhou")
chk(C.origem["monitorar_maquina"] == "humano", "origem nao veio")
chk(C.origem["chute"] == "ia", "origem da IA nao veio")
chk(None not in C and len(C) == 2, "label nulo tinha de ser descartado")
chk(getattr({}, "origem", {}) == {}, "dict comum tem de degradar para {}")

# um dict COMUM (chamador antigo) tem de fechar o portao, nao explodir
e2 = dict(ev(trabalho=False))
e2["_cat_humana"] = "valor_agregado"
e2["_cat_humana_origem"] = getattr({}, "origem", {}).get("monitorar_maquina")
cat, niv, _m, _s = pl.decidir_permanencia(e2, "camera")
chk(niv == "julgamento",
    f"chamador antigo tinha de cair no comportamento de hoje — veio `{niv}`")

# ═══════════════════════════════════════════════════════════════════════
print(f"\n{ok} ok, {falhas} falhas")
sys.exit(1 if falhas else 0)
