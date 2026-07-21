"""Worker que executa o pipeline em background.

Roda dentro do mesmo processo do FastAPI via BackgroundTasks. Em produção
com múltiplos vídeos simultâneos, mude para Celery/RQ + GPU dedicada.
"""
from __future__ import annotations

import gc
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


def _marcar_segmentos(sb, ids: list[str | None], **campos) -> None:
    """Atualiza linhas da inbox `segmentos` (Fase 6). Não-fatal."""
    for sid in [i for i in ids if i]:
        try:
            sb.table("segmentos").update(campos).eq("id", sid).execute()
        except Exception as e:
            log.warning(f"falha ao marcar segmento {sid}: {e}")


def _buscar_zonas_por_cam(sb, empresa: str, processo: str) -> dict[str, dict]:
    """Fase 28: zonas ATIVAS do processo agrupadas por cam_id, no formato que
    `processar_video`/`_build_rois` consomem:
      {cam_id: {nome: {pts_rel, descricao_contexto, papel}}}
    Não-fatal — falha vira {} (pipeline segue no comportamento legado)."""
    try:
        r = (
            sb.table("zonas_camera")
            .select("cam_id, nome, papel, pts_rel, descricao_contexto")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .eq("ativo", True)
            .execute()
        )
        zonas: dict[str, dict] = {}
        for z in r.data or []:
            pts = z.get("pts_rel") or []
            if not z.get("cam_id") or not z.get("nome") or len(pts) < 3:
                continue
            zonas.setdefault(z["cam_id"], {})[z["nome"]] = {
                "pts_rel": pts,
                "descricao_contexto": z.get("descricao_contexto"),
                "papel": z.get("papel"),
            }
        return zonas
    except Exception as e:
        log.warning(f"zonas_camera não carregadas ({e}) — pipeline sem zonas")
        return {}


def executar_job(
    job_id: str,
    empresa: str,
    processo: str,
    storage_path: str,
    descricao_processo: str | None,
    nome_original: str | None = None,
    cam_id: str | None = None,
    gravado_em: str | None = None,
    storage_path_secundario: str | None = None,
    cam_id_secundario: str | None = None,
    nome_secundario: str | None = None,
    segmento_id: str | None = None,
    segmento_id_secundario: str | None = None,
) -> None:
    """Entrypoint do worker. Atualiza o job conforme avança.

    Fase 6: se `storage_path_secundario` vier, baixa também a cam2 e processa o
    PAR junto (dual-angle); marca os `segmentos` (primário+secundário) ao fim.
    """
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
    local_path_sec: str | None = None
    _marcar_segmentos(sb, [segmento_id, segmento_id_secundario], status="processando")

    try:
        local_path = _baixar_video(sb, storage_path)
        if storage_path_secundario:
            try:
                local_path_sec = _baixar_video(sb, storage_path_secundario)
            except Exception as e:
                log.warning(f"2º ângulo não baixou ({e}) — processa só a cam1")
                local_path_sec = None

        # Pesos de cada etapa para mapear em progresso global 0-100
        pesos = {
            "setup": (0, 5),
            "deteccao": (5, 35),
            "vlm": (35, 70),
            "cluster": (70, 76),
            "segmentar": (76, 80),
            "persistir": (80, 86),
            "sugestoes": (86, 92),
            "lean": (92, 95),
            "perguntas": (95, 99),
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

        # Fase 28: zonas por câmera (posto do operador etc.). Sem zonas ou em
        # upload manual (cam_id=None) → None → pipeline legado, sem filtro.
        zonas = _buscar_zonas_por_cam(sb, empresa, processo)
        resultado = processar_video(
            empresa=empresa,
            processo=processo,
            video_path=local_path,
            descricao_processo=descricao_processo,
            progress_cb=progress_cb,
            sb=sb,
            groq_client=groq_client,
            yolo_model=_get_yolo(),
            nome_video=nome_original or Path(storage_path).name,
            caminho_storage=storage_path,
            cam_id=cam_id,
            gravado_em=gravado_em,
            video_path_secundario=local_path_sec,
            cam_id_secundario=cam_id_secundario,
            nome_secundario=nome_secundario,
            storage_path_secundario=storage_path_secundario,
            segmento_id_secundario=segmento_id_secundario,
            rois_contexto=(zonas.get(cam_id) if cam_id else None),
            rois_contexto_secundario=(
                zonas.get(cam_id_secundario) if cam_id_secundario else None
            ),
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
        # Inbox: marca o par como concluído (ligado ao vídeo do primário).
        from datetime import datetime as _dt
        _marcar_segmentos(
            sb, [segmento_id, segmento_id_secundario],
            status="concluido",
            video_id=resultado.get("video_id"),
            processado_em=_dt.utcnow().isoformat(),
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
        _marcar_segmentos(
            sb, [segmento_id, segmento_id_secundario],
            status="erro", erro=f"{type(e).__name__}: {e}",
        )
    finally:
        for p in (local_path, local_path_sec):
            if p and Path(p).exists():
                try:
                    Path(p).unlink()
                except Exception:
                    pass
        # Libera memória entre jobs: o worker é 1 processo longo-vivo e cada
        # vídeo aloca frames/arrays grandes (OpenCV/YOLO). Sem isso, o RSS sobe
        # a cada job e o Render (2GB) reinicia por OOM. gc.collect é barato
        # perto do custo de processar 1 vídeo.
        try:
            gc.collect()
        except Exception:
            pass
