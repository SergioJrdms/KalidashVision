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


def _instante_epoch(s: dict) -> float | None:
    """Instante do segmento em epoch (float), p/ o pareamento por janela.
    Usa o token do nome (seg_AAAAMMDD_HHMMSS, idêntico entre cam1/cam2);
    fallback = coluna `gravado_em`. None se nada casar (vira solo)."""
    from .pipeline import _seg_token_nome
    tok = _seg_token_nome(s.get("nome"))
    if tok:
        try:
            data, hora = tok.split("_")
            dt = datetime(
                int(data[0:4]), int(data[4:6]), int(data[6:8]),
                int(hora[0:2]), int(hora[2:4]), int(hora[4:6]),
            )
            return dt.timestamp()  # naive → TZ local do server, consistente entre cams
        except Exception:
            pass
    dt = _parse_iso(s.get("gravado_em"))
    return dt.timestamp() if dt else None


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
            # Fase 81 — paginado. Truncar aqui não era só "processa menos": o
            # par cam1/cam2 podia ficar dos dois lados do corte, e o segmento
            # cujo par ficou de fora seguia como SOLO — vídeo processado com
            # um ângulo só, sem ninguém saber.
            from .pipeline import varrer
            pend = varrer(
                sb, "segmentos",
                "id, storage_path, nome, cam_id, gravado_em, status, recebido_em",
                empresa=empresa, processo=processo,
                ajustes=lambda q: q.eq("status", "pendente"),
            )
        except Exception as e:
            log.warning(f"[lote] falha ao ler pendentes de {empresa}/{processo}: {e}")
            return {"pares": 0, "solo": 0, "itens": 0}

        if not pend:
            return {"pares": 0, "solo": 0, "itens": 0}

        # ── Pareamento por JANELA DE TEMPO (Fase 15) ──────────────────────
        # Antes pareava só por token EXATO (seg_AAAAMMDD_HHMMSS): cam1 09:20:01 e
        # cam2 09:20:00 (1s de diferença) viravam 2 solos. Agora cam1↔cam2 são
        # par se |Δt| ≤ KV_LOTE_PAR_JANELA_S (default 360s = 6min); fora disso,
        # solo. Casamento guloso pelo par MAIS PRÓXIMO (nunca cam1+cam1).
        janela_s = float(_env_int("KV_LOTE_PAR_JANELA_S", 360))
        itens = [(s, _instante_epoch(s)) for s in pend]

        candidatos: list[tuple[float, int, int]] = []
        for i in range(len(itens)):
            si, ti = itens[i]
            if ti is None:
                continue
            ci = si.get("cam_id")
            for j in range(i + 1, len(itens)):
                sj, tj = itens[j]
                if tj is None:
                    continue
                cj = sj.get("cam_id")
                if ci and cj and ci != cj and abs(ti - tj) <= janela_s:
                    candidatos.append((abs(ti - tj), i, j))
        candidatos.sort(key=lambda x: x[0])

        consumidos: set[int] = set()
        pares: list[tuple[dict, dict]] = []
        for _d, i, j in candidatos:
            if i in consumidos or j in consumidos:
                continue
            consumidos.add(i)
            consumidos.add(j)
            si, sj = itens[i][0], itens[j][0]
            # primário = câmera de MENOR id (cam1) — dirige a detecção/tracking.
            if str(si.get("cam_id") or "") <= str(sj.get("cam_id") or ""):
                pares.append((si, sj))
            else:
                pares.append((sj, si))
        # ── CARÊNCIA DE SOLO (Fase 39) ────────────────────────────────────
        # O edge sobe a cam1 inteira e depois a cam2 (cada recorte leva minutos).
        # Se o pareamento rodar NO MEIO (sweep quieto ou /lote/concluido de um
        # run vizinho), a cam1 sem par ainda seria solada — e quando a cam2
        # chega, o par dela já foi → 2 solos no lugar de 1 par. Aqui, um
        # segmento SEM par só vira solo depois de KV_LOTE_SOLO_GRACE_S desde
        # que chegou; recém-chegados sem par FICAM pendentes esperando o par
        # (a próxima passada os pareia). Assim, nunca solamos um par cedo demais;
        # solos DE VERDADE (câmera única / par que nunca vem) processam após a
        # carência. Segmento sem `recebido_em` (legado) não é segurado.
        grace_s = float(_env_int("KV_LOTE_SOLO_GRACE_S", 1200))   # 20min
        agora = datetime.now(timezone.utc)
        solos, segurados = [], 0
        for k in range(len(itens)):
            if k in consumidos:
                continue
            s = itens[k][0]
            rec = _parse_iso(s.get("recebido_em"))
            idade = (agora - rec).total_seconds() if rec else None
            if idade is not None and idade < grace_s:
                segurados += 1        # jovem e sem par → espera o par chegar
            else:
                solos.append(s)
        if segurados:
            log.info(
                f"[lote] {empresa}/{processo}: {segurados} segmento(s) sem par "
                f"SEGURADO(s) (<{grace_s:.0f}s) — aguardando o par da outra câmera."
            )

        def _enfileirar(primario: dict, secundario: dict | None) -> None:
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

        n_pares = n_solo = 0
        for primario, secundario in pares:
            _enfileirar(primario, secundario)
            n_pares += 1
            log.info(
                f"[lote] {empresa}/{processo}: enfileirando seg "
                f"{_seg_token_nome(primario.get('nome'))} {primario.get('cam_id')}+"
                f"{secundario.get('cam_id')} (par, |Δt|≤{janela_s:.0f}s)"
            )
        for primario in solos:
            _enfileirar(primario, None)
            n_solo += 1
            log.info(
                f"[lote] {empresa}/{processo}: enfileirando seg "
                f"{_seg_token_nome(primario.get('nome'))} {primario.get('cam_id')} (solo)"
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
        from .pipeline import varrer
        rows = varrer(sb, "segmentos", "id, empresa, processo, recebido_em",
                      ajustes=lambda q: q.eq("status", "pendente"))
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
