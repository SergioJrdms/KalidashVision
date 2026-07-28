"""Fase 54 — expiração do binário de vídeo após aquecer o cache de frames.

Cobre os critérios de aceitação sem Storage e sem banco:
  1) chaves corretas + carimbos   3) falha no aquecimento NÃO apaga
  4) varredura idempotente        5) KV_RETER_VIDEO_HORAS=24 preserva o vídeo
Rodar:  python tests_retencao_video.py
"""
import importlib
import sys
import types
import os
from datetime import datetime, timedelta, timezone

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
sys.modules["numpy"].array = lambda s, dtype=None: [list(r) for r in s]
sys.modules["cv2"].pointPolygonTest = lambda *a, **k: -1.0
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


UTC = timezone.utc


class FakeStorage:
    """Bucket em memória: guarda o que foi gravado e o que foi apagado."""
    def __init__(self, objetos=None):
        self.objs = dict(objetos or {})       # caminho → tamanho em bytes
        self.removidos = []
        self.uploads = []

    def from_(self, _bucket):
        return self

    def list(self, prefixo):
        import posixpath
        out = []
        for k, tam in self.objs.items():
            if posixpath.dirname(k) == prefixo:
                out.append({"name": posixpath.basename(k), "metadata": {"size": tam}})
        return out

    def upload(self, key, data, _opts=None):
        self.uploads.append(key)
        self.objs[key] = len(data or b"")

    def remove(self, caminhos):
        for c in caminhos:
            self.removidos.append(c)
            self.objs.pop(c, None)


class FakeTable:
    def __init__(self, sb, nome):
        self.sb, self.nome = sb, nome
        self._upd = None

    def select(self, *a, **k): return self
    def eq(self, campo, valor): self.sb.filtros.append((campo, valor)); return self
    def is_(self, *a, **k): return self
    def limit(self, *a, **k): return self

    @property
    def not_(self): return self

    def update(self, d):
        self._upd = d
        self.sb.updates.setdefault(self.nome, []).append(d)
        return self

    def execute(self):
        return types.SimpleNamespace(data=self.sb.linhas.get(self.nome, []))


class FakeSB:
    def __init__(self, storage, linhas=None):
        self.storage = storage
        self.linhas = linhas or {}
        self.updates = {}
        self.filtros = []

    def table(self, nome): return FakeTable(self, nome)


def recarregar(env=None):
    """Reimporta o pipeline com um ambiente (as envs são lidas no import)."""
    for k, v in (env or {}).items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    import backend.pipeline as pl
    return importlib.reload(pl)


pl = recarregar({"KV_RETER_VIDEO_HORAS": "0"})

print("\n[1] Chaves de cache — fonte ÚNICA, os dois lados batem")
CAM = "uniao/torneamento/abc_video.mp4"
k0 = pl.chave_frame_evento(CAM, "ev-123", 0)
check("chave de evento usa o prefixo __frames",
      k0 == "uniao/torneamento/__frames/ev-123_v2_0.jpg", k0)
check("FRAMES_VER é a constante única", pl.FRAMES_VER == "v2" and pl.FRAMES_VER in k0)
ks = pl.chave_frame_segmento(CAM, "seg-9", 12.4, 71.6, 2)
check("chave de segmento arredonda igual ao endpoint",
      ks == "uniao/torneamento/__frames/seg_seg-9_12_72_2.jpg", ks)
# main.py tem que importar as MESMAS funções (sem "v2" hardcoded)
main_src = open("backend/main.py").read()
check("main.py NÃO hardcoda a versão do frame", '_FRAMES_VER = "v2"' not in main_src)
check("main.py importa chave_frame_evento", "chave_frame_evento" in main_src)

print("\n[2] Critério 1 — processamento OK apaga o binário e carimba")
st = FakeStorage({"uniao/torneamento/abc_video.mp4": 25_000_000,
                  "uniao/torneamento/cam2_seg.mp4": 20_000_000})
sb = FakeSB(st)
r = pl.expirar_binarios_do_video(sb, "vid-1", CAM, frames_ok=True,
                                 storage_path_sec="uniao/torneamento/cam2_seg.mp4",
                                 segmento_id_sec="seg-9")
check("removido = True", r["removido"] is True, r)
check("binário da cam1 apagado", CAM in st.removidos, st.removidos)
check("binário da cam2 TAMBÉM apagado (não tem linha em videos)",
      "uniao/torneamento/cam2_seg.mp4" in st.removidos, st.removidos)
check("MB liberados contabilizados (~45MB)", 44 <= r["mb"] <= 46, r["mb"])
ups = sb.updates.get("videos", [])
check("frames_aquecidos_em carimbado", any("frames_aquecidos_em" in u for u in ups), ups)
check("video_removido_em carimbado", any("video_removido_em" in u for u in ups), ups)
check("segmento da cam2 carimbado",
      any("storage_removido_em" in u for u in sb.updates.get("segmentos", [])),
      sb.updates.get("segmentos"))
check("NENHUM objeto de __frames/ foi tocado",
      not any("__frames" in c for c in st.removidos), st.removidos)

