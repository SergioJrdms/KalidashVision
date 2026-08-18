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
from .productivity import agregar_produtividade
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
    _nomes_no_prefixo,
    _prefixo_frames,
    chave_frame_segmento,
    varrer_videos_expirados,
    propagar_categoria_para_eventos,
    relatorio_propagacao_lean,
    reverter_auto_validacao_maquina,
    diagnosticar_contagio_por_descricao,
    relatorio_reprocesso_por_video,
    auditar_dia,
    identificar_titular_do_dia,
    calibrar_movimento,
    comparar_arvore,
    reavaliar_correcao,
    limpar_sufixo_estado,
    # Fase 101 — o número principal.
    permanencia_do_dia,
    # Sugestões por regra, a partir dos números medidos.
    sugestoes_do_posto,
    # Fase 102 — a precisão da descrição, medida.
    origens_sem_observacao,
    descricoes_que_afirmam_estado,
    sortear_amostra_cega,
    taxa_de_acerto,
    origem_da_descricao,
    descricao_foi_observada,
    descricao_para_exibir,
    frase_permanencia,
    frente_maquina_do_processo,
    custo_reavaliacao_usd,
    chave_frame_evento,
    limiares_movimento,
    eventos_do_bin,
    BIN_JORNADA_MIN,
    VAZIO_ATIPICO_PCT,
    aprendizado_automatico,
    APRENDIZADO_AUTO_PADRAO,
    categoria_efetiva,
    categoria_tem_evidencia,
    offset_video_segmento,
    placar_camadas,
    montar_fila_duvidas,
    limiar_duvida,
    rotulos_sem_categoria,
    varrer,
    TETO_POSTGREST,
    _inicio_video_dt,
)
from .worker import executar_job, _baixar_video  # noqa: F401

log = logging.getLogger("kalidash.api")

# ═════════════════════════════════════════════════════════════════════════
# O histórico V1–V8 entra na vitrine PARA PRESENÇA. Ligado por padrão: sem
# isso a tela fica vazia até o instrumento novo acumular dias, e há 12.878
# leituras com `papel_pessoa` preenchido esperando para responder a pergunta
# que elas SABEM responder.
#
# `off` devolve exatamente o comportamento do corte original (só V9+), para
# quem quiser conferir a vitrine sem histórico nenhum. A evidência de
# PRODUTIVIDADE do histórico é neutralizada nos dois modos — ver o bloco no
# dashboard, que explica por quê com o número medido.
# ═════════════════════════════════════════════════════════════════════════
_HISTORICO_PRESENCA = os.environ.get("KV_HISTORICO_PRESENCA", "on").strip().lower() not in (
    "off", "0", "false", "no", "")

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
    # Fase 86: só faz sentido na zona 'maquina'. Diz onde a máquina está em
    # relação à CÂMERA — é a tradução de "de costas para a câmera" em "de
    # frente para o torno". Câmera e torno são fixos, logo é uma constante.
    frente_maquina: str | None = None


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

    videos = varrer(sb, "videos", "id, nome, duracao_s, total_eventos, processado_em",
                    empresa=user.empresa, processo=p["processo"])
    videos.sort(key=lambda v: v.get("processado_em") or "", reverse=True)
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


@app.get("/processos/{processo_id}/descricoes/uso")
def uso_da_descricao(
    processo_id: str,
    descricao: str = Query(..., description="a descricao_bruta a consultar"),
    user: CurrentUser = Depends(get_current_user),
):
    """Fase 77 — QUANTO CUSTA QUEIMAR ESTA FRASE.

    Marcar uma descrição como inválida tira do APRENDIZADO todos os eventos que
    a usam — não só o card na tela. Se a frase for a mais comum do dataset, o
    custo é grande e a tela não o mostrava: o gestor apertava o botão sem saber
    o tamanho do que estava desligando.

    ⚠️ Isto NÃO é irreversível, e a tela precisa dizer isso: as queimadas são
    DERIVADAS de `eventos.descricao_invalida`. Reabrir o evento (`reabrir`)
    limpa a marca e a frase volta a ensinar.

    O que a queima faz: tira a frase do aprendizado (memória de vocabulário,
    correções e descartes). NÃO remove os outros eventos das métricas — só o
    evento marcado sai, e sai por `validacao_correto=false`, como qualquer
    descarte."""
    from collections import Counter          # local, como no resto do arquivo
    alvo = (descricao or "").strip()
    if not alvo:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "descricao é obrigatória.")
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    try:
        linhas = varrer(
            sb, "eventos",
            "id, comportamento_label, label_corrigido, tempo_inicio_s, "
            "tempo_fim_s, principal, descricao_invalida",
            empresa=user.empresa, processo=nome,
            ajustes=lambda q: q.eq("descricao_bruta", alvo),
        )
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"Falha ao consultar a descrição: {e}")

    principais = [l for l in linhas if l.get("principal") is not False]
    seg = sum(max(0.0, float(l.get("tempo_fim_s") or 0) - float(l.get("tempo_inicio_s") or 0))
              for l in principais)
    rotulos: Counter = Counter(
        (l.get("label_corrigido") or l.get("comportamento_label") or "?")
        for l in principais
    )
    return {
        "descricao": alvo,
        "eventos": len(principais),
        "minutos": round(seg / 60, 1),
        # Frase que produz MUITOS rótulos diferentes é polissêmica — sinal de
        # que o problema é a frase, não o card em que ela apareceu.
        "rotulos": [{"rotulo": r, "eventos": n} for r, n in rotulos.most_common(6)],
        "ja_queimada": any(l.get("descricao_invalida") for l in linhas),
        "reversivel": True,
    }


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


_ULTIMO_TITULAR = {"ts": 0.0, "dia": ""}


def _passe_titular_com_throttle(sb, empresa: str, motivo: str) -> None:
    """Fase 91 — o passe do titular roda DEPOIS do turno, no pulso do Pi.

    Mesmo relógio da varredura de Storage, pelo mesmo motivo (Render Hobby não
    tem cron e thread morre com o processo): o heartbeat chega sempre, e
    "existe um endpoint" não é mecanismo — é tarefa manual esperando ser
    esquecida.

    Roda sobre o dia de ONTEM, 1×/dia: identificar o titular de um dia ainda em
    curso seria eleger com meia amostra. NUNCA levanta — identificação é
    sombra, e sombra que derruba a coleta não é sombra.
    """
    import time as _t
    if os.environ.get("KV_TITULAR_PASSE", "on") in ("off", "0", "false", "False"):
        return
    agora = _t.time()
    ontem = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    if _ULTIMO_TITULAR["dia"] == ontem or agora - _ULTIMO_TITULAR["ts"] < 3600:
        return
    _ULTIMO_TITULAR["ts"] = agora
    try:
        procs = {r.get("processo") for r in (
            sb.table("contexto_processo").select("processo")
            .eq("empresa", empresa).execute().data or []) if r.get("processo")}
        for proc in sorted(procs):
            rel = identificar_titular_do_dia(sb, empresa, proc, ontem)
            if rel.get("erro"):
                continue
            for c in rel.get("cameras") or []:
                log.info("[titular/%s] %s %s/%s: %d grupo(s), titular=%s (%s)",
                         motivo, ontem, proc, c["cam_id"], c["n_grupos"],
                         c.get("titular") or "NENHUM", c.get("motivo"))
            for a in rel.get("continuidade") or []:
                log.warning("[titular] %s %s: %s", ontem, a["cam_id"], a["alerta"])
        _ULTIMO_TITULAR["dia"] = ontem
    except Exception as e:  # noqa: BLE001
        log.warning("[titular/%s] passe falhou (não-fatal): %s", motivo, e)


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


