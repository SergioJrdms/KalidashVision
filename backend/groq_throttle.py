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

# RPM = requests por MINUTO. O Free Tier do Groq limita 30 RPM POR MODELO; a
# etapa VLM dispara ~20 chamadas ao scout quase sem espaçamento (o TPM do scout
# é folgado e não segura a cadência), e somando chat/debouncer concorrentes os
# bursts passam de 30 → 429. Limites efetivos com folga (default 25 < 30):
_LIMITES_RPM: dict[str, float] = {
    "openai/gpt-oss-120b":               _envf("KV_RPM_GPT_OSS", 25),
    "meta-llama/llama-4-scout-17b-16e-instruct": _envf("KV_RPM_SCOUT", 25),
    "llama-3.3-70b-versatile":           _envf("KV_RPM_LLAMA3", 25),
}

# Janela do bucket (60s = 1 min) — TPM = tokens por MINUTO
_JANELA_S = 60.0


# ═════════════════════════════════════════════════════════════════════════
# Cooldown de LIMITE DIÁRIO (TPD) por modelo (Fase 12)
# ═════════════════════════════════════════════════════════════════════════
# O Free Tier do Groq tem teto DIÁRIO (ex.: scout = 500k tokens/dia). Quando
# estoura, retentar no mesmo dia é inútil (o reset é em horas). Ao detectar o
# erro, marcamos um cooldown por modelo: durante ele, as chamadas falham RÁPIDO
# (sem retry, sem "moer") e o worker marca o segmento como erro com mensagem
# clara. Some sozinho quando o tempo passa.
_LIMITE_DIARIO: dict[str, float] = {}   # model -> time.monotonic() até quando pausar
_COOLDOWN_LOCK = threading.Lock()


def marcar_limite_diario(model: str, segundos: float) -> None:
    seg = max(60.0, min(float(segundos or 0), 12 * 3600.0))
    with _COOLDOWN_LOCK:
        _LIMITE_DIARIO[model] = time.monotonic() + seg
    log.warning(
        f"[throttle] {model}: LIMITE DIÁRIO (TPD) atingido — pausando chamadas "
        f"por ~{seg / 60:.0f}min (o Groq reseta o teto do dia)."
    )


def em_limite_diario(model: str) -> float:
    """Segundos restantes de cooldown do limite diário (0.0 = livre)."""
    with _COOLDOWN_LOCK:
        ate = _LIMITE_DIARIO.get(model)
    if not ate:
        return 0.0
    resta = ate - time.monotonic()
    return resta if resta > 0 else 0.0


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
    """Bloqueia até caber a chamada dentro do TPM **e do RPM** efetivos.

    Reusa o mesmo bucket deque[(ts, tokens)]: TPM = soma dos tokens na janela
    de 60s; RPM = número de entradas na janela (1 entrada = 1 request). Só
    reserva quando AMBOS cabem; caso contrário dorme até a entrada mais antiga
    sair da janela (o que libera simultaneamente um slot de RPM e tokens de TPM).

    Sem-op para modelos sem limite definido. Não-fatal: qualquer exceção
    interna loga e retorna (para não derrubar o pipeline em caso de bug do
    throttle).
    """
    limite_tpm = _LIMITES_TPM.get(model)
    limite_rpm = _LIMITES_RPM.get(model)
    if (not limite_tpm or limite_tpm <= 0) and (not limite_rpm or limite_rpm <= 0):
        return
    if estimated_tokens <= 0:
        estimated_tokens = 1  # toda chamada conta ao menos como 1 req no RPM

    bucket, lock = _bucket(model)

    # Loop de espera. A cada iteração: poda janela, mede TPM+RPM, dorme ou reserva.
    while True:
        try:
            with lock:
                agora = time.monotonic()
                # 1) Poda: remove entradas fora da janela de 60s
                limite_minimo = agora - _JANELA_S
                while bucket and bucket[0][0] < limite_minimo:
                    bucket.popleft()
                # 1b) Janela VAZIA → libera SEMPRE. Dormir não reduz o uso abaixo
                #     de zero, então esperar é inútil. Sem isto, UMA chamada cuja
                #     estimativa (len(prompt)//4 + max_tokens) supera o limite
                #     bloquearia o worker PARA SEMPRE (deadlock — trava a fila
                #     serial inteira). É o que travou: TPM 0+7727>7200 em loop.
                if not bucket:
                    if limite_tpm and estimated_tokens > limite_tpm:
                        log.warning(
                            f"[throttle] {model}: chamada única ~{estimated_tokens} tok "
                            f"> limite {limite_tpm:.0f} — liberando (janela vazia)"
                        )
                    bucket.append((agora, estimated_tokens))
                    return
                # 2) Mede o que está vivo: tokens (TPM) e nº de requests (RPM)
                em_uso_tpm = sum(t for _, t in bucket)
                em_uso_rpm = len(bucket)
                cabe_tpm = (not limite_tpm) or (em_uso_tpm + estimated_tokens <= limite_tpm)
                cabe_rpm = (not limite_rpm) or (em_uso_rpm + 1 <= limite_rpm)
                if cabe_tpm and cabe_rpm:
                    # Cabe nos dois — reserva já
                    bucket.append((agora, estimated_tokens))
                    return
                # 3) Não cabe. Dorme até a entrada mais antiga sair da janela.
                #    Mínimo de 0.5s para não tight-loop sob carga.
                idade_mais_antiga = agora - bucket[0][0] if bucket else 0.0
                sleep_for = max(0.5, _JANELA_S - idade_mais_antiga + 0.1)
                motivo = "RPM" if not cabe_rpm else "TPM"
        except Exception as e:
            log.warning(f"throttle.reserve falhou ({e}) — segue sem dormir")
            return

        if motivo == "RPM":
            log.info(
                f"[throttle] {model} cheio (RPM {em_uso_rpm}+1>{limite_rpm:.0f}) "
                f"dormindo {sleep_for:.1f}s"
            )
        else:
            log.info(
                f"[throttle] {model} cheio (TPM {em_uso_tpm}+{estimated_tokens}>{limite_tpm:.0f}) "
                f"dormindo {sleep_for:.1f}s"
            )
        time.sleep(sleep_for)


def snapshot(model: str) -> dict:
    """Para debug / endpoint de healthcheck."""
    limite_tpm = _LIMITES_TPM.get(model)
    limite_rpm = _LIMITES_RPM.get(model)
    bucket, lock = _bucket(model)
    with lock:
        agora = time.monotonic()
        vivos = [(ts, t) for ts, t in bucket if ts >= agora - _JANELA_S]
        return {
            "model": model,
            "tpm_limite": limite_tpm,
            "tpm_em_uso_60s": sum(t for _, t in vivos),
            "rpm_limite": limite_rpm,
            "rpm_em_uso_60s": len(vivos),
        }