print("\n[3] Critério 3 — falha no aquecimento NÃO apaga")
st2 = FakeStorage({CAM: 25_000_000})
sb2 = FakeSB(st2)
r2 = pl.expirar_binarios_do_video(sb2, "vid-2", CAM, frames_ok=False)
check("removido = False", r2["removido"] is False, r2)
check("motivo explícito no retorno", "cache de frames" in r2["motivo"], r2["motivo"])
check("binário PRESERVADO", CAM in st2.objs, st2.objs)
check("nada foi carimbado", not sb2.updates.get("videos"), sb2.updates)

print("\n[4] Caminho local/legado nunca é apagado")
r3 = pl.expirar_binarios_do_video(FakeSB(FakeStorage()), "vid-3",
                                  "/tmp/local_video.mp4", frames_ok=True)
check("path local → não remove", r3["removido"] is False, r3)
check("motivo cita Storage", "Storage" in r3["motivo"], r3["motivo"])
r4 = pl.expirar_binarios_do_video(FakeSB(FakeStorage()), "vid-4", None, frames_ok=True)
check("caminho ausente → não remove", r4["removido"] is False, r4)

print("\n[5] Critério 5 — KV_RETER_VIDEO_HORAS=24 preserva o vídeo")
pl24 = recarregar({"KV_RETER_VIDEO_HORAS": "24"})
st5 = FakeStorage({CAM: 25_000_000})
sb5 = FakeSB(st5)
r5 = pl24.expirar_binarios_do_video(sb5, "vid-5", CAM, frames_ok=True)
check("retenção ativa → NÃO remove agora", r5["removido"] is False, r5)
check("binário sobrevive", CAM in st5.objs)
check("mas frames_aquecidos_em É carimbado (a varredura precisa dele)",
      any("frames_aquecidos_em" in u for u in sb5.updates.get("videos", [])),
      sb5.updates)
check("motivo cita a retenção", "reten" in r5["motivo"], r5["motivo"])

print("\n[6] Critério 4 — varredura idempotente")
pl0 = recarregar({"KV_RETER_VIDEO_HORAS": "0"})
antigo = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
linhas = {"videos": [
    {"id": "v1", "caminho": "e/p/a.mp4", "empresa": "U", "frames_aquecidos_em": antigo},
    {"id": "v2", "caminho": "e/p/b.mp4", "empresa": "U", "frames_aquecidos_em": antigo},
]}
st6 = FakeStorage({"e/p/a.mp4": 10_000_000, "e/p/b.mp4": 5_000_000})
sb6 = FakeSB(st6, linhas)
r6 = pl0.varrer_videos_expirados(sb6, empresa="U")
check("1ª passada apaga os 2", r6["apagados"] == 2, r6)
check("MB somados (~15MB)", 14 <= r6["mb"] <= 16, r6["mb"])
check("objetos sumiram do bucket", st6.objs == {}, st6.objs)

# 2ª passada: o filtro real (video_removido_em is null) já os excluiria; aqui
# simulamos o banco devolvendo lista vazia, que é o que acontece de verdade.
sb7 = FakeSB(FakeStorage(), {"videos": []})
r7 = pl0.varrer_videos_expirados(sb7, empresa="U")
check("2ª passada não apaga nada", r7["apagados"] == 0, r7)
check("2ª passada não falha", "erro" not in r7, r7)

print("\n[7] Varredura respeita a janela de retenção")
pl24b = recarregar({"KV_RETER_VIDEO_HORAS": "24"})
recente = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
st8 = FakeStorage({"e/p/c.mp4": 8_000_000})
sb8 = FakeSB(st8, {"videos": [
    {"id": "v3", "caminho": "e/p/c.mp4", "empresa": "U", "frames_aquecidos_em": recente},
]})
r8 = pl24b.varrer_videos_expirados(sb8, empresa="U")
check("vídeo de 2h com retenção de 24h é PULADO", r8["apagados"] == 0, r8)
check("binário intacto", "e/p/c.mp4" in st8.objs)
st9 = FakeStorage({"e/p/d.mp4": 8_000_000})
sb9 = FakeSB(st9, {"videos": [
    {"id": "v4", "caminho": "e/p/d.mp4", "empresa": "U", "frames_aquecidos_em": antigo},
]})
r9 = pl24b.varrer_videos_expirados(sb9, empresa="U")
check("vídeo de 48h com retenção de 24h é apagado", r9["apagados"] == 1, r9)

print("\n[8] Varredura nunca apaga caminho local nem __frames/")
st10 = FakeStorage({"e/p/__frames/x_v2_0.jpg": 50_000})
sb10 = FakeSB(st10, {"videos": [
    {"id": "v5", "caminho": "/tmp/local.mp4", "empresa": "U", "frames_aquecidos_em": antigo},
]})
r10 = pl0.varrer_videos_expirados(sb10, empresa="U")
check("path local é pulado", r10["apagados"] == 0 and r10["pulados"] == 1, r10)
check("JPEG de __frames/ intacto", "e/p/__frames/x_v2_0.jpg" in st10.objs, st10.objs)

print("\n[9] Leitura de erro do banco não derruba a varredura")
class SBQuebrado(FakeSB):
    def table(self, nome):
        raise RuntimeError("banco fora")
r11 = pl0.varrer_videos_expirados(SBQuebrado(FakeStorage()), empresa="U")
check("erro de banco → retorno limpo, sem exceção", r11["apagados"] == 0 and "erro" in r11, r11)

print(f"\n{'=' * 56}\n== {ok} ok, {fail} fail ==\n{'=' * 56}")
sys.exit(1 if fail else 0)
