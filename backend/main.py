"""FastAPI · Kalidash Vision."""
from __future__ import annotations

import io
import logging
import os
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
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
    montar_analise_diaria,
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
    FRAMES_VER,
    chave_frame_evento,
    chave_frame_segmento,
    varrer_videos_expirados,
    propagar_categoria_para_eventos,
    relatorio_propagacao_lean,
    reverter_auto_validacao_maquina,
    diagnosticar_contagio_por_descricao,
    relatorio_reprocesso_por_video,
    aprendizado_automatico,
    APRENDIZADO_AUTO_PADRAO,
    categoria_efetiva,
    categoria_tem_evidencia,
    offset_video_segmento,
    placar_camadas,
    montar_fila_duvidas,
    limiar_duvida,
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
def _varredura_no_boot() -> None:
    """Rede secundária da Fase 74: se o Pi estiver desligado, o pulso não vem
    e a varredura nunca roda. O boot cobre esse caso. Escopo global (empresa
    None) e não-fatal — nada aqui pode impedir a API de subir."""
    try:
        _varrer_storage_com_throttle(make_supabase_client(), None, "startup")
    except Exception as e:  # noqa: BLE001
        log.warning("[varredura/startup] indisponível (não-fatal): %s", e)


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
    acao: str  # confirmar | corrigir | descartar | descricao_invalida | reabrir
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
    categoria_lean: str | None = None  # 'valor_agregado' | 'desperdicio' | None (Fase 49: binário)


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


class HeartbeatCameraBody(BaseModel):
    """Saúde de UMA câmera. `gravando` NÃO é "o RTSP respondeu" — é "o segmento
    está crescendo no disco". Um Hikvision pode responder no socket e entregar
    imagem preta/congelada; só o arquivo crescendo prova que há captura."""
    cam_id: str = Field(max_length=40)
    nome: str | None = Field(default=None, max_length=120)
    gravando: bool = False
    ultimo_segmento_em: str | None = None      # ISO 8601
    ultimo_segmento_bytes: int | None = None
    falhas: int = 0                            # falhas de conexão desde o último envio


class HeartbeatBody(BaseModel):
    """Fase 52: pulso do Pi. Tudo opcional além do essencial — um runner antigo
    ou um campo que o SO não expõe (temperatura) nunca pode derrubar o envio."""
    processo_id: str = Field(min_length=1, max_length=64)
    device_id: str = Field(min_length=1, max_length=120)
    runner_versao: str | None = Field(default=None, max_length=40)
    estado: str = Field(max_length=24)         # capturando|processando|ocioso|fora_de_turno
    cameras: list[HeartbeatCameraBody] = Field(default_factory=list)
    disco_livre_gb: float | None = None
    disco_uso_pct: float | None = None
    cpu_temp_c: float | None = None
    uptime_s: int | None = None
    turno_janela: str | None = Field(default=None, max_length=40)
    turno_deadline: str | None = None          # ISO 8601


class SegmentoUploadUrlBody(BaseModel):
    """Fase 32: modo teste — o navegador sobe o arquivo DIRETO ao Storage."""
    nome: str = Field(min_length=1, max_length=200)
    cam_id: str = Field(min_length=1, max_length=20)


class SegmentoRegistrarBody(BaseModel):
    nome: str = Field(min_length=1, max_length=200)
    cam_id: str = Field(min_length=1, max_length=20)
    storage_path: str = Field(min_length=1, max_length=400)
    # Fase 48: auditoria da seleção top-K do edge (opcionais; upload DIRETO).
    score: float | None = None
    selecao: str | None = Field(default=None, max_length=20)


PAPEIS_ZONA = ("posto_operador", "maquina", "interacao")


class ZonaBody(BaseModel):
    """Fase 28: zona nomeada por câmera. pts_rel em [0-1] no espaço do vídeo
    ENVIADO (recorte do edge)."""
    cam_id: str = Field(min_length=1, max_length=20)
    nome: str = Field(min_length=1, max_length=80)
    papel: str
    pts_rel: list[list[float]] = Field(min_length=3, max_length=30)
    descricao_contexto: str | None = Field(default=None, max_length=300)
    frame_ref_w: int | None = None
    frame_ref_h: int | None = None
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


class FusoBody(BaseModel):
    # None = volta ao padrão do ambiente (KV_TZ).
    fuso_horario: str | None = None


# Poucos e comuns no Brasil — a lista existe para o cliente não digitar um
# nome IANA errado, que é um erro silencioso caríssimo neste painel.
FUSOS_SUGERIDOS = [
    "America/Sao_Paulo", "America/Bahia", "America/Fortaleza",
    "America/Recife", "America/Belem", "America/Manaus",
    "America/Cuiaba", "America/Campo_Grande", "America/Rio_Branco",
    "America/Noronha", "UTC",
]