@app.get("/processos/{processo_id}/auditoria/dia")
def auditoria_do_dia(
    processo_id: str,
    dia: str = Query(..., description="AAAA-MM-DD no relógio da fábrica"),
    por_bloco: int = Query(3, ge=1, le=10),
    user: CurrentUser = Depends(get_current_user),
):
    """Fase 79 — abre um dia para AUDITORIA. Só leitura, GET de propósito.

    Auditar não é validar: nada aqui entra na fila nem muda `validado_humano`.
    Existe porque um dia inteiramente classificado como posto vazio fica
    INVISÍVEL — os eventos saem da fila por mecanismo (origem `posto_vazio` e
    `auditoria` nascem com validado_humano=True) e o dia só sobrevive como
    número agregado. Se a classificação estiver errada, não há como perceber.

    Devolve os vídeos do dia, os blocos contíguos por rótulo e uma AMOSTRA de
    cada bloco (início/meio/fim) — 245 trechos de posto vazio não se auditam um
    a um, mas se o operador estivesse lá apareceria em algum."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    rel = auditar_dia(sb, user.empresa, nome, dia, por_bloco=por_bloco)
    if "erro" in rel:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, rel["erro"])
    return {"ok": True, **rel}


@app.get("/processos/{processo_id}/movimento/calibracao")
def calibracao_do_movimento(
    processo_id: str,
    dia: str | None = Query(None, description="AAAA-MM-DD; vazio = tudo"),
    limite: int = Query(200, ge=1, le=1000),
    user: CurrentUser = Depends(get_current_user),
):
    """Fase 89 — a tela de calibração do sensor de movimento. Só leitura.

    Um MINUTO por linha: o que o sensor mediu, o que o VLM afirmou, o rótulo
    que saiu, a descrição e o vídeo. Ordenado pela DISCORDÂNCIA entre os dois,
    porque é onde se aprende — concordância não ensina nada, os dois podem
    estar certos ou errados juntos.

    Traz também os limiares em vigor com o nome da variável de ambiente ao
    lado: calibrar não precisa de deploy nem de abrir o código."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    return {"ok": True, **calibrar_movimento(sb, user.empresa, nome, dia, limite)}


