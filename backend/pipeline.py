"""Kalidash Vision · Pipeline Inteligente.

Refatoração do notebook KaliVision_Pipeline_Final.ipynb. A LÓGICA DE IA
(prompts, modelos, regras de aprendizado, auto-validação, isolamento por
contexto) é IDÊNTICA à do notebook. Aqui apenas encapsulamos as células
em funções parametrizáveis chamáveis pelo worker da API.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path as _Path

try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(_Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from groq import Groq
from supabase import Client, create_client
# NOTA: `from ultralytics import YOLO` foi removido do topo de propósito.
# torch + ultralytics pesam centenas de MB no boot do uvicorn. Como o módulo
# usa `from __future__ import annotations`, as anotações de tipo `YOLO` são
# strings (não exigem o símbolo em runtime). O import real acontece de forma
# lazy dentro de processar_video (e em worker.py::_get_yolo), então o processo
# que serve navegação (validação, dashboard, /frames) não carrega torch.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s · %(levelname)s · %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("kalidash")


# ═════════════════════════════════════════════════════════════════════════
# MODELOS / DEFAULTS — exatamente os mesmos do notebook
# ═════════════════════════════════════════════════════════════════════════
YOLO_MODEL = "yolo11n-pose.pt"
GROQ_MODEL_VISION = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_MODEL_ANALISE = "openai/gpt-oss-120b"
GROQ_MODEL_RAPIDO = "llama-3.3-70b-versatile"

YOLO_CONF_MIN = 0.45
AREA_MIN_RATIO = 0.005
TRACKER_CONFIG = "botsort.yaml"

# 5s (não 3s): ~40% menos chamadas ao VLM por vídeo → menos pressão de RPM/TPM
# no Groq Free Tier e fila drena mais rápido. Configurável via env.
DEFAULT_INTERVALO_AMOSTRAGEM_S = float(os.environ.get("KV_INTERVALO_AMOSTRAGEM_S", "5.0"))
DEFAULT_LIMIAR_AUTO_VALIDACAO = 2

DEFAULT_ROIS_CONTEXTO: dict[str, dict] = {}


# ═════════════════════════════════════════════════════════════════════════
# SCHEMA SQL (igual ao notebook). Use para criar/atualizar o banco.
# ═════════════════════════════════════════════════════════════════════════
SCHEMA_SQL = """
-- Vídeos analisados
create table if not exists videos (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    nome text not null,
    caminho text,
    duracao_s numeric,
    fps numeric,
    largura int,
    altura int,
    total_pessoas int,
    total_eventos int,
    cam_id text,                       -- id da câmera no edge (cam1, cam2…) — NULL p/ upload manual
    gravado_em timestamptz,            -- instante REAL de início (relógio); base p/ cruzar câmeras
    processado_em timestamptz default now()
);

-- Inbox de segmentos do edge (Fase 6). O edge sobe TUDO no storage (1-3h) antes
-- de a plataforma processar. Cada upload do edge vira uma linha aqui (pendente);
-- o orquestrador pareia cam1/cam2 pelo gravado_em (= seg_TIMESTAMP do nome) e
-- processa 1 por 1. `videos` continua sendo só o que JÁ foi processado.
create table if not exists segmentos (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    storage_path text not null,
    nome text,
    cam_id text,
    gravado_em timestamptz,
    status text default 'pendente',    -- pendente|enfileirado|processando|concluido|erro
    video_id uuid,                     -- preenchido após processar (vídeo do primário)
    erro text,
    recebido_em timestamptz default now(),
    processado_em timestamptz
);

create table if not exists comportamentos (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    label text not null,
    descricao text,
    total_ocorrencias int default 0,
    primeira_observacao timestamptz default now(),
    ultima_observacao timestamptz default now(),
    unique (empresa, processo, label)
);

create table if not exists eventos (
    id uuid primary key default gen_random_uuid(),
    video_id uuid references videos(id) on delete cascade,
    empresa text not null,
    processo text not null,
    pessoa_track_id int not null,
    comportamento_label text not null,
    descricao_bruta text,
    tempo_inicio_s numeric not null,
    tempo_fim_s numeric not null,
    duracao_s numeric generated always as (tempo_fim_s - tempo_inicio_s) stored,
    frame_inicio int,
    frame_fim int,
    bbox_inicio jsonb,
    zona_contexto text,
    n_amostras int default 1,
    confianca numeric default 0.7,
    validado_humano boolean default false,
    validacao_correto boolean,
    label_corrigido text,
    validado_em timestamptz,
    origem_validacao text,
    criado_em timestamptz default now()
);

create table if not exists sugestoes_melhoria (
    id uuid primary key default gen_random_uuid(),
    video_id uuid references videos(id) on delete cascade,
    empresa text not null,
    processo text not null,
    prioridade text,
    area text,
    situacao text,
    causa_provavel text,
    sugestao text,
    impacto_estimado text,
    eventos_relacionados jsonb,
    status text not null default 'pendente',     -- pendente | realizada | dispensada
    marcada_em timestamptz,                       -- quando o gestor marcou
    voltou_apos_realizada boolean not null default false,  -- sinal: voltou depois de marcada como realizada
    criado_em timestamptz default now()
);

create table if not exists contexto_processo (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    descricao text,
    atualizado_em timestamptz default now(),
    unique (empresa, processo)
);

create table if not exists perguntas_processo (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    pergunta text not null,
    motivo text,
    comportamentos_relacionados jsonb,
    respostas_rapidas jsonb,                -- 3 respostas curtas plausíveis geradas pela LLM
    status text not null default 'pendente',
    resposta text,
    respondida_em timestamptz,
    criada_em timestamptz default now()
);

create table if not exists prism_conversas (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    titulo text not null default 'Nova conversa',
    titulo_auto boolean not null default true,
    criada_em timestamptz default now(),
    atualizada_em timestamptz default now()
);

create table if not exists prism_mensagens (
    id uuid primary key default gen_random_uuid(),
    conversa_id uuid references prism_conversas(id) on delete cascade,
    empresa text not null,
    processo text not null,
    papel text not null,          -- 'user' | 'assistant'
    conteudo text not null,
    criada_em timestamptz default now()
);

-- Insights consolidados de portfólio (por empresa, não por processo)
create table if not exists insights_globais (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    prioridade text,                       -- alta | media | info
    titulo text,
    descricao text,
    processos_relacionados jsonb,
    criado_em timestamptz default now()
);

-- Padrões por processo (recorrência/evolução — distinto de sugestoes_melhoria)
create table if not exists padroes_processo (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    tipo text,                             -- tendencia|recorrencia|desvio|volatilidade|fluxo|desperdicio|valor
    camada text,                           -- temporal | estrutural
    titulo text,
    descricao text,
    comportamentos_relacionados jsonb,
    categoria_relacionada text,            -- valor_agregado|apoio|desperdicio|null
    confianca text,                        -- alta | media | baixa
    relevancia text,                       -- alta | media | info
    recomendacao text,
    evidencia jsonb,                       -- números que sustentam o padrão
    n_videos_analisados int,
    criado_em timestamptz default now()
);

-- Padrões globais (entre processos da empresa — sistêmicos/benchmarking)
create table if not exists padroes_globais (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    tipo text,                             -- compartilhado|benchmarking|sistemico
    titulo text,
    descricao text,
    processos_relacionados jsonb,
    confianca text,
    relevancia text,
    recomendacao text,
    evidencia jsonb,
    criado_em timestamptz default now()
);

alter table sugestoes_melhoria add column if not exists impacto_estimado text;
alter table videos add column if not exists cam_id text;
alter table videos add column if not exists gravado_em timestamptz;
alter table sugestoes_melhoria add column if not exists status text not null default 'pendente';
alter table sugestoes_melhoria add column if not exists marcada_em timestamptz;
alter table sugestoes_melhoria add column if not exists voltou_apos_realizada boolean not null default false;
alter table comportamentos    add column if not exists categoria_lean        text;
alter table comportamentos    add column if not exists categoria_lean_origem text;
alter table contexto_processo add column if not exists area text;
alter table perguntas_processo add column if not exists respostas_rapidas jsonb;

-- Classificação Lean POR EVENTO. A categoria do comportamento (memória) é
-- despejada aqui, mas cada evento guarda a sua: assim um caso específico pode
-- divergir do padrão (origem 'humano' = override individual, nunca sobrescrito).
alter table eventos add column if not exists categoria_lean        text;
alter table eventos add column if not exists categoria_lean_origem text;

-- Fase 16: 1 evento PRINCIPAL por minuto. true = principal (vai p/ validação +
-- métricas); false = cru/auditoria; null = vídeos antigos (tratados como antes).
alter table eventos add column if not exists principal boolean;

-- Backfill: despeja a categoria do comportamento nos eventos ainda sem categoria,
-- preservando qualquer override individual de humano.
update eventos e
   set categoria_lean        = c.categoria_lean,
       categoria_lean_origem = 'herdado'
  from comportamentos c
 where c.empresa  = e.empresa
   and c.processo = e.processo
   and c.label    = coalesce(e.label_corrigido, e.comportamento_label)
   and c.categoria_lean is not null
   and e.categoria_lean is null;

create index if not exists idx_eventos_categoria_lean on eventos(empresa, comportamento_label);

-- ════════════════════════════════════════════════════════════════════════
-- Turnos de gravação por processo (configuração consumida pela borda Pi).
-- Cada turno tem um nome, dias da semana (ISO: 1=seg..7=dom) e uma lista
-- de intervalos de horário no formato {"inicio":"HH:MM","fim":"HH:MM"}.
-- A pausa de almoço é, por definição, o GAP entre intervalos consecutivos.
-- ════════════════════════════════════════════════════════════════════════
create table if not exists turnos_processo (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    nome text not null,
    intervalos jsonb not null default '[]'::jsonb,         -- [{inicio:"07:00",fim:"12:00"}, ...]
    dias_semana int[] not null default array[1,2,3,4,5,6,7],
    ativo boolean not null default true,
    criado_em timestamptz default now(),
    atualizado_em timestamptz default now()
);
create index if not exists idx_turnos_ctx on turnos_processo(empresa, processo);

-- Prism: suporte a conversas de escopo global (visão de toda a empresa).
-- Conversas globais têm escopo='global' e processo = null.
alter table prism_conversas add column if not exists escopo text not null default 'processo';
alter table prism_conversas alter column processo drop not null;
alter table prism_mensagens alter column processo drop not null;

create index if not exists idx_videos_ctx        on videos(empresa, processo);
create index if not exists idx_segmentos_par     on segmentos(empresa, processo, gravado_em);
create index if not exists idx_segmentos_status  on segmentos(empresa, processo, status);

-- Fase 14: ledger de uso/custo de IA (GLOBAL — as chaves são do deploy inteiro,
-- não por empresa). Alimenta a trava de orçamento por provedor + GET /ai/uso.
create table if not exists ai_uso (
    id uuid primary key default gen_random_uuid(),
    ts timestamptz default now(),
    periodo text not null,          -- 'YYYY-MM'
    provedor text not null,         -- claude|gpt|groq|gemini
    modelo text,
    tier text,                      -- vision|analise|rapido
    tokens_in bigint default 0,
    tokens_out bigint default 0,
    custo_usd numeric(12,6) default 0
);
create index if not exists idx_ai_uso_periodo on ai_uso(periodo, provedor);
create index if not exists idx_comportamentos_ctx on comportamentos(empresa, processo);
create index if not exists idx_eventos_ctx       on eventos(empresa, processo);
create index if not exists idx_eventos_video     on eventos(video_id);
create index if not exists idx_eventos_label     on eventos(comportamento_label);
create index if not exists idx_eventos_pessoa    on eventos(pessoa_track_id);
create index if not exists idx_eventos_origem    on eventos(origem_validacao);
create index if not exists idx_sugestoes_ctx     on sugestoes_melhoria(empresa, processo);
create index if not exists idx_contexto_proc     on contexto_processo(empresa, processo);
create index if not exists idx_perguntas_ctx     on perguntas_processo(empresa, processo, status);
create index if not exists idx_prism_conversas_ctx on prism_conversas(empresa, escopo, atualizada_em desc);
create index if not exists idx_prism_mensagens_conv on prism_mensagens(conversa_id, criada_em);
create index if not exists idx_insights_globais_emp on insights_globais(empresa, criado_em desc);
create index if not exists idx_padroes_proc_ctx on padroes_processo(empresa, processo, criado_em desc);
create index if not exists idx_padroes_globais_emp on padroes_globais(empresa, criado_em desc);

-- ════════════════════════════════════════════════════════════════════════
-- RPC transacional: exclui um processo inteiro de (empresa, processo).
-- O backend remove os arquivos do Storage ANTES de chamar isto.
-- Tudo numa transação → sem estado parcial / dados órfãos.
-- ════════════════════════════════════════════════════════════════════════
create or replace function excluir_processo(p_empresa text, p_processo text)
returns void
language plpgsql
as $$
begin
  delete from prism_mensagens
    where empresa = p_empresa and processo = p_processo;
  delete from prism_conversas
    where empresa = p_empresa and processo = p_processo;
  delete from eventos
    where empresa = p_empresa and processo = p_processo;
  delete from sugestoes_melhoria
    where empresa = p_empresa and processo = p_processo;
  delete from comportamentos
    where empresa = p_empresa and processo = p_processo;
  delete from padroes_processo
    where empresa = p_empresa and processo = p_processo;
  delete from perguntas_processo
    where empresa = p_empresa and processo = p_processo;
  delete from turnos_processo
    where empresa = p_empresa and processo = p_processo;
  delete from videos
    where empresa = p_empresa and processo = p_processo;
  delete from contexto_processo
    where empresa = p_empresa and processo = p_processo;
end;
$$;
"""


# ═════════════════════════════════════════════════════════════════════════
# CLIENTES
# ═════════════════════════════════════════════════════════════════════════
def make_supabase_client(url: str | None = None, key: str | None = None) -> Client:
    url = url or os.environ["SUPABASE_URL"]
    key = key or os.environ["SUPABASE_KEY"]
    return create_client(url, key)


def make_groq_client(api_key: str | None = None):
    # Fase 13: o provedor de IA agora é resolvido pelo `ai_provider`
    # (Claude → GPT → Groq → Gemini, com fallback). Este "cliente" virou um
    # marcador vestigial: as etapas ainda recebem/repassam este handle, mas as
    # chamadas reais vão por ai_provider.text_call/vision_call/chat_call. Não
    # exige mais GROQ_API_KEY (pode rodar só com ANTHROPIC_API_KEY, por ex.).
    return None


# ═════════════════════════════════════════════════════════════════════════
# PROGRESSO — callback opcional usado pelo worker
# ═════════════════════════════════════════════════════════════════════════
ProgressCb = Callable[[str, int, str], None]  # (etapa, progresso_pct, mensagem)


def _noop_progress(etapa: str, pct: int, mensagem: str) -> None:
    log.info(f"[{etapa} {pct}%] {mensagem}")


# ═════════════════════════════════════════════════════════════════════════
# MEMÓRIA DO NEGÓCIO (isolada por empresa+processo)
# ═════════════════════════════════════════════════════════════════════════
def carregar_memoria_do_negocio(
    sb: Client,
    empresa: str,
    processo: str,
    limite_eventos: int = 5000,
    top_vocabulario: int = 30,
) -> dict[str, Any]:
    """Lê do Supabase tudo que foi validado por humanos para o par
    (empresa, processo) e monta a memória de aprendizado.
    Isolamento estrito: validações de outros clientes/processos NUNCA
    contaminam esta memória.
    """
    memoria = {
        "vocabulario": [],
        "correcoes_aprendidas": {},
        "descartados": {},
        "total_eventos_validados": 0,
    }

    r = (
        sb.table("eventos")
        .select(
            "comportamento_label, label_corrigido, descricao_bruta, validacao_correto, principal"
        )
        .eq("empresa", empresa)
        .eq("processo", processo)
        .eq("validado_humano", True)
        .limit(limite_eventos)
        .execute()
    )
    # Fase 16: crus de auditoria entram como validado_humano=True; remove-os do
    # aprendizado (só principais/antigos contam).
    eventos = [e for e in (r.data or []) if e.get("principal") is not False]
    memoria["total_eventos_validados"] = len(eventos)

    if not eventos:
        log.info(f"Memória vazia para {empresa}/{processo}.")
        return memoria

    confirmados: Counter = Counter()
    descartados: Counter = Counter()
    correcoes_brutas: dict[str, Counter] = {}

    for ev in eventos:
        correto = ev.get("validacao_correto")
        label_orig = ev.get("comportamento_label", "")
        label_corr = ev.get("label_corrigido")
        desc_bruta = (ev.get("descricao_bruta") or "").strip().lower()

        if correto is False:
            descartados[label_orig] += 1
        elif correto is True:
            if label_corr and label_corr != label_orig:
                if desc_bruta:
                    correcoes_brutas.setdefault(desc_bruta, Counter())[label_corr] += 1
            else:
                confirmados[label_orig] += 1

    memoria["correcoes_aprendidas"] = {
        desc: ctr.most_common(1)[0][0] for desc, ctr in correcoes_brutas.items()
    }
    memoria["descartados"] = dict(descartados.most_common(20))

    r2 = (
        sb.table("comportamentos")
        .select("label, descricao")
        .eq("empresa", empresa)
        .eq("processo", processo)
        .execute()
    )
    catalogo_completo = {c["label"]: c.get("descricao", "") for c in (r2.data or [])}

    vocabulario = []
    for label, n in confirmados.most_common(top_vocabulario):
        vocabulario.append(
            {
                "label": label,
                "descricao": catalogo_completo.get(label, label),
                "n_confirmacoes": n,
            }
        )
    memoria["vocabulario"] = vocabulario

    log.info(
        f"Memória · {len(eventos)} eventos validados · "
        f"{len(vocabulario)} labels · "
        f"{len(memoria['correcoes_aprendidas'])} correções · "
        f"{len(memoria['descartados'])} descartes · "
        f"{empresa}/{processo}"
    )
    return memoria


# ═════════════════════════════════════════════════════════════════════════
# DESCRIÇÃO DO PROCESSO (domain priming)
# ═════════════════════════════════════════════════════════════════════════
def resolver_descricao_processo(
    sb: Client, empresa: str, processo: str, descricao: str | None
) -> str:
    """Se descricao for não-vazia, faz upsert no banco e retorna.
    Se for vazia/None, tenta carregar a descrição salva no banco para
    este contexto.
    """
    descricao = (descricao or "").strip()
    if descricao:
        existe = (
            sb.table("contexto_processo")
            .select("id")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .execute()
        )
        if existe.data:
            sb.table("contexto_processo").update(
                {
                    "descricao": descricao,
                    "atualizado_em": datetime.utcnow().isoformat(),
                }
            ).eq("id", existe.data[0]["id"]).execute()
        else:
            sb.table("contexto_processo").insert(
                {"empresa": empresa, "processo": processo, "descricao": descricao}
            ).execute()
        return descricao

    r = (
        sb.table("contexto_processo")
        .select("descricao")
        .eq("empresa", empresa)
        .eq("processo", processo)
        .execute()
    )
    if r.data and (r.data[0].get("descricao") or "").strip():
        return r.data[0]["descricao"].strip()
    return ""


def construir_bloco_processo(descricao: str) -> str:
    if not descricao:
        return ""
    return (
        "DESCRIÇÃO DO PROCESSO (fornecida pelo cliente — use como guia de domínio):\n"
        '"""\n' + descricao + '\n"""\n'
        "Priorize reconhecer os comportamentos mencionados nesta descrição e use "
        "vocabulário alinhado a ela. Ações que claramente NÃO se encaixam nesta "
        "descrição podem ser incomuns/anômalas — descreva-as com atenção.\n\n"
    )


def construir_bloco_conhecimento_adquirido(
    sb: Client, empresa: str, processo: str, limite: int = 30
) -> str:
    """Bloco montado a partir das perguntas que o sistema fez e o cliente
    respondeu — é tratado como verdade do domínio nos prompts.

    Retorna '' se ainda não houver respostas (degradação graciosa).
    """
    try:
        r = (
            sb.table("perguntas_processo")
            .select("pergunta, resposta")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .eq("status", "respondida")
            .order("respondida_em", desc=False)
            .limit(limite)
            .execute()
        )
        respondidas = r.data or []
    except Exception as e:
        log.warning(f"Falha ao carregar conhecimento adquirido: {e}")
        return ""

    respondidas = [
        x for x in respondidas if (x.get("resposta") or "").strip() and (x.get("pergunta") or "").strip()
    ]
    if not respondidas:
        return ""

    linhas = [
        "CONHECIMENTO ADICIONAL DO PROCESSO (perguntas que o sistema fez e o cliente respondeu — use isso como VERDADE do domínio, com peso igual ou maior que a descrição acima):",
    ]
    for x in respondidas:
        linhas.append(f"- P: {x['pergunta'].strip()}")
        linhas.append(f"  R: {x['resposta'].strip()}")
    return "\n".join(linhas) + "\n\n"


def construir_bloco_dominio(descricao: str, conhecimento: str) -> str:
    """Combina descrição do processo + conhecimento adquirido.
    Substitui chamadas a construir_bloco_processo() onde também queremos
    injetar as respostas das perguntas proativas.
    """
    return construir_bloco_processo(descricao) + (conhecimento or "")


# ═════════════════════════════════════════════════════════════════════════
# FRAME UTILS
# ═════════════════════════════════════════════════════════════════════════
def anotar_frame_com_ids(frame_bgr: np.ndarray, pessoas: list[dict]) -> np.ndarray:
    f = frame_bgr.copy()
    for p in pessoas:
        x1, y1, x2, y2 = p["bbox"]
        cor = (0, 255, 100)
        cv2.rectangle(f, (x1, y1), (x2, y2), cor, 3)
        label = p["rotulo"]
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, 1.4, 3)
        cv2.rectangle(f, (x1, y1 - th - 14), (x1 + tw + 14, y1), cor, -1)
        cv2.putText(
            f, label, (x1 + 6, y1 - 6), cv2.FONT_HERSHEY_DUPLEX, 1.4, (0, 0, 0), 3
        )
    return f


def frame_para_base64(frame_bgr: np.ndarray, max_lado: int | None = None, qualidade: int | None = None) -> str:
    # Fase 12: imagem menor = MENOS tokens/frame no VLM → mais vídeos/dia dentro
    # do teto diário do Groq Free Tier. Vale p/ a amostragem e p/ o 2º ângulo.
    # Tunável por env (suba de volta a 1024/85 se migrar p/ o Dev Tier).
    if max_lado is None:
        max_lado = int(os.environ.get("KV_VLM_MAX_LADO", "1024"))
    if qualidade is None:
        qualidade = int(os.environ.get("KV_VLM_QUALIDADE", "85"))
    h, w = frame_bgr.shape[:2]
    if max(h, w) > max_lado:
        escala = max_lado / max(h, w)
        frame_bgr = cv2.resize(frame_bgr, (int(w * escala), int(h * escala)))
    ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, qualidade])
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _anexar_segundo_angulo(amostras: list, video_path_secundario: str) -> int:
    """Fase 6: para cada Amostra (da cam1), pega o frame da cam2 no MESMO tempo
    relativo (clock-aligned → mesmo instante real) e guarda em
    `img_b64_secundario`. Não roda YOLO na cam2 (é só contexto p/ o VLM).
    Retorna quantas amostras receberam o 2º ângulo. Defensivo: nunca levanta.
    """
    n = 0
    try:
        cap = cv2.VideoCapture(video_path_secundario)
        if not cap.isOpened():
            log.warning(f"2º ângulo: não abriu {video_path_secundario}")
            return 0
        dur_ms = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 30.0) * 1000.0
        for am in amostras:
            alvo_ms = am.tempo_s * 1000.0
            if dur_ms and alvo_ms > dur_ms:  # cam2 mais curta — usa o último frame
                alvo_ms = max(0.0, dur_ms - 1.0)
            cap.set(cv2.CAP_PROP_POS_MSEC, alvo_ms)
            ok, frame = cap.read()
            if ok and frame is not None:
                am.img_b64_secundario = frame_para_base64(frame)
                n += 1
        cap.release()
    except Exception as e:
        log.warning(f"2º ângulo falhou ({e}) — segue só com a cam1")
    return n


def _ponto_em_roi(cx: float, cy: float, polygon: np.ndarray) -> bool:
    return cv2.pointPolygonTest(polygon, (float(cx), float(cy)), False) >= 0


def _build_rois(rois_contexto: dict, w: int, h: int) -> dict:
    rois = {}
    for nome, info in rois_contexto.items():
        polygon = np.array(
            [[int(x * w), int(y * h)] for x, y in info["pts_rel"]], dtype=np.int32
        )
        rois[nome] = {**info, "polygon": polygon}
    return rois


def _zona_contexto(cx: int, cy: int, rois: dict) -> str | None:
    for _, info in rois.items():
        if _ponto_em_roi(cx, cy, info["polygon"]):
            return info["descricao_contexto"]
    return None


# ═════════════════════════════════════════════════════════════════════════
# GROQ CALLS COM RETRY (Retry-After + backoff exponencial + jitter)
# ═════════════════════════════════════════════════════════════════════════
def _extrair_retry_after_s(exc: BaseException) -> float | None:
    """Tenta extrair Retry-After (segundos) da resposta HTTP anexada à
    exceção do SDK Groq. Devolve None se não houver."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    try:
        headers = getattr(resp, "headers", None) or {}
        v = headers.get("retry-after") or headers.get("Retry-After")
        if not v:
            return None
        s = float(v)
        if s >= 0:
            return s
    except Exception:
        return None
    return None