@app.get("/processos/{processo_id}/fuso")
def ler_fuso(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    """Fase 65 — fuso da FÁBRICA, usado pelo painel de saúde e pelo turno."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    tz, tz_nome = fuso_do_processo(sb, user.empresa, nome)
    configurado = None
    try:
        r = (
            sb.table("contexto_processo").select("fuso_horario")
            .eq("empresa", user.empresa).eq("processo", nome).limit(1).execute().data
        ) or []
        configurado = (r[0].get("fuso_horario") if r else None) or None
    except Exception:
        pass
    return {
        "configurado": configurado,
        "efetivo": tz_nome,
        "padrao_ambiente": FUSO_PADRAO,
        "agora_local": datetime.now(timezone.utc).astimezone(tz).strftime("%d/%m %H:%M"),
        "sugestoes": FUSOS_SUGERIDOS,
    }


@app.put("/processos/{processo_id}/fuso")
def setar_fuso(processo_id: str, body: FusoBody,
               user: CurrentUser = Depends(get_current_user)):
    """Grava o fuso. Recusa nome inválido: um fuso errado não dá erro em lugar
    nenhum — só faz o painel mentir o dia inteiro."""
    alvo = (body.fuso_horario or "").strip() or None
    if alvo is not None:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(alvo)
        except Exception:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Fuso desconhecido: {alvo!r}. Use um nome IANA, ex.: America/Sao_Paulo.",
            )
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    try:
        (
            sb.table("contexto_processo").update({"fuso_horario": alvo})
            .eq("empresa", user.empresa).eq("processo", nome).execute()
        )
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"Falha ao gravar o fuso: {e}")
    tz, tz_nome = fuso_do_processo(sb, user.empresa, nome)
    return {
        "ok": True, "configurado": alvo, "efetivo": tz_nome,
        "agora_local": datetime.now(timezone.utc).astimezone(tz).strftime("%d/%m %H:%M"),
    }


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


# ═════════════════════════════════════════════════════════════════════════
# Fase 52 — SAÚDE DA BORDA (heartbeat do Pi)
#
# A ideia que organiza tudo: OFFLINE NÃO É SEMPRE PROBLEMA. Às 22h, no
# domingo ou no almoço, o Pi DEVE estar parado. Um painel que pisca vermelho
# fora do turno ensina o cliente a ignorar o alerta — e aí ele ignora o
# alerta de verdade. Então o estado nunca é "online/offline": é sempre o
# OBSERVADO contra o ESPERADO, e o esperado vem de `turnos_processo`.
#
# Todo o cálculo mora AQUI. A tela só pinta o que este endpoint decidiu.
# ═════════════════════════════════════════════════════════════════════════
def _parse_iso_utc(s):
    """ISO 8601 (com ou sem tz) → datetime AWARE em UTC. None se não parsear.
    O Postgres devolve com 'Z' ou offset; o Pi manda com o offset local."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


HEARTBEAT_INTERVALO_MIN = float(os.environ.get("KV_HEARTBEAT_INTERVALO_MIN", "5"))
# "Sem sinal" = último pulso mais velho que N× o intervalo de envio E dentro
# de uma janela do turno. 3× dá folga para um envio perdido sem alarme falso.
SAUDE_STALE_FATOR = float(os.environ.get("KV_SAUDE_STALE_FATOR", "3"))
SAUDE_RETENCAO_DIAS = int(os.environ.get("KV_SAUDE_RETENCAO_DIAS", "7"))
_ULTIMA_LIMPEZA_HB = {"ts": 0.0}


# ═════════════════════════════════════════════════════════════════════
# Fase 74 — A VARREDURA PRECISA DE UM RELÓGIO QUE NÃO DEPENDA DE NINGUÉM.
#
# A expiração da Fase 54 existia como ENDPOINT e nada mais. Ninguém a chamava,
# então em 4 dias o bucket foi de 0 a 979 MB de 1 GB e a campanha quase parou.
# "Existe um endpoint" não é um mecanismo — é uma tarefa manual esperando ser
# esquecida num fim de semana.
#
# O relógio escolhido é o HEARTBEAT DO PI: ele chega a cada poucos minutos,
# 24/7, e não depende de ninguém lembrar. Mesmo padrão já provado em
# `_limpar_heartbeats_antigos`. Render Hobby não tem cron, e uma thread morre
# junto com o processo quando o serviço hiberna — o pulso, não.
#
# Rede secundária: no startup do FastAPI (pega o caso do Pi desligado) e o
# endpoint manual, que continua existindo para uso sob demanda.
# ═════════════════════════════════════════════════════════════════════
VARREDURA_INTERVALO_MIN = int(os.environ.get("KV_VARREDURA_INTERVALO_MIN", "60"))
_ULTIMA_VARREDURA = {"ts": 0.0}


def _varrer_storage_com_throttle(sb, empresa: str | None, motivo: str) -> None:
    """Roda a varredura no máximo 1×/intervalo. NUNCA levanta: se a limpeza
    falhar, o heartbeat (ou o startup) não pode falhar junto."""
    import time as _t
    agora = _t.time()
    if agora - _ULTIMA_VARREDURA["ts"] < VARREDURA_INTERVALO_MIN * 60:
        return
    _ULTIMA_VARREDURA["ts"] = agora
    try:
        r = varrer_videos_expirados(sb, empresa=empresa)
        if r.get("total_objetos"):
            log.info("[varredura/%s] %d objeto(s) · %.1f MB liberados",
                     motivo, r["total_objetos"], r.get("total_mb", 0.0))
    except Exception as e:  # noqa: BLE001
        log.warning("[varredura/%s] falhou (não-fatal): %s", motivo, e)


def _limpar_heartbeats_antigos(sb) -> None:
    """Retenção de 7 dias, com throttle de 1×/hora. Roda no POST porque
    heartbeat é frequente e garantido — não precisa de thread nem de cron."""
    import time as _t
    agora = _t.time()
    if agora - _ULTIMA_LIMPEZA_HB["ts"] < 3600:
        return
    _ULTIMA_LIMPEZA_HB["ts"] = agora
    corte = (datetime.now(timezone.utc) - timedelta(days=SAUDE_RETENCAO_DIAS)).isoformat()
    try:
        sb.table("heartbeats_edge").delete().lt("recebido_em", corte).execute()
    except Exception as e:                        # nunca derruba o POST
        log.warning(f"[saude] limpeza de heartbeats falhou (não-fatal): {e}")


class CamadaDuvidaBody(BaseModel):
    """Fase 57 — camada de dúvida DECLARATIVA (dados, não código)."""
    nome: str = Field(min_length=1, max_length=80)
    quando_rotulo: list[str] = Field(default_factory=lambda: ["*"])
    se: dict = Field(default_factory=dict)
    motivo: str | None = Field(default=None, max_length=400)
    # `sombra` é o DEFAULT de propósito: regra nova entra medindo, não marcando.
    modo: str = "sombra"
    ordem: int = 100


@app.get("/processos/{processo_id}/camadas")
def listar_camadas(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    r = (
        sb.table("camadas_duvida")
        .select("id, nome, quando_rotulo, se, entao, motivo, modo, ordem, criado_em")
        .eq("empresa", user.empresa).eq("processo", nome)
        .order("ordem").execute()
    )
    return r.data or []


@app.put("/processos/{processo_id}/camadas/{nome}")
def salvar_camada(
    processo_id: str, nome: str, body: CamadaDuvidaBody,
    user: CurrentUser = Depends(get_current_user),
):
    """Cria ou atualiza uma camada. Vale no PRÓXIMO processamento — sem deploy."""
    if body.modo not in ("ativa", "sombra", "off"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "modo deve ser 'ativa', 'sombra' ou 'off'.")
    if not body.se:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "a condição 'se' não pode ser vazia.")
    sb = make_supabase_client()
    proc = _processo_nome(sb, user, processo_id)
    linha = {
        "empresa": user.empresa, "processo": proc, "nome": nome,
        "quando_rotulo": body.quando_rotulo or ["*"], "se": body.se,
        "entao": "duvida", "motivo": body.motivo, "modo": body.modo,
        "ordem": body.ordem,
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
    }
    existe = (
        sb.table("camadas_duvida").select("id")
        .eq("empresa", user.empresa).eq("processo", proc).eq("nome", nome)
        .execute().data
    )
    try:
        if existe:
            sb.table("camadas_duvida").update(linha).eq("id", existe[0]["id"]).execute()
        else:
            sb.table("camadas_duvida").insert(linha).execute()
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Falha ao salvar: {e}")
    return {"ok": True, "nome": nome, "modo": body.modo}


@app.delete("/processos/{processo_id}/camadas/{nome}")
def excluir_camada(processo_id: str, nome: str,
                   user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    proc = _processo_nome(sb, user, processo_id)
    sb.table("camadas_duvida").delete().eq("empresa", user.empresa).eq(
        "processo", proc).eq("nome", nome).execute()
    return {"ok": True}


@app.get("/processos/{processo_id}/duvidas")
def fila_de_duvidas(
    processo_id: str,
    rotulo: str | None = Query(None, description="filtra a fila por rótulo"),
    tipo: str | None = Query(None, description="sem_evidencia | discordancia | camada"),
    limite: int = Query(200, ge=1, le=1000),
    user: CurrentUser = Depends(get_current_user),
):
    """B4 — fila da dúvida ORDENADA POR MINUTOS EM JOGO, não por ordem de
    chegada: valida-se primeiro o que mais move o placar.

    Cada item traz o MOTIVO (qual camada disparou, ou a discordância entre as
    amostras) — sem isso o validador está adivinhando junto com a máquina. Os
    frames vêm do cache já aquecido no processamento (Fase 54), sem custo.

    `por_rotulo` mostra a concentração da dúvida por rótulo, e `?rotulo=` filtra
    a fila: é como auditar a suspeita de um rótulo virar depósito da dúvida."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    r = montar_fila_duvidas(sb, user.empresa, nome, rotulo=rotulo, limite=limite,
                            tipo_filtro=tipo)
    if "erro" in r:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, r["erro"])
    return {"ok": True, **r}


@app.get("/processos/{processo_id}/camadas/placar")
def placar_das_camadas(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    """Fase 57 — quantas vezes cada camada disparou, quantos minutos colocou em
    dúvida e a TAXA DE ACERTO (o humano mudou o rótulo × confirmou o original).

    Camada que dispara muito e sempre confirma gera trabalho sem gerar
    informação — e aqui está a evidência para desligá-la."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    r = placar_camadas(sb, user.empresa, nome)
    if "erro" in r:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, r["erro"])
    return {"ok": True, **r}


@app.post("/processos/{processo_id}/manutencao/lean/propagar")
def propagar_lean(
    processo_id: str,
    dry_run: bool = Query(True, description="true (default) = só relatório, não escreve"),
    user: CurrentUser = Depends(get_current_user),
):
    """Fase 55 — backfill idempotente da categoria Lean: comportamento → eventos.

    Escopado por (empresa, processo) — nunca um update global.

    `dry_run=true` (o DEFAULT, de propósito) calcula e devolve o relatório sem
    escrever nada: dá para ver o impacto antes de aplicar.

    Além do que migra, o relatório separa o `cinza_real` — rótulos com tempo
    observado cujo COMPORTAMENTO está sem categoria. Esses não são resolvidos
    por propagação nenhuma; é o que de fato aparece como "não classificado" no
    dashboard, e só sai de lá quando alguém classificar o comportamento."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    rel = relatorio_propagacao_lean(sb, user.empresa, nome, dry_run=dry_run)
    if "erro" in rel:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, rel["erro"])
    return {"ok": True, **rel}


class AprendizadoBody(BaseModel):
    # None = volta ao default do ambiente (KV_APRENDIZADO_AUTO, hoje 'off').
    ativo: bool | None = None


# O que a chave cobre e o que ela deliberadamente NÃO cobre. Fica no payload
# para a tela poder explicar sem que ninguém precise ler o pipeline.
_MECANISMOS_APRENDIZADO = [
    {"nome": "correcao_aprendida", "coberto": True,
     "efeito": "Uma correção sua deixa de remapear automaticamente descrições iguais."},
    {"nome": "vocabulario_canonico", "coberto": True,
     "efeito": "Rótulo já consolidado deixa de ser marcado como aprendido."},
    {"nome": "lean_precedente_humano", "coberto": True,
     "efeito": "Categoria Lean decidida em outro processo deixa de ser aplicada aqui."},
    {"nome": "lean_propagacao_irmaos", "coberto": True,
     "efeito": "Sua decisão Lean vale só no processo onde foi tomada."},
    {"nome": "lean_classificacao_ia", "coberto": False,
     "efeito": "Continua rodando — classificar é o trabalho do sistema, e a saída "
               "sai marcada 'ia', sem se passar por decisão humana."},
    {"nome": "vocabulario_no_prompt", "coberto": False,
     "efeito": "Continua sugerindo nomes já usados ao modelo. Não valida nem "
               "remapeia; é o que impede o mesmo comportamento de ganhar três "
               "nomes diferentes ao longo da campanha."},
]


@app.get("/processos/{processo_id}/aprendizado")
def ler_aprendizado(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    """Fase 62 — estado da generalização automática deste processo.

    Devolve `efetivo` (o que o pipeline vai fazer) e `configurado` (o que está
    gravado; null = herdando o default do ambiente)."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    try:
        r = (
            sb.table("contexto_processo").select("aprendizado_automatico")
            .eq("empresa", user.empresa).eq("processo", nome).limit(1).execute().data
        ) or []
        configurado = r[0].get("aprendizado_automatico") if r else None
    except Exception:
        configurado = None
    return {
        "processo": nome,
        "configurado": configurado,
        "efetivo": aprendizado_automatico(sb, user.empresa, nome),
        "padrao_ambiente": APRENDIZADO_AUTO_PADRAO,
        "mecanismos": _MECANISMOS_APRENDIZADO,
    }


@app.put("/processos/{processo_id}/aprendizado")
def setar_aprendizado(
    processo_id: str,
    body: AprendizadoBody,
    user: CurrentUser = Depends(get_current_user),
):
    """Liga/desliga a generalização automática deste processo.

    `ativo=null` devolve o processo ao default do ambiente. Vale a partir do
    PRÓXIMO vídeo processado — não reescreve nada já gravado."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    try:
        (
            sb.table("contexto_processo")
            .update({"aprendizado_automatico": body.ativo})
            .eq("empresa", user.empresa).eq("processo", nome)
            .execute()
        )
    except Exception as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Falha ao gravar o flag: {e}",
        )
    return {
        "ok": True,
        "processo": nome,
        "configurado": body.ativo,
        "efetivo": aprendizado_automatico(sb, user.empresa, nome),
    }


@app.get("/processos/{processo_id}/manutencao/reprocesso/relatorio")
def relatorio_reprocesso(
    processo_id: str,
    custo_por_min: float = Query(0.02, description="US$ por minuto de vídeo reprocessado"),
    user: CurrentUser = Depends(get_current_user),
):
    """Fase 71 — SÓ LEITURA (GET, de propósito). Ranqueia os vídeos por MINUTOS
    contaminados, não por contagem de eventos: um vídeo com 20 eventos de 5s
    pesa menos que um com 3 de 1 min, e é o minuto que move o placar.

    Responde: quantos vídeos concentram 80% do estrago, quanto custa
    reprocessar só eles, quais NÃO têm correção humana (e portanto podem ser
    reprocessados sem perda) e quais já perderam o binário."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    rel = relatorio_reprocesso_por_video(sb, user.empresa, nome, custo_por_min=custo_por_min)
    if "erro" in rel:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, rel["erro"])
    return {"ok": True, **rel}


