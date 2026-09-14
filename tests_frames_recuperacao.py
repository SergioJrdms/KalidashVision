"""Fase 115 — evidência permanente para eventos detalhados e legado.

Roda sem banco, Storage real ou OpenCV:
    python tests_frames_recuperacao.py
"""
import importlib
import os
from pathlib import Path
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
sys.modules["numpy"].array = lambda seq, dtype=None: [list(row) for row in seq]
sys.modules["cv2"].pointPolygonTest = lambda *args, **kwargs: -1.0
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")

import backend.pipeline as pipeline

pl = importlib.reload(pipeline)
ok = fail = 0


def check(nome, condicao, detalhe=""):
    global ok, fail
    if condicao:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {detalhe}")


print("\n[1] Seleção segura do cache correspondente")
alvo = {
    "id": "detalhe", "video_id": "video-1",
    "tempo_inicio_s": 216.0, "tempo_fim_s": 240.0,
}
candidatos = [
    {"id": "outro-video", "video_id": "video-2", "tempo_inicio_s": 216, "tempo_fim_s": 240},
    {"id": "parcial", "video_id": "video-1", "tempo_inicio_s": 200, "tempo_fim_s": 220},
    {"id": "resumo", "video_id": "video-1", "tempo_inicio_s": 180, "tempo_fim_s": 240, "principal": True},
    {"id": "interno", "video_id": "video-1", "tempo_inicio_s": 220, "tempo_fim_s": 230, "principal": False},
    {"id": "refutado", "video_id": "video-1", "tempo_inicio_s": 216, "tempo_fim_s": 240, "validacao_correto": False},
    {"id": "mesmo-instante", "video_id": "video-1", "tempo_inicio_s": 216, "tempo_fim_s": 240, "principal": False},
]
ordenados = pl.candidatos_frames_compativeis(alvo, candidatos)
ids = [item["id"] for item in ordenados]
check("mesmo intervalo vem primeiro", ids[:1] == ["mesmo-instante"], ids)
check("intervalo interno vem antes do resumo", ids[1:] == ["interno", "resumo"], ids)
check("sobreposição parcial é rejeitada", "parcial" not in ids, ids)
check("outro vídeo é rejeitado", "outro-video" not in ids, ids)
check("evento refutado é rejeitado", "refutado" not in ids, ids)


print("\n[2] Próximos vídeos aquecem principal e auditoria")


class Storage:
    def __init__(self):
        self.uploads = []

    def from_(self, _bucket):
        return self

    def list(self, _prefixo):
        return []

    def upload(self, key, data, _opcoes=None):
        self.uploads.append((key, data))


class Supabase:
    def __init__(self):
        self.storage = Storage()


sb = Supabase()
extrair_original = pl.extrair_3_frames_evento
codificar_original = pl.frame_para_jpeg_bytes
try:
    pl.extrair_3_frames_evento = lambda evento, caminho: [0, 1, 2]
    pl.frame_para_jpeg_bytes = lambda frame: f"jpeg-{frame}".encode()
    stats = pl.pre_extrair_frames(
        sb,
        "Uniao/Torneamento/video.mp4",
        "video-local.mp4",
        [{"principal": True}, {"principal": False}],
        ["principal-id", "auditoria-id"],
        "video-1",
        None,
    )
finally:
    pl.extrair_3_frames_evento = extrair_original
    pl.frame_para_jpeg_bytes = codificar_original

chaves = [chave for chave, _dados in sb.storage.uploads]
check("dois eventos foram aquecidos", stats["eventos"] == 2, stats)
check("principal recebeu três JPEGs", sum("principal-id" in chave for chave in chaves) == 3, chaves)
check("auditoria recebeu três JPEGs", sum("auditoria-id" in chave for chave in chaves) == 3, chaves)
check("nenhuma falha libera a expiração segura", stats["ok"] is True, stats)


print("\n[3] Integração do orquestrador e da API")
pipeline_src = Path("backend/pipeline.py").read_text(encoding="utf-8")
main_src = Path("backend/main.py").read_text(encoding="utf-8")
check(
    "orquestrador combina principais e auditoria",
    "eventos_frames = list(eventos) + list(eventos_auditoria or [])" in pipeline_src,
)
check(
    "endpoint tenta cache compatível após a expiração",
    "pl.candidatos_frames_compativeis(ev, rows)" in main_src,
)
check(
    "resposta identifica recuperação por intervalo",
    '"intervalo_correspondente" if recuperado else "evento_exato"' in main_src,
)

print(f"\n{'=' * 56}\n== {ok} ok, {fail} fail ==\n{'=' * 56}")
sys.exit(1 if fail else 0)
