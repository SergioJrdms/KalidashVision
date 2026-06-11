"""FastAPI · Kalidash Vision."""
from __future__ import annotations

import io
import logging
import os
import re
import unicodedata
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
    gerar_titulo_conversa,
    gerar_sugestoes_chat,
    agregar_portfolio,
    responder_chat_global,
    gerar_sugestoes_chat_global,
    gerar_insights_globais,
    montar_serie_temporal,
    analisar_padroes_globais,
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
# HELPERS
# ═════════════════════════════════════════════════════════════════════════
def _slug_storage(texto: str, padrao: str = "x") -> str:
    """Normaliza um segmento para chave válida do Supabase Storage.

    Storage rejeita acentos e a maioria dos caracteres não-ASCII (InvalidKey).
    Ex.: 'Linha de Produção - Queijos' → 'Linha_de_Producao_-_Queijos'.
    """
    s = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("._-")
    return s or padrao


# ═════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ═════════════════════════════════════════════════════════════════════════
class ProcessoCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    descricao: str | None = None
    area: str | None = Field(default=None, max_length=60)


class ProcessoUpdateDescricao(BaseModel):
    descricao: str


class ProcessoUpdateArea(BaseModel):
    area: str | None = Field(default=None, max_length=60)


class ValidacaoBody(BaseModel):
    acao: str  # "confirmar" | "corrigir" | "descartar" | "reabrir"
    label_corrigido: str | None = None


class LoteBody(BaseModel):
    ids: list[str] = Field(min_length=1)
    acao: str  # "confirmar" | "corrigir" | "descartar" | "reabrir"
    label_corrigido: str | None = None


class ChatBody(BaseModel):
    pergunta: str
    historico: list[dict[str, str]] | None = None


class RespostaPerguntaBody(BaseModel):
    resposta: str = Field(min_length=1, max_length=2000)


class CategoriaLeanBody(BaseModel):
    categoria_lean: str | None = None  # 'valor_agregado' | 'apoio' | 'desperdicio' | None


class SugestaoAcaoBody(BaseModel):
    acao: str  # 'realizada' | 'dispensada' | 'reabrir'


class PrismMensagemBody(BaseModel):
    pergunta: str = Field(min_length=1, max_length=4000)


class PrismRenomearBody(BaseModel):
    titulo: str = Field(min_length=1, max_length=120)


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
                "area": (body.area or "").strip() or None,
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
        .select("id, processo, descricao, area, atualizado_em")
        .eq("empresa", user.empresa)
        .order("atualizado_em", desc=True)
        .execute()
    )
    linhas = r.data or []
    # Enriquecimento por processo — uma passada por empresa (não N× queries)
    try:
        portfolio = agregar_portfolio(sb, user.empresa)
    except Exception as e:
        log.warning(f"Falha ao agregar portfólio: {e}")
        portfolio = {}
    for row in linhas:
        st = portfolio.get(row["processo"], {})
        row["n_videos"] = st.get("n_videos", 0)
        row["eventos_pendentes"] = st.get("eventos_pendentes", 0)
        row["pct_validado"] = st.get("pct_validado", 0)
        row["n_sugestoes"] = st.get("n_sugestoes", 0)
        row["n_sugestoes_alta"] = st.get("n_sugestoes_alta", 0)
        row["tempo_total_min"] = st.get("tempo_total_min", 0)
        row["ultimo_video_em"] = st.get("ultimo_video_em")
        row["composicao_valor"] = st.get("composicao_valor")
        row["maturidade"] = st.get("maturidade", 0)
    return linhas


