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
    Form,
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
    extrair_3_frames_tempo,
    frame_para_jpeg_bytes,
    make_groq_client,
    make_supabase_client,
    montar_snapshot_chat,
    montar_snapshot_global,
    resolver_descricao_processo,
    responder_chat,
    gerar_titulo_conversa,
    gerar_sugestoes_chat,
    agregar_portfolio,
    montar_insights_quantitativos,
    responder_chat_global,
    gerar_sugestoes_chat_global,
    gerar_insights_globais,
    montar_serie_temporal,
    analisar_padroes_globais,
    gerar_pergunta_onboarding,
    agrupar_eventos_multicamera,
    evento_relevante_para_validacao,
    _parse_gravado_em_nome,
    _seg_token_nome,
)
from .worker import executar_job, _baixar_video  # noqa: F401

log = logging.getLogger("kalidash.api")

app = FastAPI(title="Kalidash Vision", version="0.1.0")


@app.on_event("startup")
def _checar_segredos() -> None:
    faltando = [k for k in ("SUPABASE_URL", "SUPABASE_KEY") if not os.environ.get(k)]
    if faltando:
        log.error(
            "Variáveis de ambiente ausentes: %s. "
            "Copie backend/.env.example para backend/.env e preencha as chaves.",
            ", ".join(faltando),
        )
    # Fase 13: basta UMA chave de provedor de IA (Claude/GPT/Groq/Gemini). A
    # cadeia de fallback pula os provedores sem chave. Sem NENHUMA, as chamadas
    # de IA falham em runtime — avisa alto, mas não derruba o boot.
    provedores = {
        "ANTHROPIC_API_KEY (Claude)": os.environ.get("ANTHROPIC_API_KEY"),
        "OPENAI_API_KEY (GPT)": os.environ.get("OPENAI_API_KEY"),
        "GROQ_API_KEY (Groq)": os.environ.get("GROQ_API_KEY"),
        "GEMINI_API_KEY (Gemini)": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"),
    }
    if not any(provedores.values()):
        log.error(
            "Nenhuma chave de provedor de IA configurada. Defina ao menos uma: %s.",
            ", ".join(provedores.keys()),
        )


@app.on_event("startup")
def _iniciar_infra_jobs() -> None:
    """Sobe a fila in-process serial e o debouncer dos blocos globais.
    Ambos sobrevivem a restart via persistência em /tmp."""
    try:
        from . import debouncer, job_queue, orquestrador_lote
        from .pipeline import make_groq_client, make_supabase_client

        debouncer.bootstrap(make_supabase_client, make_groq_client)
        job_queue.bootstrap()
        job_queue.start_worker_thread()
        orquestrador_lote.start_sweep_thread()   # Fase 6: pega lotes esquecidos
    except Exception as e:
        log.warning("Falha ao iniciar fila/debouncer: %s", e)

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


class OnboardingTurno(BaseModel):
    pergunta: str
    resposta: str


class OnboardingProximaBody(BaseModel):
    historico: list[OnboardingTurno] = Field(default_factory=list)
    area_inicial: str | None = None


class IntervaloTurnoBody(BaseModel):
    inicio: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # HH:MM 24h
    fim: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class TurnoBody(BaseModel):
    nome: str = Field(min_length=1, max_length=80)
    intervalos: list[IntervaloTurnoBody] = Field(default_factory=list)
    dias_semana: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 7])
    ativo: bool = True


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
    # enriquecimento (maturidade, pendências, composição) para a sidebar/dashboard.
    # Escopo de UM processo: a fórmula de maturidade já é por-processo, então
    # o número é idêntico ao da versão sem filtro — só com scan muito menor.
    try:
        st = agregar_portfolio(sb, user.empresa, processo=p["processo"]).get(p["processo"], {})
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


_MAX_DESC_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB — sobra de folga p/ PDFs grandes


