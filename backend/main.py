"""FastAPI · Kalidash Vision."""
from __future__ import annotations

import io
import logging
import os
import uuid
from typing import Any

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Path as PathParam,
    Query,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .auth import CurrentUser, get_current_user
from .jobs import JOBS
from .pipeline import (
    extrair_3_frames_evento,
    frame_para_jpeg_bytes,
    make_groq_client,
    make_supabase_client,
    montar_snapshot_chat,
    resolver_descricao_processo,
    responder_chat,
)
from .worker import executar_job, _baixar_video  # noqa: F401

log = logging.getLogger("kalidash.api")

app = FastAPI(title="Kalidash Vision", version="0.1.0")


@app.on_event("startup")
def _checar_segredos() -> None:
    faltando = [k for k in ("SUPABASE_URL", "SUPABASE_KEY", "GROQ_API_KEY") if not os.environ.get(k)]
    if faltando:
        log.error(
            "Variáveis de ambiente ausentes: %s. "
            "Copie backend/.env.example para backend/.env e preencha as chaves.",
            ", ".join(faltando),
        )

# CORS — em produção, restrinja a origem.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ═════════════════════════════════════════════════════════════════════════
class ProcessoCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    descricao: str | None = None


class ProcessoUpdateDescricao(BaseModel):
    descricao: str


class ValidacaoBody(BaseModel):
    acao: str  # "confirmar" | "corrigir" | "descartar"
    label_corrigido: str | None = None


class ChatBody(BaseModel):
    pergunta: str
    historico: list[dict[str, str]] | None = None


class RespostaPerguntaBody(BaseModel):
    resposta: str = Field(min_length=1, max_length=2000)


