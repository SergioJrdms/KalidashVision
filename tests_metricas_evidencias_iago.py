"""Aceitação dos três pedidos da call: dois eixos, total comum e drill-down exato."""
from pathlib import Path
import sys

from backend import productivity as prod

ROOT = Path(__file__).resolve().parent
ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    print(f"  {'ok  ' if cond else 'FAIL'} {nome} {extra if not cond else ''}")
    ok += bool(cond)
    fail += not cond


estados = [
    prod.EST_PRODUTIVO,
    prod.EST_IMPRODUTIVO,
    prod.EST_PRODUTIVIDADE_INCONCLUSIVA,
    prod.EST_POSTO_VAZIO,
    prod.EST_OPERADOR_AUSENTE,
    prod.EST_OPERADOR_FORA,
    prod.EST_OPERADOR_FORA_PRODUTIVO,
    prod.EST_OPERADOR_FORA_IMPRODUTIVO,
    prod.EST_SEM_LEITURA,
]

print("[1] Partições canônicas")
for estado in estados:
    produtividade = sum(prod.estado_compone_indicador(estado, i) for i in (
        "produtivo", "improdutivo", "sem_decisao"
    ))
    presenca = sum(prod.estado_compone_indicador(estado, i) for i in (
        "presenca_operador", "posto_sem_operador", "presenca_inconclusiva"
    ))
    check(f"{estado}: pertence a uma única fatia de produtividade", produtividade == 1)
    check(f"{estado}: pertence a uma única fatia de presença", presenca == 1)
    check(f"{estado}: pertence ao total capturado", prod.estado_compone_indicador(estado, "tempo_capturado"))

print("\n[2] Contrato de tela")
dashboard = (ROOT / "frontend/src/pages/Dashboard.tsx").read_text(encoding="utf-8")
drawer = (ROOT / "frontend/src/components/EventEvidenceDrawer.tsx").read_text(encoding="utf-8")
api = (ROOT / "frontend/src/lib/api.ts").read_text(encoding="utf-8")
check("produtividade mostra três fatias", all(x in dashboard for x in (
    'titulo="Produtivo"', 'titulo="Improdutivo"', 'titulo="Sem decisão"'
)))
check("presença está declarada como eixo separado", "Eixo separado da produtividade" in dashboard)
check("as três fatias de cada eixo fecham 100%", all(x in dashboard for x in (
    "Produtivo + improdutivo + sem decisão = 100%",
    "Presença do operador não altera o percentual produtivo ou improdutivo",
)))
check("a duração capturada não é mostrada ao cliente",
      "tempo_capturado_s" not in dashboard and 'titulo="Tempo capturado"' not in dashboard)
check("o drawer afirma a relação exata", "compõem exatamente este indicador" in drawer)
check("API pede o indicador exato", "evidenciasIndicador" in api and "indicador=" in api)

print(f"\n{ok} ok · {fail} falha(s)")
sys.exit(1 if fail else 0)