@app.post("/processos/{processo_id}/manutencao/validacao/contagio-descricao")
def limpar_contagio_descricao(
    processo_id: str,
    dry_run: bool = Query(True, description="true (default) = só relatório, não escreve"),
    user: CurrentUser = Depends(get_current_user),
):
    """Fase 67 — acha (e desfaz) os eventos que herdaram um rótulo por
    CASAMENTO DE DESCRIÇÃO com uma correção humana.

    `dry_run=true` é o default: devolve o volume por descrição antes de tocar
    em nada. As correções do próprio humano (`origem_validacao='humano'`) nunca
    são alteradas — só param de se propagar.

    O rótulo ORIGINAL não volta: ele nunca foi gravado. Os eventos afetados
    voltam para a fila para serem julgados de novo."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    rel = diagnosticar_contagio_por_descricao(sb, user.empresa, nome, dry_run=dry_run)
    if "erro" in rel:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, rel["erro"])
    return {"ok": True, **rel}


@app.post("/processos/{processo_id}/manutencao/validacao/reverter-auto")
def reverter_auto_validacao(
    processo_id: str,
    origens: str = Query(
        "correcao_aprendida,vocabulario_canonico",
        description="lista separada por vírgula. 'humano', 'auditoria' e 'posto_vazio' são recusadas.",
    ),
    dry_run: bool = Query(True, description="true (default) = só relatório, não escreve"),
    user: CurrentUser = Depends(get_current_user),
):
    """Fase 61 — devolve à fila o que a MÁQUINA marcou como validado.

    Limpa `validado_humano`/`validacao_correto`/`validado_em` dos eventos com a
    origem indicada, preservando `origem_validacao` (que passa a significar
    "rótulo proposto por", e é o que ajuda quem vai julgar na fila).

    Protegidas e recusadas: `humano` (decisão da pessoa é inviolável),
    `auditoria` e `posto_vazio` (secundários/determinísticos que dependem de
    `validado_humano=True` justamente para NÃO entrar na fila).

    IDEMPOTENTE. `dry_run=true` é o default de propósito."""
    lista = tuple(o.strip() for o in (origens or "").split(",") if o.strip())
    if not lista:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "informe ao menos uma origem.")
    protegidas = {"humano", "auditoria", "posto_vazio"} & set(lista)
    if protegidas:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"origem protegida: {', '.join(sorted(protegidas))}.",
        )
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    rel = reverter_auto_validacao_maquina(sb, user.empresa, nome, origens=lista, dry_run=dry_run)
    if "erro" in rel:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, rel["erro"])
    return {"ok": True, **rel}


@app.post("/manutencao/videos/expirar")
def expirar_videos(
    todos: bool = Query(False, description="varre a empresa inteira (default: sim)"),
    dry_run: bool = Query(False, description="true = só mede, não apaga"),
    user: CurrentUser = Depends(get_current_user),
):
    """Fase 54 — varredura de segurança: acha vídeos JÁ processados, com frames
    aquecidos e binário ainda no Storage, e os apaga.

    É a rede contra o pipeline falhar em silêncio no meio da campanha de 30
    dias. IDEMPOTENTE: rodar duas vezes seguidas não apaga nada na segunda
    (quem já foi apagado tem `video_removido_em` preenchido e sai do filtro).

    ⚠️ Opera SOMENTE sobre `videos.caminho` — nunca lista o bucket por prefixo.
    Os JPEGs de `__frames/` moram no mesmo bucket e são a evidência permanente:
    sem o vídeo de origem, não há como regenerá-los."""
    sb = make_supabase_client()
    r = varrer_videos_expirados(sb, empresa=None if todos else user.empresa,
                                dry_run=dry_run)
    return {"ok": True, **r}


@app.post("/edge/heartbeat")
def receber_heartbeat(body: HeartbeatBody, user: CurrentUser = Depends(get_current_user)):
    """Recebe o pulso do Pi. Autenticado como o runner já se autentica hoje."""
    sb = make_supabase_client()
    processo_nome = _processo_nome(sb, user, body.processo_id)
    linha = {
        "empresa": user.empresa,
        "processo": processo_nome,
        "device_id": body.device_id.strip(),
        "runner_versao": body.runner_versao,
        "estado": body.estado.strip().lower(),
        "cameras": [c.model_dump() for c in body.cameras],
        "disco_livre_gb": body.disco_livre_gb,
        "disco_uso_pct": body.disco_uso_pct,
        "cpu_temp_c": body.cpu_temp_c,
        "uptime_s": body.uptime_s,
        "turno_janela": body.turno_janela,
        "turno_deadline": body.turno_deadline,
    }
    try:
        sb.table("heartbeats_edge").insert(linha).execute()
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"Falha ao gravar heartbeat: {e}")
    _limpar_heartbeats_antigos(sb)
    # Fase 74: o pulso do Pi é o relógio da varredura. Throttled, não-fatal.
    _varrer_storage_com_throttle(sb, user.empresa, "heartbeat")
    return {"ok": True}


# ═════════════════════════════════════════════════════════════════════════
# Fase 65 — O RELÓGIO DE PAREDE É O DA FÁBRICA, NÃO O DO SERVIDOR.
#
# O painel de saúde usava `datetime.now().astimezone()` — o fuso do SERVIDOR.
# No Render o container roda em UTC, e a fábrica está em UTC−3. Resultado:
#   • a faixa de 24h aparecia 3h deslocada (parecia ter começado às 03h
#     quando a gravação começou às 06h);
#   • pior, o turno era comparado contra o relógio errado: às 11h da fábrica
#     (14h UTC) o painel dizia "em repouso" com o Pi gravando. Um painel de
#     saúde que erra o estado é pior que não ter painel.
#
# O Pi decide o turno pelo relógio DELE (TURNO_JANELAS roda local no edge).
# Para o painel concordar com a realidade, o backend tem de usar o mesmo
# relógio — o da fábrica.
# ═════════════════════════════════════════════════════════════════════════
FUSO_PADRAO = os.environ.get("KV_TZ", "America/Sao_Paulo")
# Fallback se a base de fusos não existir na imagem (slim sem tzdata): offset
# fixo. Perde horário de verão, mas errar por 1h no verão é muito melhor que
# errar por 3h o ano inteiro — e o log deixa o motivo visível.
_FUSO_FALLBACK = timezone(timedelta(hours=-3), "UTC-3")


def _fuso(nome: str | None):
    """IANA → tzinfo. Nome inválido/base ausente nunca derruba o painel."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(nome or FUSO_PADRAO)
    except Exception as e:  # noqa: BLE001
        log.warning("[saude] fuso %r indisponível (%s) — usando UTC-3 fixo.",
                    nome or FUSO_PADRAO, e)
        return _FUSO_FALLBACK


def fuso_do_processo(sb, empresa: str, processo: str):
    """Fuso da FÁBRICA: coluna do processo → env KV_TZ → America/Sao_Paulo."""
    nome = None
    try:
        r = (
            sb.table("contexto_processo").select("fuso_horario")
            .eq("empresa", empresa).eq("processo", processo).limit(1).execute().data
        ) or []
        if r:
            nome = (r[0].get("fuso_horario") or "").strip() or None
    except Exception as e:  # noqa: BLE001
        log.warning("[saude] fuso do processo não lido (%s) — usando o padrão.", e)
    return _fuso(nome), (nome or FUSO_PADRAO)


