"""Fase 76 — a varredura do Storage: o furo da cam2 e o agendamento.

O bucket foi de 0 a 979 MB de 1 GB em 4 dias e a campanha quase parou. Duas
causas independentes:

  1. NUNCA HOUVE AGENDAMENTO. `varrer_videos_expirados` só era alcançável pelo
     endpoint manual. "Existe um endpoint" não é um mecanismo — é uma tarefa
     esperando ser esquecida num fim de semana.

  2. A VARREDURA IGNORAVA A CAM2. O segmento do 2º ângulo é outro objeto e não
     tem linha em `videos`. `expirar_binarios_do_video` o apagava inline, mas
     só no caminho `RETER_VIDEO_HORAS == 0`; com retenção ligada ele retorna
     cedo (só carimba) e a varredura, que olhava apenas `videos.caminho`, nunca
     o alcançava. Num setup de 2 câmeras, METADE do bucket era imortal.

Rodar:  python tests_storage_varredura.py
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

from datetime import datetime, timezone, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402
from backend import pipeline as pl  # noqa: E402

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


def iso(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


class FakeQ:
    def __init__(self, sb, tabela, modo, payload=None):
        self.sb, self.tabela, self.modo, self.payload = sb, tabela, modo, payload
        self.eqs, self.isnull, self.notnull = {}, [], []

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def eq(self, c, v): self.eqs[c] = v; return self

    def is_(self, c, _v): self.isnull.append(c); return self

    @property
    def not_(self):
        outer = self
        class _N:
            def is_(self, c, _v):
                outer.notnull.append(c); return outer
        return _N()

    def execute(self):
        linhas = self.sb.dados.setdefault(self.tabela, [])
        casam = []
        for l in linhas:
            if any(l.get(c) != v for c, v in self.eqs.items()):
                continue
            if any(l.get(c) is not None for c in self.isnull):
                continue
            if any(l.get(c) is None for c in self.notnull):
                continue
            casam.append(l)
        if self.modo == "update":
            for l in casam:
                l.update(self.payload)
                self.sb.escritas.append((self.tabela, l.get("id")))
        return types.SimpleNamespace(data=[dict(l) for l in casam])


class FakeStorage:
    def __init__(self, sb, bucket): self.sb, self.bucket = sb, bucket
    def list(self, pasta):
        return [{"name": n.split("/")[-1], "metadata": {"size": sz}}
                for n, sz in self.sb.objetos.items()
                if n.rsplit("/", 1)[0] == pasta]
    def remove(self, caminhos):
        for c in caminhos:
            self.sb.objetos.pop(c, None)
            self.sb.removidos.append(c)


class FakeSB:
    def __init__(self, dados=None, objetos=None):
        self.dados = dados or {}
        self.objetos = objetos or {}
        self.removidos, self.escritas = [], []
        sb = self
        class _S:
            def from_(self, bucket): return FakeStorage(sb, bucket)
        self.storage = _S()

    def table(self, nome):
        sb = self
        class T:
            def select(self, *a, **k): return FakeQ(sb, nome, "select")
            def update(self, p): return FakeQ(sb, nome, "update", p)
        return T()


CAM1 = "U/T/seg_cam1.mp4"
CAM2 = "U/T/seg_cam2.mp4"


def cenario():
    return FakeSB(
        dados={
            "videos": [{
                "id": "v1", "empresa": "U", "caminho": CAM1,
                "processado_em": iso(hours=48), "frames_aquecidos_em": iso(hours=48),
                "video_removido_em": None,
            }],
            "segmentos": [{
                "id": "s1", "empresa": "U", "storage_path": CAM2,
                "status": "concluido", "processado_em": iso(hours=48),
                "storage_removido_em": None,
            }],
        },
        objetos={CAM1: 3_000_000, CAM2: 2_000_000},
    )


print("\n[1] O FURO DA CAM2 — era metade do bucket")
sb = cenario()
r = pl.varrer_videos_expirados(sb, empresa="U")
check("apagou a cam1", CAM1 in sb.removidos, sb.removidos)
check("apagou a CAM2 (era o que nunca acontecia)", CAM2 in sb.removidos, sb.removidos)
check("contabiliza os dois separadamente",
      r["apagados"] == 1 and r["cam2_apagados"] == 1, r)
check("total_objetos soma os dois", r["total_objetos"] == 2, r)
check("mede os MB liberados", r["total_mb"] == 5.0, r)
check("carimba video_removido_em", sb.dados["videos"][0]["video_removido_em"])
check("carimba storage_removido_em na cam2",
      sb.dados["segmentos"][0]["storage_removido_em"])

print("\n[2] Idempotente — a 2ª passada não acha nada")
r2 = pl.varrer_videos_expirados(sb, empresa="U")
check("nada a apagar na 2ª passada", r2["total_objetos"] == 0, r2)

print("\n[3] dry_run mede sem apagar")
sb = cenario()
r = pl.varrer_videos_expirados(sb, empresa="U", dry_run=True)
check("conta os dois objetos", r["total_objetos"] == 2, r)
check("mede os MB", r["total_mb"] == 5.0, r)
check("NÃO removeu nada do storage", not sb.removidos, sb.removidos)
check("NÃO carimbou nada no banco", not sb.escritas, sb.escritas)
check("marca o relatório como dry_run", r["dry_run"] is True)

print("\n[4] A janela de retenção é respeitada nos DOIS")
_orig = pl.RETER_VIDEO_HORAS
pl.RETER_VIDEO_HORAS = 24.0
sb = FakeSB(
    dados={
        "videos": [{"id": "v1", "empresa": "U", "caminho": CAM1,
                    "frames_aquecidos_em": iso(hours=2),
                    "video_removido_em": None}],
        "segmentos": [{"id": "s1", "empresa": "U", "storage_path": CAM2,
                       "status": "concluido", "processado_em": iso(hours=2),
                       "storage_removido_em": None}],
    },
    objetos={CAM1: 3_000_000, CAM2: 2_000_000},
)
r = pl.varrer_videos_expirados(sb, empresa="U")
check("vídeo recente fica (dentro das 24h)", CAM1 not in sb.removidos)
check("segmento cam2 recente também fica", CAM2 not in sb.removidos)
check("ambos contados como pulados",
      r["pulados"] == 1 and r["cam2_pulados"] == 1, r)
pl.RETER_VIDEO_HORAS = _orig

print("\n[5] Escopo e segurança")
sb = cenario()
sb.dados["videos"][0]["empresa"] = "OUTRA"
sb.dados["segmentos"][0]["empresa"] = "OUTRA"
pl.varrer_videos_expirados(sb, empresa="U")
check("não toca em objeto de outra empresa", not sb.removidos, sb.removidos)

sb = cenario()
sb.dados["segmentos"][0]["status"] = "pendente"
pl.varrer_videos_expirados(sb, empresa="U")
check("segmento AINDA NÃO processado não é apagado", CAM2 not in sb.removidos)

sb = cenario()
sb.dados["videos"][0]["caminho"] = "/tmp/local.mp4"
pl.varrer_videos_expirados(sb, empresa="U")
check("upload legado com path local é ignorado",
      "/tmp/local.mp4" not in sb.removidos)

src = Path("backend/pipeline.py").read_text()
check("a varredura nunca lista o bucket por prefixo (frames são a evidência)",
      ".list(" not in src.split("def varrer_videos_expirados")[1].split("def ")[0])

print("\n[6] O AGENDAMENTO — item que mais importa")
main_src = Path("backend/main.py").read_text()
check("existe throttle configurável", "VARREDURA_INTERVALO_MIN" in main_src)
check("engatado no HEARTBEAT (o relógio garantido 24/7)",
      '_varrer_storage_com_throttle(sb, user.empresa, "heartbeat")' in main_src)
check("com rede secundária no startup",
      '_varrer_storage_com_throttle(make_supabase_client(), None, "startup")' in main_src)
check("é NÃO-FATAL (não pode derrubar o heartbeat nem o boot)",
      "não-fatal" in main_src.split("def _varrer_storage_com_throttle")[1][:900])
check("o motivo do desenho está escrito",
      "não é um mecanismo" in main_src)

import backend.main as mn  # noqa: E402
mn._ULTIMA_VARREDURA["ts"] = 0.0
chamadas = []
pl_orig = mn.varrer_videos_expirados
mn.varrer_videos_expirados = lambda sb, empresa=None: (
    chamadas.append(empresa) or {"total_objetos": 1, "total_mb": 1.0})
mn._varrer_storage_com_throttle(None, "U", "teste")
mn._varrer_storage_com_throttle(None, "U", "teste")
mn._varrer_storage_com_throttle(None, "U", "teste")
check("o throttle deixa passar 1 e barra as seguintes", len(chamadas) == 1, chamadas)

mn._ULTIMA_VARREDURA["ts"] = 0.0
def _explode(sb, empresa=None): raise RuntimeError("storage fora")
mn.varrer_videos_expirados = _explode
try:
    mn._varrer_storage_com_throttle(None, "U", "teste")
    subiu = False
except Exception:
    subiu = True
check("falha da varredura NÃO propaga para o heartbeat", subiu is False)
mn.varrer_videos_expirados = pl_orig

print("\n[7] Frames novos: menores por resolução, não só por compressão")
check("qualidade cai para 60", pl.FRAME_QUALIDADE == 60, pl.FRAME_QUALIDADE)
check("largura máxima de 640", pl.FRAME_MAX_W == 640, pl.FRAME_MAX_W)
check("ambos regulados por env",
      "KV_FRAME_QUALIDADE" in src and "KV_FRAME_MAX_W" in src)
check("usa INTER_AREA (o certo para REDUZIR)", "INTER_AREA" in src)
check("o frame de REFERÊNCIA de zonas fica em resolução cheia",
      src.count("qualidade=85, max_w=0") == 2, src.count("qualidade=85, max_w=0"))
check("redimensionamento que falha não derruba o frame",
      "redimensionamento falhou" in src)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