@app.post("/processos/{processo_id}/descricao/extrair")
async def extrair_descricao_arquivo(
    processo_id: str,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    """Recebe um arquivo (PDF, DOCX, TXT, MD) enviado pelo gestor na tela de
    descrição manual e devolve o texto extraído. O frontend cola/anexa esse
    texto na textarea da descrição; nada é salvo aqui — a persistência segue
    pelo PUT /descricao existente. Autenticado e escopado ao processo."""
    # Auth + escopo de empresa (mesmo padrão dos outros endpoints).
    sb = make_supabase_client()
    _processo_nome(sb, user, processo_id)

    conteudo = await file.read()
    if not conteudo:
        return {"texto": ""}
    if len(conteudo) > _MAX_DESC_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "Arquivo muito grande (limite de 20 MB).",
        )

    nome = (file.filename or "").lower()
    ext = nome.rsplit(".", 1)[-1] if "." in nome else ""

    try:
        if ext == "pdf" or (file.content_type or "").endswith("/pdf"):
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(conteudo))
            partes = []
            for page in reader.pages:
                try:
                    t = page.extract_text() or ""
                except Exception:
                    t = ""
                if t.strip():
                    partes.append(t)
            texto = "\n\n".join(partes)
        elif ext == "docx" or (file.content_type or "").endswith("wordprocessingml.document"):
            from docx import Document

            doc = Document(io.BytesIO(conteudo))
            texto = "\n".join(p.text for p in doc.paragraphs if p.text and p.text.strip())
        elif ext in {"txt", "md", "markdown"} or (file.content_type or "").startswith("text/"):
            texto = conteudo.decode("utf-8", errors="replace")
        elif ext == "doc":
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                "Formato .doc (Word 97) não suportado. Converta para .docx ou PDF.",
            )
        else:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                "Formato não suportado. Use PDF, DOCX, TXT ou MD.",
            )
    except HTTPException:
        raise
    except Exception as e:
        log.warning(f"Falha ao extrair descrição do arquivo {nome!r}: {e}")
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Não foi possível ler este arquivo: {e}",
        )

    # Normalização leve (preserva quebras de parágrafo, remove espaços excessivos
    # em cada linha) — não muda conteúdo, só evita ruído de OCR/PDF.
    linhas = [" ".join(l.split()) for l in (texto or "").splitlines()]
    texto = "\n".join(linhas).strip()
    return {"texto": texto}


@app.post("/processos/{processo_id}/onboarding/proxima-pergunta")
def onboarding_proxima_pergunta(
    processo_id: str,
    body: OnboardingProximaBody,
    user: CurrentUser = Depends(get_current_user),
):
    """Conversa adaptativa para colher a descrição inicial do processo.

    Frontend envia o histórico (pergunta→resposta) acumulado e a área já
    informada. Backend devolve a próxima pergunta (com 3 chips de resposta
    rápida geradas pela LLM) ou, quando há cobertura suficiente, a
    descricao_consolidada (frontend salva via PUT /descricao).
    """
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    groq_client = make_groq_client()
    try:
        return gerar_pergunta_onboarding(
            groq_client,
            user.empresa,
            nome,
            body.area_inicial,
            [t.model_dump() for t in body.historico],
        )
    except Exception as e:
        log.warning(f"Falha no onboarding LLM ({user.empresa}/{nome}): {e}")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Falha ao gerar próxima pergunta: {e}",
        )


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


# ═════════════════════════════════════════════════════════════════════════
# TURNOS DE GRAVAÇÃO (configuração consumida pela borda Pi)
# Cada processo pode ter N turnos. Cada turno tem dias_semana (ISO 1=seg..7=dom)
# e uma lista de intervalos {inicio, fim} no formato "HH:MM". A pausa de
# almoço é o GAP entre intervalos consecutivos. Datas e timezone ficam por
# conta do runner da borda (que conhece o relógio local da fábrica).
# ═════════════════════════════════════════════════════════════════════════
_DIAS_VALIDOS = {1, 2, 3, 4, 5, 6, 7}


def _hhmm_min(s: str) -> int:
    """Converte 'HH:MM' em minutos desde 00:00 (já validado por regex no Body)."""
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _validar_intervalos(itens: list[dict]) -> list[dict]:
    """Valida e normaliza a lista de intervalos:
    - inicio < fim em cada intervalo (sem cruzar meia-noite — caso raro em
      fábrica; se aparecer, divida em dois turnos);
    - ordena por inicio;
    - rejeita sobreposições.
    Levanta 400 com mensagem amigável em caso de erro.
    """
    norm = []
    for it in itens:
        ini, fim = it["inicio"], it["fim"]
        if _hhmm_min(ini) >= _hhmm_min(fim):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Intervalo {ini}–{fim}: o horário de início precisa ser menor que o de fim.",
            )
        norm.append({"inicio": ini, "fim": fim})
    norm.sort(key=lambda x: _hhmm_min(x["inicio"]))
    for a, b in zip(norm, norm[1:]):
        if _hhmm_min(a["fim"]) > _hhmm_min(b["inicio"]):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Intervalos sobrepostos: {a['inicio']}–{a['fim']} e {b['inicio']}–{b['fim']}.",
            )
    return norm


def _validar_dias(dias: list[int]) -> list[int]:
    if not dias:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Escolha pelo menos um dia da semana para o turno.",
        )
    if any(d not in _DIAS_VALIDOS for d in dias):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "dias_semana deve conter inteiros entre 1 (seg) e 7 (dom).",
        )
    # remove duplicatas, ordena
    return sorted(set(dias))