def _turno_janelas_do_dia(turnos: list, quando: datetime) -> list:
    """[(inicio_dt, fim_dt, nome)] das janelas ATIVAS no dia de `quando`.

    ⚠️ `quando` TEM de estar no fuso da fábrica: as janelas são horário de
    parede ("06:00") e `replace(hour=...)` as ancora no fuso que vier.
    Um turno vale no dia se `dias_semana` contém o ISO weekday."""
    dow = quando.isoweekday()
    janelas = []
    for t in turnos:
        if not t.get("ativo", True):
            continue
        if dow not in (t.get("dias_semana") or []):
            continue
        for iv in (t.get("intervalos") or []):
            try:
                hi, mi = [int(x) for x in str(iv["inicio"]).split(":")[:2]]
                hf, mf = [int(x) for x in str(iv["fim"]).split(":")[:2]]
            except Exception:
                continue
            ini = quando.replace(hour=hi, minute=mi, second=0, microsecond=0)
            fim = quando.replace(hour=hf, minute=mf, second=0, microsecond=0)
            if fim <= ini:            # intervalo inválido/cruzando meia-noite: ignora
                continue
            janelas.append((ini, fim, t.get("nome") or "Turno"))
    janelas.sort(key=lambda j: j[0])
    return janelas


def _dentro_de_janela(janelas: list, quando: datetime):
    for ini, fim, nome in janelas:
        if ini <= quando < fim:
            return (ini, fim, nome)
    return None