@app.get("/processos/{processo_id}/arvore/comparar")
def comparar_arvore_decisao(
    processo_id: str,
    dia: str | None = Query(None, description="AAAA-MM-DD; vazio = todo o período"),
    user: CurrentUser = Depends(get_current_user),
):
    """Fase 95 — o ANTES e o DEPOIS da árvore, no MESMO dado. Só leitura.

    A árvore é determinística e lê só campos já persistidos, então os dois
    números saem dos mesmos eventos: não precisa reprocessar, não precisa
    ligar a flag, não custa chamada nenhuma.

    Devolve de ONDE saiu cada ponto (`por_nivel`, `mudancas`) — uma queda de
    produtividade sem decomposição não se apresenta a sócio nenhum."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    return {"ok": True, **comparar_arvore(sb, user.empresa, nome, dia)}


# ═════════════════════════════════════════════════════════════════════════
# Fase 102 — A PRECISÃO DA DESCRIÇÃO: medida, não suposta.
# ═════════════════════════════════════════════════════════════════════════
def _evs_do_dia(sb, empresa: str, processo: str, dia: str | None) -> list:
    """Eventos principais com o que a medição precisa. Uma leitura só."""
    linhas = varrer(
        sb, "eventos",
        "id, video_id, comportamento_label, label_corrigido, descricao_bruta, "
        "tempo_inicio_s, tempo_fim_s, n_amostras, observacoes_origem, principal, "
        "papel_pessoa, versao_instrumento, criado_em, validado_humano, "
        "pessoa_track_id, frame_inicio, cam_id",
        empresa=empresa, processo=processo,
        ajustes=(lambda q: q.gte("criado_em", f"{dia}T00:00:00")
                 .lt("criado_em", f"{dia}T23:59:59.999")) if dia else None,
    )
    return [e for e in linhas if e.get("principal") is not False]


@app.get("/processos/{processo_id}/descricao/diagnostico")
def descricao_diagnostico(
    processo_id: str,
    dia: str | None = Query(None, description="AAAA-MM-DD; vazio = todo o período"),
    user: CurrentUser = Depends(get_current_user),
):
    """As DUAS medidas das Partes 1 e 2, num payload só. Zero chamada de API.

    Substitui a query avulsa: a pergunta "consertou?" passa a ter resposta
    consultável a qualquer momento, em vez de depender de alguém lembrar o SQL.
    """
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    evs = _evs_do_dia(sb, user.empresa, nome, dia)
    return {
        "ok": True,
        "dia": dia,
        "sem_observacao": origens_sem_observacao(evs),
        "afirmam_estado_maquina": descricoes_que_afirmam_estado(evs),
    }


class SortearBody(BaseModel):
    dia: str
    n: int = 20
    semente: int | None = None


@app.post("/processos/{processo_id}/amostragem/sortear")
def amostragem_sortear(
    processo_id: str, body: SortearBody,
    user: CurrentUser = Depends(get_current_user),
):
    """Sorteia N eventos do dia para julgamento CEGO.

    ⚠️ A descrição NÃO volta neste payload. Ela é congelada no banco e só é
    revelada pelo endpoint de revelação, depois da resposta do gestor — a ordem
    é o experimento.
    """
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    evs = _evs_do_dia(sb, user.empresa, nome, body.dia)
    # Semente derivada do dia quando não vier: o mesmo dia sorteia o mesmo
    # conjunto, e duas pessoas medem o mesmo — a taxa fica comparável.
    semente = body.semente if body.semente is not None else abs(hash(body.dia)) % (2**31)
    escolhidos = sortear_amostra_cega(evs, body.n, semente)
    linhas = [{
        "empresa": user.empresa, "processo": nome, "dia": body.dia,
        "evento_id": e["id"],
        "descricao_no_sorteio": e.get("descricao_bruta"),
        "n_amostras_no_sorteio": int(e.get("n_amostras") or 0),
        "origem_descricao": origem_da_descricao(e),
    } for e in escolhidos]
    if linhas:
        try:
            sb.table("amostragem_cega").upsert(
                linhas, on_conflict="empresa,processo,evento_id").execute()
        except Exception as e:   # noqa: BLE001
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"amostragem_cega indisponível ({e}). Rode o schema.sql.")
    return {"ok": True, "sorteados": len(linhas), "semente": semente,
            "candidatos_no_dia": len([x for x in evs if (x.get("descricao_bruta") or "").strip()])}


@app.get("/processos/{processo_id}/amostragem")
def amostragem_listar(
    processo_id: str,
    dia: str = Query(...),
    user: CurrentUser = Depends(get_current_user),
):
    """A fila de julgamento + a taxa até agora.

    Item ainda não respondido vem SEM `descricao_no_sorteio` — a tela não pode
    nem receber o texto, senão basta abrir o inspetor para contaminar.
    """
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    linhas = varrer(sb, "amostragem_cega", "*", empresa=user.empresa, processo=nome,
                    ajustes=lambda q: q.eq("dia", dia))
    itens = []
    for l in linhas:
        cego = not l.get("respondido_em")
        itens.append({
            "id": l["id"], "evento_id": l["evento_id"],
            "respondido": bool(l.get("respondido_em")),
            "revelado": bool(l.get("revelado_em")),
            "veredito": l.get("veredito"),
            "resposta_humana": l.get("resposta_humana"),
            "n_amostras_no_sorteio": l.get("n_amostras_no_sorteio"),
            "origem_descricao": l.get("origem_descricao"),
            # ⛔ o texto só sai depois de respondido.
            "descricao": None if cego else l.get("descricao_no_sorteio"),
        })
    return {"ok": True, "dia": dia, "itens": itens, "taxa": taxa_de_acerto(linhas)}


class ResponderBody(BaseModel):
    resposta: str


@app.post("/amostragem/{item_id}/responder")
def amostragem_responder(
    item_id: str, body: ResponderBody,
    user: CurrentUser = Depends(get_current_user),
):
    """Grava o que o gestor viu e SÓ ENTÃO revela a descrição."""
    sb = make_supabase_client()
    r = (sb.table("amostragem_cega").select("*").eq("id", item_id)
         .limit(1).execute().data or [])
    if not r or r[0].get("empresa") != user.empresa:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item não encontrado")
    if r[0].get("respondido_em"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Este item já foi respondido — reabrir contaminaria a medida.")
    agora = datetime.now(timezone.utc).isoformat()
    sb.table("amostragem_cega").update({
        "resposta_humana": (body.resposta or "").strip(),
        "respondido_em": agora, "revelado_em": agora,
    }).eq("id", item_id).execute()
    return {"ok": True, "descricao": r[0].get("descricao_no_sorteio"),
            "n_amostras": r[0].get("n_amostras_no_sorteio"),
            "origem_descricao": r[0].get("origem_descricao")}


class VereditoBody(BaseModel):
    veredito: str
    observacao: str | None = None


@app.post("/amostragem/{item_id}/veredito")
def amostragem_veredito(
    item_id: str, body: VereditoBody,
    user: CurrentUser = Depends(get_current_user),
):
    if body.veredito not in ("bate", "bate_em_parte", "nao_bate"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "veredito deve ser bate | bate_em_parte | nao_bate")
    sb = make_supabase_client()
    r = (sb.table("amostragem_cega").select("empresa, respondido_em")
         .eq("id", item_id).limit(1).execute().data or [])
    if not r or r[0].get("empresa") != user.empresa:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item não encontrado")
    # ⚠️ A ordem É o experimento: veredito antes da resposta mede ancoragem.
    if not r[0].get("respondido_em"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Responda o que você vê ANTES de ver a descrição.")
    sb.table("amostragem_cega").update({
        "veredito": body.veredito,
        "observacao": (body.observacao or "").strip() or None,
        "veredito_em": datetime.now(timezone.utc).isoformat(),
    }).eq("id", item_id).execute()
    return {"ok": True}


@app.get("/movimento/limiares")
def movimento_limiares(user: CurrentUser = Depends(get_current_user)):
    """Os limiares do sensor em vigor NESTE processo do servidor, com o nome da
    env ao lado. Serve para conferir que a variável que você mexeu no Render de
    fato chegou — mexer e não conferir já nos custou um dia."""
    return {"ok": True, "limiares": limiares_movimento()}


@app.get("/processos/{processo_id}/jornada/bin")
def jornada_do_bin(
    processo_id: str,
    dia: str = Query(..., description="AAAA-MM-DD no relógio da fábrica"),
    minuto: float = Query(..., ge=0, le=1439.99,
                          description="Minuto-do-dia clicado; o bloco de "
                                      f"{BIN_JORNADA_MIN} min é derivado dele"),
    limite: int = Query(300, ge=1, le=1000),
    user: CurrentUser = Depends(get_current_user),
):
    """Fase 87 — abre um BLOCO da faixa "A jornada de …". Só leitura.

    A faixa é desenhada em buckets de 15 min fatiados por PROPORÇÃO de
    categoria — a largura de uma cor não é horário. Este endpoint devolve o
    bloco inteiro que contém o minuto clicado, com os eventos que o compõem:
    hora real, rótulo, descrição do VLM, papel, origem e quanto de cada um
    caiu DENTRO do bloco. É o que permite conferir se a cor bate com o que
    aconteceu — e, durante os testes do instrumento, ver qual descrição virou
    qual rótulo."""
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    rel = eventos_do_bin(sb, user.empresa, nome, dia, minuto, limite=limite)
    if "erro" in rel:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, rel["erro"])
    return {"ok": True, **rel}


@app.get("/processos/{processo_id}/titular/dia")
def titular_do_dia(
    processo_id: str,
    dia: str = Query(..., description="AAAA-MM-DD no relógio da fábrica"),
    com_recortes: bool = Query(True),
    user: CurrentUser = Depends(get_current_user),
):
    """Fase 91 — quem DOMINOU o posto neste dia, por câmera. SOMBRA.

    Devolve os grupos com o recorte de referência de cada um, o tempo
    acumulado na zona e qual foi eleito titular — para o dono bater o olho e
    dizer se separou pessoas ou virou sopa. É essa conferência que decide se a
    identificação um dia pode mexer no número; até lá, não mexe em nada.

    Identidade ANÔNIMA por papel: `g1`/`g2` valem para UM dia e UMA câmera. Não
    há nome, não há cadastro, não há re-identificação persistente.
    """
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    rel = identificar_titular_do_dia(sb, user.empresa, nome, dia)
    if "erro" in rel:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, rel["erro"])
    if com_recortes and rel.get("cameras"):
        _anexar_recortes_dos_grupos(sb, user.empresa, nome, dia, rel)
    return {"ok": True, **rel}


def _anexar_recortes_dos_grupos(sb, empresa: str, processo: str, dia: str,
                                rel: dict) -> None:
    """Põe um JPEG (data URI) no grupo, cortado do frame JÁ no Storage.

    Sem a imagem ao lado do número não dá para dizer se um grupo é uma pessoa
    ou virou sopa — e essa é a pergunta que decide se isto um dia sai da
    sombra. Não-fatal.

    ⚠️ Fase 93 — DOIS CONSERTOS, e os dois eram egress perdido:

    1. SÓ EVENTO PRINCIPAL tem frame aquecido. O "melhor evento por track" caía
       quase sempre num de AUDITORIA, cuja chave nunca existiu no Storage —
       cada grupo virava um GET que só podia falhar.

    2. TRACK DA CAM2 NÃO TEM EVENTO. Os ids da lateral vêm de outro tracker e
       não casam com os da cam1. Quando não casavam, o cartão saía sem recorte
       ("frame não aquecido", que era diagnóstico errado); quando casavam POR
       COINCIDÊNCIA, baixava-se um frame da cam1 e cortava-se com coordenada
       da cam2 — imagem de outra pessoa, que é pior que imagem nenhuma. Agora
       a cam2 é explicitamente sem recorte, com o motivo certo: os frames dela
       são indexados por JANELA DE TEMPO do segmento, não por evento.
    """
    import base64 as _b64
    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")
    cache_frames: dict = {}
    try:
        videos = varrer(sb, "videos", "id, nome, cam_id, caminho",
                        empresa=empresa, processo=processo)
        por_id = {v["id"]: v for v in videos}
        alvo = {g["referencia"]["video_id"] for c in rel["cameras"]
                for g in c["grupos"] if (g.get("referencia") or {}).get("video_id")}
        eventos = varrer(
            sb, "eventos", "id, video_id, pessoa_track_id, n_amostras, principal",
            empresa=empresa, processo=processo,
            ajustes=lambda q: q.in_("video_id", sorted(alvo)[:100]).is_("principal", "true"),
        ) if alvo else []
        melhor: dict = {}
        for e in eventos:
            if e.get("principal") is not True:
                continue
            k = (e.get("video_id"), e.get("pessoa_track_id"))
            a = melhor.get(k)
            if a is None or (e.get("n_amostras") or 0) > (a.get("n_amostras") or 0):
                melhor[k] = e
        for c in rel["cameras"]:
            # O frame do evento é da câmera PRIMÁRIA. Cortá-lo com a caixa de um
            # track da lateral daria a pessoa errada.
            eh_primaria = (c.get("cam_id") or "") in ("", "cam1")
            for g in c["grupos"]:
                if not eh_primaria:
                    g["recorte"] = None
                    g["recorte_motivo"] = (
                        "a lateral não tem frame por evento — os frames dela são "
                        "indexados por janela de tempo do segmento")
                    continue
                r = g.get("referencia") or {}
                v = por_id.get(r.get("video_id")) or {}
                ev = melhor.get((r.get("video_id"), r.get("pessoa_track_id")))
                jpg = _recorte_do_track(
                    sb, bucket, v, ev, r.get("bbox_ref"),
                    existentes=_frames_do_video(sb, bucket, v, cache_frames),
                )
                g["recorte"] = ("data:image/jpeg;base64,"
                                + _b64.b64encode(jpg).decode()) if jpg else None
                if not jpg:
                    g["recorte_motivo"] = ("nenhum evento principal deste track teve "
                                           "frame aquecido")
    except Exception as e:  # noqa: BLE001
        log.warning("[titular] recortes não anexados (%s) — não-fatal.", e)


@app.get("/processos/{processo_id}/descritores/dia")
def exportar_descritores_do_dia(
    processo_id: str,
    dia: str = Query(..., description="AAAA-MM-DD no relógio da fábrica"),
    limite: int = Query(400, ge=1, le=2000),
    com_recortes: bool = Query(True, description="Inclui um JPEG por track"),
    user: CurrentUser = Depends(get_current_user),
):
    """Fase 83 — EXPORTA os descritores de um dia num .zip, com um recorte de
    imagem por track, para o experimento de separabilidade rodar por fora.

    Por que .zip e não JSON na tela: o experimento é agrupar e OLHAR. Sem a
    imagem ao lado do vetor não dá para dizer se um grupo é uma pessoa ou virou
    sopa — e essa é a pergunta que decide o projeto inteiro.

    Não identifica ninguém, não consolida nada, não escreve nada.

    O recorte sai do frame que JÁ está no Storage (cache dos eventos), cortado
    pela `bbox_ref` normalizada. Nenhum byte novo é gravado: o bucket já
    estourou uma vez nesta campanha e um exportador não vai ser a causa da
    segunda.
    """
    import io
    import zipfile
    import json as _json
    import posixpath

    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)

    # ── 1) Descritores do dia, recortados pelo relógio REAL de gravação ──
    videos = varrer(sb, "videos", "id, nome, cam_id, caminho, duracao_s, processado_em",
                    empresa=user.empresa, processo=nome)
    do_dia = {}
    for v in videos:
        dt0 = _inicio_video_dt(v)
        if dt0 and dt0.date().isoformat() == dia:
            do_dia[v["id"]] = v
    if not do_dia:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"Nenhum vídeo processado com gravação em {dia}.")

    descs = varrer(sb, "descritores_track", "*", empresa=user.empresa, processo=nome)
    descs = [d for d in descs if d.get("video_id") in do_dia][:limite]
    if not descs:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Vídeos existem em {dia}, mas nenhum descritor — eles só passaram a "
            "ser gravados na Fase 83. Rode um vídeo depois deste deploy.",
        )

    # ── 2) Um evento por track, para achar o frame já aquecido no Storage ──
    ev_por_track: dict[tuple, dict] = {}
    if com_recortes:
        # ⚠️ Fase 93 — SÓ EVENTO PRINCIPAL. `pre_extrair_frames` aquece frames
        # apenas para os principais; evento de AUDITORIA (principal=false) nunca
        # teve frame no Storage. Sem este filtro, o "melhor evento por track"
        # caía quase sempre num de auditoria (eles são a maioria esmagadora), e
        # cada track virava um GET que só podia falhar. Era a maior fonte dos
        # erros — e de egress desperdiçado no free tier.
        eventos = varrer(
            sb, "eventos", "id, video_id, pessoa_track_id, n_amostras, principal",
            empresa=user.empresa, processo=nome,
            ajustes=lambda q: q.in_("video_id", list(do_dia)[:100]).is_("principal", "true"),
        )
        for e in eventos:
            if e.get("principal") is not True:
                continue
            k = (e.get("video_id"), e.get("pessoa_track_id"))
            atual = ev_por_track.get(k)
            if atual is None or (e.get("n_amostras") or 0) > (atual.get("n_amostras") or 0):
                ev_por_track[k] = e

    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")
    _cache_frames: dict = {}
    buf = io.BytesIO()
    n_recortes = 0
    sem_recorte: list[str] = []

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        linhas_csv = []
        cab = ["track", "video_id", "cam_id", "papel", "n_amostras",
               "tempo_posto_s", "tempo_visivel_s", "altura_rel", "aspecto",
               "ombro_tronco", "ombro_tronco_mad", "ombro_tronco_n",
               "quadril_ombro", "quadril_ombro_mad", "quadril_ombro_n",
               "cabeca_tronco", "cabeca_tronco_mad", "cabeca_tronco_n",
               # Fase 84: o n do histograma é outro que o das razões — cor sai
               # de qualquer amostra com caixa; razão exige ombros E quadris
               # visíveis, que atrás do torno é bem mais raro.
               "n_hist_sup", "n_hist_inf", "recorte"]
        linhas_csv.append(";".join(cab))

        for d in descs:
            v = do_dia.get(d["video_id"]) or {}
            chave = f"{(v.get('nome') or d['video_id'])[:40]}__t{d['pessoa_track_id']}"
            chave = "".join(c if c.isalnum() or c in "-_." else "_" for c in chave)
            razoes = d.get("razoes") or {}

            def _r(k, campo="med"):
                return (razoes.get(k) or {}).get(campo, "")

            arq_recorte = ""
            if com_recortes:
                jpg = _recorte_do_track(
                    sb, bucket, v, ev_por_track.get((d["video_id"], d["pessoa_track_id"])),
                    d.get("bbox_ref"),
                    existentes=_frames_do_video(sb, bucket, v, _cache_frames),
                )
                if jpg:
                    arq_recorte = f"recortes/{chave}.jpg"
                    z.writestr(arq_recorte, jpg)
                    n_recortes += 1
                else:
                    sem_recorte.append(chave)

            linhas_csv.append(";".join(str(x) for x in [
                d["pessoa_track_id"], d["video_id"], d.get("cam_id") or "",
                d.get("papel_predominante") or "", d.get("n_amostras") or 0,
                d.get("tempo_posto_s") or 0, d.get("tempo_visivel_s") or 0,
                d.get("altura_rel") or "", d.get("aspecto") or "",
                _r("ombro_tronco"), _r("ombro_tronco", "mad"), _r("ombro_tronco", "n"),
                _r("quadril_ombro"), _r("quadril_ombro", "mad"), _r("quadril_ombro", "n"),
                _r("cabeca_tronco"), _r("cabeca_tronco", "mad"), _r("cabeca_tronco", "n"),
                (d.get("hist_bins") or {}).get("n_sup", ""),
                (d.get("hist_bins") or {}).get("n_inf", ""),
                arq_recorte,
            ]))

        z.writestr("descritores.csv", "\n".join(linhas_csv))
        z.writestr("descritores.json", _json.dumps(
            {"dia": dia, "processo": nome, "n_tracks": len(descs),
             "n_recortes": n_recortes, "tracks": descs},
            ensure_ascii=False, indent=1, default=str))
        z.writestr("LEIA-ME.md", _LEIAME_EXPORT.format(
            dia=dia, processo=nome, n=len(descs), n_rec=n_recortes,
            n_sem=len(sem_recorte),
            cams=", ".join(sorted({str(d.get("cam_id")) for d in descs})),
        ))

    buf.seek(0)
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="descritores_{nome}_{dia}.zip"'},
    )


def _frames_do_video(sb, bucket: str, video: dict, cache: dict) -> set:
    """Nomes de frame já existentes no prefixo deste vídeo — UMA listagem de
    metadados por vídeo, memorizada.

    É a peça que substitui centenas de GETs perdidos por uma chamada que não
    baixa byte nenhum. No free tier de 5 GB/mês essa diferença é a campanha.
    """
    caminho = (video or {}).get("caminho")
    if not caminho or str(caminho).startswith(("/", "\\")):
        return set()
    if caminho in cache:
        return cache[caminho]
    cache[caminho] = _nomes_no_prefixo(sb, bucket, _prefixo_frames(caminho))
    return cache[caminho]


def _recorte_do_track(sb, bucket: str, video: dict, evento: dict | None,
                      bbox_ref, existentes: set | None = None) -> bytes | None:
    """JPEG do track, cortado do frame que JÁ está no Storage.

    `bbox_ref` é NORMALIZADA (0-1) de propósito: o frame guardado foi
    redimensionado (FRAME_MAX_W), então coordenada em pixel do vídeo original
    cortaria o lugar errado. Normalizada, funciona em qualquer tamanho.

    ⚠️ Fase 93 — NUNCA PEDIR O QUE NÃO EXISTE. Antes, cada track sem frame
    aquecido virava um GET ao Storage que voltava erro. Centenas por
    exportação, no free tier de 5 GB/mês, para descobrir pelo 404 uma coisa
    que uma listagem de metadados responde de uma vez. `existentes` é o
    conjunto de nomes já no prefixo (uma chamada de LISTAGEM, que não baixa
    byte nenhum) — sem ele na mão, esta função se recusa a tentar.
    """
    if not evento or not bbox_ref or not video.get("caminho"):
        return None
    caminho = video["caminho"]
    if str(caminho).startswith(("/", "\\")):
        return None                      # upload legado com path local
    chave = chave_frame_evento(caminho, evento["id"], 1)
    if existentes is not None and posixpath.basename(chave) not in existentes:
        # O frame não foi aquecido para este evento. Saber ANTES é a diferença
        # entre zero requisição e uma requisição perdida por track.
        return None
    try:
        dados = sb.storage.from_(bucket).download(chave)
        if not dados:
            return None
        import numpy as _np
        import cv2 as _cv2
        img = _cv2.imdecode(_np.frombuffer(dados, dtype=_np.uint8), _cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]
        x1 = max(0, int(float(bbox_ref[0]) * w)); x2 = min(w, int(float(bbox_ref[2]) * w))
        y1 = max(0, int(float(bbox_ref[1]) * h)); y2 = min(h, int(float(bbox_ref[3]) * h))
        # Folga de 8% em volta: o experimento é OLHAR, e um recorte colado no
        # corpo esconde justamente o contexto que ajuda a reconhecer a pessoa.
        mx, my = int((x2 - x1) * 0.08), int((y2 - y1) * 0.08)
        x1, x2 = max(0, x1 - mx), min(w, x2 + mx)
        y1, y2 = max(0, y1 - my), min(h, y2 + my)
        if x2 - x1 < 8 or y2 - y1 < 8:
            return None
        ok, enc = _cv2.imencode(".jpg", img[y1:y2, x1:x2],
                                [int(_cv2.IMWRITE_JPEG_QUALITY), 80])
        return enc.tobytes() if ok else None
    except Exception as e:  # noqa: BLE001
        log.warning("[descritor] recorte falhou (%s)", e)
        return None


_LEIAME_EXPORT = """# Descritores por track — {processo} · {dia}

