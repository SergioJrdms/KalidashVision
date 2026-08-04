"""Fase 82 — a caixa da pessoa volta a ser uma medida.

O SINTOMA: `eventos.bbox_inicio` vinha {x1:0,y1:0,x2:0,y2:0} em todo evento de
papel 'operador' e 'posto_vazio'. Só 'visitante' tinha coordenada real. A altura
aparente do corpo — o descritor mais barato que existe com câmera fixa — estava
sendo zerada na escrita.

AS DUAS CAUSAS (ambas de escrita, nenhuma de leitura):
  1. resgate pela cam2: o detector da cam2 CALCULAVA a caixa e o laço guardava
     só o booleano `achou`; a observação nascia com (0,0,0,0);
  2. posto vazio: não há pessoa, e gravava-se (0,0,0,0) mesmo assim.

E o efeito colateral que ninguém veria: zero passa em qualquer teste de
existência (`if not b` é falso para [0,0,0,0]), então o ponto fantasma na origem
entrava no cálculo de deslocamento de `montar_fato_evento` e o sinal `movimento`
dizia "andando" num minuto de gente parada.

Rodar:  python tests_bbox_sinal.py
"""
import sys, types, os

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

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


print("[1] Zero nunca mais passa por medida")
check("(0,0,0,0) não é caixa válida", pl._bbox_valido((0, 0, 0, 0)) is False)
check("None não é caixa válida", pl._bbox_valido(None) is False)
check("caixa de 1px é degenerada", pl._bbox_valido((10, 10, 11, 11)) is False)
check("caixa real é válida", pl._bbox_valido((100, 50, 160, 380)) is True)
check("o jsonb de uma caixa nula é NULL", pl._bbox_jsonb((0, 0, 0, 0)) is None)
check("o jsonb de uma caixa real tem as 4 coordenadas",
      pl._bbox_jsonb((1, 2, 3, 40)) == {"x1": 1, "y1": 2, "x2": 3, "y2": 40})


def obs(t, bbox, papel="operador", label="operar_torno", cam="cam1",
        dim=(640, 480), maos=None):
    return {"tempo_s": float(t), "frame_idx": int(t * 10), "track_id": 7,
            "descricao": "d", "label": label, "bbox": bbox,
            "bbox_cam": cam if bbox else None,
            "bbox_dim": dim if bbox else None,
            "papel": papel, "zona": "posto", "maos_maquina": maos}


print("\n[2] O evento guarda a caixa quando ela existe — e NULL quando não")
evs = pl.etapa_segmentar_eventos(
    [obs(0, (100, 60, 160, 380)), obs(2, (102, 58, 162, 384)),
     obs(4, (98, 62, 158, 376))],
    lambda d: "operar_torno", 2.0)
check("um evento contínuo", len(evs) == 1, len(evs))
e = evs[0]
check("bbox_inicio é a caixa real", e["bbox_inicio"] == [100, 60, 160, 380])
check("e diz de qual câmera veio", e["bbox_cam"] == "cam1")

vazios = pl.etapa_segmentar_eventos(
    [obs(0, None, papel="posto_vazio", label="posto_vazio"),
     obs(2, None, papel="posto_vazio", label="posto_vazio")],
    lambda d: "posto_vazio", 2.0)
check("posto vazio nasce com bbox_inicio NULO",
      vazios[0]["bbox_inicio"] is None, vazios[0]["bbox_inicio"])
check("e sem câmera atribuída", vazios[0]["bbox_cam"] is None)
check("e sem estatística de corpo", vazios[0]["bbox_stats"] is None)

print("\n[3] A 1ª caixa REAL vale mais que um None do primeiro frame")
tardio = pl.etapa_segmentar_eventos(
    [obs(0, None), obs(2, (10, 20, 60, 260)), obs(4, (12, 22, 62, 262))],
    lambda d: "operar_torno", 2.0)
check("evento que começa ocluso adota a 1ª caixa que aparecer",
      tardio[0]["bbox_inicio"] == [10, 20, 60, 260], tardio[0]["bbox_inicio"])
check("e registra a câmera dela", tardio[0]["bbox_cam"] == "cam1")

print("\n[4] bbox_stats: mediana das amostras, não um frame só")
st = evs[0]["bbox_stats"]
check("conta as caixas do evento", st["n"] == 3, st)
check("altura mediana das 3 (320, 326, 314) = 320", st["altura_med"] == 320.0, st)
check("guarda o intervalo observado",
      (st["altura_min"], st["altura_max"]) == (314.0, 326.0), st)
check("normaliza pela altura do frame (320/480)", st["altura_rel"] == 0.6667, st)
check("e diz qual era esse frame", st["frame_h"] == 480, st)
check("aspecto largura/altura ~0.19", 0.18 < st["aspecto_med"] < 0.20, st)
check("uma caixa degenerada no meio não entra na conta",
      pl._resumo_bbox([(0, 0, 0, 0), (10, 10, 60, 210)], (640, 480), "cam1")["n"] == 1)

