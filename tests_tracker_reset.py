"""Fase 64 — reset do tracker entre vídeos.

O worker mantém UM YOLO vivo para todos os jobs e chama `.track(persist=True)`.
`persist` está certo DENTRO de um vídeo e errado ENTRE vídeos:

  1) tracks perdidas sobrevivem `track_buffer` frames e podem casar com quem
     aparecer no primeiro frame do vídeo seguinte (câmera fixa, mesmo posto —
     cenário ideal para o casamento errado);
  2) o GMC guarda `prevFrame` e, em `gmc.py::applySparseOptFlow`, o
     `calcOpticalFlowPyrLK` roda ANTES de `self.prevFrame = frame.copy()`.
     Se os tamanhos divergirem o OpenCV levanta, o ultralytics cai para
     identidade e `prevFrame` NUNCA é atualizado — todo frame seguinte falha
     igual, para sempre. É a origem do warning repetido no Render.

Rodar:  python tests_tracker_reset.py
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

from pathlib import Path  # noqa: E402
from backend import pipeline as pl  # noqa: E402

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


# ── Dublês do BoT-SORT ────────────────────────────────────────────────────
class TrackerModerno:
    """Versão com `reset()` — o caminho bom (BOTSORT.reset chama gmc.reset_params)."""
    def __init__(self):
        self.tracks = ["stale-1", "stale-2"]
        self.gmc_prev_frame = "frame 1280x720"   # o quadro travado de outro vídeo
        self.resets = 0

    def reset(self):
        self.resets += 1
        self.tracks = []
        self.gmc_prev_frame = None


class TrackerAntigo:
    """Versão sem `reset()` — precisa cair no caminho de recriação."""
    def __init__(self):
        self.tracks = ["stale-1"]


class TrackerQuebrado:
    def reset(self):
        raise RuntimeError("boom")


class Pred:
    def __init__(self, trackers):
        self.trackers = trackers


class Yolo:
    def __init__(self, predictor=None):
        self.predictor = predictor


print("\n[1] Caminho bom — trackers com reset()")
t1, t2 = TrackerModerno(), TrackerModerno()
y = Yolo(Pred([t1, t2]))
r = pl.resetar_tracker(y)
check("usa reset()", r == "reset", r)
check("todos os trackers foram resetados", t1.resets == 1 and t2.resets == 1)
check("tracks velhas foram embora (sem vazar p/ o próximo vídeo)",
      t1.tracks == [] and t2.tracks == [])
check("prevFrame do GMC foi zerado (destrava o warning repetido)",
      t1.gmc_prev_frame is None and t2.gmc_prev_frame is None)
check("o predictor continua de pé", hasattr(y.predictor, "trackers"))

print("\n[2] Versão antiga sem reset() — força a recriação")
y = Yolo(Pred([TrackerAntigo()]))
r = pl.resetar_tracker(y)
check("cai no caminho de recriação", r == "recriar", r)
check("apaga `trackers` — é o que faz on_predict_start recriar mesmo com persist=True",
      not hasattr(y.predictor, "trackers"))

# Mistura (um com reset, outro sem) tem de ir para o caminho seguro, não
# resetar metade e deixar a outra metade suja.
y = Yolo(Pred([TrackerModerno(), TrackerAntigo()]))
r = pl.resetar_tracker(y)
check("lista mista → recriação (nunca resetar pela metade)", r == "recriar", r)
check("mista: trackers apagados", not hasattr(y.predictor, "trackers"))

print("\n[3] Nunca levanta — falhar aqui não pode matar um vídeo")
check("sem predictor (1º vídeo do processo)", pl.resetar_tracker(Yolo(None)) == "nada")
check("predictor sem trackers", pl.resetar_tracker(Yolo(types.SimpleNamespace())) == "nada")
check("lista de trackers vazia", pl.resetar_tracker(Yolo(Pred([]))) == "recriar")


class YoloExplosivo:
    @property
    def predictor(self):
        raise RuntimeError("acesso proibido")


try:
    r = pl.resetar_tracker(YoloExplosivo())
    subiu = False
except Exception:  # noqa: BLE001
    subiu = True
check("atributo que explode não propaga", subiu is False)

y = Yolo(Pred([TrackerQuebrado()]))
try:
    r = pl.resetar_tracker(y)
    subiu = False
except Exception:  # noqa: BLE001
    subiu = True
check("reset() que levanta não propaga", subiu is False)
check("e reporta a falha em vez de mentir que resetou", r == "falhou", r)

print("\n[4] A detecção reseta o tracker a cada vídeo")
fonte = Path("backend/pipeline.py").read_text()
i_def = fonte.index("def etapa_detectar_e_amostrar(")
i_insp = fonte.index("info = inspecionar_video(video_path)", i_def)
check("resetar_tracker é chamado ANTES de abrir o vídeo",
      "resetar_tracker(yolo)" in fonte[i_def:i_insp],
      fonte[i_def:i_insp][-200:])

print("\n[5] Perfil de câmera fixa — opt-in, e idêntico ao de fábrica")
cfg = Path("backend/trackers/botsort_camera_fixa.yaml")
check("arquivo existe", cfg.is_file())

import yaml  # noqa: E402

perfil = yaml.safe_load(cfg.read_text())
# Cópia fiel do botsort.yaml de fábrica (ultralytics 8.4.x).
FABRICA = {
    "tracker_type": "botsort", "track_high_thresh": 0.25, "track_low_thresh": 0.1,
    "new_track_thresh": 0.25, "track_buffer": 30, "match_thresh": 0.8,
    "fuse_score": True, "gmc_method": "sparseOptFlow", "proximity_thresh": 0.5,
    "appearance_thresh": 0.8, "with_reid": False, "model": "auto",
}
check("mesmas chaves do de fábrica (nada some, nada aparece)",
      set(perfil) == set(FABRICA), set(perfil) ^ set(FABRICA))
difs = {k for k in FABRICA if perfil[k] != FABRICA[k]}
check("a ÚNICA diferença é gmc_method", difs == {"gmc_method"}, difs)
check("gmc_method desligado", perfil["gmc_method"] == "none", perfil["gmc_method"])

check("default NÃO é o perfil fixo (campanha não muda sozinha)",
      pl.TRACKER_CONFIG == "botsort.yaml", pl.TRACKER_CONFIG)

os.environ["KV_TRACKER"] = "fixa"
import importlib  # noqa: E402
pl2 = importlib.reload(pl)
check("KV_TRACKER=fixa aponta para o perfil", pl2.TRACKER_CONFIG.endswith("botsort_camera_fixa.yaml"),
      pl2.TRACKER_CONFIG)
os.environ["KV_TRACKER"] = "valor_invalido"
pl2 = importlib.reload(pl)
check("valor desconhecido cai no de fábrica (nunca quebra o tracking)",
      pl2.TRACKER_CONFIG == "botsort.yaml", pl2.TRACKER_CONFIG)
os.environ.pop("KV_TRACKER", None)
importlib.reload(pl)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
