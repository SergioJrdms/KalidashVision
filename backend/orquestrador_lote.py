"""Orquestrador de lote multi-câmera (Fase 6).

O edge sobe TODOS os segmentos no storage (toda a cam1, depois toda a cam2) ao
longo de 1-3h. Cada upload do edge vira uma linha PENDENTE na inbox `segmentos`
(ver main.upload_video) — NÃO processamos na hora.

Quando o lote termina, dois gatilhos disparam `processar_lote`:
  • explícito: o edge chama POST /processos/{id}/lote/concluido;
  • rede de segurança: uma varredura periódica (thread daemon) detecta
    processos com pendentes "quietos" (sem upload novo há KV_LOTE_QUIET_S) e
    dispara sozinha — pega lotes esquecidos e sinais perdidos.

`processar_lote` pareia os pendentes PELO NOME (token seg_AAAAMMDD_HHMMSS):
cam1 e cam2 do mesmo instante têm o mesmo token. Para cada par (ou solo)
enfileira UM item na fila serial (`job_queue`), que processa 1 por vez,
throttle-safe (Fase 4). O dual-angle (cam1 + cam2 na mesma chamada ao VLM)
acontece dentro do worker → processar_video.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

log = logging.getLogger("kalidash.lote")

_LOCK = threading.Lock()           # serializa processar_lote (sweep + sinal)
_SWEEP_INICIADO = False
_SWEEP_LOCK = threading.Lock()


def _env_int(nome: str, padrao: int) -> int:
    try:
        return int(float(os.environ.get(nome, str(padrao))))
    except Exception:
        return padrao


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def processar_lote(sb, empresa: str, processo: str) -> dict:
    """Pareia os segmentos pendentes do processo e os enfileira (par/solo).

    Retorna {'pares': M, 'solo': K, 'itens': N}. Idempotente: marca os
    segmentos como 'enfileirado' ao enfileirar, então uma 2ª chamada não
    reenfileira o que já saiu de 'pendente'.
    """
    from .pipeline import _seg_token_nome
    from . import job_queue
    from .jobs import JOBS

    with _LOCK:
        try:
            pend = (
                sb.table("segmentos")
                .select("id, storage_path, nome, cam_id, gravado_em, status")
                .eq("empresa", empresa)
                .eq("processo", processo)
                .eq("status", "pendente")
                .limit(20000)
                .execute()
                .data
            ) or []
        except Exception as e:
            log.warning(f"[lote] falha ao ler pendentes de {empresa}/{processo}: {e}")
            return {"pares": 0, "solo": 0, "itens": 0}

        if not pend:
            return {"pares": 0, "solo": 0, "itens": 0}

        # Agrupa por token do nome (seg_TIMESTAMP). Sem token → grupo próprio (solo).
        grupos: dict[str, list[dict]] = defaultdict(list)
        for s in pend:
            tok = _seg_token_nome(s.get("nome")) or f"__solo_{s['id']}"
            grupos[tok].append(s)

        n_pares = n_solo = 0
        # Ordem determinística/cronológica pela chave (timestamp no token).
        for tok in sorted(grupos.keys()):
            membros = sorted(grupos[tok], key=lambda x: str(x.get("cam_id") or ""))
            primario = membros[0]
            secundario = membros[1] if len(membros) >= 2 else None

            job = JOBS.create(processo_id=processo, user_id="edge-lote")
            job_queue.enqueue(
                job.id,
                empresa,
                processo,
                primario["storage_path"],
                None,                              # descrição é resolvida no pipeline
                primario.get("nome"),
                cam_id=primario.get("cam_id"),
                gravado_em=primario.get("gravado_em"),
                storage_path_secundario=(secundario or {}).get("storage_path"),
                cam_id_secundario=(secundario or {}).get("cam_id"),
                nome_secundario=(secundario or {}).get("nome"),
                segmento_id=primario["id"],
                segmento_id_secundario=(secundario or {}).get("id"),
            )
            ids = [primario["id"]] + ([secundario["id"]] if secundario else [])
            for sid in ids:
                try:
                    sb.table("segmentos").update({"status": "enfileirado"}).eq("id", sid).execute()
                except Exception as e:
                    log.warning(f"[lote] falha ao marcar enfileirado {sid}: {e}")
            if secundario:
                n_pares += 1
                log.info(
                    f"[lote] {empresa}/{processo}: enfileirando seg {tok} "
                    f"{primario.get('cam_id')}+{secundario.get('cam_id')} (par)"
                )
            else:
                n_solo += 1
                log.info(
                    f"[lote] {empresa}/{processo}: enfileirando seg {tok} "
                    f"{primario.get('cam_id')} (solo)"
                )

        total = n_pares + n_solo
        log.info(
            f"[lote] {empresa}/{processo}: {len(pend)} pendentes → "
            f"{n_pares} pares + {n_solo} solo = {total} itens enfileirados"
        )
        return {"pares": n_pares, "solo": n_solo, "itens": total}


# ═════════════════════════════════════════════════════════════════════════
# Varredura periódica (rede de segurança p/ lotes esquecidos / sinal perdido)
# ═════════════════════════════════════════════════════════════════════════
def _sweep_uma_vez() -> None:
    from .pipeline import make_supabase_client

    quiet_s = _env_int("KV_LOTE_QUIET_S", 900)   # 15min sem upload novo = lote "quieto"
    sb = make_supabase_client()
    try:
        rows = (
            sb.table("segmentos")
            .select("empresa, processo, recebido_em")
            .eq("status", "pendente")
            .limit(20000)
            .execute()
            .data
        ) or []
    except Exception as e:
        log.warning(f"[lote] sweep: falha ao ler pendentes: {e}")
        return

    if not rows:
        return

    # max(recebido_em) por (empresa, processo)
    ultimo: dict[tuple[str, str], datetime] = {}
    for r in rows:
        chave = (r.get("empresa"), r.get("processo"))
        ts = _parse_iso(r.get("recebido_em")) or datetime.now(timezone.utc)
        if chave not in ultimo or ts > ultimo[chave]:
            ultimo[chave] = ts

    agora = datetime.now(timezone.utc)
    for (empresa, processo), ts in ultimo.items():
        idade = (agora - ts).total_seconds()
        if idade >= quiet_s:
            log.info(
                f"[lote] sweep: {empresa}/{processo} quieto há {int(idade)}s "
                f"(≥{quiet_s}s) — processando lote"
            )
            try:
                processar_lote(sb, empresa, processo)
            except Exception as e:
                log.warning(f"[lote] sweep: processar_lote falhou ({e})")


def _sweep_loop() -> None:
    intervalo = _env_int("KV_LOTE_SWEEP_S", 300)   # checa a cada 5min
    log.info(f"[lote] varredura iniciada (cada {intervalo}s, quiet={_env_int('KV_LOTE_QUIET_S', 900)}s)")
    while True:
        try:
            _sweep_uma_vez()
        except Exception as e:
            log.warning(f"[lote] sweep ciclo falhou: {e}")
        time.sleep(intervalo)


def start_sweep_thread() -> None:
    """Inicia (uma vez por processo) a varredura periódica. Chamado no startup."""
    global _SWEEP_INICIADO
    with _SWEEP_LOCK:
        if _SWEEP_INICIADO:
            return
        t = threading.Thread(target=_sweep_loop, name="lote_sweep", daemon=True)
        t.start()
        _SWEEP_INICIADO = True