@app.get("/processos/{processo_id}/turnos")
def listar_turnos(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    r = (
        sb.table("turnos_processo")
        .select("id, nome, intervalos, dias_semana, ativo, criado_em, atualizado_em")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .order("criado_em", desc=False)
        .execute()
    )
    return r.data or []


@app.post("/processos/{processo_id}/turnos")
def criar_turno(
    processo_id: str,
    body: TurnoBody,
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    intervalos = _validar_intervalos([i.model_dump() for i in body.intervalos])
    dias = _validar_dias(body.dias_semana)
    r = (
        sb.table("turnos_processo")
        .insert(
            {
                "empresa": user.empresa,
                "processo": nome,
                "nome": body.nome.strip(),
                "intervalos": intervalos,
                "dias_semana": dias,
                "ativo": bool(body.ativo),
            }
        )
        .execute()
    )
    return r.data[0]


def _carregar_turno_proprio(sb, user: CurrentUser, turno_id: str) -> dict:
    """Carrega um turno garantindo que pertence à empresa do usuário (403)."""
    r = (
        sb.table("turnos_processo")
        .select("id, empresa, processo")
        .eq("id", turno_id)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Turno não encontrado")
    t = r.data[0]
    if t["empresa"] != user.empresa:
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    return t


@app.put("/turnos/{turno_id}")
def atualizar_turno(
    turno_id: str,
    body: TurnoBody,
    user: CurrentUser = Depends(get_current_user),
):
    from datetime import datetime

    sb = make_supabase_client()
    _carregar_turno_proprio(sb, user, turno_id)
    intervalos = _validar_intervalos([i.model_dump() for i in body.intervalos])
    dias = _validar_dias(body.dias_semana)
    r = (
        sb.table("turnos_processo")
        .update(
            {
                "nome": body.nome.strip(),
                "intervalos": intervalos,
                "dias_semana": dias,
                "ativo": bool(body.ativo),
                "atualizado_em": datetime.utcnow().isoformat(),
            }
        )
        .eq("id", turno_id)
        .execute()
    )
    return (r.data or [{}])[0]


@app.delete("/turnos/{turno_id}")
def excluir_turno(turno_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    _carregar_turno_proprio(sb, user, turno_id)
    sb.table("turnos_processo").delete().eq("id", turno_id).execute()
    return {"ok": True}


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

    # 1b) Remove também os JPEGs de frames em cache (prefixo __frames ao lado
    # de cada vídeo). NÃO-FATAL — não pode quebrar o delete.
    try:
        import posixpath

        prefixos = {
            posixpath.dirname(v["caminho"]) + "/__frames"
            for v in (vids or [])
            if v.get("caminho")
            and not v["caminho"].startswith(("/", "\\"))
            and not (len(v["caminho"]) > 1 and v["caminho"][1] == ":")
        }
        for prefixo in prefixos:
            try:
                itens = sb.storage.from_(bucket).list(prefixo) or []
                chaves = [f"{prefixo}/{it['name']}" for it in itens if it.get("name")]
                if chaves:
                    sb.storage.from_(bucket).remove(chaves)
            except Exception as e2:
                log.warning(f"Falha ao limpar cache de frames {prefixo}: {e2}")
    except Exception as e:
        log.warning(f"Limpeza de cache de frames falhou (não-fatal): {e}")

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
    # Portfólio + snapshot global são computados UMA vez e reaproveitados nos
    # dois passos (cada um deles, internamente, faria o mesmo scan caro).
    try:
        gc = make_groq_client()
        portfolio = agregar_portfolio(sb, user.empresa)
        snapshot_global = montar_snapshot_global(sb, user.empresa, portfolio=portfolio)
        gerar_insights_globais(sb, gc, user.empresa, snapshot_global=snapshot_global)
        analisar_padroes_globais(sb, gc, user.empresa, portfolio=portfolio)
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
    # Fase 1 multi-câmera (additive, opcional): edge passa esses 2 campos.
    # Upload manual (frontend) NÃO passa — fica NULL no banco.
    cam_id: str | None = Form(default=None),
    gravado_em: str | None = Form(default=None),
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

    # Fase 9: idempotência da inbox do edge. Se o edge re-enviar o MESMO segmento
    # (mesmo cam_id + nome — determinístico por instante real: seg_AAAAMMDD_HHMMSS),
    # NÃO duplica nem re-sobe ao storage. Devolve sucesso e o edge apaga o arquivo
    # local. Cobre o retry causado pelo bug do job_id (sem limpeza manual).
    if cam_id:
        try:
            ja = (
                sb.table("segmentos")
                .select("id")
                .eq("empresa", user.empresa)
                .eq("processo", processo_nome)
                .eq("cam_id", cam_id)
                .eq("nome", file.filename or "")
                .limit(1)
                .execute()
                .data
            ) or []
        except Exception:
            ja = []
        if ja:
            return {"ok": True, "modo": "lote", "status": "duplicado"}

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

    # Fase 6: DESACOPLA upload de processamento para o edge.
    #  • Edge (cam_id presente): o edge sobe TODA a cam1 e depois TODA a cam2
    #    ao longo de horas. NÃO processamos na hora (o par da outra câmera ainda
    #    nem chegou). Registramos o segmento na inbox `segmentos` (pendente); o
    #    orquestrador pareia cam1/cam2 pelo nome e processa 1 por 1 quando o lote
    #    termina (sinal /lote/concluido ou varredura por inatividade).
    #  • Manual (frontend, sem cam_id): processa na hora (fila serial), como hoje.
    gravado_em_efetivo = (gravado_em or None) or _parse_gravado_em_nome(file.filename)

    if cam_id:
        linha_seg: dict = {
            "empresa": user.empresa,
            "processo": processo_nome,
            "storage_path": storage_path,
            "nome": file.filename,
            "cam_id": cam_id,
            "status": "pendente",
        }
        if gravado_em_efetivo:
            linha_seg["gravado_em"] = gravado_em_efetivo
        try:
            sb.table("segmentos").insert(linha_seg).execute()
        except Exception as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                f"Falha ao registrar segmento na inbox: {e}",
            )
        return {"ok": True, "modo": "lote", "status": "pendente"}

    # Upload manual: fila serial imediata (1 vídeo por vez) — ver job_queue.
    job = JOBS.create(processo_id=processo_id, user_id=user.id)
    from . import job_queue
    job_queue.enqueue(
        job.id,
        user.empresa,
        processo_nome,
        storage_path,
        descricao,
        file.filename,
        cam_id=None,
        gravado_em=gravado_em_efetivo,
    )
    return {"job_id": job.id}


@app.post("/processos/{processo_id}/lote/concluido")
def lote_concluido(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    """Sinal do edge: terminei de enviar o lote deste processo — pode processar.

    Pareia os segmentos pendentes (cam1+cam2 pelo nome) e os enfileira na fila
    serial. Idempotente: a varredura periódica também dispara isso sozinha caso
    o sinal se perca.
    """
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    from . import orquestrador_lote
    resumo = orquestrador_lote.processar_lote(sb, user.empresa, nome)
    return {"ok": True, **resumo}


_FILA_STATUS = ["pendente", "enfileirado", "processando", "concluido", "erro"]


@app.get("/processos/{processo_id}/fila")
def listar_fila(
    processo_id: str,
    status_filter: str = Query("todos", alias="status"),
    user: CurrentUser = Depends(get_current_user),
):
    """Estado da inbox `segmentos` deste processo (painel da fila, Fase 7)."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)

    # Contagens por status — baratas (count exato, sem puxar linhas).
    contagens: dict[str, int] = {}
    for st in _FILA_STATUS:
        try:
            r = (
                sb.table("segmentos")
                .select("id", count="exact")
                .eq("empresa", user.empresa)
                .eq("processo", nome)
                .eq("status", st)
                .limit(1)
                .execute()
            )
            contagens[st] = r.count or 0
        except Exception:
            contagens[st] = 0
    total = sum(contagens.values())

    # Lista p/ exibição (cap 500, mais recentes primeiro).
    q = (
        sb.table("segmentos")
        .select("id, nome, cam_id, gravado_em, status, erro, recebido_em, processado_em, video_id")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
    )
    if status_filter in _FILA_STATUS:
        q = q.eq("status", status_filter)
    itens = (q.order("recebido_em", desc=True).limit(500).execute().data) or []
    for s in itens:
        s["seg_token"] = _seg_token_nome(s.get("nome"))

    return {"contagens": contagens, "total": total, "itens": itens}


@app.post("/processos/{processo_id}/fila/reprocessar-erros")
def reprocessar_erros(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    """Volta os segmentos em `erro` para `pendente` e re-enfileira o lote."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    try:
        alvo = (
            sb.table("segmentos")
            .select("id")
            .eq("empresa", user.empresa)
            .eq("processo", nome)
            .eq("status", "erro")
            .limit(20000)
            .execute()
            .data
        ) or []
        for s in alvo:
            sb.table("segmentos").update({"status": "pendente", "erro": None}).eq("id", s["id"]).execute()
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Falha ao reprocessar erros: {e}")

    from . import orquestrador_lote
    resumo = orquestrador_lote.processar_lote(sb, user.empresa, nome)
    return {"ok": True, "reset": len(alvo), **resumo}


@app.get("/ai/uso")
def ai_uso(user: CurrentUser = Depends(get_current_user)):
    """Gasto de IA do mês por provedor + tetos (Fase 14). Devolve só números
    agregados (nunca chaves). O gasto é GLOBAL do deploy (as chaves de IA são
    compartilhadas por todas as empresas), não por empresa."""
    from . import ai_provider
    return ai_provider.resumo_uso()


@app.get("/fila/global")
def fila_global(user: CurrentUser = Depends(get_current_user)):
    """Visão GLOBAL da fila (Fase 8): agrega a inbox `segmentos` de TODOS os
    processos da empresa, com contagens por status por processo + totais."""
    sb = make_supabase_client()
    # Mapa nome do processo → id (p/ o frontend abrir a fila de cada um).
    try:
        ctx = (
            sb.table("contexto_processo")
            .select("id, processo")
            .eq("empresa", user.empresa)
            .execute()
            .data
        ) or []
    except Exception:
        ctx = []
    id_por_nome = {c["processo"]: c["id"] for c in ctx}

    # Puxa só (processo, status) de todos os segmentos da empresa e agrega em Python.
    try:
        rows = (
            sb.table("segmentos")
            .select("processo, status")
            .eq("empresa", user.empresa)
            .limit(100000)
            .execute()
            .data
        ) or []
    except Exception as e:
        log.warning("fila_global: falha ao ler segmentos (%s)", e)
        rows = []

    por_proc: dict[str, dict[str, int]] = {}
    totais = {s: 0 for s in _FILA_STATUS}
    for r in rows:
        proc = r.get("processo") or "—"
        st = r.get("status") or "pendente"
        if st not in _FILA_STATUS:
            st = "pendente"
        d = por_proc.setdefault(proc, {s: 0 for s in _FILA_STATUS})
        d[st] += 1
        totais[st] += 1

    processos = []
    for nome, c in sorted(por_proc.items(), key=lambda kv: kv[0].lower()):
        processos.append({
            "processo": nome,
            "processo_id": id_por_nome.get(nome),
            "contagens": c,
            "total": sum(c.values()),
        })
    return {"contagens": totais, "total": sum(totais.values()), "processos": processos}


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

    # Busca eventos / vídeos / comportamentos UMA vez com superset de colunas e
    # reaproveita no snapshot e nos blocos abaixo. Antes, cada bloco refazia o
    # próprio scan — dashboard fazia 3 buscas redundantes só por isso.
    # Superset = união das colunas usadas pelo snapshot e pelo dashboard.
    from collections import Counter

    evs = (
        sb.table("eventos")
        .select(
            "id, video_id, pessoa_track_id, comportamento_label, label_corrigido, "
            "tempo_inicio_s, tempo_fim_s, validacao_correto, validado_humano, "
            "origem_validacao, confianca, principal, zona_contexto"
        )
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .limit(50000)
        .execute()
        .data
    ) or []
    videos = (
        sb.table("videos")
        .select("id, nome, duracao_s, total_eventos, total_pessoas, processado_em")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .order("processado_em", desc=True)
        .execute()
        .data
    ) or []
    comps_full = (
        sb.table("comportamentos")
        .select("id, label, descricao, categoria_lean, categoria_lean_origem")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .execute()
        .data
    ) or []

    snapshot = montar_snapshot_chat(
        sb, user.empresa, nome,
        eventos=evs, videos=videos, comportamentos=comps_full,
    )

    sugs = (
        sb.table("sugestoes_melhoria")
        .select("id, prioridade, area, situacao, causa_provavel, sugestao, impacto_estimado, eventos_relacionados, status, voltou_apos_realizada, criado_em")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .eq("status", "pendente")
        .order("criado_em", desc=True)
        .limit(12)  # Fase 18: sugestões curadas (≤ KV_SUGESTOES_MAX) — teto baixo
        .execute()
        .data
    ) or []

    base = [
        e for e in evs
        if e.get("validacao_correto") is not False and e.get("principal") is not False
    ]
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

    # Categoria Lean por comportamento (mapa label → categoria).
    # `comps_full` foi buscado uma vez no topo desta função.
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
        # Fase 17: insights simples e numéricos (frases + tempo por ação + Lean +
        # ROI + tendência), determinísticos, a partir dos eventos principais.
        "insights_quantitativos": montar_insights_quantitativos(
            dist_enriquecida, composicao_valor, base, videos, cat_por_label
        ),
        # mesma cap (50 mais recentes) que existia antes; videos foi buscado
        # uma vez no topo, mas só os mais recentes vão para a resposta.
        "videos": videos[:50],
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
    # Fase 18: só as PENDENTES (o painel age sobre elas), com teto — evita
    # devolver a pilha inteira. As sugestões já vêm curadas (≤ KV_SUGESTOES_MAX).
    r = (
        sb.table("sugestoes_melhoria")
        .select("*")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .eq("status", "pendente")
        .order("criado_em", desc=True)
        .limit(20)
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
    agrupar: bool = Query(False),
    gate: bool = Query(True),
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    q = (
        sb.table("eventos")
        .select(
            "id, video_id, comportamento_label, descricao_bruta, tempo_inicio_s, "
            "tempo_fim_s, confianca, validado_humano, validacao_correto, n_amostras, "
            "label_corrigido, origem_validacao, frame_inicio, frame_fim, bbox_inicio, pessoa_track_id, principal"
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
    # Fase 16: só os PRINCIPAIS (1/min) vão pros cards de validação; os crus de
    # auditoria (principal=False) ficam de fora. Vídeos antigos (null) seguem.
    itens = [e for e in itens if e.get("principal") is not False]

    # Categoria Lean PREVISTA + total_ocorrencias por label (join leve). A
    # categoria alimenta o display E o gate de relevância (Fase 5); o
    # total_ocorrencias alimenta o sinal de "raridade" do gate.
    labels_distintos = list({(i.get("label_corrigido") or i.get("comportamento_label")) for i in itens})
    labels_distintos = [l for l in labels_distintos if l]
    cat_por_label: dict[str, str | None] = {}
    ocorr_por_label: dict[str, int] = {}
    if labels_distintos:
        try:
            comp = (
                sb.table("comportamentos")
                .select("label, categoria_lean, total_ocorrencias")
                .eq("empresa", user.empresa)
                .eq("processo", nome)
                .in_("label", labels_distintos)
                .execute()
                .data
            ) or []
            cat_por_label = {c["label"]: c.get("categoria_lean") for c in comp}
            ocorr_por_label = {c["label"]: (c.get("total_ocorrencias") or 0) for c in comp}
        except Exception:
            cat_por_label = {}
            ocorr_por_label = {}
        for i in itens:
            lbl = i.get("label_corrigido") or i.get("comportamento_label")
            i["categoria_lean_prevista"] = cat_por_label.get(lbl)

    # Fase 2 multi-câmera: agrupa eventos da MESMA ação vistos por câmeras
    # diferentes (só pendentes). Back-compat: sem agrupar=true, shape inalterado.
    if agrupar and status_filter == "pendente" and itens:
        # Join leve eventos→videos p/ trazer cam_id + gravado_em (padrão de
        # listar_eventos_tabela). Anexa em cada evento p/ display e agrupamento.
        vids = {i["video_id"] for i in itens if i.get("video_id")}
        meta_video: dict[str, dict] = {}
        if vids:
            rv = (
                sb.table("videos")
                .select("id, cam_id, gravado_em")
                .in_("id", list(vids))
                .execute()
            )
            meta_video = {v["id"]: v for v in (rv.data or [])}
        for i in itens:
            mv = meta_video.get(i.get("video_id"), {})
            i["cam_id"] = mv.get("cam_id")
            i["gravado_em"] = mv.get("gravado_em")

        grupos, secundarios = agrupar_eventos_multicamera(itens)
        # Anexa irmãos ao primário; eventos solo ficam com irmaos=[].
        for i in itens:
            irm = grupos.get(i["id"], [])
            i["irmaos"] = [
                {
                    "id": s["id"],
                    "cam_id": s.get("cam_id"),
                    "comportamento_label": s.get("comportamento_label"),
                    "label_corrigido": s.get("label_corrigido"),
                    "confianca": s.get("confianca"),
                    "pessoa_track_id": s.get("pessoa_track_id"),
                    "tempo_inicio_s": s.get("tempo_inicio_s"),
                    "tempo_fim_s": s.get("tempo_fim_s"),
                    "categoria_lean_prevista": s.get("categoria_lean_prevista"),
                }
                for s in irm
            ]
        # Fase 6 (dual-angle): eventos processados com os 2 ângulos juntos têm
        # UMA trilha só (na cam1) — sem irmão-evento. Para mostrar o 2º ângulo
        # no card, achamos o SEGMENTO par (mesmo video_id, outra câmera) na inbox
        # e anexamos `segundo_angulo` {segmento_id, cam_id}. Só p/ eventos sem
        # irmãos pós-hoc (senão duplicaria).
        try:
            if vids:
                rs = (
                    sb.table("segmentos")
                    .select("id, video_id, cam_id")
                    .eq("empresa", user.empresa)
                    .in_("video_id", list(vids))
                    .eq("status", "concluido")
                    .execute()
                    .data
                ) or []
                segs_por_video: dict[str, list[dict]] = {}
                for s in rs:
                    segs_por_video.setdefault(s.get("video_id"), []).append(s)
                for i in itens:
                    if i.get("irmaos"):
                        continue
                    pares = [
                        s for s in segs_por_video.get(i.get("video_id"), [])
                        if s.get("cam_id") and s.get("cam_id") != i.get("cam_id")
                    ]
                    if pares:
                        i["segundo_angulo"] = {"segmento_id": pares[0]["id"], "cam_id": pares[0].get("cam_id")}
        except Exception as e:
            log.warning("segundo_angulo: lookup falhou (%s)", e)

        # Remove os secundários do topo (1 card por grupo).
        itens = [i for i in itens if i["id"] not in secundarios]

    # Fase 5: gate de relevância — esconde micro-ações da FILA de validação
    # (não-destrutivo: continuam no banco, nas métricas e na tabela de Eventos).
    # Roda sobre a lista final (após agrupamento). Permissivo enquanto o
    # processo é imaturo; aperta conforme a maturidade sobe. Opt-out: ?gate=false
    # ou KV_VALIDACAO_GATE=off. Fail-open: qualquer erro mantém a fila completa.
    if (
        gate
        and status_filter == "pendente"
        and itens
        and os.environ.get("KV_VALIDACAO_GATE", "on").lower() not in ("off", "0", "false")
    ):
        try:
            portfolio = agregar_portfolio(sb, user.empresa, processo=nome)
            maturidade = float((portfolio.get(nome) or {}).get("maturidade", 0) or 0)
        except Exception as e:
            log.warning("gate: falha ao calcular maturidade (%s) — fila completa", e)
            maturidade = 0.0

        relevantes: list[dict] = []
        motivos: dict[str, int] = {}
        for i in itens:
            lbl = i.get("label_corrigido") or i.get("comportamento_label")
            ocorr = ocorr_por_label.get(lbl, 0)
            ok, motivo = evento_relevante_para_validacao(i, ocorr, maturidade)
            if ok:
                relevantes.append(i)
            else:
                motivos[motivo] = motivos.get(motivo, 0) + 1
        ocultados = len(itens) - len(relevantes)
        if ocultados:
            log.info(
                "gate: %s/%s eventos ocultados da validação (maturidade=%.0f, motivos=%s)",
                ocultados, len(itens), maturidade, motivos,
            )
        itens = relevantes

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
    cams: dict[str, str | None] = {}
    if vids:
        rv = (
            sb.table("videos")
            .select("id, nome, cam_id")
            .in_("id", list(vids))
            .execute()
        )
        nomes = {v["id"]: v.get("nome", "") for v in (rv.data or [])}
        cams = {v["id"]: v.get("cam_id") for v in (rv.data or [])}

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
        ev["cam_id"] = cams.get(ev.get("video_id"))
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
    import base64
    import posixpath

    # Cache determinístico de frames já extraídos (JPEGs pequenos), ao lado do
    # vídeo no Storage. Evita rebaixar o vídeo inteiro a cada visualização
    # (causa do OOM ao navegar na Validação).
    # O sufixo de VERSÃO (_v2) invalida caches antigos quando mudamos os
    # parâmetros de extração (resolução/qualidade). Bump aqui sempre que o
    # formato do frame mudar.
    _FRAMES_VER = "v2"
    frames_prefix = posixpath.dirname(caminho) + "/__frames"
    frame_keys = [f"{frames_prefix}/{evento_id}_{_FRAMES_VER}_{k}.jpg" for k in (0, 1, 2)]

    # 1) Tenta servir do cache. Para não fazer 3 round-trips num miss, sonda só
    #    o primeiro; se existir, baixa os 3 (cache foi escrito atômico junto).
    try:
        primeiro = sb.storage.from_(bucket).download(frame_keys[0])
        if primeiro:
            cached = [primeiro] + [
                sb.storage.from_(bucket).download(k) for k in frame_keys[1:]
            ]
            if all(cached):
                return {
                    "frames": [
                        "data:image/jpeg;base64," + base64.b64encode(j).decode("ascii")
                        for j in cached
                    ]
                }
    except Exception:
        pass  # cache miss → extrai abaixo

    # 2) Cache miss: baixa o vídeo via STREAMING pro disco (pico de RAM ≈ 1 chunk
    #    de 64 KB), NUNCA com .download() (que carrega o arquivo todo na RAM).
    import httpx
    from urllib.parse import quote

    suffix = Path(caminho).suffix or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        url = f"{os.environ['SUPABASE_URL']}/storage/v1/object/{bucket}/{quote(caminho, safe='/')}"
        headers = {
            "Authorization": f"Bearer {os.environ['SUPABASE_KEY']}",
            "apikey": os.environ["SUPABASE_KEY"],
        }
        with httpx.stream("GET", url, headers=headers, timeout=120) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes(1 << 16):
                tmp.write(chunk)
        tmp.close()
    except Exception as e:
        try:
            Path(tmp.name).unlink()
        except Exception:
            pass
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Falha ao baixar vídeo: {e}")

    # 3) Extrai os 3 frames da ação (seleção [fi, mid, ff], anotação
    #    proporcional, normaliza p/ ≤720px) e codifica em JPEG (quality 85).
    #    Os MESMOS bytes vão para a resposta e para o cache em Storage.
    try:
        crops = extrair_3_frames_evento(ev, tmp.name)
        jpegs = [frame_para_jpeg_bytes(c) for c in crops]
    finally:
        try:
            Path(tmp.name).unlink()
        except Exception:
            pass

    # 4) Grava no cache para as próximas visualizações (NÃO-FATAL).
    for key, jpeg in zip(frame_keys, jpegs):
        try:
            sb.storage.from_(bucket).upload(
                key, jpeg, {"content-type": "image/jpeg", "upsert": "true"}
            )
        except Exception as e:
            log.warning(f"Cache de frame falhou (não-fatal) {key}: {e}")

    # 5) Mesmos bytes da extração → saída byte-idêntica à resposta antiga.
    return {
        "frames": [
            "data:image/jpeg;base64," + base64.b64encode(j).decode("ascii")
            for j in jpegs
        ]
    }


@app.get("/segmentos/{segmento_id}/frames")
def frames_segmento(
    segmento_id: str,
    ini: float = Query(0.0),
    fim: float = Query(0.0),
    user: CurrentUser = Depends(get_current_user),
):
    """3 frames (por TEMPO) do 2º ângulo (cam2) na validação dual-câmera (Fase 6).

    O segmento da cam2 não tem evento próprio; pegamos 3 frames pela janela de
    tempo [ini, fim] (clock-aligned com a cam1). Mesma resposta de /eventos/.../frames.
    """
    sb = make_supabase_client()
    r = (
        sb.table("segmentos")
        .select("empresa, storage_path")
        .eq("id", segmento_id)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segmento não encontrado")
    seg = r.data[0]
    if seg.get("empresa") != user.empresa:
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    caminho = seg.get("storage_path")
    if not caminho:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segmento sem caminho no storage")

    import base64
    import posixpath
    import tempfile
    from pathlib import Path

    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")
    # Cache determinístico (JPEGs pequenos) ao lado do segmento no Storage.
    chave_t = f"{int(round(ini))}_{int(round(fim))}"
    frames_prefix = posixpath.dirname(caminho) + "/__frames"
    frame_keys = [f"{frames_prefix}/seg_{segmento_id}_{chave_t}_{k}.jpg" for k in (0, 1, 2)]
    try:
        primeiro = sb.storage.from_(bucket).download(frame_keys[0])
        if primeiro:
            cached = [primeiro] + [sb.storage.from_(bucket).download(k) for k in frame_keys[1:]]
            if all(cached):
                return {"frames": ["data:image/jpeg;base64," + base64.b64encode(j).decode("ascii") for j in cached]}
    except Exception:
        pass

    # Cache miss: streaming download → extrai por tempo.
    import httpx
    from urllib.parse import quote

    suffix = Path(caminho).suffix or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        url = f"{os.environ['SUPABASE_URL']}/storage/v1/object/{bucket}/{quote(caminho, safe='/')}"
        headers = {
            "Authorization": f"Bearer {os.environ['SUPABASE_KEY']}",
            "apikey": os.environ["SUPABASE_KEY"],
        }
        with httpx.stream("GET", url, headers=headers, timeout=120) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes(1 << 16):
                tmp.write(chunk)
        tmp.close()
    except Exception as e:
        try:
            Path(tmp.name).unlink()
        except Exception:
            pass
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Falha ao baixar segmento: {e}")

    try:
        crops = extrair_3_frames_tempo(tmp.name, ini, fim)
        jpegs = [frame_para_jpeg_bytes(c) for c in crops]
    finally:
        try:
            Path(tmp.name).unlink()
        except Exception:
            pass

    if not jpegs:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Não foi possível extrair frames do segmento")

    for key, jpeg in zip(frame_keys, jpegs):
        try:
            sb.storage.from_(bucket).upload(key, jpeg, {"content-type": "image/jpeg", "upsert": "true"})
        except Exception as e:
            log.warning(f"Cache de frame (segmento) falhou (não-fatal) {key}: {e}")

    return {"frames": ["data:image/jpeg;base64," + base64.b64encode(j).decode("ascii") for j in jpegs]}


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