def _eh_rate_limit(exc: BaseException) -> bool:
    """Heurística leve: 429 ou 503 (overloaded) — retry vale a pena."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        sc = getattr(resp, "status_code", None)
        if sc in (429, 503):
            return True
    nome = exc.__class__.__name__.lower()
    if "ratelimit" in nome or "overload" in nome:
        return True
    return False


def _eh_4xx_definitivo(exc: BaseException) -> bool:
    """Outros 4xx (≠ 429) — retry só atrasa o erro real."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return False
    sc = getattr(resp, "status_code", None)
    if isinstance(sc, int) and 400 <= sc < 500 and sc != 429:
        return True
    return False


def _espera_retry(tentativa: int, exc: BaseException) -> float:
    """Retry-After se vier (cap 90s); senão exponencial com jitter ±25% (cap 60s)."""
    import random
    ra = _extrair_retry_after_s(exc)
    if ra is not None:
        return min(ra, 90.0)
    base = min(2.0 ** tentativa, 60.0)
    jitter = base * (0.75 + 0.5 * random.random())
    return jitter


def _eh_limite_diario(exc: BaseException) -> bool:
    """True se o 429 for do teto DIÁRIO (TPD) do Groq — retentar no mesmo dia é
    inútil (reset em horas). Ex.: 'tokens per day (TPD): Limit 500000...'."""
    msg = str(exc).lower()
    return ("per day" in msg) or ("(tpd)" in msg) or ("tokens per day" in msg)


def _segundos_ate_reset(exc: BaseException) -> float:
    """Extrai quanto falta pro reset do limite: Retry-After, senão o 'try again
    in Xm Ys' da mensagem; fallback 1h."""
    ra = _extrair_retry_after_s(exc)
    if ra is not None:
        return ra
    import re
    m = re.search(r"try again in\s+(?:(\d+)m)?([\d.]+)s", str(exc))
    if m:
        return int(m.group(1) or 0) * 60 + float(m.group(2) or 0)
    return 3600.0


def _eh_json_invalido(exc: BaseException) -> bool:
    """400 de JSON inválido (json_mode) — vale UM retry (o modelo às vezes
    devolve JSON vazio sob carga), em vez de falhar de vez como outros 4xx."""
    return "json_validate_failed" in str(exc).lower()


# ── Fase 13: os wrappers viraram shims finos p/ o ai_provider ─────────────
# As assinaturas são mantidas (os ~12 call sites não mudam). O 1º argumento
# `groq_client` é vestigial (ignorado). O `model` (uma das constantes
# GROQ_MODEL_*) é traduzido pro tier lógico; o ai_provider escolhe o modelo
# concreto por provedor e faz o fallback Claude→GPT→Groq→Gemini.
def _tier_de_modelo(model: str | None) -> str:
    from . import ai_provider
    if model == GROQ_MODEL_VISION:
        return ai_provider.VISION
    if model == GROQ_MODEL_RAPIDO:
        return ai_provider.RAPIDO
    return ai_provider.ANALISE


def groq_vision_call(
    groq_client,
    image_b64: str,
    prompt_texto: str,
    json_mode: bool = True,
    max_tokens: int = 1024,
    temperatura: float = 0.2,
    retries: int = 5,
    model: str = GROQ_MODEL_VISION,
    imagens_extra: list[str] | None = None,
) -> str:
    from . import ai_provider
    return ai_provider.vision_call(
        image_b64,
        prompt_texto,
        imagens_extra=imagens_extra,
        json_mode=json_mode,
        max_tokens=max_tokens,
        temperatura=temperatura,
    )


def groq_text_call(
    groq_client,
    prompt: str,
    model: str | None = None,
    system: str | None = None,
    json_mode: bool = False,
    max_tokens: int = 2048,
    temperatura: float = 0.3,
    retries: int = 5,
) -> str:
    from . import ai_provider
    return ai_provider.text_call(
        prompt,
        _tier_de_modelo(model),
        system=system,
        json_mode=json_mode,
        max_tokens=max_tokens,
        temperatura=temperatura,
    )


# ═════════════════════════════════════════════════════════════════════════
# PROMPTS — VLM (visão) e CLUSTER (agrupar descrições)
# ═════════════════════════════════════════════════════════════════════════
PROMPT_VLM = """Você é um analista de processos industriais observando uma operação.
Na imagem há pessoas marcadas com rótulos P1, P2, ... numa zona vermelha.
Para cada pessoa marcada, descreva em UMA FRASE CURTA (até 10 palavras) o que ela está fazendo.

{bloco_processo}{bloco_vocabulario}REGRAS:
- Foque na AÇÃO (verbo + objeto), não na aparência.
- Use linguagem operacional clara em português ("manipulando peça", "digitando no computador", "andando pelo corredor").
- Se a ação não estiver clara, escreva "ação não identificada".
- NÃO invente ações que não estão visíveis.

CONTEXTO: {contexto_zonas}

Responda APENAS um JSON no formato:
{{"acoes": {{"P1": "...", "P2": "...", ...}}}}"""


# Fase 6 — dual-angle: 2 imagens do MESMO posto, no MESMO instante, de câmeras
# diferentes. A 1ª tem as pessoas marcadas (P1, P2…); a 2ª é só outro ângulo.
PROMPT_VLM_DUAL = """Você é um analista de processos industriais observando uma operação.
Você recebe DUAS IMAGENS do MESMO local e MESMO instante, de CÂMERAS DIFERENTES (ângulos distintos):
- IMAGEM 1 (câmera principal): tem as pessoas marcadas com rótulos P1, P2, ...
- IMAGEM 2 (segundo ângulo): a MESMA cena de outro ponto de vista, SEM rótulos.

Para cada pessoa marcada (P1, P2, ...) na IMAGEM 1, descreva em UMA FRASE CURTA (até 10 palavras) o que ela está fazendo.
Use OS DOIS ângulos juntos para decidir — um ângulo pode revelar o que o outro esconde, exatamente como um humano que olha as duas câmeras antes de concluir.

{bloco_processo}{bloco_vocabulario}REGRAS:
- Foque na AÇÃO (verbo + objeto), não na aparência.
- Use linguagem operacional clara em português ("manipulando peça", "digitando no computador", "andando pelo corredor").
- Os rótulos P1, P2 referem-se SEMPRE às pessoas marcadas na IMAGEM 1.
- Se mesmo com os dois ângulos a ação não estiver clara, escreva "ação não identificada".
- NÃO invente ações que não estão visíveis em nenhum dos ângulos.

CONTEXTO: {contexto_zonas}

Responda APENAS um JSON no formato:
{{"acoes": {{"P1": "...", "P2": "...", ...}}}}"""


PROMPT_CLUSTER = """Você é um analista de processos industriais.
Abaixo está uma lista de descrições de ações observadas num vídeo de operação.
Várias descrições se referem ao MESMO comportamento, mas com palavras diferentes.

{bloco_processo}{bloco_memoria}Sua tarefa: AGRUPAR as descrições em comportamentos únicos e dar para cada um:
- um label canônico em snake_case, curto e operacional (ex: "operar_computador", "manipular_peca")
- uma descrição humana em português

REGRAS:
- Comportamentos como "digitando no PC", "operando o computador", "usando o teclado" devem ter o MESMO label.
- Comportamentos genuinamente diferentes devem ter labels diferentes.
- Use labels descritivos da AÇÃO (verbo+objeto), não da localização.
- Inclua TODAS as descrições da entrada — cada uma cai em algum grupo.
- "ação não identificada" vira o label "acao_indefinida".
- PRIORIDADE MÁXIMA: se um label canônico já validado se aplica, REUSE-O em vez de criar um novo.

Responda APENAS um JSON no formato:
{{
  "comportamentos": [
    {{"label": "operar_computador", "descricao": "Pessoa operando computador / digitando", "descricoes_originais": ["digitando no pc", "operando computador"]}},
    ...
  ]
}}

DESCRIÇÕES OBSERVADAS:
"""


def construir_bloco_vocabulario(memoria: dict, max_itens: int = 20) -> str:
    if not memoria.get("vocabulario"):
        return ""
    linhas = [
        "VOCABULÁRIO OPERACIONAL CONHECIDO deste cliente (use estes termos quando a ação corresponder, mantendo consistência com observações anteriores):"
    ]
    for v in memoria["vocabulario"][:max_itens]:
        linhas.append(f'- {v["descricao"]}')
    linhas.append("")
    linhas.append(
        "Se reconhecer uma das ações conhecidas, descreva usando vocabulário CONSISTENTE com o catálogo acima. Se for ação genuinamente nova, descreva livremente."
    )
    return "\n".join(linhas) + "\n\n"


def construir_bloco_memoria_cluster(
    memoria: dict,
    max_vocab: int = 25,
    max_correcoes: int = 15,
    max_descartes: int = 10,
) -> str:
    blocos: list[str] = []

    if memoria.get("vocabulario"):
        linhas = ["LABELS CANÔNICOS JÁ VALIDADOS por humanos neste cliente:"]
        for v in memoria["vocabulario"][:max_vocab]:
            linhas.append(f'  - {v["label"]}: {v["descricao"]}')
        linhas.append("")
        linhas.append(
            'REGRA DURA: se uma descrição corresponde semanticamente a um destes labels, REUSE o label existente. NÃO crie variantes ("operar_pc" vs "operar_computador" — escolha o validado).'
        )
        blocos.append("\n".join(linhas))

    if memoria.get("correcoes_aprendidas"):
        linhas = [
            "",
            "CORREÇÕES APRENDIDAS de execuções anteriores (modelo havia inferido outro label e o humano corrigiu — use estes labels quando ver descrições parecidas):",
        ]
        for desc, label in list(memoria["correcoes_aprendidas"].items())[:max_correcoes]:
            linhas.append(f'  - descrição "{desc}" → label CORRETO: {label}')
        blocos.append("\n".join(linhas))

    if memoria.get("descartados"):
        linhas = [
            "",
            "LABELS MARCADOS COMO FALSO POSITIVO (humanos descartaram estes labels em execuções anteriores — EVITE criar variantes deles):",
        ]
        for label, n in list(memoria["descartados"].items())[:max_descartes]:
            linhas.append(f"  - {label} (descartado {n}× pelo humano)")
        blocos.append("\n".join(linhas))

    if not blocos:
        return ""
    return "\n".join(blocos) + "\n\n"


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 1 · Detecção + tracking (amostragem temporal)
# ═════════════════════════════════════════════════════════════════════════
@dataclass
class Amostra:
    frame_idx: int
    tempo_s: float
    img_b64: str
    pessoas: list
    img_b64_secundario: str | None = None   # 2º ângulo (cam2) no mesmo instante (Fase 6)


def inspecionar_video(video_path: str) -> dict:
    assert Path(video_path).exists(), f"Vídeo não encontrado: {video_path}"
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return {
        "fps": fps,
        "total_frames": total_frames,
        "largura": w,
        "altura": h,
        "duracao_s": total_frames / max(1, fps),
    }


def etapa_detectar_e_amostrar(
    yolo: YOLO,
    video_path: str,
    intervalo_s: float,
    rois_contexto: dict,
    progress_cb: ProgressCb,
) -> tuple[list[Amostra], dict, list[int]]:
    info = inspecionar_video(video_path)
    fps = info["fps"]
    total_frames = info["total_frames"]
    w = info["largura"]
    h = info["altura"]

    rois = _build_rois(rois_contexto, w, h)
    area_min_px = AREA_MIN_RATIO * (w * h)

    # Aceleração da detecção (env-tunável, sem novo deploy):
    #  - KV_TRACK_FPS: cadência efetiva do tracking (faixa útil 4–8). O YOLO roda 1 a
    #    cada `track_stride` frames; os demais são pulados SEM decodificar (cap.grab) →
    #    ~track_stride× menos inferência. BoT-SORT mantém os IDs estáveis nessa cadência.
    #  - KV_IMGSZ: 640→416 reduz ~1.7× o custo por frame (o sub-stream já é pequeno).
    track_fps = float(os.environ.get("KV_TRACK_FPS", "6"))
    track_stride = max(1, round(fps / track_fps)) if track_fps > 0 else 1
    imgsz = int(os.environ.get("KV_IMGSZ", "416"))

    amostras: list[Amostra] = []
    cap = cv2.VideoCapture(video_path)
    frame_idx = 0
    prox_amostra_s = 0.0   # próxima marca de amostragem p/ o VLM (a cada intervalo_s)
    progress_cb("deteccao", 0, f"Detectando pessoas · {total_frames} frames")

    while cap.isOpened():
        if not cap.grab():                 # avança sem decodificar (barato)
            break
        if frame_idx % track_stride == 0:
            ret, frame = cap.retrieve()    # decodifica só os frames que vão pro YOLO
            if not ret:
                break
            results = yolo.track(
                frame,
                persist=True,
                classes=[0],
                conf=YOLO_CONF_MIN,
                tracker=TRACKER_CONFIG,
                imgsz=imgsz,
                verbose=False,
            )
            tempo_s = frame_idx / fps
            if tempo_s >= prox_amostra_s:
                prox_amostra_s += intervalo_s   # consome este slot (~1 amostra / intervalo_s)
                if (
                    results[0].boxes is not None
                    and results[0].boxes.id is not None
                ):
                    boxes = results[0].boxes.xyxy.cpu().numpy()
                    ids = results[0].boxes.id.cpu().numpy().astype(int)
                    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
                    mask = areas >= area_min_px
                    pessoas = []
                    for i, (box, tid) in enumerate(zip(boxes[mask], ids[mask])):
                        x1, y1, x2, y2 = box.astype(int)
                        cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
                        pessoas.append(
                            {
                                "track_id": int(tid),
                                "bbox": (int(x1), int(y1), int(x2), int(y2)),
                                "centro": (cx, cy),
                                "zona": _zona_contexto(cx, cy, rois),
                                "rotulo": f"P{i + 1}",
                            }
                        )
                    if pessoas:
                        # Codifica imediatamente em base64 (mesma pipeline:
                        # anotar→resize→JPEG, com defaults max_lado=1024 e
                        # qualidade=85). Guardamos só a string e descartamos
                        # o numpy do frame, evitando reter ~1–2 GB de RAM em
                        # vídeos longos até a etapa VLM. anotar_frame_com_ids
                        # já copia internamente, então não precisa frame.copy().
                        img_b64 = frame_para_base64(
                            anotar_frame_com_ids(frame, pessoas)
                        )
                        amostras.append(
                            Amostra(
                                frame_idx=frame_idx,
                                tempo_s=tempo_s,
                                img_b64=img_b64,
                                pessoas=pessoas,
                            )
                        )
        frame_idx += 1
        if frame_idx % 60 == 0:
            pct = int(frame_idx / max(1, total_frames) * 100)
            progress_cb(
                "deteccao",
                min(pct, 99),
                f"frame {frame_idx}/{total_frames} · {len(amostras)} amostras",
            )

    cap.release()
    ids_unicos = sorted({p["track_id"] for a in amostras for p in a.pessoas})
    progress_cb("deteccao", 100, f"{len(amostras)} amostras · {len(ids_unicos)} pessoas")
    return amostras, info, ids_unicos


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 2 · Análise semântica (VLM)
# ═════════════════════════════════════════════════════════════════════════
def _analisar_amostra_vlm(
    groq_client: Groq,
    amostra: Amostra,
    descricao_processo: str,
    memoria: dict,
    conhecimento_adquirido: str = "",
) -> dict[int, str]:
    # Frame já chega pré-codificado (mesma rotina anotar→resize→JPEG aplicada
    # no momento da amostragem, em etapa_detectar_e_amostrar). Byte-idêntico.
    img_b64 = amostra.img_b64
    img_sec = amostra.img_b64_secundario   # 2º ângulo (cam2), se houver (Fase 6)

    contexto_partes = []
    for p in amostra.pessoas:
        if p["zona"]:
            contexto_partes.append(f"{p['rotulo']} está em {p['zona']}")
    contexto = ". ".join(contexto_partes) if contexto_partes else "sem zonas pré-definidas"

    template = PROMPT_VLM_DUAL if img_sec else PROMPT_VLM
    prompt = template.format(
        bloco_processo=construir_bloco_dominio(descricao_processo, conhecimento_adquirido),
        bloco_vocabulario=construir_bloco_vocabulario(memoria),
        contexto_zonas=contexto,
    )

    try:
        resposta = groq_vision_call(
            groq_client, img_b64, prompt, json_mode=True, max_tokens=600,
            imagens_extra=([img_sec] if img_sec else None),
        )
        dados = json.loads(resposta)
        acoes = dados.get("acoes", {})
    except json.JSONDecodeError:
        log.warning(f"JSON inválido no frame {amostra.frame_idx}")
        return {}
    except Exception as e:
        log.warning(f"Falha na análise do frame {amostra.frame_idx}: {e}")
        return {}

    mapa_rotulo_tid = {p["rotulo"]: p["track_id"] for p in amostra.pessoas}
    return {
        mapa_rotulo_tid[rot]: desc.strip().lower()
        for rot, desc in acoes.items()
        if rot in mapa_rotulo_tid and isinstance(desc, str) and desc.strip()
    }


def etapa_analise_vlm(
    groq_client: Groq,
    amostras: list[Amostra],
    descricao_processo: str,
    memoria: dict,
    progress_cb: ProgressCb,
    conhecimento_adquirido: str = "",
) -> list[dict]:
    progress_cb("vlm", 0, f"Analisando {len(amostras)} amostras com VLM")
    observacoes: list[dict] = []
    for i, am in enumerate(amostras):
        descricoes = _analisar_amostra_vlm(
            groq_client, am, descricao_processo, memoria, conhecimento_adquirido
        )
        for p in am.pessoas:
            desc = descricoes.get(p["track_id"])
            if desc:
                observacoes.append(
                    {
                        "tempo_s": am.tempo_s,
                        "frame_idx": am.frame_idx,
                        "track_id": p["track_id"],
                        "descricao": desc,
                        "bbox": p["bbox"],
                        "zona": p["zona"],
                    }
                )
        pct = int((i + 1) / max(1, len(amostras)) * 100)
        progress_cb(
            "vlm", pct, f"{i + 1}/{len(amostras)} amostras · {len(observacoes)} observações"
        )
    return observacoes


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 3 · Clusterização
# ═════════════════════════════════════════════════════════════════════════
def etapa_clusterizar(
    groq_client: Groq,
    observacoes_brutas: list[dict],
    descricao_processo: str,
    memoria: dict,
    limiar_auto_validacao: int,
    progress_cb: ProgressCb,
    conhecimento_adquirido: str = "",
) -> tuple[dict[str, str], dict[str, str], Callable[[str], str], Callable[[str], str]]:
    """Retorna (mapa_desc_label, catalogo, label_de, origem_de)."""
    progress_cb("cluster", 0, "Agrupando descrições em comportamentos")
    descricoes_unicas = sorted(set(o["descricao"] for o in observacoes_brutas))
    correcoes = memoria.get("correcoes_aprendidas", {})

    descricoes_conhecidas: dict[str, str] = {}
    descricoes_novas: list[str] = []
    for d in descricoes_unicas:
        d_lower = d.lower().strip()
        if d_lower in correcoes:
            descricoes_conhecidas[d] = correcoes[d_lower]
        else:
            descricoes_novas.append(d)

    # Fase 12: capa a lista enviada ao gpt-oss. Sem isso, um vídeo com MUITAS
    # descrições únicas monta um prompt gigante e a chamada estoura os 8K TPM/req
    # do Free Tier (erro 413 "Request too large"). 120 já cobre vídeos reais; o
    # excedente (raro) só não recebe rótulo canônico (cai no fallback).
    _max_desc = int(os.environ.get("KV_CLUSTER_MAX_DESC", "200"))
    if len(descricoes_novas) > _max_desc:
        log.warning(
            "cluster: %d descrições novas > %d — truncando p/ caber no limite do Groq.",
            len(descricoes_novas), _max_desc,
        )
        descricoes_novas = descricoes_novas[:_max_desc]

    mapa_descricao_label: dict[str, str] = {}
    catalogo: dict[str, str] = {}

    for desc, label in descricoes_conhecidas.items():
        mapa_descricao_label[desc.lower().strip()] = label
        if label not in catalogo:
            for v in memoria.get("vocabulario", []):
                if v["label"] == label:
                    catalogo[label] = v["descricao"]
                    break
            if label not in catalogo:
                catalogo[label] = label.replace("_", " ").capitalize()

    if descricoes_novas:
        prompt_completo = PROMPT_CLUSTER.format(
            bloco_processo=construir_bloco_dominio(descricao_processo, conhecimento_adquirido),
            bloco_memoria=construir_bloco_memoria_cluster(memoria),
        )
        lista_formatada = "\n".join(f"- {d}" for d in descricoes_novas)
        resposta = groq_text_call(
            groq_client,
            prompt_completo + lista_formatada,
            model=GROQ_MODEL_ANALISE,
            json_mode=True,
            max_tokens=4000,   # Fase 14: fora do Free Tier — mais espaço p/ qualidade
            temperatura=0.1,
        )
        dados = json.loads(resposta)
        clusters = dados["comportamentos"]
        for c in clusters:
            for d in c.get("descricoes_originais", []):
                mapa_descricao_label[d.strip().lower()] = c["label"]
            catalogo[c["label"]] = c["descricao"]

    for d in descricoes_unicas:
        if d.lower().strip() not in mapa_descricao_label:
            log.warning(f"Descrição não clusterizada: {d!r}")
            mapa_descricao_label[d.lower().strip()] = "acao_indefinida"

    if "acao_indefinida" not in catalogo:
        catalogo["acao_indefinida"] = "Ação não foi identificada com clareza pelo modelo"

    def label_de(desc: str) -> str:
        return mapa_descricao_label.get(desc.lower().strip(), "acao_indefinida")

    vocab_estabelecido = {
        v["label"]
        for v in memoria.get("vocabulario", [])
        if v["n_confirmacoes"] >= limiar_auto_validacao
    }
    origem_por_desc: dict[str, str] = {}
    for desc in descricoes_unicas:
        d_lower = desc.lower().strip()
        if d_lower in correcoes:
            origem_por_desc[d_lower] = "correcao_aprendida"
        elif mapa_descricao_label.get(d_lower) in vocab_estabelecido:
            origem_por_desc[d_lower] = "vocabulario_canonico"
        else:
            origem_por_desc[d_lower] = "pendente"

    def origem_de(desc: str) -> str:
        return origem_por_desc.get(desc.lower().strip(), "pendente")

    progress_cb("cluster", 100, f"{len(catalogo)} comportamentos canônicos")
    return mapa_descricao_label, catalogo, label_de, origem_de


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 4 · Segmentação em eventos
# ═════════════════════════════════════════════════════════════════════════
def etapa_segmentar_eventos(
    observacoes_brutas: list[dict],
    label_de: Callable[[str], str],
    intervalo_s: float,
) -> list[dict]:
    for o in observacoes_brutas:
        o["label"] = label_de(o["descricao"])

    por_pessoa: dict[int, list[dict]] = defaultdict(list)
    for o in observacoes_brutas:
        por_pessoa[o["track_id"]].append(o)
    for tid in por_pessoa:
        por_pessoa[tid].sort(key=lambda o: o["tempo_s"])

    janela_continuidade_s = intervalo_s * 1.6
    eventos: list[dict] = []

    for tid, obs_lista in por_pessoa.items():
        if not obs_lista:
            continue
        atual = {
            "pessoa_track_id": tid,
            "comportamento_label": obs_lista[0]["label"],
            "descricao_bruta": obs_lista[0]["descricao"],
            "tempo_inicio_s": obs_lista[0]["tempo_s"],
            "tempo_fim_s": obs_lista[0]["tempo_s"],
            "frame_inicio": obs_lista[0]["frame_idx"],
            "frame_fim": obs_lista[0]["frame_idx"],
            "bbox_inicio": list(obs_lista[0]["bbox"]),
            "zona_contexto": obs_lista[0]["zona"],
            "n_amostras": 1,
        }
        for o in obs_lista[1:]:
            gap = o["tempo_s"] - atual["tempo_fim_s"]
            if o["label"] == atual["comportamento_label"] and gap <= janela_continuidade_s:
                atual["tempo_fim_s"] = o["tempo_s"]
                atual["frame_fim"] = o["frame_idx"]
                atual["n_amostras"] += 1
            else:
                eventos.append(atual)
                atual = {
                    "pessoa_track_id": tid,
                    "comportamento_label": o["label"],
                    "descricao_bruta": o["descricao"],
                    "tempo_inicio_s": o["tempo_s"],
                    "tempo_fim_s": o["tempo_s"],
                    "frame_inicio": o["frame_idx"],
                    "frame_fim": o["frame_idx"],
                    "bbox_inicio": list(o["bbox"]),
                    "zona_contexto": o["zona"],
                    "n_amostras": 1,
                }
        eventos.append(atual)

    for e in eventos:
        e["tempo_fim_s"] = round(e["tempo_fim_s"] + intervalo_s, 2)
        e["tempo_inicio_s"] = round(e["tempo_inicio_s"], 2)
        e["confianca"] = round(min(0.95, 0.6 + 0.05 * e["n_amostras"]), 2)

    return eventos


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 4b · Consolidação em 1 evento PRINCIPAL por minuto (Fase 16)
# ═════════════════════════════════════════════════════════════════════════
def _principal_por_ia(no_bucket: list[tuple[dict, float]], catalogo: dict[str, str]) -> str | None:
    """1 chamada de IA p/ escolher a ação que RESUME um minuto fragmentado.
    Devolve um rótulo presente no minuto (ou None se falhar). Não-fatal."""
    try:
        from . import ai_provider
        import json as _json
        rotulos = sorted({e["comportamento_label"] for e, _ in no_bucket})
        descr = [f"- {e['descricao_bruta']} ({e['comportamento_label']})" for e, _ in no_bucket]
        prompt = (
            "Estas são as ações observadas durante UM minuto de uma operação "
            "industrial (uma linha por amostra):\n" + "\n".join(descr[:60]) +
            "\n\nQual foi a AÇÃO PRINCIPAL que melhor RESUME esse minuto? Escolha "
            "EXATAMENTE um destes rótulos:\n" + ", ".join(rotulos) +
            '\nResponda em JSON: {"label": "<um dos rótulos acima>"}'
        )
        resp = ai_provider.text_call(
            prompt, ai_provider.RAPIDO, json_mode=True, max_tokens=120, temperatura=0.0
        )
        label = (_json.loads(resp) or {}).get("label")
        return label if label in rotulos else None
    except Exception as e:  # noqa: BLE001
        log.warning(f"[principal] IA no empate falhou (não-fatal): {e}")
        return None


