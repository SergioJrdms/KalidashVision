"""Fase 78 — a defasagem entre as câmeras é temporal, não estética.

Medido no banco (defasagem entre `gravado_em` dos pares cam1/cam2):
  0-1s: 108 pares · 10-12s: 24 · 54-67s: 13 · 135-158s: 2

A causa dos 10s está no watchdog do edge, que relança as câmeras separadamente
(seg_..._070056 na cam1 contra seg_..._070106 na cam2). Nos pares de 55s+,
dizer "2º ângulo (mesma ação)" é FALSO — são momentos distintos, e afirmar
simultaneidade que não existe faz julgar duas cenas diferentes como uma.

Rodar:  python tests_defasagem_cameras.py
"""
import sys, types, os, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for m in ["cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
          "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image"]:
    sys.modules.setdefault(m, types.ModuleType(m))
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

from pathlib import Path  # noqa: E402
from backend import pipeline as pl  # noqa: E402

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


print("\n[1] O offset sai dos NOMES, com o sinal certo")
# Caso real do log: cam1 às 07:00:56, cam2 às 07:01:06 (10s depois).
off = pl._offset_entre_nomes("seg_20260730_070056.mp4", "seg_20260730_070106.mp4")
check("cam2 10s DEPOIS → offset −10", off == -10.0, off)
check("o instante 180s da cam1 cai em 170s da cam2", 180 + off == 170.0)

off = pl._offset_entre_nomes("seg_20260730_070153.mp4", "seg_20260730_070056.mp4")
check("cam2 57s ANTES → offset +57", off == 57.0, off)

# O exemplo do enunciado: cam2 começou 57s DEPOIS.
off = pl._offset_entre_nomes("seg_20260730_070056.mp4", "seg_20260730_070153.mp4")
check("cam2 57s depois → o instante 180s cai em 123s (o número do enunciado)",
      180 + off == 123.0, 180 + off)

check("nome sem token → 0.0 (assume alinhado)",
      pl._offset_entre_nomes("qualquer.mp4", "seg_20260730_070056.mp4") == 0.0)

print("\n[2] O PIPELINE já compensa — a defasagem NÃO corrompe a análise")
src = Path("backend/pipeline.py").read_text()
i = src.index("def _anexar_segundo_angulo(")
# Recorta até a PRÓXIMA função, não por contagem de caracteres: uma janela fixa
# passa a mentir assim que alguém acrescenta duas linhas na função.
corpo = src[i:src.index("def etapa_confirmar_operador(")]
check("o alvo na cam2 soma o offset",
      "alvo_ms = (am.tempo_s + offset_s) * 1000.0" in corpo, )
check("instante fora do segmento é detectado",
      "fora_da_cam2 = alvo_ms < 0 or" in corpo)
check("e nesse caso a cam2 NÃO confirma nem nega (op_cam2=None)",
      "op_cam2 = None" in corpo or "am.op_cam2 = None" in corpo)
check("o offset é calculado a partir do par e passado adiante",
      "offset_cam2 = _offset_entre_nomes(nome_video, nome_secundario)" in src)
check("e chega ao anexador", "offset_s=offset_cam2" in src)

print("\n[3] A TELA: janela compensada + residual explícito")
fr = Path("frontend/src/lib/frames.tsx").read_text()
check("existe o helper da janela da cam2", "export function janelaCam2" in fr)
check("o limite de residual é 5s (o sugerido)", "RESIDUAL_MAX_S = 5" in fr)
check("o residual mede o CLAMP (era silencioso antes)",
      "residual = -iniAlvo" in fr and "residual = fimAlvo - durSec" in fr)
check("'sincronizado' é derivado do residual, não assumido",
      "sincronizado: residual <= RESIDUAL_MAX_S" in fr)
check("o rótulo só diz 'mesmo instante' quando sincronizado",
      "mesmo instante" in fr and "momento DIFERENTE" in fr)

# O comportamento numérico do helper, lido do próprio arquivo.
def janela(ini, fim, off, dur=None):
    ia, fa = ini + off, fim + off
    res = 0.0
    if ia < 0:
        res = -ia
    elif dur and fa > dur:
        res = fa - dur
    return max(0.0, ia), max(0.0, fa), res, res <= 5


i_, f_, r_, s_ = janela(180, 240, -57)
check("evento a 180s com cam2 57s depois → janela 123-183s, sem residual",
      (i_, f_, r_) == (123.0, 183.0, 0.0) and s_, (i_, f_, r_))
i_, f_, r_, s_ = janela(10, 70, -57)
check("evento a 10s com o mesmo offset → CLAMPA e o residual aparece",
      i_ == 0.0 and r_ == 47.0 and not s_, (i_, f_, r_))
check("e aí a tela NÃO pode dizer 'mesmo instante'", s_ is False)
i_, f_, r_, s_ = janela(100, 160, -10)
check("defasagem de 10s compensada continua sincronizada",
      (i_, f_) == (90.0, 150.0) and s_, (i_, f_, r_))

print("\n[4] O clamp silencioso saiu das telas")
for arq in ("frontend/src/pages/Duvidas.tsx", "frontend/src/pages/Eventos.tsx"):
    txt = Path(arq).read_text()
    check(f"{Path(arq).name}: sem Math.max(0, …) escondendo o desalinhamento",
          "Math.max(0, e.ini + e.segundoAngulo.offsetS)" not in txt
          and "Math.max(0, it.ini + sa.offset_s)" not in txt)
    check(f"{Path(arq).name}: usa o helper", "janelaCam2(" in txt)
    check(f"{Path(arq).name}: o rótulo depende de sincronizado",
          "sincronizado" in txt)

print("\n[5] A grade se adapta a ROIs de proporções diferentes")
check("mede o aspecto do frame carregado (não supõe constante)",
      "naturalWidth / img.naturalHeight" in fr)
check("as colunas saem dos aspectos medidos", "export function colunasPorAspecto" in fr)
check("sem medida ainda, cai em 1fr 1fr", 'return "1fr 1fr"' in fr)
check("a desproporção é limitada para o painel menor seguir legível",
      "Math.min(3, Math.max(1 / 3" in fr)
duv = Path("frontend/src/pages/Duvidas.tsx").read_text()
check("a fila de dúvidas usa a grade proporcional", "colunasPorAspecto(a1, a2)" in duv)
check("e alimenta os dois medidores",
      "onAspecto={mede1}" in duv and "onAspecto={mede2}" in duv)


def colunas(a1, a2):
    if not a1 or not a2:
        return "1fr 1fr"
    return f"{min(3, max(1/3, a1/a2))}fr 1fr"


check("CAM1 0.300x0.200 vs CAM2 0.190x0.360 → cam1 ~2.84x mais larga",
      colunas(0.300 / 0.200, 0.190 / 0.360).startswith("2.8"),
      colunas(0.300 / 0.200, 0.190 / 0.360))
check("aspectos iguais → colunas iguais", colunas(1.5, 1.5) == "1.0fr 1fr")
check("desproporção extrema é limitada em 3x", colunas(100, 1) == "3fr 1fr")

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
