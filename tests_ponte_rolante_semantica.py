"""Semântica mecânica da origem ``ponte_rolante`` no pipeline.

Executar: python -X utf8 tests_ponte_rolante_semantica.py
"""
from __future__ import annotations

import os
import sys
import types


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for modulo in [
    "cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
    "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image",
]:
    sys.modules.setdefault(modulo, types.ModuleType(modulo))
sys.modules["dotenv"].load_dotenv = lambda *args, **kwargs: None
sys.modules["ultralytics"].YOLO = object
sys.modules["supabase"].create_client = lambda *args, **kwargs: None
sys.modules["supabase"].Client = object
sys.modules["groq"].Groq = object
sys.modules["anthropic"].Anthropic = object
sys.modules["openai"].OpenAI = object
sys.modules["numpy"].ndarray = object
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")

from backend import pipeline as pl  # noqa: E402


ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


print("[1] Origem é máquina e mecanismo")
check("ponte é origem de máquina", "ponte_rolante" in pl.ORIGENS_MAQUINA,
      pl.ORIGENS_MAQUINA)
check("ponte é origem mecânica", "ponte_rolante" in pl._ORIGENS_MECANICAS,
      pl._ORIGENS_MECANICAS)


print("\n[2] Flag técnica não vira julgamento nem aprendizado humano")
evento = {
    "principal": True,
    "origem_validacao": "ponte_rolante",
    "validado_humano": True,
    "validacao_correto": True,
    "comportamento_label": "operando_ponte_rolante",
    "papel_pessoa": None,
}
varrer_real = pl.varrer
try:
    pl.varrer = lambda *args, **kwargs: [dict(evento)]
    memoria = pl.carregar_memoria_do_negocio(object(), "União", "Torneamento Convencional")
finally:
    pl.varrer = varrer_real
check("evento técnico não conta como confirmação humana",
      memoria["total_eventos_validados"] == 0, memoria)
check("evento técnico não alimenta vocabulário nem correções",
      memoria["vocabulario"] == [] and memoria["correcoes_aprendidas"] == {}, memoria)

_categoria, nivel, _motivo, _estado = pl.decidir_permanencia(evento, None)
check("validado_humano técnico não ganha precedência humana",
      nivel != pl.NIVEL_HUMANO, nivel)


print("\n[3] Evento fica fora da fila atual e da curva histórica")
atual = pl.evento_em_duvida(evento, 0.65)
historico = pl.evento_em_duvida(evento, 0.65, incluir_resolvidas=True)
check("não entra na fila atual", atual == (False, "", ""), atual)
check("não aparece como dúvida resolvida no histórico",
      historico == (False, "", ""), historico)


print(f"\n{ok} ok · {fail} falha(s)")
raise SystemExit(1 if fail else 0)