@app.get("/processos/{processo_id}")
def detalhe_processo(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    r = (
        sb.table("contexto_processo")
        .select("id, processo, descricao, area, atualizado_em")
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
    # enriquecimento (maturidade, pendências, composição) para a sidebar/dashboard
    try:
        st = agregar_portfolio(sb, user.empresa).get(p["processo"], {})
        p["maturidade"] = st.get("maturidade", 0)
        p["eventos_pendentes"] = st.get("eventos_pendentes", 0)
        p["pendencias"] = st.get("eventos_pendentes", 0)
        p["pct_validado"] = st.get("pct_validado", 0)
        p["n_sugestoes_alta"] = st.get("n_sugestoes_alta", 0)
        p["composicao_valor"] = st.get("composicao_valor")
    except Exception as e:
        log.warning(f"detalhe_processo: falha ao agregar: {e}")
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


@app.put("/processos/{processo_id}/area")
def atualizar_area(
    processo_id: str,
    body: ProcessoUpdateArea,
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    r = (
        sb.table("contexto_processo")
        .select("id")
        .eq("id", processo_id)
        .eq("empresa", user.empresa)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Processo não encontrado")
    val = (body.area or "").strip() or None
    sb.table("contexto_processo").update({"area": val}).eq("id", processo_id).execute()
    return {"ok": True, "area": val}


@app.delete("/processos/{processo_id}")
def excluir_processo_endpoint(
    processo_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Exclui um processo e TODOS os seus dados: arquivos no Storage +
    todas as tabelas-folha de (empresa, processo), via RPC transacional.
    Não apaga insights_globais (são da empresa) — apenas recalcula depois.
    """
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)

    # 1) Remove os vídeos do Storage (lê os caminhos antes de apagar as linhas)
    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")
    try:
        vids = (
            sb.table("videos")
            .select("caminho")
            .eq("empresa", user.empresa)
            .eq("processo", nome)
            .execute()
            .data
        ) or []
        caminhos = [
            v["caminho"]
            for v in vids
            if v.get("caminho")
            and not v["caminho"].startswith(("/", "\\"))
            and not (len(v["caminho"]) > 1 and v["caminho"][1] == ":")
        ]
        if caminhos:
            sb.storage.from_(bucket).remove(caminhos)
    except Exception as e:
        log.warning(f"Falha ao remover vídeos do storage (segue mesmo assim): {e}")

    # 2) Apaga todas as linhas numa transação (RPC). Fallback: deletes em ordem.
    try:
        sb.rpc("excluir_processo", {"p_empresa": user.empresa, "p_processo": nome}).execute()
    except Exception as e:
        log.warning(f"RPC excluir_processo falhou ({e}); aplicando deletes em sequência.")
        for tabela in (
            "prism_mensagens",
            "prism_conversas",
            "eventos",
            "sugestoes_melhoria",
            "comportamentos",
            "perguntas_processo",
            "videos",
            "contexto_processo",
        ):
            try:
                sb.table(tabela).delete().eq("empresa", user.empresa).eq("processo", nome).execute()
            except Exception as e2:
                log.error(f"Falha ao limpar {tabela} de {user.empresa}/{nome}: {e2}")

    # 3) Recalcula insights e padrões globais — o portfólio mudou. Não-fatal.
    try:
        gc = make_groq_client()
        gerar_insights_globais(sb, gc, user.empresa)
        analisar_padroes_globais(sb, gc, user.empresa)
    except Exception as e:
        log.warning(f"Recalcular insights/padrões após exclusão falhou: {e}")

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
    # Chave do Storage só aceita ASCII seguro — sanitiza empresa/processo/arquivo
    # (o nome original do vídeo segue preservado na coluna `nome` da tabela videos).
    nome_orig = file.filename or "video.mp4"
    if "." in nome_orig:
        base_nome, _, ext_nome = nome_orig.rpartition(".")
    else:
        base_nome, ext_nome = nome_orig, "mp4"
    arquivo = f"{uuid.uuid4()}_{_slug_storage(base_nome, 'video')}.{_slug_storage(ext_nome, 'mp4')}"
    storage_path = f"{_slug_storage(user.empresa, 'empresa')}/{_slug_storage(processo_nome, 'processo')}/{arquivo}"
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
        .select("id, prioridade, area, situacao, causa_provavel, sugestao, impacto_estimado, eventos_relacionados, status, voltou_apos_realizada, criado_em")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .eq("status", "pendente")
        .order("criado_em", desc=True)
        .limit(60)
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

    # Categoria Lean por comportamento (mapa label → categoria)
    comps_full = (
        sb.table("comportamentos")
        .select("id, label, descricao, categoria_lean, categoria_lean_origem")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .execute()
        .data
    ) or []
    cat_por_label = {c["label"]: c.get("categoria_lean") for c in comps_full}
    cat_origem_por_label = {c["label"]: c.get("categoria_lean_origem") for c in comps_full}
    comp_id_por_label = {c["label"]: c["id"] for c in comps_full}

    # Anexa categoria a cada item de distribuicao + acrescenta acumulado para Pareto
    dist_enriquecida = []
    total_tempo = sum(d.get("tempo_total_s", 0) for d in snapshot["distribuicao_comportamentos"]) or 1
    acumulado = 0.0
    for d in snapshot["distribuicao_comportamentos"]:
        cat = cat_por_label.get(d["comportamento"])
        acumulado += d.get("tempo_total_s", 0)
        dist_enriquecida.append(
            {
                **d,
                "categoria_lean": cat,
                "categoria_lean_origem": cat_origem_por_label.get(d["comportamento"]),
                "comportamento_id": comp_id_por_label.get(d["comportamento"]),
                "pct_acumulado": round(acumulado / total_tempo * 100, 1),
            }
        )
    snapshot["distribuicao_comportamentos"] = dist_enriquecida

    # Composição de valor agregada (% sobre o tempo total observado)
    soma_por_cat = {"valor_agregado": 0.0, "apoio": 0.0, "desperdicio": 0.0, "nao_classificado": 0.0}
    for d in dist_enriquecida:
        cat = d.get("categoria_lean") or "nao_classificado"
        if cat not in soma_por_cat:
            cat = "nao_classificado"
        soma_por_cat[cat] += d.get("tempo_total_s", 0)
    composicao_valor = {
        f"{k}_pct": round(v / total_tempo * 100, 1) for k, v in soma_por_cat.items()
    }
    composicao_valor["tempo_total_s"] = round(total_tempo, 1)
    composicao_valor["por_categoria_s"] = {k: round(v, 1) for k, v in soma_por_cat.items()}

    # Pareto: top comportamentos com acumulado
    pareto = [
        {
            "comportamento": d["comportamento"],
            "descricao": d.get("descricao"),
            "categoria_lean": d.get("categoria_lean"),
            "pct_tempo": d.get("pct_tempo", 0),
            "tempo_total_s": d.get("tempo_total_s", 0),
            "pct_acumulado": d.get("pct_acumulado", 0),
        }
        for d in dist_enriquecida[:15]
    ]

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
        "composicao_valor": composicao_valor,
        "pareto": pareto,
        "videos": videos,
        "padroes_resumo": (
            sb.table("padroes_processo")
            .select("id, tipo, camada, titulo, relevancia, confianca")
            .eq("empresa", user.empresa)
            .eq("processo", nome)
            .order("criado_em", desc=True)
            .limit(4)
            .execute()
            .data
        )
        or [],
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


@app.post("/sugestoes/{sugestao_id}/marcar")
def marcar_sugestao(
    sugestao_id: str,
    body: SugestaoAcaoBody,
    user: CurrentUser = Depends(get_current_user),
):
    """Gestor marca uma sugestão como realizada/dispensada (ou reabre).

    Mantemos a linha em vez de apagar: assim, quando o pipeline gerar nova
    sugestão parecida no futuro, dá pra detectar que ela já foi marcada como
    realizada (e a ação não foi cumprida).
    """
    from datetime import datetime

    acao = (body.acao or "").strip().lower()
    if acao not in {"realizada", "dispensada", "reabrir"}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "acao deve ser 'realizada', 'dispensada' ou 'reabrir'.",
        )

    sb = make_supabase_client()
    r = (
        sb.table("sugestoes_melhoria")
        .select("id, empresa")
        .eq("id", sugestao_id)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sugestão não encontrada")
    if r.data[0]["empresa"] != user.empresa:
        raise HTTPException(status.HTTP_403_FORBIDDEN)

    if acao == "reabrir":
        update = {"status": "pendente", "marcada_em": None}
    else:
        update = {"status": acao, "marcada_em": datetime.utcnow().isoformat()}
    sb.table("sugestoes_melhoria").update(update).eq("id", sugestao_id).execute()
    return {"ok": True, "status": update["status"]}


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
    itens = r.data or []

    # Categoria Lean PREVISTA por evento (derivada do label) — sem cálculo pesado.
    labels_distintos = list({(i.get("label_corrigido") or i.get("comportamento_label")) for i in itens})
    labels_distintos = [l for l in labels_distintos if l]
    if labels_distintos:
        try:
            comp = (
                sb.table("comportamentos")
                .select("label, categoria_lean")
                .eq("empresa", user.empresa)
                .eq("processo", nome)
                .in_("label", labels_distintos)
                .execute()
                .data
            ) or []
            cat_por_label = {c["label"]: c.get("categoria_lean") for c in comp}
        except Exception:
            cat_por_label = {}
        for i in itens:
            lbl = i.get("label_corrigido") or i.get("comportamento_label")
            i["categoria_lean_prevista"] = cat_por_label.get(lbl)
    return itens


def _status_efetivo(ev: dict) -> str:
    """Regra única de status derivado (front não reimplementa)."""
    if not ev.get("validado_humano"):
        return "pendente"
    if ev.get("validacao_correto") is False:
        return "descartado"
    origem = ev.get("origem_validacao")
    if origem in ("correcao_aprendida", "vocabulario_canonico"):
        return "auto"
    if ev.get("label_corrigido"):
        return "corrigido"
    return "confirmado"


_SORT_COLS = {
    "criado_em",
    "tempo_inicio_s",
    "duracao_s",
    "comportamento_label",
    "confianca",
}


@app.get("/processos/{processo_id}/eventos/tabela")
def listar_eventos_tabela(
    processo_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status_filter: str = Query("todos", alias="status"),
    label: str | None = None,
    video_id: str | None = None,
    busca: str | None = None,
    sort: str = Query("criado_em"),
    order: str = Query("desc"),
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)

    campos = (
        "id, video_id, pessoa_track_id, comportamento_label, label_corrigido, "
        "descricao_bruta, tempo_inicio_s, tempo_fim_s, duracao_s, confianca, "
        "validado_humano, validacao_correto, origem_validacao, criado_em, validado_em, "
        "categoria_lean, categoria_lean_origem"
    )
    q = (
        sb.table("eventos")
        .select(campos, count="exact")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
    )

    # Filtros de status (mapeados para combinações de colunas)
    if status_filter == "pendente":
        q = q.or_("validado_humano.eq.false,validado_humano.is.null")
    elif status_filter == "descartado":
        q = q.eq("validacao_correto", False)
    elif status_filter == "auto":
        q = q.eq("validado_humano", True).in_(
            "origem_validacao", ["correcao_aprendida", "vocabulario_canonico"]
        )
    elif status_filter == "corrigido":
        q = (
            q.eq("validado_humano", True)
            .eq("validacao_correto", True)
            .eq("origem_validacao", "humano")
            .filter("label_corrigido", "not.is", "null")
        )
    elif status_filter == "confirmado":
        q = (
            q.eq("validado_humano", True)
            .eq("validacao_correto", True)
            .eq("origem_validacao", "humano")
            .filter("label_corrigido", "is", "null")
        )
    # "todos" → sem filtro

    if label:
        q = q.eq("comportamento_label", label)
    if video_id:
        q = q.eq("video_id", video_id)
    if busca:
        q = q.ilike("descricao_bruta", f"%{busca}%")

    sort_col = sort if sort in _SORT_COLS else "criado_em"
    desc = order != "asc"
    q = q.order(sort_col, desc=desc)

    inicio = (page - 1) * page_size
    fim = inicio + page_size - 1
    r = q.range(inicio, fim).execute()
    itens = r.data or []

    # "Join" leve com videos só para a página atual
    vids = {v["video_id"] for v in itens if v.get("video_id")}
    nomes: dict[str, str] = {}
    if vids:
        rv = (
            sb.table("videos")
            .select("id, nome")
            .in_("id", list(vids))
            .execute()
        )
        nomes = {v["id"]: v.get("nome", "") for v in (rv.data or [])}

    # Categoria Lean + id do comportamento (mapa label → ...), para exibir a
    # classificação e permitir reclassificar pela lista de eventos.
    comps_full = (
        sb.table("comportamentos")
        .select("id, label, categoria_lean")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .execute()
        .data
    ) or []
    cat_por_label = {c["label"]: c.get("categoria_lean") for c in comps_full}
    comp_id_por_label = {c["label"]: c["id"] for c in comps_full}

    for ev in itens:
        ev["video_nome"] = nomes.get(ev.get("video_id"), "—")
        label_ef = ev.get("label_corrigido") or ev.get("comportamento_label")
        ev["label_efetivo"] = label_ef
        ev["status_efetivo"] = _status_efetivo(ev)
        # Categoria efetiva: override individual do humano vence; senão usa a
        # categoria viva do comportamento (memória). Mantém a lista coerente com
        # o dashboard mesmo antes do backfill físico rodar.
        if ev.get("categoria_lean_origem") == "humano" and ev.get("categoria_lean"):
            ev["categoria_lean"] = ev["categoria_lean"]
        else:
            ev["categoria_lean"] = cat_por_label.get(label_ef)
        ev["comportamento_id"] = comp_id_por_label.get(label_ef)

    return {
        "itens": itens,
        "total": r.count or 0,
        "page": page,
        "page_size": page_size,
    }


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


def _montar_update_validacao(acao: str, label_original: str, label_corrigido: str | None) -> dict[str, Any]:
    """Calcula o estado final do evento para cada ação humana.

    IMPORTANTE (coerência do aprendizado): a memória do negócio é recalculada
    do zero a cada processar_video (carregar_memoria_do_negocio lê o estado
    ATUAL dos eventos). Logo, não há cache a invalidar — basta gravar aqui o
    estado final correto e o efeito se propaga no próximo processamento.

    Tabela de transições:
      confirmar  → VH=true,  VC=true,  LC=null,         OV=humano, VE=now
      corrigir   → VH=true,  VC=true,  LC=X (ou null),  OV=humano, VE=now
      descartar  → VH=true,  VC=false, LC=inalterado,   OV=humano, VE=now
      reabrir    → VH=false, VC=null,  LC=null,         OV=null,   VE=null
    """
    from datetime import datetime

    now = datetime.utcnow().isoformat()
    if acao == "confirmar":
        # Confirmar = "o label original está certo": limpa qualquer correção antiga.
        return {
            "validado_humano": True,
            "validacao_correto": True,
            "label_corrigido": None,
            "origem_validacao": "humano",
            "validado_em": now,
        }
    if acao == "corrigir":
        novo = (label_corrigido or "").strip()
        if not novo:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "label_corrigido obrigatório")
        # Se o "corrigido" é igual ao original, não é correção: limpa LC.
        lc = novo if novo != label_original else None
        return {
            "validado_humano": True,
            "validacao_correto": True,
            "label_corrigido": lc,
            "origem_validacao": "humano",
            "validado_em": now,
        }
    if acao == "descartar":
        # Falso positivo. Mantém label_corrigido inalterado (não enviado no update).
        return {
            "validado_humano": True,
            "validacao_correto": False,
            "origem_validacao": "humano",
            "validado_em": now,
        }
    if acao == "reabrir":
        # Devolve à fila como pendente, limpando toda marca de validação.
        return {
            "validado_humano": False,
            "validacao_correto": None,
            "label_corrigido": None,
            "origem_validacao": None,
            "validado_em": None,
        }
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "ação inválida")


@app.post("/eventos/{evento_id}/validar")
def validar_evento(
    evento_id: str,
    body: ValidacaoBody,
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    r = sb.table("eventos").select("id, empresa, comportamento_label").eq("id", evento_id).execute()
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evento não encontrado")
    ev = r.data[0]
    if ev["empresa"] != user.empresa:
        raise HTTPException(status.HTTP_403_FORBIDDEN)

    update = _montar_update_validacao(body.acao, ev["comportamento_label"], body.label_corrigido)
    sb.table("eventos").update(update).eq("id", evento_id).execute()
    return {"ok": True}


@app.post("/eventos/{evento_id}/reabrir")
def reabrir_evento(evento_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    r = sb.table("eventos").select("id, empresa, comportamento_label").eq("id", evento_id).execute()
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evento não encontrado")
    if r.data[0]["empresa"] != user.empresa:
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    update = _montar_update_validacao("reabrir", r.data[0]["comportamento_label"], None)
    sb.table("eventos").update(update).eq("id", evento_id).execute()
    return {"ok": True}


@app.post("/eventos/lote")
def validar_lote(body: LoteBody, user: CurrentUser = Depends(get_current_user)):
    if body.acao not in ("confirmar", "corrigir", "descartar", "reabrir"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ação inválida")
    sb = make_supabase_client()
    # Carrega todos os eventos do lote e valida que pertencem à empresa do usuário.
    r = (
        sb.table("eventos")
        .select("id, empresa, comportamento_label")
        .in_("id", body.ids)
        .execute()
    )
    encontrados = r.data or []
    if len(encontrados) != len(set(body.ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Um ou mais eventos não foram encontrados")
    for ev in encontrados:
        if ev["empresa"] != user.empresa:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Acesso negado a um dos eventos")

    aplicados = 0
    for ev in encontrados:
        update = _montar_update_validacao(body.acao, ev["comportamento_label"], body.label_corrigido)
        sb.table("eventos").update(update).eq("id", ev["id"]).execute()
        aplicados += 1
    return {"ok": True, "aplicados": aplicados}


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
        .select("id, pergunta, motivo, comportamentos_relacionados, respostas_rapidas, status, resposta, respondida_em, criada_em")
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
# CLASSIFICAÇÃO LEAN — override do gestor
# ═════════════════════════════════════════════════════════════════════════
_CATS_LEAN_VALIDAS = {"valor_agregado", "apoio", "desperdicio"}


@app.put("/comportamentos/{comportamento_id}/categoria")
def setar_categoria_lean(
    comportamento_id: str,
    body: CategoriaLeanBody,
    user: CurrentUser = Depends(get_current_user),
):
    cat = (body.categoria_lean or "").strip().lower() or None
    if cat is not None and cat not in _CATS_LEAN_VALIDAS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "categoria_lean deve ser uma de: valor_agregado, apoio, desperdicio, ou null.",
        )
    sb = make_supabase_client()
    r = (
        sb.table("comportamentos")
        .select("id, empresa, processo, label")
        .eq("id", comportamento_id)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comportamento não encontrado")
    alvo = r.data[0]
    if alvo["empresa"] != user.empresa:
        raise HTTPException(status.HTTP_403_FORBIDDEN)

    update = (
        {"categoria_lean": cat, "categoria_lean_origem": "humano"}
        if cat is not None
        else {"categoria_lean": None, "categoria_lean_origem": None}  # libera pra IA reclassificar
    )
    sb.table("comportamentos").update(update).eq("id", comportamento_id).execute()

    # Propagação cross-processo (mesma empresa, MESMO label):
    # a decisão do gestor para 'andar' vale em toda a fábrica. Atualiza
    # registros com o mesmo label que NÃO foram tocados manualmente em
    # outros processos (origem em 'ia' | 'aprendido' | null), marcando-os
    # como 'aprendido'. Nunca toca em 'humano' (cada processo pode ter
    # decisão própria de propósito).
    propagados = 0
    if cat is not None and alvo.get("label"):
        try:
            r2 = (
                sb.table("comportamentos")
                .select("id, categoria_lean_origem")
                .eq("empresa", user.empresa)
                .eq("label", alvo["label"])
                .neq("id", comportamento_id)
                .execute()
            )
            for c in r2.data or []:
                if (c.get("categoria_lean_origem") or "") == "humano":
                    continue  # respeitar override deliberado em outro processo
                try:
                    sb.table("comportamentos").update(
                        {"categoria_lean": cat, "categoria_lean_origem": "aprendido"}
                    ).eq("id", c["id"]).execute()
                    propagados += 1
                except Exception as e:
                    log.warning(f"Lean: falha ao propagar p/ {c['id']}: {e}")
        except Exception as e:
            log.warning(f"Lean: falha ao listar comportamentos para propagação: {e}")

    # Despeja a categoria nos EVENTOS do comportamento (mesma empresa+label).
    # Cada evento guarda a sua, mas NÃO sobrescrevemos overrides individuais de
    # humano — assim a decisão de 'andar = apoio' vale como padrão, e um evento
    # específico classificado à mão por alguém permanece intacto.
    eventos_atualizados = 0
    if alvo.get("label"):
        try:
            upd = (
                sb.table("eventos")
                .update(
                    {
                        "categoria_lean": cat,
                        "categoria_lean_origem": "aprendido" if cat else None,
                    }
                )
                .eq("empresa", user.empresa)
                .eq("comportamento_label", alvo["label"])
                .or_("categoria_lean_origem.is.null,categoria_lean_origem.neq.humano")
                .execute()
            )
            eventos_atualizados = len(upd.data or [])
        except Exception as e:
            log.warning(f"Lean: falha ao despejar categoria nos eventos: {e}")

    return {
        "ok": True,
        "categoria_lean": cat,
        "origem": "humano" if cat else None,
        "propagados": propagados,
        "eventos_atualizados": eventos_atualizados,
    }


# ═════════════════════════════════════════════════════════════════════════
# PRISM — chat lateral (conversas persistidas + tópicos + sugestões dinâmicas)
# ═════════════════════════════════════════════════════════════════════════
_PRISM_MAX_TROCAS = 6  # últimas N trocas mandadas como histórico ao LLM


def _carregar_conversa_propria(sb, user: CurrentUser, processo_nome: str, conversa_id: str) -> dict:
    """Carrega uma conversa garantindo que pertence à empresa+processo do usuário."""
    r = (
        sb.table("prism_conversas")
        .select("id, empresa, processo, titulo, titulo_auto, criada_em, atualizada_em")
        .eq("id", conversa_id)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversa não encontrada")
    c = r.data[0]
    if c["empresa"] != user.empresa or c["processo"] != processo_nome:
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    return c


@app.get("/processos/{processo_id}/prism/conversas")
def prism_listar_conversas(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    r = (
        sb.table("prism_conversas")
        .select("id, titulo, titulo_auto, criada_em, atualizada_em")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .order("atualizada_em", desc=True)
        .limit(200)
        .execute()
    )
    return r.data or []


@app.post("/processos/{processo_id}/prism/conversas")
def prism_criar_conversa(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    r = (
        sb.table("prism_conversas")
        .insert({"empresa": user.empresa, "processo": nome})
        .execute()
    )
    return r.data[0]


@app.get("/processos/{processo_id}/prism/conversas/{conversa_id}")
def prism_get_conversa(
    processo_id: str,
    conversa_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    c = _carregar_conversa_propria(sb, user, nome, conversa_id)
    msgs = (
        sb.table("prism_mensagens")
        .select("id, papel, conteudo, criada_em")
        .eq("conversa_id", conversa_id)
        .order("criada_em", desc=False)
        .limit(500)
        .execute()
        .data
    ) or []
    return {**c, "mensagens": msgs}


@app.patch("/processos/{processo_id}/prism/conversas/{conversa_id}")
def prism_renomear_conversa(
    processo_id: str,
    conversa_id: str,
    body: PrismRenomearBody,
    user: CurrentUser = Depends(get_current_user),
):
    from datetime import datetime

    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    _carregar_conversa_propria(sb, user, nome, conversa_id)
    sb.table("prism_conversas").update(
        {
            "titulo": body.titulo.strip(),
            "titulo_auto": False,
            "atualizada_em": datetime.utcnow().isoformat(),
        }
    ).eq("id", conversa_id).execute()
    return {"ok": True}


@app.delete("/processos/{processo_id}/prism/conversas/{conversa_id}")
def prism_excluir_conversa(
    processo_id: str,
    conversa_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    _carregar_conversa_propria(sb, user, nome, conversa_id)
    sb.table("prism_conversas").delete().eq("id", conversa_id).execute()
    return {"ok": True}


@app.post("/processos/{processo_id}/prism/conversas/{conversa_id}/mensagens")
def prism_enviar_mensagem(
    processo_id: str,
    conversa_id: str,
    body: PrismMensagemBody,
    user: CurrentUser = Depends(get_current_user),
):
    from datetime import datetime

    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    conv = _carregar_conversa_propria(sb, user, nome, conversa_id)

    # Histórico (em ordem cronológica) — usado tanto pra mandar ao LLM quanto
    # pra detectar se é a primeira troca (pra título auto).
    msgs_existentes = (
        sb.table("prism_mensagens")
        .select("papel, conteudo")
        .eq("conversa_id", conversa_id)
        .order("criada_em", desc=False)
        .limit(_PRISM_MAX_TROCAS * 4)
        .execute()
        .data
    ) or []
    eh_primeira_troca = len(msgs_existentes) == 0

    # Grava a mensagem do usuário antes de chamar o LLM (defensivo: se a
    # geração falhar, a pergunta fica registrada e o front pode tentar de novo).
    sb.table("prism_mensagens").insert(
        {
            "conversa_id": conversa_id,
            "empresa": user.empresa,
            "processo": nome,
            "papel": "user",
            "conteudo": body.pergunta.strip(),
        }
    ).execute()

    historico_chat = [
        {"role": m["papel"], "content": m["conteudo"]}
        for m in msgs_existentes
        if m.get("papel") in ("user", "assistant")
    ]

    groq_client = make_groq_client()
    try:
        resposta = responder_chat(
            groq_client,
            sb,
            user.empresa,
            nome,
            body.pergunta.strip(),
            historico=historico_chat,
            max_trocas=_PRISM_MAX_TROCAS,
        )
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Falha ao consultar o Prism: {e}")

    resposta_txt = (resposta or "").strip() or "Desculpe, não consegui responder agora."

    sb.table("prism_mensagens").insert(
        {
            "conversa_id": conversa_id,
            "empresa": user.empresa,
            "processo": nome,
            "papel": "assistant",
            "conteudo": resposta_txt,
        }
    ).execute()

    # Título automático na primeira troca — NÃO-FATAL.
    titulo_auto: str | None = None
    if eh_primeira_troca and conv.get("titulo_auto"):
        try:
            t = gerar_titulo_conversa(groq_client, body.pergunta, resposta_txt)
            if t:
                sb.table("prism_conversas").update(
                    {"titulo": t, "atualizada_em": datetime.utcnow().isoformat()}
                ).eq("id", conversa_id).execute()
                titulo_auto = t
        except Exception as e:
            log.warning(f"Prism: título auto falhou (não-fatal): {e}")

    if titulo_auto is None:
        sb.table("prism_conversas").update(
            {"atualizada_em": datetime.utcnow().isoformat()}
        ).eq("id", conversa_id).execute()

    return {
        "resposta": resposta_txt,
        "titulo_auto": titulo_auto,
        "fora_de_escopo": False,
    }


@app.get("/processos/{processo_id}/prism/sugestoes")
def prism_sugestoes(
    processo_id: str,
    excluir: str = "",
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    excluidas = [s.strip() for s in excluir.split("|") if s.strip()] if excluir else []
    groq_client = make_groq_client()
    sugestoes = gerar_sugestoes_chat(
        sb, groq_client, user.empresa, nome, excluir=excluidas, n=4
    )
    return {"sugestoes": sugestoes}


# ═════════════════════════════════════════════════════════════════════════
# PRISM GLOBAL — visão de portfólio (toda a empresa). Conversas com
# escopo='global' e processo=null. RLS continua por empresa.
# ═════════════════════════════════════════════════════════════════════════
def _carregar_conversa_global(sb, user: CurrentUser, conversa_id: str) -> dict:
    r = (
        sb.table("prism_conversas")
        .select("id, empresa, escopo, titulo, titulo_auto, criada_em, atualizada_em")
        .eq("id", conversa_id)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversa não encontrada")
    c = r.data[0]
    if c["empresa"] != user.empresa or c.get("escopo") != "global":
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    return c


@app.get("/prism/conversas")
def prism_g_listar(user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    r = (
        sb.table("prism_conversas")
        .select("id, titulo, titulo_auto, criada_em, atualizada_em")
        .eq("empresa", user.empresa)
        .eq("escopo", "global")
        .order("atualizada_em", desc=True)
        .limit(200)
        .execute()
    )
    return r.data or []


@app.post("/prism/conversas")
def prism_g_criar(user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    r = (
        sb.table("prism_conversas")
        .insert({"empresa": user.empresa, "processo": None, "escopo": "global"})
        .execute()
    )
    return r.data[0]


@app.get("/prism/conversas/{conversa_id}")
def prism_g_get(conversa_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    c = _carregar_conversa_global(sb, user, conversa_id)
    msgs = (
        sb.table("prism_mensagens")
        .select("id, papel, conteudo, criada_em")
        .eq("conversa_id", conversa_id)
        .order("criada_em", desc=False)
        .limit(500)
        .execute()
        .data
    ) or []
    return {**c, "mensagens": msgs}


@app.patch("/prism/conversas/{conversa_id}")
def prism_g_renomear(
    conversa_id: str,
    body: PrismRenomearBody,
    user: CurrentUser = Depends(get_current_user),
):
    from datetime import datetime

    sb = make_supabase_client()
    _carregar_conversa_global(sb, user, conversa_id)
    sb.table("prism_conversas").update(
        {
            "titulo": body.titulo.strip(),
            "titulo_auto": False,
            "atualizada_em": datetime.utcnow().isoformat(),
        }
    ).eq("id", conversa_id).execute()
    return {"ok": True}


@app.delete("/prism/conversas/{conversa_id}")
def prism_g_excluir(conversa_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    _carregar_conversa_global(sb, user, conversa_id)
    sb.table("prism_conversas").delete().eq("id", conversa_id).execute()
    return {"ok": True}


@app.post("/prism/conversas/{conversa_id}/mensagens")
def prism_g_enviar(
    conversa_id: str,
    body: PrismMensagemBody,
    user: CurrentUser = Depends(get_current_user),
):
    from datetime import datetime

    sb = make_supabase_client()
    conv = _carregar_conversa_global(sb, user, conversa_id)

    msgs_existentes = (
        sb.table("prism_mensagens")
        .select("papel, conteudo")
        .eq("conversa_id", conversa_id)
        .order("criada_em", desc=False)
        .limit(_PRISM_MAX_TROCAS * 4)
        .execute()
        .data
    ) or []
    eh_primeira_troca = len(msgs_existentes) == 0

    sb.table("prism_mensagens").insert(
        {
            "conversa_id": conversa_id,
            "empresa": user.empresa,
            "processo": None,
            "papel": "user",
            "conteudo": body.pergunta.strip(),
        }
    ).execute()

    historico_chat = [
        {"role": m["papel"], "content": m["conteudo"]}
        for m in msgs_existentes
        if m.get("papel") in ("user", "assistant")
    ]

    groq_client = make_groq_client()
    try:
        resposta = responder_chat_global(
            groq_client,
            sb,
            user.empresa,
            body.pergunta.strip(),
            historico=historico_chat,
            max_trocas=_PRISM_MAX_TROCAS,
        )
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Falha ao consultar o Prism: {e}")

    resposta_txt = (resposta or "").strip() or "Desculpe, não consegui responder agora."

    sb.table("prism_mensagens").insert(
        {
            "conversa_id": conversa_id,
            "empresa": user.empresa,
            "processo": None,
            "papel": "assistant",
            "conteudo": resposta_txt,
        }
    ).execute()

    titulo_auto: str | None = None
    if eh_primeira_troca and conv.get("titulo_auto"):
        try:
            t = gerar_titulo_conversa(groq_client, body.pergunta, resposta_txt)
            if t:
                sb.table("prism_conversas").update(
                    {"titulo": t, "atualizada_em": datetime.utcnow().isoformat()}
                ).eq("id", conversa_id).execute()
                titulo_auto = t
        except Exception as e:
            log.warning(f"Prism global: título auto falhou: {e}")

    if titulo_auto is None:
        sb.table("prism_conversas").update(
            {"atualizada_em": datetime.utcnow().isoformat()}
        ).eq("id", conversa_id).execute()

    return {"resposta": resposta_txt, "titulo_auto": titulo_auto, "fora_de_escopo": False}


@app.get("/prism/sugestoes")
def prism_g_sugestoes(excluir: str = "", user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    excluidas = [s.strip() for s in excluir.split("|") if s.strip()] if excluir else []
    sugestoes = gerar_sugestoes_chat_global(
        sb, make_groq_client(), user.empresa, excluir=excluidas, n=4
    )
    return {"sugestoes": sugestoes}


@app.get("/prism/insights-globais")
def prism_insights_globais(user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    r = (
        sb.table("insights_globais")
        .select("id, prioridade, titulo, descricao, processos_relacionados, criado_em")
        .eq("empresa", user.empresa)
        .order("criado_em", desc=True)
        .limit(20)
        .execute()
    )
    return r.data or []


# ═════════════════════════════════════════════════════════════════════════
# PADRÕES (temporais/estruturais por processo + globais por empresa)
# ═════════════════════════════════════════════════════════════════════════
@app.get("/processos/{processo_id}/padroes")
def padroes_do_processo(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    r = (
        sb.table("padroes_processo")
        .select(
            "id, tipo, camada, titulo, descricao, comportamentos_relacionados, "
            "categoria_relacionada, confianca, relevancia, recomendacao, n_videos_analisados, criado_em"
        )
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .order("criado_em", desc=True)
        .limit(50)
        .execute()
    )
    return r.data or []


@app.get("/processos/{processo_id}/serie-temporal")
def serie_temporal_processo(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    serie = montar_serie_temporal(sb, user.empresa, nome)
    return serie


@app.get("/prism/padroes-globais")
def padroes_globais_empresa(user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    r = (
        sb.table("padroes_globais")
        .select("id, tipo, titulo, descricao, processos_relacionados, confianca, relevancia, recomendacao, criado_em")
        .eq("empresa", user.empresa)
        .order("criado_em", desc=True)
        .limit(30)
        .execute()
    )
    return r.data or []


# ═════════════════════════════════════════════════════════════════════════
# HEALTH
# ═════════════════════════════════════════════════════════════════════════
@app.get("/health")
def health():
    return {"ok": True}