# ═════════════════════════════════════════════════════════════════════════
# PROCESSOS
# Cada processo é uma linha em `contexto_processo` para a empresa do usuário.
# `id` = uuid do registro · `nome` = campo `processo` · `descricao` opcional.
# ═════════════════════════════════════════════════════════════════════════
@app.post("/processos")
def criar_processo(body: ProcessoCreate, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    # Garante unicidade do par (empresa, processo) por usuário
    existe = (
        sb.table("contexto_processo")
        .select("id")
        .eq("empresa", user.empresa)
        .eq("processo", body.nome)
        .execute()
    )
    if existe.data:
        raise HTTPException(status.HTTP_409_CONFLICT, "Já existe um processo com esse nome.")
    r = (
        sb.table("contexto_processo")
        .insert(
            {
                "empresa": user.empresa,
                "processo": body.nome,
                "descricao": (body.descricao or "").strip(),
            }
        )
        .execute()
    )
    return r.data[0]


@app.get("/processos")
def listar_processos(user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    r = (
        sb.table("contexto_processo")
        .select("id, processo, descricao, atualizado_em")
        .eq("empresa", user.empresa)
        .order("atualizado_em", desc=True)
        .execute()
    )
    return r.data or []


@app.get("/processos/{processo_id}")
def detalhe_processo(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    r = (
        sb.table("contexto_processo")
        .select("id, processo, descricao, atualizado_em")
        .eq("id", processo_id)
        .eq("empresa", user.empresa)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Processo não encontrado")
    p = r.data[0]

    videos = (
        sb.table("videos")
        .select("id, nome, duracao_s, total_eventos, processado_em")
        .eq("empresa", user.empresa)
        .eq("processo", p["processo"])
        .order("processado_em", desc=True)
        .execute()
        .data
    ) or []
    p["videos"] = videos
    p["n_videos"] = len(videos)
    return p


@app.put("/processos/{processo_id}/descricao")
def atualizar_descricao(
    processo_id: str,
    body: ProcessoUpdateDescricao,
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    r = (
        sb.table("contexto_processo")
        .select("id, processo")
        .eq("id", processo_id)
        .eq("empresa", user.empresa)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Processo não encontrado")
    resolver_descricao_processo(sb, user.empresa, r.data[0]["processo"], body.descricao)
    return {"ok": True}


# ═════════════════════════════════════════════════════════════════════════
# UPLOAD + JOB
# ═════════════════════════════════════════════════════════════════════════
@app.post("/processos/{processo_id}/videos")
async def upload_video(
    processo_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    r = (
        sb.table("contexto_processo")
        .select("id, processo, descricao")
        .eq("id", processo_id)
        .eq("empresa", user.empresa)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Processo não encontrado")
    processo_nome = r.data[0]["processo"]
    descricao = r.data[0].get("descricao") or ""

    if not file.content_type or not file.content_type.startswith("video"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Arquivo precisa ser um vídeo.")

    conteudo = await file.read()
    if not conteudo:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Arquivo vazio.")

    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")
    storage_path = f"{user.empresa}/{processo_nome}/{uuid.uuid4()}_{file.filename}"
    try:
        sb.storage.from_(bucket).upload(
            storage_path,
            conteudo,
            {"content-type": file.content_type, "upsert": "false"},
        )
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Falha ao enviar para o storage: {e}")

    job = JOBS.create(processo_id=processo_id, user_id=user.id)
    background_tasks.add_task(
        executar_job,
        job.id,
        user.empresa,
        processo_nome,
        storage_path,
        descricao,
        file.filename,
    )
    return {"job_id": job.id}


@app.get("/jobs/{job_id}")
def status_job(job_id: str, user: CurrentUser = Depends(get_current_user)):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job não encontrado")
    if job.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Acesso negado")
    return job.to_dict()


# ═════════════════════════════════════════════════════════════════════════
# DASHBOARD / SUGESTÕES
# ═════════════════════════════════════════════════════════════════════════
def _processo_nome(sb, user: CurrentUser, processo_id: str) -> str:
    r = (
        sb.table("contexto_processo")
        .select("processo")
        .eq("id", processo_id)
        .eq("empresa", user.empresa)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Processo não encontrado")
    return r.data[0]["processo"]


@app.get("/processos/{processo_id}/dashboard")
def dashboard(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    snapshot = montar_snapshot_chat(sb, user.empresa, nome)

    sugs = (
        sb.table("sugestoes_melhoria")
        .select("id, prioridade, area, situacao, causa_provavel, sugestao, impacto_estimado, eventos_relacionados, criado_em")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .order("criado_em", desc=True)
        .limit(20)
        .execute()
        .data
    ) or []

    # Transições agregadas (sequências por pessoa) — pra mostrar fluxo
    from collections import Counter

    evs = (
        sb.table("eventos")
        .select("video_id, pessoa_track_id, comportamento_label, label_corrigido, tempo_inicio_s, validacao_correto, validado_humano, origem_validacao")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .limit(50000)
        .execute()
        .data
    ) or []
    base = [e for e in evs if e.get("validacao_correto") is not False]
    seqs: dict = {}
    for e in base:
        chave = (e.get("video_id"), e.get("pessoa_track_id"))
        seqs.setdefault(chave, []).append(
            (e.get("tempo_inicio_s") or 0, e.get("label_corrigido") or e.get("comportamento_label"))
        )
    contagem_t: Counter = Counter()
    for lista in seqs.values():
        lista.sort()
        labels = [l for _, l in lista]
        for x, y in zip(labels, labels[1:]):
            if x != y:
                contagem_t[(x, y)] += 1
    transicoes = [
        {"de": a, "para": b, "vezes": n} for (a, b), n in contagem_t.most_common(8)
    ]

    # Distribuição por origem (auto vs humano vs pendente)
    origens: Counter = Counter()
    for e in evs:
        if e.get("validado_humano") is True and e.get("origem_validacao") == "humano":
            origens["humano"] += 1
        elif e.get("origem_validacao") in ("correcao_aprendida", "vocabulario_canonico"):
            origens["auto"] += 1
        else:
            origens["pendente"] += 1

    videos = (
        sb.table("videos")
        .select("id, nome, duracao_s, total_eventos, total_pessoas, processado_em")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .order("processado_em", desc=True)
        .limit(50)
        .execute()
        .data
    ) or []

    pendentes = (
        sb.table("eventos")
        .select("id", count="exact")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .or_("validado_humano.eq.false,validado_humano.is.null")
        .limit(1)
        .execute()
    )

    perguntas_pend = (
        sb.table("perguntas_processo")
        .select("id", count="exact")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .eq("status", "pendente")
        .limit(1)
        .execute()
    )

    return {
        "snapshot": snapshot,
        "sugestoes": sugs,
        "eventos_pendentes": pendentes.count or 0,
        "perguntas_pendentes": perguntas_pend.count or 0,
        "transicoes": transicoes,
        "origens": {
            "auto": origens["auto"],
            "humano": origens["humano"],
            "pendente": origens["pendente"],
        },
        "videos": videos,
    }


@app.get("/processos/{processo_id}/sugestoes")
def sugestoes(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    r = (
        sb.table("sugestoes_melhoria")
        .select("*")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .order("criado_em", desc=True)
        .execute()
    )
    return r.data or []


# ═════════════════════════════════════════════════════════════════════════
# VALIDAÇÃO HUMANA
# ═════════════════════════════════════════════════════════════════════════
@app.get("/processos/{processo_id}/eventos")
def listar_eventos(
    processo_id: str,
    status_filter: str = Query("pendente", alias="status"),
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    q = (
        sb.table("eventos")
        .select(
            "id, video_id, comportamento_label, descricao_bruta, tempo_inicio_s, "
            "tempo_fim_s, confianca, validado_humano, validacao_correto, "
            "label_corrigido, origem_validacao, frame_inicio, frame_fim, bbox_inicio, pessoa_track_id"
        )
        .eq("empresa", user.empresa)
        .eq("processo", nome)
    )
    if status_filter == "pendente":
        q = q.or_("validado_humano.eq.false,validado_humano.is.null")
    elif status_filter == "validado":
        q = q.eq("validado_humano", True)

    r = q.order("tempo_inicio_s").limit(500).execute()
    return r.data or []


@app.get("/eventos/{evento_id}/frames")
def frames_evento(evento_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    r = sb.table("eventos").select("*").eq("id", evento_id).execute()
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evento não encontrado")
    ev = r.data[0]
    if ev.get("empresa") != user.empresa:
        raise HTTPException(status.HTTP_403_FORBIDDEN)

    # Baixa o vídeo do Storage
    vid = sb.table("videos").select("caminho, nome").eq("id", ev["video_id"]).execute().data
    if not vid:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vídeo do evento não encontrado")
    caminho = vid[0]["caminho"]  # storage_path original
    import tempfile
    from pathlib import Path

    # Vídeos processados antes do fix gravavam o tempfile local como
    # `caminho`. Se parece path absoluto local, não dá pra recuperar.
    if not caminho or caminho.startswith(("/", "\\")) or (len(caminho) > 1 and caminho[1] == ":"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Este vídeo foi processado com uma versão antiga (caminho local em vez do storage path). Reprocesse o vídeo para visualizar os frames.",
        )

    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")
    try:
        data = sb.storage.from_(bucket).download(caminho)
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Falha ao baixar vídeo: {e}")
    suffix = Path(caminho).suffix or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp.close()
    try:
        crops = extrair_3_frames_evento(ev, tmp.name)
    finally:
        try:
            Path(tmp.name).unlink()
        except Exception:
            pass

    import base64
    return {
        "frames": [
            "data:image/jpeg;base64," + base64.b64encode(frame_para_jpeg_bytes(c)).decode("ascii")
            for c in crops
        ]
    }


@app.post("/eventos/{evento_id}/validar")
def validar_evento(
    evento_id: str,
    body: ValidacaoBody,
    user: CurrentUser = Depends(get_current_user),
):
    from datetime import datetime

    sb = make_supabase_client()
    r = sb.table("eventos").select("id, empresa, comportamento_label").eq("id", evento_id).execute()
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evento não encontrado")
    ev = r.data[0]
    if ev["empresa"] != user.empresa:
        raise HTTPException(status.HTTP_403_FORBIDDEN)

    update: dict[str, Any] = {
        "validado_humano": True,
        "validado_em": datetime.utcnow().isoformat(),
        "origem_validacao": "humano",
    }
    if body.acao == "confirmar":
        update["validacao_correto"] = True
    elif body.acao == "corrigir":
        novo = (body.label_corrigido or "").strip()
        if not novo:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "label_corrigido obrigatório")
        update["validacao_correto"] = True
        if novo != ev["comportamento_label"]:
            update["label_corrigido"] = novo
    elif body.acao == "descartar":
        update["validacao_correto"] = False
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ação inválida")

    sb.table("eventos").update(update).eq("id", evento_id).execute()
    return {"ok": True}


# ═════════════════════════════════════════════════════════════════════════
# CHAT
# ═════════════════════════════════════════════════════════════════════════
@app.post("/processos/{processo_id}/chat")
def chat(processo_id: str, body: ChatBody, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    pergunta = (body.pergunta or "").strip()
    if not pergunta:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pergunta vazia")
    groq_client = make_groq_client()
    resposta = responder_chat(
        groq_client,
        sb,
        user.empresa,
        nome,
        pergunta,
        historico=body.historico,
    )
    return {"resposta": resposta}


# ═════════════════════════════════════════════════════════════════════════
# PERGUNTAS PROATIVAS
# ═════════════════════════════════════════════════════════════════════════
@app.get("/processos/{processo_id}/perguntas")
def listar_perguntas(
    processo_id: str,
    status_filter: str = Query("pendente", alias="status"),
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    q = (
        sb.table("perguntas_processo")
        .select("id, pergunta, motivo, comportamentos_relacionados, status, resposta, respondida_em, criada_em")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
    )
    if status_filter and status_filter != "todas":
        q = q.eq("status", status_filter)
    r = q.order("criada_em", desc=True).limit(200).execute()
    return r.data or []


@app.get("/processos/{processo_id}/perguntas/contagem")
def contagem_perguntas(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    r = (
        sb.table("perguntas_processo")
        .select("id", count="exact")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .eq("status", "pendente")
        .limit(1)
        .execute()
    )
    return {"pendentes": r.count or 0}


@app.post("/perguntas/{pergunta_id}/responder")
def responder_pergunta(
    pergunta_id: str,
    body: RespostaPerguntaBody,
    user: CurrentUser = Depends(get_current_user),
):
    from datetime import datetime

    sb = make_supabase_client()
    r = (
        sb.table("perguntas_processo")
        .select("id, empresa, status")
        .eq("id", pergunta_id)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pergunta não encontrada")
    if r.data[0]["empresa"] != user.empresa:
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    sb.table("perguntas_processo").update(
        {
            "resposta": body.resposta.strip(),
            "status": "respondida",
            "respondida_em": datetime.utcnow().isoformat(),
        }
    ).eq("id", pergunta_id).execute()
    return {"ok": True}


@app.post("/perguntas/{pergunta_id}/dispensar")
def dispensar_pergunta(
    pergunta_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    r = (
        sb.table("perguntas_processo")
        .select("id, empresa")
        .eq("id", pergunta_id)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pergunta não encontrada")
    if r.data[0]["empresa"] != user.empresa:
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    sb.table("perguntas_processo").update({"status": "dispensada"}).eq("id", pergunta_id).execute()
    return {"ok": True}


# ═════════════════════════════════════════════════════════════════════════
# HEALTH
# ═════════════════════════════════════════════════════════════════════════
@app.get("/health")
def health():
    return {"ok": True}