def etapa_consolidar_principais(
    eventos_crus: list[dict],
    catalogo: dict[str, str],
    duracao_s: float,
) -> list[dict]:
    """Fase 16: reduz os ~100 eventos crus a ~1 evento PRINCIPAL por minuto — a
    ação que RESUME o minuto. Por minuto, escolhe o rótulo DOMINANTE (o que mais
    durou); se o minuto for fragmentado/empate, 1 chamada de IA decide. Cada
    principal representa o minuto inteiro (tempo = a janela) p/ as métricas de
    tempo baterem. Retorna eventos no MESMO shape com `principal=True`.
    Não-fatal (o chamador cai no fluxo antigo se der []).
    """
    if not eventos_crus or duracao_s <= 0:
        return []
    import math
    bucket_s = float(os.environ.get("KV_PRINCIPAL_BUCKET_S", "60") or 60)
    dominancia = float(os.environ.get("KV_PRINCIPAL_DOMINANCIA", "0.5") or 0.5)
    n_buckets = max(1, int(math.ceil(duracao_s / bucket_s)))

    principais: list[dict] = []
    for b in range(n_buckets):
        ws, we = b * bucket_s, min((b + 1) * bucket_s, duracao_s)
        no_bucket: list[tuple[dict, float]] = []
        dur_por_label: dict[str, float] = defaultdict(float)
        for e in eventos_crus:
            ov = min(e["tempo_fim_s"], we) - max(e["tempo_inicio_s"], ws)
            if ov > 0:
                no_bucket.append((e, ov))
                dur_por_label[e["comportamento_label"]] += ov
        if not no_bucket:
            continue  # minuto sem atividade → sem principal
        total = sum(dur_por_label.values())
        top_label, top_dur = max(dur_por_label.items(), key=lambda kv: kv[1])
        share = (top_dur / total) if total > 0 else 1.0
        escolhido = top_label
        if share < dominancia and len(dur_por_label) > 1:
            escolhido = _principal_por_ia(no_bucket, catalogo) or top_label
        # Representante = evento do rótulo escolhido com MAIOR sobreposição no minuto.
        reps = [(e, ov) for (e, ov) in no_bucket if e["comportamento_label"] == escolhido]
        rep = (max(reps, key=lambda x: x[1]) if reps else max(no_bucket, key=lambda x: x[1]))[0]
        principais.append({
            "pessoa_track_id": rep["pessoa_track_id"],
            "comportamento_label": escolhido,
            "descricao_bruta": rep["descricao_bruta"],
            "tempo_inicio_s": round(ws, 2),
            "tempo_fim_s": round(we, 2),
            "frame_inicio": rep["frame_inicio"],
            "frame_fim": rep["frame_fim"],
            "bbox_inicio": list(rep["bbox_inicio"]),
            "zona_contexto": rep["zona_contexto"],
            "n_amostras": sum(e["n_amostras"] for e, _ in no_bucket),
            "confianca": rep.get("confianca", 0.7),
            "principal": True,
        })
    return principais


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 5 · Persistência
# ═════════════════════════════════════════════════════════════════════════
def etapa_persistir(
    sb: Client,
    empresa: str,
    processo: str,
    video_path: str,
    info_video: dict,
    eventos: list[dict],
    ids_unicos: list[int],
    catalogo: dict[str, str],
    origem_de: Callable[[str], str],
    nome_video: str | None = None,
    caminho_storage: str | None = None,
    cam_id: str | None = None,
    gravado_em: str | None = None,
    eventos_auditoria: list[dict] | None = None,
) -> tuple[str, int]:
    """Persiste vídeo, comportamentos, eventos. Retorna (video_id, n_auto_validados).

    Fase 16: `eventos` são os PRINCIPAIS (1/min) — contam p/ comportamentos,
    total_eventos e validação (gravados com `principal=True`). `eventos_auditoria`
    (opcional) são os crus (~100), gravados só como auditoria (`principal=False`),
    sem contar em métricas/aprendizado.

    `video_path` é o caminho LOCAL usado pelo OpenCV. `caminho_storage`
    (opcional) é o que vai pra coluna `caminho` da tabela `videos` —
    use o path do Supabase Storage aqui pra que o endpoint `/frames`
    consiga baixar o vídeo depois.
    """
    # Fase 1 multi-câmera: cam_id e gravado_em são opcionais. Só vão pro INSERT
    # se não-nulos, mantendo NULL na coluna em uploads manuais (sem edge).
    linha_video: dict[str, Any] = {
        "empresa": empresa,
        "processo": processo,
        "nome": nome_video or Path(video_path).name,
        "caminho": caminho_storage or str(video_path),
        "duracao_s": round(info_video["duracao_s"], 2),
        "fps": round(info_video["fps"], 2),
        "largura": info_video["largura"],
        "altura": info_video["altura"],
        "total_pessoas": len(ids_unicos),
        "total_eventos": len(eventos),
    }
    if cam_id:
        linha_video["cam_id"] = cam_id
    if gravado_em:
        # Validação leve do ISO 8601 — Supabase aceita string p/ timestamptz, mas
        # gravar lixo aqui quebraria queries de cruzamento na Fase 2.
        try:
            from datetime import datetime as _dt
            _dt.fromisoformat(gravado_em.replace("Z", "+00:00"))
            linha_video["gravado_em"] = gravado_em
        except Exception as e:
            log.warning(f"gravado_em ignorado (não é ISO 8601: {gravado_em!r}, {e})")
    video_row = (
        sb.table("videos")
        .insert(linha_video)
        .execute()
    )
    video_id = video_row.data[0]["id"]

    por_label = Counter(e["comportamento_label"] for e in eventos)
    for label, descricao in catalogo.items():
        n_neste_video = por_label.get(label, 0)
        if n_neste_video == 0:
            continue
        existente = (
            sb.table("comportamentos")
            .select("id, total_ocorrencias")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .eq("label", label)
            .execute()
        )
        if existente.data:
            atual = existente.data[0]
            sb.table("comportamentos").update(
                {
                    "total_ocorrencias": atual["total_ocorrencias"] + n_neste_video,
                    "ultima_observacao": datetime.utcnow().isoformat(),
                }
            ).eq("id", atual["id"]).execute()
        else:
            sb.table("comportamentos").insert(
                {
                    "empresa": empresa,
                    "processo": processo,
                    "label": label,
                    "descricao": descricao,
                    "total_ocorrencias": n_neste_video,
                }
            ).execute()

    linhas_eventos: list[dict] = []
    n_auto_validados = 0
    for e in eventos:
        origem = origem_de(e["descricao_bruta"])
        auto_validado = origem in ("correcao_aprendida", "vocabulario_canonico")
        row = {
            "video_id": video_id,
            "empresa": empresa,
            "processo": processo,
            "pessoa_track_id": e["pessoa_track_id"],
            "comportamento_label": e["comportamento_label"],
            "descricao_bruta": e["descricao_bruta"],
            "tempo_inicio_s": e["tempo_inicio_s"],
            "tempo_fim_s": e["tempo_fim_s"],
            "frame_inicio": e["frame_inicio"],
            "frame_fim": e["frame_fim"],
            "bbox_inicio": {
                "x1": e["bbox_inicio"][0],
                "y1": e["bbox_inicio"][1],
                "x2": e["bbox_inicio"][2],
                "y2": e["bbox_inicio"][3],
            },
            "zona_contexto": e["zona_contexto"],
            "n_amostras": e["n_amostras"],
            "confianca": e["confianca"],
            "origem_validacao": origem,
            # IMPORTANTE: validado_humano sempre explícito (PostgREST batch
            # não aplica DEFAULT de coluna ausente).
            "validado_humano": auto_validado,
            # Fase 16: True nos principais; None quando a consolidação está off
            # (comportamento antigo — o filtro downstream mantém True + None).
            "principal": e.get("principal"),
        }
        if auto_validado:
            row["validacao_correto"] = True
            row["validado_em"] = datetime.utcnow().isoformat()
            n_auto_validados += 1
        linhas_eventos.append(row)

    # Fase 16: eventos crus só como AUDITORIA (principal=False) — não contam em
    # comportamentos/total_eventos nem viram sugestão de validação.
    for e in (eventos_auditoria or []):
        linhas_eventos.append({
            "video_id": video_id, "empresa": empresa, "processo": processo,
            "pessoa_track_id": e["pessoa_track_id"],
            "comportamento_label": e["comportamento_label"],
            "descricao_bruta": e["descricao_bruta"],
            "tempo_inicio_s": e["tempo_inicio_s"], "tempo_fim_s": e["tempo_fim_s"],
            "frame_inicio": e["frame_inicio"], "frame_fim": e["frame_fim"],
            "bbox_inicio": {
                "x1": e["bbox_inicio"][0], "y1": e["bbox_inicio"][1],
                "x2": e["bbox_inicio"][2], "y2": e["bbox_inicio"][3],
            },
            "zona_contexto": e["zona_contexto"],
            "n_amostras": e["n_amostras"], "confianca": e["confianca"],
            "origem_validacao": "auditoria",
            # validado_humano=True mantém os crus FORA de toda query "pendente"
            # (validação/contagens) sem precisar filtrar por `principal` no banco.
            # validacao_correto fica null → os leitores de métrica os removem pelo
            # filtro em memória `principal is not False`.
            "validado_humano": True,
            "principal": False,
        })

    CHUNK = 100
    for i in range(0, len(linhas_eventos), CHUNK):
        sb.table("eventos").insert(linhas_eventos[i : i + CHUNK]).execute()

    return video_id, n_auto_validados


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 6 · Análise de produtividade (sugestões agregadas)
# ═════════════════════════════════════════════════════════════════════════
PROMPT_ANALISE = """Você é um consultor sênior de produtividade industrial e engenharia de processos, especialista em Lean Manufacturing.

Você está analisando dados REAIS e AGREGADOS de múltiplos turnos de operação da empresa "{empresa}", no processo "{processo}". Os dados vêm de visão computacional sobre vídeos da operação.

PERGUNTA CENTRAL que você deve responder com suas sugestões:
"Onde podemos melhorar este processo para AUMENTAR A PRODUTIVIDADE, considerando o contexto empresarial e processual disponível?"

Gere de 3 a 6 SUGESTÕES priorizadas por IMPACTO em produtividade. Para cada uma:
- prioridade: "alta" | "media" | "info"  (alta = maior ganho de produtividade)
- area: termo curto (ex: "Gargalo", "Ociosidade", "Fluxo", "Balanceamento", "Layout", "Retrabalho", "Movimentação")
- situacao: o que os DADOS AGREGADOS mostram, com números reais (tempos, %, frequência, nº de vídeos) — 1 a 2 frases
- causa_provavel: hipótese de causa-raiz da perda de produtividade (1-2 frases)
- sugestao: ação concreta e acionável que aumenta a produtividade (1-2 frases)
- impacto_estimado: estimativa do ganho potencial em produtividade, qualitativa ou quantitativa (ex: "redução de ~15% no tempo ocioso", "ganho médio-alto") — seja realista
- comportamentos_relacionados: lista dos labels envolvidos

COMO PENSAR (Lean — 7 desperdícios): espera/ociosidade, transporte e movimentação desnecessária, processamento excessivo, retrabalho/defeitos, estoque/fila, superprodução. Procure onde o TEMPO está sendo gasto sem agregar valor.

REGRAS:
- Baseie-se na BASE AGREGADA (vários turnos), não num único vídeo. Padrões recorrentes em muitos vídeos são mais confiáveis que achados de um vídeo só.
- Priorize comportamentos que consomem MUITO % do tempo observado — é onde mora o maior ganho.
- Quantifique sempre que possível, usando os números do contexto.
- NUNCA mencione pessoas específicas (track_ids). Fale em termos de processo e estação.
- Se houver "descricao_processo_pelo_cliente", use-a como referência do fluxo que AGREGA valor: tempo gasto fora desse fluxo é candidato a desperdício.
- Se houver "conhecimento_adquirido_do_cliente" (pares pergunta→resposta), trate-o como VERDADE confirmada do domínio — peso igual ou maior que a descrição.
- Se houver "labels_descartados_pelo_cliente", ignore esses comportamentos (são falsos positivos).
- Se a base ainda for pequena (poucos vídeos / pouco tempo observado), seja mais cauteloso e sinalize isso no impacto_estimado.
- Seja específico e acionável. Evite frases genéricas como "melhorar o fluxo".

Responda APENAS um JSON no formato:
{{"sugestoes": [{{"prioridade": "...", "area": "...", "situacao": "...", "causa_provavel": "...", "sugestao": "...", "impacto_estimado": "...", "comportamentos_relacionados": [...]}}, ...]}}

DADOS AGREGADOS DO PROCESSO:
"""


def _label_efetivo(e: dict) -> str:
    return e.get("label_corrigido") or e.get("comportamento_label")


# ═════════════════════════════════════════════════════════════════════════
# RELEVÂNCIA (Fase 5) — gate que decide se um evento PENDENTE vai pra fila de
# validação humana. Não-destrutivo: False só ESCONDE da fila (o evento segue no
# banco, nas métricas e na tabela de Eventos). Aperta com a maturidade.
# ═════════════════════════════════════════════════════════════════════════
def _envi(nome: str, padrao: int) -> int:
    try:
        return int(float(os.environ.get(nome, str(padrao))))
    except Exception:
        return padrao


# Knobs (env, com defaults). Maturidade 0–100.
REL_MAT_APRENDIZADO = _envi("KV_REL_MAT_APRENDIZADO", 25)   # < isto: sem gate
REL_MAT_MADURO = _envi("KV_REL_MAT_MADURO", 60)            # >= isto: gate rígido
REL_MIN_AMOSTRAS = _envi("KV_REL_MIN_AMOSTRAS", 2)         # persistência mínima
REL_MIN_DUR_S = _envi("KV_REL_MIN_DUR_S", 6)              # duração mínima (foco)
REL_MIN_DUR_MADURO_S = _envi("KV_REL_MIN_DUR_MADURO_S", 12)  # duração mínima (maduro)
REL_RARO_MAX = _envi("KV_REL_RARO_MAX", 1)               # total_ocorrencias <= isto = raro


def _eh_ruido_label(label: str | None, descricao: str | None) -> bool:
    """True para o ruído que o próprio VLM emite quando não tem certeza."""
    alvo = f"{label or ''} {descricao or ''}".lower()
    return ("não identificad" in alvo) or ("nao identificad" in alvo) or ("ação não" in alvo)


def evento_relevante_para_validacao(
    ev: dict,
    total_ocorrencias: int,
    maturidade: float,
) -> tuple[bool, str]:
    """Decide se um evento PENDENTE merece a fila de validação humana.

    Sinais (todos via campos já presentes + total_ocorrencias do label):
      - ruído ("ação não identificada") → nunca relevante;
      - persistência (n_amostras >= MIN ou duração >= MIN_DUR);
      - raridade (total_ocorrencias <= RARO_MAX = label quase novo);
      - impacto Lean (desperdicio/valor_agregado = foco do negócio).

    Gate por maturidade:
      - Aprendizado (< REL_MAT_APRENDIZADO): sem gate (só corta ruído).
      - Foco (até REL_MAT_MADURO): persistente OU Lean OU raro (vale aprender).
      - Maduro (>=): tira a leniência de raro e sobe o piso de duração.

    Retorna (relevante, motivo). Não-destrutivo: False só esconde da fila.
    """
    label = ev.get("label_corrigido") or ev.get("comportamento_label")
    descricao = ev.get("descricao_bruta")
    if _eh_ruido_label(label, descricao):
        return (False, "ruido")

    n_amostras = int(ev.get("n_amostras") or 1)
    try:
        dur = float(ev.get("tempo_fim_s") or 0) - float(ev.get("tempo_inicio_s") or 0)
    except Exception:
        dur = 0.0
    cat = ev.get("categoria_lean_prevista")
    lean_relevante = cat in ("desperdicio", "valor_agregado")
    raro = (total_ocorrencias or 0) <= REL_RARO_MAX

    # Aprendizado: microgerencia pra aprender o todo (só ruído é cortado).
    if maturidade < REL_MAT_APRENDIZADO:
        return (True, "aprendizado")

    maduro = maturidade >= REL_MAT_MADURO
    piso_dur = REL_MIN_DUR_MADURO_S if maduro else REL_MIN_DUR_S
    persistente = (n_amostras >= REL_MIN_AMOSTRAS) or (dur >= piso_dur)

    if persistente:
        return (True, "persistente")
    if lean_relevante:
        return (True, "lean")
    if (not maduro) and raro:
        # Ainda vale aprender labels novos enquanto o vocabulário não decantou.
        return (True, "raro_aprendendo")
    return (False, "micro")


# ═════════════════════════════════════════════════════════════════════════
# MULTI-CÂMERA — pareamento por nome do segmento (Fase 6) e por evento (Fase 2)
# ═════════════════════════════════════════════════════════════════════════
def _seg_token_nome(nome: str | None) -> str | None:
    """Token de relógio do nome do segmento: 'seg_20260626_155058_roi.mp4'
    → '20260626_155058'. É a CHAVE de pareamento cam1/cam2 ("pelo nome",
    imune a fuso): cam1 e cam2 do mesmo instante têm o mesmo token. None se
    o padrão não casar."""
    if not nome:
        return None
    import re

    m = re.search(r"seg_(\d{8})_(\d{6})", nome)
    return f"{m.group(1)}_{m.group(2)}" if m else None


def _parse_gravado_em_nome(nome: str | None) -> str | None:
    """Extrai o relógio do nome do segmento → ISO 8601 (com TZ local do server).

    Nome típico: '<uuid>_seg_20260626_155058_roi.mp4' → '2026-06-26T15:50:58-03:00'.
    Usado como fallback de `gravado_em` quando o edge não envia explícito (para
    Fases 1/2/3 e display). O PAREAMENTO em si usa `_seg_token_nome` (sem fuso).
    None se o padrão não casar.
    """
    tok = _seg_token_nome(nome)
    if not tok:
        return None
    data, hora = tok.split("_")
    try:
        dt = datetime(
            int(data[0:4]), int(data[4:6]), int(data[6:8]),
            int(hora[0:2]), int(hora[2:4]), int(hora[4:6]),
        )
        # Carimba a TZ local do servidor (mesma origem do edge clock); o
        # importante é que cam1 e cam2 com o mesmo seg_TIMESTAMP gerem o MESMO
        # valor — e geram, pois o parse é idêntico.
        return dt.astimezone().isoformat()
    except Exception:
        return None


def _janela_abs_evento(ev: dict) -> tuple[float, float] | None:
    """Janela absoluta (segundos epoch UTC) de um evento = gravado_em +
    tempo_inicio_s .. gravado_em + tempo_fim_s. None se gravado_em ausente/ruim.

    `ev` precisa ter `gravado_em` (ISO str), `tempo_inicio_s`, `tempo_fim_s`.
    """
    g = ev.get("gravado_em")
    if not g:
        return None
    try:
        from datetime import datetime
        base = datetime.fromisoformat(str(g).replace("Z", "+00:00"))
        t0 = float(ev.get("tempo_inicio_s") or 0)
        t1 = float(ev.get("tempo_fim_s") or 0)
        if t1 < t0:
            t0, t1 = t1, t0
        epoch = base.timestamp()
        return (epoch + t0, epoch + t1)
    except Exception:
        return None


def _iou_temporal(a: tuple[float, float], b: tuple[float, float], tol_s: float) -> float:
    """Interseção-sobre-união de 2 janelas [ini,fim], com tolerância `tol_s`
    que dilata ambas (absorve drift de relógio / fronteira do VLM). 0..1."""
    a0, a1 = a[0] - tol_s, a[1] + tol_s
    b0, b1 = b[0] - tol_s, b[1] + tol_s
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    if inter <= 0:
        return 0.0
    union = (a1 - a0) + (b1 - b0) - inter
    return inter / union if union > 0 else 0.0


