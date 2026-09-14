"""Contrato da interface final: operacao e evidencias, sem metricas internas."""
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
app = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
shell = (ROOT / "frontend/src/design/Shell.tsx").read_text(encoding="utf-8")
dashboard = (ROOT / "frontend/src/pages/Dashboard.tsx").read_text(encoding="utf-8")
drawer = (ROOT / "frontend/src/components/EventEvidenceDrawer.tsx").read_text(encoding="utf-8")
backend = (ROOT / "backend/main.py").read_text(encoding="utf-8")

interface_ativa = "\n".join((app, shell, dashboard, drawer))

checks = {
    "dashboard mantem os percentuais operacionais": all(
        trecho in dashboard
        for trecho in ("produtivo_total_pct", "improdutivo_total_pct", "sem_decisao_total_pct")
    ),
    "percentuais sao descritos como periodo analisado": "do período analisado" in dashboard,
    "interface nao consulta o replay interno": "replayProdutividade" not in interface_ativa,
    "interface nao publica precisao produtiva": "precision_productive_pct" not in interface_ativa,
    "interface nao publica precisao improdutiva": "precision_improductive_pct" not in interface_ativa,
    "interface nao publica coverage": "coverage_pct" not in interface_ativa,
    "interface nao publica volume do conjunto de validacao": "actual_events" not in interface_ativa,
    "interface nao publica confianca validada": "Confiança validada" not in interface_ativa,
    "interface nao publica evolucao da validacao": "Ver evolução da validação" not in interface_ativa,
    "interface nao publica horas gravadas ou capturadas": all(
        termo not in interface_ativa.lower()
        for termo in ("horas gravadas", "horas capturadas", "quantidade de horas")
    ),
    "rotas finais nao incluem telas de precisao": all(
        termo not in app and termo not in shell
        for termo in ("ReplayPrecisao", '"replay-precisao"', '"precisao"')
    ),
    "evidencias continuam ligadas ao indicador exato": "Estas leituras compõem exatamente este indicador." in drawer,
    "evidencias recentes aparecem primeiro": (
        "leituras · mais recentes primeiro" in drawer
        and 'e["_capturado_em"].timestamp()' in backend
    ),
}

falhas = [nome for nome, ok in checks.items() if not ok]
for nome, ok in checks.items():
    print(f"  {'ok  ' if ok else 'FAIL'} {nome}")
print(f"\n{len(checks) - len(falhas)} ok · {len(falhas)} falha(s)")
sys.exit(1 if falhas else 0)
