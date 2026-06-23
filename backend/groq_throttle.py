"""Token bucket TPM POR MODELO (token throttle proativo, in-process).

Mesmo com fila serial + debounce, um único vídeo dispara 3–4 chamadas pesadas
ao gpt-oss-120b em ~10–20s, somando 12–15k tokens. Basta isso pra estourar os
8K TPM do Free Tier. Este módulo "reserva" tokens antes de cada chamada Groq:
se o orçamento dos últimos 60s já estourou, dorme até liberar.

Tudo in-process (deque + threading.Lock por modelo). Sem dependências externas.
Como o backend roda 1 worker uvicorn em 1 vCPU, a estimativa é boa. Em deploy
multi-replica, o throttle vira otimista — documentar no .env.example.

Limites efetivos (com folga de 10%, configuráveis via env):
  - openai/gpt-oss-120b           → 7200 TPM     (limite real: 8000)
  - meta-llama/llama-4-scout-17b  → 27000 TPM    (limite real: 30000)
  - llama-3.3-70b-versatile       → 11000 TPM    (limite real: 12000)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Deque

log = logging.getLogger("kalidash.throttle")


# ═════════════════════════════════════════════════════════════════════════
# Configuração — limites efetivos por modelo, com folga
# ═════════════════════════════════════════════════════════════════════════
def _envf(nome: str, padrao: float) -> float:
    try:
        return float(os.environ.get(nome, str(padrao)))
    except Exception:
        return padrao


_LIMITES_TPM: dict[str, float] = {
    "openai/gpt-oss-120b":               _envf("KV_TPM_GPT_OSS", 7200),
    "meta-llama/llama-4-scout-17b-16e-instruct": _envf("KV_TPM_SCOUT", 27000),
    "llama-3.3-70b-versatile":           _envf("KV_TPM_LLAMA3", 11000),
}

# Janela do bucket (60s = 1 min) — TPM = tokens por MINUTO
_JANELA_S = 60.0


# ═════════════════════════════════════════════════════════════════════════
# Buckets
# ═════════════════════════════════════════════════════════════════════════
# Cada modelo tem seu deque[(ts, tokens)] e seu lock. Lock por modelo evita
# que uma chamada de scout serialize uma de gpt-oss (e vice-versa).
_BUCKETS: dict[str, Deque[tuple[float, int]]] = {}
_LOCKS: dict[str, threading.Lock] = {}
_INIT_LOCK = threading.Lock()


def _bucket(model: str) -> tuple[Deque[tuple[float, int]], threading.Lock]:
    if model not in _BUCKETS:
        with _INIT_LOCK:
            if model not in _BUCKETS:
                _BUCKETS[model] = deque()
                _LOCKS[model] = threading.Lock()
    return _BUCKETS[model], _LOCKS[model]


def estimar_tokens(prompt: str, max_completion_tokens: int) -> int:
    """Estimativa simples: ~4 chars/token + reserva total de saída."""
    if not prompt:
        return max_completion_tokens
    return max(1, len(prompt) // 4) + max(0, max_completion_tokens)


def reserve(model: str, estimated_tokens: int) -> None:
    """Bloqueia até caber a chamada dentro do TPM efetivo do modelo.

    Sem-op para modelos sem limite definido. Não-fatal: qualquer exceção
    interna dorme 0.1s e retorna (para não derrubar o pipeline em caso de
    bug do throttle).
    """
    limite = _LIMITES_TPM.get(model)
    if not limite or limite <= 0:
        return
    if estimated_tokens <= 0:
        return

    bucket, lock = _bucket(model)

    # Loop de espera. A cada iteração: poda janela, soma, dorme ou reserva.
    while True:
        try:
            with lock:
                agora = time.monotonic()
                # 1) Poda: remove entradas fora da janela de 60s
                limite_minimo = agora - _JANELA_S
                while bucket and bucket[0][0] < limite_minimo:
                    bucket.popleft()
                # 2) Soma o que está vivo
                em_uso = sum(t for _, t in bucket)
                if em_uso + estimated_tokens <= limite:
                    # Cabe — reserva já
                    bucket.append((agora, estimated_tokens))
                    return
                # 3) Não cabe. Calcula sleep até a entrada mais antiga sair.
                #    Garante mínimo de 0.5s para não tight-loop sob carga.
                idade_mais_antiga = agora - bucket[0][0]
                sleep_for = max(0.5, _JANELA_S - idade_mais_antiga + 0.1)
        except Exception as e:
            log.warning(f"throttle.reserve falhou ({e}) — segue sem dormir")
            return

        log.info(
            f"[throttle] {model} cheio ({em_uso}+{estimated_tokens}>{limite:.0f} TPM) "
            f"dormindo {sleep_for:.1f}s"
        )
        time.sleep(sleep_for)


def snapshot(model: str) -> dict:
    """Para debug / endpoint de healthcheck."""
    limite = _LIMITES_TPM.get(model)
    if not limite:
        return {"model": model, "tpm_limite": None, "em_uso_60s": 0, "n_entradas": 0}
    bucket, lock = _bucket(model)
    with lock:
        agora = time.monotonic()
        em_uso = sum(t for ts, t in bucket if ts >= agora - _JANELA_S)
        return {
            "model": model,
            "tpm_limite": limite,
            "em_uso_60s": em_uso,
            "n_entradas": len(bucket),
        }