def agrupar_eventos_multicamera(
    eventos: list[dict],
    tol_s: float = 2.0,
    iou_min: float = 0.3,
) -> tuple[dict[str, list[dict]], set[str]]:
    """Agrupa eventos que são a MESMA ação vista por câmeras DIFERENTES.

    Critério (par é irmão):
      • cam_id presente e DIFERENTE entre os dois,
      • janelas absolutas (gravado_em + tempos) com IoU temporal >= iou_min.
    Pareamento GULOSO por melhor IoU, no máx. 1 par por câmera-alvo, depois
    união transitiva (generaliza p/ N câmeras). Determinístico: primário do
    grupo = menor cam_id, depois maior confiança, depois menor id.

    Entrada: lista de dicts de evento, cada um com pelo menos `id`, `cam_id`,
    `gravado_em`, `tempo_inicio_s`, `tempo_fim_s`, `confianca`.

    Retorna:
      (grupos, secundarios) onde
        grupos     = { primario_id: [evento_irmao_dict, ...] }  (só p/ grupos >1)
        secundarios = set de ids que NÃO são primário (a remover do topo da fila)
    Eventos sem irmão NÃO aparecem em `grupos` nem em `secundarios` (são solo).
    """
    # Indexa janela por id; só participam os que têm cam_id E janela válida.
    elegiveis: list[dict] = []
    janela: dict[str, tuple[float, float]] = {}
    for ev in eventos:
        if not ev.get("cam_id"):
            continue
        w = _janela_abs_evento(ev)
        if w is None:
            continue
        janela[ev["id"]] = w
        elegiveis.append(ev)

    by_id = {ev["id"]: ev for ev in elegiveis}

    # Candidatos a par: (iou, idA, idB) entre câmeras diferentes, ordenados desc.
    pares: list[tuple[float, str, str]] = []
    n = len(elegiveis)
    for i in range(n):
        a = elegiveis[i]
        for j in range(i + 1, n):
            b = elegiveis[j]
            if a.get("cam_id") == b.get("cam_id"):
                continue  # mesma câmera = ações sequenciais, não irmãos
            iou = _iou_temporal(janela[a["id"]], janela[b["id"]], tol_s)
            if iou >= iou_min:
                pares.append((iou, a["id"], b["id"]))
    pares.sort(key=lambda p: p[0], reverse=True)

    # Matching guloso: cada (evento, câmera-alvo) é casado no máx. 1 vez.
    usado_para_cam: set[tuple[str, str]] = set()  # (id_evento, cam_alvo)
    arestas: list[tuple[str, str]] = []
    for iou, ia, ib in pares:
        ca, cb = by_id[ia]["cam_id"], by_id[ib]["cam_id"]
        if (ia, cb) in usado_para_cam or (ib, ca) in usado_para_cam:
            continue
        usado_para_cam.add((ia, cb))
        usado_para_cam.add((ib, ca))
        arestas.append((ia, ib))

    # União transitiva (union-find leve) sobre as arestas casadas.
    pai: dict[str, str] = {ev["id"]: ev["id"] for ev in elegiveis}

    def _find(x: str) -> str:
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x

    for ia, ib in arestas:
        ra, rb = _find(ia), _find(ib)
        if ra != rb:
            pai[rb] = ra

    componentes: dict[str, list[str]] = {}
    for _id in pai:
        componentes.setdefault(_find(_id), []).append(_id)

    def _chave_primario(ev_id: str) -> tuple:
        ev = by_id[ev_id]
        # menor cam_id, depois maior confiança, depois menor id (determinístico)
        return (str(ev.get("cam_id") or ""), -float(ev.get("confianca") or 0), str(ev_id))

    grupos: dict[str, list[dict]] = {}
    secundarios: set[str] = set()
    for membros in componentes.values():
        if len(membros) < 2:
            continue  # solo
        membros_ordenados = sorted(membros, key=_chave_primario)
        primario = membros_ordenados[0]
        irmaos = membros_ordenados[1:]
        grupos[primario] = [by_id[m] for m in irmaos]
        secundarios.update(irmaos)
    return grupos, secundarios


def consolidar_eventos_para_metricas(
    eventos: list[dict],
    tol_s: float = 2.0,
    iou_min: float = 0.3,
) -> list[dict]:
    """Dedupa grupos multi-câmera para fins de agregação. NÃO persiste.

    Pré-condição: cada evento idealmente tem `cam_id` e `gravado_em` (None
    quando ausente). Eventos sem cam_id OU gravado_em ficam intocados (solo).

    Para cada grupo retornado por `agrupar_eventos_multicamera`:
      - mantém SÓ o primário (remove secundários do output)
      - tempo_inicio_s/tempo_fim_s do primário viram a UNIÃO dos intervalos
        absolutos do grupo, traduzida na linha do tempo local do primário
        (preserva a referência ao vídeo do primário p/ display de frames)
      - duracao_s do primário é atualizada (max(fim_abs) - min(ini_abs))
      - se TODOS os irmãos têm o mesmo _label_efetivo do primário:
        confianca = min(0.99, confianca + 0.05)  (sinal de concordância)

    Os dicts originais NÃO são mutados — devolve cópias rasas dos primários
    afetados (o resto é referência ao input).
    """
    grupos, secundarios = agrupar_eventos_multicamera(eventos, tol_s=tol_s, iou_min=iou_min)
    if not secundarios:
        return list(eventos)

    saida: list[dict] = []
    for ev in eventos:
        if ev["id"] in secundarios:
            continue
        irmaos = grupos.get(ev["id"], [])
        if not irmaos:
            saida.append(ev)
            continue

        # Janela absoluta UNIÃO entre primário + irmãos
        membros = [ev, *irmaos]
        janelas_abs = [_janela_abs_evento(m) for m in membros]
        janelas_validas = [w for w in janelas_abs if w is not None]
        if not janelas_validas:
            saida.append(ev)
            continue
        ini_abs = min(w[0] for w in janelas_validas)
        fim_abs = max(w[1] for w in janelas_validas)
        duracao_uniao = max(0.0, fim_abs - ini_abs)

        # Traduz a janela absoluta de volta na linha local do primário
        # (mantém o vídeo do primário como referência p/ frames).
        w_prim = _janela_abs_evento(ev)
        novo_ini, novo_fim = ev.get("tempo_inicio_s"), ev.get("tempo_fim_s")
        if w_prim is not None:
            delta_ini = ini_abs - w_prim[0]
            delta_fim = fim_abs - w_prim[1]
            base_ini = float(ev.get("tempo_inicio_s") or 0)
            base_fim = float(ev.get("tempo_fim_s") or 0)
            novo_ini = base_ini + delta_ini
            novo_fim = base_fim + delta_fim

        # Concordância de labels: todos os irmãos com mesmo _label_efetivo?
        lbl_prim = _label_efetivo(ev)
        concordam = all(_label_efetivo(s) == lbl_prim for s in irmaos)
        conf_atual = float(ev.get("confianca") or 0.0)
        nova_conf = min(0.99, conf_atual + 0.05) if concordam else conf_atual

        novo = dict(ev)
        novo["tempo_inicio_s"] = novo_ini
        novo["tempo_fim_s"] = novo_fim
        novo["duracao_s"] = round(duracao_uniao, 3)
        novo["confianca"] = round(nova_conf, 3)
        saida.append(novo)

    return saida


def _anexar_meta_video(eventos: list[dict], sb: Client) -> None:
    """In-place: anexa cam_id e gravado_em a cada evento.

    Faz UM único SELECT em `videos` por id ∈ {video_id dos eventos} e
    enriquece cada evento. No-op quando os eventos já têm os 2 campos
    preenchidos (idempotente). Defensivo: nunca levanta — falha vira logs
    warning e mantém os eventos como estavam.
    """
    if not eventos:
        return
    faltando = [e for e in eventos if "cam_id" not in e or "gravado_em" not in e]
    if not faltando:
        return
    vids = sorted({e.get("video_id") for e in faltando if e.get("video_id")})
    if not vids:
        for e in faltando:
            e.setdefault("cam_id", None)
            e.setdefault("gravado_em", None)
        return
    try:
        rv = (
            sb.table("videos")
            .select("id, cam_id, gravado_em")
            .in_("id", list(vids))
            .execute()
            .data
        ) or []
        meta = {v["id"]: v for v in rv}
    except Exception as e:
        log.warning(f"_anexar_meta_video: falha ao buscar videos ({e}) — segue sem dedup")
        meta = {}
    for ev in faltando:
        mv = meta.get(ev.get("video_id"), {})
        ev.setdefault("cam_id", mv.get("cam_id"))
        ev.setdefault("gravado_em", mv.get("gravado_em"))


def montar_contexto_agregado(
    sb: Client,
    empresa: str,
    processo: str,
    catalogo: dict[str, str] | None = None,
    descricao_processo: str = "",
    memoria: dict | None = None,
    conhecimento_adquirido_texto: str = "",
    video_recem_processado: dict | None = None,
) -> dict:
    todos_eventos = (
        sb.table("eventos")
        .select(
            "video_id, comportamento_label, label_corrigido, tempo_inicio_s, "
            "tempo_fim_s, pessoa_track_id, validacao_correto, validado_humano, principal"
        )
        .eq("empresa", empresa)
        .eq("processo", processo)
        .limit(50000)
        .execute()
        .data
    )
    # Fase 16: só os PRINCIPAIS (1/min); crus de auditoria (principal=False) fora.
    base = [
        e for e in todos_eventos
        if e.get("validacao_correto") is not False and e.get("principal") is not False
    ]

    videos_ctx = (
        sb.table("videos")
        .select("id, duracao_s, processado_em")
        .eq("empresa", empresa)
        .eq("processo", processo)
        .execute()
        .data
    )
    n_videos_ctx = len(videos_ctx)
    duracao_total_ctx = sum((v.get("duracao_s") or 0) for v in videos_ctx)

    catalogo = catalogo or {}
    if not catalogo:
        comps = (
            sb.table("comportamentos")
            .select("label, descricao")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .execute()
            .data
        )
        catalogo = {c["label"]: c.get("descricao", "") for c in comps}

    agg: dict = defaultdict(
        lambda: {"ocorrencias": 0, "duracao_total_s": 0.0, "videos": set(), "pessoas": set()}
    )
    for e in base:
        lbl = _label_efetivo(e)
        dur = (e.get("tempo_fim_s") or 0) - (e.get("tempo_inicio_s") or 0)
        a = agg[lbl]
        a["ocorrencias"] += 1
        a["duracao_total_s"] += max(0, dur)
        a["videos"].add(e.get("video_id"))
        a["pessoas"].add(e.get("pessoa_track_id"))

    resumo_agregado = []
    for lbl, a in sorted(agg.items(), key=lambda kv: kv[1]["duracao_total_s"], reverse=True):
        resumo_agregado.append(
            {
                "comportamento": lbl,
                "descricao": catalogo.get(lbl, lbl),
                "ocorrencias_totais": a["ocorrencias"],
                "duracao_total_s": round(a["duracao_total_s"], 1),
                "duracao_media_s": round(a["duracao_total_s"] / max(1, a["ocorrencias"]), 1),
                "pct_do_tempo_observado": round(
                    a["duracao_total_s"] / max(1, duracao_total_ctx) * 100, 1
                ),
                "aparece_em_n_videos": len(a["videos"]),
            }
        )

    seqs: dict = defaultdict(list)
    for e in base:
        seqs[(e.get("video_id"), e.get("pessoa_track_id"))].append(
            (e.get("tempo_inicio_s") or 0, _label_efetivo(e))
        )
    contagem_transicoes: Counter = Counter()
    for _, lista in seqs.items():
        lista.sort()
        labels = [l for _, l in lista]
        for x, y in zip(labels, labels[1:]):
            if x != y:
                contagem_transicoes[(x, y)] += 1
    transicoes_top = contagem_transicoes.most_common(12)

    n_validados = sum(1 for e in base if e.get("validado_humano"))
    pct_validado = round(n_validados / max(1, len(base)) * 100, 1)

    contexto: dict = {
        "empresa": empresa,
        "processo": processo,
        "base_agregada": {
            "videos_analisados": n_videos_ctx,
            "tempo_total_observado_s": round(duracao_total_ctx, 1),
            "tempo_total_observado_min": round(duracao_total_ctx / 60, 1),
            "eventos_considerados": len(base),
            "pct_base_validada_por_humano": pct_validado,
        },
        "distribuicao_comportamentos": resumo_agregado,
        "transicoes_dominantes": [
            {"de": a, "para": b, "vezes": n} for (a, b), n in transicoes_top
        ],
    }
    if video_recem_processado:
        contexto["video_recem_processado"] = video_recem_processado
    if descricao_processo:
        contexto["descricao_processo_pelo_cliente"] = descricao_processo
    if conhecimento_adquirido_texto:
        contexto["conhecimento_adquirido_do_cliente"] = conhecimento_adquirido_texto
    if memoria and memoria.get("descartados"):
        contexto["labels_descartados_pelo_cliente"] = memoria["descartados"]

    return contexto


def etapa_gerar_sugestoes(
    sb: Client,
    groq_client: Groq,
    empresa: str,
    processo: str,
    video_id: str,
    contexto_analise: dict,
) -> list[dict]:
    prompt = PROMPT_ANALISE.format(empresa=empresa, processo=processo)
    resposta = groq_text_call(
        groq_client,
        prompt + json.dumps(contexto_analise, indent=2, ensure_ascii=False),
        model=GROQ_MODEL_ANALISE,
        json_mode=True,
        max_tokens=4000,   # Fase 14: fora do Free Tier — mais espaço p/ qualidade
        temperatura=0.3,
    )
    sugestoes = json.loads(resposta)["sugestoes"]

    # Para sinalizar reincidência: tokens das sugestões que o gestor já marcou
    # como REALIZADA neste processo. Se uma nova sugestão tem texto parecido
    # (Jaccard ≥ 0.5) com uma já realizada, é porque a ação não foi cumprida —
    # marcamos voltou_apos_realizada=true e o painel destaca isso pro gestor.
    realizadas_tokens: list[set[str]] = []
    try:
        rea = (
            sb.table("sugestoes_melhoria")
            .select("sugestao")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .eq("status", "realizada")
            .limit(200)
            .execute()
            .data
        ) or []
        realizadas_tokens = [_normalizar_pergunta(r.get("sugestao") or "") for r in rea]
    except Exception as e:
        log.warning(f"Não foi possível checar sugestões já realizadas: {e}")

    linhas_sug = []
    for s in sugestoes:
        texto = s.get("sugestao", "") or ""
        voltou = bool(realizadas_tokens) and _eh_duplicada(texto, realizadas_tokens, limiar=0.5)
        linhas_sug.append(
            {
                "video_id": video_id,
                "empresa": empresa,
                "processo": processo,
                "prioridade": s.get("prioridade", "info"),
                "area": s.get("area", ""),
                "situacao": s.get("situacao", ""),
                "causa_provavel": s.get("causa_provavel", ""),
                "sugestao": texto,
                "impacto_estimado": s.get("impacto_estimado", ""),
                "eventos_relacionados": {
                    "comportamentos": s.get("comportamentos_relacionados", [])
                },
                "voltou_apos_realizada": voltou,
            }
        )
    if linhas_sug:
        sb.table("sugestoes_melhoria").insert(linhas_sug).execute()
    return sugestoes


# ═════════════════════════════════════════════════════════════════════════
# CLASSIFICAÇÃO LEAN — IA classifica cada comportamento em
# valor_agregado | apoio | desperdicio (Lean / análise de valor).
#
# Esta é a SEGUNDA MEMÓRIA da plataforma. Distinta da memória de label
# (carregar_memoria_do_negocio). Aqui aprendemos o VALOR de cada
# comportamento a partir das decisões humanas do gestor.
#
# Origens em comportamentos.categoria_lean_origem:
#   'humano'   → o gestor classificou manualmente. INVIOLÁVEL pela IA.
#   'aprendido'→ herdado de uma decisão humana anterior por match exato
#                de label (mesma empresa). Alta confiança, sem LLM.
#   'ia'       → palpite do LLM para um label sem precedente humano,
#                guiado pelos exemplos do cliente quando disponíveis.
#
# Escopo: a memória de categoria é por EMPRESA (o valor de "andar"
# costuma ser estável em toda a empresa), com preferência para a
# decisão do PRÓPRIO processo quando houver conflito.
# ═════════════════════════════════════════════════════════════════════════
CATEGORIAS_LEAN_VALIDAS = {"valor_agregado", "apoio", "desperdicio"}


def carregar_memoria_categoria(
    sb: Client, empresa: str, processo: str | None = None
) -> dict:
    """Lê as decisões humanas de categoria Lean da empresa.

    Retorna:
      - 'mapa_humano':       label → categoria (vencedora). Preferência
                              para a decisão do `processo` quando há
                              conflito com outros processos da empresa.
      - 'exemplos_por_cat':  {categoria: [labels...]} para o bloco
                              de exemplos do prompt (até ~10 por categoria).
      - 'n_decisoes':        contagem total de decisões humanas (para log).
    """
    memoria = {"mapa_humano": {}, "exemplos_por_cat": {}, "n_decisoes": 0}
    try:
        r = (
            sb.table("comportamentos")
            .select("label, categoria_lean, categoria_lean_origem, processo")
            .eq("empresa", empresa)
            .eq("categoria_lean_origem", "humano")
            .limit(2000)
            .execute()
        )
        humanos = r.data or []
    except Exception as e:
        log.warning(f"Lean: falha ao carregar memória de categoria: {e}")
        return memoria

    if not humanos:
        return memoria

    # Conflito entre processos: vence a decisão do processo atual; fora
    # disso, a categoria mais frequente para aquele label entre processos
    # da empresa.
    por_label_local: dict[str, str] = {}
    por_label_outros: dict[str, Counter] = {}
    for h in humanos:
        label = (h.get("label") or "").strip()
        cat = (h.get("categoria_lean") or "").strip().lower()
        if not label or cat not in CATEGORIAS_LEAN_VALIDAS:
            continue
        if processo and h.get("processo") == processo:
            por_label_local[label] = cat
        else:
            por_label_outros.setdefault(label, Counter())[cat] += 1

    mapa: dict[str, str] = {}
    for lbl, ctr in por_label_outros.items():
        mapa[lbl] = ctr.most_common(1)[0][0]
    mapa.update(por_label_local)  # local sobrescreve

    exemplos: dict[str, list[str]] = {c: [] for c in CATEGORIAS_LEAN_VALIDAS}
    for lbl, cat in mapa.items():
        if len(exemplos[cat]) < 12:
            exemplos[cat].append(lbl)

    memoria["mapa_humano"] = mapa
    memoria["exemplos_por_cat"] = exemplos
    memoria["n_decisoes"] = len(mapa)
    log.info(
        f"Lean memória categoria · {empresa}: {len(mapa)} labels com decisão humana "
        f"(VA:{len(exemplos['valor_agregado'])}, Apoio:{len(exemplos['apoio'])}, "
        f"Desp:{len(exemplos['desperdicio'])})"
    )
    return memoria


def construir_bloco_categoria_aprendida(memoria_categoria: dict) -> str:
    """Bloco de texto pro prompt: exemplos das decisões do gestor para
    cada categoria. Vazio se não houver decisões."""
    exemplos = memoria_categoria.get("exemplos_por_cat") or {}
    if not any(exemplos.values()):
        return ""
    linhas = [
        "CRITÉRIO DE CATEGORIA DESTE CLIENTE (decisões anteriores do gestor — USE como referência: comportamentos semanticamente parecidos a estes provavelmente caem na MESMA categoria):"
    ]
    rotulos = {
        "valor_agregado": "VALOR AGREGADO (o cliente considera estas como valor):",
        "apoio": "APOIO (o cliente considera estas como apoio):",
        "desperdicio": "DESPERDÍCIO (o cliente considera estas como desperdício):",
    }
    for cat in ("valor_agregado", "apoio", "desperdicio"):
        lst = exemplos.get(cat) or []
        if not lst:
            continue
        linhas.append("")
        linhas.append(rotulos[cat])
        for lbl in lst:
            linhas.append(f"  - {lbl}")
    linhas.append("")
    linhas.append(
        "REGRA DURA: se o comportamento a classificar é semanticamente equivalente a um dos exemplos acima, USE a mesma categoria do exemplo. Não invente uma categoria diferente."
    )
    return "\n".join(linhas) + "\n\n"


PROMPT_CLASSIFICAR_LEAN = """Você é um especialista em Lean Manufacturing classificando comportamentos observados na operação da empresa "{empresa}" no processo "{processo}".

Classifique CADA comportamento abaixo em UMA destas três categorias:
- "valor_agregado": a atividade transforma o produto/serviço de modo que o cliente final pagaria por ela (ex.: montar, soldar, embalar a peça que será entregue, executar o serviço contratado).
- "apoio": atividade necessária para que o valor agregado aconteça, mas que por si só não agrega valor ao cliente (ex.: conferir, registrar, organizar, abastecer, preparar máquina, comunicar).
- "desperdicio": atividade que consome tempo sem necessidade — espera, ociosidade, deslocamento, retrabalho, movimentação excessiva, busca por itens.

{bloco_dominio}{bloco_categoria}REGRAS:
- Decida pela ação MAIS PROVÁVEL dado o vocabulário e o contexto de domínio acima. Se a descrição do processo / conhecimento adquirido descrevem a ação como obrigatória/produtiva, ela tende a ser "valor_agregado" ou "apoio".
- "acao_indefinida" sempre vira "apoio" (sem informação suficiente para chamar de desperdício).
- Comportamentos como "operar_computador" geralmente são "apoio" (registro, conferência), a menos que a descrição do processo deixe claro que digitar É o trabalho.
- Comportamentos como "andar", "esperar", "ocioso", "parado", "buscar" tendem a "desperdicio".
- PRIORIDADE: se o "critério de categoria deste cliente" (acima) cobre o caso, alinhe a essa decisão — esse é o critério REAL do cliente.
- Responda APENAS um JSON estrito (categoria SEM espaços, snake_case):
{{"classificacoes": [{{"label": "operar_computador", "categoria": "apoio"}}, ...]}}

COMPORTAMENTOS A CLASSIFICAR:
{lista_comportamentos}
"""


def classificar_comportamentos_lean(
    sb: Client,
    groq_client: Groq,
    empresa: str,
    processo: str,
    *,
    descricao_processo: str = "",
    conhecimento_adquirido: str = "",
    reclassificar_ia: bool = False,
) -> int:
    """Classifica em batch os comportamentos do contexto que ainda não têm
    `categoria_lean` definitiva.

    Estratégia em 2 níveis:
      1) Match exato por label na memória humana da empresa (sem LLM) →
         categoria 'aprendido'. Decisão do próprio processo prevalece.
      2) Para os restantes (sem precedente humano), uma única chamada ao
         LLM com o bloco de exemplos do cliente → categoria 'ia'.

    Nunca toca em quem tem origem='humano'. Não-fatal.
    Retorna total de comportamentos atualizados.
    """
    try:
        r = (
            sb.table("comportamentos")
            .select("id, label, descricao, categoria_lean, categoria_lean_origem")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .limit(500)
            .execute()
        )
        todos = r.data or []
    except Exception as e:
        log.warning(f"Lean: falha ao carregar comportamentos: {e}")
        return 0

    candidatos = []
    for c in todos:
        origem = c.get("categoria_lean_origem")
        if origem == "humano":
            continue  # inviolável
        # 'aprendido' e 'ia' são candidatos a refinamento se reclassificar_ia
        if c.get("categoria_lean") and origem in ("ia", "aprendido") and not reclassificar_ia:
            continue
        candidatos.append(c)

    if not candidatos:
        return 0

    # ─── Nível 1: match exato pela memória humana (escopo empresa) ────
    mem_cat = carregar_memoria_categoria(sb, empresa, processo)
    mapa_humano: dict[str, str] = mem_cat.get("mapa_humano") or {}

    aprendidos = 0
    para_llm = []
    for c in candidatos:
        lbl = c.get("label") or ""
        cat = mapa_humano.get(lbl)
        if cat in CATEGORIAS_LEAN_VALIDAS:
            try:
                sb.table("comportamentos").update(
                    {"categoria_lean": cat, "categoria_lean_origem": "aprendido"}
                ).eq("id", c["id"]).execute()
                aprendidos += 1
            except Exception as e:
                log.warning(f"Lean: falha ao aplicar match aprendido em {lbl}: {e}")
        else:
            para_llm.append(c)

    if aprendidos:
        log.info(f"Lean: {aprendidos} comportamento(s) herdaram categoria humana por match exato.")

    if not para_llm:
        return aprendidos

    # ─── Nível 2: LLM, guiado pelos exemplos do cliente ───────────────
    bloco_dominio = construir_bloco_dominio(descricao_processo or "", conhecimento_adquirido or "")
    if not bloco_dominio.strip():
        bloco_dominio = "(o cliente não forneceu descrição nem respondeu perguntas — use convenções de Lean para decidir)\n\n"
    bloco_categoria = construir_bloco_categoria_aprendida(mem_cat)

    lista_txt = "\n".join(
        f'- label="{c["label"]}" · descricao="{(c.get("descricao") or "").strip()}"'
        for c in para_llm
    )
    prompt = PROMPT_CLASSIFICAR_LEAN.format(
        empresa=empresa,
        processo=processo,
        bloco_dominio=bloco_dominio.rstrip() + "\n\n" if bloco_dominio.strip() else "",
        bloco_categoria=bloco_categoria,
        lista_comportamentos=lista_txt,
    )

    try:
        resposta = groq_text_call(
            groq_client,
            prompt,
            model=GROQ_MODEL_ANALISE,
            json_mode=True,
            max_tokens=1200,
            temperatura=0.1,
        )
        dados = json.loads(resposta)
        classifs = dados.get("classificacoes") or []
    except Exception as e:
        log.warning(f"Lean: falha ao classificar via LLM: {e}")
        return aprendidos

    por_label = {c["label"]: c["id"] for c in para_llm}
    atualizados_ia = 0
    for item in classifs:
        if not isinstance(item, dict):
            continue
        label = (item.get("label") or "").strip()
        cat = (item.get("categoria") or "").strip().lower()
        if label not in por_label:
            continue
        if cat not in CATEGORIAS_LEAN_VALIDAS:
            log.info(f"Lean: categoria inválida ignorada ({label}={cat!r})")
            continue
        try:
            sb.table("comportamentos").update(
                {"categoria_lean": cat, "categoria_lean_origem": "ia"}
            ).eq("id", por_label[label]).execute()
            atualizados_ia += 1
        except Exception as e:
            log.warning(f"Lean: falha ao atualizar {label}: {e}")

    log.info(
        f"Lean: {empresa}/{processo} · {aprendidos} aprendidos (match humano) "
        f"+ {atualizados_ia} via IA (de {len(para_llm)} candidatos novos)."
    )
    return aprendidos + atualizados_ia