@app.get("/processos/{processo_id}/saude")
def saude_edge(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    """Estado JÁ INTERPRETADO da borda — o frontend não recalcula nada."""
    sb = make_supabase_client()
    processo_nome = _processo_nome(sb, user, processo_id)
    agora = datetime.now(timezone.utc)
    desde = (agora - timedelta(hours=24)).isoformat()

    try:
        hbs = (
            sb.table("heartbeats_edge")
            .select("device_id, runner_versao, estado, cameras, disco_livre_gb, "
                    "disco_uso_pct, cpu_temp_c, uptime_s, turno_janela, "
                    "turno_deadline, recebido_em")
            .eq("empresa", user.empresa)
            .eq("processo", processo_nome)
            .gte("recebido_em", desde)
            .order("recebido_em", desc=True)
            .limit(2000)
            .execute()
            .data
        ) or []
    except Exception as e:
        log.warning(f"[saude] leitura de heartbeats falhou: {e}")
        hbs = []

    try:
        turnos = (
            sb.table("turnos_processo")
            .select("nome, intervalos, dias_semana, ativo")
            .eq("empresa", user.empresa)
            .eq("processo", processo_nome)
            .execute()
            .data
        ) or []
    except Exception:
        turnos = []

    # Fase 65: o relógio de parede é o DA FÁBRICA. O Pi decide o turno pelo
    # fuso dele; o painel tem de usar o mesmo, senão diverge do que está
    # realmente acontecendo no chão. Nunca o fuso do servidor (UTC no Render).
    tz_fabrica, tz_nome = fuso_do_processo(sb, user.empresa, processo_nome)
    local = agora.astimezone(tz_fabrica)
    janelas = _turno_janelas_do_dia(turnos, local)
    ativa = _dentro_de_janela(janelas, local)
    stale_s = HEARTBEAT_INTERVALO_MIN * 60 * SAUDE_STALE_FATOR

    ultimo = hbs[0] if hbs else None
    ultimo_em = _parse_iso_utc(ultimo.get("recebido_em")) if ultimo else None
    idade_s = (agora - ultimo_em).total_seconds() if ultimo_em else None

    # ── Estado geral: observado × esperado ──
    # `sem_captura` só existe porque o Pi manda pulso 24/7 (--heartbeat): sem
    # essa distinção, um ffmpeg morto DENTRO do turno continuaria mostrando
    # "Capturando agora" — falsamente tranquilizador, pior que não ter painel.
    estado_runner_ult = (ultimo or {}).get("estado")
    if ultimo is None:
        estado, desde_ts = "sem_dados", None
    elif ativa and (idade_s is None or idade_s > stale_s):
        estado, desde_ts = "sem_sinal", ultimo_em
    elif ativa and estado_runner_ult not in ("capturando", "processando"):
        estado, desde_ts = "sem_captura", ultimo_em
    elif ativa:
        estado, desde_ts = "capturando", ultimo_em
    else:
        estado, desde_ts = "em_repouso", ultimo_em

    # ── Câmeras: mesmo tratamento, uma a uma ──
    cams_out = []
    vistas = {}
    for hb in hbs:                     # do mais recente para o mais antigo
        for c in (hb.get("cameras") or []):
            cid = c.get("cam_id")
            if cid and cid not in vistas:
                vistas[cid] = (c, hb.get("recebido_em"))
    for cid, (c, quando) in sorted(vistas.items()):
        ult = c.get("ultimo_segmento_em")
        ult_dt = _parse_iso_utc(ult)
        idade_cam = (agora - ult_dt).total_seconds() if ult_dt else None
        if estado == "sem_dados":
            cam_estado = "sem_dados"
        elif not ativa:
            cam_estado = "em_repouso"
        elif estado == "sem_sinal":
            # O Pi parou de reportar: `gravando` deste payload é uma afirmação
            # VELHA e não vale nada. Sem informação fresca, nenhuma câmera pode
            # ser dada como saudável.
            cam_estado = "sem_sinal"
        # Pulso fresco: a IDADE DO SEGMENTO é o sinal primário (um stream
        # congelado para de atualizar o mtime) e `gravando` (bytes crescendo)
        # corrobora. OR, não AND: um pulso perdido não pode virar alarme falso.
        elif (idade_cam is not None and idade_cam <= stale_s) or c.get("gravando"):
            cam_estado = "capturando"
        else:
            cam_estado = "sem_sinal"
        cams_out.append({
            "cam_id": cid,
            "nome": c.get("nome") or cid,
            "estado": cam_estado,
            "gravando": bool(c.get("gravando")),
            "ultimo_segmento_em": ult,
            "falhas": c.get("falhas") or 0,
            "visto_em": quando,
        })

    # ── Disco: GB livres + projeção de dias no ritmo atual ──
    disco = None
    if ultimo and ultimo.get("disco_livre_gb") is not None:
        livre = float(ultimo["disco_livre_gb"])
        dias_rest = None
        # Ritmo real medido entre o pulso mais antigo (24h) e o mais novo. Se o
        # disco NÃO está caindo (a limpeza dá conta), não inventamos projeção.
        antigos = [h for h in hbs if h.get("disco_livre_gb") is not None]
        if len(antigos) >= 2:
            velho = antigos[-1]
            t0 = _parse_iso_utc(velho.get("recebido_em"))
            if t0 and ultimo_em:
                horas = (ultimo_em - t0).total_seconds() / 3600.0
                queda = float(velho["disco_livre_gb"]) - livre
                if horas >= 1 and queda > 0.05:
                    dias_rest = round(livre / (queda / horas * 24), 1)
        disco = {
            "livre_gb": round(livre, 1),
            "uso_pct": (round(float(ultimo["disco_uso_pct"]), 1)
                        if ultimo.get("disco_uso_pct") is not None else None),
            "dias_restantes": dias_rest,
        }

    # ── Turno de hoje: janelas + qual está ativa + falta quanto p/ a próxima ──
    proxima = next((j for j in janelas if j[0] > local), None)
    turno_out = {
        "janelas": [{"inicio": i.strftime("%H:%M"), "fim": f.strftime("%H:%M"),
                     "nome": n, "ativa": bool(ativa and ativa[0] == i)}
                    for i, f, n in janelas],
        "ativa": ({"inicio": ativa[0].strftime("%H:%M"), "fim": ativa[1].strftime("%H:%M"),
                   "nome": ativa[2]} if ativa else None),
        "proxima": ({"inicio": proxima[0].strftime("%H:%M"),
                     "fim": proxima[1].strftime("%H:%M"),
                     "em_min": int((proxima[0] - local).total_seconds() // 60)}
                    if proxima else None),
        "configurado": bool(janelas) or bool(turnos),
    }

    # ── Cobertura das últimas 24h em blocos de 15 min ──
    # `esperado` vem do turno (o fundo da faixa); `houve` vem dos pulsos (o
    # preenchimento). Buraco DENTRO do esperado = falha visível.
    BLOCO_MIN = 15
    n_blocos = (24 * 60) // BLOCO_MIN
    # Ancora no FIM (agora, arredondado para CIMA no bloco) e volta 24h. Ancorar
    # no início e arredondar para baixo deixava o bloco de AGORA fora do array —
    # o pulso mais recente não aparecia na faixa, que é justamente o que o
    # cliente olha primeiro.
    fim_faixa = local.replace(second=0, microsecond=0)
    resto = fim_faixa.minute % BLOCO_MIN
    fim_faixa += timedelta(minutes=(BLOCO_MIN - resto) if resto else BLOCO_MIN)
    ini_faixa = fim_faixa - timedelta(minutes=n_blocos * BLOCO_MIN)
    marcados = set()
    for h in hbs:
        t = _parse_iso_utc(h.get("recebido_em"))
        if not t:
            continue
        delta = (t.astimezone(tz_fabrica) - ini_faixa).total_seconds()
        if delta >= 0:
            idx = int(delta // (BLOCO_MIN * 60))
            if 0 <= idx < n_blocos:
                marcados.add(idx)
    cobertura = []
    for i in range(n_blocos):
        t_ini = ini_faixa + timedelta(minutes=i * BLOCO_MIN)
        jan_dia = _turno_janelas_do_dia(turnos, t_ini)
        cobertura.append({
            "inicio": t_ini.isoformat(),
            "esperado": bool(_dentro_de_janela(jan_dia, t_ini)),
            "houve": i in marcados,
        })

    return {
        "estado": estado,
        "desde": desde_ts.isoformat() if desde_ts else None,
        "ultimo_heartbeat_em": ultimo.get("recebido_em") if ultimo else None,
        "idade_s": int(idade_s) if idade_s is not None else None,
        "device_id": ultimo.get("device_id") if ultimo else None,
        "runner_versao": ultimo.get("runner_versao") if ultimo else None,
        "estado_runner": ultimo.get("estado") if ultimo else None,
        "cpu_temp_c": ultimo.get("cpu_temp_c") if ultimo else None,
        "uptime_s": ultimo.get("uptime_s") if ultimo else None,
        "cameras": cams_out,
        "disco": disco,
        "turno": turno_out,
        "cobertura_24h": cobertura,
        "intervalo_min": HEARTBEAT_INTERVALO_MIN,
        # Fuso usado para TUDO acima. Vai no payload de propósito: fuso errado
        # é um erro silencioso — o painel continua bonito e mente o dia
        # inteiro. Visível na tela, alguém percebe no primeiro olhar.
        "fuso": tz_nome,
        "agora_local": local.strftime("%H:%M"),
    }


# ═════════════════════════════════════════════════════════════════════════
# ZONAS POR CÂMERA (Fase 28) — onde o operador titular trabalha.
# Consumidas pelo pipeline (filtro de pessoas) e baixadas pelo edge (score).
# ═════════════════════════════════════════════════════════════════════════
_ZONA_CAMPOS = (
    "id, cam_id, nome, papel, pts_rel, descricao_contexto, "
    "frame_ref_w, frame_ref_h, ativo, criado_em, atualizado_em"
)


def _validar_zona(sb, user: CurrentUser, nome_proc: str, body: ZonaBody,
                  zona_id: str | None = None) -> None:
    """Valida papel/pontos e a unicidade de posto_operador ativa por câmera."""
    if body.papel not in PAPEIS_ZONA:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"papel deve ser um de: {', '.join(PAPEIS_ZONA)}.",
        )
    for pt in body.pts_rel:
        if len(pt) != 2 or not all(0.0 <= v <= 1.0 for v in pt):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Cada ponto de pts_rel deve ser [x, y] com valores em [0, 1].",
            )
    if body.papel == "posto_operador" and body.ativo:
        q = (
            sb.table("zonas_camera")
            .select("id")
            .eq("empresa", user.empresa)
            .eq("processo", nome_proc)
            .eq("cam_id", body.cam_id.strip())
            .eq("papel", "posto_operador")
            .eq("ativo", True)
        )
        if zona_id:
            q = q.neq("id", zona_id)
        if q.limit(1).execute().data:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Já existe uma zona 'posto do operador' ativa nesta câmera. "
                "Desative ou edite a existente — o posto tem um único titular.",
            )


@app.get("/processos/{processo_id}/zonas")
def listar_zonas(
    processo_id: str,
    cam_id: str | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    q = (
        sb.table("zonas_camera")
        .select(_ZONA_CAMPOS)
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .order("criado_em", desc=False)
    )
    if cam_id:
        q = q.eq("cam_id", cam_id)
    return q.execute().data or []


@app.post("/processos/{processo_id}/zonas")
def criar_zona(
    processo_id: str,
    body: ZonaBody,
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    _validar_zona(sb, user, nome, body)
    r = (
        sb.table("zonas_camera")
        .insert(
            {
                "empresa": user.empresa,
                "processo": nome,
                "cam_id": body.cam_id.strip(),
                "nome": body.nome.strip(),
                "papel": body.papel,
                "pts_rel": body.pts_rel,
                "descricao_contexto": (body.descricao_contexto or "").strip() or None,
                "frame_ref_w": body.frame_ref_w,
                "frame_ref_h": body.frame_ref_h,
                "ativo": bool(body.ativo),
            }
        )
        .execute()
    )
    return r.data[0]


def _carregar_zona_propria(sb, user: CurrentUser, zona_id: str) -> dict:
    r = (
        sb.table("zonas_camera")
        .select("id, empresa, processo")
        .eq("id", zona_id)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zona não encontrada")
    z = r.data[0]
    if z["empresa"] != user.empresa:
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    return z


@app.put("/zonas/{zona_id}")
def atualizar_zona(
    zona_id: str,
    body: ZonaBody,
    user: CurrentUser = Depends(get_current_user),
):
    from datetime import datetime

    sb = make_supabase_client()
    z = _carregar_zona_propria(sb, user, zona_id)
    _validar_zona(sb, user, z["processo"], body, zona_id=zona_id)
    r = (
        sb.table("zonas_camera")
        .update(
            {
                "cam_id": body.cam_id.strip(),
                "nome": body.nome.strip(),
                "papel": body.papel,
                "pts_rel": body.pts_rel,
                "descricao_contexto": (body.descricao_contexto or "").strip() or None,
                "frame_ref_w": body.frame_ref_w,
                "frame_ref_h": body.frame_ref_h,
                "ativo": bool(body.ativo),
                "atualizado_em": datetime.utcnow().isoformat(),
            }
        )
        .eq("id", zona_id)
        .execute()
    )
    return (r.data or [{}])[0]


@app.delete("/zonas/{zona_id}")
def excluir_zona(zona_id: str, user: CurrentUser = Depends(get_current_user)):
    sb = make_supabase_client()
    _carregar_zona_propria(sb, user, zona_id)
    sb.table("zonas_camera").delete().eq("id", zona_id).execute()
    return {"ok": True}


@app.get("/processos/{processo_id}/cameras")
def listar_cameras(processo_id: str, user: CurrentUser = Depends(get_current_user)):
    """cam_ids distintos vistos neste processo (videos ∪ segmentos)."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    cams: set[str] = set()
    for tbl in ("videos", "segmentos"):
        try:
            r = (
                sb.table(tbl)
                .select("cam_id")
                .eq("empresa", user.empresa)
                .eq("processo", nome)
                .not_.is_("cam_id", "null")
                .limit(2000)
                .execute()
            )
            cams.update(row["cam_id"] for row in (r.data or []) if row.get("cam_id"))
        except Exception as e:
            log.warning(f"listar_cameras: falha em {tbl}: {e}")
    return {"cameras": sorted(cams)}


# Fase 48.2: URL assinada (absoluta) de um JPEG já no Storage, para o NAVEGADOR
# buscar o frame DIRETO do Supabase em vez de o backend baixar e reenviar em
# base64 — tira o egress dos frames do Render. None se falhar (o chamador cai
# pro base64, garantindo que nunca quebra). TTL longo (6h) porque o front
# re-consulta a query e frames de monitoria não são sensíveis.
_FRAME_URL_TTL = 21600


def _url_frame_assinada(sb, bucket: str, key: str, ttl: int = _FRAME_URL_TTL) -> str | None:
    try:
        res = sb.storage.from_(bucket).create_signed_url(key, ttl)
    except Exception:
        return None
    url = None
    if isinstance(res, dict):
        url = res.get("signedURL") or res.get("signedUrl") or res.get("signed_url") or res.get("url")
    elif isinstance(res, str):
        url = res
    if not url:
        return None
    if url.startswith("http"):
        return url
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if url.startswith("/storage/v1"):
        return f"{base}{url}"
    return f"{base}/storage/v1{url if url.startswith('/') else '/' + url}"


@app.get("/processos/{processo_id}/cameras/{cam_id}/frame-referencia")
def frame_referencia(
    processo_id: str,
    cam_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Um frame real da câmera (vídeo mais recente) para desenhar zonas em cima.
    Cache em Storage ao lado do vídeo (padrão de frames_evento)."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    v = (
        sb.table("videos")
        .select("id, caminho, nome, gravado_em, processado_em")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .eq("cam_id", cam_id)
        .order("processado_em", desc=True)
        .limit(1)
        .execute()
        .data
    )
    video: dict | None = v[0] if v else None
    # Fallback (Fase 28.1 fix): no dual-angle o PAR vira UM vídeo registrado
    # com o cam_id PRIMÁRIO (cam1) — a cam2 nunca ganha linha em `videos`.
    # O arquivo dela vive na inbox `segmentos`; usamos o mais recente.
    if video is None or not video.get("caminho"):
        s = (
            sb.table("segmentos")
            .select("id, storage_path, nome, gravado_em, recebido_em")
            .eq("empresa", user.empresa)
            .eq("processo", nome)
            .eq("cam_id", cam_id)
            .order("recebido_em", desc=True)
            .limit(1)
            .execute()
            .data
        )
        if s and s[0].get("storage_path"):
            video = {
                "id": s[0]["id"],
                "caminho": s[0]["storage_path"],
                "nome": s[0].get("nome"),
                "gravado_em": s[0].get("gravado_em"),
            }
    if video is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Esta câmera ainda não tem vídeo processado. Suba ao menos 1 "
            "segmento dela para desenhar as zonas sobre um frame real.",
        )
    caminho = video.get("caminho")
    if not caminho or caminho.startswith(("/", "\\")) or (len(caminho) > 1 and caminho[1] == ":"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "O vídeo mais recente desta câmera não tem storage path válido. "
            "Processe um segmento novo e tente de novo.",
        )

    import base64
    import posixpath
    import tempfile
    from pathlib import Path

    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")
    ref_key = f"{posixpath.dirname(caminho)}/__frames/ref_{cam_id}_{video['id']}.jpg"

    jpeg: bytes | None = None
    try:
        jpeg = sb.storage.from_(bucket).download(ref_key) or None
    except Exception:
        jpeg = None
    frame_no_storage = jpeg is not None   # Fase 48.2: cache hit = já está no Storage

    largura = altura = None
    if jpeg is None:
        # Miss: streaming pro disco (RAM ≈ 1 chunk) → frame do MEIO via cv2.
        import cv2
        import httpx
        from urllib.parse import quote

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=Path(caminho).suffix or ".mp4")
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
            cap = cv2.VideoCapture(tmp.name)
            try:
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if total > 1:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
                ok, frame = cap.read()
            finally:
                cap.release()
            if not ok or frame is None:
                raise RuntimeError("não foi possível ler um frame do vídeo")
            altura, largura = frame.shape[:2]
            ok2, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if not ok2:
                raise RuntimeError("falha ao codificar JPEG")
            jpeg = buf.tobytes()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, f"Falha ao extrair frame: {e}"
            )
        finally:
            try:
                Path(tmp.name).unlink()
            except Exception:
                pass
        try:  # cache (não-fatal)
            sb.storage.from_(bucket).upload(
                ref_key, jpeg, {"content-type": "image/jpeg", "upsert": "true"}
            )
            frame_no_storage = True
        except Exception as e:
            log.warning(f"cache do frame de referência falhou (não-fatal): {e}")

    if largura is None:
        # Veio do cache: decodifica só as dimensões (barato, imagem pequena).
        try:
            import cv2
            import numpy as np

            img = cv2.imdecode(np.frombuffer(jpeg, dtype="uint8"), cv2.IMREAD_COLOR)
            if img is not None:
                altura, largura = img.shape[:2]
        except Exception:
            pass

    # Fase 48.2: se o JPEG está no Storage, serve por URL assinada (navegador
    # busca direto do Supabase, sem egress do Render). Senão, base64 (nunca quebra).
    img_url = _url_frame_assinada(sb, bucket, ref_key) if frame_no_storage else None
    return {
        "img": img_url or ("data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")),
        "largura": largura,
        "altura": altura,
        "video_nome": video.get("nome"),
        "gravado_em": video.get("gravado_em"),
    }


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
    # Fase 22 — auditoria da seleção top-K do edge (opcionais): score 0-100 da
    # pontuação de atividade e o motivo da subida ('topk'|'calibracao'|'retry').
    score: float | None = Form(default=None),
    selecao: str | None = Form(default=None),
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
        # Fase 22 — auditoria da seleção top-K (colunas novas; só quando o edge
        # manda; requer `alter table segmentos add column ...` — ver SCHEMA).
        if score is not None:
            linha_seg["score"] = score
        if selecao:
            linha_seg["selecao"] = selecao
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


# ═════════════════════════════════════════════════════════════════════════
# Fase 32 — modo teste: upload de segmento DIRETO do navegador ao Storage.
# Os bytes NÃO passam pelo backend/proxy (que corta uploads longos): o front
# pede uma URL ASSINADA, sobe direto ao Supabase e depois só REGISTRA aqui
# (JSON de milissegundos). Mesma inbox/idempotência do caminho do edge.
# ═════════════════════════════════════════════════════════════════════════
def _segmento_ja_existe(sb, user: CurrentUser, processo_nome: str, cam_id: str, nome: str) -> bool:
    try:
        ja = (
            sb.table("segmentos")
            .select("id")
            .eq("empresa", user.empresa)
            .eq("processo", processo_nome)
            .eq("cam_id", cam_id)
            .eq("nome", nome)
            .limit(1)
            .execute()
            .data
        ) or []
        return bool(ja)
    except Exception:
        return False


@app.post("/processos/{processo_id}/segmentos/upload-url")
def segmento_upload_url(
    processo_id: str,
    body: SegmentoUploadUrlBody,
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    processo_nome = _processo_nome(sb, user, processo_id)
    if _segmento_ja_existe(sb, user, processo_nome, body.cam_id.strip(), body.nome):
        return {"ok": True, "status": "duplicado"}

    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")
    nome_orig = body.nome
    if "." in nome_orig:
        base_nome, _, ext_nome = nome_orig.rpartition(".")
    else:
        base_nome, ext_nome = nome_orig, "mp4"
    arquivo = f"{uuid.uuid4()}_{_slug_storage(base_nome, 'video')}.{_slug_storage(ext_nome, 'mp4')}"
    storage_path = f"{_slug_storage(user.empresa, 'empresa')}/{_slug_storage(processo_nome, 'processo')}/{arquivo}"
    try:
        assinado = sb.storage.from_(bucket).create_signed_upload_url(storage_path)
    except Exception as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"Falha ao criar URL assinada: {e}"
        )
    token = (assinado or {}).get("token")
    if not token:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Storage não devolveu token de upload assinado.",
        )
    return {
        "ok": True,
        "status": "novo",
        "bucket": bucket,
        "storage_path": storage_path,
        "token": token,
    }


@app.post("/processos/{processo_id}/segmentos/registrar")
def segmento_registrar(
    processo_id: str,
    body: SegmentoRegistrarBody,
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    processo_nome = _processo_nome(sb, user, processo_id)
    # O caminho tem que estar no prefixo da PRÓPRIA empresa (nunca registrar
    # objeto de outro tenant).
    prefixo = f"{_slug_storage(user.empresa, 'empresa')}/"
    if not body.storage_path.startswith(prefixo):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "storage_path fora do escopo da empresa.")
    if _segmento_ja_existe(sb, user, processo_nome, body.cam_id.strip(), body.nome):
        return {"ok": True, "modo": "lote", "status": "duplicado"}
    # Confere que o objeto chegou mesmo no Storage (URL de leitura falha se não).
    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")
    try:
        sb.storage.from_(bucket).create_signed_url(body.storage_path, 60)
    except Exception:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "O arquivo não foi encontrado no Storage — o upload direto falhou ou não terminou.",
        )
    linha_seg: dict = {
        "empresa": user.empresa,
        "processo": processo_nome,
        "storage_path": body.storage_path,
        "nome": body.nome,
        "cam_id": body.cam_id.strip(),
        "status": "pendente",
    }
    gravado_em_efetivo = _parse_gravado_em_nome(body.nome)
    if gravado_em_efetivo:
        linha_seg["gravado_em"] = gravado_em_efetivo
    if body.score is not None:
        linha_seg["score"] = body.score
    if body.selecao:
        linha_seg["selecao"] = body.selecao
    try:
        sb.table("segmentos").insert(linha_seg).execute()
    except Exception as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"Falha ao registrar segmento: {e}"
        )
    return {"ok": True, "modo": "lote", "status": "pendente"}


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
        # Fase 61: "pendente" vem PRIMEIRO. Desde que `correcao_aprendida` virou
        # só proposta (sem validado_humano), um evento com essa origem continua
        # pendente — contá-lo como "auto" mostraria trabalho já feito que não foi.
        if not e.get("validado_humano"):
            origens["pendente"] += 1
        elif e.get("origem_validacao") == "humano":
            origens["humano"] += 1
        else:
            origens["auto"] += 1

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

    # Composição de valor agregada (% sobre o tempo total observado).
    # Fase 63: DUAS fatias, sempre — produtivo × não-produtivo. Não existe
    # "não classificado". Onde falta evidência, vale a convenção Lean (o ônus
    # da prova é de quem afirma que agrega valor) e o trecho vai para a fila
    # de dúvidas em vez de virar uma fatia cinza que ninguém reclama.
    soma_por_cat = {"valor_agregado": 0.0, "desperdicio": 0.0}
    _sem_evid_s = 0.0
    for d in dist_enriquecida:
        soma_por_cat[categoria_efetiva(d.get("categoria_lean"))] += d.get("tempo_total_s", 0)
        if not categoria_tem_evidencia(d.get("categoria_lean"), d.get("categoria_lean_origem")):
            _sem_evid_s += d.get("tempo_total_s", 0)
    composicao_valor = {
        f"{k}_pct": round(v / total_tempo * 100, 1) for k, v in soma_por_cat.items()
    }
    composicao_valor["tempo_total_s"] = round(total_tempo, 1)
    composicao_valor["por_categoria_s"] = {k: round(v, 1) for k, v in soma_por_cat.items()}
    # Quanto do tempo já classificado foi ASSUMIDO em vez de decidido. Não é
    # uma fatia — é a medida honesta de quanto ainda falta julgar, e é o mesmo
    # tempo que aparece na fila de dúvidas.
    composicao_valor["sem_evidencia_pct"] = round(_sem_evid_s / total_tempo * 100, 1)
    composicao_valor["sem_evidencia_s"] = round(_sem_evid_s, 1)
    # Fase 56: `posto_vazio` (operador AUSENTE) segue contando DENTRO de
    # desperdicio_pct — o número do card não muda —, mas vai separado aqui para
    # a tela poder mostrar a fatia e dizer "dos quais X pts são posto vazio".
    # Sem isso, "operador ausente" e "operador presente sem agregar valor" —
    # problemas com causas e ações completamente diferentes — viram um número só.
    _vazio_s = sum(d.get("tempo_total_s", 0) for d in dist_enriquecida
                   if d.get("comportamento") == "posto_vazio")
    composicao_valor["posto_vazio_pct"] = round(_vazio_s / total_tempo * 100, 1)
    composicao_valor["posto_vazio_s"] = round(_vazio_s, 1)

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
# Fase 63: a implementação vive em pipeline.py — a fila de dúvidas também
# precisa do offset, e duplicar a regra de sincronismo entre os dois
# arquivos é como os dois ângulos acabariam mostrando instantes
# diferentes. Aqui fica só o alias.
_offset_video_segmento = offset_video_segmento


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
            "label_corrigido, origem_validacao, frame_inicio, frame_fim, bbox_inicio, pessoa_track_id, principal, papel_pessoa"
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
                .select("id, cam_id, gravado_em, nome")
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
                    .select("id, video_id, cam_id, gravado_em, nome")
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
                        i["segundo_angulo"] = {
                            "segmento_id": pares[0]["id"],
                            "cam_id": pares[0].get("cam_id"),
                            # Fase 30: offset real de relógio cam1→cam2
                            "offset_s": _offset_video_segmento(
                                meta_video.get(i.get("video_id"), {}), pares[0]
                            ),
                        }
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
    # Fase 70: vem ANTES de "descartado" — os dois têm validacao_correto=False,
    # mas dizem coisas diferentes e só um deles mede alucinação do VLM.
    if ev.get("descricao_invalida"):
        return "descricao_invalida"
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
        "descricao_invalida, "
        "categoria_lean, categoria_lean_origem, papel_pessoa"
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
        # Fase 70: `descricao_invalida` também tem validacao_correto=false —
        # excluí-lo aqui é o que mantém os dois estados distinguíveis na tela.
        q = q.eq("validacao_correto", False).eq("descricao_invalida", False)
    elif status_filter == "descricao_invalida":
        q = q.eq("descricao_invalida", True)
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
            .select("id, nome, cam_id, gravado_em")
            .in_("id", list(vids))
            .execute()
        )
        nomes = {v["id"]: v.get("nome", "") for v in (rv.data or [])}
        cams = {v["id"]: v.get("cam_id") for v in (rv.data or [])}
        meta_video_tab = {v["id"]: v for v in (rv.data or [])}
    else:
        meta_video_tab = {}

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

    # Fase 29: 2º ângulo (par cam2 do mesmo vídeo, clock-aligned) para a linha
    # expandida mostrar as duas câmeras lado a lado. Mesmo lookup dos pendentes
    # (listar_eventos): segmento concluído do MESMO video_id com OUTRA câmera.
    try:
        if vids:
            rs = (
                sb.table("segmentos")
                .select("id, video_id, cam_id, gravado_em, nome")
                .eq("empresa", user.empresa)
                .in_("video_id", list(vids))
                .eq("status", "concluido")
                .execute()
                .data
            ) or []
            segs_por_video: dict[str, list[dict]] = {}
            for s in rs:
                segs_por_video.setdefault(s.get("video_id"), []).append(s)
            for ev in itens:
                pares = [
                    s for s in segs_por_video.get(ev.get("video_id"), [])
                    if s.get("cam_id") and s.get("cam_id") != ev.get("cam_id")
                ]
                if pares:
                    ev["segundo_angulo"] = {
                        "segmento_id": pares[0]["id"],
                        "cam_id": pares[0].get("cam_id"),
                        # Fase 30: offset real de relógio cam1→cam2
                        "offset_s": _offset_video_segmento(
                            meta_video_tab.get(ev.get("video_id"), {}), pares[0]
                        ),
                    }
    except Exception as e:
        log.warning("tabela: segundo_angulo lookup falhou (%s)", e)

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
    vid = (sb.table("videos")
             .select("caminho, nome, video_removido_em")
             .eq("id", ev["video_id"]).execute().data)
    if not vid:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vídeo do evento não encontrado")
    caminho = vid[0]["caminho"]  # storage_path original
    ev_video_removido = bool(vid[0].get("video_removido_em"))
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
    # A VERSÃO do formato vem de pipeline.FRAMES_VER — FONTE ÚNICA, dividida com
    # quem GRAVA o cache (pre_extrair_frames). Hardcodar "v2" aqui faria o cache
    # deixar de casar em silêncio no dia em que o formato mudasse.
    frames_prefix = posixpath.dirname(caminho) + "/__frames"
    frame_keys = [chave_frame_evento(caminho, evento_id, k) for k in (0, 1, 2)]

    # 1) Tenta servir do cache. Para não fazer 3 round-trips num miss, sonda só
    #    o primeiro; se existir, baixa os 3 (cache foi escrito atômico junto).
    try:
        primeiro = sb.storage.from_(bucket).download(frame_keys[0])
        if primeiro:
            # Fase 48.2: cache existe → serve por URL assinada (navegador busca
            # direto do Supabase; sem egress do Render). Só a sonda foi baixada.
            urls = [_url_frame_assinada(sb, bucket, k) for k in frame_keys]
            if all(urls):
                return {"frames": urls}
            # Assinatura falhou → base64 como antes (nunca quebra).
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

    # Fase 54: cache miss COM o binário já expirado. Não adianta (nem pode)
    # tentar baixar — o objeto não existe mais. Degrada com motivo explícito em
    # vez de estourar 500. Na prática isto quase nunca acontece: o cache é
    # aquecido no fim do processamento, antes de o vídeo ser apagado.
    if ev_video_removido:
        return {
            "frames": [],
            "motivo": "video_expirado",
            "detalhe": ("O vídeo original foi removido do armazenamento após o "
                        "processamento. Os frames deste evento não foram "
                        "encontrados no cache."),
        }

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
    subiu_ok = True
    for key, jpeg in zip(frame_keys, jpegs):
        try:
            sb.storage.from_(bucket).upload(
                key, jpeg, {"content-type": "image/jpeg", "upsert": "true"}
            )
        except Exception as e:
            subiu_ok = False
            log.warning(f"Cache de frame falhou (não-fatal) {key}: {e}")

    # 5) Se subiu tudo, serve por URL assinada (sem egress do Render); senão,
    #    base64 dos mesmos bytes já extraídos (nunca quebra).
    if subiu_ok:
        urls = [_url_frame_assinada(sb, bucket, k) for k in frame_keys]
        if all(urls):
            return {"frames": urls}
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
        .select("empresa, storage_path, storage_removido_em")
        .eq("id", segmento_id)
        .execute()
    )
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segmento não encontrado")
    seg = r.data[0]
    if seg.get("empresa") != user.empresa:
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    caminho = seg.get("storage_path")
    seg_removido = bool(seg.get("storage_removido_em"))
    if not caminho:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segmento sem caminho no storage")

    import base64
    import posixpath
    import tempfile
    from pathlib import Path

    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")
    # Cache determinístico (JPEGs pequenos) ao lado do segmento no Storage.
    frames_prefix = posixpath.dirname(caminho) + "/__frames"
    frame_keys = [chave_frame_segmento(caminho, segmento_id, ini, fim, k) for k in (0, 1, 2)]
    try:
        primeiro = sb.storage.from_(bucket).download(frame_keys[0])
        if primeiro:
            # Fase 48.2: cache existe → URL assinada (navegador busca direto do Supabase).
            urls = [_url_frame_assinada(sb, bucket, k) for k in frame_keys]
            if all(urls):
                return {"frames": urls}
            cached = [primeiro] + [sb.storage.from_(bucket).download(k) for k in frame_keys[1:]]
            if all(cached):
                return {"frames": ["data:image/jpeg;base64," + base64.b64encode(j).decode("ascii") for j in cached]}
    except Exception:
        pass

    # Fase 54: o segmento da cam2 também é expirado após o processamento. As
    # janelas que a tela realmente pede (a de cada evento) foram pré-aquecidas;
    # uma janela ARBITRÁRIA fora dessas, com o binário já apagado, degrada aqui.
    if seg_removido:
        return {
            "frames": [],
            "motivo": "video_expirado",
            "detalhe": ("O segmento original foi removido do armazenamento após "
                        "o processamento. Só as janelas pré-extraídas dos "
                        "eventos continuam disponíveis."),
        }

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

    subiu_ok = True
    for key, jpeg in zip(frame_keys, jpegs):
        try:
            sb.storage.from_(bucket).upload(key, jpeg, {"content-type": "image/jpeg", "upsert": "true"})
        except Exception as e:
            subiu_ok = False
            log.warning(f"Cache de frame (segmento) falhou (não-fatal) {key}: {e}")

    if subiu_ok:
        urls = [_url_frame_assinada(sb, bucket, k) for k in frame_keys]
        if all(urls):
            return {"frames": urls}
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
      descricao_invalida
                 → VH=true,  VC=false, LC=null,         OV=humano, VE=now,
                   descricao_invalida=true
      reabrir    → VH=false, VC=null,  LC=null,         OV=null,   VE=null,
                   descricao_invalida=false

    Fase 70 — POR QUE `descricao_invalida` NÃO É `descartar`.
    Descartar significa "não havia ação aqui" (falso positivo da detecção).
    `descricao_invalida` significa outra coisa: HAVIA uma cena, e o modelo de
    visão MENTIU sobre ela — descreveu alguém operando com o posto vazio.
    Sai das métricas como o descarte, mas o registro é diferente porque as
    consequências são diferentes:
      • a frase entra na lista de QUEIMADAS e nunca mais funda aprendizado
        nenhum (nem hoje, nem quando o mecanismo declarativo existir);
      • a contagem vira a taxa de alucinação do VLM, que é um número que o
        dono do processo precisa acompanhar durante a campanha.
    Misturar os dois perderia exatamente o sinal que revelou o contágio.
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
    if acao == "descricao_invalida":
        # A DESCRIÇÃO está errada — o VLM alucinou a cena. Corrigir o RÓTULO
        # aqui seria criar um mapeamento falso a partir de uma frase que nunca
        # descreveu nada. Foi assim que o mapa envenenado nasceu — e o que
        # o tornava perigoso não era o estrago já feito (que a medição
        # mostrou não ter chegado às métricas), e sim o prompt ensinando o
        # remapeamento a cada nova correção.
        # `label_corrigido` é limpo de propósito: não existe rótulo certo para
        # uma descrição que não aconteceu.
        return {
            "validado_humano": True,
            "validacao_correto": False,
            "label_corrigido": None,
            "descricao_invalida": True,
            "origem_validacao": "humano",
            "validado_em": now,
        }
    if acao == "reabrir":
        # Devolve à fila como pendente, limpando toda marca de validação.
        return {
            "validado_humano": False,
            "validacao_correto": None,
            "label_corrigido": None,
            "descricao_invalida": False,
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
    if body.acao not in ("confirmar", "corrigir", "descartar",
                         "descricao_invalida", "reabrir"):
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
# Fase 49: binário — produtivo (valor_agregado) × não-produtivo (desperdicio);
# null = não-classificado. "apoio" removido.
_CATS_LEAN_VALIDAS = {"valor_agregado", "desperdicio"}


def _normalizar_cat_lean(bruto: str | None) -> str | None:
    cat = (bruto or "").strip().lower() or None
    if cat is not None and cat not in _CATS_LEAN_VALIDAS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "categoria_lean deve ser uma de: valor_agregado, desperdicio, ou null.",
        )
    return cat


@app.put("/comportamentos/{comportamento_id}/categoria")
def setar_categoria_lean(
    comportamento_id: str,
    body: CategoriaLeanBody,
    user: CurrentUser = Depends(get_current_user),
):
    cat = _normalizar_cat_lean(body.categoria_lean)
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
    return _aplicar_categoria_lean(sb, user.empresa, comportamento_id, alvo, cat)


def _aplicar_categoria_lean(sb, empresa: str, comportamento_id: str, alvo: dict, cat: str | None) -> dict:
    """Grava a decisão do gestor + propaga (irmãos e eventos).

    Extraído da rota para poder ser reusado pela variante POR RÓTULO — que é
    a que atende os 'não classificados' cujo rótulo ainda não tem linha em
    `comportamentos` e que, por isso, não tinham como ser reclassificados.
    """
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
    # Fase 62: com a generalização desligada no processo de origem, a decisão
    # vale ONDE foi tomada e não vaza para os irmãos. Aplicá-la nos eventos do
    # próprio (empresa, processo, label) continua — isso não é generalizar, é
    # cumprir a decisão no lugar em que ela foi feita.
    _generaliza = aprendizado_automatico(sb, empresa, alvo.get("processo") or "")
    propagados = 0
    processos_irmaos: set = set()
    if cat is not None and alvo.get("label") and _generaliza:
        try:
            r2 = (
                sb.table("comportamentos")
                .select("id, processo, categoria_lean_origem")
                .eq("empresa", empresa)
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
                    if c.get("processo"):
                        processos_irmaos.add(c["processo"])
                except Exception as e:
                    log.warning(f"Lean: falha ao propagar p/ {c['id']}: {e}")
        except Exception as e:
            log.warning(f"Lean: falha ao listar comportamentos para propagação: {e}")

    # Fase 55 — desce a categoria para os EVENTOS. Três correções sobre a
    # versão anterior, todas com consequência real:
    #  1) ESCOPO: antes filtrava só por empresa, então a decisão de um processo
    #     vazava para os eventos de TODOS os processos da mesma empresa. Agora
    #     é (empresa, processo) — e a propagação cross-processo acima, que é
    #     deliberada, roda com o processo de cada comportamento irmão.
    #  2) LABEL EFETIVO: antes casava só por `comportamento_label`, deixando de
    #     fora os eventos que o gestor RENOMEOU na validação (label_corrigido) —
    #     justo os que ele mais espera ver classificados.
    #  3) PRECEDÊNCIA: elegível é só `categoria_lean IS NULL` ou origem
    #     'herdado'. Antes, `neq('humano')` também sobrescrevia 'aprendido'.
    eventos_atualizados = 0
    if alvo.get("label") and cat:
        eventos_atualizados = propagar_categoria_para_eventos(
            sb, empresa, alvo["processo"], alvo["label"], cat)
        # Os comportamentos irmãos (mesmo label, outros processos) recebem a
        # categoria como 'aprendido' — os eventos deles também descem.
        for proc_irmao in processos_irmaos:
            eventos_atualizados += propagar_categoria_para_eventos(
                sb, empresa, proc_irmao, alvo["label"], cat)

    return {
        "ok": True,
        "comportamento_id": comportamento_id,
        "categoria_lean": cat,
        "origem": "humano" if cat else None,
        "propagados": propagados,
        "eventos_atualizados": eventos_atualizados,
    }


class CategoriaLeanPorLabelBody(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    categoria_lean: str | None = None


@app.put("/processos/{processo_id}/comportamentos/categoria")
def setar_categoria_lean_por_label(
    processo_id: str,
    body: CategoriaLeanPorLabelBody,
    user: CurrentUser = Depends(get_current_user),
):
    """Reclassifica pelo RÓTULO, criando a linha do catálogo se ela não existir.

    Por que existe: até aqui só dava para reclassificar por `comportamento_id`,
    e esse id vem de `comportamentos`. Só que a linha de `comportamentos` nasce
    no processamento, a partir do CATÁLOGO do vídeo — um rótulo que aparece nos
    eventos sem ter entrado no catálogo (rótulo renomeado à mão na validação,
    rótulo de um vídeo antigo, rótulo semeado fora do cluster) fica sem linha.
    Sem linha, `comportamento_id` vem nulo; sem id, a tela desabilitava o chip
    (Eventos) ou mandava um id inexistente (gráfico "Tempo por comportamento").

    E como a tela mostra "não classificado" exatamente quando não há categoria —
    o que inclui todo rótulo sem linha —, o sintoma aparecia colado nos
    "não classificados". Materializar a linha aqui resolve na origem: a decisão
    do gestor passa a ter onde morar, e a herança na ingestão passa a alcançar
    os vídeos seguintes.
    """
    cat = _normalizar_cat_lean(body.categoria_lean)
    label = body.label.strip()
    if not label:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "label é obrigatório.")

    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)

    r = (
        sb.table("comportamentos")
        .select("id, empresa, processo, label")
        .eq("empresa", user.empresa)
        .eq("processo", nome)
        .eq("label", label)
        .execute()
    )
    if r.data:
        alvo = r.data[0]
    else:
        try:
            ins = (
                sb.table("comportamentos")
                .insert(
                    {
                        "empresa": user.empresa,
                        "processo": nome,
                        "label": label,
                        "descricao": label.replace("_", " ").capitalize(),
                        "total_ocorrencias": 0,
                    }
                )
                .execute()
            )
            alvo = (ins.data or [{}])[0]
        except Exception as e:
            # Corrida com o processamento (unique empresa+processo+label): se
            # alguém criou no meio do caminho, seguimos com a linha existente.
            r2 = (
                sb.table("comportamentos")
                .select("id, empresa, processo, label")
                .eq("empresa", user.empresa)
                .eq("processo", nome)
                .eq("label", label)
                .execute()
            )
            if not r2.data:
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    f"Não foi possível criar o comportamento {label!r}: {e}",
                )
            alvo = r2.data[0]

    if not alvo.get("id"):
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Comportamento criado sem id — não foi possível classificar.",
        )
    alvo.setdefault("processo", nome)
    alvo.setdefault("label", label)
    return _aplicar_categoria_lean(sb, user.empresa, alvo["id"], alvo, cat)


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


@app.get("/processos/{processo_id}/dias")
def analise_diaria_processo(
    processo_id: str,
    dias: int = Query(30, ge=7, le=60),
    user: CurrentUser = Depends(get_current_user),
):
    """Fase 35 — Dashboard "Dia a dia": agregação por DIA real (evolução,
    ritmo/resumão do dia, dias sem trabalho) + janelas 7/30 dias + tendência.
    Python puro (sem custo de IA)."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    return montar_analise_diaria(sb, user.empresa, nome, dias=dias)


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
