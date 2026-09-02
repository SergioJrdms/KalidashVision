"""Regressões de contrato da auditabilidade: sem pipeline e sem métricas paralelas."""
from pathlib import Path
import sys

raiz = Path(__file__).resolve().parent
main = (raiz / "backend" / "main.py").read_text(encoding="utf-8")
drawer = (raiz / "frontend" / "src" / "components" / "EventEvidenceDrawer.tsx").read_text(encoding="utf-8")
dash = (raiz / "frontend" / "src" / "pages" / "Dashboard.tsx").read_text(encoding="utf-8")
types = (raiz / "frontend" / "src" / "lib" / "types.ts").read_text(encoding="utf-8")
rotulo_presenca = drawer[drawer.find("export function rotuloLeituraPresenca"):drawer.find("export function EventEvidenceDrawer")]
ok = fail = 0


def check(nome, cond):
    global ok, fail
    print(f"  {'ok  ' if cond else 'FAIL'} {nome}")
    ok += bool(cond)
    fail += not cond


print("[1] KPI de presença")
check("endpoint recebe a janela", "janela_dias: int = Query(7, ge=1, le=30)" in main)
check("reutiliza a linha do tempo canônica", "produtividade._linha_do_tempo(periodo, frentes)" in main)
check("não filtra por label", "labels.split" not in main[main.find("def evidencias_de_presenca"):main.find("@app.get(\"/eventos", main.find("def evidencias_de_presenca"))])
check("dashboard envia a janela", "janelaPresenca={janela}" in dash)

print("[2] Segundo ângulo e lazy loading")
check("CAM2 só existe no evento expandido", "aberto === `${e.id}-${i}`" in drawer)
check("usa offset e política existente", "janelaCam2(ini, fim, segundoAngulo.offset_s || 0)" in drawer)
check("segundo ângulo preserva o rótulo honesto", "RotuloSegundoAngulo" in drawer)

print("[3] Paginação acumulativa")
check("usa páginas infinitas", "useInfiniteQuery" in drawer)
check("concatena páginas", "flatMap((p) => p.itens)" in drawer)
check("botão depende de próxima página", "q.hasNextPage" in drawer)

print("[4] Estado canônico de presença no drawer")
check("tipo transporta estado canônico", "estado_presenca?: string | null" in types)
check("EST_POSTO_VAZIO recebe rótulo humano", 'estadoPresenca === "posto_vazio"' in rotulo_presenca and '"Posto sem operador"' in rotulo_presenca)
check("OPERADOR_FORA recebe rótulo humano", 'estadoPresenca === "operador_fora"' in rotulo_presenca and '"Operador fora do posto"' in rotulo_presenca)
check("OPERADOR_FORA_PRODUTIVO recebe rótulo humano", 'estadoPresenca === "operador_fora_produtivo"' in rotulo_presenca and '"Operador fora do posto"' in rotulo_presenca)
check("OPERADOR_FORA_IMPRODUTIVO recebe rótulo humano", 'estadoPresenca === "operador_fora_improdutivo"' in rotulo_presenca and '"Operador fora do posto"' in rotulo_presenca)
check("drawer usa somente o estado canônico", "rotuloLeituraPresenca(e.estado_presenca)" in drawer)
check("rótulo não calcula C6", "_linha_do_tempo" not in rotulo_presenca and "bbox" not in rotulo_presenca and "track" not in rotulo_presenca)

print(f"\n{ok} ok · {fail} falha(s)")
sys.exit(1 if fail else 0)