print("\n[5] O minuto consolidado herda o corpo do MESMO track")
crus = pl.etapa_segmentar_eventos(
    [obs(0, (100, 60, 160, 380)), obs(2, (100, 60, 160, 380))],
    lambda d: "operar_torno", 2.0)
outra = pl.etapa_segmentar_eventos(
    [dict(obs(0, (300, 200, 340, 300)), track_id=99)], lambda d: "operar_torno", 2.0)
for c in outra:
    c["pessoa_track_id"] = 99
principais = pl.etapa_consolidar_principais(crus + outra, {}, 60.0)
check("gera 1 principal para o minuto", len(principais) == 1, len(principais))
p = principais[0]
check("a estatística é do track representante, não da soma das pessoas",
      p["bbox_stats"]["n"] == 2, p["bbox_stats"])
check("e a altura é a do operador (320), não a do visitante (100)",
      p["bbox_stats"]["altura_med"] == 320.0, p["bbox_stats"])
check("bbox_cam vem junto", p["bbox_cam"] == "cam1")

vaz_principal = pl.etapa_consolidar_principais(vazios, {}, 60.0)
check("minuto de posto vazio segue sem caixa",
      vaz_principal[0]["bbox_inicio"] is None
      and vaz_principal[0]["bbox_stats"] is None)

print("\n[6] O ponto fantasma não move mais ninguém")
# Um minuto com pessoa PARADA + um trecho de posto vazio no mesmo bucket.
parado = [
    {"pessoa_track_id": 7, "bbox_inicio": [100, 60, 160, 380],
     "tempo_inicio_s": 0.0, "tempo_fim_s": 30.0, "papel_pessoa": "operador",
     "zona_contexto": "posto"},
    {"pessoa_track_id": 7, "bbox_inicio": [101, 61, 161, 381],
     "tempo_inicio_s": 30.0, "tempo_fim_s": 60.0, "papel_pessoa": "operador",
     "zona_contexto": "posto"},
]
fantasma = parado + [
    {"pessoa_track_id": -1, "bbox_inicio": [0, 0, 0, 0],
     "tempo_inicio_s": 20.0, "tempo_fim_s": 25.0, "papel_pessoa": "posto_vazio",
     "zona_contexto": "posto"},
]
f_ok = pl.montar_fato_evento(parado[0], [(e, 30.0) for e in parado], 1.0, 1)
f_ph = pl.montar_fato_evento(parado[0], [(e, 30.0) for e in fantasma], 1.0, 1)
check("gente parada é lida como parada", f_ok.get("movimento") == "parado", f_ok)
check("e o zero de um posto_vazio no mesmo minuto não a faz 'andar'",
      f_ph.get("movimento") == "parado", f_ph)
check("o deslocamento é o mesmo com e sem o fantasma",
      f_ok.get("deslocamento_rel") == f_ph.get("deslocamento_rel"),
      (f_ok.get("deslocamento_rel"), f_ph.get("deslocamento_rel")))

print("\n[7] maos_na_maquina deixou de ser um sinal morto")
com_maos = pl.etapa_segmentar_eventos(
    [obs(0, (100, 60, 160, 380), maos=False),
     obs(2, (100, 60, 160, 380), maos=True)],
    lambda d: "operar_torno", 2.0)
check("o evento cru carrega o punho na zona da máquina",
      com_maos[0].get("maos_maquina") is True, com_maos[0].get("maos_maquina"))
fato = pl.montar_fato_evento(
    com_maos[0],
    [(dict(com_maos[0], tempo_inicio_s=0.0, tempo_fim_s=4.0), 4.0)], 1.0, 1)
check("e o fato do evento passa a enxergá-lo",
      fato.get("maos_na_maquina") is True, fato)

print("\n[8] A escrita e o consumo de frames aguentam o NULL")
from pathlib import Path  # noqa: E402
src = Path("backend/pipeline.py").read_text()
check("nenhuma escrita monta o dict de zeros à mão",
      '"x1": e["bbox_inicio"][0]' not in src)
check("as duas linhas de evento passam pelo mesmo conversor",
      src.count('"bbox_inicio": _bbox_jsonb(') == 2, src.count('"bbox_inicio": _bbox_jsonb('))
check("a extração de frames trata bbox ausente",
      'bbox = evento.get("bbox_inicio")' in src and "x1 = y1 = x2 = y2 = 0" in src)
check("a caixa da cam2 é guardada, não descartada",
      "am.bbox_cam2 = bbox_no_posto[0]" in src)
check("com as dimensões do frame da cam2 junto", "am.dim_cam2 = (w2, h2)" in src)
check("a ponte temporal continua SEM caixa (ninguém foi visto)",
      'bbox_obs = am.bbox_cam2 if origem_resgate != "ponte_temporal" else None' in src)
check("as colunas novas existem no schema",
      "add column if not exists bbox_cam" in Path("sql/schema.sql").read_text()
      and "add column if not exists bbox_stats" in Path("sql/schema.sql").read_text())
check("e no bootstrap do pipeline", "add column if not exists bbox_cam" in src)

print(f"\n{'=' * 56}\n  {ok} ok · {fail} falha(s)\n{'=' * 56}")
sys.exit(1 if fail else 0)
