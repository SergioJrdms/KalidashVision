"""Job tracker com persistência em disco.

Persistir é essencial: o status do job precisa sobreviver a um reload do
uvicorn (`--reload`), que reinicia o processo. O arquivo vive fora da
pasta do projeto pra NÃO disparar o próprio reload quando o status muda.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _default_store_path() -> Path:
    custom = os.environ.get("KV_JOBS_FILE")
    if custom:
        return Path(custom)
    return Path(tempfile.gettempdir()) / "kalidash_jobs.json"


@dataclass
class Job:
    id: str
    processo_id: str
    user_id: str
    status: str = "pendente"
    etapa_atual: str = ""
    progresso_pct: int = 0
    mensagem: str = ""
    video_id: str | None = None
    erro: str | None = None
    resultado: dict[str, Any] | None = None
    criado_em: float = field(default_factory=time.time)
    atualizado_em: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_store_path()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._carregar()

    def _carregar(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            for jid, d in data.items():
                self._jobs[jid] = Job(**d)
        except Exception:
            # arquivo corrompido — começa do zero
            self._jobs = {}

    def _salvar_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({jid: j.to_dict() for jid, j in self._jobs.items()}, f)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def create(self, processo_id: str, user_id: str) -> Job:
        job = Job(id=str(uuid.uuid4()), processo_id=processo_id, user_id=user_id)
        with self._lock:
            self._jobs[job.id] = job
            self._salvar_locked()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            # re-leitura preguiçosa caso o arquivo tenha sido atualizado
            # por outra instância do processo (reload, worker separado, etc.)
            if job_id not in self._jobs and self.path.exists():
                self._carregar()
            return self._jobs.get(job_id)

    def update(self, job_id: str, **kwargs: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                # tenta recarregar do disco — outra instância pode ter criado
                self._carregar()
                job = self._jobs.get(job_id)
                if not job:
                    return
            for k, v in kwargs.items():
                setattr(job, k, v)
            job.atualizado_em = time.time()
            self._salvar_locked()


JOBS = JobStore()
