"""Job tracker em memória (MVP).

Em produção, troque por Redis/RQ/Celery. Aqui, um dicionário thread-safe
serve para o uso da plataforma com FastAPI BackgroundTasks.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Job:
    id: str
    processo_id: str
    user_id: str
    status: str = "pendente"  # pendente | processando | concluido | erro
    etapa_atual: str = ""
    progresso_pct: int = 0
    mensagem: str = ""
    video_id: str | None = None
    erro: str | None = None
    resultado: dict[str, Any] | None = None
    criado_em: float = field(default_factory=time.time)
    atualizado_em: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "processo_id": self.processo_id,
            "status": self.status,
            "etapa_atual": self.etapa_atual,
            "progresso_pct": self.progresso_pct,
            "mensagem": self.mensagem,
            "video_id": self.video_id,
            "erro": self.erro,
            "resultado": self.resultado,
            "criado_em": self.criado_em,
            "atualizado_em": self.atualizado_em,
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, processo_id: str, user_id: str) -> Job:
        job = Job(id=str(uuid.uuid4()), processo_id=processo_id, user_id=user_id)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **kwargs: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for k, v in kwargs.items():
                setattr(job, k, v)
            job.atualizado_em = time.time()


JOBS = JobStore()