{n} track(s) · {n_rec} recorte(s) · {n_sem} sem recorte · câmeras: {cams}

Isto é insumo de EXPERIMENTO. Nenhuma identificação foi feita, nenhum grupo
foi formado. Os números são o que a detecção já calculava e descartava.

## Arquivos

- `descritores.csv` — uma linha por track, para abrir em planilha
- `descritores.json` — tudo, inclusive os histogramas (32 bins cada)
- `recortes/*.jpg` — uma imagem por track, cortada do frame já guardado

## Colunas que importam

| campo | o que é |
|---|---|
| `ombro_tronco` | largura dos ombros ÷ comprimento do tronco |
| `quadril_ombro` | largura do quadril ÷ largura dos ombros |
| `cabeca_tronco` | nariz→pescoço ÷ comprimento do tronco |
| `*_mad` | dispersão da razão NESTE track — **vazio quando n < 3** |
| `*_n` | em quantas amostras a razão pôde ser medida |
| `n_hist_sup` / `n_hist_inf` | amostras que entraram em cada histograma |
| `altura_rel` | altura da caixa ÷ altura do frame |
| `tempo_posto_s` | tempo estimado dentro da zona do posto |
| `hist_sup` / `hist_inf` | cor HSV (matiz × saturação) da metade de cima e de baixo |

