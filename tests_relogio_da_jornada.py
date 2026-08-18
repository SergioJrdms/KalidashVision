# ============================================================
# O RELÓGIO DA JORNADA — hora de parede da fábrica, vinda do NOME.
#
# O DEFEITO, medido no banco em 17/08: a jornada aparecia das 6h às 18h quando
# a captura real ia das 03:00 às 15:20. Exatamente três horas à frente — e o
# intervalo em UTC daquele dia é 06:00→18:20, que é o que a tela desenhava.
#
# DUAS CAUSAS SOMADAS, e as duas moravam em `_inicio_video_dt`:
#
#  (1) FUSO. `gravado_em` é `timestamptz` e o PostgREST o devolve em UTC. Quem
#      fazia `dt.hour` lia a hora UTC. Render roda em UTC, então nem o fuso do
#      container salvava.
#  (2) ORDEM DAS FONTES. `gravado_em` vinha antes do NOME. O nome é o carimbo
#      que a borda escreveu no instante da gravação, já em hora local — imune a
#      reinterpretação de fuso e imune a um `gravado_em` preenchido com a hora
#      do upload.
#
# ⚠️ `_inicio_video_dt` é o helper ÚNICO de "que horas foi isto". Consertar
# aqui conserta a jornada típica, o mapa da quinzena, o ritmo por hora, o
# placar por sessão e a fila — de uma vez, sem cada tela ter a sua conversão.
# ============================================================
import sys, types, os
from datetime import datetime, timezone, timedelta

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

from backend import pipeline as pl  # noqa: E402

RAIZ = os.path.dirname(os.path.abspath(__file__))
ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


fonte = open(os.path.join(RAIZ, "backend", "pipeline.py"), encoding="utf-8").read()
dt_de = pl._inicio_video_dt

# ── Linhas REAIS do banco (17 e 18/08), como o PostgREST as devolve ────────
REAIS = [
    ("seg_20260817_152000_roi.mp4", "2026-08-17T18:20:00+00:00", "15:20"),
    ("seg_20260817_140057_roi.mp4", "2026-08-17T17:00:57+00:00", "14:00"),
    ("seg_20260817_131000_roi.mp4", "2026-08-17T16:10:00+00:00", "13:10"),
    ("seg_20260818_062000_roi.mp4", "2026-08-18T09:20:00+00:00", "06:20"),
    ("seg_20260818_061000_roi.mp4", "2026-08-18T09:10:00+00:00", "06:10"),
]

print("\n[1] ⭐ A hora desenhada é a do RELÓGIO DA FÁBRICA")
for nome, grav, esperado in REAIS:
    d = dt_de({"nome": nome, "gravado_em": grav})
    check(f"{nome[4:19]} → {esperado}", d and d.strftime("%H:%M") == esperado,
          d and d.strftime("%H:%M"))
# A prova do defeito: a hora UTC crua seria três colunas adiante.
_d = dt_de({"nome": "seg_20260817_152000_roi.mp4",
            "gravado_em": "2026-08-17T18:20:00+00:00"})
check("⭐ e NÃO é mais a hora UTC (que era 18:20 e virava barra às 18h)",
      _d.strftime("%H:%M") != "18:20")

print("\n[2] ⭐ O NOME DO SEGMENTO vem primeiro")
# O pedido do dono, literal: o corte de horário sai do nome, não de quando o
# arquivo subiu. Aqui o `gravado_em` está propositalmente errado (hora do
# upload) e o nome tem de vencer.
d = dt_de({"nome": "seg_20260817_152000_roi.mp4",
           "gravado_em": "2026-08-17T21:11:33+00:00",     # hora do UPLOAD
           "processado_em": "2026-08-17T21:11:33+00:00"})
check("⭐ com `gravado_em` errado, o NOME manda", d.strftime("%H:%M") == "15:20",
      d.strftime("%H:%M"))
check("e a ordem das fontes está escrita no código",
      "O NOME DO SEGMENTO vem primeiro" in fonte)

print("\n[3] As reservas, na ordem certa")
check("sem nome, usa `gravado_em` — também convertido",
      dt_de({"nome": None, "gravado_em": "2026-08-17T18:20:00+00:00"}
            ).strftime("%H:%M") == "15:20")
check("nome sem relógio não engana — cai para `gravado_em`",
      dt_de({"nome": "video_qualquer.mp4", "gravado_em": "2026-08-17T18:20:00+00:00"}
            ).strftime("%H:%M") == "15:20")
check("só `processado_em` é o ÚLTIMO recurso",
      dt_de({"processado_em": "2026-08-17T21:11:33+00:00"}).strftime("%H:%M") == "18:11")
check("e está declarado como sem relação com quando a cena aconteceu",
      "relação com quando a cena aconteceu" in fonte)
check("nada parseável devolve None — não inventa hora", dt_de({}) is None)
check("lixo no campo não quebra",
      dt_de({"gravado_em": "nao-e-data"}) is None)

print("\n[4] Carimbo sem fuso é hora de PAREDE, não UTC")
# É assim que a borda grava. Tratá-lo como UTC seria reintroduzir o mesmo
# deslocamento de três horas pela porta dos fundos.
d = dt_de({"nome": None, "gravado_em": "2026-08-17T15:20:00"})
check("sem fuso, o carimbo é lido como hora local", d.strftime("%H:%M") == "15:20")
check("e o motivo está escrito", "hora de parede da fábrica" in fonte)

print("\n[5] O helper de fuso é ÚNICO")
check("existe `_tz_edge`", callable(getattr(pl, "_tz_edge", None)))
check("e o parser do nome usa ele, sem duplicar a lógica",
      "return dt.replace(tzinfo=_tz_edge()).isoformat()" in fonte)
check("com o aviso de que Render roda em UTC",
      "Render roda em UTC" in fonte)
# Duas conversões diferentes em lugares diferentes é como o defeito nasce.
check("nenhuma outra ZoneInfo solta no arquivo",
      fonte.count('ZoneInfo(os.environ.get("KV_TZ"') == 1,
      fonte.count('ZoneInfo(os.environ.get("KV_TZ"'))

print("\n[6] Consertar aqui conserta TODAS as telas de hora")
# `_inicio_video_dt` é o helper único; se alguém criar uma conversão paralela,
# a jornada e o mapa voltam a divergir entre si.
for uso in ("dia_iso = dt.date().isoformat()",):
    check(f"o dia a dia deriva o dia do MESMO helper", uso in fonte)
check("o mapa/jornada e o placar usam `_inicio_video_dt`",
      fonte.count("_inicio_video_dt(v)") >= 4, fonte.count("_inicio_video_dt(v)"))

print("\n[7] O dia 17/08 inteiro, reconstruído")
# Primeiro e último segmento reais do dia, como estão no banco.
primeiro = dt_de({"nome": None, "gravado_em": "2026-08-17T06:00:00+00:00"})
ultimo = dt_de({"nome": "seg_20260817_152000_roi.mp4",
                "gravado_em": "2026-08-17T18:20:00+00:00"})
check("a jornada do dia 17 termina às 15:20, não às 18:20",
      ultimo.strftime("%H:%M") == "15:20", ultimo.strftime("%H:%M"))
check("e começa às 03:00, não às 06:00",
      primeiro.strftime("%H:%M") == "03:00", primeiro.strftime("%H:%M"))
check("os dois caem no MESMO dia — a conversão não empurra para a véspera",
      primeiro.date().isoformat() == ultimo.date().isoformat() == "2026-08-17")

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