# ═════════════════════════════════════════════════════════════════════════
# ONBOARDING — conversa adaptativa para colher a descrição inicial do
# processo. O frontend chama /onboarding/proxima-pergunta a cada turno
# mandando o histórico. Quando a IA julga ter cobertura suficiente,
# devolve completo=true + descricao_consolidada (vai pra contexto_processo).
# ═════════════════════════════════════════════════════════════════════════
PROMPT_ONBOARDING = """Você está conduzindo o ONBOARDING de um novo processo industrial chamado "{processo}" para a empresa "{empresa}". Sua missão é COLHER a descrição do processo do gestor através de uma conversa curta, natural e adaptativa.

Você precisa COBRIR durante a conversa, na ordem natural, TODOS estes pontos:
  1. ÁREA / nicho da indústria (qual o setor: estamparia, embalagem, picking, logística, frigorífico, etc.).
  2. INÍCIO do processo: como começa, o que dispara a operação, qual o INPUT (matéria-prima, peça, pacote, pedido).
  3. PASSOS principais: o que precisa ser feito, em que ordem, em quais estações.
  4. OUTPUT: o que sai pronto ao final, como o operador sabe que terminou.
  5. PROBLEMAS RECORRENTES: o que costuma dar errado, onde trava.
  6. EXCEÇÕES: situações fora do padrão que mudam o fluxo.

REGRAS DURAS:
- Faça UMA pergunta por vez. CURTA (1 frase), em linguagem de chão de fábrica, sem jargão técnico/Lean/IA.
- ADAPTE a próxima pergunta ao que o gestor já respondeu. NÃO repita, NÃO crie variantes de perguntas já feitas.
- Se ainda não sabe a ÁREA, comece perguntando por ela. Caso contrário siga a ordem natural.
- Gere SEMPRE 3 "respostas_rapidas" (1 a 5 palavras cada), PLAUSÍVEIS e específicas a essa pergunta + a esse processo. PROIBIDO devolver Sim/Não/Às vezes como padrão, a menos que a pergunta seja genuinamente binária. As 3 devem cobrir os cenários mais prováveis que esse gestor responderia.
- Quando já tiver informação suficiente para descrever bem o processo (cobriu razoavelmente os 6 pontos acima — ÁREA + INPUT + PASSOS + OUTPUT no mínimo; PROBLEMAS e EXCEÇÕES podem ter sido "não há"), marque "completo": true e devolva "descricao_consolidada": um texto profissional de 2 a 4 parágrafos em português do Brasil, sintetizando TUDO que o gestor contou, na voz neutra ("os operadores fazem X", "o processo começa com Y"). NUNCA invente o que não foi dito.

CONTEXTO DA CONVERSA ATÉ AQUI:
Empresa: {empresa}
Processo: {processo}
Área já informada pelo gestor: {area_inicial}

Histórico (pergunta → resposta):
{bloco_historico}

Responda APENAS um JSON estrito. Dois formatos possíveis:
- Se ainda precisa perguntar mais:
  {{"completo": false, "pergunta": "...", "motivo": "1 frase curta — por que essa pergunta importa", "respostas_rapidas": ["...", "...", "..."]}}
- Se já tem o suficiente para consolidar:
  {{"completo": true, "descricao_consolidada": "..."}}
"""


def gerar_pergunta_onboarding(
    groq_client: Groq,
    empresa: str,
    processo: str,
    area_inicial: str | None,
    historico: list[dict],
) -> dict:
    """Devolve a próxima pergunta do onboarding adaptativo ou a descrição
    consolidada quando a IA julga ter cobertura suficiente."""
    if historico:
        bloco = "\n".join(
            f"P: {(h.get('pergunta') or '').strip()}\nR: {(h.get('resposta') or '').strip()}\n"
            for h in historico
            if (h.get("pergunta") or "").strip() and (h.get("resposta") or "").strip()
        ) or "(nenhuma resposta válida ainda)"
    else:
        bloco = "(nenhuma pergunta feita ainda — comece pela ÁREA do processo, se ainda não informada)"

    prompt = PROMPT_ONBOARDING.format(
        empresa=empresa,
        processo=processo,
        area_inicial=(area_inicial or "—"),
        bloco_historico=bloco,
    )
    resposta = groq_text_call(
        groq_client,
        prompt,
        model=GROQ_MODEL_ANALISE,
        json_mode=True,
        max_tokens=2200,
        temperatura=0.4,
    )
    dados = json.loads(resposta)

    if dados.get("completo"):
        descricao = (dados.get("descricao_consolidada") or "").strip()
        if not descricao:
            # Salvaguarda: se a IA disse completo=true mas não mandou texto,
            # devolvemos uma pergunta fallback ao invés de quebrar.
            return {
                "completo": False,
                "pergunta": "Tem mais alguma coisa importante do processo que eu deveria saber?",
                "motivo": "Última checagem antes de consolidar a descrição.",
                "respostas_rapidas": ["Não, pode consolidar", "Sim, deixa eu contar", "Acho que cobrimos tudo"],
            }
        return {"completo": True, "descricao_consolidada": descricao}

    # Sanitiza as 3 respostas rápidas (mesmo padrão de gerar_perguntas_processo).
    rapidas_raw = dados.get("respostas_rapidas") or []
    rapidas: list[str] = []
    vistas: set[str] = set()
    if isinstance(rapidas_raw, list):
        for r in rapidas_raw:
            if not isinstance(r, str):
                continue
            s = " ".join(r.split())
            if not (2 <= len(s) <= 60):
                continue
            chave = s.lower()
            if chave in vistas:
                continue
            vistas.add(chave)
            rapidas.append(s)
            if len(rapidas) == 3:
                break

    return {
        "completo": False,
        "pergunta": (dados.get("pergunta") or "").strip(),
        "motivo": (dados.get("motivo") or "").strip(),
        "respostas_rapidas": rapidas if len(rapidas) == 3 else None,
    }


# ═════════════════════════════════════════════════════════════════════════
# PERGUNTAS PROATIVAS — a IA pede esclarecimentos ao cliente, e cada
# resposta vira contexto de domínio nos prompts seguintes.
# ═════════════════════════════════════════════════════════════════════════
PROMPT_PERGUNTAS = """Você é a IA de análise de processos industriais do Kalidash Vision, observando a empresa "{empresa}" no processo "{processo}". Você já analisou alguns vídeos da operação. Sua tarefa AGORA não é gerar sugestões de melhoria (isso já foi feito em outra etapa) — é IDENTIFICAR LACUNAS no seu próprio entendimento da operação e formular PERGUNTAS GENUÍNAS ao cliente que, se respondidas, te permitiriam classificar e analisar com muito mais precisão a partir do próximo vídeo.

REGRAS DURAS:
- Você está LIVRE para perguntar o que quiser. NÃO use um catálogo pré-fabricado. As perguntas têm que nascer de incertezas REAIS nos dados abaixo.
- NÃO repita nenhuma pergunta que você já fez antes (lista mais abaixo) e NÃO crie variantes dela.
- NÃO pergunte o que a "descrição do processo" e o "conhecimento já adquirido" já respondem.
- Cada pergunta deve ser CURTA (1 frase), ESPECÍFICA e em linguagem de chão de fábrica. Nada de termos de IA, estatística ou Lean.
- Priorize perguntas que ajudam a:
    (a) DESAMBIGUAR comportamentos parecidos (mesmo objeto/ação descritos de formas diferentes; ou labels distintos que talvez sejam o mesmo);
    (b) NOMEAR corretamente ações que ficaram como "acao_indefinida" ou de baixa confiança;
    (c) Entender ORDEM e OBRIGATORIEDADE de passos (ex.: "é obrigatório conferir antes de embalar?");
    (d) Distinguir o que AGREGA VALOR do que é apoio / verificação / espera.
- Gere NO MÁXIMO {max_perguntas} perguntas. Se não há lacuna genuína, devolva uma lista VAZIA. NÃO invente perguntas para preencher cota.
- NÃO comente o desempenho de pessoas; foque no processo.

PARA CADA PERGUNTA, gere também EXATAMENTE 3 "respostas_rapidas" — opções curtas (1 a 5 palavras) que o gestor da fábrica pode tocar para responder com 1 clique. Regras das respostas_rapidas:
  - DEVEM ser plausíveis E adaptadas àquela pergunta específica e a esse processo. PROIBIDO devolver só {{"Sim","Não","Às vezes"}} como padrão — só use Sim/Não se a pergunta for genuinamente binária; caso contrário, use opções com conteúdo (ex.: "Sempre antes da embalagem" / "Depende do produto" / "Só quando há lote misto").
  - Entre as 3 opções deve estar a resposta mais provável que o gestor daria, junto com as 2 alternativas razoáveis seguintes — cobrindo os cenários reais. Em conjunto, as 3 devem dar conta de >80% das respostas esperadas.
  - Linguagem do chão de fábrica, sem jargão de IA/Lean. Cada opção é uma resposta CURTA E COMPLETA por si só (vai ser usada como texto da resposta do cliente).

CONTEXTO QUE VOCÊ JÁ SABE DO PROCESSO:
{bloco_dominio}

LACUNAS DETECTADAS NOS DADOS:
{bloco_lacunas}

PERGUNTAS JÁ FEITAS (NÃO REPITA, NÃO CRIE VARIANTES):
{bloco_perguntas_existentes}

Responda APENAS um JSON estrito:
{{"perguntas": [
  {{"pergunta": "...",
   "motivo": "1 frase explicando por que essa pergunta importa pra análise",
   "comportamentos_relacionados": ["label_1", "label_2"],
   "respostas_rapidas": ["...", "...", "..."]}},
  ...
]}}
"""


_STOPWORDS_PT = set(
    """a o e ou de da do das dos para por em no na nos nas com sem como esse essa esses essas
    isso isto aquele aquela aqueles aquelas que quem qual quais quando onde se já não nao um
    uma uns umas é eh são sao ser está esta estão estao tem têm tinha tinham vai vão ele ela
    eles elas você voce vocês voces o que""".split()
)


def _normalizar_pergunta(texto: str) -> set[str]:
    """Tokeniza para comparação por sobreposição (Jaccard)."""
    import re

    txt = (texto or "").lower()
    txt = re.sub(r"[^a-záàâãéêíóôõúüç0-9\s]", " ", txt)
    toks = [t for t in txt.split() if t and t not in _STOPWORDS_PT and len(t) > 2]
    return set(toks)


def _eh_duplicada(nova: str, existentes_tokens: list[set[str]], limiar: float = 0.6) -> bool:
    a = _normalizar_pergunta(nova)
    if not a:
        return True  # texto sem conteúdo útil
    for b in existentes_tokens:
        if not b:
            continue
        inter = len(a & b)
        union = len(a | b)
        if union and inter / union >= limiar:
            return True
    return False


def _montar_bloco_lacunas(
    observacoes_brutas: list[dict] | None,
    catalogo: dict[str, str] | None,
    contexto_agregado: dict | None,
    memoria: dict | None,
) -> str:
    """Bloco em texto plano com os indícios mais úteis para a IA perguntar."""
    linhas: list[str] = []

    # Top descrições brutas (texto cru que o VLM gerou)
    if observacoes_brutas:
        contagem = Counter(o["descricao"] for o in observacoes_brutas if o.get("descricao"))
        if contagem:
            linhas.append("Descrições brutas mais frequentes vistas nas amostras (texto cru, antes da clusterização):")
            for desc, n in contagem.most_common(12):
                linhas.append(f"  - {n}× \"{desc}\"")
            linhas.append("")

    # Catálogo final de comportamentos detectados, com % do tempo agregado
    if contexto_agregado and contexto_agregado.get("distribuicao_comportamentos"):
        linhas.append("Comportamentos detectados (% do tempo observado · ocorrências):")
        for c in contexto_agregado["distribuicao_comportamentos"][:12]:
            linhas.append(
                f"  - {c['comportamento']} ({c.get('descricao','')}) — "
                f"{c.get('pct_do_tempo_observado', 0)}% · {c.get('ocorrencias_totais', 0)} ocorrências"
            )
        linhas.append("")

    # Ações que caíram em acao_indefinida e suas descrições brutas
    if observacoes_brutas:
        indef = [
            o["descricao"]
            for o in observacoes_brutas
            if o.get("label") == "acao_indefinida" or o.get("descricao") == "ação não identificada"
        ]
        if indef:
            linhas.append(
                "Ações que o sistema NÃO conseguiu nomear com clareza (acao_indefinida) — descrições brutas associadas:"
            )
            for desc, n in Counter(indef).most_common(8):
                linhas.append(f"  - {n}× \"{desc}\"")
            linhas.append("")

    # Transições mais comuns (fluxo real)
    if contexto_agregado and contexto_agregado.get("transicoes_dominantes"):
        linhas.append("Sequências mais comuns observadas (comportamento A → comportamento B):")
        for t in contexto_agregado["transicoes_dominantes"][:8]:
            linhas.append(f"  - {t['de']} → {t['para']} ({t['vezes']}×)")
        linhas.append("")

    # Vocabulário com poucas confirmações (pode estar em formação)
    if memoria and memoria.get("vocabulario"):
        em_formacao = [
            v for v in memoria["vocabulario"]
            if v.get("n_confirmacoes", 0) < 2
        ]
        if em_formacao:
            linhas.append("Comportamentos com poucas confirmações ainda (vocabulário em formação):")
            for v in em_formacao[:8]:
                linhas.append(f"  - {v['label']}: {v.get('descricao', '')} ({v.get('n_confirmacoes', 0)}× confirmado)")
            linhas.append("")

    if not linhas:
        return "(nenhum sinal forte de lacuna no momento)"
    return "\n".join(linhas)


def gerar_pergunta_divergencia_camera(
    sb: Client,
    empresa: str,
    processo: str,
    *,
    janela_horas: int = 24,
    max_perguntas: int = 2,
) -> int:
    """Detecta grupos multi-câmera com label DIVERGENTE e enfileira UMA
    pergunta determinística por par de labels (A, B). Zero custo Groq.

    Varredura: eventos pendentes (validado_humano is null) das últimas
    `janela_horas` do processo, com cam_id e gravado_em não-nulos. Roda
    `agrupar_eventos_multicamera`; para cada grupo cujos labels efetivos
    divergem entre os irmãos, monta UMA pergunta — texto template fixo,
    `respostas_rapidas=[A, B, 'ambos descrevem ações diferentes']`.

    Dedup idempotente: prefixo estável `[multicam:A↔B]` no campo
    `pergunta` (signatura = sorted([A,B])). Checa em qualquer status
    (pendente/respondida/dispensada) antes de inserir. Limita a
    `max_perguntas` por execução.

    Retorna o número de perguntas enfileiradas. Defensivo: nunca
    levanta — falhas viram log.warning e devolve 0.
    """
    try:
        from datetime import datetime, timedelta, timezone

        corte = (datetime.now(timezone.utc) - timedelta(hours=janela_horas)).isoformat()
        evs = (
            sb.table("eventos")
            .select(
                "id, video_id, comportamento_label, label_corrigido, "
                "tempo_inicio_s, tempo_fim_s, confianca, validado_humano, "
                "validacao_correto, criado_em"
            )
            .eq("empresa", empresa)
            .eq("processo", processo)
            .is_("validado_humano", "null")
            .gte("criado_em", corte)
            .limit(2000)
            .execute()
            .data
        ) or []
        if not evs:
            return 0

        _anexar_meta_video(evs, sb)
        grupos, _ = agrupar_eventos_multicamera(evs)
        if not grupos:
            return 0

        # Coleta pares (A↔B) divergentes — uma signatura por par único.
        existentes = (
            sb.table("perguntas_processo")
            .select("pergunta")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .limit(2000)
            .execute()
            .data
        ) or []
        ja_feitas: set[str] = set()
        for x in existentes:
            t = x.get("pergunta") or ""
            if t.startswith("[multicam:") and "]" in t:
                ja_feitas.add(t[: t.index("]") + 1])  # ex.: '[multicam:A↔B]'

        candidatos: list[tuple[str, str, dict, dict]] = []
        vistos_par: set[tuple[str, str]] = set()
        for primario_id, irmaos in grupos.items():
            primario = next((e for e in evs if e["id"] == primario_id), None)
            if not primario:
                continue
            lbl_p = _label_efetivo(primario)
            for irmao in irmaos:
                lbl_i = _label_efetivo(irmao)
                if lbl_i == lbl_p:
                    continue  # concordam — não é divergência
                par = tuple(sorted([lbl_p, lbl_i]))
                if par in vistos_par:
                    continue
                vistos_par.add(par)
                sig = f"[multicam:{par[0]}↔{par[1]}]"
                if sig in ja_feitas:
                    continue
                candidatos.append((par[0], par[1], primario, irmao))
                if len(candidatos) >= max_perguntas:
                    break
            if len(candidatos) >= max_perguntas:
                break

        if not candidatos:
            return 0

        agora = datetime.now(timezone.utc).isoformat()
        novas: list[dict] = []
        for A, B, _prim, _irm in candidatos:
            pergunta_texto = (
                f'[multicam:{A}↔{B}] Em uma mesma ação, uma câmera viu como '
                f'"{A}" e a outra como "{B}". Qual descreve melhor essa operação?'
            )
            motivo = (
                "Quando duas câmeras divergem no rótulo, costuma ser ângulo "
                "desfavorável ou vocabulário com nomes próximos pro mesmo "
                "movimento — sua resposta vira treino direto pro Prism."
            )
            novas.append({
                "empresa": empresa,
                "processo": processo,
                "pergunta": pergunta_texto,
                "motivo": motivo,
                "comportamentos_relacionados": [A, B],
                "respostas_rapidas": [A, B, "ambos descrevem ações diferentes"],
                "status": "pendente",
                "criada_em": agora,
            })
        try:
            sb.table("perguntas_processo").insert(novas).execute()
            log.info(
                f"[multicam-divergencia] {len(novas)} pergunta(s) enfileirada(s) "
                f"em {empresa}/{processo}"
            )
            return len(novas)
        except Exception as e:
            log.warning(f"[multicam-divergencia] insert falhou: {e}")
            return 0
    except Exception as e:
        log.warning(f"gerar_pergunta_divergencia_camera falhou: {e}")
        return 0


def gerar_perguntas_processo(
    sb: Client,
    groq_client: Groq,
    empresa: str,
    processo: str,
    *,
    descricao_processo: str = "",
    memoria: dict | None = None,
    catalogo: dict[str, str] | None = None,
    observacoes_brutas: list[dict] | None = None,
    contexto_agregado: dict | None = None,
    max_perguntas: int = 4,
) -> list[dict]:
    """Gera (e persiste) perguntas proativas a partir das lacunas observadas.

    - Carrega perguntas já feitas (pendentes + respondidas + dispensadas)
      e o conhecimento adquirido, para a IA não repetir nem perguntar o
      que já foi resolvido.
    - Faz dedupe local por Jaccard (limiar 0.6) sobre tokens normalizados.
    - Persiste e retorna apenas o que foi efetivamente gravado.
    - Pode devolver lista vazia (e isso é OK).
    """
    # Perguntas já existentes neste contexto
    try:
        existentes = (
            sb.table("perguntas_processo")
            .select("pergunta, status")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .limit(500)
            .execute()
            .data
        ) or []
    except Exception as e:
        log.warning(f"Não foi possível carregar perguntas existentes: {e}")
        existentes = []

    existentes_textos = [(x.get("pergunta") or "").strip() for x in existentes if x.get("pergunta")]
    existentes_tokens = [_normalizar_pergunta(t) for t in existentes_textos]

    if existentes_textos:
        bloco_existentes = "\n".join(f"  - {t}" for t in existentes_textos[:60])
    else:
        bloco_existentes = "(nenhuma — esta é a primeira rodada de perguntas)"

    conhecimento = construir_bloco_conhecimento_adquirido(sb, empresa, processo)
    bloco_dominio = construir_bloco_dominio(descricao_processo or "", conhecimento)
    if not bloco_dominio.strip():
        bloco_dominio = "(o cliente ainda não forneceu descrição nem respondeu perguntas anteriores)"

    bloco_lacunas = _montar_bloco_lacunas(
        observacoes_brutas, catalogo, contexto_agregado, memoria
    )

    prompt = PROMPT_PERGUNTAS.format(
        empresa=empresa,
        processo=processo,
        max_perguntas=max_perguntas,
        bloco_dominio=bloco_dominio.strip(),
        bloco_lacunas=bloco_lacunas.strip(),
        bloco_perguntas_existentes=bloco_existentes,
    )

    try:
        resposta = groq_text_call(
            groq_client,
            prompt,
            model=GROQ_MODEL_ANALISE,
            json_mode=True,
            max_tokens=1200,
            temperatura=0.4,
        )
        dados = json.loads(resposta)
    except Exception as e:
        log.warning(f"Falha ao gerar/parsear perguntas: {e}")
        return []

    cruas = dados.get("perguntas") or []
    if not isinstance(cruas, list):
        return []

    novas_linhas: list[dict] = []
    tokens_acc = list(existentes_tokens)  # vai crescendo pra evitar duplicatas internas no batch
    for p in cruas[:max_perguntas]:
        if not isinstance(p, dict):
            continue
        texto = (p.get("pergunta") or "").strip()
        if not texto or len(texto) < 8:
            continue
        if _eh_duplicada(texto, tokens_acc):
            log.info(f"Pergunta descartada por similaridade: {texto[:60]}…")
            continue
        comp_rel = p.get("comportamentos_relacionados")
        if not isinstance(comp_rel, list):
            comp_rel = []
        # Sanitiza as 3 respostas curtas geradas pela LLM (1-5 palavras, sem vazias,
        # sem duplicatas). Se a LLM mandou bobagem, deixamos null e o frontend cai
        # no fallback Sim/Não/Às vezes.
        rapidas_raw = p.get("respostas_rapidas")
        rapidas: list[str] = []
        if isinstance(rapidas_raw, list):
            vistas: set[str] = set()
            for r in rapidas_raw:
                if not isinstance(r, str):
                    continue
                r2 = " ".join(r.split())  # normaliza espaços
                if not (2 <= len(r2) <= 60):
                    continue
                chave = r2.lower()
                if chave in vistas:
                    continue
                vistas.add(chave)
                rapidas.append(r2)
                if len(rapidas) == 3:
                    break
        novas_linhas.append(
            {
                "empresa": empresa,
                "processo": processo,
                "pergunta": texto,
                "motivo": (p.get("motivo") or "").strip() or None,
                "comportamentos_relacionados": [str(c) for c in comp_rel][:8],
                "respostas_rapidas": rapidas if len(rapidas) == 3 else None,
                "status": "pendente",
            }
        )
        tokens_acc.append(_normalizar_pergunta(texto))

    if not novas_linhas:
        log.info("Nenhuma pergunta nova após dedupe.")
        return []

    try:
        r = sb.table("perguntas_processo").insert(novas_linhas).execute()
        salvas = r.data or novas_linhas
        log.info(f"{len(salvas)} pergunta(s) proativa(s) persistida(s) em {empresa}/{processo}.")
        return salvas
    except Exception as e:
        log.warning(f"Falha ao persistir perguntas: {e}")
        return []


