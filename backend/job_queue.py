"""Fila in-process serial de jobs de processamento.

Substitui o `BackgroundTasks.add_task` direto do FastAPI por uma fila controlada
com worker daemon. Motivo: o Groq Free Tier do gpt-oss-120b é apertado (30 RPM
/ 8K TPM) e uma rajada de 200 uploads do edge_runner --processar cria 200 jobs
"paralelos" — as chamadas Groq disparam concorrentes e estouram o limite.

Aqui ficamos com 1 vídeo por vez. O upload responde imediato com job_id
(202-like), o worker thread vai consumindo a fila em ordem FIFO. Bem mais
lento na rajada (200 vídeos × ~60-90s = 3-5h), mas nunca recebe 429.

Reusa o JobStore existente (backend/jobs.py) para os estados canônicos
(pendente/processando/concluido/erro) e mantém apenas uma lista ordenada de
job_ids em /tmp/kalidash_queue.json (atomic write).
"""
from __future__ import annotations

import json
import logging
import os
import queue
import tempfile
import threading
import time
from pathlib import Path

from .jobs import JOBS

log = logging.getLogger("kalidash.job_queue")


# ═════════════════════════════════════════════════════════════════════════
# Persistência da fila (atomic write — mesmo padrão de backend/jobs.py:65)
# ═════════════════════════════════════════════════════════════════════════
def _default_queue_path() -> Path:
    custom = os.environ.get("KV_QUEUE_FILE")
    if custom:
        return Path(custom)
    return Path(tempfile.gettempdir()) / "kalidash_queue.json"


_QUEUE_PATH = _default_queue_path()
_FILE_LOCK = threading.Lock()


def _ler_disco() -> list[dict]:
    if not _QUEUE_PATH.exists():
        return []
    try:
        with open(_QUEUE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _salvar_disco(itens: list[dict]) -> None:
    with _FILE_LOCK:
        try:
            _QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _QUEUE_PATH.with_suffix(_QUEUE_PATH.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(itens, f)
            os.replace(tmp, _QUEUE_PATH)
        except Exception as e:
            log.warning(f"falha ao salvar queue file ({e}) — segue em memória")


def _adicionar_disco(payload: dict) -> None:
    itens = _ler_disco()
    itens.append(payload)
    _salvar_disco(itens)


def _remover_disco(job_id: str) -> None:
    itens = _ler_disco()
    itens = [x for x in itens if x.get("job_id") != job_id]
    _salvar_disco(itens)


# ═════════════════════════════════════════════════════════════════════════
# Fila + worker thread
# ═════════════════════════════════════════════════════════════════════════
_Q: queue.Queue[dict] = queue.Queue()
_WORKER_INICIADO = False
_WORKER_LOCK = threading.Lock()


def _executar_um(payload: dict) -> None:
    """Roda um job (via worker.executar_job). NÃO levanta — captura tudo."""
    job_id = payload["job_id"]
    try:
        # Import lazy para evitar custo de import no startup quando ainda não
        # processou nada (ultralytics/torch são pesados).
        from .worker import executar_job

        executar_job(
            job_id,
            payload["empresa"],
            payload["processo"],
            payload["storage_path"],
            payload.get("descricao"),
            payload.get("nome_original"),
            cam_id=payload.get("cam_id"),
            gravado_em=payload.get("gravado_em"),
        )
    except Exception as e:
        log.exception(f"job_queue: job {job_id} falhou no executar_job: {e}")
        try:
            JOBS.update(job_id, status="erro", erro=str(e), mensagem="erro no worker")
        except Exception:
            pass
    finally:
        try:
            _remover_disco(job_id)
        except Exception:
            pass


def _worker_loop() -> None:
    log.info("[job_queue] worker iniciado")
    while True:
        try:
            payload = _Q.get()
        except Exception as e:
            log.exception(f"job_queue: get falhou ({e}) — dormindo 5s")
            time.sleep(5)
            continue
        try:
            _executar_um(payload)
        except Exception as e:
            log.exception(f"job_queue: ciclo falhou ({e})")
        finally:
            try:
                _Q.task_done()
            except Exception:
                pass


# ═════════════════════════════════════════════════════════════════════════
# API pública
# ═════════════════════════════════════════════════════════════════════════
def enqueue(
    job_id: str,
    empresa: str,
    processo: str,
    storage_path: str,
    descricao: str | None,
    nome_original: str | None,
    cam_id: str | None = None,
    gravado_em: str | None = None,
) -> None:
    """Adiciona um job à fila. O upload retorna imediato; o worker daemon
    processa em série.

    `cam_id`/`gravado_em` são metadados da Fase 1 multi-câmera (additive,
    opcionais). Persistem no payload e chegam até o INSERT em `videos`.
    """
    payload = {
        "job_id": job_id,
        "empresa": empresa,
        "processo": processo,
        "storage_path": storage_path,
        "descricao": descricao,
        "nome_original": nome_original,
        "cam_id": cam_id,
        "gravado_em": gravado_em,
        "enq_ts": time.time(),
    }
    _adicionar_disco(payload)
    _Q.put(payload)


def start_worker_thread() -> None:
    """Inicia (uma vez por processo) o worker daemon. Chamado no startup."""
    global _WORKER_INICIADO
    with _WORKER_LOCK:
        if _WORKER_INICIADO:
            return
        t = threading.Thread(target=_worker_loop, name="job_queue_worker", daemon=True)
        t.start()
        _WORKER_INICIADO = True


def bootstrap() -> None:
    """Carrega itens do disco + recupera jobs "órfãos" do JobStore.

    Regras:
      - Itens no queue file → reenfileira (mantém ordem do JSON).
      - Jobs no JobStore com status "processando" → marca como erro
        ("interrompido por restart"). O pipeline NÃO é idempotente, melhor
        falhar visivelmente do que retomar do meio.
      - Jobs no JobStore com status "pendente" SEM entrada no queue file →
        marca como erro (perdemos o payload do upload, não dá pra reenfileirar).
    """
    itens = _ler_disco()
    ids_na_fila = {x.get("job_id") for x in itens if x.get("job_id")}
    log.info(f"[job_queue] bootstrap: {len(itens)} item(ns) no queue file")

    # 1) Sobe os itens do disco para a fila em memória
    for payload in itens:
        try:
            _Q.put(payload)
        except Exception as e:
            log.warning(f"[job_queue] falha ao reenfileirar {payload.get('job_id')}: {e}")

    # 2) Marca processando-órfãos como erro
    try:
        # Acesso direto à coleção interna (snapshot) — JobStore não expõe iteração.
        with JOBS._lock:  # type: ignore[attr-defined]
            for jid, job in list(JOBS._jobs.items()):  # type: ignore[attr-defined]
                if job.status == "processando":
                    log.warning(f"[job_queue] job {jid} estava processando — marcando erro")
                    job.status = "erro"
                    job.erro = "interrompido por restart"
                    job.mensagem = "Reinício do servidor"
                elif job.status == "pendente" and jid not in ids_na_fila:
                    log.warning(f"[job_queue] job {jid} pendente sem payload — marcando erro")
                    job.status = "erro"
                    job.erro = "payload do upload perdido no restart"
                    job.mensagem = "Reenvie o vídeo"
            JOBS._salvar_locked()  # type: ignore[attr-defined]
    except Exception as e:
        log.warning(f"[job_queue] varredura do JobStore falhou: {e}")
