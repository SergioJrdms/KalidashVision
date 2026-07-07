"""Debouncer dos blocos GLOBAIS de análise (insights + padrões).

Sem debounce, `processar_video` dispara — para CADA vídeo — uma rajada de
chamadas a `gpt-oss-120b` na cauda do pipeline:

    gerar_insights_globais(empresa)         (~5-6k tokens)
    analisar_padroes_globais(empresa)       (~3-5k tokens)
    analisar_padroes_processo(processo)     (~3-5k tokens)

Para uma rajada de 200 segmentos (típico do edge_runner --processar), isso é
~600 chamadas que recomputam o mesmo snapshot da empresa muitas vezes. Vira o
gargalo do Groq Free Tier (8K TPM no gpt-oss-120b).

Este módulo COALESCE essas chamadas em "flushes" disparados N segundos depois
da ÚLTIMA marcação de "dirty" (silêncio). API mínima:

    marcar_dirty_empresa(empresa)          # chamado no fim do processar_video
    marcar_dirty_processo(empresa, processo)
    bootstrap(supabase_factory, groq_factory)   # chamado no @app.on_event('startup')

A persistência em /tmp/kalidash_dirty.json (mesmo padrão atomic-write do
JobStore em backend/jobs.py) garante que um restart do uvicorn não perca o
flush — os timers são reagendados com o restante da janela de silêncio.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger("kalidash.debouncer")


# ═════════════════════════════════════════════════════════════════════════
# Configuração
# ═════════════════════════════════════════════════════════════════════════
_DEBOUNCE_EMPRESA_S = float(os.environ.get("KV_DEBOUNCE_EMPRESA_S", "300"))   # 5 min
_DEBOUNCE_PROCESSO_S = float(os.environ.get("KV_DEBOUNCE_PROCESSO_S", "120")) # 2 min


def _default_store_path() -> Path:
    custom = os.environ.get("KV_DIRTY_FILE")
    if custom:
        return Path(custom)
    return Path(tempfile.gettempdir()) / "kalidash_dirty.json"


_STORE_PATH = _default_store_path()


# ═════════════════════════════════════════════════════════════════════════
# Estado interno (process-local)
# ═════════════════════════════════════════════════════════════════════════
@dataclass
class _PendingFlush:
    escopo: str           # "empresa" | "processo"
    empresa: str
    processo: str | None  # None se escopo == "empresa"
    last_touch_ts: float = field(default_factory=time.time)


_LOCK = threading.RLock()
_PENDING: dict[tuple[str, str], _PendingFlush] = {}   # (escopo, chave) -> PendingFlush
_TIMERS: dict[tuple[str, str], threading.Timer] = {}  # mesmo key, separado p/ não serializar

# Factories (injetadas no bootstrap) para criar cliente Supabase + Groq sem
# importar pipeline aqui (evita ciclo de import).
_sb_factory: Callable[[], object] | None = None
_groq_factory: Callable[[], object] | None = None


def _chave(escopo: str, empresa: str, processo: str | None) -> tuple[str, str]:
    return (escopo, f"{empresa}|{processo or ''}")


# ═════════════════════════════════════════════════════════════════════════
# Persistência (atomic write, mesmo padrão de backend/jobs.py)
# ═════════════════════════════════════════════════════════════════════════
def _salvar_locked() -> None:
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STORE_PATH.with_suffix(_STORE_PATH.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([asdict(p) for p in _PENDING.values()], f)
        os.replace(tmp, _STORE_PATH)
    except Exception as e:
        log.warning(f"falha ao salvar dirty file ({e}) — segue em memória")


def _carregar() -> list[_PendingFlush]:
    if not _STORE_PATH.exists():
        return []
    try:
        with open(_STORE_PATH, encoding="utf-8") as f:
            dados = json.load(f)
        return [_PendingFlush(**d) for d in dados]
    except Exception:
        return []


# ═════════════════════════════════════════════════════════════════════════
# Flushes (rodam fora do _LOCK — chamam Groq e Supabase)
# ═════════════════════════════════════════════════════════════════════════
def _flush_empresa(empresa: str) -> None:
    """Recalcula insights + padrões globais da empresa. Cada bloco é NÃO-FATAL
    individualmente, no mesmo espírito da cauda do processar_video."""
    if _sb_factory is None or _groq_factory is None:
        log.warning("debouncer não inicializado — pulando flush empresa")
        return

    key = _chave("empresa", empresa, None)
    with _LOCK:
        _PENDING.pop(key, None)
        _TIMERS.pop(key, None)
        _salvar_locked()

    log.info(f"[debouncer] flush EMPRESA {empresa}")
    try:
        # Import lazy: evita ciclo com pipeline + carrega ultralytics/torch só
        # quando o pipeline (de upload) precisa; o flush não usa YOLO.
        from .pipeline import (
            agregar_portfolio,
            analisar_padroes_globais,
            gerar_insights_globais,
            montar_snapshot_global,
        )

        sb = _sb_factory()
        gc = _groq_factory()
        portfolio = None
        snapshot_global = None
        try:
            portfolio = agregar_portfolio(sb, empresa)
            snapshot_global = montar_snapshot_global(sb, empresa, portfolio=portfolio)
            gerar_insights_globais(sb, gc, empresa, snapshot_global=snapshot_global)
        except Exception as e:
            log.warning(f"[debouncer] insights_globais falhou (não-fatal): {e}")
        try:
            analisar_padroes_globais(sb, gc, empresa, portfolio=portfolio)
        except Exception as e:
            log.warning(f"[debouncer] padroes_globais falhou (não-fatal): {e}")
    except Exception as e:
        log.warning(f"[debouncer] flush_empresa({empresa}) falhou: {e}")


def _flush_processo(empresa: str, processo: str) -> None:
    """Recalcula padrões do processo. NÃO-FATAL."""
    if _sb_factory is None or _groq_factory is None:
        log.warning("debouncer não inicializado — pulando flush processo")
        return

    key = _chave("processo", empresa, processo)
    with _LOCK:
        _PENDING.pop(key, None)
        _TIMERS.pop(key, None)
        _salvar_locked()

    log.info(f"[debouncer] flush PROCESSO {empresa}/{processo}")
    try:
        from .pipeline import (
            analisar_padroes_processo,
            carregar_memoria_do_negocio,
            construir_bloco_conhecimento_adquirido,
            recomputar_sugestoes_processo,
            resolver_descricao_processo,
        )

        sb = _sb_factory()
        gc = _groq_factory()
        # Recarrega o contexto do processo (descrição + conhecimento adquirido)
        # — o analisar_padroes_processo espera esses blocos como entrada.
        try:
            descricao = resolver_descricao_processo(sb, empresa, processo, None)
        except Exception:
            descricao = ""
        try:
            conhecimento = construir_bloco_conhecimento_adquirido(sb, empresa, processo)
        except Exception:
            conhecimento = ""
        try:
            _ = carregar_memoria_do_negocio(sb, empresa, processo)
        except Exception:
            pass

        try:
            analisar_padroes_processo(
                sb, gc, empresa, processo,
                descricao_processo=descricao,
                conhecimento_adquirido=conhecimento,
            )
        except Exception as e:
            log.warning(f"[debouncer] padroes_processo falhou (não-fatal): {e}")

        # Fase 18: sugestões CURADAS a partir do agregado (1× por rajada, não por
        # vídeo) — conserta o empilhamento (47 p/ 24 vídeos).
        try:
            recomputar_sugestoes_processo(sb, empresa, processo)
        except Exception as e:
            log.warning(f"[debouncer] sugestoes falhou (não-fatal): {e}")
    except Exception as e:
        log.warning(f"[debouncer] flush_processo({empresa}/{processo}) falhou: {e}")


# ═════════════════════════════════════════════════════════════════════════
# API pública
# ═════════════════════════════════════════════════════════════════════════
def _agendar(escopo: str, empresa: str, processo: str | None, atraso_s: float) -> None:
    """Cancela timer anterior e agenda um novo (sob _LOCK)."""
    key = _chave(escopo, empresa, processo)
    with _LOCK:
        t_anterior = _TIMERS.pop(key, None)
        if t_anterior is not None:
            try:
                t_anterior.cancel()
            except Exception:
                pass
        _PENDING[key] = _PendingFlush(escopo=escopo, empresa=empresa, processo=processo)
        _salvar_locked()
        if escopo == "empresa":
            t = threading.Timer(atraso_s, _flush_empresa, args=(empresa,))
        else:
            t = threading.Timer(atraso_s, _flush_processo, args=(empresa, processo or ""))
        t.daemon = True
        _TIMERS[key] = t
        t.start()


def marcar_dirty_empresa(empresa: str) -> None:
    """Marca a empresa como tendo o portfólio "sujo" — agenda um flush daqui a
    _DEBOUNCE_EMPRESA_S segundos. Se outro vídeo da mesma empresa entrar antes,
    o timer reinicia. Útil ao FIM do processar_video.
    """
    if not empresa:
        return
    _agendar("empresa", empresa, None, _DEBOUNCE_EMPRESA_S)


def marcar_dirty_processo(empresa: str, processo: str) -> None:
    """Marca o processo como tendo a série temporal "suja" — agenda flush de
    padrões do processo."""
    if not empresa or not processo:
        return
    _agendar("processo", empresa, processo, _DEBOUNCE_PROCESSO_S)


def bootstrap(
    sb_factory: Callable[[], object],
    groq_factory: Callable[[], object],
) -> None:
    """Carrega pendentes do disco e reagenda timers com o restante da janela.
    Chamado no @app.on_event('startup') do FastAPI."""
    global _sb_factory, _groq_factory
    _sb_factory = sb_factory
    _groq_factory = groq_factory

    pendentes = _carregar()
    if not pendentes:
        log.info("[debouncer] sem pendentes — pronto")
        return

    agora = time.time()
    for p in pendentes:
        if p.escopo == "empresa":
            janela = _DEBOUNCE_EMPRESA_S
        else:
            janela = _DEBOUNCE_PROCESSO_S
        restante = max(0.0, janela - (agora - p.last_touch_ts))
        _agendar(p.escopo, p.empresa, p.processo, restante)
        log.info(
            f"[debouncer] reagendado {p.escopo} {p.empresa}/{p.processo or '-'} "
            f"em {restante:.1f}s"
        )