# ═════════════════════════════════════════════════════════════════════════
# FRAMES PARA VALIDAÇÃO
# ═════════════════════════════════════════════════════════════════════════
def extrair_3_frames_evento(evento: dict, video_path: str) -> list[np.ndarray]:
    fi, ff = int(evento["frame_inicio"]), int(evento["frame_fim"])

    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total > 0:
        fi = max(0, min(fi, total - 1))
        ff = max(0, min(ff, total - 1))
    if ff < fi:
        fi, ff = ff, fi
    fmid = (fi + ff) // 2
    alvos = [fi, fmid, ff]  # sempre 3 posições (podem coincidir em eventos curtos)

    bbox = evento["bbox_inicio"]
    if isinstance(bbox, dict):
        x1, y1, x2, y2 = int(bbox["x1"]), int(bbox["y1"]), int(bbox["x2"]), int(bbox["y2"])
    else:
        x1, y1, x2, y2 = (int(v) for v in bbox)

    # Leitura SEQUENCIAL a partir do frame inicial: o seek por índice
    # (cap.set(POS_FRAMES)) é instável em vários codecs e costuma falhar nos
    # frames do meio/fim, deixando só o primeiro. Lendo em sequência e usando a
    # posição real do decoder garantimos os 3 frames da ação.
    cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
    restantes = sorted(set(alvos))
    capturados: dict[int, np.ndarray] = {}
    limite = (ff - fi) + 1500  # cap de segurança (distância até o keyframe + folga)
    lidos = 0
    while restantes and lidos <= limite:
        pos = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        ret, frame = cap.read()
        if not ret or frame is None:
            break
        lidos += 1
        for alvo in list(restantes):
            if pos >= alvo:
                capturados[alvo] = frame
                restantes.remove(alvo)
    cap.release()

    def _anota(frame: np.ndarray) -> np.ndarray:
        a = frame.copy()  # copia: o mesmo frame pode servir a mais de um alvo
        h, w = a.shape[:2]
        m = max(h, w)
        # Anotação PROPORCIONAL ao tamanho do frame — assim a caixa não vira um
        # "blob" verde em frames pequenos nem some em frames grandes.
        esp = max(2, round(m / 360))
        fsc = max(0.5, min(1.2, m / 900))
        fth = max(1, round(m / 600))
        cv2.rectangle(a, (x1, y1), (x2, y2), (0, 255, 100), esp)
        cv2.putText(
            a,
            f'P{evento["pessoa_track_id"]:03d}',
            (x1, max(20, y1 - 10)),
            cv2.FONT_HERSHEY_DUPLEX,
            fsc,
            (0, 255, 100),
            fth,
        )
        # Frame de validação: normaliza para no máx. 720px no maior lado (meio
        # termo entre nitidez e peso). NÃO faz upscale — preserva a resolução
        # nativa quando menor, deixando o navegador escalar no display.
        if m > 720:
            escala = 720 / m
            a = cv2.resize(a, (int(w * escala), int(h * escala)), interpolation=cv2.INTER_AREA)
        return a

    crops: list[np.ndarray] = []
    ultimo: np.ndarray | None = None
    for alvo in alvos:
        frame = capturados.get(alvo, ultimo)
        if frame is None:
            continue
        ultimo = frame
        crops.append(_anota(frame))
    # Garante 3 frames na faixa de validação, repetindo o último válido.
    while crops and len(crops) < 3:
        crops.append(crops[-1])
    return crops


def frame_para_jpeg_bytes(frame_bgr: np.ndarray, qualidade: int = 85) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, qualidade])
    assert ok
    return buf.tobytes()


def extrair_3_frames_tempo(video_path: str, ini_s: float, fim_s: float) -> list[np.ndarray]:
    """3 frames (início, meio, fim) por TEMPO — sem bbox/anotação.

    Usado pelo 2º ângulo (cam2) na validação dual-câmera (Fase 6): a cam2 não é
    rastreada, então não há frame_idx/bbox; pegamos pelo relógio (clock-aligned →
    mesmo instante da cam1). Normaliza p/ ≤720px. Defensivo: nunca levanta;
    devolve [] se não conseguir abrir/ler.
    """
    if fim_s < ini_s:
        ini_s, fim_s = fim_s, ini_s
    mid_s = (ini_s + fim_s) / 2.0
    crops: list[np.ndarray] = []
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []
        dur_ms = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 30.0) * 1000.0
        ultimo: np.ndarray | None = None
        for t in (ini_s, mid_s, fim_s):
            alvo_ms = t * 1000.0
            if dur_ms and alvo_ms > dur_ms:
                alvo_ms = max(0.0, dur_ms - 1.0)
            cap.set(cv2.CAP_PROP_POS_MSEC, alvo_ms)
            ok, frame = cap.read()
            if ok and frame is not None:
                ultimo = frame
            if ultimo is None:
                continue
            f = ultimo
            h, w = f.shape[:2]
            m = max(h, w)
            if m > 720:
                escala = 720 / m
                f = cv2.resize(f, (int(w * escala), int(h * escala)), interpolation=cv2.INTER_AREA)
            crops.append(f)
        cap.release()
    except Exception:
        return crops
    while crops and len(crops) < 3:
        crops.append(crops[-1])
    return crops


# ═════════════════════════════════════════════════════════════════════════
# CHAT
# ═════════════════════════════════════════════════════════════════════════
def montar_snapshot_chat(
    sb: Client,
    empresa: str,
    processo: str,
    eventos: list | None = None,
    videos: list | None = None,
    comportamentos: list | None = None,
) -> dict:
    """Snapshot leve do processo. Aceita listas pré-buscadas (superset de
    colunas é OK — a função lê só o que precisa) para que callers pesados
    como /dashboard não busquem os mesmos dados duas vezes.
    """
    if eventos is None:
        evs = (
            sb.table("eventos")
            .select(
                "id, video_id, comportamento_label, label_corrigido, "
                "tempo_inicio_s, tempo_fim_s, validacao_correto, validado_humano, "
                "confianca, principal"
            )
            .eq("empresa", empresa)
            .eq("processo", processo)
            .limit(50000)
            .execute()
            .data
        ) or []
    else:
        evs = eventos
    base = [
        e for e in evs
        if e.get("validacao_correto") is not False and e.get("principal") is not False
    ]
    # Dedup multi-câmera (Fase 3): mesma ação física vista por 2+ câmeras
    # vira 1 evento efetivo. Eventos sem cam_id/gravado_em ficam intocados.
    _anexar_meta_video(base, sb)
    base = consolidar_eventos_para_metricas(base)

    if videos is None:
        vids = (
            sb.table("videos")
            .select("id, duracao_s")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .execute()
            .data
        ) or []
    else:
        vids = videos
    dur_total = sum((v.get("duracao_s") or 0) for v in vids)

    if comportamentos is None:
        comps = (
            sb.table("comportamentos")
            .select("label, descricao")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .execute()
            .data
        ) or []
    else:
        comps = comportamentos
    desc_por_label = {c["label"]: c.get("descricao", "") for c in comps}

    agg: dict = {}
    for e in base:
        l = _label_efetivo(e)
        d = max(0, (e.get("tempo_fim_s") or 0) - (e.get("tempo_inicio_s") or 0))
        a = agg.setdefault(l, {"oc": 0, "dur": 0.0, "vids": set()})
        a["oc"] += 1
        a["dur"] += d
        a["vids"].add(e.get("video_id"))

    distrib = []
    for l, a in sorted(agg.items(), key=lambda kv: kv[1]["dur"], reverse=True):
        distrib.append(
            {
                "comportamento": l,
                "descricao": desc_por_label.get(l, l),
                "ocorrencias": a["oc"],
                "tempo_total_s": round(a["dur"], 1),
                "pct_tempo": round(a["dur"] / max(1, dur_total) * 100, 1),
                "em_n_videos": len(a["vids"]),
            }
        )

    sugs = (
        sb.table("sugestoes_melhoria")
        .select("prioridade, area, situacao, sugestao, impacto_estimado")
        .eq("empresa", empresa)
        .eq("processo", processo)
        .order("criado_em", desc=True)
        .limit(8)
        .execute()
        .data
    )

    n_val = sum(1 for e in base if e.get("validado_humano"))
    return {
        "videos_analisados": len(vids),
        "tempo_total_observado_min": round(dur_total / 60, 1),
        "eventos_considerados": len(base),
        "pct_validado_por_humano": round(n_val / max(1, len(base)) * 100, 1),
        "distribuicao_comportamentos": distrib,
        "sugestoes_recentes": sugs,
        "padroes_vigentes": resumir_padroes_para_snapshot(sb, empresa, processo),
    }


def system_prompt_chat(
    empresa: str,
    processo: str,
    descricao_processo: str,
    snapshot: dict,
    conhecimento_adquirido: str = "",
) -> str:
    partes = [
        "Você é o Prism, a inteligência por trás da plataforma Kalidash Vision.",
        "Fala como um especialista sênior em produtividade industrial e engenharia de processos (Lean), em português do Brasil: confiante, direto e prático.",
        f'Está ajudando a empresa "{empresa}" a melhorar o processo "{processo}".',
        "",
        "ESCOPO — REGRA INEGOCIÁVEL:",
        "Você SÓ trata de MELHORAR A OPERAÇÃO DESTE CLIENTE com base nos dados dele: produtividade, comportamentos detectados, distribuição do tempo, gargalos, desperdícios (Lean), padrões, sequências, indicadores e sugestões de melhoria DESTE processo. NADA além disso.",
        'Se a pergunta fugir desse escopo (assuntos gerais, programação, conversa aleatória, outros domínios, opinião pessoal, perguntas pessoais sobre você), RECUSE em UMA frase e redirecione, por exemplo: "Sou o Prism, focado na sua operação — posso te ajudar a ver onde o tempo está indo, achar gargalos ou ler seus indicadores. Sobre isso, o que você quer ver?". NÃO responda o conteúdo fora de escopo, mesmo se insistirem.',
        "",
        "Você tem acesso a dados reais coletados por visão computacional sobre a operação (abaixo, em JSON). Use-os para embasar suas respostas com números concretos.",
    ]
    if descricao_processo:
        partes += ["", "DESCRIÇÃO DO PROCESSO (fornecida pelo cliente):", descricao_processo]
    if conhecimento_adquirido:
        partes += ["", conhecimento_adquirido.rstrip()]
    partes += [
        "",
        "DADOS AGREGADOS DA OPERAÇÃO (JSON):",
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        "",
        "COMO RESPONDER:",
        "- Quando a pergunta envolver o que acontece na operação (tempos, %, comportamentos, gargalos), baseie-se nos DADOS e cite os números reais.",
        "- Combine os dados com BOAS PRÁTICAS DE MERCADO (Lean, redução dos 7 desperdícios, balanceamento de linha, 5S, padronização, ergonomia, etc.).",
        "- Foque sempre em PRODUTIVIDADE: onde há tempo sem valor agregado e como recuperá-lo.",
        "- Seja específico, prático e acionável. Evite generalidades vazias.",
        "- Se os dados não cobrirem a pergunta, diga isso com transparência e responda com recomendação geral de mercado, deixando claro que é uma orientação (não uma leitura dos dados).",
        "- NUNCA invente números que não estejam nos dados.",
        "- Não comente desempenho de pessoas/indivíduos; fale sempre em termos de processo e estações.",
        "- Responda em português do Brasil, de forma clara e organizada. Use listas curtas quando ajudar.",
    ]
    return "\n".join(partes)


def responder_chat(
    groq_client: Groq,
    sb: Client,
    empresa: str,
    processo: str,
    pergunta: str,
    historico: list[dict] | None = None,
    max_trocas: int = 6,
) -> str:
    historico = historico or []
    descricao = resolver_descricao_processo(sb, empresa, processo, None)
    conhecimento = construir_bloco_conhecimento_adquirido(sb, empresa, processo)
    snapshot = montar_snapshot_chat(sb, empresa, processo)

    mensagens = [
        {
            "role": "system",
            "content": system_prompt_chat(empresa, processo, descricao, snapshot, conhecimento),
        }
    ]
    mensagens += historico[-max_trocas * 2 :]
    mensagens.append({"role": "user", "content": pergunta})

    from . import ai_provider
    return ai_provider.chat_call(mensagens, ai_provider.ANALISE, max_tokens=1500, temperatura=0.4)


# ═════════════════════════════════════════════════════════════════════════
# PRISM — título automático da conversa e sugestões dinâmicas
# Ambas são funções AUXILIARES (não-fatais): falhar nunca pode quebrar
# o envio de mensagem nem a abertura do painel.
# ═════════════════════════════════════════════════════════════════════════
def gerar_titulo_conversa(
    groq_client: Groq,
    pergunta: str,
    resposta: str,
    max_palavras: int = 6,
) -> str | None:
    """Gera um título curto (3-6 palavras) para uma conversa do Prism, a
    partir da primeira pergunta + primeira resposta. Devolve None se falhar.
    """
    if not pergunta.strip() or not resposta.strip():
        return None
    prompt = (
        "Crie um TÍTULO CURTO (no máximo {n} palavras, em português do Brasil) "
        "que resuma o assunto da troca abaixo entre um gestor e o consultor de "
        "produtividade. Sem aspas, sem emojis, sem ponto final. Apenas o título.\n\n"
        "PERGUNTA DO GESTOR:\n{p}\n\nRESPOSTA DO CONSULTOR:\n{r}\n\nTÍTULO:"
    ).format(n=max_palavras, p=pergunta.strip()[:400], r=resposta.strip()[:600])
    try:
        bruto = groq_text_call(
            groq_client,
            prompt,
            model=GROQ_MODEL_RAPIDO,
            json_mode=False,
            max_tokens=40,
            temperatura=0.3,
        )
    except Exception as e:
        log.warning(f"Prism: falha ao gerar título da conversa: {e}")
        return None

    titulo = (bruto or "").strip().splitlines()[0].strip()
    # limpeza: tira aspas e pontuação final, limita comprimento
    titulo = titulo.strip('"\'“”‘’ \t.!?')
    palavras = titulo.split()
    if not palavras:
        return None
    if len(palavras) > max_palavras + 2:
        titulo = " ".join(palavras[: max_palavras + 2])
    if len(titulo) > 80:
        titulo = titulo[:80].rstrip()
    return titulo or None


_SUGESTOES_FALLBACK = [
    "Onde estamos perdendo mais tempo?",
    "Quais as 3 maiores oportunidades de produtividade?",
    "O que foge do fluxo esperado do processo?",
]


def gerar_sugestoes_chat(
    sb: Client,
    groq_client: Groq,
    empresa: str,
    processo: str,
    *,
    excluir: list[str] | None = None,
    n: int = 4,
) -> list[str]:
    """Gera N sugestões CURTAS de assunto, baseadas no snapshot atual dos
    dados do processo, evitando repetir as já mostradas (lista `excluir`).
    Não-fatal: em caso de falha, devolve um fallback genérico (marcado).
    """
    excluir = [s.strip() for s in (excluir or []) if s and s.strip()]
    try:
        snapshot = montar_snapshot_chat(sb, empresa, processo)
    except Exception as e:
        log.warning(f"Prism: snapshot indisponível para sugestões: {e}")
        return list(_SUGESTOES_FALLBACK[:n])

    # snapshot leve pra economizar tokens
    distrib = snapshot.get("distribuicao_comportamentos") or []
    leve = {
        "videos_analisados": snapshot.get("videos_analisados"),
        "tempo_total_min": snapshot.get("tempo_total_observado_min"),
        "pct_validado": snapshot.get("pct_validado_por_humano"),
        "top_comportamentos": [
            {
                "comportamento": d.get("comportamento"),
                "pct_tempo": d.get("pct_tempo"),
            }
            for d in distrib[:6]
        ],
        "sugestoes_recentes": [
            {"area": s.get("area"), "sugestao": s.get("sugestao")}
            for s in (snapshot.get("sugestoes_recentes") or [])[:3]
        ],
    }
    bloco_excluir = (
        "\n".join(f"- {x}" for x in excluir[:20])
        if excluir
        else "(nenhuma — primeira rodada)"
    )

    prompt = (
        "Você é o Prism, inteligência de produtividade industrial da Kalidash Vision.\n"
        "Com base no RESUMO DOS DADOS deste processo (abaixo), gere {n} perguntas "
        "CURTAS (máximo 9 palavras cada) que um gestor faria para entender e "
        "melhorar a operação.\n\n"
        "REGRAS:\n"
        "- Cada pergunta deve ser ESPECÍFICA aos dados (cite o que salta aos olhos: "
        "comportamento que mais consome tempo, desperdícios, transições estranhas, "
        "baixa validação, etc.).\n"
        "- VARIADAS entre si e DIFERENTES das já mostradas (lista a evitar abaixo).\n"
        "- Só sobre produtividade / processo / dados DESTE cliente. Nada fora disso.\n"
        "- Linguagem de chão de fábrica, sem termos técnicos de IA ou estatística.\n"
        "- Termine com ponto de interrogação.\n\n"
        "RESUMO DOS DADOS (JSON):\n{snap}\n\n"
        "PERGUNTAS JÁ MOSTRADAS (evite repetir ou criar variantes):\n{ex}\n\n"
        'Responda APENAS: {{"sugestoes": ["...", "...", "..."]}}'
    ).format(n=n, snap=json.dumps(leve, ensure_ascii=False, indent=2), ex=bloco_excluir)

    try:
        resp = groq_text_call(
            groq_client,
            prompt,
            model=GROQ_MODEL_RAPIDO,
            json_mode=True,
            max_tokens=400,
            temperatura=0.9,
        )
        dados = json.loads(resp)
        cruas = dados.get("sugestoes") or []
    except Exception as e:
        log.warning(f"Prism: falha ao gerar sugestões: {e}")
        return list(_SUGESTOES_FALLBACK[:n])

    saidas: list[str] = []
    vistos = {s.lower().strip(" ?.!") for s in excluir}
    for s in cruas:
        if not isinstance(s, str):
            continue
        t = s.strip().strip('"\'“”')
        if not t or len(t) < 6 or len(t.split()) > 14:
            continue
        chave = t.lower().strip(" ?.!")
        if chave in vistos:
            continue
        if not t.endswith("?"):
            t = t.rstrip(".") + "?"
        saidas.append(t)
        vistos.add(chave)
        if len(saidas) >= n:
            break

    if not saidas:
        return list(_SUGESTOES_FALLBACK[:n])
    return saidas


