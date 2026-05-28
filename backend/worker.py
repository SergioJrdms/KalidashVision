"""Worker que executa o pipeline em background.

Roda dentro do mesmo processo do FastAPI via BackgroundTasks. Em produção
com múltiplos vídeos simultâneos, mude para Celery/RQ + GPU dedicada.
"""
from __future__ import annotations

import logging
import os
import tempfile
import traceback
from pathlib import Path

from supabase import Client

from .jobs import JOBS
from .pipeline import (
    make_groq_client,
    make_supabase_client,
    processar_video,
)

log = logging.getLogger("kalidash.worker")

# Carregar YOLO uma vez por processo
_YOLO = None


def _modelo_path(nome: str) -> str:
    """Resolve o caminho do .pt em uma pasta FORA da raiz do projeto.

    Se o Ultralytics baixar o .pt na pasta atual e ela for observada pelo
    `uvicorn --reload`, o reload é disparado no meio do processamento e
    mata a BackgroundTask. Pra evitar isso, baixamos em
    KV_MODELS_DIR (default: %TEMP%/kalidash_models).
    """
    base = Path(os.environ.get("KV_MODELS_DIR", "")) if os.environ.get("KV_MODELS_DIR") else Path(tempfile.gettempdir()) / "kalidash_models"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / nome)


def _get_yolo():
    global _YOLO
    if _YOLO is None:
        from ultralytics import YOLO
        from .pipeline import YOLO_MODEL

        destino = _modelo_path(YOLO_MODEL)
        log.info(f"Carregando YOLO {YOLO_MODEL} → {destino} (uma vez por processo)")
        _YOLO = YOLO(destino)
    return _YOLO


def _baixar_video(sb: Client, storage_path: str) -> str:
    """Baixa o vídeo do Supabase Storage para um tmp local e retorna o path."""
    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")
    data = sb.storage.from_(bucket).download(storage_path)
    suffix = Path(storage_path).suffix or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp.close()
    return tmp.name


def executar_job(
    job_id: str,
    empresa: str,
    processo: str,
    storage_path: str,
    descricao_processo: str | None,
) -> None:
    """Entrypoint do worker. Atualiza o job conforme avança."""
    JOBS.update(
        job_id,
        status="processando",
        etapa_atual="setup",
        progresso_pct=0,
        mensagem="Preparando processamento",
    )

    sb = make_supabase_client()
    groq_client = make_groq_client()
    local_path: str | None = None

    try:
        local_path = _baixar_video(sb, storage_path)

        # Pesos de cada etapa para mapear em progresso global 0-100
        pesos = {
            "setup": (0, 5),
            "deteccao": (5, 35),
            "vlm": (35, 70),
            "cluster": (70, 78),
            "segmentar": (78, 82),
            "persistir": (82, 88),
            "sugestoes": (88, 99),
            "concluido": (99, 100),
        }

        def progress_cb(etapa: str, pct: int, mensagem: str) -> None:
            lo, hi = pesos.get(etapa, (0, 100))
            global_pct = lo + int(pct / 100 * (hi - lo))
            JOBS.update(
                job_id,
                etapa_atual=etapa,
                progresso_pct=global_pct,
                mensagem=mensagem,
            )

        resultado = processar_video(
            empresa=empresa,
            processo=processo,
            video_path=local_path,
            descricao_processo=descricao_processo,
            progress_cb=progress_cb,
            sb=sb,
            groq_client=groq_client,
            yolo_model=_get_yolo(),
        )

        JOBS.update(
            job_id,
            status="concluido",
            etapa_atual="concluido",
            progresso_pct=100,
            mensagem="Pronto",
            video_id=resultado.get("video_id"),
            resultado=resultado,
        )
    except Exception as e:
        log.exception(f"Job {job_id} falhou")
        JOBS.update(
            job_id,
            status="erro",
            erro=f"{type(e).__name__}: {e}",
            mensagem="Falha no processamento",
            resultado={"traceback": traceback.format_exc()},
        )
    finally:
        if local_path and Path(local_path).exists():
            try:
                Path(local_path).unlink()
            except Exception:
                pass