## Antes de agrupar — três armadilhas

1. **NÃO misture câmeras.** `cam1` e `cam2` têm ângulo, distância e resolução
   diferentes. Agrupe cada uma separada, ou a primeira divisão que aparecer
   vai ser "câmera", não "pessoa".
2. **`*_n` baixo é ruído.** Uma razão medida em 3 amostras de um track de 200
   não vale o mesmo que uma medida em 180. Corte por `n` antes de agrupar.
3. **`*_mad` alto significa que a razão não está estável neste ambiente.**
   Se a dispersão DENTRO de um track é da ordem da diferença ENTRE tracks, o
   sinal não separa — e isso é a resposta do experimento, não uma falha dele.
   `*_mad` **vazio** é outra coisa: o track tem menos de 3 medidas e não há
   dispersão para estimar. Não leia vazio como zero.
4. **Track curto é a regra, não a exceção.** Num dia medido, 57 de 90 tracks da
   cam1 tinham UMA amostra. Isso não invalida o descritor como unidade de
   agrupamento — é justamente por isso que se agrupa primeiro e só depois se
   soma o tempo —, mas invalida qualquer leitura de "este track é assim".
   Trate cada linha como uma observação fraca, não como uma pessoa.

## O que o resultado decide

Se os grupos baterem com as pessoas ao olhar os recortes, o caminho barato
(sem modelo de reidentificação) está de pé. Se virar sopa — uniformes iguais,
luz das 6h contra a das 15h — o caminho barato morreu, e é melhor saber agora
do que depois de construir consolidação diária em cima.
"""


@app.get("/processos/{processo_id}/rotulos/sem-categoria")
def listar_rotulos_sem_categoria(
    processo_id: str,
    limite: int = Query(60, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
):
    """Fase 85 — rótulos sem categoria Lean, do mais CARO para o mais barato.

    Só leitura. Existe porque rótulo novo nasce sem categoria e, sem categoria,
    o tempo conta como NÃO-PRODUTIVO (`categoria_efetiva`, Fase 63). Quando a
    mudança de prompt fizer nascer vários rótulos de uma vez, parte deles será
    trabalho produtivo de verdade — e a produtividade cai por CONTABILIDADE
    antes de cair por MEDIÇÃO. No gráfico de um dia as duas quedas são
    indistinguíveis; a saída é classificar rápido, começando pelo rótulo que
    representa mais tempo.

    A classificação em si é o `PUT /processos/{id}/comportamentos/categoria`.
    """
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)
    rel = rotulos_sem_categoria(sb, user.empresa, nome, limite=limite)
    if "erro" in rel:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, rel["erro"])
    return {"ok": True, **rel}


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
    # Fase 91: o passe do titular pega carona no mesmo pulso — 1×/dia, sobre o
    # dia de ontem, fora do caminho de ingestão. Não-fatal por dentro.
    _passe_titular_com_throttle(sb, user.empresa, "heartbeat")
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
        # Janela fechada de 24h: com pulso de 5 min são ~288 linhas por câmera,
        # bem abaixo do teto — mas paginado por regra, não por sorte.
        hbs = varrer(
            sb, "heartbeats_edge",
            "device_id, runner_versao, estado, cameras, disco_livre_gb, "
            "disco_uso_pct, cpu_temp_c, uptime_s, turno_janela, "
            "turno_deadline, recebido_em",
            empresa=user.empresa, processo=processo_nome, ordem="id",
            ajustes=lambda q: q.gte("recebido_em", desde),
        )
        # Pagina por `id` (chave única — `recebido_em` empata entre câmeras e a
        # paginação pularia linhas) e ordena aqui: o resto da função espera do
        # mais novo para o mais velho.
        hbs.sort(key=lambda h: h.get("recebido_em") or "", reverse=True)
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
    "frame_ref_w, frame_ref_h, ativo, frente_maquina, criado_em, atualizado_em"
)
_FRENTE_MAQUINA_VALIDOS = ("camera", "oposta", "perfil")


def _validar_zona(sb, user: CurrentUser, nome_proc: str, body: ZonaBody,
                  zona_id: str | None = None) -> None:
    """Valida papel/pontos e a unicidade de posto_operador ativa por câmera."""
    if body.papel not in PAPEIS_ZONA:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"papel deve ser um de: {', '.join(PAPEIS_ZONA)}.",
        )
    if body.frente_maquina is not None:
        if body.frente_maquina not in _FRENTE_MAQUINA_VALIDOS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"frente_maquina deve ser um de: {', '.join(_FRENTE_MAQUINA_VALIDOS)}.",
            )
        if body.papel != "maquina":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "frente_maquina só se aplica à zona de papel 'maquina'.",
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
                "frente_maquina": body.frente_maquina,
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
                "frente_maquina": body.frente_maquina,
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
            linhas = varrer(sb, tbl, "id, cam_id", empresa=user.empresa, processo=nome,
                            ajustes=lambda q: q.not_.is_("cam_id", "null"))
            cams.update(row["cam_id"] for row in linhas if row.get("cam_id"))
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
        # Exclusão de processo: ler pela metade deixaria metade dos binários
        # órfãos no bucket para sempre.
        vids = varrer(sb, "videos", "id, caminho", empresa=user.empresa, processo=nome)
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
        alvo = varrer(sb, "segmentos", "id", empresa=user.empresa, processo=nome,
                      ajustes=lambda q: q.eq("status", "erro"))
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
        rows = varrer(sb, "segmentos", "id, processo, status", empresa=user.empresa)
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
def dashboard(
    processo_id: str,
    janela_dias: int = Query(7, ge=1, le=30),
    user: CurrentUser = Depends(get_current_user),
):
    sb = make_supabase_client()
    nome = _processo_nome(sb, user, processo_id)

    # Busca eventos / vídeos / comportamentos UMA vez com superset de colunas e
    # reaproveita no snapshot e nos blocos abaixo. Antes, cada bloco refazia o
    # próprio scan — dashboard fazia 3 buscas redundantes só por isso.
    # Superset = união das colunas usadas pelo snapshot e pelo dashboard.
    from collections import Counter

    # Fase 81 — TUDO paginado. O `.limit(50000)` daqui não pedia 50 mil linhas:
    # pedia as 1000 primeiras, e o dashboard inteiro (placar, composição,
    # comportamentos, snapshot do chat) era calculado sobre esse pedaço.
    # `videos` era pior ainda: sem `.limit()` nenhum, o teto do PostgREST
    # aplicava-se igual e em silêncio.
    evs = varrer(
        sb, "eventos",
        "id, video_id, pessoa_track_id, comportamento_label, label_corrigido, "
        "tempo_inicio_s, tempo_fim_s, validacao_correto, validado_humano, "
        "origem_validacao, confianca, principal, zona_contexto, "
        # Caso de uso comercial: identidade/presença + decisão binária da
        # descrição. Cluster, vocabulário e categoria Lean não entram na conta.
        "papel_pessoa, maos_maquina, orientacao, trabalho, descricao_bruta, "
        "n_amostras, versao_instrumento",
        empresa=user.empresa, processo=nome,
    )
    videos = varrer(
        sb, "videos", "id, nome, duracao_s, total_eventos, total_pessoas, "
        "cam_id, gravado_em, processado_em",
        empresa=user.empresa, processo=nome,
    )
    videos.sort(key=lambda v: v.get("processado_em") or "", reverse=True)
    comps_full = varrer(
        sb, "comportamentos", "id, label, descricao, categoria_lean, categoria_lean_origem",
        empresa=user.empresa, processo=nome, 
    )

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

    # ═══════════════════════════════════════════════════════════════════
    # Fase 102 — QUANTO DO PARETO É OBSERVAÇÃO. O Pareto é "no que o tempo foi
    # gasto", e virou o diferencial do produto: só visão computacional responde
    # isso. Por isso ele precisa dizer quanto de si mesmo é afirmação sem
    # observação — um Pareto bonito construído sobre herança é pior que um
    # Pareto com buraco declarado.
    # ═══════════════════════════════════════════════════════════════════
    _diag_desc = origens_sem_observacao(evs)

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

    # ═══════════════════════════════════════════════════════════════════
    # Fase 101 — O NÚMERO PRINCIPAL. Calculado DEPOIS de tudo e sem depender
    # de nada acima: não lê `dist_enriquecida`, não lê `comps_full`, não lê
    # categoria. Se toda a esteira de rótulo/categoria estiver errada, este
    # número continua certo — é o ponto da fase.
    # ═══════════════════════════════════════════════════════════════════
    _frente = frente_maquina_do_processo(sb, user.empresa, nome)
    permanencia = permanencia_do_dia(evs, _frente)
    permanencia["frase"] = frase_permanencia(permanencia)

    # ── O PRODUTO VENDIDO ──────────────────────────────────────────────
    # O dashboard antigo misturava presença, labels de vocabulário e Lean.
    # Este contrato usa uma única decisão por evento e separa os dois
    # denominadores: presença no período e produtividade quando o operador foi
    # identificado. A cobertura impede que falha de identidade vire acusação
    # de improdutividade.
    _tz_fabrica, _ = fuso_do_processo(sb, user.empresa, nome)
    _meta_video: dict[str, tuple[datetime, str | None]] = {}
    for _v in videos:
        _dt = _inicio_video_dt(_v)
        if _v.get("id") and _dt is not None:
            if _dt.tzinfo is None:
                _dt = _dt.replace(tzinfo=_tz_fabrica)
            else:
                _dt = _dt.astimezone(_tz_fabrica)
            _meta_video[str(_v["id"])] = (_dt, _v.get("cam_id"))

    _eventos_prod: list[dict] = []
    _n_historico = 0
    for _e in evs:
        # V1–V8 elegeram P1 por permanência/bbox e não persistiram a decisão
        # direta. Reinterpretar esse histórico com o contrato novo misturaria
        # instrumentos incompatíveis no mesmo percentual.
        _legado = int(_e.get("versao_instrumento") or 0) < 9
        if _legado and not _HISTORICO_PRESENCA:
            continue
        _meta = _meta_video.get(str(_e.get("video_id")))
        if not _meta:
            continue
        _dt, _cam = _meta
        _ee = dict(_e)
        if _legado:
            # ═══════════════════════════════════════════════════════════════
            # O HISTÓRICO ENTRA SÓ PARA PRESENÇA — e a neutralização abaixo é
            # o que impede que ele responda o que não pode.
            #
            # Medido em 14/08 (948 eventos, todos V7), rodando o contrato como
            # está: `presenca_pct` = 78,8% com cobertura 100%. Bate com a
            # permanência determinística da Fase 101 no mesmo dia (78,8%) — o
            # dado antigo responde presença muito bem, porque presença sai de
            # `papel_pessoa`, que está preenchido em 100% dos 12.878 eventos
            # do banco, em todas as versões.
            #
            # ⛔ PRODUTIVIDADE, NÃO. E o motivo não é o suposto: no histórico a
            # ÚNICA evidência de produtividade que existe é `maos_maquina`
            # (`trabalho` é NULL em 100% dos eventos de TODAS as versões, e
            # `orientacao` está atrás do gate de calibração). E `maos_maquina`
            # só produz evidência A FAVOR: `true` decide produtivo, `false`
            # não decide nada. Rodando o contrato sobre os principais de
            # 14/08, o resultado é `produtividade_pct` = 100,0% com 80% de
            # cobertura — um número que PARECE medido e é estruturalmente
            # incapaz de sair diferente de 100%. Isso é pior que a tela vazia.
            #
            # Então a evidência de produtividade é zerada e o contrato do
            # Codex, SEM NENHUMA ALTERAÇÃO, classifica essas leituras como
            # `produtividade_inconclusiva` — que é exatamente o mecanismo de
            # abstenção que ele já projetou para "presente, sem evidência".
            # A cobertura mostra o buraco em vez de preenchê-lo.
            # ═══════════════════════════════════════════════════════════════
            _ee["maos_maquina"] = None
            _ee["orientacao"] = None
            _ee["trabalho"] = None
            _ee["_instrumento_legado"] = True
            _n_historico += 1
        _ee["_capturado_em"] = _dt
        _ee["_dia"] = _dt.date().isoformat()
        _ee["_cam_id"] = _cam
        _eventos_prod.append(_ee)

    _frentes_por_camera: dict[str, str] = {}
    _orientacao_liberada = os.environ.get(
        "KV_ORIENTACAO_VERIFICADA", "off"
    ).strip().lower() in {"1", "true", "on", "yes"}
    try:
        for _z in varrer(
            sb,
            "zonas_camera",
            "cam_id, papel, frente_maquina, ativo",
            empresa=user.empresa,
            processo=nome,
        ):
            if (
                _orientacao_liberada
                and
                _z.get("ativo") is not False
                and _z.get("papel") == "maquina"
                and _z.get("cam_id")
                and _z.get("frente_maquina")
            ):
                _frentes_por_camera[str(_z["cam_id"])] = str(_z["frente_maquina"])
    except Exception as _exc:  # noqa: BLE001
        log.warning("[produtividade] configuração por câmera indisponível: %s", _exc)

    _ultimo_inicio = max(
        (_e["_capturado_em"] for _e in _eventos_prod), default=None
    )
    _inicio_janela = (
        _ultimo_inicio - timedelta(days=janela_dias) if _ultimo_inicio else None
    )
    _eventos_janela = [
        _e for _e in _eventos_prod
        if _inicio_janela is None or _e["_capturado_em"] >= _inicio_janela
    ]
    produtividade_posto = agregar_produtividade(
        _eventos_janela,
        frentes_por_camera=_frentes_por_camera,
        eventos_estado_atual=_eventos_prod,
        janela_dias=janela_dias,
        agora=datetime.now(timezone.utc),
    )
    # A PROVENIÊNCIA VIAJA COM O NÚMERO. Misturar instrumentos em silêncio era
    # a preocupação legítima do corte original; a resposta não é esconder o
    # histórico, é dizer quanto dele está ali. Quem lê o payload consegue
    # distinguir "presença medida por 12 mil leituras antigas" de "cobertura de
    # produtividade zerada porque o instrumento novo ainda não rodou".
    _iq = montar_insights_quantitativos(
        dist_enriquecida, composicao_valor, base, videos, cat_por_label
    )

    # SUGESTÕES POR REGRA — sobre o PROCESSO e o OPERADOR, nunca sobre o
    # produto. Zero token, e impossível falarem de um problema que o dado não
    # mostra: cada uma exige um gatilho numérico.
    sugestoes_praticas = sugestoes_do_posto(
        permanencia=permanencia,
        produtividade=produtividade_posto,
        por_hora=(_iq or {}).get("por_hora"),
        # O MIX DE ATIVIDADES é o que permite falar do PROCESSO em vez de falar
        # do produto: é dele que saem "tanto do turno é ciclo automático" e
        # "tanto é o operador andando pelo posto".
        atividades=dist_enriquecida,
        serie=produtividade_posto.get("serie_diaria"),
    )

    produtividade_posto["leituras_do_instrumento_legado"] = _n_historico
    produtividade_posto["historico_incluido"] = bool(_HISTORICO_PRESENCA)

    return {
        "snapshot": snapshot,
        "permanencia": permanencia,
        "produtividade_posto": produtividade_posto,
        "sugestoes_praticas": sugestoes_praticas,
        # Fase 102: a descrição é o diferencial — e vem com o próprio
        # certificado de origem ao lado.
        "descricao_diagnostico": _diag_desc,
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
        "insights_quantitativos": _iq,
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
    _COLS = (
        "id, video_id, comportamento_label, descricao_bruta, tempo_inicio_s, "
        "tempo_fim_s, confianca, validado_humano, validacao_correto, n_amostras, "
        "label_corrigido, origem_validacao, frame_inicio, frame_fim, bbox_inicio, "
        "pessoa_track_id, principal, papel_pessoa"
    )

    def _buscar(cols: str):
        _q = (
            sb.table("eventos")
            .select(cols)
            .eq("empresa", user.empresa)
            .eq("processo", nome)
        )
        if status_filter == "pendente":
            _q = _q.or_("validado_humano.eq.false,validado_humano.is.null")
        elif status_filter == "validado":
            _q = _q.eq("validado_humano", True)
        return _q.order("tempo_inicio_s").limit(500).execute()

    # A NARRATIVA é opcional no banco: o SQL é rodado à mão, e a fila não pode
    # ficar de pé esperando isso. Pede-se a coluna; se ela não existir, repete
    # sem ela e a tela simplesmente não mostra o parágrafo.
    try:
        r = _buscar(_COLS + ", narrativa")
    except Exception as _e:  # noqa: BLE001
        if "narrativa" not in str(_e):
            raise
        log.warning("[fila] coluna `narrativa` não existe neste banco — "
                    "seguindo sem ela (rode o schema.sql para tê-la).")
        r = _buscar(_COLS)
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

        # ═══════════════════════════════════════════════════════════════
        # O RELÓGIO DO TRECHO VEM DO SEGMENTO, e é formatado AQUI.
        #
        # Dois problemas, um conserto:
        #
        # (1) REFERÊNCIA. `videos.gravado_em` é derivado (cai para o relógio do
        #     nome do arquivo e, na falta dele, para o instante de
        #     PROCESSAMENTO — que não tem relação com quando a cena aconteceu).
        #     O SEGMENTO é o que a borda carimbou na hora de gravar; é ele que
        #     sabe que horas eram na fábrica.
        #
        # (2) FUSO. A hora saía formatada no NAVEGADOR. O carimbo está em hora
        #     de fábrica, e o navegador em São Paulo o relia como UTC e subtraía
        #     três horas: 07h aparecia como 04h. Formatar no servidor, que já
        #     conhece o fuso do processo, elimina a conversão dupla — o cliente
        #     recebe texto pronto, não instante para reinterpretar.
        # ═══════════════════════════════════════════════════════════════
        _tz, _ = fuso_do_processo(sb, user.empresa, nome)
        _seg_por_video: dict[str, dict] = {}
        if vids:
            try:
                _rsg = (
                    sb.table("segmentos")
                    .select("video_id, cam_id, gravado_em")
                    .eq("empresa", user.empresa)
                    .in_("video_id", list(vids))
                    .execute()
                    .data
                ) or []
                for _s in _rsg:
                    if not _s.get("gravado_em"):
                        continue
                    _k = str(_s.get("video_id"))
                    _ant = _seg_por_video.get(_k)
                    # O MAIS ANTIGO do vídeo: é o começo da gravação, que é a
                    # âncora de `tempo_inicio_s`. Um segmento posterior daria
                    # um instante deslocado para a frente.
                    if _ant is None or str(_s["gravado_em"]) < str(_ant["gravado_em"]):
                        _seg_por_video[_k] = _s
            except Exception as e:  # noqa: BLE001
                log.warning("[fila] hora do segmento não lida (%s) — usando o vídeo.", e)

        for i in itens:
            _s = _seg_por_video.get(str(i.get("video_id")))
            _base = (_s or {}).get("gravado_em") or i.get("gravado_em")
            i["instante_iso"] = None
            i["instante_fabrica"] = None
            i["faixa_hora_fabrica"] = None
            i["hora_de"] = "segmento" if _s else ("video" if _base else None)
            if not _base:
                continue
            try:
                _d = datetime.fromisoformat(str(_base).replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                continue
            # Carimbo sem fuso é hora de PAREDE da fábrica — é assim que a
            # borda grava. Só o com fuso precisa de conversão.
            _d = _d.replace(tzinfo=_tz) if _d.tzinfo is None else _d.astimezone(_tz)
            _d = _d + timedelta(seconds=float(i.get("tempo_inicio_s") or 0))
            i["instante_iso"] = _d.isoformat()
            i["instante_fabrica"] = _d.strftime("%H:%M")
            i["faixa_hora_fabrica"] = _d.strftime("%Hh")

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

    # ═══════════════════════════════════════════════════════════════════
    # ORDEM CRONOLÓGICA REAL — a fila segue o relógio da fábrica.
    #
    # `q.order("tempo_inicio_s")` lá em cima ordena pelo tempo DENTRO do vídeo,
    # e todo vídeo começa em 0s. Com 46 vídeos no dia, o resultado é: todos os
    # trechos de 0s de todos os vídeos, depois todos os de 10s, e assim por
    # diante — o gestor recebia 06h, 14h, 09h, 07h em sequência. Parecia
    # sorteio e não era; era ordenação pela chave errada.
    #
    # A chave certa é o instante de RELÓGIO: quando o vídeo foi gravado mais o
    # deslocamento dentro dele. `gravado_em` já vem anexado pelo bloco de
    # agrupamento acima, então isto não custa consulta nenhuma.
    #
    # ⚠️ SÓ ORDEM. Nada de o que entra, o que é escondido pelo gate, o que é
    # aprendido ou como se valida — o conjunto é exatamente o mesmo, em outra
    # sequência. Evento sem `gravado_em` (vídeo antigo) vai para o fim em vez
    # de para o começo: sem relógio, ele não tem lugar na linha do tempo, e
    # jogá-lo no início desalinharia justamente a primeira hora.
    # A mesma âncora que a tela mostra: o carimbo do SEGMENTO, no fuso da
    # fábrica. Ordenar por uma referência e exibir outra faria a fila parecer
    # fora de ordem mesmo estando ordenada — que é o defeito que acabou de ser
    # consertado, com outra roupa.
    def _instante(e: dict) -> tuple:
        g = e.get("instante_iso") or e.get("gravado_em")
        return (0 if g else 1, str(g or ""), float(e.get("tempo_inicio_s") or 0))

    itens.sort(key=_instante)
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
        # ⚠️ Fase 99 — A GUARDA VALE AQUI TAMBÉM. A tela oferece os rótulos do
        # HISTÓRICO, que ainda tem os 896 com sufixo de estado; sem esta linha
        # o gestor reintroduziria `monitorar_maquina_parada` de boa-fé, e o
        # rótulo voltaria a afirmar algo que nada mede. A correção continua
        # valendo — só o sufixo cai.
        novo = limpar_sufixo_estado((label_corrigido or "").strip())
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

    # Fase 98 — REAVALIAÇÃO: uma chamada de visão para DIAGNOSTICAR o erro.
    # Só em correção HUMANA individual, só com KV_REAVALIAR_CORRECAO ligada,
    # e o resultado vale SÓ para este evento. Não-fatal: se falhar, a
    # correção já está gravada e é ela que importa.
    reav = None
    # Fase 99: o MESMO rótulo que foi gravado. `_montar_update_validacao` limpa
    # o sufixo de estado; mandar o texto cru aqui faria o diagnóstico raciocinar
    # sobre `monitorar_maquina_parada` — um rótulo que não existe no banco.
    _lc = limpar_sufixo_estado(body.label_corrigido or "")
    if body.acao == "corrigir" and _lc:
        try:
            reav = _reavaliar_evento(sb, user.empresa, evento_id, _lc)
        except Exception as e:  # noqa: BLE001
            log.warning("[reavaliacao] não-fatal: %s", e)
    return {"ok": True, "reavaliacao": reav}


def _reavaliar_evento(sb, empresa: str, evento_id: str, rotulo_novo: str):
    """Busca os frames JÁ no Storage e pede o diagnóstico. Zero frame novo."""
    import posixpath
    r = (sb.table("eventos")
         .select("id, video_id, comportamento_label, descricao_bruta, empresa")
         .eq("id", evento_id).limit(1).execute().data or [])
    if not r or r[0].get("empresa") != empresa:
        return None
    ev = r[0]
    v = (sb.table("videos").select("caminho")
         .eq("id", ev.get("video_id")).limit(1).execute().data or [])
    caminho = (v[0].get("caminho") if v else None)
    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")
    imgs = []
    if caminho and not str(caminho).startswith(("/", "\\")):
        # Fase 93: nunca pedir ao Storage o que não existe — lista primeiro.
        existentes = _nomes_no_prefixo(sb, bucket, _prefixo_frames(caminho))
        import base64 as _b64
        for k in (0, 1, 2):
            chave = chave_frame_evento(caminho, evento_id, k)
            if posixpath.basename(chave) not in existentes:
                continue
            try:
                dados = sb.storage.from_(bucket).download(chave)
                if dados:
                    imgs.append(_b64.b64encode(dados).decode())
            except Exception:  # noqa: BLE001
                pass
    reav = reavaliar_correcao(None, ev, imgs, rotulo_novo)
    if reav and "erro" not in reav:
        sb.table("eventos").update({"reavaliacao": reav}).eq("id", evento_id).execute()
    return reav


@app.get("/reavaliacao/custo")
def reavaliacao_custo(user: CurrentUser = Depends(get_current_user)):
    """Quanto custa UMA reavaliação, para decidir se liga a chave."""
    from .pipeline import _REAVALIAR
    return {"ok": True, "ligada": _REAVALIAR,
            "usd_por_correcao_3_imagens": custo_reavaliacao_usd(3),
            "usd_por_correcao_1_imagem": custo_reavaliacao_usd(1),
            "usd_por_100_correcoes": round(custo_reavaliacao_usd(3) * 100, 3),
            "chave": "KV_REAVALIAR_CORRECAO"}


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