# ═════════════════════════════════════════════════════════════════════════
# PORTFÓLIO — agregação de TODOS os processos da empresa numa passada.
# Usado pelo GET /processos enriquecido, pelo snapshot global e pelos
# insights globais. Faz ~5 queries por empresa (não N× por processo).
# ═════════════════════════════════════════════════════════════════════════
def agregar_portfolio(
    sb: Client, empresa: str, processo: str | None = None
) -> dict[str, dict]:
    """Retorna { nome_processo: {stats...} } para todos os processos da empresa.

    Se `processo` for informado, escopa TODAS as queries a esse processo —
    permite reaproveitar a função para o detalhe de um processo só sem
    varrer a empresa inteira. A fórmula de maturidade já é por-processo, então
    o número resultante é idêntico ao da versão sem filtro.
    """
    q_ctx = (
        sb.table("contexto_processo")
        .select("processo")
        .eq("empresa", empresa)
    )
    if processo is not None:
        q_ctx = q_ctx.eq("processo", processo)
    processos = q_ctx.execute().data or []
    nomes = [p["processo"] for p in processos]

    base: dict[str, dict] = {
        n: {
            "n_videos": 0,
            "tempo_total_s": 0.0,
            "ultimo_video_em": None,
            "eventos_considerados": 0,
            "eventos_pendentes": 0,
            "n_validados": 0,
            "n_sugestoes": 0,
            "n_sugestoes_alta": 0,
            "_agg": defaultdict(lambda: {"dur": 0.0, "oc": 0}),
        }
        for n in nomes
    }

    q_vid = (
        sb.table("videos")
        .select("processo, duracao_s, processado_em")
        .eq("empresa", empresa)
    )
    if processo is not None:
        q_vid = q_vid.eq("processo", processo)
    videos = q_vid.limit(50000).execute().data or []
    for v in videos:
        p = base.get(v.get("processo"))
        if not p:
            continue
        p["n_videos"] += 1
        p["tempo_total_s"] += v.get("duracao_s") or 0
        pe = v.get("processado_em")
        if pe and (p["ultimo_video_em"] is None or pe > p["ultimo_video_em"]):
            p["ultimo_video_em"] = pe

    q_cmp = (
        sb.table("comportamentos")
        .select("processo, label, categoria_lean")
        .eq("empresa", empresa)
    )
    if processo is not None:
        q_cmp = q_cmp.eq("processo", processo)
    comps = q_cmp.limit(50000).execute().data or []
    cat_por_pl: dict[tuple, str | None] = {}
    for c in comps:
        cat_por_pl[(c.get("processo"), c.get("label"))] = c.get("categoria_lean")

    q_ev = (
        sb.table("eventos")
        .select(
            "id, video_id, processo, comportamento_label, label_corrigido, "
            "tempo_inicio_s, tempo_fim_s, validacao_correto, validado_humano, "
            "origem_validacao, confianca, principal"
        )
        .eq("empresa", empresa)
    )
    if processo is not None:
        q_ev = q_ev.eq("processo", processo)
    eventos = q_ev.limit(100000).execute().data or []
    # Fase 16: só os PRINCIPAIS (1/min); crus de auditoria fora dos dois loops.
    eventos = [e for e in eventos if e.get("principal") is not False]

    # Loop 1: contadores de validação (origem, pendentes, considerados, validados)
    # — usam o stream BRUTO (sem dedup), pra refletir o trabalho real do humano.
    for e in eventos:
        p = base.get(e.get("processo"))
        if not p:
            continue
        ov = e.get("origem_validacao") or ""
        if not e.get("validado_humano"):
            p["eventos_pendentes"] += 1
            p["n_origem_pendente"] = p.get("n_origem_pendente", 0) + 1
        elif ov in ("correcao_aprendida", "vocabulario_canonico"):
            p["n_origem_auto"] = p.get("n_origem_auto", 0) + 1
        else:
            p["n_origem_humano"] = p.get("n_origem_humano", 0) + 1
        if e.get("validacao_correto") is False:
            continue
        p["eventos_considerados"] += 1
        if e.get("validado_humano"):
            p["n_validados"] += 1

    # Loop 2: agregação de DURAÇÃO por categoria Lean — usa stream DEDUPLICADO
    # (Fase 3: cam1+cam2 da mesma ação contam 1 vez só).
    _anexar_meta_video(eventos, sb)
    por_proc: dict[str, list[dict]] = {}
    for e in eventos:
        if e.get("validacao_correto") is False:
            continue
        por_proc.setdefault(e.get("processo"), []).append(e)
    for nome_proc, evs_p in por_proc.items():
        p = base.get(nome_proc)
        if not p:
            continue
        for e in consolidar_eventos_para_metricas(evs_p):
            lbl = _label_efetivo(e)
            dur = max(0, (e.get("tempo_fim_s") or 0) - (e.get("tempo_inicio_s") or 0))
            a = p["_agg"][lbl]
            a["dur"] += dur
            a["oc"] += 1

    q_sug = (
        sb.table("sugestoes_melhoria")
        .select("processo, prioridade, status")
        .eq("empresa", empresa)
        .eq("status", "pendente")
    )
    if processo is not None:
        q_sug = q_sug.eq("processo", processo)
    sugs = q_sug.limit(50000).execute().data or []
    for s in sugs:
        p = base.get(s.get("processo"))
        if not p:
            continue
        p["n_sugestoes"] += 1
        if (s.get("prioridade") or "").lower() == "alta":
            p["n_sugestoes_alta"] += 1

    # Padrões com confiança alta — entram na fórmula de maturidade
    n_padroes_alta: dict[str, int] = defaultdict(int)
    try:
        q_pad = (
            sb.table("padroes_processo")
            .select("processo, confianca")
            .eq("empresa", empresa)
        )
        if processo is not None:
            q_pad = q_pad.eq("processo", processo)
        pads = q_pad.limit(5000).execute().data or []
        for r in pads:
            if (r.get("confianca") or "").lower() == "alta":
                n_padroes_alta[r.get("processo")] += 1
    except Exception:
        pass

    # Perguntas proativas RESPONDIDAS — cada resposta é tratada como verdade do
    # domínio nos prompts seguintes (construir_bloco_conhecimento_adquirido),
    # então tem que pesar na maturidade do Prism sobre o processo.
    n_respostas: dict[str, int] = defaultdict(int)
    try:
        q_perg = (
            sb.table("perguntas_processo")
            .select("processo, status")
            .eq("empresa", empresa)
            .eq("status", "respondida")
        )
        if processo is not None:
            q_perg = q_perg.eq("processo", processo)
        perg = q_perg.limit(5000).execute().data or []
        for r in perg:
            n_respostas[r.get("processo")] += 1
    except Exception:
        pass

    # Finaliza: top comportamentos + composição de valor + MATURIDADE
    saida: dict[str, dict] = {}
    for n, p in base.items():
        agg = p.pop("_agg")
        tempo_total = p["tempo_total_s"] or 0
        top = sorted(agg.items(), key=lambda kv: kv[1]["dur"], reverse=True)
        top_comportamentos = [
            {
                "comportamento": lbl,
                "pct_tempo": round(d["dur"] / max(1, tempo_total) * 100, 1),
                "categoria_lean": cat_por_pl.get((n, lbl)),
            }
            for lbl, d in top[:5]
        ]
        soma_cat = {"valor_agregado": 0.0, "apoio": 0.0, "desperdicio": 0.0, "nao_classificado": 0.0}
        n_comp_local = 0
        n_nao_classif = 0
        for lbl, d in agg.items():
            cat = cat_por_pl.get((n, lbl))
            n_comp_local += 1
            if not cat:
                n_nao_classif += 1
                cat = "nao_classificado"
            if cat not in soma_cat:
                cat = "nao_classificado"
            soma_cat[cat] += d["dur"]
        composicao = {
            f"{k}_pct": round(v / max(1, tempo_total) * 100, 1) for k, v in soma_cat.items()
        }

        # ── Maturidade do Prism (0-100, derivada, saturada) ──
        # Critérios de calibração:
        #  • Volume = TEMPO OBSERVADO (min), não contagem de arquivos. 12 vídeos
        #    de 1 min ≠ 12 vídeos de 30 min — só contagem inflava maturidade
        #    em testes curtos.
        #  • Pontos por VALIDAÇÃO ABSOLUTA do humano: hoje 100% validado de 5
        #    eventos valia o mesmo que 100% de 5000. Agora a *quantidade*
        #    importa, com saturação alta.
        #  • Cobertura Lean é "puxada" pela IA automaticamente — antes dava
        #    ~5pts grátis. Agora multiplica por um GATE de validação humana,
        #    então só conta se houve trabalho real do gestor.
        #  • Limites de saturação maiores em geral: as faixas
        #    Aprendendo / Confiante / Especialista passam a exigir uso real.
        n_videos = p["n_videos"]
        ev_cons = p["eventos_considerados"]
        tempo_min = (p["tempo_total_s"] or 0) / 60.0
        n_validados = p["n_validados"]
        pct_val = n_validados / max(1, ev_cons)
        n_auto = p.get("n_origem_auto", 0)
        n_humano = p.get("n_origem_humano", 0)
        n_pend = p.get("n_origem_pendente", 0)
        pct_auto = n_auto / max(1, n_auto + n_humano + n_pend)
        cobertura_lean = 1 - (n_nao_classif / max(1, n_comp_local))
        n_pad = n_padroes_alta.get(n, 0)
        n_resp = n_respostas.get(n, 0)

        # Gate de validação: a cobertura Lean (que a IA "preenche sozinha")
        # só passa a contar à medida que o humano valida — evita 4-5 pontos
        # grátis logo no primeiro vídeo. Satura em 10 validações.
        gate_validacao = min(1, n_validados / 10)

        # Pesos somam 100: 18+12+10+25+6+5+8+16=100.
        maturidade = (
            18 * min(1, tempo_min / 720)            # tempo observado (sat. em 12h)
            + 12 * min(1, ev_cons / 3000)           # volume de eventos (sat. em 3k)
            + 10 * pct_val                          # % validado por humano (fração)
            + 25 * min(1, n_validados / 300)        # validações ABSOLUTAS (sat. em 300)
            + 6 * pct_auto                          # % auto-validação aprendida
            + 5 * cobertura_lean * gate_validacao   # cobertura Lean, gated pelo humano
            + 8 * min(1, n_pad / 10)                # padrões com confiança alta (sat. em 10)
            + 16 * min(1, n_resp / 20)              # perguntas proativas respondidas (sat. em 20)
        )
        maturidade = max(0, min(100, round(maturidade)))

        saida[n] = {
            "n_videos": n_videos,
            "tempo_total_s": round(tempo_total, 1),
            "tempo_total_min": round(tempo_total / 60, 1),
            "ultimo_video_em": p["ultimo_video_em"],
            "eventos_considerados": ev_cons,
            "eventos_pendentes": p["eventos_pendentes"],
            "pct_validado": round(pct_val * 100, 1),
            "n_sugestoes": p["n_sugestoes"],
            "n_sugestoes_alta": p["n_sugestoes_alta"],
            "top_comportamentos": top_comportamentos,
            "composicao_valor": composicao,
            "maturidade": maturidade,
        }
    return saida


def montar_snapshot_global(
    sb: Client, empresa: str, portfolio: dict | None = None
) -> dict:
    """Panorama leve de TODOS os processos da empresa (visão de portfólio).

    Aceita `portfolio` pré-computado para evitar refazer o scan caro de
    eventos/comportamentos quando o caller já tem ele em mãos.
    """
    if portfolio is None:
        portfolio = agregar_portfolio(sb, empresa)
    processos = []
    cons = {
        "videos_analisados": 0,
        "tempo_total_min": 0.0,
        "eventos_considerados": 0,
        "n_validados": 0,
        "n_sugestoes_alta": 0,
    }
    for nome, st in sorted(portfolio.items(), key=lambda kv: kv[1]["tempo_total_s"], reverse=True):
        processos.append(
            {
                "processo": nome,
                "n_videos": st["n_videos"],
                "tempo_total_min": st["tempo_total_min"],
                "top_comportamentos": st["top_comportamentos"],
                "composicao_valor": st["composicao_valor"],
                "n_sugestoes_alta": st["n_sugestoes_alta"],
                "pct_validado": st["pct_validado"],
                "eventos_pendentes": st["eventos_pendentes"],
            }
        )
        cons["videos_analisados"] += st["n_videos"]
        cons["tempo_total_min"] += st["tempo_total_min"]
        cons["eventos_considerados"] += st["eventos_considerados"]
        cons["n_validados"] += int(round(st["pct_validado"] / 100 * st["eventos_considerados"]))
        cons["n_sugestoes_alta"] += st["n_sugestoes_alta"]

    # composição consolidada (recomputa direto pelos % ponderados por tempo)
    total_min = cons["tempo_total_min"] or 1
    comp_cons = {"valor_agregado": 0.0, "apoio": 0.0, "desperdicio": 0.0, "nao_classificado": 0.0}
    for nome, st in portfolio.items():
        peso = st["tempo_total_min"]
        for k in comp_cons:
            comp_cons[k] += st["composicao_valor"].get(f"{k}_pct", 0) * peso
    composicao_consolidada = {k: round(v / total_min, 1) for k, v in comp_cons.items()}

    return {
        "empresa": empresa,
        "total_processos": len(processos),
        "consolidado": {
            "videos_analisados": cons["videos_analisados"],
            "tempo_total_min": round(cons["tempo_total_min"], 1),
            "eventos_considerados": cons["eventos_considerados"],
            "pct_validado": round(
                cons["n_validados"] / max(1, cons["eventos_considerados"]) * 100, 1
            ),
            "n_sugestoes_alta": cons["n_sugestoes_alta"],
            "composicao_valor": composicao_consolidada,
        },
        "processos": processos,
        "padroes_globais_vigentes": resumir_padroes_para_snapshot(sb, empresa, None),
    }


def system_prompt_chat_global(empresa: str, snapshot_global: dict) -> str:
    partes = [
        "Você é o Prism, a inteligência por trás da plataforma Kalidash Vision.",
        "Fala como um especialista sênior em produtividade industrial e engenharia de processos (Lean), em português do Brasil: confiante, direto e prático.",
        f'Agora você está em VISÃO GLOBAL: enxerga TODOS os processos da empresa "{empresa}" ao mesmo tempo (visão de portfólio).',
        "Você PODE comparar processos entre si, apontar qual precisa de mais atenção, achar padrões e desperdícios comuns, e ajudar a PRIORIZAR onde agir primeiro.",
        "",
        "ESCOPO — REGRA INEGOCIÁVEL:",
        "Você SÓ trata de MELHORAR AS OPERAÇÕES DESTA EMPRESA com base nos dados dela: produtividade, comportamentos, distribuição do tempo, gargalos, desperdícios (Lean), comparação entre processos, priorização e sugestões. NADA além disso.",
        'Se a pergunta fugir desse escopo, RECUSE em UMA frase e redirecione, por exemplo: "Sou o Prism, focado nas suas operações — posso comparar seus processos, dizer qual priorizar ou onde está a maior oportunidade. Sobre isso, o que você quer ver?". NÃO responda fora de escopo, mesmo se insistirem.',
        "",
        "PANORAMA DE TODOS OS PROCESSOS (JSON):",
        json.dumps(snapshot_global, ensure_ascii=False, indent=2),
        "",
        "COMO RESPONDER:",
        "- Baseie-se nos NÚMEROS do panorama. Cite processos pelo nome e use os percentuais/tempos reais.",
        "- Quando fizer sentido, compare processos e recomende prioridade (onde o ganho é maior).",
        "- NUNCA invente números que não estejam no panorama.",
        "- Não comente desempenho de pessoas; fale de processos e estações.",
        "- Português do Brasil, claro e organizado, listas curtas quando ajudarem.",
    ]
    return "\n".join(partes)


def responder_chat_global(
    groq_client: Groq,
    sb: Client,
    empresa: str,
    pergunta: str,
    historico: list[dict] | None = None,
    max_trocas: int = 6,
) -> str:
    historico = historico or []
    snapshot = montar_snapshot_global(sb, empresa)
    mensagens = [{"role": "system", "content": system_prompt_chat_global(empresa, snapshot)}]
    mensagens += historico[-max_trocas * 2 :]
    mensagens.append({"role": "user", "content": pergunta})
    from . import ai_provider
    return ai_provider.chat_call(mensagens, ai_provider.ANALISE, max_tokens=1500, temperatura=0.4)


def gerar_sugestoes_chat_global(
    sb: Client,
    groq_client: Groq,
    empresa: str,
    *,
    excluir: list[str] | None = None,
    n: int = 4,
) -> list[str]:
    excluir = [s.strip() for s in (excluir or []) if s and s.strip()]
    try:
        snap = montar_snapshot_global(sb, empresa)
    except Exception as e:
        log.warning(f"Prism global: snapshot indisponível p/ sugestões: {e}")
        return [
            "Qual processo devo priorizar?",
            "Onde está a maior oportunidade da operação?",
            "Que desperdícios se repetem entre os processos?",
        ][:n]

    leve = {
        "total_processos": snap["total_processos"],
        "consolidado": snap["consolidado"],
        "processos": [
            {"processo": p["processo"], "tempo_min": p["tempo_total_min"], "n_sugestoes_alta": p["n_sugestoes_alta"]}
            for p in snap["processos"][:8]
        ],
    }
    bloco_excluir = "\n".join(f"- {x}" for x in excluir[:20]) if excluir else "(nenhuma)"
    prompt = (
        "Você é o Prism (visão global de portfólio). Com base no PANORAMA abaixo, "
        "gere {n} perguntas CURTAS (máx. 9 palavras) que um gestor faria para "
        "PRIORIZAR e MELHORAR o conjunto de processos.\n\n"
        "REGRAS:\n- Específicas ao panorama (compare processos, priorização, padrões).\n"
        "- Variadas e diferentes das já mostradas.\n- Só produtividade/processos/dados. Nada fora.\n"
        "- Linguagem de gestor, sem termos de IA. Termine com '?'.\n\n"
        "PANORAMA (JSON):\n{snap}\n\nJÁ MOSTRADAS:\n{ex}\n\n"
        'Responda APENAS: {{"sugestoes": ["...", "..."]}}'
    ).format(n=n, snap=json.dumps(leve, ensure_ascii=False), ex=bloco_excluir)
    try:
        resp = groq_text_call(
            groq_client, prompt, model=GROQ_MODEL_RAPIDO, json_mode=True,
            max_tokens=400, temperatura=0.9,
        )
        cruas = json.loads(resp).get("sugestoes") or []
    except Exception as e:
        log.warning(f"Prism global: falha sugestões: {e}")
        cruas = []
    saidas, vistos = [], {s.lower().strip(" ?.!") for s in excluir}
    for s in cruas:
        if not isinstance(s, str):
            continue
        t = s.strip().strip('"\'“”')
        if not t or len(t) < 6 or len(t.split()) > 14:
            continue
        if t.lower().strip(" ?.!") in vistos:
            continue
        if not t.endswith("?"):
            t = t.rstrip(".") + "?"
        saidas.append(t)
        vistos.add(t.lower().strip(" ?.!"))
        if len(saidas) >= n:
            break
    return saidas or [
        "Qual processo devo priorizar?",
        "Onde está a maior oportunidade da operação?",
        "Que desperdícios se repetem entre os processos?",
    ][:n]


PROMPT_INSIGHTS_GLOBAIS = """Você é o Prism, consultor de produtividade industrial (Lean) com VISÃO DE PORTFÓLIO da empresa "{empresa}". Você vê TODOS os processos ao mesmo tempo (dados reais por visão computacional, no JSON abaixo).

Gere de 2 a 5 INSIGHTS DE PORTFÓLIO — olhando o conjunto, não um processo isolado. Cada insight responde a uma destas perguntas:
- Qual processo PRIORIZAR e por quê (maior oportunidade de ganho consolidada)?
- Onde estão os maiores desperdícios / menor valor agregado entre os processos?
- Que PADRÕES ou problemas se REPETEM em vários processos?

Para cada insight:
- prioridade: "alta" | "media" | "info"
- titulo: curto e direto (ex.: "Priorize a Linha 2: 41% do tempo em deslocamento")
- descricao: 1-3 frases com NÚMEROS REAIS do panorama (tempos, %, nº de vídeos, nomes de processos)
- processos_relacionados: lista dos nomes de processos citados

REGRAS:
- Use SÓ números que aparecem no panorama. NUNCA invente.
- Compare processos quando fizer sentido (priorização é o mais valioso).
- Se a base é pequena (poucos vídeos), seja cauteloso e diga isso.
- Não comente pessoas; fale de processos e estações.

Responda APENAS um JSON:
{{"insights": [{{"prioridade": "...", "titulo": "...", "descricao": "...", "processos_relacionados": ["..."]}}, ...]}}

PANORAMA DE TODOS OS PROCESSOS (JSON):
"""


def gerar_insights_globais(
    sb: Client,
    groq_client: Groq,
    empresa: str,
    snapshot_global: dict | None = None,
) -> int:
    """Recalcula os insights de portfólio da empresa (substitui os anteriores).
    Não-fatal. Retorna quantos insights foram persistidos.

    `snapshot_global` pode vir pré-computado para evitar refazer o portfólio.
    """
    if snapshot_global is not None:
        snap = snapshot_global
    else:
        try:
            snap = montar_snapshot_global(sb, empresa)
        except Exception as e:
            log.warning(f"Insights globais: snapshot indisponível: {e}")
            return 0

    if snap["consolidado"]["videos_analisados"] == 0:
        # Sem dados — limpa insights antigos e não gera nada.
        try:
            sb.table("insights_globais").delete().eq("empresa", empresa).execute()
        except Exception:
            pass
        return 0

    prompt = PROMPT_INSIGHTS_GLOBAIS.format(empresa=empresa)
    try:
        resp = groq_text_call(
            groq_client,
            prompt + json.dumps(snap, ensure_ascii=False, indent=2),
            model=GROQ_MODEL_ANALISE,
            json_mode=True,
            max_tokens=4000,
            temperatura=0.3,
        )
        insights = json.loads(resp).get("insights") or []
    except Exception as e:
        log.warning(f"Insights globais: falha ao gerar: {e}")
        return 0

    linhas = []
    for it in insights:
        if not isinstance(it, dict):
            continue
        rel = it.get("processos_relacionados")
        if not isinstance(rel, list):
            rel = []
        linhas.append(
            {
                "empresa": empresa,
                "prioridade": (it.get("prioridade") or "info").lower(),
                "titulo": (it.get("titulo") or "").strip(),
                "descricao": (it.get("descricao") or "").strip(),
                "processos_relacionados": [str(x) for x in rel][:10],
            }
        )
    if not linhas:
        return 0

    # Substitui o estado vigente (apaga antigos, insere novos)
    try:
        sb.table("insights_globais").delete().eq("empresa", empresa).execute()
        sb.table("insights_globais").insert(linhas).execute()
    except Exception as e:
        log.warning(f"Insights globais: falha ao persistir: {e}")
        return 0
    log.info(f"Insights globais recalculados para {empresa}: {len(linhas)}")
    return len(linhas)


# ═════════════════════════════════════════════════════════════════════════
# INTELIGÊNCIA DE PADRÕES (recorrência e evolução ao longo do tempo)
#
# PRINCÍPIO: todos os NÚMEROS (tendência, recorrência, z-score, sobreposição
# entre processos) são calculados aqui em Python — determinístico e
# auditável. O LLM SÓ interpreta e dá linguagem; nunca inventa número.
#
# Padrão ≠ retrato: o eixo é RECORRÊNCIA e EVOLUÇÃO, não o estado atual
# (que já é coberto por sugestoes_melhoria e insights_globais).
#
# A plataforma mede TEMPO das pessoas. "Padrão de erro" = padrão de
# DESPERDÍCIO de tempo (categoria 'desperdicio'); "padrão de acerto" =
# VALOR AGREGADO. Nunca defeito/refugo/qualidade/output.
# ═════════════════════════════════════════════════════════════════════════
MIN_VIDEOS_PADRAO = 3
MIN_PROCESSOS_GLOBAL = 2


