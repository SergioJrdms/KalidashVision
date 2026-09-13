"""Contrato da coleta independente de produtividade na fila humana."""
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for nome in (
    "cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
    "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image",
):
    sys.modules.setdefault(nome, types.ModuleType(nome))
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
sys.modules["ultralytics"].YOLO = object
sys.modules["supabase"].create_client = lambda *a, **k: None
sys.modules["supabase"].Client = object
sys.modules["groq"].Groq = object
sys.modules["anthropic"].Anthropic = object
sys.modules["openai"].OpenAI = object
sys.modules["numpy"].ndarray = object
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")

from fastapi import HTTPException

from backend import main
from backend.productivity import (
    EST_IMPRODUTIVO,
    EST_OPERADOR_FORA_IMPRODUTIVO,
    EST_OPERADOR_FORA_PRODUTIVO,
    EST_PRODUTIVO,
    classificar_observacao,
    classificar_produtividade_auditavel,
)


def check(nome, condicao, detalhe=None):
    if not condicao:
        raise AssertionError(f"{nome}: {detalhe}")
    print("OK", nome)


def check_projecao(nome, evento):
    estado, motivo = classificar_observacao(evento)
    predita, regra = classificar_produtividade_auditavel(evento)
    esperada = (
        "PRODUTIVO"
        if estado in {EST_PRODUTIVO, EST_OPERADOR_FORA_PRODUTIVO}
        else "IMPRODUTIVO"
        if estado in {EST_IMPRODUTIVO, EST_OPERADOR_FORA_IMPRODUTIVO}
        else "ABSTEM"
    )
    check(nome, predita == esperada and regra == motivo)


base = {"papel_pessoa": "operador"}
check_projecao("congela a decisão produtiva atual", {**base, "maos_maquina": True})
check_projecao(
    "congela a decisão negativa atual e sua causa",
    {**base, "trabalho": False, "produtividade_motivo": "uso_celular"},
)
check_projecao(
    "preserva a política atual para negativo genérico",
    {**base, "trabalho": False},
)
check(
    "posto vazio não vira improdutividade",
    classificar_produtividade_auditavel({"papel_pessoa": "posto_vazio"})[0]
    == "ABSTEM",
)

update = main._montar_update_validacao(
    "confirmar", "operar_torno", None, "PRODUTIVO"
)
check("verdade humana separada", update["produtividade_humana"] == "PRODUTIVO")
check("instante da verdade gravado", bool(update["produtividade_validada_em"]))

reaberto = main._montar_update_validacao("reabrir", "operar_torno", None)
check("reabrir limpa verdade P/I", reaberto["produtividade_humana"] is None)

try:
    main._montar_update_validacao("confirmar", "operar_torno", None, "TALVEZ")
except HTTPException as exc:
    check("valor fora do contrato falha fechado", exc.status_code == 400)
else:
    raise AssertionError("produtividade_humana inválida foi aceita")

schema = Path("sql/schema.sql").read_text(encoding="utf-8")
for coluna in (
    "produtividade_predita",
    "produtividade_regra",
    "produtividade_humana",
    "produtividade_validada_em",
):
    check(f"schema contém {coluna}", coluna in schema)

pipeline = Path("backend/pipeline.py").read_text(encoding="utf-8")
check(
    "pipeline congela predição e regra",
    'row["produtividade_predita"]' in pipeline
    and 'row["produtividade_regra"]' in pipeline,
)
check(
    "fallback trata campos de auditoria como opcionais",
    '"produtividade_predita", "produtividade_regra"' in pipeline,
)

fila = Path("frontend/src/pages/Validacao.tsx").read_text(encoding="utf-8")
check("fila pergunta produtividade", "Neste intervalo, o trabalho foi" in fila)
check(
    "rotulagem permanece cega à predição",
    "Prism:" not in fila and "predita={" not in fila,
)