def _media(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _desvio_padrao(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _media(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def montar_serie_temporal(sb: Client, empresa: str, processo: str) -> dict:
    """Série por vídeo (ordenada por processado_em) com o SHARE de % do
    tempo por comportamento (label efetivo) e por categoria Lean. Tudo
    normalizado para ser comparável entre turnos de durações diferentes.
    """
    videos = (
        sb.table("videos")
        .select("id, nome, duracao_s, processado_em")
        .eq("empresa", empresa)
        .eq("processo", processo)
        .order("processado_em", desc=False)
        .limit(500)
        .execute()
        .data
    ) or []

    eventos = (
        sb.table("eventos")
        .select(
            "video_id, comportamento_label, label_corrigido, tempo_inicio_s, "
            "tempo_fim_s, validacao_correto, pessoa_track_id, principal"
        )
        .eq("empresa", empresa)
        .eq("processo", processo)
        .limit(100000)
        .execute()
        .data
    ) or []

    comps = (
        sb.table("comportamentos")
        .select("label, categoria_lean")
        .eq("empresa", empresa)
        .eq("processo", processo)
        .execute()
        .data
    ) or []
    cat_por_label = {c["label"]: c.get("categoria_lean") for c in comps}

    # agrupa eventos por vídeo (Fase 16: só principais; crus de auditoria fora)
    por_video: dict = defaultdict(list)
    for e in eventos:
        if e.get("validacao_correto") is False or e.get("principal") is False:
            continue
        por_video[e.get("video_id")].append(e)

    pontos = []
    for v in videos:
        evs = por_video.get(v["id"], [])
        dur_por_label: dict[str, float] = defaultdict(float)
        dur_por_cat: dict[str, float] = defaultdict(float)
        pessoas = set()
        total = 0.0
        for e in evs:
            lbl = _label_efetivo(e)
            d = max(0, (e.get("tempo_fim_s") or 0) - (e.get("tempo_inicio_s") or 0))
            dur_por_label[lbl] += d
            cat = cat_por_label.get(lbl) or "nao_classificado"
            dur_por_cat[cat] += d
            pessoas.add(e.get("pessoa_track_id"))
            total += d
        total = total or 1
        pontos.append(
            {
                "video_id": v["id"],
                "nome": v.get("nome"),
                "processado_em": v.get("processado_em"),
                "n_eventos": len(evs),
                "n_pessoas": len(pessoas),
                "share_comportamento": {
                    k: round(val / total * 100, 1) for k, val in dur_por_label.items()
                },
                "share_categoria": {
                    k: round(val / total * 100, 1) for k, val in dur_por_cat.items()
                },
            }
        )

    # labels e categorias que aparecem na série
    labels = sorted({k for p in pontos for k in p["share_comportamento"]})
    cats = sorted({k for p in pontos for k in p["share_categoria"]})
    return {"pontos": pontos, "labels": labels, "categorias": cats, "n_videos": len(pontos)}


def _serie_de(serie: dict, chave: str, item: str) -> list[float]:
    """Extrai a série temporal (com 0 onde ausente) de um label/categoria."""
    return [p[chave].get(item, 0.0) for p in serie["pontos"]]


def calcular_sinais_padroes(serie: dict) -> dict:
    """Camada A (temporal) + B (estrutural), tudo em Python.

    Retorna um dict de sinais quantitativos prontos para o LLM interpretar.
    """
    pontos = serie["pontos"]
    n = len(pontos)
    sinais: dict = {
        "n_videos": n,
        "tendencias": [],
        "recorrencias": [],
        "desvios": [],
        "volatilidades": [],
        "estrutural_desperdicio": [],
        "estrutural_valor": [],
    }
    if n < MIN_VIDEOS_PADRAO:
        return sinais

    def analisa(chave: str, itens: list[str], rotulo: str):
        for item in itens:
            s = _serie_de(serie, chave, item)
            if not any(s):
                continue
            media = _media(s)
            std = _desvio_padrao(s)
            # tendência: 1º terço vs último terço
            terco = max(1, n // 3)
            ini = _media(s[:terco])
            fim = _media(s[-terco:])
            delta = round(fim - ini, 1)
            recorr = round(sum(1 for x in s if x > 0.5) / n * 100, 0)
            # desvio recente (z-score do último ponto vs histórico anterior)
            z = 0.0
            if n >= 4:
                hist = s[:-1]
                m_h, sd_h = _media(hist), _desvio_padrao(hist)
                if sd_h > 0:
                    z = round((s[-1] - m_h) / sd_h, 2)
            if abs(delta) >= 5:
                sinais["tendencias"].append(
                    {
                        "tipo": rotulo,
                        "item": item,
                        "direcao": "subindo" if delta > 0 else "descendo",
                        "delta_pp": delta,
                        "share_inicial_pct": round(ini, 1),
                        "share_final_pct": round(fim, 1),
                        "media_pct": round(media, 1),
                    }
                )
            if recorr >= 70 and media >= 3:
                sinais["recorrencias"].append(
                    {"tipo": rotulo, "item": item, "presenca_pct_turnos": recorr, "media_pct": round(media, 1)}
                )
            if abs(z) >= 2:
                sinais["desvios"].append(
                    {
                        "tipo": rotulo,
                        "item": item,
                        "z_score": z,
                        "ultimo_pct": round(s[-1], 1),
                        "media_historica_pct": round(_media(s[:-1]), 1),
                    }
                )
            if std >= 8 and media >= 3:
                sinais["volatilidades"].append(
                    {"tipo": rotulo, "item": item, "desvio_padrao_pp": round(std, 1), "media_pct": round(media, 1)}
                )

    analisa("share_comportamento", serie["labels"], "comportamento")
    analisa("share_categoria", serie["categorias"], "categoria")

    # ── Camada B: estrutural (desperdício e valor recorrentes) ──
    # média do share por categoria ao longo dos turnos
    for cat, alvo in (("desperdicio", "estrutural_desperdicio"), ("valor_agregado", "estrutural_valor")):
        ranking = []
        for lbl in serie["labels"]:
            s = _serie_de(serie, "share_comportamento", lbl)
            if not any(s):
                continue
            ranking.append((lbl, _media(s), round(sum(1 for x in s if x > 0.5) / len(s) * 100, 0)))
        ranking.sort(key=lambda x: x[1], reverse=True)
        # share médio da categoria
        s_cat = _serie_de(serie, "share_categoria", cat)
        media_cat = round(_media(s_cat), 1)
        if media_cat >= 1:
            sinais[alvo].append(
                {
                    "categoria": cat,
                    "media_share_pct": media_cat,
                    "recorrencia_pct_turnos": round(sum(1 for x in s_cat if x > 0.5) / len(s_cat) * 100, 0),
                    "top_comportamentos": [
                        {"item": lbl, "media_pct": round(m, 1), "presenca_pct": rec}
                        for lbl, m, rec in ranking[:4]
                    ],
                }
            )

    return sinais


def calcular_sinais_globais(
    sb: Client, empresa: str, portfolio: dict | None = None
) -> dict:
    """Camada C — cruza os processos da empresa (em Python).

    Lê apenas `top_comportamentos`, `composicao_valor` e `n_videos` do
    portfólio (não usa `maturidade`/`n_padroes`), então um portfólio
    pré-computado serve sem perda de fidelidade.
    """
    if portfolio is None:
        portfolio = agregar_portfolio(sb, empresa)
    processos_com_dados = {n: st for n, st in portfolio.items() if st["n_videos"] > 0}
    n_proc = len(processos_com_dados)

    sinais: dict = {
        "n_processos_com_dados": n_proc,
        "compartilhados": [],
        "benchmarking": [],
        "sistemicos": [],
    }
    if n_proc < MIN_PROCESSOS_GLOBAL:
        return sinais

    # comportamento (label) → em quantos processos aparece com share relevante (>=5%)
    presenca: dict[str, list] = defaultdict(list)
    presenca_desp: dict[str, list] = defaultdict(list)
    for nome, st in processos_com_dados.items():
        for tc in st.get("top_comportamentos", []):
            lbl = tc.get("comportamento")
            pct = tc.get("pct_tempo", 0)
            cat = tc.get("categoria_lean")
            if pct >= 5:
                presenca[lbl].append({"processo": nome, "pct": pct, "categoria": cat})
                if cat == "desperdicio":
                    presenca_desp[lbl].append({"processo": nome, "pct": pct})

    for lbl, ocorr in presenca.items():
        if len(ocorr) >= max(2, round(n_proc * 0.5)):
            sinais["compartilhados"].append(
                {
                    "item": lbl,
                    "n_processos": len(ocorr),
                    "de_total": n_proc,
                    "categoria": ocorr[0].get("categoria"),
                    "share_medio_pct": round(_media([o["pct"] for o in ocorr]), 1),
                    "processos": [o["processo"] for o in ocorr],
                }
            )

    # sistêmico: um DESPERDÍCIO recorrente em vários processos
    for lbl, ocorr in presenca_desp.items():
        if len(ocorr) >= max(2, round(n_proc * 0.5)):
            sinais["sistemicos"].append(
                {
                    "item": lbl,
                    "n_processos": len(ocorr),
                    "de_total": n_proc,
                    "share_medio_pct": round(_media([o["pct"] for o in ocorr]), 1),
                    "processos": [o["processo"] for o in ocorr],
                }
            )

    # benchmarking: ranking por índice de valor agregado
    ranking_va = sorted(
        (
            {
                "processo": nome,
                "valor_agregado_pct": st["composicao_valor"].get("valor_agregado_pct", 0),
                "desperdicio_pct": st["composicao_valor"].get("desperdicio_pct", 0),
                "n_videos": st["n_videos"],
            }
            for nome, st in processos_com_dados.items()
        ),
        key=lambda x: x["valor_agregado_pct"],
        reverse=True,
    )
    sinais["benchmarking"] = ranking_va
    return sinais


# ─── Interpretação por LLM (só dá linguagem aos números) ──────────────────
PROMPT_PADROES_PROCESSO = """Você é o Prism, especialista em produtividade industrial (Lean), analisando PADRÕES na operação da empresa "{empresa}", processo "{processo}".

DIFERENÇA CRUCIAL: você NÃO está descrevendo o estado atual (isso já foi feito em outra etapa). Você está identificando PADRÕES de RECORRÊNCIA e EVOLUÇÃO ao longo dos turnos (vídeos). Ex.: "o desperdício subiu de 18% para 31% em 6 turnos" (tendência), "deslocamento aparece em 100% dos turnos" (recorrência), "o último turno destoou muito" (desvio).

OS NÚMEROS JÁ ESTÃO CALCULADOS (abaixo, em JSON). Sua tarefa é INTERPRETAR e dar linguagem — NUNCA invente ou estime números. Use só os que estão nos sinais.

{bloco_dominio}Para cada padrão relevante, produza:
- tipo: "tendencia" | "recorrencia" | "desvio" | "volatilidade" | "fluxo" | "desperdicio" | "valor"
- camada: "temporal" (tendência/recorrência/desvio/volatilidade) ou "estrutural" (fluxo/desperdicio/valor)
- titulo: curto e direto, com o número-chave (ex.: "Desperdício subindo: 18% → 31% em 6 turnos")
- descricao: 1-3 frases interpretando o padrão com os NÚMEROS REAIS dos sinais
- comportamentos_relacionados: lista de labels citados
- categoria_relacionada: "valor_agregado" | "apoio" | "desperdicio" | null
- confianca: "alta" | "media" | "baixa" (conforme nº de turnos: poucos turnos = baixa/média)
- relevancia: "alta" | "media" | "info"
- recomendacao: ação ancorada NO PADRÃO (ex.: "como o deslocamento vem crescendo, investigar mudança recente de layout") — diferente de sugestão pontual; pode ser null se não houver ação clara

REGRAS:
- "Padrão de erro" = desperdício de TEMPO (categoria desperdicio). NUNCA fale de defeito, refugo, qualidade ou output — a plataforma não mede isso.
- Se há poucos turnos (n_videos baixo), seja cauteloso e use confiança baixa/média.
- Gere de 0 a 6 padrões. Se os sinais não sustentam nenhum padrão claro, devolva lista vazia.

SINAIS QUANTITATIVOS (JSON, já calculados):
{sinais}

Responda APENAS um JSON:
{{"padroes": [{{"tipo":"...","camada":"...","titulo":"...","descricao":"...","comportamentos_relacionados":[...],"categoria_relacionada":"...","confianca":"...","relevancia":"...","recomendacao":"..."}}]}}
"""


PROMPT_PADROES_GLOBAIS = """Você é o Prism, com VISÃO DE PORTFÓLIO da empresa "{empresa}". Identifique PADRÕES SISTÊMICOS entre os processos (não o retrato atual, que já foi feito).

Tipos de padrão global:
- "compartilhado": o mesmo comportamento relevante aparece em vários processos
- "benchmarking": qual processo é referência (melhor índice de valor agregado) e o contraste com os demais
- "sistemico": um DESPERDÍCIO recorrente em várias linhas → sinal de causa-raiz organizacional (alto valor para a direção)

OS NÚMEROS JÁ ESTÃO CALCULADOS (abaixo). INTERPRETE, não invente.

Para cada padrão:
- tipo: "compartilhado" | "benchmarking" | "sistemico"
- titulo: curto com o número-chave (ex.: "'andar' é desperdício dominante em 4 de 5 linhas")
- descricao: 1-3 frases com os NÚMEROS REAIS
- processos_relacionados: nomes dos processos citados
- confianca: "alta" | "media" | "baixa"
- relevancia: "alta" | "media" | "info"
- recomendacao: ação de nível EMPRESA ancorada no padrão (ex.: replicar o layout da linha referência); pode ser null

REGRAS:
- Só desperdício/valor de TEMPO. Nunca defeito/qualidade/output.
- Poucos processos = confiança menor.
- Gere de 0 a 5 padrões; lista vazia se não houver padrão claro.

SINAIS GLOBAIS (JSON, já calculados):
{sinais}

Responda APENAS: {{"padroes": [{{"tipo":"...","titulo":"...","descricao":"...","processos_relacionados":[...],"confianca":"...","relevancia":"...","recomendacao":"..."}}]}}
"""


def _confianca_por_n(n: int, minimo: int) -> str:
    if n >= minimo + 4:
        return "alta"
    if n >= minimo + 1:
        return "media"
    return "baixa"


def analisar_padroes_processo(
    sb: Client,
    groq_client: Groq,
    empresa: str,
    processo: str,
    *,
    descricao_processo: str = "",
    conhecimento_adquirido: str = "",
) -> int:
    """Calcula sinais (Python) e pede ao LLM a interpretação. Persiste
    (substituindo os vigentes). Não-fatal. Retorna nº de padrões."""
    serie = montar_serie_temporal(sb, empresa, processo)
    if serie["n_videos"] < MIN_VIDEOS_PADRAO:
        # massa insuficiente — limpa vigentes e não chama LLM
        try:
            sb.table("padroes_processo").delete().eq("empresa", empresa).eq("processo", processo).execute()
        except Exception:
            pass
        return 0

    sinais = calcular_sinais_padroes(serie)
    tem_sinal = any(
        sinais[k]
        for k in ("tendencias", "recorrencias", "desvios", "volatilidades", "estrutural_desperdicio", "estrutural_valor")
    )
    if not tem_sinal:
        try:
            sb.table("padroes_processo").delete().eq("empresa", empresa).eq("processo", processo).execute()
        except Exception:
            pass
        return 0

    bloco_dominio = construir_bloco_dominio(descricao_processo or "", conhecimento_adquirido or "")
    prompt = PROMPT_PADROES_PROCESSO.format(
        empresa=empresa,
        processo=processo,
        bloco_dominio=(bloco_dominio.rstrip() + "\n\n") if bloco_dominio.strip() else "",
        sinais=json.dumps(sinais, ensure_ascii=False, indent=2),
    )
    try:
        resp = groq_text_call(
            groq_client, prompt, model=GROQ_MODEL_ANALISE, json_mode=True,
            max_tokens=4000, temperatura=0.3,
        )
        padroes = json.loads(resp).get("padroes") or []
    except Exception as e:
        log.warning(f"Padrões processo: falha LLM ({empresa}/{processo}): {e}")
        return 0

    conf_base = _confianca_por_n(serie["n_videos"], MIN_VIDEOS_PADRAO)
    linhas = []
    for p in padroes:
        if not isinstance(p, dict):
            continue
        cr = p.get("comportamentos_relacionados")
        linhas.append(
            {
                "empresa": empresa,
                "processo": processo,
                "tipo": (p.get("tipo") or "").strip()[:40] or None,
                "camada": (p.get("camada") or "").strip()[:20] or None,
                "titulo": (p.get("titulo") or "").strip(),
                "descricao": (p.get("descricao") or "").strip(),
                "comportamentos_relacionados": [str(x) for x in cr][:10] if isinstance(cr, list) else [],
                "categoria_relacionada": (p.get("categoria_relacionada") or None),
                "confianca": (p.get("confianca") or conf_base),
                "relevancia": (p.get("relevancia") or "media"),
                "recomendacao": (p.get("recomendacao") or None),
                "evidencia": sinais,
                "n_videos_analisados": serie["n_videos"],
            }
        )
    if not linhas:
        return 0
    try:
        sb.table("padroes_processo").delete().eq("empresa", empresa).eq("processo", processo).execute()
        sb.table("padroes_processo").insert(linhas).execute()
    except Exception as e:
        log.warning(f"Padrões processo: falha ao persistir: {e}")
        return 0
    log.info(f"Padrões recalculados {empresa}/{processo}: {len(linhas)}")
    return len(linhas)


def analisar_padroes_globais(
    sb: Client,
    groq_client: Groq,
    empresa: str,
    portfolio: dict | None = None,
) -> int:
    """Calcula sinais globais (Python) + interpretação LLM. Substitui os
    vigentes da empresa. Não-fatal."""
    sinais = calcular_sinais_globais(sb, empresa, portfolio=portfolio)
    if sinais["n_processos_com_dados"] < MIN_PROCESSOS_GLOBAL:
        try:
            sb.table("padroes_globais").delete().eq("empresa", empresa).execute()
        except Exception:
            pass
        return 0
    if not (sinais["compartilhados"] or sinais["sistemicos"] or len(sinais["benchmarking"]) >= 2):
        try:
            sb.table("padroes_globais").delete().eq("empresa", empresa).execute()
        except Exception:
            pass
        return 0

    prompt = PROMPT_PADROES_GLOBAIS.format(
        empresa=empresa, sinais=json.dumps(sinais, ensure_ascii=False, indent=2)
    )
    try:
        resp = groq_text_call(
            groq_client, prompt, model=GROQ_MODEL_ANALISE, json_mode=True,
            max_tokens=2200, temperatura=0.3,
        )
        padroes = json.loads(resp).get("padroes") or []
    except Exception as e:
        log.warning(f"Padrões globais: falha LLM ({empresa}): {e}")
        return 0

    conf_base = _confianca_por_n(sinais["n_processos_com_dados"], MIN_PROCESSOS_GLOBAL)
    linhas = []
    for p in padroes:
        if not isinstance(p, dict):
            continue
        pr = p.get("processos_relacionados")
        linhas.append(
            {
                "empresa": empresa,
                "tipo": (p.get("tipo") or "").strip()[:40] or None,
                "titulo": (p.get("titulo") or "").strip(),
                "descricao": (p.get("descricao") or "").strip(),
                "processos_relacionados": [str(x) for x in pr][:20] if isinstance(pr, list) else [],
                "confianca": (p.get("confianca") or conf_base),
                "relevancia": (p.get("relevancia") or "media"),
                "recomendacao": (p.get("recomendacao") or None),
                "evidencia": sinais,
            }
        )
    if not linhas:
        return 0
    try:
        sb.table("padroes_globais").delete().eq("empresa", empresa).execute()
        sb.table("padroes_globais").insert(linhas).execute()
    except Exception as e:
        log.warning(f"Padrões globais: falha ao persistir: {e}")
        return 0
    log.info(f"Padrões globais recalculados {empresa}: {len(linhas)}")
    return len(linhas)


def resumir_padroes_para_snapshot(sb: Client, empresa: str, processo: str | None = None) -> list[dict]:
    """Resumo enxuto dos padrões vigentes para injetar no snapshot do Prism."""
    try:
        if processo:
            r = (
                sb.table("padroes_processo")
                .select("tipo, titulo, relevancia, confianca")
                .eq("empresa", empresa)
                .eq("processo", processo)
                .order("criado_em", desc=True)
                .limit(10)
                .execute()
            )
        else:
            r = (
                sb.table("padroes_globais")
                .select("tipo, titulo, relevancia, confianca")
                .eq("empresa", empresa)
                .order("criado_em", desc=True)
                .limit(10)
                .execute()
            )
        return r.data or []
    except Exception:
        return []


# ═════════════════════════════════════════════════════════════════════════
# ORQUESTRADOR — chamada principal do worker
# ═════════════════════════════════════════════════════════════════════════
def processar_video(
    empresa: str,
    processo: str,
    video_path: str,
    descricao_processo: str | None = None,
    intervalo_amostragem_s: float = DEFAULT_INTERVALO_AMOSTRAGEM_S,
    limiar_auto_validacao: int = DEFAULT_LIMIAR_AUTO_VALIDACAO,
    rois_contexto: dict | None = None,
    progress_cb: ProgressCb | None = None,
    sb: Client | None = None,
    groq_client: Groq | None = None,
    yolo_model: YOLO | None = None,
    nome_video: str | None = None,
    caminho_storage: str | None = None,
    cam_id: str | None = None,
    gravado_em: str | None = None,
    video_path_secundario: str | None = None,
    cam_id_secundario: str | None = None,
) -> dict:
    """Roda o pipeline completo. Devolve dict com video_id, n_eventos,
    n_auto_validados, n_sugestoes.

    Fase 6 (dual-angle): se `video_path_secundario` for dado, a cam2 entra como
    2º ângulo na MESMA chamada ao VLM (a cam1 dirige detecção/segmentação). Gera
    UMA trilha de eventos (na cam1) — sem duplicidade na validação/métricas.
    """
    progress_cb = progress_cb or _noop_progress
    rois_contexto = rois_contexto or DEFAULT_ROIS_CONTEXTO
    sb = sb or make_supabase_client()
    groq_client = groq_client or make_groq_client()
    if yolo_model is not None:
        yolo = yolo_model
    else:
        from ultralytics import YOLO  # import lazy: só carrega torch quando há upload
        yolo = YOLO(YOLO_MODEL)

    progress_cb("setup", 0, f"Iniciando · {empresa}/{processo}")
    memoria = carregar_memoria_do_negocio(sb, empresa, processo)
    descricao = resolver_descricao_processo(sb, empresa, processo, descricao_processo)
    conhecimento = construir_bloco_conhecimento_adquirido(sb, empresa, processo)
    progress_cb(
        "setup",
        100,
        f"Memória: {memoria['total_eventos_validados']} eventos validados"
        + (f" · {conhecimento.count('- P:')} respostas no domínio" if conhecimento else ""),
    )

    amostras, info_video, ids_unicos = etapa_detectar_e_amostrar(
        yolo, video_path, intervalo_amostragem_s, rois_contexto, progress_cb
    )

    if not amostras:
        progress_cb("concluido", 100, "Nenhuma pessoa detectada no vídeo")
        return {
            "video_id": None,
            "n_eventos": 0,
            "n_auto_validados": 0,
            "n_sugestoes": 0,
            "n_perguntas": 0,
        }

    # Fase 6: 2º ângulo (cam2) anexado a cada amostra para o VLM concluir com os
    # dois pontos de vista. cam1 já dirigiu detecção/tracking acima.
    if video_path_secundario:
        n_sec = _anexar_segundo_angulo(amostras, video_path_secundario)
        log.info(
            f"[dual-angle] {cam_id or 'cam1'} + {cam_id_secundario or 'cam2'}: "
            f"2º ângulo em {n_sec}/{len(amostras)} amostras"
        )

    observacoes = etapa_analise_vlm(
        groq_client, amostras, descricao, memoria, progress_cb,
        conhecimento_adquirido=conhecimento,
    )

    if not observacoes:
        progress_cb("concluido", 100, "Nenhuma observação obtida do VLM")
        return {
            "video_id": None,
            "n_eventos": 0,
            "n_auto_validados": 0,
            "n_sugestoes": 0,
            "n_perguntas": 0,
        }

    _, catalogo, label_de, origem_de = etapa_clusterizar(
        groq_client, observacoes, descricao, memoria, limiar_auto_validacao, progress_cb,
        conhecimento_adquirido=conhecimento,
    )

    progress_cb("segmentar", 0, "Formando eventos contínuos")
    eventos_crus = etapa_segmentar_eventos(observacoes, label_de, intervalo_amostragem_s)

    # Fase 16: reduz a ~1 evento PRINCIPAL por minuto (a ação que resume o minuto).
    # Os crus viram AUDITORIA; os principais alimentam validação e métricas.
    principais: list[dict] = []
    if os.environ.get("KV_PRINCIPAL_ENABLE", "on") not in ("off", "0", "false", "False"):
        try:
            principais = etapa_consolidar_principais(
                eventos_crus, catalogo, info_video["duracao_s"]
            )
        except Exception as e:  # noqa: BLE001
            log.warning(f"[principal] consolidação falhou (não-fatal): {e}")
            principais = []
    if principais:
        eventos, eventos_auditoria = principais, eventos_crus
    else:
        eventos, eventos_auditoria = eventos_crus, None
    progress_cb("segmentar", 100, f"{len(eventos_crus)} eventos → {len(eventos)} principais")

    progress_cb("persistir", 0, "Salvando no banco de dados")
    video_id, n_auto = etapa_persistir(
        sb,
        empresa,
        processo,
        video_path,
        info_video,
        eventos,
        ids_unicos,
        catalogo,
        origem_de,
        nome_video=nome_video,
        caminho_storage=caminho_storage,
        cam_id=cam_id,
        gravado_em=gravado_em,
        eventos_auditoria=eventos_auditoria,
    )
    progress_cb("persistir", 100, f"{len(eventos)} eventos · {n_auto} auto-validados")

    progress_cb("sugestoes", 0, "Gerando sugestões de produtividade")
    contexto = montar_contexto_agregado(
        sb,
        empresa,
        processo,
        catalogo=catalogo,
        descricao_processo=descricao,
        memoria=memoria,
        conhecimento_adquirido_texto=conhecimento,
        video_recem_processado={
            "nome": nome_video or Path(video_path).name,
            "duracao_s": round(info_video["duracao_s"], 1),
            "eventos_neste_video": len(eventos),
        },
    )
    sugestoes = etapa_gerar_sugestoes(sb, groq_client, empresa, processo, video_id, contexto)
    progress_cb("sugestoes", 100, f"{len(sugestoes)} sugestões geradas")

    # Classificação Lean dos comportamentos — NÃO-FATAL.
    # Roda antes das perguntas pra que a próxima execução já veja as categorias.
    try:
        n_classif = classificar_comportamentos_lean(
            sb,
            groq_client,
            empresa,
            processo,
            descricao_processo=descricao,
            conhecimento_adquirido=conhecimento,
        )
        if n_classif:
            log.info(f"Classificação Lean atualizada para {n_classif} comportamento(s).")
    except Exception as e:
        log.warning(f"Classificação Lean falhou (não-fatal): {e}")

    # Geração proativa de perguntas — NÃO-FATAL.
    n_perguntas = 0
    try:
        progress_cb("perguntas", 0, "Identificando lacunas pra perguntar ao cliente")
        # Fase 3: pergunta determinística por divergência de rótulo entre
        # câmeras (zero custo Groq, idempotente por signatura de par).
        try:
            gerar_pergunta_divergencia_camera(sb, empresa, processo)
        except Exception as e:
            log.warning(f"Pergunta de divergência multi-câmera falhou (não-fatal): {e}")
        novas_perguntas = gerar_perguntas_processo(
            sb,
            groq_client,
            empresa,
            processo,
            descricao_processo=descricao,
            memoria=memoria,
            catalogo=catalogo,
            observacoes_brutas=observacoes,
            contexto_agregado=contexto,
        )
        n_perguntas = len(novas_perguntas)
        progress_cb("perguntas", 100, f"{n_perguntas} perguntas geradas")
    except Exception as e:
        log.warning(f"Geração de perguntas falhou (não-fatal): {e}")

    # Computa apenas o que o próprio processar_video precisou consumir; os
    # blocos GLOBAIS (insights da empresa, padrões globais, padrões deste
    # processo) NÃO rodam mais aqui — entram numa fila com debounce.
    # Sem isso, uma rajada de 200 segmentos do edge_runner geraria ~600
    # chamadas extras a gpt-oss-120b, estourando o TPM do Groq Free Tier.
    # Veja backend/debouncer.py.
    try:
        from . import debouncer
        debouncer.marcar_dirty_empresa(empresa)
        debouncer.marcar_dirty_processo(empresa, processo)
    except Exception as e:
        log.warning(f"Debouncer falhou (não-fatal): {e}")

    progress_cb("concluido", 100, "Processamento concluído")
    return {
        "video_id": video_id,
        "n_eventos": len(eventos),
        "n_auto_validados": n_auto,
        "n_sugestoes": len(sugestoes),
        "n_perguntas": n_perguntas,
    }
