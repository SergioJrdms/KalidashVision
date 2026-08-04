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
from datetime import datetime, timedelta, timezone
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
# Fase 64: `KV_TRACKER=fixa` usa o perfil de CÂMERA FIXA (idêntico ao de
# fábrica, só com gmc_method: none). Default = arquivo de fábrica: trocar o
# tracker no meio da campanha de 30 dias é decisão do dono do dado, não efeito
# colateral de deploy. Env desconhecido cai no de fábrica, nunca quebra.
_TRACKER_FIXA = str(Path(__file__).resolve().parent / "trackers" / "botsort_camera_fixa.yaml")
TRACKER_CONFIG = (
    _TRACKER_FIXA
    if os.environ.get("KV_TRACKER", "").strip().lower() in ("fixa", "fixed", "camera_fixa")
    and Path(_TRACKER_FIXA).is_file()
    else "botsort.yaml"
)

# 5s (não 3s): ~40% menos chamadas ao VLM por vídeo → menos pressão de RPM/TPM
# no Groq Free Tier e fila drena mais rápido. Configurável via env.
DEFAULT_INTERVALO_AMOSTRAGEM_S = float(os.environ.get("KV_INTERVALO_AMOSTRAGEM_S", "5.0"))
DEFAULT_LIMIAR_AUTO_VALIDACAO = 2

# Fase 61 — origens que a MÁQUINA escreve. Nenhuma delas conta como evidência
# humana: não alimenta a memória de aprendizado e não pode marcar
# `validado_humano`/`validacao_correto`, que são a verdade de referência do
# dataset e do placar das camadas.
ORIGENS_MAQUINA = frozenset(
    {"correcao_aprendida", "vocabulario_canonico", "posto_vazio", "auditoria"}
)

# Fase 62 — GENERALIZAÇÃO AUTOMÁTICA, chave de liga/desliga por processo.
# Durante a campanha de coleta o objetivo é um dataset limpo rotulado por
# gente. Aprender em cima de dado ainda sujo, no meio da coleta, destrói
# justamente o ativo que a campanha existe para produzir — e com cinco
# mecanismos de aprendizado sobrepostos ninguém consegue prever o efeito de
# corrigir um evento. Desligado, o sistema classifica e a pessoa valida;
# nada se propaga sozinho. O código dos mecanismos continua todo aqui: isto
# é uma chave, não uma remoção.
# NULL na coluna do processo → cai neste default de ambiente.
APRENDIZADO_AUTO_PADRAO = os.environ.get("KV_APRENDIZADO_AUTO", "off") not in (
    "off", "0", "false", "False", "no",
)


def aprendizado_automatico(sb: Client, empresa: str, processo: str) -> bool:
    """Lê `contexto_processo.aprendizado_automatico`; NULL → default do env.

    Falha de leitura cai no default — e o default é DESLIGADO, então o modo
    seguro é o que vale quando não se sabe.
    """
    try:
        r = (
            sb.table("contexto_processo").select("aprendizado_automatico")
            .eq("empresa", empresa).eq("processo", processo).limit(1).execute().data
        ) or []
        if r and r[0].get("aprendizado_automatico") is not None:
            return bool(r[0]["aprendizado_automatico"])
    except Exception as e:
        log.warning(f"[aprendizado] leitura do flag falhou, usando default: {e}")
    return APRENDIZADO_AUTO_PADRAO

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
    categoria_relacionada text,            -- valor_agregado|desperdicio|null (Fase 49: binário)
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

-- Fase 49: classificação BINÁRIA — 'apoio' foi removido. Zera as decisões
-- antigas de 'apoio' (passam a "não classificado"; a IA/gestor reclassificam
-- como produtivo ou desperdício). Idempotente — nada quebra se rodar de novo.
update comportamentos   set categoria_lean = null, categoria_lean_origem = null where categoria_lean = 'apoio';
update eventos          set categoria_lean = null, categoria_lean_origem = null where categoria_lean = 'apoio';
update padroes_processo set categoria_relacionada = null where categoria_relacionada = 'apoio';

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

-- ════════════════════════════════════════════════════════════════════════
-- Fase 28: zonas nomeadas por câmera. Coordenadas normalizadas [0-1] no
-- ESPAÇO DO VÍDEO ENVIADO (recorte CAMn_ROI do edge). papel:
-- 'posto_operador' (onde o operador titular trabalha, máx. 1 ativa por
-- câmera), 'maquina' (contexto), 'interacao' (terceiros no posto).
-- ════════════════════════════════════════════════════════════════════════
create table if not exists zonas_camera (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    cam_id text not null,
    nome text not null,
    papel text not null,
    pts_rel jsonb not null,
    descricao_contexto text,
    frame_ref_w int,
    frame_ref_h int,
    ativo boolean not null default true,
    criado_em timestamptz default now(),
    atualizado_em timestamptz default now(),
    constraint zonas_papel_chk check (papel in ('posto_operador','maquina','interacao')),
    unique (empresa, processo, cam_id, nome)
);
create index if not exists idx_zonas_ctx on zonas_camera(empresa, processo, cam_id);

-- Fase 28: papel da pessoa no evento ('operador'|'visitante'|'posto_vazio'|null)
alter table eventos add column if not exists papel_pessoa text;
create index if not exists idx_eventos_papel on eventos(papel_pessoa);

-- Fase 82: de qual câmera são as coordenadas de `bbox_inicio` ('cam1'|'cam2')
-- e o resumo do corpo no evento (mediana das amostras + altura relativa).
alter table eventos add column if not exists bbox_cam   text;
alter table eventos add column if not exists bbox_stats jsonb;

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
  delete from zonas_camera
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
        "correcoes_confirmacoes": {},
        "descartados": {},
        "total_eventos_validados": 0,
    }

    # Fase 81 — a MEMÓRIA do processo não pode ser lida pela metade: é ela que
    # vai no prompt como "o que esta fábrica já ensinou". Truncada, o modelo
    # reaprende do zero o que já foi corrigido. `limite_eventos` deixa de ser
    # um teto de leitura (o PostgREST cortava em 1000 de qualquer jeito) e passa
    # a ser o corte aplicado DEPOIS, sobre o conjunto completo.
    linhas_val = varrer(
        sb, "eventos",
        "id, comportamento_label, label_corrigido, descricao_bruta, validacao_correto, "
        "principal, origem_validacao, descricao_invalida",
        empresa=empresa, processo=processo,
        ajustes=lambda q: q.eq("validado_humano", True),
    )[:max(1, limite_eventos)]
    # Fase 16: crus de auditoria entram como validado_humano=True; remove-os do
    # aprendizado (só principais/antigos contam).
    eventos = [e for e in linhas_val if e.get("principal") is not False]
    # Fase 61 — A MÁQUINA NÃO CONFIRMA A SI MESMA.
    # Todo evento auto-validado entrava aqui como "confirmação humana" e somava
    # em `n_confirmacoes` — o mesmo contador que libera a auto-validação. Ou
    # seja: uma vez cruzado o limiar, cada evento auto-validado reforçava o
    # limiar que o auto-validou. O contador só subia, nunca descia, e a
    # "evidência humana" virava eco da própria inferência.
    # Origem nula fica de fora do corte: é validação humana legada, anterior
    # ao campo `origem_validacao`.
    eventos = [
        e for e in eventos
        if (e.get("origem_validacao") or "humano") not in ORIGENS_MAQUINA
    ]
    memoria["total_eventos_validados"] = len(eventos)

    if not eventos:
        log.info(f"Memória vazia para {empresa}/{processo}.")
        return memoria

    confirmados: Counter = Counter()
    descartados: Counter = Counter()
    correcoes_brutas: dict[str, Counter] = {}

    # Fase 70 — FRASES QUEIMADAS. Descrição que um humano declarou INVÁLIDA (o
    # VLM alucinou a cena) nunca mais funda aprendizado nenhum. É o antídoto
    # permanente: mesmo quando o mecanismo declarativo existir, uma regra
    # fundamentada numa frase que nunca descreveu nada seria falsa.
    queimadas = {
        (ev.get("descricao_bruta") or "").strip().lower()
        for ev in eventos if ev.get("descricao_invalida")
    } - {""}

    for ev in eventos:
        correto = ev.get("validacao_correto")
        label_orig = ev.get("comportamento_label", "")
        label_corr = ev.get("label_corrigido")
        desc_bruta = (ev.get("descricao_bruta") or "").strip().lower()

        # Nada que venha de uma frase queimada entra na memória — nem como
        # correção, nem como confirmação, nem como descarte de rótulo. A frase
        # não descreve a cena, então nada derivado dela é evidência.
        if desc_bruta and desc_bruta in queimadas:
            continue
        if correto is False:
            descartados[label_orig] += 1
        elif correto is True:
            if label_corr and label_corr != label_orig:
                if desc_bruta:
                    correcoes_brutas.setdefault(desc_bruta, Counter())[label_corr] += 1
            else:
                confirmados[label_orig] += 1

    # Fase 61: guarda também QUANTAS vezes a correção vencedora foi feita. Sem
    # essa contagem não há como aplicar limiar — e sem limiar, uma correção
    # isolada vira regra global (foi exatamente o que aconteceu).
    memoria["correcoes_aprendidas"] = {
        desc: ctr.most_common(1)[0][0] for desc, ctr in correcoes_brutas.items()
    }
    memoria["correcoes_confirmacoes"] = {
        desc: ctr.most_common(1)[0][1] for desc, ctr in correcoes_brutas.items()
    }
    memoria["descartados"] = dict(descartados.most_common(20))
    memoria["descricoes_queimadas"] = sorted(queimadas)
    if queimadas:
        log.info("Memória: %d descrição(ões) QUEIMADAS (VLM alucinou) — fora do "
                 "aprendizado.", len(queimadas))

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


def _offset_entre_nomes(nome_prim: str | None, nome_sec: str | None) -> float:
    """Fase 30 — offset REAL de relógio entre os dois segmentos do par, em
    segundos: t0(prim) − t0(sec), parseado do token seg_YYYYMMDD_HHMMSS dos
    nomes. O instante relativo `tempo_s` da cam1 corresponde a
    `tempo_s + offset` na cam2: se a cam2 começou DEPOIS (t0_sec > t0_prim),
    offset < 0 e o mesmo instante real cai mais cedo no relógio relativo da
    cam2. Nome não parseável → 0.0 (assume alinhado, comportamento anterior)."""
    ta, tb = _seg_token_nome(nome_prim), _seg_token_nome(nome_sec)
    if not ta or not tb:
        return 0.0
    try:
        da, db = ta.split("_"), tb.split("_")
        dt_a = datetime(int(da[0][0:4]), int(da[0][4:6]), int(da[0][6:8]),
                        int(da[1][0:2]), int(da[1][2:4]), int(da[1][4:6]))
        dt_b = datetime(int(db[0][0:4]), int(db[0][4:6]), int(db[0][6:8]),
                        int(db[1][0:2]), int(db[1][2:4]), int(db[1][4:6]))
        return (dt_a - dt_b).total_seconds()
    except Exception:
        return 0.0


def _anexar_segundo_angulo(
    amostras: list,
    video_path_secundario: str,
    yolo=None,
    rois_sec: dict | None = None,
    offset_s: float = 0.0,
    desc_acc: dict | None = None,
) -> int:
    """Fase 6: para cada Amostra (da cam1), pega o frame da cam2 no MESMO
    instante REAL e guarda em `img_b64_secundario`. Retorna quantas amostras
    receberam o 2º ângulo. Defensivo: nunca levanta.

    Fase 30: os segmentos do par NÃO começam no mesmo segundo (podem diferir
    de segundos a minutos) — `offset_s = t0(cam1) − t0(cam2)` corrige:
    instante na cam2 = tempo_s + offset_s. Fora de [0, duração da cam2] →
    frame clampado só como contexto visual e `op_cam2=None` (não confirma
    nem nega — o desalinhado não pode rebaixar o operador).

    Fase 28: se `yolo` + `rois_sec` (com zona posto_operador) vierem, roda um
    predict LEVE no frame da cam2 e marca `am.op_cam2` — a cam2 é a câmera com
    PROFUNDIDADE que confirma se o operador está mesmo atrás da máquina
    (desambigua o outro torneiro visto por cima do torno na cam1).
    """
    n = 0
    posto_sec = None
    # Fase 84: o passe da cam2 passa a RASTREAR (e não só detectar) para que o
    # descritor tenha uma chave de agrupamento. Ver o comentário no laço.
    if yolo is not None and desc_acc is not None:
        log.debug("[descritor] tracker zerado antes do passe da cam2: %s",
                  resetar_tracker(yolo))
    if yolo is not None and rois_sec:
        _tem_posto = any(i.get("papel") == "posto_operador" for i in rois_sec.values())
        posto_sec = rois_sec if (_OPERADOR_FILTRO_ENABLE and _tem_posto) else None
    try:
        cap = cv2.VideoCapture(video_path_secundario)
        if not cap.isOpened():
            log.warning(f"2º ângulo: não abriu {video_path_secundario}")
            return 0
        dur_ms = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 30.0) * 1000.0
        if abs(offset_s) > 0.5:
            log.info(f"[dual-angle] offset de relógio cam1→cam2 = {offset_s:+.0f}s (alinhado pelo nome)")
        rois2 = None
        rois2_maq = None   # Fase 44: zonas 'maquina' da cam2 (mãos no torno)
        for idx, am in enumerate(amostras):
            alvo_ms = (am.tempo_s + offset_s) * 1000.0
            fora_da_cam2 = alvo_ms < 0 or (bool(dur_ms) and alvo_ms > dur_ms)
            if fora_da_cam2:  # instante não existe na cam2 — clampa (só imagem)
                alvo_ms = min(max(0.0, alvo_ms), max(0.0, dur_ms - 1.0))
            cap.set(cv2.CAP_PROP_POS_MSEC, alvo_ms)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            # Fase 33: anexa SEMPRE — amostras vazias na cam1 usam esta imagem
            # p/ o RESGATE pela lateral (a cam2 vê o operador que a cam1 não vê).
            am.img_b64_secundario = frame_para_base64(frame)
            n += 1
            # Confirmação do operador pela cam2 (Fase 28) — barata: predict
            # (sem tracker/estado) só nos slots de amostra, imgsz pequeno.
            if posto_sec is None or fora_da_cam2 or (idx % _CAM2_CONFIRM_STRIDE) != 0:
                continue
            try:
                if rois2 is None:
                    h2, w2 = frame.shape[:2]
                    rois2 = _build_rois(
                        {n2: i for n2, i in posto_sec.items()
                         if i.get("papel") == "posto_operador"},
                        w2, h2,
                    )
                    # Fase 44: zonas 'maquina' da cam2 p/ o sinal de mãos no torno
                    # (vazio = câmera sem zona de máquina desenhada → sinal off).
                    rois2_maq = _build_rois(
                        {n2: i for n2, i in posto_sec.items()
                         if i.get("papel") == "maquina"},
                        w2, h2,
                    )
                # Fase 84 — `track` no lugar de `predict`. Mesmo detector, mesmos
                # parâmetros, mesmas caixas: o veredito `op_cam2` não muda. O que
                # se ganha é o ID, sem o qual não existe "descritor POR TRACK" na
                # cam2 — e sem cam2 metade do experimento não existe.
                # Os frames vêm por seek, não em sequência: a associação erra
                # mais que na cam1 e os tracks da cam2 fragmentam mais. Para
                # agrupar-por-aparência-primeiro isso é aceitável; para contar
                # tempo por track, não seria.
                res = yolo.track(
                    frame, classes=[0], conf=_CAM2_CONF, imgsz=416,
                    persist=True, tracker=TRACKER_CONFIG, verbose=False,
                )
                achou = False
                maos = False
                bbox_no_posto = None      # Fase 82: a caixa de quem está no posto
                if res and res[0].boxes is not None and len(res[0].boxes) > 0:
                    boxes2 = res[0].boxes.xyxy.cpu().numpy()
                    ids2 = None
                    if res[0].boxes.id is not None:
                        try:
                            ids2 = res[0].boxes.id.cpu().numpy().astype(int)
                        except Exception:
                            ids2 = None
                    kpts2 = None
                    if getattr(res[0], "keypoints", None) is not None and \
                            res[0].keypoints.xyn is not None:
                        try:
                            kpts2 = res[0].keypoints.xyn.cpu().numpy()
                        except Exception:
                            kpts2 = None
                    h2, w2 = frame.shape[:2]
                    for j, b in enumerate(boxes2):
                        pessoa2 = {"bbox": tuple(int(v) for v in b.astype(int))}
                        if kpts2 is not None and j < len(kpts2):
                            pessoa2["kpts"] = kpts2[j].astype("float32")
                        # Fase 31: qualquer parte do corpo no posto conta.
                        pontos2 = _pontos_da_pessoa(pessoa2, w2, h2)
                        no_posto2 = any(
                            _ponto_em_roi(px, py, i["polygon"])
                            for i in rois2.values() for px, py in pontos2
                        )
                        if no_posto2:
                            achou = True
                            # Fase 82: guarda a MAIOR caixa dentro do posto. Com
                            # duas pessoas na zona, a maior é a mais próxima da
                            # câmera — mesmo critério de desempate que a eleição
                            # do titular já usa na cam1.
                            bx1, by1, bx2, by2 = pessoa2["bbox"]
                            area2 = max(0, bx2 - bx1) * max(0, by2 - by1)
                            if bbox_no_posto is None or area2 > bbox_no_posto[1]:
                                bbox_no_posto = (pessoa2["bbox"], area2)
                        # Fase 44: punho na zona 'maquina' da cam2 = operando.
                        if rois2_maq and not maos and _maos_na_maquina(pessoa2, rois2_maq, w2, h2):
                            maos = True
                        # Fase 84: descritor da cam2, do MESMO frame já decodificado
                        # e da MESMA inferência — custo adicional zero.
                        # Detecção sem id não vira descritor: sem chave estável,
                        # a linha seria uma pessoa diferente a cada amostra.
                        if desc_acc is not None and ids2 is not None and j < len(ids2):
                            pessoa2["frame_idx"] = None
                            acumular_descritor(
                                desc_acc, int(ids2[j]), frame=frame, pessoa=pessoa2,
                                w=w2, h=h2, tempo_s=am.tempo_s,
                                no_posto=no_posto2, papel=None,
                            )
                    if bbox_no_posto is not None:
                        am.bbox_cam2 = bbox_no_posto[0]
                        am.dim_cam2 = (w2, h2)
                am.op_cam2 = achou
                am.maos_cam2 = maos
            except Exception as e:
                log.warning(f"[operador] confirmação cam2 falhou no slot {am.tempo_s:.0f}s ({e})")
                am.op_cam2 = None
        cap.release()
    except Exception as e:
        log.warning(f"2º ângulo falhou ({e}) — segue só com a cam1")
    return n


def etapa_confirmar_operador(amostras: list, politica: str) -> dict:
    """Fase 28/33 — veredito POR SLOT, usando AS DUAS câmeras SIMETRICAMENTE
    (tracks não cruzam câmeras; o veredito é por instante):
      op_cam1 = alguma pessoa da amostra tem papel 'operador'.
      politica 'cam1' : presente = op_cam1.
      politica 'dupla': presente = op_cam1 OU op_cam2 — qualquer câmera que
        veja o operador no posto ESTABELECE a presença (Fase 33; antes a cam2
        só podia negar, e oclusão total na cam1 virava posto_vazio falso):
        · op_cam1 e op_cam2 True/None → presente (cam2 confirma ou não opina);
        · op_cam1 e op_cam2 False → é o OUTRO torneiro visto por cima do torno
          na cam1: REBAIXA (remove as pessoas 'operador'); visitantes seguem;
        · sem op_cam1 mas op_cam2 True → RESGATE: presente=True mesmo sem
          pessoa 'operador' na cam1 (a etapa VLM descreve a ação pela imagem
          da lateral — track sintético OPERADOR_CAM2_TID);
        · nenhuma das duas → ausente (candidato a posto_vazio).

    Fase 30 — GUARDRAIL: avalia em 2 fases (decidir → aplicar). Se a cam2
    negaria mais que KV_CAM2_REBAIXA_MAX dos slots em que a cam1 viu o
    operador, o problema é sistêmico (desalinhamento de relógio, zona errada,
    câmera mexida) — aí IGNORA a negação da cam2 no vídeo inteiro em vez de
    zerar tudo como posto_vazio (o RESGATE positivo continua valendo).
    Marca am.operador_presente. Retorna stats p/ log."""
    # Fase 1: decidir sem mutar.
    decisoes = []   # (am, op_cam1, rebaixaria, resgataria)
    n_op_cam1 = n_rebaixaria = 0
    for am in amostras:
        op_cam1 = any(p.get("papel") == "operador" for p in am.pessoas)
        rebaixaria = politica == "dupla" and op_cam1 and am.op_cam2 is False
        resgataria = politica == "dupla" and not op_cam1 and am.op_cam2 is True
        n_op_cam1 += 1 if op_cam1 else 0
        n_rebaixaria += 1 if rebaixaria else 0
        decisoes.append((am, op_cam1, rebaixaria, resgataria))

    # Guardrail só com massa mínima (≥5 slots com operador): em vídeos muito
    # curtos a fração é ruidosa e o impacto de confiar na cam2 é pequeno.
    aplicar_negacao = politica == "dupla"
    if aplicar_negacao and n_op_cam1 >= 5 and (n_rebaixaria / n_op_cam1) > _CAM2_REBAIXA_MAX:
        log.warning(
            "[operador] GUARDRAIL: a cam2 negaria %d de %d slots com operador "
            "(>%d%%) — provável desalinhamento/zona errada na cam2. Ignorando a "
            "NEGAÇÃO da cam2 neste vídeo (o resgate positivo continua valendo).",
            n_rebaixaria, n_op_cam1, int(_CAM2_REBAIXA_MAX * 100),
        )
        aplicar_negacao = False

    # Fase 2: aplicar.
    stats = {"slots": len(amostras), "presentes": 0, "vazios": 0,
             "rebaixados": 0, "resgatados_cam2": 0, "pontes": 0}
    for am, op_cam1, rebaixaria, resgataria in decisoes:
        if aplicar_negacao and rebaixaria:
            am.pessoas = [p for p in am.pessoas if p.get("papel") != "operador"]
            stats["rebaixados"] += 1
            op_cam1 = False
        presente = op_cam1 or resgataria
        if resgataria and not op_cam1:
            stats["resgatados_cam2"] += 1
        am.operador_presente = presente
        am.operador_ponte = False

    # Fase 34: PONTE TEMPORAL — o operador não se teletransporta. Ausência de
    # até _OPERADOR_GAP_SLOTS slots ENTRE duas presenças vira presença (o
    # YOLO "pisca" em oclusão momentânea; cada piscada virava posto_vazio).
    if _OPERADOR_GAP_SLOTS > 0 and len(amostras) > 2:
        pres = [bool(a.operador_presente) for a in amostras]
        for i, a in enumerate(amostras):
            if pres[i]:
                continue
            antes = any(pres[j] for j in range(max(0, i - _OPERADOR_GAP_SLOTS), i))
            depois = any(
                pres[j] for j in range(i + 1, min(len(pres), i + 1 + _OPERADOR_GAP_SLOTS))
            )
            if antes and depois:
                a.operador_presente = True
                a.operador_ponte = True
                stats["pontes"] += 1

    for am in amostras:
        if am.operador_presente:
            stats["presentes"] += 1
        elif not am.pessoas:
            stats["vazios"] += 1
    return stats


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
# Fase 28 — análise focada no OPERADOR titular do posto.
# Zonas com papel (posto_operador/maquina/interacao) classificam cada pessoa:
# no posto = operador (1 por câmera, eleito por presença acumulada);
# em interacao = visitante; fora de tudo = transeunte (descartado na raiz).
# ═════════════════════════════════════════════════════════════════════════
_OPERADOR_FILTRO_ENABLE = os.environ.get("KV_OPERADOR_FILTRO_ENABLE", "on") not in ("off", "0", "false", "False", "")
_OPERADOR_CONFIRMACAO = os.environ.get("KV_OPERADOR_CONFIRMACAO", "dupla")   # dupla | cam1
_POSTO_VAZIO_ENABLE = os.environ.get("KV_POSTO_VAZIO_ENABLE", "on") not in ("off", "0", "false", "False", "")
# Fase 45: RECALL do operador. Ocluso pela máquina/escuro, ele fica com pouca
# confiança/área no YOLO e some do frame — vira posto_vazio FALSO (o pior erro:
# marca ausente quem está presente). Em modo operador as zonas já descartam
# transeuntes, então baixar o corte de detecção só ajuda a NÃO perder o titular.
_OPERADOR_CONF = float(os.environ.get("KV_OPERADOR_CONF", "0.30"))            # < YOLO_CONF_MIN (0.45)
_OPERADOR_AREA_MIN_RATIO = float(os.environ.get("KV_OPERADOR_AREA_MIN_RATIO", "0.0015"))  # < AREA_MIN_RATIO (0.005)
_CAM2_CONF = float(os.environ.get("KV_CAM2_CONF", "0.35"))
_CAM2_CONFIRM_STRIDE = max(1, int(os.environ.get("KV_CAM2_CONFIRM_STRIDE", "1")))
# Fase 30: guardrail — se a cam2 negar mais que esta fração dos slots em que a
# cam1 viu o operador, algo está errado (desalinhamento/zona) → ignora a cam2
# no vídeo inteiro em vez de zerar tudo como posto_vazio.
_CAM2_REBAIXA_MAX = float(os.environ.get("KV_CAM2_REBAIXA_MAX", "0.8"))
# Fase 34: PONTE TEMPORAL — operador não se teletransporta. Ausência de até N
# slots ENTRE duas presenças confirmadas conta como presente (o YOLO "pisca"
# em oclusão momentânea; sem a ponte, cada piscada virava posto_vazio falso).
_OPERADOR_GAP_SLOTS = max(0, int(os.environ.get("KV_OPERADOR_GAP_SLOTS", "3")))

POSTO_VAZIO_LABEL = "posto_vazio"
POSTO_VAZIO_TID = -1
POSTO_VAZIO_DESC = "posto de trabalho vazio (operador ausente)"
# Fase 33: operador estabelecido pela CÂMERA LATERAL (cam1 não o detectou —
# oclusão total pela máquina — mas a cam2 o vê na zona do posto).
OPERADOR_CAM2_TID = -2


def _modo_operador(rois: dict) -> bool:
    """True quando a análise deve focar no operador: flag global ligada E
    existe zona ativa com papel posto_operador nesta câmera."""
    if not _OPERADOR_FILTRO_ENABLE:
        return False
    return any((info.get("papel") == "posto_operador") for info in rois.values())


def _ponto_ancora(pessoa: dict, w: int, h: int) -> tuple[float, float]:
    """Ponto que representa a pessoa no teste de zona, robusto à OCLUSÃO pela
    máquina (o operador atrás do torno tem só tronco/cabeça visíveis; o bbox
    dele termina em cima da máquina, enquanto um transeunte à frente tem o
    corpo inteiro). Prioridade:
      1) ponto médio dos OMBROS (kpts COCO 5 e 6) quando ambos válidos;
      2) um ombro só, se apenas um é válido;
      3) NARIZ (kpt 0) deslocado 10% da altura do bbox para baixo (≈ pescoço);
      4) sem pose: topo-do-tronco do bbox → (cx, y1 + 0.30*(y2-y1)).
    kpts vêm normalizados (xyn) — retorna em PIXELS."""
    x1, y1, x2, y2 = pessoa["bbox"]
    kpts = pessoa.get("kpts")
    if kpts is not None and len(kpts) >= 7:
        le, ld = kpts[5], kpts[6]   # ombro esq/dir (x, y) normalizados
        val_e = le[0] > 0 and le[1] > 0
        val_d = ld[0] > 0 and ld[1] > 0
        if val_e and val_d:
            return ((le[0] + ld[0]) / 2 * w, (le[1] + ld[1]) / 2 * h)
        if val_e:
            return (le[0] * w, le[1] * h)
        if val_d:
            return (ld[0] * w, ld[1] * h)
        nariz = kpts[0]
        if nariz[0] > 0 and nariz[1] > 0:
            return (nariz[0] * w, nariz[1] * h + 0.10 * max(0, y2 - y1))
    return ((x1 + x2) / 2.0, y1 + 0.30 * max(0, y2 - y1))


def _fracao_inferior_visivel(kpts) -> float:
    """Fração de keypoints INFERIORES válidos (quadris/joelhos/tornozelos,
    COCO 11-16). Operador ocluso pela máquina ≈ 0.0; transeunte de corpo
    inteiro ≈ 1.0. Usado só em telemetria/log nesta fase."""
    if kpts is None or len(kpts) < 17:
        return 0.0
    baixos = [kpts[i] for i in range(11, 17)]
    validos = sum(1 for k in baixos if k[0] > 0 and k[1] > 0)
    return validos / 6.0


def _pontos_da_pessoa(pessoa: dict, w: int, h: int) -> list[tuple[float, float]]:
    """Fase 31 — TODOS os pontos que representam a pessoa no teste de zona:
    os 17 keypoints COCO válidos (punhos, cotovelos, tornozelos, joelhos...)
    em pixels + a âncora (`_ponto_ancora`) como garantia p/ pose parcial/sem
    pose. Semântica pedida: UM PÉ ou UM BRAÇO dentro da zona já conta como
    "a pessoa estava ali"."""
    pontos: list[tuple[float, float]] = []
    kpts = pessoa.get("kpts")
    if kpts is not None:
        for k in kpts:
            if k[0] > 0 and k[1] > 0:
                pontos.append((float(k[0]) * w, float(k[1]) * h))
    pontos.append(_ponto_ancora(pessoa, w, h))
    return pontos


def _zona_da_pessoa(pontos: list[tuple[float, float]], rois: dict) -> tuple[str | None, str | None, str | None]:
    """(nome_zona, papel, descricao) da pessoa: pertence à zona se QUALQUER
    um dos seus pontos (kpts + âncora — Fase 31) cair no polígono. Prioridade
    posto_operador > interacao (pé no posto + corpo na interação → posto).
    Zona 'maquina' NÃO classifica pessoa (é contexto da cena). None em tudo =
    fora das áreas de interesse."""
    achado: tuple[str | None, str | None, str | None] = (None, None, None)
    for nome, info in rois.items():
        papel = info.get("papel")
        if papel not in ("posto_operador", "interacao"):
            continue
        if any(_ponto_em_roi(px, py, info["polygon"]) for px, py in pontos):
            if papel == "posto_operador":
                return (nome, papel, info.get("descricao_contexto"))
            achado = (nome, papel, info.get("descricao_contexto"))
    return achado


# Fase 44 — MÃOS NA MÁQUINA: a zona 'maquina' (torno) desenhada em cima do
# equipamento não classifica a pessoa (é cenário), MAS se um PUNHO do operador
# cai dentro dela, ele está manipulando/operando — mesmo com o TRONCO na zona
# do posto. Sinal geométrico (pose) que desfaz o falso "esperar_ciclo_maquina".
_MAOS_KPTS = (9, 10)   # punhos COCO (esquerdo, direito)


def _maos_na_maquina(pessoa: dict, rois: dict, w: int, h: int) -> bool:
    """True se um dos PUNHOS (kpts COCO 9/10) do operador cair em ALGUMA zona
    com papel 'maquina'. Sem pose ou sem punhos válidos → False (o VLM decide
    pela imagem, como antes)."""
    kpts = pessoa.get("kpts")
    if kpts is None or len(kpts) <= max(_MAOS_KPTS):
        return False
    maos = [(float(kpts[i][0]) * w, float(kpts[i][1]) * h)
            for i in _MAOS_KPTS if kpts[i][0] > 0 and kpts[i][1] > 0]
    if not maos:
        return False
    for info in rois.values():
        if info.get("papel") == "maquina" and any(
            _ponto_em_roi(px, py, info["polygon"]) for px, py in maos
        ):
            return True
    return False


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


# Fase 28 — modo OPERADOR: P1 é sempre o operador titular do posto; P2+ são
# pessoas DENTRO da área do posto interagindo com ele. Transeuntes nem chegam
# ao prompt (foram filtrados na detecção).
PROMPT_VLM_OPERADOR = """Você é um analista de processos industriais observando UM posto de trabalho específico.
Na imagem, P1 é o OPERADOR TITULAR deste posto (ele trabalha nesta máquina).
Outras pessoas marcadas (P2, P3, ...), se existirem, estão DENTRO da área do posto interagindo com ele.

Descreva em UMA FRASE CURTA (até 10 palavras) o que cada pessoa marcada está fazendo:
- P1 (o operador): a AÇÃO dele no posto (verbo + objeto), ex.: "monitorando o ciclo da máquina", "observando a operação", "operando o torno", "medindo a peça".
- P2+ (se houver): a INTERAÇÃO com o posto/operador, ex.: "conversando com o operador", "entregando material ao posto".

{bloco_processo}{bloco_vocabulario}REGRAS:
- DISTINÇÃO CRÍTICA (operar × monitorar): só diga que ele está OPERANDO, manipulando, preparando, ajustando ou medindo se você VÊ as MÃOS dele na máquina, na ferramenta ou na peça, em ação. Se ele está PARADO, de pé, braços ao lado do corpo, apenas OLHANDO/acompanhando a máquina ou a área, é "monitorando o ciclo da máquina" ou "observando a operação" — NÃO é operar. Na dúvida entre operar e monitorar, escolha MONITORAR.
- EXCEÇÃO (o CONTEXTO manda): se o CONTEXTO abaixo disser que ele está com as MÃOS na máquina/torno, isso vem da posição REAL das mãos dele (sensor) — então ele ESTÁ operando/manipulando/ajustando o equipamento; descreva a ação de OPERAR, mesmo que na imagem o corpo pareça só de pé. Não diga "monitorando" nesse caso.
- Foque na AÇÃO, não na aparência.
- Use linguagem operacional clara em português.
- Se a ação não estiver clara, escreva "ação não identificada".
- NÃO invente ações que não estão visíveis.

CONTEXTO: {contexto_zonas}

Responda APENAS um JSON no formato:
{{"acoes": {{"P1": "...", "P2": "...", ...}}}}"""


PROMPT_VLM_DUAL_OPERADOR = """Você é um analista de processos industriais observando UM posto de trabalho específico.
Você recebe DUAS IMAGENS do MESMO posto e MESMO instante, de CÂMERAS DIFERENTES:
- IMAGEM 1 (câmera principal): P1 é o OPERADOR TITULAR deste posto. Outras pessoas marcadas (P2, P3, ...), se existirem, estão DENTRO da área do posto interagindo com ele.
- IMAGEM 2 (câmera lateral): a MESMA cena com visão clara da área de trabalho ATRÁS da máquina, SEM rótulos — use-a para confirmar o que o operador realmente está fazendo (a máquina esconde parte do corpo dele na IMAGEM 1).

Descreva em UMA FRASE CURTA (até 10 palavras) o que cada pessoa marcada está fazendo:
- P1 (o operador): a AÇÃO dele no posto (verbo + objeto), ex.: "monitorando o ciclo da máquina", "observando a operação", "operando o torno", "medindo a peça".
- P2+ (se houver): a INTERAÇÃO com o posto/operador, ex.: "conversando com o operador", "entregando material ao posto".

{bloco_processo}{bloco_vocabulario}REGRAS:
- DISTINÇÃO CRÍTICA (operar × monitorar): só diga que ele está OPERANDO, manipulando, preparando, ajustando ou medindo se você VÊ as MÃOS dele na máquina, na ferramenta ou na peça, em ação — em QUALQUER um dos dois ângulos. Se, mesmo vendo os dois ângulos, ele está PARADO, de pé, braços ao lado do corpo, apenas OLHANDO/acompanhando a máquina ou a área, é "monitorando o ciclo da máquina" ou "observando a operação" — NÃO é operar. Na dúvida entre operar e monitorar, escolha MONITORAR.
- EXCEÇÃO (o CONTEXTO manda): se o CONTEXTO abaixo disser que ele está com as MÃOS na máquina/torno, isso vem da posição REAL das mãos dele (sensor) — então ele ESTÁ operando/manipulando/ajustando o equipamento; descreva a ação de OPERAR, mesmo que na imagem o corpo pareça só de pé. Não diga "monitorando" nesse caso.
- Foque na AÇÃO, não na aparência.
- Use linguagem operacional clara em português.
- Os rótulos P1, P2 referem-se SEMPRE às pessoas marcadas na IMAGEM 1.
- Se mesmo com os dois ângulos a ação não estiver clara, escreva "ação não identificada".
- NÃO invente ações que não estão visíveis em nenhum dos ângulos.

CONTEXTO: {contexto_zonas}

Responda APENAS um JSON no formato:
{{"acoes": {{"P1": "...", "P2": "...", ...}}}}"""


# Fase 33 — RESGATE pela lateral: a cam1 não detectou o operador (oclusão
# total pela máquina), mas a cam2 o vê dentro da zona do posto. A ação é
# descrita pela IMAGEM DA CÂMERA LATERAL.
PROMPT_VLM_OPERADOR_CAM2 = """Você é um analista de processos industriais observando UM posto de trabalho pela CÂMERA LATERAL (com profundidade).
O OPERADOR TITULAR do posto está DENTRO da área de trabalho dele, atrás da máquina — visível nesta imagem (a câmera frontal não o enxerga neste instante porque a máquina o esconde).

Descreva em UMA FRASE CURTA (até 10 palavras) o que o OPERADOR está fazendo (verbo + objeto), ex.: "monitorando o ciclo da máquina", "observando a operação", "operando o torno", "medindo a peça".

{bloco_processo}{bloco_vocabulario}REGRAS:
- DISTINÇÃO CRÍTICA (operar × monitorar): só diga que ele está OPERANDO, manipulando, preparando, ajustando ou medindo se você VÊ as MÃOS dele na máquina, na ferramenta ou na peça, em ação. Se ele está PARADO, de pé, braços ao lado do corpo, apenas OLHANDO/acompanhando a máquina ou a área, é "monitorando o ciclo da máquina" ou "observando a operação" — NÃO é operar. Na dúvida entre operar e monitorar, escolha MONITORAR.
- EXCEÇÃO (o CONTEXTO manda): se o CONTEXTO abaixo disser que ele está com as MÃOS na máquina/torno, isso vem da posição REAL das mãos dele (sensor) — então ele ESTÁ operando/manipulando/ajustando o equipamento; descreva a ação de OPERAR, mesmo que na imagem o corpo pareça só de pé. Não diga "monitorando" nesse caso.
- Foque na AÇÃO do operador (a pessoa junto à máquina, na área de trabalho).
- Use linguagem operacional clara em português.
- Se a ação não estiver clara, escreva "ação não identificada".
- NÃO invente ações que não estão visíveis.

CONTEXTO: {contexto_zonas}

Responda APENAS um JSON no formato:
{{"acao": "..."}}"""


def _analisar_operador_cam2(
    groq_client: Groq,
    img_cam2_b64: str,
    descricao_processo: str,
    memoria: dict,
    conhecimento_adquirido: str = "",
    zona_desc: str | None = None,
    maos_maquina: bool = False,
) -> str | None:
    """Fase 33: descreve a ação do operador PELA IMAGEM DA CAM2 (resgate).
    None em falha (o slot cai para posto_vazio se nada mais o cobrir).
    Fase 44: `maos_maquina` (punho na zona 'maquina' da cam2) informa ao VLM
    que ele está OPERANDO, mesmo sem a cam1."""
    contexto = zona_desc or "área de trabalho do operador, atrás da máquina"
    if maos_maquina:
        contexto += (" — e está com as MÃOS na máquina (torno), tocando/"
                     "manipulando o equipamento (logo, OPERANDO, não apenas monitorando)")
    prompt = PROMPT_VLM_OPERADOR_CAM2.format(
        bloco_processo=construir_bloco_dominio(descricao_processo, conhecimento_adquirido),
        bloco_vocabulario=construir_bloco_vocabulario(memoria),
        contexto_zonas=contexto,
    )
    try:
        resposta = groq_vision_call(
            groq_client, img_cam2_b64, prompt, json_mode=True, max_tokens=200,
        )
        acao = (json.loads(resposta).get("acao") or "").strip().lower()
        return acao or None
    except Exception as e:
        log.warning(f"[operador] resgate pela cam2 falhou: {e}")
        return None


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
    # Fase 70: frase queimada não volta como "vocabulário conhecido" — seria
    # ensinar ao VLM a mesma alucinação que o humano acabou de rejeitar.
    _queimadas = set(memoria.get("descricoes_queimadas") or [])
    linhas = [
        "VOCABULÁRIO OPERACIONAL CONHECIDO deste cliente (use estes termos quando a ação corresponder, mantendo consistência com observações anteriores):"
    ]
    for v in memoria["vocabulario"][:max_itens]:
        if (v.get("descricao") or "").strip().lower() in _queimadas:
            continue
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

    # ═══════════════════════════════════════════════════════════════
    # Fase 67 — O BLOCO DE "CORREÇÕES APRENDIDAS" FOI REMOVIDO DAQUI.
    #
    # Ele dizia ao modelo, em texto:
    #     descrição "operando o torno, manipulando a máquina" → label
    #     CORRETO: posto_vazio    (use quando ver descrições PARECIDAS)
    #
    # Era o caminho que a chave da Fase 62 NÃO alcançava. A chave desligava
    # o remapeamento DETERMINÍSTICO (o dict `correcoes`), mas o prompt
    # continuava recebendo o mesmo mapa e mandando o modelo generalizar —
    # e generalizar SEMANTICAMENTE, o que é pior que casar texto exato:
    # "operando o torno, manipulando a máquina E FERRAMENTA" é "parecida".
    #
    # A raiz, porém, é anterior à chave: chavear correção por
    # `descricao_bruta` é errado em qualquer modo. Quando o VLM ALUCINA a
    # descrição (descreveu alguém operando com o posto vazio) e o humano
    # corrige o RÓTULO, o sistema aprende que a frase mais frequente do
    # dataset significa outra coisa — e envenena todos os usos corretos
    # dela. Uma correção vale para o EVENTO corrigido e nada mais, até
    # existir o mecanismo declarativo.
    #
    # `memoria["correcoes_aprendidas"]` continua sendo CALCULADO: serve de
    # diagnóstico (é o que a limpeza usa para achar o contágio) e será a
    # matéria-prima do mecanismo declarativo. O que não pode é virar regra.
    # ═══════════════════════════════════════════════════════════════

    # `descartados` só entra com a generalização LIGADA: é a decisão de um
    # humano num evento virando instrução para todos os vídeos seguintes —
    # a mesma classe de propagação, ainda que menos destrutiva.
    if memoria.get("descartados") and memoria.get("_generalizar", True):
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
    op_cam2: bool | None = None             # Fase 28: operador visto no posto pela cam2
    maos_cam2: bool = False                  # Fase 44: punho na zona 'maquina' pela cam2
    operador_presente: bool | None = None   # Fase 28: veredito do slot (pós-confirmação)
    operador_ponte: bool = False            # Fase 34: presença por PONTE temporal
    # Fase 82: a caixa da pessoa que ESTABELECEU a presença na cam2. O detector
    # já a calculava e ela era descartada no `if` que só guardava o booleano —
    # no resgate pela cam2 o evento nascia sem nenhuma medida do corpo.
    # Coordenadas no referencial da CAM2; `dim_cam2` = (largura, altura) do
    # frame, sem o qual a caixa não é comparável entre câmeras/resoluções.
    bbox_cam2: tuple | None = None
    dim_cam2: tuple | None = None
    # Dimensões do frame da cam1 (largura, altura). Uma altura de bbox em pixels
    # não significa nada sem a altura do quadro: 300px num frame de 480 e num de
    # 1080 são pessoas de tamanhos aparentes completamente diferentes.
    dim: tuple | None = None


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


# ═════════════════════════════════════════════════════════════════════════
# Gate de repetição do VLM (Fase 23) — config + helpers puros
# A mudança de ação é o evento; a repetição do padrão só é contabilizada.
# Um classificador barato (pose local grátis; VLM binário na dúvida) decide
# se a amostra repete a ÚLTIMA analisada (padrão) ou é diferente (mudança de
# contexto). Só as diferentes pagam a análise completa. Feature-flag: default
# OFF → pipeline idêntico ao de hoje.
# ═════════════════════════════════════════════════════════════════════════
_GATE_ENABLE = os.environ.get("KV_GATE_ENABLE", "off") not in ("off", "0", "false", "False", "")
_GATE_LIMIAR_IGUAL = float(os.environ.get("KV_GATE_LIMIAR_IGUAL", "0.12"))
_GATE_LIMIAR_DIFERENTE = float(os.environ.get("KV_GATE_LIMIAR_DIFERENTE", "0.30"))
_GATE_PESO_POSE = float(os.environ.get("KV_GATE_PESO_POSE", "0.6"))
_GATE_PESO_MOV = float(os.environ.get("KV_GATE_PESO_MOV", "0.4"))
_GATE_MOV_REF = float(os.environ.get("KV_GATE_MOV_REF", "18.0"))   # absdiff cinza "muito diferente"


# ═════════════════════════════════════════════════════════════════════════
# Fase 83 — DESCRITOR POR TRACK. Só o que a detecção JÁ calcula.
#
# Nenhum modelo novo, nenhuma inferência a mais: os keypoints vêm do
# yolo11n-POSE em toda detecção e eram descartados; o recorte já é feito para o
# gate (e virava cinza, jogando a cor fora); altura e aspecto já estavam no
# bbox_stats; o tempo na zona já era contado para eleger o titular.
#
# NÃO identifica ninguém. É o insumo do experimento de separabilidade.
# ═════════════════════════════════════════════════════════════════════════

# COCO-17: 0 nariz · 5/6 ombros · 11/12 quadris.
_KP_NARIZ, _KP_OMB_E, _KP_OMB_D, _KP_QUA_E, _KP_QUA_D = 0, 5, 6, 11, 12

# Segmento mínimo, em pixels, para uma razão ser aceita. Abaixo disso o
# denominador é ruído de detecção e a razão explode.
_RAZAO_MIN_PX = float(os.environ.get("KV_RAZAO_MIN_PX", "12"))


def _kp_px(kpts, i: int, w: int, h: int):
    """Keypoint i em PIXELS, ou None se não detectado.

    `xyn` é normalizado pela LARGURA em x e pela ALTURA em y — escalas
    diferentes num frame não quadrado. Medir distância direto no normalizado
    distorce o eixo horizontal contra o vertical e estraga justamente as razões
    que deveriam ser invariantes. Voltar para pixel é obrigatório, não detalhe.

    Keypoint não detectado vem (0,0) — o mesmo tipo de zero mentiroso que a
    caixa tinha. Filtrado aqui.
    """
    try:
        x, y = float(kpts[i][0]), float(kpts[i][1])
    except Exception:
        return None
    if x <= 0.0 or y <= 0.0:
        return None
    return (x * w, y * h)


def _dist(a, b) -> float | None:
    if a is None or b is None:
        return None
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _meio(a, b):
    if a is None or b is None:
        return None
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def razoes_corporais(kpts, w: int, h: int) -> dict:
    """Razões entre segmentos do corpo — adimensionais, logo invariantes à
    distância da câmera (que é o confundidor que a altura aparente não resolve).

    CRITÉRIO DE ESCOLHA (por que estas e não outras):
      1. Só landmarks RÍGIDOS. Ombro, quadril e nariz não se articulam entre si.
         Cotovelo, punho, joelho e tornozelo estão fora: mudam de posição com a
         ação, não com a pessoa — e, neste enquadramento, ficam atrás do torno
         na maior parte do tempo.
      2. Razão, nunca medida absoluta. Dividir cancela a escala; é o ponto
         inteiro do exercício.
      3. Preferir MESMO EIXO quando dá. `quadril_ombro` é horizontal ÷
         horizontal: não se altera quando a pessoa se inclina para a frente
         (o que encurta a projeção vertical do tronco). As razões que misturam
         eixos são mais informativas e menos estáveis — por isso cada uma vai
         com a sua dispersão, e o experimento decide o peso.
      4. Denominador com tamanho mínimo (`_RAZAO_MIN_PX`), senão a razão é
         ruído dividido por ruído.

    O que NÃO é invariante e precisa estar dito: nada disto sobrevive a uma
    rotação grande do corpo (yaw). De costas, a largura de ombros projetada
    encolhe. A dispersão por track é a medida disso.
    """
    if kpts is None:
        return {}
    omb_e, omb_d = _kp_px(kpts, _KP_OMB_E, w, h), _kp_px(kpts, _KP_OMB_D, w, h)
    qua_e, qua_d = _kp_px(kpts, _KP_QUA_E, w, h), _kp_px(kpts, _KP_QUA_D, w, h)
    nariz = _kp_px(kpts, _KP_NARIZ, w, h)
    ombros = _dist(omb_e, omb_d)
    quadris = _dist(qua_e, qua_d)
    tronco = _dist(_meio(omb_e, omb_d), _meio(qua_e, qua_d))
    cabeca = _dist(nariz, _meio(omb_e, omb_d))

    out: dict[str, float] = {}
    if ombros and tronco and tronco >= _RAZAO_MIN_PX:
        out["ombro_tronco"] = round(ombros / tronco, 4)
    if quadris and ombros and ombros >= _RAZAO_MIN_PX:
        out["quadril_ombro"] = round(quadris / ombros, 4)
    if cabeca and tronco and tronco >= _RAZAO_MIN_PX:
        out["cabeca_tronco"] = round(cabeca / tronco, 4)
    return out


# Histograma de cor: HSV, matiz × saturação. V (brilho) fica de fora de
# propósito — é ele que muda entre a luz das 6h e a das 15h, exatamente a
# variação que não pode virar "outra pessoa".
_HIST_BINS_H = int(os.environ.get("KV_HIST_BINS_H", "8"))
_HIST_BINS_S = int(os.environ.get("KV_HIST_BINS_S", "4"))
# Faixa central da caixa usada no histograma. A bbox de uma pessoa é um
# retângulo com fundo nos cantos; a coluna central é quase toda corpo.
_HIST_FAIXA = float(os.environ.get("KV_HIST_FAIXA", "0.6"))


def _hist_hs(sub) -> list | None:
    """Histograma H×S normalizado (soma 1) de um recorte BGR."""
    if sub is None or sub.size == 0:
        return None
    try:
        hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None,
                            [_HIST_BINS_H, _HIST_BINS_S], [0, 180, 0, 256])
        total = float(hist.sum())
        if total <= 0:
            return None
        return [round(float(v) / total, 5) for v in hist.flatten()]
    except Exception:
        return None


def histograma_cor(frame, bbox) -> dict | None:
    """Cor da METADE SUPERIOR e da METADE INFERIOR da pessoa, separadas —
    camisa e calça. O recorte do gate existe desde a Fase 23 mas é convertido
    para CINZA (`_crop_cinza_pequeno`): a cor, que é o descritor clássico de
    reidentificação com câmera fixa, era jogada fora ali.

    Ressalva honesta para o experimento: com uniforme igual nos dois torneiros,
    isto não separa ninguém. Vai junto porque é o mais barato de todos e porque
    a resposta "não separa" também é resultado.
    """
    if not _bbox_valido(bbox):
        return None
    x1, y1, x2, y2 = (int(v) for v in bbox[:4])
    h, w = frame.shape[:2]
    x1 = max(0, min(x1, w - 1)); x2 = max(x1 + 1, min(x2, w))
    y1 = max(0, min(y1, h - 1)); y2 = max(y1 + 1, min(y2, h))
    larg = x2 - x1
    margem = int(larg * (1.0 - _HIST_FAIXA) / 2)
    cx1, cx2 = x1 + margem, max(x1 + margem + 1, x2 - margem)
    meio = y1 + (y2 - y1) // 2
    sup = _hist_hs(frame[y1:meio, cx1:cx2])
    inf = _hist_hs(frame[meio:y2, cx1:cx2])
    if sup is None and inf is None:
        return None
    return {"sup": sup, "inf": inf}


def _mediana(v: list) -> float | None:
    if not v:
        return None
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


# Abaixo disto não há dispersão para medir: 1 amostra dá MAD 0, e 2 dão um
# número que é metade da diferença entre duas medidas — nenhum dos dois é uma
# estimativa de estabilidade.
_MAD_MIN_N = 3


def _mad(v: list, med: float) -> float | None:
    """Desvio absoluto mediano — dispersão que não é sequestrada por um frame
    ruim. É o que diz se a razão é estável NESTE ambiente.

    Fase 84 — devolve None com menos de `_MAD_MIN_N` amostras. Antes devolvia
    0.0, e 0.0 lido numa planilha é "perfeitamente estável" — exatamente a
    leitura oposta da verdade, que é "não há como saber". Com 57 dos 90 tracks
    de um dia tendo UMA amostra, esse zero seria a maioria da coluna: o
    experimento concluiria que as razões são estáveis quando ninguém as mediu
    duas vezes. É o mesmo erro do bbox (0,0,0,0) da Fase 82 — ausência de
    medida vestida de medida.
    """
    if len(v) < _MAD_MIN_N:
        return None
    return _mediana([abs(x - med) for x in v]) or 0.0


def acumular_descritor(acc: dict, tid: int, *, frame, pessoa: dict,
                       w: int, h: int, tempo_s: float, no_posto: bool,
                       papel: str | None) -> None:
    """Junta os sinais de UMA amostra no acumulador do track. Chamado dentro do
    laço de detecção, onde o frame ainda existe — depois dele, não há mais
    imagem para tirar cor nenhuma."""
    d = acc.setdefault(tid, {
        "razoes": defaultdict(list), "hist_sup": [], "hist_inf": [],
        "alturas_rel": [], "aspectos": [], "n": 0, "n_posto": 0,
        "papeis": Counter(), "t_ini": tempo_s, "t_fim": tempo_s,
        "melhor_area": -1.0, "bbox_ref": None, "frame_ref": None,
    })
    d["n"] += 1
    d["t_fim"] = max(d["t_fim"], tempo_s)
    d["t_ini"] = min(d["t_ini"], tempo_s)
    if no_posto:
        d["n_posto"] += 1
    if papel:
        d["papeis"][papel] += 1

    bbox = pessoa.get("bbox")
    if _bbox_valido(bbox):
        x1, y1, x2, y2 = (float(v) for v in bbox[:4])
        alt, larg = y2 - y1, x2 - x1
        d["alturas_rel"].append(alt / max(1.0, float(h)))
        d["aspectos"].append(larg / max(1.0, alt))
        area = alt * larg
        if area > d["melhor_area"]:
            # Recorte de referência para o EXPORT: a amostra em que a pessoa
            # aparece maior é a que dá a imagem mais legível para olhar.
            # Normalizado (0-1) para funcionar sobre o frame já redimensionado
            # que está no Storage.
            d["melhor_area"] = area
            d["bbox_ref"] = [round(x1 / max(1, w), 5), round(y1 / max(1, h), 5),
                             round(x2 / max(1, w), 5), round(y2 / max(1, h), 5)]
            d["frame_ref"] = pessoa.get("frame_idx")
        hc = histograma_cor(frame, bbox)
        if hc:
            if hc.get("sup"):
                d["hist_sup"].append(hc["sup"])
            if hc.get("inf"):
                d["hist_inf"].append(hc["inf"])
    for k, v in razoes_corporais(pessoa.get("kpts"), w, h).items():
        d["razoes"][k].append(v)


def _media_hist(hists: list) -> list | None:
    """Média bin a bin, renormalizada. Média (e não mediana) porque histograma
    é distribuição: somar amostras é o agregado natural."""
    if not hists:
        return None
    n_bins = len(hists[0])
    soma = [0.0] * n_bins
    for hh in hists:
        if len(hh) != n_bins:
            continue
        for i, v in enumerate(hh):
            soma[i] += v
    tot = sum(soma)
    if tot <= 0:
        return None
    return [round(v / tot, 5) for v in soma]


def fechar_descritores(acc: dict, intervalo_s: float, cam_id: str | None,
                       w: int, h: int) -> list[dict]:
    """Fecha o acumulador em um descritor por track.

    Mediana para as razões (robusta ao frame em que a pessoa está torta),
    dispersão junto, e o `n` de cada razão — uma razão medida 3 vezes num track
    de 200 amostras não vale o mesmo que uma medida 180 vezes, e quem for
    agrupar precisa saber disso.
    """
    saida: list[dict] = []
    for tid, d in sorted(acc.items()):
        razoes: dict[str, dict] = {}
        for nome, vals in d["razoes"].items():
            med = _mediana(vals)
            if med is None:
                continue
            _m = _mad(vals, med)
            razoes[nome] = {"med": round(med, 4),
                            "mad": (round(_m, 4) if _m is not None else None),
                            "n": len(vals)}
        alt_rel = _mediana(d["alturas_rel"])
        asp = _mediana(d["aspectos"])
        papel = d["papeis"].most_common(1)[0][0] if d["papeis"] else None
        saida.append({
            "pessoa_track_id": int(tid),
            "cam_id": cam_id,
            "n_amostras": d["n"],
            "n_amostras_posto": d["n_posto"],
            # Tempo é nº de amostras × intervalo de amostragem: a amostragem é
            # sistemática, então isto é uma ESTIMATIVA do tempo real na zona,
            # não uma cronometragem.
            "tempo_posto_s": round(d["n_posto"] * float(intervalo_s), 1),
            "tempo_visivel_s": round(d["n"] * float(intervalo_s), 1),
            "papel_predominante": papel,
            "altura_rel": round(alt_rel, 5) if alt_rel is not None else None,
            "aspecto": round(asp, 4) if asp is not None else None,
            "razoes": razoes or None,
            "hist_sup": _media_hist(d["hist_sup"]),
            "hist_inf": _media_hist(d["hist_inf"]),
            "hist_bins": {"espaco": "hsv", "h": _HIST_BINS_H, "s": _HIST_BINS_S,
                          "faixa_central": _HIST_FAIXA,
                          "n_sup": len(d["hist_sup"]), "n_inf": len(d["hist_inf"])},
            "bbox_ref": d["bbox_ref"],
            "frame_ref": d["frame_ref"],
            "frame_w": int(w), "frame_h": int(h),
        })
    return saida


def _crop_cinza_pequeno(frame, bbox, lado: int = 32):
    """Recorte cinza pequeno (lado×lado) da pessoa — assinatura visual barata
    p/ o termo de movimento do gate. None se o bbox for degenerado."""
    x1, y1, x2, y2 = bbox
    h, w = frame.shape[:2]
    x1 = max(0, min(int(x1), w - 1)); x2 = max(x1 + 1, min(int(x2), w))
    y1 = max(0, min(int(y1), h - 1)); y2 = max(y1 + 1, min(int(y2), h))
    sub = frame[y1:y2, x1:x2]
    if sub.size == 0:
        return None
    try:
        cinza = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
        return cv2.resize(cinza, (lado, lado)).astype("float32")
    except Exception:
        return None


def _dist_pose(kpts_a, kpts_b) -> float | None:
    """Distância entre duas poses (keypoints normalizados [0-1], shape (K,2)).
    Re-centra no centroide e re-escala pela dispersão p/ ser invariante a
    posição/tamanho — mede a FORMA do esqueleto. None se faltarem keypoints
    válidos. Retorno ~0 = mesma pose; cresce com a diferença."""
    if kpts_a is None or kpts_b is None:
        return None
    try:
        import numpy as _np
        a = _np.asarray(kpts_a, dtype="float32").reshape(-1, 2)
        b = _np.asarray(kpts_b, dtype="float32").reshape(-1, 2)
        if a.shape != b.shape or a.shape[0] == 0:
            return None
        # keypoints ausentes vêm como (0,0) do YOLO → só compara os presentes nos DOIS
        val = (a[:, 0] > 0) & (a[:, 1] > 0) & (b[:, 0] > 0) & (b[:, 1] > 0)
        if val.sum() < 4:
            return None
        a, b = a[val], b[val]

        def _norm(p):
            c = p.mean(axis=0)
            q = p - c
            esc = _np.sqrt((q ** 2).sum(axis=1)).mean() or 1.0
            return q / esc

        an, bn = _norm(a), _norm(b)
        return float(_np.sqrt(((an - bn) ** 2).sum(axis=1)).mean())
    except Exception:
        return None


def _dist_movimento(crop_a, crop_b) -> float | None:
    """Movimento entre dois crops cinza (absdiff médio normalizado por
    _GATE_MOV_REF → 0..1). None se algum faltar/incompatível."""
    if crop_a is None or crop_b is None:
        return None
    try:
        import numpy as _np
        a = _np.asarray(crop_a, dtype="float32")
        b = _np.asarray(crop_b, dtype="float32")
        if a.shape != b.shape:
            return None
        return float(min(1.0, _np.abs(a - b).mean() / max(0.001, _GATE_MOV_REF)))
    except Exception:
        return None


def _gate_distancia(ancora: dict, pessoa: dict) -> float:
    """Distância 0..~1 entre a amostra atual e a ÂNCORA (última analisada do
    mesmo track). Combina pose (forma do corpo) e movimento (mudança visual);
    troca de ZONA força "diferente". Pesos re-normalizados entre os sinais
    disponíveis (pose e/ou crop podem faltar). Puro e testável."""
    if pessoa.get("zona") != ancora.get("zona"):
        return 1.0   # mudou de posto/zona → sempre reanalisa
    dp = _dist_pose(ancora.get("kpts"), pessoa.get("kpts"))
    dm = _dist_movimento(ancora.get("crop"), pessoa.get("crop"))
    termos, pesos = [], []
    if dp is not None:
        termos.append(min(1.0, dp)); pesos.append(_GATE_PESO_POSE)
    if dm is not None:
        termos.append(dm); pesos.append(_GATE_PESO_MOV)
    if not termos:
        return _GATE_LIMIAR_DIFERENTE   # sem sinal → cai na FRONTEIRA (VLM binário decide)
    soma = sum(p for p in pesos) or 1.0
    return sum(t * p for t, p in zip(termos, pesos)) / soma


def resetar_tracker(yolo) -> str:
    """Fase 64 — zera o estado do BoT-SORT ENTRE VÍDEOS. Devolve o que fez
    (para log/teste). Nunca levanta: falhar aqui não pode matar um vídeo.

    POR QUE ISTO É NECESSÁRIO
    O worker mantém UM `YOLO` vivo para todos os vídeos (`_get_yolo`, para não
    recarregar o modelo a cada job) e chama `.track(persist=True)`. `persist`
    quer dizer "não recrie os trackers" — o que é o correto DENTRO de um vídeo
    e errado ENTRE vídeos. Duas consequências, nesta ordem de gravidade:

    1) VAZAMENTO DE TRACK ENTRE VÍDEOS. Tracks perdidas sobrevivem
       `track_buffer` frames (30 no botsort.yaml). No primeiro frame do vídeo
       seguinte elas ainda estão vivas e podem casar com quem aparecer ali —
       câmera fixa, mesmo posto, pessoa quase na mesma posição: é o cenário
       ideal para o casamento errado. `ids_unicos` (total_pessoas) sai torto.

    2) GMC TRAVADO. O GMC guarda `prevFrame`. Em
       `gmc.py::applySparseOptFlow`, `calcOpticalFlowPyrLK` roda ANTES de
       `self.prevFrame = frame.copy()`. Se os tamanhos divergirem, o OpenCV
       levanta (assertion `prevPyr.size() == nextPyr.size()`), o ultralytics
       captura e cai para identidade — mas `prevFrame` NUNCA é atualizado.
       Resultado: o quadro velho fica preso e TODO frame seguinte falha
       igual, para sempre, no processo inteiro. É por isso que o warning sai
       repetido em vez de uma vez só: não é um tropeço, é um estado travado.

    `BOTSORT.reset()` chama `gmc.reset_params()`, que é exatamente a saída
    desse estado. Em versões sem `reset()`, apagar `predictor.trackers` força
    `on_predict_start` a recriá-los mesmo com `persist=True` (ele só reusa se
    o atributo existir).
    """
    try:
        # A leitura dos atributos fica DENTRO do try: `predictor` pode ser uma
        # property e levantar. Deixá-la de fora dava a um detalhe do ultralytics
        # o poder de derrubar o vídeo inteiro.
        pred = getattr(yolo, "predictor", None)
        trackers = getattr(pred, "trackers", None) or []
        if trackers and all(hasattr(t, "reset") for t in trackers):
            for t in trackers:
                t.reset()
            return "reset"
        if pred is not None and hasattr(pred, "trackers"):
            del pred.trackers          # força a recriação no próximo track()
            return "recriar"
    except Exception as e:  # noqa: BLE001
        log.warning("[tracker] reset falhou (não-fatal): %s", e)
        return "falhou"
    return "nada"                      # 1º vídeo do processo: ainda não existe


def etapa_detectar_e_amostrar(
    yolo: YOLO,
    video_path: str,
    intervalo_s: float,
    rois_contexto: dict,
    progress_cb: ProgressCb,
    cam_id: str | None = None,
) -> tuple[list[Amostra], dict, list[int], list[dict]]:
    # Cada vídeo começa com o tracker limpo. Ver `resetar_tracker` para o
    # porquê — em resumo: `persist=True` é certo dentro do vídeo e errado
    # entre vídeos.
    log.debug("[tracker] estado entre vídeos: %s", resetar_tracker(yolo))
    info = inspecionar_video(video_path)
    fps = info["fps"]
    total_frames = info["total_frames"]
    w = info["largura"]
    h = info["altura"]

    rois = _build_rois(rois_contexto, w, h)
    # Fase 28: com zona de posto_operador configurada, a análise foca no
    # operador titular — transeuntes (fora das zonas) morrem aqui na raiz.
    modo_op = _modo_operador(rois)
    # Nome da zona de posto (Fase 83: o descritor conta o tempo do track DENTRO
    # dela — é o sinal de "quem fica", que separa titular de visitante melhor
    # que qualquer aparência).
    zona_posto_nome = next(
        (n for n, i in rois.items() if i.get("papel") == "posto_operador"), None)
    # Fase 45: em modo operador, corta MENOS na detecção (recall do titular
    # ocluso); as zonas já filtram os transeuntes. Fora do modo, corte normal.
    area_min_px = (_OPERADOR_AREA_MIN_RATIO if modo_op else AREA_MIN_RATIO) * (w * h)
    conf_deteccao = _OPERADOR_CONF if modo_op else YOLO_CONF_MIN
    presenca_zona: dict[int, int] = {}   # track_id → nº de amostras no posto
    # Fase 83: acumulador do descritor por track. Vive só aqui, onde o frame
    # ainda está na mão — depois desta etapa não há mais imagem para tirar cor.
    desc_acc: dict[int, dict] = {}
    if modo_op:
        log.info("[operador] modo operador ATIVO — zonas: "
                 + ", ".join(f"{n}({i.get('papel')})" for n, i in rois.items()))

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
                conf=conf_deteccao,
                tracker=TRACKER_CONFIG,
                imgsz=imgsz,
                verbose=False,
            )
            tempo_s = frame_idx / fps
            if tempo_s >= prox_amostra_s:
                prox_amostra_s += intervalo_s   # consome este slot (~1 amostra / intervalo_s)
                pessoas = []
                if (
                    results[0].boxes is not None
                    and results[0].boxes.id is not None
                ):
                    boxes = results[0].boxes.xyxy.cpu().numpy()
                    ids = results[0].boxes.id.cpu().numpy().astype(int)
                    # Keypoints da pose (Fase 23): o yolo11n-POSE já os calcula —
                    # os lemos aqui p/ o gate de repetição (comparação grátis de
                    # pose entre amostras). `xyn` = normalizado [0-1], alinhado
                    # (mesma ordem) aos boxes ANTES do mask. None se sem pose.
                    kpts_all = None
                    if getattr(results[0], "keypoints", None) is not None and \
                            results[0].keypoints.xyn is not None:
                        try:
                            kpts_all = results[0].keypoints.xyn.cpu().numpy()
                        except Exception:
                            kpts_all = None
                    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
                    mask = areas >= area_min_px
                    idx_validos = [j for j, m in enumerate(mask) if m]
                    for j in idx_validos:
                        box, tid = boxes[j], ids[j]
                        x1, y1, x2, y2 = box.astype(int)
                        cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
                        pessoa = {
                            "track_id": int(tid),
                            "bbox": (int(x1), int(y1), int(x2), int(y2)),
                            "centro": (cx, cy),
                        }
                        if kpts_all is not None and j < len(kpts_all):
                            pessoa["kpts"] = kpts_all[j].astype("float32")
                        if modo_op:
                            # Fase 28/31: classifica por QUALQUER parte do corpo
                            # na zona (kpts: pé, braço, joelho... + âncora dos
                            # ombros p/ pose parcial — robusto à oclusão pelo
                            # torno). Fora das zonas de interesse = transeunte →
                            # descartado ANTES de virar pessoa/evento/métrica.
                            pontos = _pontos_da_pessoa(pessoa, w, h)
                            nome_z, papel_z, desc_z = _zona_da_pessoa(pontos, rois)
                            if papel_z is None:
                                continue
                            pessoa["zona"] = nome_z
                            pessoa["zona_desc"] = desc_z
                            pessoa["_papel_zona"] = papel_z
                            # Fase 44: punho na zona 'maquina' → mãos no torno
                            # (operando), mesmo com o tronco no posto.
                            pessoa["maos_maquina"] = _maos_na_maquina(pessoa, rois, w, h)
                        else:
                            pessoa["zona"] = _zona_contexto(cx, cy, rois)
                        if _GATE_ENABLE:
                            pessoa["crop"] = _crop_cinza_pequeno(frame, pessoa["bbox"])
                        pessoa["frame_idx"] = frame_idx
                        pessoas.append(pessoa)
                    if modo_op and pessoas:
                        # Eleição do OPERADOR: entre quem está no posto, vence o
                        # track com maior presença acumulada (desempate: maior
                        # bbox). Demais no posto e todos em 'interacao' são
                        # visitantes. Operador reordenado p/ 1º → P1 = operador.
                        no_posto = [p for p in pessoas if p["_papel_zona"] == "posto_operador"]
                        for p in no_posto:
                            presenca_zona[p["track_id"]] = presenca_zona.get(p["track_id"], 0) + 1
                        if no_posto:
                            def _rank_op(p):
                                bx1, by1, bx2, by2 = p["bbox"]
                                return (-presenca_zona.get(p["track_id"], 0),
                                        -(bx2 - bx1) * (by2 - by1))
                            no_posto.sort(key=_rank_op)
                            titular = no_posto[0]
                            titular["papel"] = "operador"
                            for p in pessoas:
                                if p is not titular:
                                    p["papel"] = "visitante"
                            pessoas.sort(key=lambda p: 0 if p.get("papel") == "operador" else 1)
                        else:
                            for p in pessoas:
                                p["papel"] = "visitante"
                        for p in pessoas:
                            p.pop("_papel_zona", None)
                    for i, p in enumerate(pessoas):
                        p["rotulo"] = f"P{i + 1}"
                    # Fase 83: descritor por track, DEPOIS da eleição de papel
                    # (para o descritor saber se este track é o titular) e ainda
                    # com o frame vivo.
                    for p in pessoas:
                        acumular_descritor(
                            desc_acc, p["track_id"], frame=frame, pessoa=p,
                            w=w, h=h, tempo_s=tempo_s,
                            no_posto=(p.get("papel") == "operador"
                                      or p.get("zona") == zona_posto_nome),
                            papel=p.get("papel"),
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
                            dim=(w, h),
                        )
                    )
                elif modo_op:
                    # Fase 28: slot sem ninguém de interesse — amostra VAZIA
                    # (sem encode JPEG, custo zero). É o insumo do posto_vazio
                    # e da confirmação pela cam2.
                    amostras.append(
                        Amostra(frame_idx=frame_idx, tempo_s=tempo_s,
                                img_b64="", pessoas=[], dim=(w, h))
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
    descritores = fechar_descritores(desc_acc, intervalo_s, cam_id, w, h)
    progress_cb("deteccao", 100, f"{len(amostras)} amostras · {len(ids_unicos)} pessoas")
    return amostras, info, ids_unicos, descritores


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

    # Fase 28/33: o prompt de OPERADOR (P1 = titular) só vale quando há de
    # fato um operador na amostra; amostra só com VISITANTES usa o prompt
    # neutro (senão o P1-visitante seria descrito como se fosse o operador).
    modo_op = any(p.get("papel") for p in amostra.pessoas)
    tem_operador = any(p.get("papel") == "operador" for p in amostra.pessoas)

    # Fase 44: mãos na máquina vistas por QUALQUER câmera (cam1 pelo pose do
    # track; cam2 pela confirmação lateral) — o sinal vale nas duas, sempre.
    maos_cam2 = getattr(amostra, "maos_cam2", False)
    contexto_partes = []
    for p in amostra.pessoas:
        zona_txt = p.get("zona_desc") or p.get("zona")
        if zona_txt:
            quem = "o OPERADOR" if p.get("papel") == "operador" else p["rotulo"]
            linha = (f"{p['rotulo']} ({quem}) está em: {zona_txt}"
                     if modo_op else f"{p['rotulo']} está em {zona_txt}")
            # Sinal geométrico do pose — punho dentro da zona 'maquina' (posição
            # REAL das mãos), na cam1 ou na cam2 → o VLM deve tratar como operar.
            op_maos = p.get("maos_maquina") or (p.get("papel") == "operador" and maos_cam2)
            if op_maos:
                linha += (" — e está com as MÃOS na máquina (torno), tocando/"
                          "manipulando o equipamento (logo, OPERANDO, não apenas monitorando)")
            contexto_partes.append(linha)
    contexto = ". ".join(contexto_partes) if contexto_partes else "sem zonas pré-definidas"

    if tem_operador:
        template = PROMPT_VLM_DUAL_OPERADOR if img_sec else PROMPT_VLM_OPERADOR
    else:
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


def _gate_vlm_binario(groq_client, amostra: Amostra, desc_ancora: str) -> bool:
    """Desempate BARATO do gate (só na fronteira): mostra o frame e pergunta
    se a pessoa AINDA está fazendo a ação da âncora — resposta sim/não
    (max_tokens minúsculo). True = repetição (mantém o padrão). Em erro,
    devolve False (na dúvida, ANALISA — falso "diferente" só custa 1 chamada,
    falso "igual" perderia um insight)."""
    prompt = (
        "Você compara ações em vídeo industrial. A ação observada até agora é:\n"
        f"\"{desc_ancora}\"\n"
        "Olhando a imagem, a pessoa AINDA está fazendo essencialmente essa mesma ação? "
        "Responda APENAS com uma palavra: SIM ou NAO."
    )
    try:
        r = groq_vision_call(
            groq_client, amostra.img_b64, prompt,
            json_mode=False, max_tokens=3, temperatura=0.0,
            imagens_extra=([amostra.img_b64_secundario] if amostra.img_b64_secundario else None),
        )
        return (r or "").strip().lower().startswith("s")
    except Exception as e:
        log.warning(f"[gate] binário falhou no frame {amostra.frame_idx} ({e}) — analisando.")
        return False


def etapa_analise_vlm(
    groq_client: Groq,
    amostras: list[Amostra],
    descricao_processo: str,
    memoria: dict,
    progress_cb: ProgressCb,
    conhecimento_adquirido: str = "",
    zona_posto: str | None = None,
) -> list[dict]:
    """Analisa as amostras com o VLM. Com KV_GATE_ENABLE=on (Fase 23), um gate
    de repetição evita reanalisar o PADRÃO: por track, mantém uma ÂNCORA (a
    última amostra analisada) e, para cada nova amostra, decide barato —
      • pose+movimento local (grátis) muito parecidos → REPETIÇÃO: herda a
        descrição da âncora, sem chamar o VLM (só contabiliza o tempo);
      • muito diferentes → analisa (VLM completo), vira a nova âncora;
      • na fronteira → 1 VLM BINÁRIO barato (sim/não) desempata.
    TODA amostra continua gerando observação (tempo 100% preservado); as
    suprimidas herdam o rótulo e estendem o mesmo evento downstream.

    Fase 28: `zona_posto` (nome da zona posto_operador) liga a síntese de
    POSTO VAZIO — amostras vazias viram observação determinística sem VLM."""
    progress_cb("vlm", 0, f"Analisando {len(amostras)} amostras com VLM"
                + (" · gate ON" if _GATE_ENABLE else ""))
    observacoes: list[dict] = []
    ancoras: dict[int, dict] = {}   # track_id → {kpts, crop, zona, descricao}
    n_completo = n_binario = n_repeticao = 0
    # Fase 34: última ação CONHECIDA do operador (qualquer track) — herdada
    # nas pontes temporais e quando o VLM devolve "ação não identificada"
    # (operador ocluso é difícil de ler; indefinida só fica sem histórico).
    ultima_desc_op: str | None = None
    ultimo_tid_op: int | None = None
    n_herdadas = 0

    def _eh_indefinida(d: str | None) -> bool:
        return bool(d) and ("não identificada" in d or "nao identificada" in d)

    for i, am in enumerate(amostras):
        # Fase 33: RESGATE pela lateral — a cam2 ESTABELECEU a presença mas a
        # cam1 não tem pessoa 'operador' neste slot (oclusão total): a ação é
        # descrita pela IMAGEM DA CAM2 (track sintético). Visitantes que
        # existam na cam1 seguem no fluxo normal abaixo.
        tem_op_cam1 = any(p.get("papel") == "operador" for p in am.pessoas)
        if zona_posto and am.operador_presente and not tem_op_cam1:
            desc_cam2 = None
            origem_resgate = "resgate_cam2"
            if am.operador_ponte:
                # Fase 34: PONTE — presença por continuidade temporal; herda a
                # última ação conhecida SEM chamada de VLM (custo zero).
                desc_cam2 = ultima_desc_op
                origem_resgate = "ponte_temporal"
            elif am.img_b64_secundario:
                desc_cam2 = _analisar_operador_cam2(
                    groq_client, am.img_b64_secundario, descricao_processo,
                    memoria, conhecimento_adquirido, zona_desc=zona_posto,
                    maos_maquina=getattr(am, "maos_cam2", False),
                )
                if desc_cam2:
                    n_completo += 1
                if _eh_indefinida(desc_cam2) and ultima_desc_op:
                    desc_cam2 = ultima_desc_op   # Fase 34: herda o padrão
                    origem_resgate = "indefinida_herdada"
                    n_herdadas += 1
            if desc_cam2:
                # Fase 82 — A CAIXA VEM DA CÂMERA QUE VIU A PESSOA.
                # Aqui a cam1 não vê o operador (é o caso do resgate); a caixa
                # que existe é a da cam2, e ela vinha sendo substituída por
                # (0,0,0,0). Zero não é "sem medida": é uma medida FALSA de uma
                # pessoa de tamanho nenhum na origem da imagem, e ela contamina
                # tudo que lê bbox (o `deslocamento_rel` do fato do evento
                # inclusive). Na ponte temporal ninguém foi visto em instante
                # nenhum — ali a caixa é NULA de verdade.
                bbox_obs = am.bbox_cam2 if origem_resgate != "ponte_temporal" else None
                observacoes.append(
                    {
                        "tempo_s": am.tempo_s,
                        "frame_idx": am.frame_idx,
                        "track_id": (ultimo_tid_op if origem_resgate == "ponte_temporal"
                                     and ultimo_tid_op is not None else OPERADOR_CAM2_TID),
                        "descricao": desc_cam2,
                        "bbox": bbox_obs,
                        "bbox_cam": "cam2" if bbox_obs else None,
                        "bbox_dim": am.dim_cam2 if bbox_obs else None,
                        "zona": zona_posto,
                        "papel": "operador",
                        "origem_gate": origem_resgate,
                        "mudanca_contexto": origem_resgate == "resgate_cam2",
                    }
                )
                if not _eh_indefinida(desc_cam2):
                    ultima_desc_op = desc_cam2
                    if origem_resgate == "resgate_cam2":
                        ultimo_tid_op = OPERADOR_CAM2_TID
        # Fase 28/33: slot sem ninguém de interesse E sem operador confirmado
        # por NENHUMA das câmeras → POSTO VAZIO sintético (custo zero).
        if not am.pessoas:
            if _POSTO_VAZIO_ENABLE and zona_posto and not am.operador_presente:
                observacoes.append(
                    {
                        "tempo_s": am.tempo_s,
                        "frame_idx": am.frame_idx,
                        "track_id": POSTO_VAZIO_TID,
                        "descricao": POSTO_VAZIO_DESC,
                        # Fase 82: não há pessoa — a caixa é NULA, não zerada.
                        # Zerada, ela entrava nos cálculos como um corpo de
                        # altura 1px na origem.
                        "bbox": None,
                        "bbox_cam": None,
                        "bbox_dim": None,
                        "zona": zona_posto,
                        "papel": "posto_vazio",
                        "origem_gate": "posto_vazio",
                        "mudanca_contexto": False,
                    }
                )
            continue
        # Quais tracks desta amostra podem ser servidos pela âncora (repetição)?
        analisar_agora = not _GATE_ENABLE
        decisao: dict[int, tuple[str, str]] = {}   # track_id → (origem, descricao_ou_"")
        if _GATE_ENABLE:
            precisa_vlm = False
            for p in am.pessoas:
                tid = p["track_id"]
                anc = ancoras.get(tid)
                if anc is None:
                    decisao[tid] = ("analisar", "")      # 1ª vez do track → analisa
                    precisa_vlm = True
                    continue
                d = _gate_distancia(anc, p)
                if d <= _GATE_LIMIAR_IGUAL:
                    decisao[tid] = ("repeticao_pose", anc["descricao"])
                elif d >= _GATE_LIMIAR_DIFERENTE:
                    decisao[tid] = ("analisar", "")
                    precisa_vlm = True
                else:
                    # fronteira: desempata com 1 VLM binário barato
                    n_binario += 1
                    if _gate_vlm_binario(groq_client, am, anc["descricao"]):
                        decisao[tid] = ("repeticao_gate", anc["descricao"])
                    else:
                        decisao[tid] = ("analisar", "")
                        precisa_vlm = True
            analisar_agora = precisa_vlm

        descricoes = (
            _analisar_amostra_vlm(
                groq_client, am, descricao_processo, memoria, conhecimento_adquirido
            )
            if analisar_agora else {}
        )
        if analisar_agora:
            n_completo += 1

        for p in am.pessoas:
            tid = p["track_id"]
            if not _GATE_ENABLE:
                desc = descricoes.get(tid)
                origem_gate = "analisado"
            else:
                origem, desc_ancora = decisao.get(tid, ("analisar", ""))
                if origem == "analisar":
                    desc = descricoes.get(tid)
                    origem_gate = "analisado"
                else:
                    desc = desc_ancora            # herda o padrão (sem token)
                    origem_gate = origem
                    n_repeticao += 1
            if not desc:
                continue
            # Fase 34: operador com "ação não identificada" HERDA a última
            # ação conhecida (ocluso é difícil de ler; indefinida de verdade
            # só quando ainda não há histórico no vídeo).
            if p.get("papel") == "operador":
                if _eh_indefinida(desc) and ultima_desc_op:
                    desc = ultima_desc_op
                    origem_gate = "indefinida_herdada"
                    n_herdadas += 1
                elif not _eh_indefinida(desc):
                    ultima_desc_op = desc
                    ultimo_tid_op = tid
            # Atualiza/instala a âncora quando a amostra foi de fato ANALISADA.
            if _GATE_ENABLE and origem_gate == "analisado":
                ancoras[tid] = {
                    "kpts": p.get("kpts"), "crop": p.get("crop"),
                    "zona": p.get("zona"), "descricao": desc,
                }
            observacoes.append(
                {
                    "tempo_s": am.tempo_s,
                    "frame_idx": am.frame_idx,
                    "track_id": tid,
                    "descricao": desc,
                    "bbox": p["bbox"],
                    "bbox_cam": "cam1",
                    "bbox_dim": am.dim,
                    "zona": p["zona"],
                    "papel": p.get("papel"),
                    # Fase 82: `maos_maquina` era LIDO em montar_fato_evento
                    # (`e.get("maos_maquina")`) e nunca chegava lá — a chave
                    # morria aqui e o sinal do punho na zona da máquina nunca
                    # entrou em fato nenhum. Custo zero: já estava calculado.
                    "maos_maquina": p.get("maos_maquina"),
                    "origem_gate": origem_gate,
                    "mudanca_contexto": origem_gate == "analisado",
                }
            )
        pct = int((i + 1) / max(1, len(amostras)) * 100)
        progress_cb(
            "vlm", pct, f"{i + 1}/{len(amostras)} amostras · {len(observacoes)} observações"
        )

    if n_herdadas:
        log.info("[operador] %d observação(ões) herdaram a última ação conhecida "
                 "(indefinida/ponte — operador ocluso).", n_herdadas)
    if _GATE_ENABLE:
        chamadas = n_completo + n_binario
        base = len(amostras)
        economia = round((1 - chamadas / max(1, base)) * 100, 1)
        log.info(
            "[gate] %d amostras → %d VLM completo + %d binário (%d repetições contadas) "
            "· ~%.0f%% menos chamadas de amostragem",
            base, n_completo, n_binario, n_repeticao, economia,
        )
        etapa_analise_vlm._ultima_economia = {   # type: ignore[attr-defined]
            "amostras": base, "vlm_completo": n_completo, "vlm_binario": n_binario,
            "repeticoes": n_repeticao, "economia_pct": economia,
        }
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
    aprendizado_auto: bool = True,
) -> tuple[dict[str, str], dict[str, str], Callable[[str], str], Callable[[str], str]]:
    """Retorna (mapa_desc_label, catalogo, label_de, origem_de).

    `aprendizado_auto=False` (Fase 62) desliga a GENERALIZAÇÃO: correções
    humanas não remapeiam descrições e nenhuma origem sai como aprendida —
    tudo vira `pendente` e vai para a fila. O vocabulário continua indo no
    prompt (ver nota em `_vocab_estabelecido`): ele propõe NOMES, não grava
    verdade, e é o que mantém os rótulos comparáveis entre si — sem isso o
    dataset da campanha sairia com o mesmo comportamento batizado de três
    jeitos, que é o oposto de limpo.
    """
    progress_cb("cluster", 0, "Agrupando descrições em comportamentos")
    descricoes_unicas = sorted(set(o["descricao"] for o in observacoes_brutas))
    # Fase 67 — CORREÇÃO NÃO É MAIS CHAVEADA POR `descricao_bruta`, EM MODO
    # NENHUM. Nem com limiar (Fase 61), nem com a chave ligada (Fase 62).
    #
    # O motivo é que o pressuposto era falso: "mesma descrição ⇒ mesma ação".
    # Quando o VLM alucina a descrição — descreve alguém operando o torno com
    # o posto vazio — e o humano corrige o RÓTULO daquele evento, o sistema
    # conclui que a frase MAIS FREQUENTE do dataset significa `posto_vazio`.
    # A partir daí toda ocorrência legítima da frase é remapeada.
    #
    # Existem dois erros diferentes e o sistema tratava os dois igual:
    #   (a) descrição certa, rótulo errado  → corrigir o rótulo faz sentido;
    #   (b) descrição ERRADA (alucinação)   → corrigir o rótulo cria um
    #       mapeamento falso e contamina todos os usos corretos da frase.
    # Enquanto a validação não separar (a) de (b), nenhuma correção pode
    # generalizar: vale para o evento corrigido e nada mais.
    _corr_todas = memoria.get("correcoes_aprendidas", {}) or {}
    correcoes: dict[str, str] = {}
    if _corr_todas:
        log.info(
            "cluster: %d correção(ões) na memória, 0 aplicadas — correção não "
            "generaliza por descrição (Fase 67).", len(_corr_todas),
        )

    descricoes_conhecidas: dict[str, str] = {}
    descricoes_novas: list[str] = []
    for d in descricoes_unicas:
        d_lower = d.lower().strip()
        if d_lower == POSTO_VAZIO_DESC:
            # Fase 28: label determinístico — nunca passa pela LLM.
            continue
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

    # Fase 28: posto_vazio é semeado direto (curto-circuito determinístico).
    if any(d.lower().strip() == POSTO_VAZIO_DESC for d in descricoes_unicas):
        mapa_descricao_label[POSTO_VAZIO_DESC] = POSTO_VAZIO_LABEL
        catalogo[POSTO_VAZIO_LABEL] = (
            "Posto de trabalho vazio — operador ausente do posto"
        )

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
            # `_generalizar` diz ao bloco se `descartados` pode entrar. O
            # vocabulário entra sempre: sugere NOMES já usados, não remapeia
            # nada, e é o que impede o mesmo comportamento de ganhar três
            # nomes ao longo da campanha.
            bloco_memoria=construir_bloco_memoria_cluster(
                {**memoria, "_generalizar": aprendizado_auto}),
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

    # Fase 62: com a generalização desligada, nada é "aprendido" — tudo vai
    # para a fila como pendente. Nota sobre o que NÃO é desligado aqui: o
    # vocabulário continua entrando no PROMPT do cluster
    # (`construir_bloco_memoria_cluster`). Ele sugere nomes já usados; não
    # marca validação nem remapeia nada. Tirá-lo faria o mesmo comportamento
    # ser batizado de três formas ao longo dos 30 dias, o que suja o dataset
    # em vez de limpá-lo.
    vocab_estabelecido = (
        {
            v["label"]
            for v in memoria.get("vocabulario", [])
            if v["n_confirmacoes"] >= limiar_auto_validacao
        }
        if aprendizado_auto
        else set()
    )
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
def _bbox_valido(b) -> bool:
    """Fase 82: caixa que MEDE alguma coisa. (0,0,0,0) não mede — era o valor
    que o resgate pela cam2 e o posto vazio gravavam quando não havia caixa, e
    ele passava por qualquer checagem de existência."""
    if not b or len(b) < 4:
        return False
    x1, y1, x2, y2 = (float(v) for v in b[:4])
    return (x2 - x1) > 1 and (y2 - y1) > 1


def _resumo_bbox(caixas: list, dim: tuple | None, cam: str | None) -> dict | None:
    """Estatística das caixas de UM evento — o insumo do experimento de
    separabilidade. Uma caixa de um único frame é uma amostra de tamanho 1;
    a mediana sobre as amostras do evento resiste ao frame em que o operador
    está agachado ou meio ocluso.

    `altura_rel` = altura da caixa ÷ altura do frame: é o que permite comparar
    dois vídeos, duas resoluções e duas câmeras. Altura em pixel crua vai junto
    porque a normalização pode estar errada e é bom poder conferir.
    """
    validas = [b for b in caixas if _bbox_valido(b)]
    if not validas:
        return None
    alturas = sorted(float(b[3]) - float(b[1]) for b in validas)
    larguras = sorted(float(b[2]) - float(b[0]) for b in validas)

    def _med(v):
        n = len(v)
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2

    alt_med = _med(alturas)
    h_frame = float(dim[1]) if dim and len(dim) > 1 and dim[1] else 0.0
    out = {
        "n": len(validas),
        "cam": cam,
        "altura_med": round(alt_med, 1),
        "altura_min": round(alturas[0], 1),
        "altura_max": round(alturas[-1], 1),
        "largura_med": round(_med(larguras), 1),
        # Proporção do corpo: quem está de pé é mais "magro" que quem se abaixa.
        "aspecto_med": round(_med(larguras) / max(1.0, alt_med), 3),
    }
    if h_frame > 0:
        out["altura_rel"] = round(alt_med / h_frame, 4)
        out["frame_h"] = int(h_frame)
    return out


def _bbox_jsonb(b) -> dict | None:
    """Caixa → jsonb, ou NULL. Só grava o que mede alguma coisa."""
    if not _bbox_valido(b):
        return None
    return {"x1": int(b[0]), "y1": int(b[1]), "x2": int(b[2]), "y2": int(b[3])}


def _abrir_evento(tid: int, o: dict) -> dict:
    """Abre um evento a partir da 1ª observação dele. `bbox_inicio` fica NULO
    quando a observação não tem caixa (posto vazio, ponte temporal) — antes
    virava (0,0,0,0), que é uma medida falsa, não uma ausência."""
    tem = _bbox_valido(o.get("bbox"))
    return {
        "pessoa_track_id": tid,
        "comportamento_label": o["label"],
        "descricao_bruta": o["descricao"],
        "tempo_inicio_s": o["tempo_s"],
        "tempo_fim_s": o["tempo_s"],
        "frame_inicio": o["frame_idx"],
        "frame_fim": o["frame_idx"],
        "bbox_inicio": list(o["bbox"]) if tem else None,
        "bbox_cam": o.get("bbox_cam") if tem else None,
        "_dim": o.get("bbox_dim") if tem else None,
        "_caixas": [list(o["bbox"])] if tem else [],
        "maos_maquina": o.get("maos_maquina"),
        "zona_contexto": o["zona"],
        "papel_pessoa": o.get("papel"),
        "n_amostras": 1,
    }


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
        atual = _abrir_evento(tid, obs_lista[0])
        for o in obs_lista[1:]:
            gap = o["tempo_s"] - atual["tempo_fim_s"]
            # Fase 28: mudança de PAPEL (operador↔visitante) quebra o evento —
            # o mesmo track em papéis diferentes conta separado nas métricas.
            if (
                o["label"] == atual["comportamento_label"]
                and o.get("papel") == atual.get("papel_pessoa")
                and gap <= janela_continuidade_s
            ):
                atual["tempo_fim_s"] = o["tempo_s"]
                atual["frame_fim"] = o["frame_idx"]
                atual["n_amostras"] += 1
                # Fase 82: a caixa de CADA amostra do evento, não só a primeira.
                if _bbox_valido(o.get("bbox")):
                    atual["_caixas"].append(list(o["bbox"]))
                    if atual["bbox_inicio"] is None:
                        # A 1ª amostra pode não ter tido caixa (oclusão no
                        # instante inicial); a 1ª caixa REAL do evento vale mais
                        # que um None herdado do primeiro frame.
                        atual["bbox_inicio"] = list(o["bbox"])
                        atual["bbox_cam"] = o.get("bbox_cam")
                        atual["_dim"] = o.get("bbox_dim")
                if o.get("maos_maquina") is not None:
                    atual["maos_maquina"] = bool(
                        atual.get("maos_maquina")) or bool(o["maos_maquina"])
            else:
                eventos.append(atual)
                atual = _abrir_evento(tid, o)
        eventos.append(atual)

    for e in eventos:
        e["tempo_fim_s"] = round(e["tempo_fim_s"] + intervalo_s, 2)
        e["tempo_inicio_s"] = round(e["tempo_inicio_s"], 2)
        # Fase 59: a fórmula antiga (0.6 + 0.05*n) era o que produzia o degrau
        # de 0.65 — exatamente o valor de UMA amostra. Ela misturava dois eixos
        # INDEPENDENTES: quanto as amostras concordam (concordância) e quantas
        # amostras existem (evidência). Um evento cru é, por construção, uma
        # sequência do MESMO rótulo: a concordância é total. O que pode faltar
        # é EVIDÊNCIA — e isso `n_amostras` já diz, sem precisar fingir de %.
        e["confianca"] = 1.0 if e["n_amostras"] >= MIN_AMOSTRAS_EVIDENCIA else None
        # Fase 82: fecha o resumo do corpo com as caixas acumuladas do evento.
        e["bbox_stats"] = _resumo_bbox(e.pop("_caixas", []), e.pop("_dim", None),
                                       e.get("bbox_cam"))

    return eventos


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 4b · Consolidação em 1 evento PRINCIPAL por minuto (Fase 16)
# ═════════════════════════════════════════════════════════════════════════
def _merge_bbox_stats(eventos: list[dict]) -> dict | None:
    """Junta os resumos de caixa de vários eventos crus num só (o do minuto).

    Ponderação por `n`: um cru com 8 amostras vale 8× um com 1 na média das
    alturas. Sem isso, um trecho curto de oclusão puxaria a mediana do minuto
    tanto quanto o trecho longo em que a pessoa aparece inteira.
    """
    partes = [e["bbox_stats"] for e in eventos if e.get("bbox_stats")]
    if not partes:
        return None
    n_tot = sum(p["n"] for p in partes) or 1
    cams = {p.get("cam") for p in partes if p.get("cam")}
    frames_h = {p.get("frame_h") for p in partes if p.get("frame_h")}

    def _pond(chave):
        vals = [(p[chave], p["n"]) for p in partes if p.get(chave) is not None]
        return round(sum(v * n for v, n in vals) / max(1, sum(n for _, n in vals)), 3) \
            if vals else None

    out = {
        "n": n_tot,
        # Caixa medida por mais de uma câmera no mesmo minuto não é comparável
        # em pixel; o consumidor precisa saber disso em vez de descobrir depois.
        "cam": (cams.pop() if len(cams) == 1 else "misto") if cams else None,
        "altura_med": _pond("altura_med"),
        "altura_min": min(p["altura_min"] for p in partes),
        "altura_max": max(p["altura_max"] for p in partes),
        "largura_med": _pond("largura_med"),
        "aspecto_med": _pond("aspecto_med"),
    }
    rel = _pond("altura_rel")
    if rel is not None:
        out["altura_rel"] = rel
        if len(frames_h) == 1:
            out["frame_h"] = frames_h.pop()
    return out


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
    camadas: list | None = None,
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

    # O processo rastreia PAPEL? Só se alguma zona de posto estiver desenhada —
    # sem ela `papel_pessoa` nunca é preenchido. Decidido UMA vez, sobre o vídeo
    # inteiro: um minuto sem ninguém não pode fazer o sinal desaparecer e mudar
    # o comportamento das camadas no meio do vídeo.
    _rastreia_papel = any(e.get("papel_pessoa") for e in eventos_crus)

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
        _n_votos = sum(e["n_amostras"] for e, _ in no_bucket)
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
            # Fase 82 — o principal representa o MINUTO, então o corpo dele
            # também: a caixa vem do representante (pode ser nula), mas a
            # estatística junta as caixas de todos os crus do MESMO track no
            # minuto. Misturar tracks aqui seria misturar pessoas — é
            # exatamente o que a identificação do titular vai querer separar.
            "bbox_inicio": (list(rep["bbox_inicio"]) if rep.get("bbox_inicio")
                            else None),
            "bbox_cam": rep.get("bbox_cam"),
            "bbox_stats": _merge_bbox_stats(
                [e for e, _ in no_bucket
                 if e.get("pessoa_track_id") == rep.get("pessoa_track_id")]),
            "maos_maquina": (True if any(e.get("maos_maquina")
                                         for e, _ in no_bucket) else None),
            "zona_contexto": rep["zona_contexto"],
            "papel_pessoa": rep.get("papel_pessoa"),
            "n_amostras": _n_votos,
            # Fase 56 (B1) — CONFIANÇA = CONCORDÂNCIA entre as amostras do minuto.
            #
            # A fórmula antiga era `min(0.95, 0.6 + 0.05*n_amostras)`: contagem
            # de amostras vestida de certeza. Por construção nada ficava abaixo
            # de 0.6 e o mínimo observado era 0.65 em TODOS os rótulos — um
            # número que não media nada.
            #
            # `share` é a fração do minuto ocupada pelo rótulo vencedor e JÁ era
            # calculada aqui, só que descartada. É a medida de incerteza mais
            # honesta disponível e não custa nenhuma chamada a mais:
            #   4 amostras concordantes → share 1.00
            #   2 contra 2             → share 0.50 (moeda ao ar)
            "confianca": (round(share, 2)
                          if _n_votos >= MIN_AMOSTRAS_EVIDENCIA else None),
            # Guardados à parte porque explicam a confiança na fila de dúvida —
            # e porque n_amostras é informação útil, só não é confiança.
            "concordancia": (round(share, 2)
                             if _n_votos >= MIN_AMOSTRAS_EVIDENCIA else None),
            "n_rotulos_no_minuto": len(dur_por_label),
            "rotulos_competindo": sorted(dur_por_label, key=dur_por_label.get, reverse=True)[:4],
            "decidido_por_ia": escolhido != top_label,
            "principal": True,
        })
        # Fase 57: CAMADAS DE DÚVIDA — determinísticas, em CPU, ZERO chamada
        # extra ao VLM. Camada nunca corrige o rótulo: só marca dúvida.
        if camadas:
            fato = montar_fato_evento(principais[-1], no_bucket, share,
                                      len(dur_por_label), rastreia_papel=_rastreia_papel)
            em_duvida, disparos = avaliar_camadas(fato, escolhido, camadas)
            if disparos:
                principais[-1]["camadas_disparadas"] = disparos
                principais[-1]["em_duvida"] = em_duvida
                # Só as ATIVAS explicam a dúvida ao validador; as em sombra
                # entram no placar sem aparecer na fila.
                motivos = [d["motivo"] for d in disparos
                           if d["modo"] == "ativa" and d.get("motivo")]
                if motivos:
                    principais[-1]["duvida_motivo"] = " · ".join(motivos)
            principais[-1]["_fato"] = fato
    return principais


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 5 · Persistência
# ═════════════════════════════════════════════════════════════════════════
# ═════════════════════════════════════════════════════════════════════════
# Fase 57 — CAMADAS DE DÚVIDA: regras declarativas, sombra e placar
#
# Camada NUNCA corrige o rótulo — só marca DÚVIDA. A máquina não sabe qual
# lado está certo; quem sabe é o humano.
#
# Tudo aqui é determinístico e roda em CPU: ZERO chamada extra ao VLM.
# ═════════════════════════════════════════════════════════════════════════
# Deslocamento é normalizado pela ALTURA DA BBOX (alturas-de-corpo por segundo),
# não em pixels. Pixel depende de resolução e distância da câmera — o mesmo
# operador andando geraria números diferentes em cada câmera, e a décima regra
# viraria adivinhação. Em alturas-de-corpo o número é comparável entre câmeras
# e praticamente não precisa de calibração.
MOV_LIMIAR_PADRAO = float(os.environ.get("KV_MOV_LIMIAR", "0.15"))

_OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "em": lambda a, b: a in (b or []),
    "contem": lambda a, b: b in (a or []),
}


def _avaliar_condicao(cond, fato: dict) -> bool:
    '''Condição declarativa → bool. Objeto simples = E entre as chaves;
    combinadores "e" / "ou" / "nao" aninháveis.

    SINAL AUSENTE NUNCA DISPARA: se o processo não tem zona de máquina, a regra
    que fala de mão-na-máquina fica quieta. Falta de dado não é dúvida.'''
    if not isinstance(cond, dict):
        return False
    for chave, valor in cond.items():
        if chave == "e":
            if not all(_avaliar_condicao(c, fato) for c in (valor or [])):
                return False
            continue
        if chave == "ou":
            if not any(_avaliar_condicao(c, fato) for c in (valor or [])):
                return False
            continue
        if chave == "nao":
            if _avaliar_condicao(valor, fato):
                return False
            continue
        if chave not in fato or fato[chave] is None:
            return False                      # sinal indisponível → não dispara
        atual = fato[chave]
        if not isinstance(valor, dict):       # açúcar: {"maos_na_maquina": false}
            valor = {"==": valor}
        for op, esperado in valor.items():
            fn = _OPS.get(op)
            if fn is None:
                log.warning("[camadas] operador desconhecido %r — regra ignorada", op)
                return False
            try:
                if not fn(atual, esperado):
                    return False
            except TypeError:
                return False                  # tipos incompatíveis → não dispara
    return True


def _rotulo_casa(quando, label: str) -> bool:
    """`quando_rotulo` é sempre LISTA. ['*'] vale para todos os rótulos."""
    lista = quando if isinstance(quando, list) else [quando]
    return "*" in lista or label in lista


def avaliar_camadas(fato: dict, label: str, camadas: list) -> tuple:
    '''(em_duvida, disparos) — FUNÇÃO PURA, testável sem banco.

    `em_duvida` só considera camadas ATIVAS. As em SOMBRA entram em `disparos`
    (para o placar contar) mas não marcam o evento: é assim que o dono do
    processo mede o impacto de uma regra nova antes de ligá-la.'''
    disparos, em_duvida = [], False
    for c in sorted(camadas or [], key=lambda x: x.get("ordem", 100)):
        modo = (c.get("modo") or "sombra").lower()
        if modo == "off":
            continue
        if not _rotulo_casa(c.get("quando_rotulo") or ["*"], label):
            continue
        try:
            if not _avaliar_condicao(c.get("se") or {}, fato):
                continue
        except Exception as e:
            log.warning("[camadas] %s falhou ao avaliar (ignorada): %s", c.get("nome"), e)
            continue
        disparos.append({"nome": c.get("nome"), "modo": modo,
                         "motivo": c.get("motivo") or ""})
        if modo == "ativa":
            em_duvida = True
    return em_duvida, disparos


def montar_fato_evento(rep: dict, no_bucket: list, share: float,
                       n_rotulos: int, mov_limiar: float = None,
                       rastreia_papel: bool = True) -> dict:
    '''Sinais da cena para UM evento principal, montados do que a detecção já
    produziu. Nada aqui custa inferência nova.'''
    limiar = MOV_LIMIAR_PADRAO if mov_limiar is None else mov_limiar
    eventos = [e for e, _ in no_bucket]
    pessoas = {e.get("pessoa_track_id") for e in eventos if e.get("pessoa_track_id") is not None}
    zonas = {e.get("zona_contexto") for e in eventos if e.get("zona_contexto")}
    no_posto = {e.get("pessoa_track_id") for e in eventos
                if e.get("papel_pessoa") == "operador"}

    # Deslocamento em ALTURAS-DE-CORPO por segundo (invariante de escala).
    desloc_rel = None
    try:
        centros = []
        for e in eventos:
            b = e.get("bbox_inicio")
            # Fase 82: `not b` NÃO pegava (0,0,0,0) — lista de quatro zeros é
            # verdadeira. Um posto_vazio ou um resgate pela cam2 no mesmo minuto
            # injetava um ponto fantasma na origem com altura 1px, e a distância
            # até a pessoa real virava um deslocamento enorme: o `movimento`
            # dizia "andando" em minuto de gente parada.
            if not _bbox_valido(b):
                continue
            x1, y1, x2, y2 = (float(v) for v in b[:4])
            alt = max(1.0, y2 - y1)
            centros.append((((x1 + x2) / 2), ((y1 + y2) / 2), alt, e.get("tempo_inicio_s") or 0))
        if len(centros) >= 2:
            centros.sort(key=lambda c: c[3])
            (ax, ay, aalt, at), (bx, by, balt, bt) = centros[0], centros[-1]
            dt = max(0.5, float(bt) - float(at))
            dist = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
            desloc_rel = round(dist / max(1.0, (aalt + balt) / 2) / dt, 3)
    except Exception:
        desloc_rel = None

    fato = {
        "pessoas_na_cena": len(pessoas),
        "pessoas_no_posto": len(no_posto),
        "zonas_ocupadas": sorted(z for z in zonas),
        "concordancia": round(float(share), 2),
        "n_rotulos_no_minuto": int(n_rotulos),
        "duracao_s": round(float(rep.get("tempo_fim_s") or 0)
                           - float(rep.get("tempo_inicio_s") or 0), 1),
    }
    # Fase 68: `papel_pessoa` como sinal de primeira classe. Já existia embutido
    # em `pessoas_no_posto`, mas escrever uma regra contando gente para dizer "o
    # operador está lá" é indireto — e regra indireta é regra que ninguém revisa.
    # `operador_presente` vem do RASTREAMENTO + ZONAS: determinístico, sem VLM.
    #
    # ⚠️ SÓ EXISTE SE O PROCESSO RASTREIA PAPEL. Sem zona de posto desenhada,
    # `papel_pessoa` é sempre nulo e `operador_presente` seria sempre False —
    # uma regra do tipo {"operador_presente": false} dispararia em TODOS os
    # eventos do processo. Ausência de zona não é ausência de operador; é
    # ausência de informação, e o contrato das camadas é que falta de dado
    # nunca vira suspeita. Por isso a chave é OMITIDA, não posta como False.
    if rastreia_papel:
        fato["operador_presente"] = bool(no_posto)
        fato["papeis_na_cena"] = sorted(
            {p for p in (e.get("papel_pessoa") for e in eventos) if p})
    if desloc_rel is not None:
        fato["deslocamento_rel"] = desloc_rel
        # Abstração de chão de fábrica: quem escreve a regra pensa em "parado"
        # ou "andando", não em pixels.
        fato["movimento"] = "andando" if desloc_rel >= limiar else "parado"
    # `maos_na_maquina` só existe quando há zona 'maquina' desenhada (Fase 44).
    maos = [e.get("maos_maquina") for e in eventos if e.get("maos_maquina") is not None]
    if maos:
        fato["maos_na_maquina"] = any(maos)
    return fato


def carregar_camadas_duvida(sb: Client, empresa: str, processo: str) -> list:
    """Camadas do processo (ativas + sombra). Falha → lista vazia: sem camada,
    o pipeline segue exatamente como antes."""
    try:
        return (
            sb.table("camadas_duvida")
            .select("nome, quando_rotulo, se, entao, motivo, modo, ordem")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .neq("modo", "off")
            .execute().data
        ) or []
    except Exception as e:
        log.warning("[camadas] não carregadas (%s) — seguindo sem camadas.", e)
        return []


def placar_camadas(sb: Client, empresa: str, processo: str) -> dict:
    '''Fase 57 — PLACAR POR CAMADA: quantas vezes disparou, quantos minutos
    colocou em dúvida e a TAXA DE ACERTO.

    Acerto = o humano mudou o rótulo (corrigiu) ou descartou o evento.
    Falso alarme = o humano CONFIRMOU o rótulo original — a camada gerou
    trabalho sem gerar informação.

    Camada que dispara muito e sempre confirma precisa ser desligada COM
    EVIDÊNCIA. Sem este placar, na vigésima camada a fila enche de alarme falso
    e o mecanismo morre por descrédito.'''
    def _fab():
        return (
            sb.table("eventos")
            .select("id, camadas_disparadas, tempo_inicio_s, tempo_fim_s, "
                    "validado_humano, validacao_correto, label_corrigido")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .not_.is_("camadas_disparadas", "null")
            .order("id")
        )
    try:
        eventos = _scan_todos(_fab)
    except Exception as e:
        return {"erro": f"leitura falhou: {e}", "camadas": []}

    agg: dict = {}
    for e in eventos:
        dur = max(0.0, float(e.get("tempo_fim_s") or 0) - float(e.get("tempo_inicio_s") or 0))
        validado = bool(e.get("validado_humano"))
        # confirmou o rótulo original = falso alarme da camada
        confirmou = validado and e.get("validacao_correto") is True and not e.get("label_corrigido")
        acertou = validado and not confirmou
        for d in (e.get("camadas_disparadas") or []):
            nome = d.get("nome")
            if not nome:
                continue
            a = agg.setdefault(nome, {"nome": nome, "modo": d.get("modo"), "disparos": 0,
                                      "segundos": 0.0, "validados": 0, "acertos": 0,
                                      "falsos_alarmes": 0})
            a["disparos"] += 1
            a["segundos"] += dur
            a["modo"] = d.get("modo") or a["modo"]
            if validado:
                a["validados"] += 1
                a["acertos"] += 1 if acertou else 0
                a["falsos_alarmes"] += 0 if acertou else 1

    saida = []
    for a in agg.values():
        saida.append({
            "nome": a["nome"], "modo": a["modo"],
            "disparos": a["disparos"],
            "minutos_em_duvida": round(a["segundos"] / 60, 1),
            "validados": a["validados"],
            "acertos": a["acertos"],
            "falsos_alarmes": a["falsos_alarmes"],
            # None enquanto ninguém validou — não inventamos taxa sem amostra.
            "taxa_acerto": (round(a["acertos"] / a["validados"] * 100, 1)
                            if a["validados"] else None),
        })
    saida.sort(key=lambda x: -x["minutos_em_duvida"])
    return {"camadas": saida, "total_disparos": sum(c["disparos"] for c in saida)}


# ═════════════════════════════════════════════════════════════════════════
# Fase 58 (B4/B5) — FILA DA DÚVIDA + KPI DO PRODUTO
#
# Limiar medido nos dados (198 eventos), não chutado: abaixo de 0.65 estão os
# empates reais (3 rótulos disputando, moeda ao ar, 3-contra-2).
#
# ⚠️ O corte é aplicado na LEITURA, nunca gravado no evento. Mudar o limiar
# passa a valer na hora, inclusive para os eventos já processados — se ficasse
# congelado numa coluna, ajustar exigiria reprocessar 30 dias de campanha.
# ═════════════════════════════════════════════════════════════════════════
DUVIDA_LIMIAR_PADRAO = float(os.environ.get("KV_DUVIDA_LIMIAR", "0.65"))
# Abaixo disto não há com quem concordar: a concordância é INDEFINIDA, não alta.
# Com 1 amostra, o share do vencedor é 1.0 por definição — leitura falsamente
# confiante. Isso é AUSÊNCIA DE EVIDÊNCIA, um problema diferente de dúvida:
# um se resolve com mais amostragem, o outro com melhor decisão.
MIN_AMOSTRAS_EVIDENCIA = int(os.environ.get("KV_MIN_AMOSTRAS", "2"))


def limiar_duvida(sb: Client, empresa: str, processo: str) -> float:
    """Limiar do processo (coluna `duvida_limiar` em contexto_processo) com
    fallback no env. Configurável por processo porque cada operação tem seu
    próprio nível de ambiguidade."""
    try:
        r = (
            sb.table("contexto_processo").select("duvida_limiar")
            .eq("empresa", empresa).eq("processo", processo).limit(1).execute().data
        ) or []
        if r and r[0].get("duvida_limiar") is not None:
            return float(r[0]["duvida_limiar"])
    except Exception:
        pass
    return DUVIDA_LIMIAR_PADRAO


# Origens em que `validado_humano=True` NÃO significa "alguém julgou": é o
# mecanismo que mantém o registro fora da fila. Nunca foram dúvida e não podem
# entrar na curva histórica como dúvida resolvida.
_ORIGENS_MECANICAS = frozenset({"posto_vazio", "auditoria"})


def evento_em_duvida(e: dict, limiar: float,
                     labels_assumidos: set | None = None,
                     incluir_resolvidas: bool = False) -> tuple:
    '''(em_duvida, motivo, tipo). Origens independentes:

    `incluir_resolvidas=True` ignora o julgamento humano e responde à pergunta
    HISTÓRICA: "este trecho estava em dúvida quando foi lido?". É o que a curva
    do veredito precisa — ver `montar_analise_diaria`. A FILA usa o default
    (False), porque lá a pergunta é outra: "o que ainda falta julgar?".
      • CAMADA ativa disparou (Fase 57) — a cena contradiz o rótulo;
      • CONCORDÂNCIA abaixo do limiar (Fase 56/B1) — as amostras do minuto não
        se entenderam. É o sistema dizendo "não sei" de forma DERIVADA, sem
        precisar que o VLM declare abstenção.
    Evento já julgado por humano sai da fila: a dúvida foi resolvida.'''
    # Determinístico/auditoria nunca foi dúvida — nem hoje, nem no histórico.
    if (e.get("origem_validacao") or "") in _ORIGENS_MECANICAS:
        return False, "", ""
    if e.get("validado_humano") and not incluir_resolvidas:
        return False, "", ""
    # AUSÊNCIA DE EVIDÊNCIA vem primeiro e é EXCLUSIVA: com menos de duas
    # amostras não existe concordância a medir — falar em "amostras
    # discordantes" aqui seria mentira. São problemas diferentes: este se
    # resolve com mais evidência (amostrar mais denso), o outro com melhor
    # decisão (rótulo, prompt, camada).
    n_am = e.get("n_amostras")
    if n_am is not None and int(n_am) < MIN_AMOSTRAS_EVIDENCIA:
        return (True,
                f"apenas {int(n_am)} amostra neste trecho — não há evidência "
                "suficiente para afirmar nem para duvidar",
                "sem_evidencia")
    motivos = []
    # A garantia da SOMBRA vale também na leitura: mesmo que `em_duvida` venha
    # marcado, só conta se houver camada ATIVA entre as disparadas. Defesa em
    # profundidade — o compromisso é "sombra nunca contamina", e ele não pode
    # depender só de quem escreveu.
    tipo = ""
    disparos = e.get("camadas_disparadas")
    tem_ativa = (any((d or {}).get("modo") == "ativa" for d in disparos)
                 if disparos else bool(e.get("em_duvida")))
    if e.get("em_duvida") and tem_ativa:
        motivos.append(e.get("duvida_motivo")
                       or "uma verificação da cena contradiz o rótulo")
        tipo = "camada"
    conf = e.get("confianca")
    if conf is not None and float(conf) < limiar:
        n = e.get("n_rotulos_no_minuto")
        motivos.append(
            f"as amostras do minuto discordaram (concordância {float(conf):.0%}"
            + (f", {n} rótulos disputando)" if n and n > 1 else ")")
        )
        tipo = tipo or "discordancia"
    # Fase 63 — CATEGORIA ASSUMIDA. O rótulo pode estar certíssimo e ainda
    # assim ninguém ter decidido se aquilo agrega valor. Como o "não
    # classificado" deixou de existir, esse tempo já está contando como
    # NÃO-PRODUTIVO no placar — e é justamente por isso que precisa aparecer
    # aqui: se estiver errado, a produtividade está subestimada agora.
    # Vem por último de propósito: se o minuto já é duvidoso por outra razão,
    # aquela razão é mais informativa para quem valida.
    if labels_assumidos:
        label = e.get("label_corrigido") or e.get("comportamento_label") or ""
        if label in labels_assumidos:
            motivos.append(
                "ninguém decidiu se este comportamento agrega valor — está "
                "contando como NÃO-produtivo por convenção, não por evidência"
            )
            tipo = tipo or "categoria_assumida"
    return (bool(motivos), " · ".join(motivos), tipo)


def offset_video_segmento(video_meta: dict, seg: dict) -> float:
    """Fase 30: offset (s) entre o início do vídeo (cam1) e o do segmento par
    (cam2) — os dois NÃO começam no mesmo segundo. O front soma este offset em
    ini/fim ao pedir frames do 2º ângulo (/segmentos/{id}/frames).

    Usa a MESMA fonte nos dois lados (gravado_em de ambos, senão o token
    seg_YYYYMMDD_HHMMSS do nome de ambos) para nunca misturar tz-aware com
    naive. Sem dado confiável → 0.0 (comportamento anterior)."""
    from datetime import datetime as _dt
    import re as _re

    def _iso(v):
        try:
            return _dt.fromisoformat(str(v).replace("Z", "+00:00")) if v else None
        except Exception:
            return None

    def _token(nome):
        m = _re.search(r"seg_(\d{8})_(\d{6})", nome or "")
        if not m:
            return None
        d, h = m.group(1), m.group(2)
        try:
            return _dt(int(d[0:4]), int(d[4:6]), int(d[6:8]),
                       int(h[0:2]), int(h[2:4]), int(h[4:6]))
        except Exception:
            return None

    ga, gb = _iso(video_meta.get("gravado_em")), _iso(seg.get("gravado_em"))
    if ga is not None and gb is not None:
        return round((ga - gb).total_seconds(), 1)
    na, nb = _token(video_meta.get("nome")), _token(seg.get("nome"))
    if na is not None and nb is not None:
        return round((na - nb).total_seconds(), 1)
    return 0.0


def labels_com_categoria_assumida(sb: Client, empresa: str, processo: str) -> set:
    """Rótulos cuja categoria Lean foi ASSUMIDA (origem 'fallback') ou ainda
    está nula. É o conjunto que alimenta a dúvida de categoria.

    Consulta por RÓTULO, não por evento: a categoria vive em `comportamentos`
    e é de lá que o dashboard a lê. Perguntar ao banco por evento traria
    milhares de linhas para responder uma pergunta que tem dezenas."""
    try:
        r = varrer(sb, "comportamentos", "label, categoria_lean, categoria_lean_origem",
                   empresa=empresa, processo=processo)
    except Exception as e:
        log.warning("[duvidas] catálogo não lido (%s) — sem dúvida de categoria.", e)
        return set()
    return {
        c["label"] for c in r
        if c.get("label")
        and not categoria_tem_evidencia(c.get("categoria_lean"),
                                        c.get("categoria_lean_origem"))
    }


def montar_fila_duvidas(sb: Client, empresa: str, processo: str,
                        rotulo: str | None = None, limite: int = 200,
                        tipo_filtro: str | None = None) -> dict:
    '''B4 — fila ORDENADA POR MINUTOS EM JOGO, não por ordem de chegada:
    valida-se primeiro o que mais move o placar.

    `por_rotulo` existe para auditar a suspeita de "rótulo depósito da dúvida"
    — um rótulo que aparece em todas as faixas de baixa confiança costuma ser
    onde o modelo joga o que não sabe. `rotulo` filtra a fila para auditá-lo.'''
    lim = limiar_duvida(sb, empresa, processo)

    # Colunas pedidas ao banco. `cam_id` NÃO entra: ele vive em `videos`, não em
    # `eventos` — foi exatamente esse engano que derrubou a tela em 500.
    COLS = ["id", "video_id", "comportamento_label", "label_corrigido",
            "descricao_bruta", "tempo_inicio_s", "tempo_fim_s", "confianca",
            "n_amostras", "validado_humano", "pessoa_track_id", "papel_pessoa",
            "principal", "em_duvida", "duvida_motivo", "camadas_disparadas",
            "n_rotulos_no_minuto", "rotulos_competindo"]

    _RE_COL = __import__("re").compile(r"column\s+\S*?eventos\.(\w+)\s+does not exist")

    def _fab_com(cols):
        def _f():
            return (
                sb.table("eventos").select(", ".join(cols))
                .eq("empresa", empresa).eq("processo", processo)
                .eq("validado_humano", False)
                .order("id")
            )
        return _f

    # Leitura AUTO-CURATIVA: se o banco recusar uma coluna, ela é removida e a
    # consulta refeita. Uma tela inteira em 500 porque o schema está uma
    # migração atrás é falha de projeto — a fila perde detalhe, nunca a função.
    cols, eventos, ultimo_erro = list(COLS), None, None
    for _ in range(len(COLS)):
        try:
            eventos = _scan_todos(_fab_com(cols))
            break
        except Exception as e:
            ultimo_erro = e
            faltando = _RE_COL.search(str(e))
            if not faltando or faltando.group(1) not in cols:
                break
            alvo = faltando.group(1)
            cols = [c for c in cols if c != alvo]
            log.warning("[duvidas] coluna `%s` não existe neste banco — seguindo "
                        "sem ela (rode a migração para ter o detalhe).", alvo)
    if eventos is None:
        log.error("[duvidas] leitura falhou: %s", ultimo_erro)
        return {"erro": f"leitura falhou: {ultimo_erro}", "itens": [], "por_rotulo": [],
                "por_tipo": [], "limiar": lim, "total": 0, "minutos_totais": 0.0,
                "filtrado_por": rotulo}

    # cam_id vem de `videos` (uma consulta só, com os ids que já temos).
    # Fase 63: a mesma leitura traz `gravado_em`/`nome`, que são o que permite
    # calcular o offset real de relógio até a cam2 — sem ele os dois ângulos
    # mostrariam instantes diferentes, que é pior que não mostrar o segundo.
    cam_por_video: dict = {}
    meta_por_video: dict = {}
    segundo_por_video: dict = {}
    vids = sorted({e.get("video_id") for e in eventos if e.get("video_id")})
    try:
        if vids:
            for v in (sb.table("videos").select("id, cam_id, nome, gravado_em")
                      .in_("id", vids).execute().data or []):
                cam_por_video[v["id"]] = v.get("cam_id")
                meta_por_video[v["id"]] = v
    except Exception as e:
        log.warning("[duvidas] cam_id não resolvido (%s) — segue sem a câmera.", e)

    # 2º ÂNGULO: segmento concluído do MESMO vídeo com OUTRA câmera. Mesmo
    # lookup da tela de eventos — a fila é onde a segunda vista mais importa,
    # porque é exatamente onde a primeira não bastou para decidir.
    try:
        if vids:
            rs = (
                sb.table("segmentos")
                .select("id, video_id, cam_id, gravado_em, nome")
                .eq("empresa", empresa).in_("video_id", vids)
                .eq("status", "concluido").execute().data
            ) or []
            for s in rs:
                vid = s.get("video_id")
                if not vid or not s.get("cam_id"):
                    continue
                if s.get("cam_id") == cam_por_video.get(vid):
                    continue               # é a própria câmera do evento
                if vid not in segundo_por_video:
                    segundo_por_video[vid] = {
                        "segmento_id": s["id"],
                        "cam_id": s.get("cam_id"),
                        "offset_s": offset_video_segmento(meta_por_video.get(vid, {}), s),
                    }
    except Exception as e:
        log.warning("[duvidas] 2º ângulo não resolvido (%s) — segue com uma câmera.", e)

    assumidos = labels_com_categoria_assumida(sb, empresa, processo)

    itens, por_rotulo, por_tipo = [], {}, {}
    for e in eventos:
        if e.get("principal") is False:
            continue                       # auditoria não vai para a fila
        duvida, motivo, tipo = evento_em_duvida(e, lim, assumidos)
        if not duvida:
            continue
        label = e.get("label_corrigido") or e.get("comportamento_label") or "?"
        dur = max(0.0, float(e.get("tempo_fim_s") or 0) - float(e.get("tempo_inicio_s") or 0))
        r = por_rotulo.setdefault(label, {"rotulo": label, "eventos": 0, "segundos": 0.0})
        r["eventos"] += 1
        r["segundos"] += dur
        t = por_tipo.setdefault(tipo, {"tipo": tipo, "eventos": 0, "segundos": 0.0})
        t["eventos"] += 1
        t["segundos"] += dur
        if (rotulo and label != rotulo) or (tipo_filtro and tipo != tipo_filtro):
            continue                       # filtros aplicados DEPOIS do agregado
        itens.append({
            "id": e["id"], "video_id": e.get("video_id"), "rotulo": label,
            "descricao": e.get("descricao_bruta"),
            "ini": e.get("tempo_inicio_s"), "fim": e.get("tempo_fim_s"),
            "minutos": round(dur / 60, 2),
            "confianca": e.get("confianca"),
            "motivo": motivo,
            # "sem_evidencia" | "discordancia" | "camada" | "categoria_assumida"
            # — nunca misturados: exigem ações diferentes de quem valida.
            "tipo": tipo,
            "n_amostras": e.get("n_amostras"),
            "camadas": [d.get("nome") for d in (e.get("camadas_disparadas") or [])
                        if d.get("modo") == "ativa"],
            "rotulos_competindo": e.get("rotulos_competindo") or [],
            "cam_id": cam_por_video.get(e.get("video_id")),
            # Fase 63: 2º ângulo do MESMO instante (offset real de relógio).
            "segundo_angulo": segundo_por_video.get(e.get("video_id")),
            "pessoa": e.get("pessoa_track_id"),
            "papel": e.get("papel_pessoa"),
        })

    # ORDEM POR IMPACTO: mais minutos primeiro. Empate → menor confiança antes.
    itens.sort(key=lambda x: (-x["minutos"], x["confianca"] if x["confianca"] is not None else 1))
    lista_rot = sorted(
        ({"rotulo": v["rotulo"], "eventos": v["eventos"],
          "minutos": round(v["segundos"] / 60, 1)} for v in por_rotulo.values()),
        key=lambda x: -x["minutos"],
    )
    return {
        "limiar": lim,
        "total": sum(v["eventos"] for v in por_rotulo.values()),
        "minutos_totais": round(sum(v["segundos"] for v in por_rotulo.values()) / 60, 1),
        "por_rotulo": lista_rot,
        # Separado de propósito: "amostra única" e "amostras discordantes" são
        # problemas distintos e misturá-los esconde os dois.
        "por_tipo": sorted(
            ({"tipo": v["tipo"], "eventos": v["eventos"],
              "minutos": round(v["segundos"] / 60, 1)} for v in por_tipo.values()),
            key=lambda x: -x["minutos"]),
        "itens": itens[:limite],
        "filtrado_por": rotulo,
    }


class VideoJaProcessado(RuntimeError):
    """Fase 72 — este arquivo já virou eventos. Processar de novo DUPLICA."""


def video_ja_processado(sb, empresa: str, processo: str, caminho: str | None) -> dict | None:
    """Linha de `videos` com este mesmo `caminho`, se existir. None = livre.

    Falha de leitura devolve None de propósito: a guarda não pode ser o motivo
    de um vídeo legítimo não ser processado. Ela evita o acidente comum, não
    substitui a substituição idempotente que ainda não existe.
    """
    if not caminho:
        return None
    try:
        r = (
            sb.table("videos").select("id, nome, processado_em")
            .eq("empresa", empresa).eq("processo", processo)
            .eq("caminho", caminho).limit(1).execute().data
        ) or []
        return r[0] if r else None
    except Exception as e:  # noqa: BLE001
        log.warning("[guarda] checagem de duplicata falhou (%s) — seguindo.", e)
        return None


def _barrar_duplicata(sb, empresa: str, processo: str, caminho: str | None) -> None:
    """Recusa processar um caminho que já tem vídeo. Ver `VideoJaProcessado`."""
    ja = video_ja_processado(sb, empresa, processo, caminho)
    if not ja:
        return
    raise VideoJaProcessado(
        f"Este arquivo já foi processado em {ja.get('processado_em')} "
        f"(video_id={ja.get('id')}). Processar de novo criaria uma SEGUNDA "
        f"linha em `videos` e um SEGUNDO conjunto de eventos — tudo passaria a "
        f"contar em dobro. Reprocessamento ainda não é suportado; ver "
        f"docs/problemas_conhecidos.md. Se este segmento está em ERRO mas o "
        f"vídeo existe, o processamento chegou ao fim: marque o segmento como "
        f"concluído em vez de reenfileirá-lo."
    )


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
    descritores_track: list[dict] | None = None,
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
    # Fase 72: última checagem antes do INSERT. `insert` sem upsert é o que
    # torna o reprocessamento destrutivo; enquanto não houver substituição
    # idempotente, a duplicata é barrada aqui, no ponto exato da escrita.
    _barrar_duplicata(sb, empresa, processo, caminho_storage)
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

    # Fase 55 — HERANÇA NA INGESTÃO: se o comportamento já tem categoria, o
    # evento NASCE com ela (origem 'herdado'). Sem isto, todo vídeo novo
    # reacumula cinza que a propagação teria de limpar depois.
    # Rótulo sem categoria (ex.: `acao_indefinida`, que fica sem categoria de
    # propósito desde a Fase 49) simplesmente não entra no mapa — nunca é chutado.
    cat_ingestao: dict[str, str] = {}
    try:
        _cats = varrer(sb, "comportamentos", "label, categoria_lean",
                       empresa=empresa, processo=processo)
        cat_ingestao = {c["label"]: c["categoria_lean"]
                        for c in _cats if c.get("categoria_lean")}
    except Exception as e:
        log.warning(f"[lean] herança na ingestão indisponível (não-fatal): {e}")

    linhas_eventos: list[dict] = []
    n_auto_validados = 0
    for e in eventos:
        origem = origem_de(e["descricao_bruta"])
        # Fase 61/62 — INFERÊNCIA DA MÁQUINA NUNCA É VERDADE HUMANA.
        # NENHUMA origem inferida auto-valida. `correcao_aprendida` e
        # `vocabulario_canonico` PROPÕEM o rótulo; quem confirma é a pessoa.
        # `validado_humano`/`validacao_correto` são a base do dataset dos 30
        # dias e do placar das camadas — se a máquina escreve neles, não sobra
        # verdade de referência contra a qual medir coisa alguma. Metade do que
        # a tela chamava de "validado" era a máquina assinando por si mesma.
        #
        # `origem_validacao` continua gravada e passa a significar "rótulo
        # proposto por" — é o que ajuda quem julga o evento na fila.
        #
        # As duas exceções abaixo NÃO são afirmações de verdade: `posto_vazio`
        # e `auditoria` usam validado_humano=True só como mecanismo para ficar
        # FORA da fila. Ambas são excluídas do aprendizado por ORIGENS_MAQUINA.
        auto_validado = False
        # Fase 28: posto_vazio é determinístico (sem VLM) — nasce validado e
        # FORA da fila de validação, mas DENTRO das métricas (principal=True).
        if e.get("papel_pessoa") == "posto_vazio":
            origem = "posto_vazio"
            auto_validado = True
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
            # Fase 82: NULL quando não há caixa. O dict de zeros que ficava aqui
            # afirmava uma pessoa de tamanho nenhum na origem da imagem — e
            # qualquer leitor a tratava como medida válida.
            "bbox_inicio": _bbox_jsonb(e.get("bbox_inicio")),
            "bbox_cam": e.get("bbox_cam"),
            "bbox_stats": e.get("bbox_stats"),
            "zona_contexto": e["zona_contexto"],
            "papel_pessoa": e.get("papel_pessoa"),
            "n_amostras": e["n_amostras"],
            "confianca": e["confianca"],
            "origem_validacao": origem,
            # IMPORTANTE: validado_humano sempre explícito (PostgREST batch
            # não aplica DEFAULT de coluna ausente).
            "validado_humano": auto_validado,
            # Fase 75 — MESMA REGRA, e ela mordeu de verdade: `em_duvida` é
            # NOT NULL DEFAULT false, mas só era escrito quando alguma camada
            # disparava. Num INSERT em lote o PostgREST UNIFICA as colunas de
            # todas as linhas do chunk: basta UMA linha trazer `em_duvida` para
            # que as demais sejam enviadas com NULL explícito — e o DEFAULT não
            # se aplica a NULL explícito. Resultado: 23502, o vídeo inteiro
            # falha.
            #
            # Ficou latente desde a Fase 57 porque nenhuma camada estava ATIVA:
            # nenhuma linha trazia a chave, o lote era homogêneo e o DEFAULT
            # valia. As camadas de contradição das Fases 68/69 tornaram o lote
            # heterogêneo e o bug apareceu no primeiro vídeo.
            #
            # Regra geral para este dict: coluna NOT NULL vai SEMPRE explícita.
            "em_duvida": bool(e.get("em_duvida")),
            # Fase 70: NOT NULL DEFAULT false. Hoje nenhuma linha do lote a
            # traz, então o DEFAULT ainda valeria — mas é a MESMA armadilha
            # esperando a primeira linha que a escreva. Explícita desde já.
            "descricao_invalida": False,
            # Fase 16: True nos principais; None quando a consolidação está off
            # (comportamento antigo — o filtro downstream mantém True + None).
            "principal": e.get("principal"),
        }
        # Fase 59: sinais do minuto que explicam a dúvida na fila.
        for _c in ("concordancia", "n_rotulos_no_minuto", "rotulos_competindo"):
            if e.get(_c) is not None:
                row[_c] = e[_c]
        # Fase 57: dúvida levantada pelas camadas (o placar depende disto).
        # `camadas_disparadas` e `duvida_motivo` são NULLABLE — podem continuar
        # condicionais sem risco. `em_duvida` já foi escrito acima.
        if e.get("camadas_disparadas"):
            row["camadas_disparadas"] = e["camadas_disparadas"]
            if e.get("duvida_motivo"):
                row["duvida_motivo"] = e["duvida_motivo"]
        _cat_h = cat_ingestao.get(e["comportamento_label"])
        if _cat_h:
            row["categoria_lean"] = _cat_h
            row["categoria_lean_origem"] = "herdado"
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
            "bbox_inicio": _bbox_jsonb(e.get("bbox_inicio")),
            "bbox_cam": e.get("bbox_cam"),
            "bbox_stats": e.get("bbox_stats"),
            "zona_contexto": e["zona_contexto"],
            "papel_pessoa": e.get("papel_pessoa"),
            "n_amostras": e["n_amostras"], "confianca": e["confianca"],
            "origem_validacao": "auditoria",
            # Mesmo lote dos principais: a coluna NOT NULL tem de vir explícita
            # aqui também, senão a unificação do PostgREST manda NULL.
            "em_duvida": False,
            "descricao_invalida": False,
            # validado_humano=True mantém os crus FORA de toda query "pendente"
            # (validação/contagens) sem precisar filtrar por `principal` no banco.
            # validacao_correto fica null → os leitores de métrica os removem pelo
            # filtro em memória `principal is not False`.
            "validado_humano": True,
            "principal": False,
            **({"categoria_lean": cat_ingestao[e["comportamento_label"]],
                "categoria_lean_origem": "herdado"}
               if cat_ingestao.get(e["comportamento_label"]) else {}),
        })

    CHUNK = 100
    inseridos: list[dict] = []
    for i in range(0, len(linhas_eventos), CHUNK):
        resp = sb.table("eventos").insert(linhas_eventos[i : i + CHUNK]).execute()
        inseridos.extend(resp.data or [])
    # Fase 36: ids dos PRINCIPAIS (mesma ordem de `eventos` — os primeiros N
    # de linhas_eventos), p/ pré-extrair os frames enquanto o vídeo é local.
    ids_principais = [r.get("id") for r in inseridos[: len(eventos)]]

    # Fase 83 — descritor por track. NÃO-FATAL de propósito: é insumo de
    # experimento, e um experimento não pode ser motivo para um vídeo da
    # campanha falhar. `upsert` na chave (video_id, pessoa_track_id) para que
    # reprocessar não duplique linha (o vídeo duplicaria os eventos — problema
    # conhecido nº 1 — mas o descritor, não).
    if descritores_track:
        try:
            linhas_desc = [{
                "empresa": empresa, "processo": processo, "video_id": video_id,
                "gravado_em": gravado_em,
                # Fase 84: a chave PRECISA incluir a câmera. cam1 e cam2 numeram
                # tracks de forma independente — as duas têm um track 1, e sem a
                # câmera na chave o upsert de uma sobrescreveria a outra.
                # `cam_id` nunca nulo: coluna de chave com NULL não deduplica.
                **{**d, "cam_id": (d.get("cam_id") or cam_id or "cam1")},
            } for d in descritores_track]
            sb.table("descritores_track").upsert(
                linhas_desc, on_conflict="video_id,cam_id,pessoa_track_id"
            ).execute()
            log.info("[descritor] %d track(s) descritos neste vídeo.", len(linhas_desc))
        except Exception as e:   # noqa: BLE001
            log.warning("[descritor] não gravado (%s) — o vídeo segue normal.", e)

    return video_id, n_auto_validados, ids_principais


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
    todos_eventos = varrer(
        sb, "eventos",
        "video_id, comportamento_label, label_corrigido, tempo_inicio_s, "
        "tempo_fim_s, pessoa_track_id, validacao_correto, validado_humano, principal",
        empresa=empresa, processo=processo,
    )
    # Fase 16: só os PRINCIPAIS (1/min); crus de auditoria (principal=False) fora.
    base = [
        e for e in todos_eventos
        if e.get("validacao_correto") is not False and e.get("principal") is not False
    ]

    videos_ctx = varrer(sb, "videos", "id, duracao_s, processado_em",
                        empresa=empresa, processo=processo)
    n_videos_ctx = len(videos_ctx)
    duracao_total_ctx = sum((v.get("duracao_s") or 0) for v in videos_ctx)

    catalogo = catalogo or {}
    if not catalogo:
        comps = varrer(sb, "comportamentos", "label, descricao",
                       empresa=empresa, processo=processo)
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
    video_id: str | None,
    contexto_analise: dict,
) -> list[dict]:
    """Fase 18: modelo CURADO (não empilha). Gera sugestões do AGREGADO, dedup
    contra as pendentes/dispensadas, remove as pendentes que a IA não citou mais
    (replace, como insights_globais) e limita a KV_SUGESTOES_MAX. NUNCA toca em
    `realizada`/`dispensada`. Mantém o sinal `voltou_apos_realizada`."""
    prompt = PROMPT_ANALISE.format(empresa=empresa, processo=processo)
    resposta = groq_text_call(
        groq_client,
        prompt + json.dumps(contexto_analise, indent=2, ensure_ascii=False),
        model=GROQ_MODEL_ANALISE,
        json_mode=True,
        max_tokens=4000,   # Fase 14: fora do Free Tier — mais espaço p/ qualidade
        temperatura=0.3,
    )
    sugestoes = json.loads(resposta).get("sugestoes") or []

    def _carrega(status: str) -> list[dict]:
        try:
            return (
                sb.table("sugestoes_melhoria")
                .select("id, sugestao")
                .eq("empresa", empresa)
                .eq("processo", processo)
                .eq("status", status)
                .limit(500)
                .execute()
                .data
            ) or []
        except Exception as e:
            log.warning(f"[sugestoes] falha ao ler {status}: {e}")
            return []

    pend = _carrega("pendente")
    disp_tok = [_normalizar_pergunta(r.get("sugestao") or "") for r in _carrega("dispensada")]
    rea_tok = [_normalizar_pergunta(r.get("sugestao") or "") for r in _carrega("realizada")]
    pend_tok = [_normalizar_pergunta(r.get("sugestao") or "") for r in pend]

    try:
        max_n = int(os.environ.get("KV_SUGESTOES_MAX", "6"))
    except Exception:
        max_n = 6
    ordem_prio = {"alta": 0, "media": 1, "info": 2}
    sugestoes = sorted(sugestoes, key=lambda s: ordem_prio.get((s.get("prioridade") or "info").lower(), 3))

    # Tokens das sugestões novas (após tirar as dispensadas) — p/ decidir quais
    # pendentes continuam "vivas".
    novas_validas = [s for s in sugestoes if not (disp_tok and _eh_duplicada(s.get("sugestao", "") or "", disp_tok, 0.5))]
    novas_tok = [_normalizar_pergunta(s.get("sugestao", "") or "") for s in novas_validas]

    # 1) DELETE das pendentes que a IA NÃO citou mais nesta rodada (replace).
    ids_stale = [
        r["id"] for r in pend
        if not _eh_duplicada(r.get("sugestao", "") or "", novas_tok, 0.5)
    ]
    for sid in ids_stale:
        try:
            sb.table("sugestoes_melhoria").delete().eq("id", sid).eq("status", "pendente").execute()
        except Exception as e:
            log.warning(f"[sugestoes] falha ao remover pendente obsoleta {sid}: {e}")

    # 2) INSERT só das genuinamente novas (não casam com pendente já existente),
    #    respeitando o cap (mantendo as pendentes reaproveitadas).
    reaproveitadas = len(pend) - len(ids_stale)
    linhas_sug: list[dict] = []
    for s, tok in zip(novas_validas, novas_tok):
        if reaproveitadas + len(linhas_sug) >= max_n:
            break
        texto = s.get("sugestao", "") or ""
        if pend_tok and _eh_duplicada(texto, pend_tok, 0.5):
            continue  # já existe uma pendente igual — não duplica
        voltou = bool(rea_tok) and _eh_duplicada(texto, rea_tok, 0.5)
        linhas_sug.append({
            "video_id": video_id,
            "empresa": empresa,
            "processo": processo,
            "prioridade": s.get("prioridade", "info"),
            "area": s.get("area", ""),
            "situacao": s.get("situacao", ""),
            "causa_provavel": s.get("causa_provavel", ""),
            "sugestao": texto,
            "impacto_estimado": s.get("impacto_estimado", ""),
            "eventos_relacionados": {"comportamentos": s.get("comportamentos_relacionados", [])},
            "voltou_apos_realizada": voltou,
        })
    if linhas_sug:
        sb.table("sugestoes_melhoria").insert(linhas_sug).execute()
    log.info(
        f"[sugestoes] {empresa}/{processo}: {len(novas_validas)} geradas → "
        f"{reaproveitadas} mantidas + {len(linhas_sug)} novas, {len(ids_stale)} removidas"
    )
    return sugestoes


def recomputar_sugestoes_processo(sb: Client, empresa: str, processo: str) -> int:
    """Fase 18: recomputa as sugestões do processo a partir do AGREGADO (chamado
    pelo debouncer, 1× por rajada — NÃO por vídeo). Monta o contexto agregado e
    delega ao modelo curado do `etapa_gerar_sugestoes`. Não-fatal."""
    try:
        descricao = resolver_descricao_processo(sb, empresa, processo, None)
    except Exception:
        descricao = ""
    try:
        conhecimento = construir_bloco_conhecimento_adquirido(sb, empresa, processo)
    except Exception:
        conhecimento = ""
    try:
        memoria = carregar_memoria_do_negocio(sb, empresa, processo)
    except Exception:
        memoria = None
    try:
        contexto = montar_contexto_agregado(
            sb, empresa, processo,
            descricao_processo=descricao, memoria=memoria,
            conhecimento_adquirido_texto=conhecimento,
        )
        # groq_client vestigial (ai_provider); video_id=None (sugestão do agregado).
        sug = etapa_gerar_sugestoes(sb, None, empresa, processo, None, contexto)
        return len(sug or [])
    except Exception as e:
        log.warning(f"[sugestoes] recompute {empresa}/{processo} falhou (não-fatal): {e}")
        return 0


# ═════════════════════════════════════════════════════════════════════════
# CLASSIFICAÇÃO LEAN (BINÁRIA, Fase 49) — IA classifica cada comportamento em
# valor_agregado (produtivo) | desperdicio (não-produtivo). null = não-classif.
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
# Fase 49: classificação BINÁRIA — produtivo (valor_agregado) × não-produtivo
# (desperdicio). "apoio" foi removido (a IA decide por ação).
CATEGORIAS_LEAN_VALIDAS = {"valor_agregado", "desperdicio"}

# ═════════════════════════════════════════════════════════════════════════
# Fase 63 — "NÃO CLASSIFICADO" DEIXA DE EXISTIR.
#
# Todo tempo observado é produtivo ou não-produtivo. Não há terceira fatia,
# nem no banco, nem nas métricas, nem na tela. O cinza era uma não-resposta
# que crescia sem dono: ninguém olhava para ele e ele não pedia nada a
# ninguém.
#
# A regra de decisão, quando falta evidência, é a convenção Lean: o ônus da
# prova é de quem afirma que a atividade agrega valor. Sem essa prova, é
# NÃO-PRODUTIVO. Isso é conservador na direção certa — nunca infla a
# produtividade que o cliente vai mostrar para a diretoria.
#
# O que substitui o cinza é a DÚVIDA DECLARADA: sempre que o sistema decide
# sem evidência, ele marca a decisão com origem 'fallback' e o trecho vai
# para a fila de dúvidas. A pergunta deixa de ser "quanto está sem
# classificar?" e passa a ser "de quanto eu ainda não tenho certeza?" — que
# é a mesma informação, só que acionável e com fim previsto.
# ═════════════════════════════════════════════════════════════════════════
CATEGORIA_SEM_EVIDENCIA = "desperdicio"
# Origem que marca "o sistema escolheu, mas sem evidência" — é ela que joga o
# trecho para a fila. Precede 'ia' em fraqueza: 'ia' é um palpite fundamentado
# no domínio; 'fallback' é a ausência de qualquer palpite.
ORIGEM_SEM_EVIDENCIA = "fallback"


def categoria_efetiva(cat: str | None) -> str:
    """Categoria Lean que a tela mostra. NUNCA devolve None.

    Único ponto de decisão do sistema inteiro: qualquer lugar que precise
    saber "isso é produtivo ou não" passa por aqui. Antes cada agregação
    escrevia `cat or "nao_classificado"` por conta própria, e era daí que
    vinham as divergências entre a tela principal e os gráficos.
    """
    return cat if cat in CATEGORIAS_LEAN_VALIDAS else CATEGORIA_SEM_EVIDENCIA


def categoria_tem_evidencia(cat: str | None, origem: str | None = None) -> bool:
    """False quando a categoria foi assumida, não decidida. É o que separa
    'o sistema sabe' de 'o sistema teve de escolher' — e o que alimenta a
    fila de dúvidas e o KPI de cobertura."""
    if cat not in CATEGORIAS_LEAN_VALIDAS:
        return False
    return (origem or "") != ORIGEM_SEM_EVIDENCIA


# Label da ação que o modelo de visão não conseguiu nomear. Fase 63: passa a
# receber categoria como todo o resto (não-produtivo, por falta de prova de
# valor) — e vai para a fila de dúvidas, que é onde a incerteza deve morar.
LABEL_INDEFINIDA = "acao_indefinida"


# ═════════════════════════════════════════════════════════════════════════
# Fase 55 — PROPAGAÇÃO da categoria Lean: comportamento → eventos
#
# PRECEDÊNCIA (a regra que não se quebra):
#     humano (no evento)  >  aprendido  >  herdado (do comportamento)
#
# Só são elegíveis para escrita eventos com `categoria_lean IS NULL` OU
# `categoria_lean_origem = 'herdado'`. Um evento marcado à mão pelo gestor é
# INVIOLÁVEL: sobrescrevê-lo destrói a confiança no produto e é pior que o cinza.
#
# `acao_indefinida` (e qualquer rótulo cujo comportamento esteja com categoria
# NULA de propósito) fica de fora automaticamente: a propagação só copia
# categoria EXISTENTE, nunca inventa uma.
# ═════════════════════════════════════════════════════════════════════════
def propagar_categoria_para_eventos(
    sb: Client, empresa: str, processo: str, label: str, categoria: str | None,
    *, dry_run: bool = False,
) -> int:
    """Desce a categoria do comportamento para os eventos ELEGÍVEIS daquele
    (empresa, processo, label). Retorna quantos eventos foram (ou seriam)
    afetados. Não-fatal: falha aqui nunca derruba quem chamou.

    Casa pelo label EFETIVO — `label_corrigido` quando existe, senão
    `comportamento_label`. Filtrar só por `comportamento_label` deixaria de
    fora justamente os eventos que o gestor renomeou na validação, que são os
    que ele mais espera ver classificados.

    `categoria=None` NÃO limpa evento nenhum: liberar o comportamento para a IA
    reclassificar não pode apagar o que já foi herdado."""
    if not categoria or not label:
        return 0

    def _aplica(coluna_filtro: str, so_sem_correcao: bool) -> int:
        try:
            q = (
                sb.table("eventos")
                .select("id") if dry_run else sb.table("eventos").update(
                    {"categoria_lean": categoria, "categoria_lean_origem": "herdado"}
                )
            )
            q = q.eq("empresa", empresa).eq("processo", processo)
            q = q.eq(coluna_filtro, label)
            if so_sem_correcao:
                q = q.is_("label_corrigido", "null")
            # PRECEDÊNCIA: só NULL ou já-herdado. 'humano' e 'aprendido' ficam.
            q = q.or_("categoria_lean.is.null,categoria_lean_origem.eq.herdado")
            return len(q.execute().data or [])
        except Exception as e:
            log.warning("[lean] propagação %s=%s falhou (não-fatal): %s",
                        coluna_filtro, label, e)
            return 0

    # Dois passes em vez de um OR aninhado: o PostgREST aceita, mas a forma
    # aninhada é frágil e um erro de sintaxe aqui passaria despercebido.
    n = _aplica("comportamento_label", so_sem_correcao=True)
    n += _aplica("label_corrigido", so_sem_correcao=False)
    return n


VAZIO_ATIPICO_PCT = float(os.environ.get("KV_VAZIO_ATIPICO_PCT", "80"))


def auditar_dia(sb, empresa: str, processo: str, dia: str,
                por_bloco: int = 3, limite: int = 60) -> dict:
    """Fase 79 — AUDITAR não é VALIDAR.

    O dia 29 sumiu das telas com 463 eventos: 245 de origem `posto_vazio` e 163
    de `auditoria`. Nos dois casos `validado_humano=True` é o MECANISMO que os
    mantém fora da fila — ninguém os julgou. O dia estava correto (o operador
    faltou), mas se estivesse errado não haveria como perceber: um dia
    inteiramente classificado como posto vazio fica invisível por construção.

    Esta função abre o dia SEM tocar em validação nenhuma. Só leitura.

    AMOSTRAGEM, não lista: 245 trechos de posto vazio não se auditam um a um.
    Blocos contíguos do mesmo rótulo são detectados e de cada um saem até
    `por_bloco` trechos — início, meio e fim. Se o operador estivesse lá, ele
    apareceria em algum: o começo pega a transição que originou o bloco, o fim
    pega a que o encerrou, e o meio pega o regime.
    """
    def _fab():
        return (
            sb.table("eventos")
            .select("id, video_id, comportamento_label, label_corrigido, "
                    "descricao_bruta, tempo_inicio_s, tempo_fim_s, principal, "
                    "papel_pessoa, origem_validacao, validado_humano, "
                    "validacao_correto, confianca, n_amostras, pessoa_track_id")
            .eq("empresa", empresa).eq("processo", processo).order("id")
        )
    try:
        todos = _scan_todos(_fab)
    except Exception as e:  # noqa: BLE001
        return {"erro": f"leitura de eventos falhou: {e}"}

    vids = sorted({e.get("video_id") for e in todos if e.get("video_id")})
    meta: dict = {}
    try:
        for i in range(0, len(vids), 100):
            for v in (sb.table("videos")
                      .select("id, nome, cam_id, gravado_em, processado_em, duracao_s")
                      .in_("id", vids[i : i + 100]).execute().data or []):
                meta[v["id"]] = v
    except Exception as e:  # noqa: BLE001
        log.warning("[auditoria] metadados de vídeo não lidos (%s).", e)

    # Recorta o dia pelo instante REAL de gravação (relógio do Pi no nome).
    do_dia, videos_dia = [], {}
    for e in todos:
        if e.get("principal") is False:
            continue
        v = meta.get(e.get("video_id")) or {}
        dt0 = _inicio_video_dt(v)
        if not dt0 or dt0.date().isoformat() != dia:
            continue
        inst = dt0 + timedelta(seconds=float(e.get("tempo_inicio_s") or 0))
        e = {**e, "_inst": inst}
        do_dia.append(e)
        videos_dia.setdefault(e["video_id"], v)
    do_dia.sort(key=lambda x: x["_inst"])

    if not do_dia:
        # Mesmo contrato do caminho cheio: a tela não pode ter que adivinhar
        # quais campos existem num dia vazio.
        return {"dia": dia, "eventos": 0, "minutos": 0.0, "posto_vazio_pct": 0.0,
                "atipico": False, "limiar_atipico": VAZIO_ATIPICO_PCT,
                "contradicoes_c1": 0, "videos": [], "blocos": [], "amostras": [],
                "nota": "Nenhum vídeo processado com gravação nesta data."}

    # ── Blocos contíguos do mesmo rótulo ──
    blocos: list[dict] = []
    for e in do_dia:
        lbl = e.get("label_corrigido") or e.get("comportamento_label")
        dur = max(0.0, float(e.get("tempo_fim_s") or 0) - float(e.get("tempo_inicio_s") or 0))
        if blocos and blocos[-1]["rotulo"] == lbl:
            blocos[-1]["eventos"].append(e)
            blocos[-1]["segundos"] += dur
            blocos[-1]["fim"] = e["_inst"]
        else:
            blocos.append({"rotulo": lbl, "eventos": [e], "segundos": dur,
                           "ini": e["_inst"], "fim": e["_inst"]})

    amostras = []
    for b in sorted(blocos, key=lambda x: -x["segundos"]):
        evs = b["eventos"]
        n = len(evs)
        # Início, meio e fim: as transições e o regime.
        idxs = sorted({0, n // 2, n - 1})[:max(1, por_bloco)]
        for pos, i in enumerate(idxs):
            e = evs[i]
            v = videos_dia.get(e["video_id"], {})
            amostras.append({
                "id": e["id"], "video_id": e["video_id"],
                "video_nome": v.get("nome"), "cam_id": v.get("cam_id"),
                "rotulo": b["rotulo"], "descricao": e.get("descricao_bruta"),
                "ini": e.get("tempo_inicio_s"), "fim": e.get("tempo_fim_s"),
                "hora": e["_inst"].strftime("%H:%M"),
                "papel": e.get("papel_pessoa"),
                "origem": e.get("origem_validacao"),
                "posicao": ("inicio", "meio", "fim")[min(pos, 2)] if n > 1 else "unico",
                "bloco_eventos": n,
                "bloco_minutos": round(b["segundos"] / 60, 1),
            })
        if len(amostras) >= limite:
            break

    seg_total = sum(b["segundos"] for b in blocos)
    seg_vazio = sum(b["segundos"] for b in blocos
                    if b["rotulo"] == POSTO_VAZIO_LABEL)
    pct_vazio = round(seg_vazio / seg_total * 100, 1) if seg_total else 0.0
    # A contradição da C1 sobre o que JÁ está gravado: rótulo posto_vazio com o
    # rastreamento dizendo que o operador estava lá.
    contradicoes = [e for e in do_dia
                    if (e.get("label_corrigido") or e.get("comportamento_label")) == POSTO_VAZIO_LABEL
                    and e.get("papel_pessoa") == "operador"]
    return {
        "dia": dia,
        "eventos": len(do_dia),
        "minutos": round(seg_total / 60, 1),
        "posto_vazio_pct": pct_vazio,
        # Dia assim ou é falta real, ou é falha grave de detecção. As duas
        # merecem olhada, e nenhuma das duas chamava atenção antes.
        "atipico": pct_vazio >= VAZIO_ATIPICO_PCT,
        "limiar_atipico": VAZIO_ATIPICO_PCT,
        "contradicoes_c1": len(contradicoes),
        "videos": [{"id": k, "nome": v.get("nome"), "cam_id": v.get("cam_id"),
                    "duracao_s": v.get("duracao_s")}
                   for k, v in sorted(videos_dia.items(), key=lambda kv: kv[1].get("nome") or "")],
        "blocos": [{"rotulo": b["rotulo"], "eventos": len(b["eventos"]),
                    "minutos": round(b["segundos"] / 60, 1),
                    "de": b["ini"].strftime("%H:%M"), "ate": b["fim"].strftime("%H:%M")}
                   for b in blocos],
        "amostras": amostras,
        "nota": ("Auditoria é só leitura: nada aqui entra na fila nem muda "
                 "validação. Os trechos são AMOSTRAS (início/meio/fim de cada "
                 "bloco), não a lista completa."),
    }


def relatorio_reprocesso_por_video(
    sb, empresa: str, processo: str, custo_por_min: float = 0.02,
) -> dict:
    """Fase 71 — SÓ LEITURA. Ranqueia os vídeos por MINUTOS CONTAMINADOS.

    A pergunta que responde: quantos vídeos concentram 80% do estrago, e quanto
    custaria reprocessar só eles? Ranquear por CONTAGEM de eventos daria a
    resposta errada — um vídeo com 20 eventos de 5s pesa menos que um com 3
    eventos de 1 min, e é o minuto que move o placar.

    Também separa, por vídeo, o que decide se ele PODE ser reprocessado sem
    perda:
      • `correcoes_humanas` — reprocessar cria eventos NOVOS; as decisões
        tomadas nos eventos antigos não sobrevivem;
      • `binario_disponivel` — sem o arquivo no Storage não há reprocesso.

    NÃO ESCREVE NADA. É o insumo da decisão entre reprocessar e limpar.
    """
    diag = diagnosticar_contagio_por_descricao(sb, empresa, processo, dry_run=True)
    if "erro" in diag:
        return diag

    # Reconstrói o conjunto contaminado com o video_id junto (o diagnóstico
    # agrega por descrição; aqui a unidade é o vídeo).
    def _fab():
        return (
            sb.table("eventos")
            .select("id, video_id, comportamento_label, label_corrigido, "
                    "descricao_bruta, tempo_inicio_s, tempo_fim_s, "
                    "origem_validacao, validado_humano, principal, criado_em, "
                    "validado_em")
            .eq("empresa", empresa).eq("processo", processo).order("id")
        )
    try:
        eventos = _scan_todos(_fab)
    except Exception as e:  # noqa: BLE001
        return {"erro": f"leitura de eventos falhou: {e}"}

    # Mesma assinatura do diagnóstico, incluindo o corte do mapeamento natural.
    alvo_desc_rot = {(d["descricao"] or "").strip().lower(): d["rotulo"]
                     for d in diag.get("por_descricao", [])}

    por_video: dict = {}
    correcoes_por_video: dict = {}
    for e in eventos:
        vid = e.get("video_id")
        if not vid:
            continue
        if (e.get("origem_validacao") or "") == "humano":
            correcoes_por_video[vid] = correcoes_por_video.get(vid, 0) + 1
            continue
        if e.get("principal") is False:
            continue
        desc = (e.get("descricao_bruta") or "").strip().lower()
        label_ef = e.get("label_corrigido") or e.get("comportamento_label")
        if alvo_desc_rot.get(desc) != label_ef:
            continue
        dur = max(0.0, float(e.get("tempo_fim_s") or 0) - float(e.get("tempo_inicio_s") or 0))
        d = por_video.setdefault(vid, {"eventos": 0, "segundos": 0.0})
        d["eventos"] += 1
        d["segundos"] += dur

    metas: dict = {}
    try:
        vids = sorted(por_video)
        for i in range(0, len(vids), 100):
            for v in (sb.table("videos")
                      .select("id, nome, duracao_s, processado_em, video_removido_em")
                      .in_("id", vids[i : i + 100]).execute().data or []):
                metas[v["id"]] = v
    except Exception as e:  # noqa: BLE001
        log.warning("[reprocesso] metadados de vídeo não lidos (%s).", e)

    linhas = []
    for vid, d in por_video.items():
        m = metas.get(vid, {})
        linhas.append({
            "video_id": vid,
            "nome": m.get("nome"),
            "processado_em": m.get("processado_em"),
            "duracao_min": round(float(m.get("duracao_s") or 0) / 60, 1),
            "eventos_contaminados": d["eventos"],
            "minutos_contaminados": round(d["segundos"] / 60, 1),
            # Reprocessar cria eventos NOVOS: estas decisões não sobrevivem.
            "correcoes_humanas": correcoes_por_video.get(vid, 0),
            "binario_disponivel": m.get("video_removido_em") is None,
        })
    linhas.sort(key=lambda x: -x["minutos_contaminados"])

    total_min = sum(l["minutos_contaminados"] for l in linhas) or 0.0
    acc = 0.0
    n_80 = None
    custo_80 = 0.0
    for i, l in enumerate(linhas, 1):
        acc += l["minutos_contaminados"]
        l["pct_do_estrago"] = round(l["minutos_contaminados"] / total_min * 100, 1) if total_min else 0.0
        l["acumulado_pct"] = round(acc / total_min * 100, 1) if total_min else 0.0
        l["custo_reprocesso"] = round(l["duracao_min"] * custo_por_min, 2)
        if n_80 is None and l["acumulado_pct"] >= 80:
            n_80 = i
            custo_80 = round(sum(x["duracao_min"] for x in linhas[:i]) * custo_por_min, 2)

    limpos = [l for l in linhas if l["correcoes_humanas"] == 0 and l["binario_disponivel"]]
    return {
        "custo_por_min": custo_por_min,
        "videos_afetados": len(linhas),
        "minutos_contaminados": round(total_min, 1),
        "minutos_de_video": round(sum(l["duracao_min"] for l in linhas), 1),
        "custo_reprocessar_tudo": round(sum(l["duracao_min"] for l in linhas) * custo_por_min, 2),
        # Pareto: quantos vídeos concentram 80% do estrago, e o que custam.
        "videos_para_80pct": n_80,
        "custo_80pct": custo_80,
        # Os que dá para reprocessar sem perder decisão humana nenhuma.
        "sem_correcao_humana": {
            "videos": len(limpos),
            "minutos_contaminados": round(sum(l["minutos_contaminados"] for l in limpos), 1),
            "minutos_de_video": round(sum(l["duracao_min"] for l in limpos), 1),
            "custo": round(sum(l["duracao_min"] for l in limpos) * custo_por_min, 2),
        },
        "com_correcao_humana": [
            {k: l[k] for k in ("video_id", "nome", "minutos_contaminados", "correcoes_humanas")}
            for l in linhas if l["correcoes_humanas"] > 0
        ],
        "sem_binario": [l["nome"] for l in linhas if not l["binario_disponivel"]],
        "por_video": linhas,
        "aviso": ("⚠️ REPROCESSAR HOJE DUPLICA: `etapa_persistir` faz INSERT em "
                  "`videos` sem dedup por caminho, então o mesmo arquivo vira uma "
                  "SEGUNDA linha de vídeo e um SEGUNDO conjunto de eventos. Tudo "
                  "conta em dobro. Não reprocesse nada antes de isso ser resolvido."),
    }


def diagnosticar_contagio_por_descricao(
    sb, empresa: str, processo: str, dry_run: bool = True,
) -> dict:
    """Fase 67 — acha e desfaz o CONTÁGIO POR DESCRIÇÃO.

    O que é contágio: o humano corrigiu o rótulo de UM evento; o sistema
    guardou "esta `descricao_bruta` significa este rótulo" e passou a aplicar
    isso a todos os eventos futuros com a mesma frase (e, via prompt, com
    frases parecidas). Quando a descrição corrigida era uma ALUCINAÇÃO do VLM,
    o mapeamento é falso e envenena justamente a frase mais comum do dataset.

    COMO IDENTIFICAR, sem coluna nova: para cada par (descricao_bruta →
    rótulo) que um HUMANO criou, procuram-se os eventos que
      • têm a MESMA `descricao_bruta`,
      • terminaram com AQUELE rótulo,
      • e NÃO foram tocados por humano (origem != 'humano').

    ⚠️ Isso sozinho gera FALSO POSITIVO GRANDE, e foi medido: o par
    `monitorar_maquina ← "monitorando o ciclo da máquina"` casa a assinatura
    com 361 eventos, mas é o mapeamento NATURAL — a descrição levaria a esse
    rótulo sem mapa nenhum. Contá-lo como estrago esconderia a contaminação
    real dentro de um número dez vezes maior. (Com o corte aplicado, a
    contaminação de eventos PRINCIPAIS deu zero — os suspeitos eram todos
    registro de auditoria ou correção humana.)

    O CORTE que separa um do outro é temporal e determinístico: o par
    (descrição, rótulo) já existia ANTES da primeira correção humana daquela
    descrição? Se sim, o cluster chegava lá sozinho — é natural. Se o par só
    aparece DEPOIS, ele nasceu do mapa. Bate com o que os dados mostraram: a
    contaminação de `posto_vazio` e `conversando_colega` começa em 29/07,
    enquanto `monitorar_maquina` está lá desde o primeiro dia.

    O QUE A REVERSÃO FAZ E O QUE NÃO FAZ: devolve o evento à fila
    (`validado_humano=false`, `validacao_correto=null`, `validado_em=null`) e
    limpa `label_corrigido`. NÃO recupera o rótulo original: quando o remap
    aconteceu na clusterização, o rótulo pré-remap nunca foi gravado. Só
    reprocessar o vídeo o traria de volta — o que a limpeza garante é que
    nenhum desses eventos continue passando por verdade.

    NUNCA toca em `origem_validacao='humano'`: a decisão da pessoa é
    inviolável, inclusive as correções que originaram o contágio.
    """
    def _fab():
        return (
            sb.table("eventos")
            .select("id, comportamento_label, label_corrigido, descricao_bruta, "
                    "tempo_inicio_s, tempo_fim_s, origem_validacao, "
                    "validado_humano, validacao_correto, principal, "
                    # `criado_em` e `validado_em` são o que permite o corte
                    # temporal entre mapeamento natural e contágio.
                    "criado_em, validado_em")
            .eq("empresa", empresa).eq("processo", processo)
            .order("id")
        )
    try:
        eventos = _scan_todos(_fab)
    except Exception as e:  # noqa: BLE001
        return {"erro": f"leitura de eventos falhou: {e}"}

    # 1) O mapa que o humano criou sem querer: descrição → rótulo corrigido.
    #    Guarda também QUANDO a primeira correção daquele par aconteceu — é o
    #    marco que separa mapeamento natural de contágio.
    mapa_humano: dict[str, Counter] = {}
    desde: dict[tuple, str] = {}
    for e in eventos:
        if (e.get("origem_validacao") or "") != "humano":
            continue
        corr = e.get("label_corrigido")
        desc = (e.get("descricao_bruta") or "").strip().lower()
        if corr and desc and corr != e.get("comportamento_label"):
            mapa_humano.setdefault(desc, Counter())[corr] += 1
            quando = e.get("validado_em") or e.get("criado_em")
            if quando:
                atual = desde.get((desc, corr))
                if atual is None or str(quando) < str(atual):
                    desde[(desc, corr)] = str(quando)

    # 1b) O par já existia ANTES da correção? Então o cluster chegava lá
    #     sozinho e nada disso é contágio. Um único evento anterior basta:
    #     mapeamento natural não precisa de maioria para ser natural.
    natural: set = set()
    for e in eventos:
        # A PRÓPRIA correção não serve de prova. Ela nasce com
        # `label_corrigido = Y` e `criado_em` anterior ao `validado_em`, então
        # sem este corte ela se auto-atestava como "o par já existia" e o
        # contágio inteiro virava mapeamento natural.
        if (e.get("origem_validacao") or "") == "humano":
            continue
        desc = (e.get("descricao_bruta") or "").strip().lower()
        label_ef = e.get("label_corrigido") or e.get("comportamento_label")
        marco = desde.get((desc, label_ef))
        if not marco:
            continue
        criado = e.get("criado_em")
        if criado and str(criado) < marco:
            natural.add((desc, label_ef))

    if not mapa_humano:
        return {"dry_run": dry_run, "correcoes_humanas": 0, "contaminados": 0,
                "passando_por_verdade": 0, "revertidos": 0, "minutos": 0.0,
                "por_descricao": []}

    # 2) Quem herdou esse mapa sem ninguém ter pedido.
    contagio: dict[str, dict] = {}
    naturais: dict[tuple, int] = {}
    alvos: list[dict] = []
    for e in eventos:
        if (e.get("origem_validacao") or "") == "humano":
            continue                       # decisão da pessoa: intocável
        if e.get("principal") is False:
            continue                       # auditoria não conta
        desc = (e.get("descricao_bruta") or "").strip().lower()
        ctr = mapa_humano.get(desc)
        if not ctr:
            continue
        label_ef = e.get("label_corrigido") or e.get("comportamento_label")
        if label_ef not in ctr:
            continue                       # ficou com outro rótulo: não é contágio
        if (desc, label_ef) in natural:
            naturais[(desc, label_ef)] = naturais.get((desc, label_ef), 0) + 1
            continue                       # o cluster chegava lá sem o mapa
        dur = max(0.0, float(e.get("tempo_fim_s") or 0) - float(e.get("tempo_inicio_s") or 0))
        d = contagio.setdefault(desc, {
            "descricao": e.get("descricao_bruta"), "rotulo": label_ef,
            "eventos": 0, "segundos": 0.0, "passando_por_verdade": 0,
            "corrigido_pelo_humano_n": sum(ctr.values()),
        })
        d["eventos"] += 1
        d["segundos"] += dur
        # SÓ escrevemos em quem ainda está PASSANDO POR VERDADE. Um evento
        # contaminado que já está na fila não precisa de reversão — o rótulo
        # dele continua errado, mas ninguém o está tomando como julgado, e só
        # reprocessar o vídeo corrigiria o rótulo. Separar as duas coisas é o
        # que torna a limpeza idempotente e o relatório honesto: `contaminados`
        # mede o estrago, `passando_por_verdade` mede o que dá para desfazer.
        if e.get("validado_humano"):
            d["passando_por_verdade"] += 1
            alvos.append(e)

    revertidos = 0
    if not dry_run and alvos:
        CH = 100
        ids = [e["id"] for e in alvos if e.get("id")]
        for i in range(0, len(ids), CH):
            try:
                (
                    sb.table("eventos")
                    .update({"validado_humano": False, "validacao_correto": None,
                             "validado_em": None, "label_corrigido": None})
                    .in_("id", ids[i : i + CH]).execute()
                )
                revertidos += len(ids[i : i + CH])
            except Exception as e:  # noqa: BLE001
                log.warning(f"[fase67] falha ao reverter lote {i}: {e}")

    return {
        "dry_run": dry_run,
        "correcoes_humanas": len(mapa_humano),
        # Todo evento que herdou o rótulo pela descrição (o estrago).
        "contaminados": sum(d["eventos"] for d in contagio.values()),
        # Destes, os que ainda constam como julgados (o que dá para desfazer).
        "passando_por_verdade": len(alvos),
        "revertidos": revertidos,
        "minutos": round(sum(d["segundos"] for d in contagio.values()) / 60, 1),
        # Pares que casam a assinatura mas são MAPEAMENTO NATURAL: o cluster
        # chegava neles antes de existir qualquer correção. Ficam à vista para
        # o número poder ser conferido, e NUNCA são escritos.
        "naturais": sorted(
            ({"descricao": d, "rotulo": r, "eventos": n}
             for (d, r), n in naturais.items()), key=lambda x: -x["eventos"]),
        "por_descricao": sorted(
            ({"descricao": d["descricao"], "rotulo": d["rotulo"],
              "eventos": d["eventos"], "minutos": round(d["segundos"] / 60, 1),
              "passando_por_verdade": d["passando_por_verdade"],
              "corrigido_pelo_humano_n": d["corrigido_pelo_humano_n"]}
             for d in contagio.values()),
            key=lambda x: -x["minutos"],
        ),
        "aviso": ("O rótulo ORIGINAL não é recuperável: quando o remap aconteceu "
                  "na clusterização, o rótulo pré-remap nunca foi gravado. Estes "
                  "eventos voltam para a fila para serem julgados de novo."),
    }


def reverter_auto_validacao_maquina(
    sb,
    empresa: str,
    processo: str,
    origens: tuple = ("correcao_aprendida", "vocabulario_canonico"),
    dry_run: bool = True,
) -> dict:
    """Fase 61 — devolve à fila os eventos que a MÁQUINA marcou como validados.

    Zera `validado_humano`, `validacao_correto` e `validado_em` dos eventos cuja
    `origem_validacao` está em `origens`. NÃO mexe em:
      • `origem_validacao='humano'`  — a decisão da pessoa é inviolável;
      • `origem_validacao='auditoria'` — secundários marcados de propósito para
        ficar fora da fila (validado_humano=True é o mecanismo disso);
      • `origem_validacao='posto_vazio'` — determinístico, sem VLM, e também
        depende de validado_humano=True para não poluir a fila.

    A `origem_validacao` é PRESERVADA: ela deixa de significar "validado" e
    passa a significar "rótulo proposto por", que é a informação útil para quem
    vai julgar o evento na fila.

    IDEMPOTENTE: na segunda passada o filtro `validado_humano=true` já não casa
    com nada. `dry_run=True` (default) só conta.
    """
    alvo = [o for o in origens if o not in ("humano", "auditoria", "posto_vazio")]
    if not alvo:
        return {"erro": "nenhuma origem elegível — 'humano'/'auditoria'/'posto_vazio' são protegidas."}

    achados: list[dict] = []
    for origem in alvo:
        def _fab(_o=origem):
            return (
                sb.table("eventos")
                .select("id, comportamento_label, label_corrigido, tempo_inicio_s, "
                        "tempo_fim_s, origem_validacao, validacao_correto")
                .eq("empresa", empresa)
                .eq("processo", processo)
                .eq("origem_validacao", _o)
                .eq("validado_humano", True)
                .order("id")
            )
        try:
            achados.extend(_scan_todos(_fab))
        except Exception as e:
            return {"erro": f"leitura de eventos falhou ({origem}): {e}"}

    por_rotulo: Counter = Counter()
    minutos = 0.0
    for e in achados:
        rot = e.get("label_corrigido") or e.get("comportamento_label") or "—"
        por_rotulo[rot] += 1
        dur = (e.get("tempo_fim_s") or 0) - (e.get("tempo_inicio_s") or 0)
        minutos += max(0.0, float(dur)) / 60.0

    revertidos = 0
    if not dry_run and achados:
        CH = 100
        ids = [e["id"] for e in achados if e.get("id")]
        for i in range(0, len(ids), CH):
            try:
                (
                    sb.table("eventos")
                    .update({
                        "validado_humano": False,
                        "validacao_correto": None,
                        "validado_em": None,
                    })
                    .in_("id", ids[i : i + CH])
                    .execute()
                )
                revertidos += len(ids[i : i + CH])
            except Exception as e:
                log.warning(f"[fase61] falha ao reverter lote {i}: {e}")

    return {
        "dry_run": dry_run,
        "origens": alvo,
        "encontrados": len(achados),
        "revertidos": revertidos,
        "minutos_devolvidos_a_fila": round(minutos, 1),
        "por_rotulo": [
            {"rotulo": r, "eventos": n} for r, n in por_rotulo.most_common(30)
        ],
    }


def relatorio_propagacao_lean(
    sb: Client, empresa: str, processo: str, *, dry_run: bool = True,
) -> dict:
    """Fase 55 — backfill idempotente + diagnóstico do tempo cinza.

    `dry_run=True` calcula e devolve o relatório SEM escrever nada.

    Devolve duas coisas bem diferentes, e a distinção importa:
      • `propagacao`  — eventos cuja categoria JÁ EXISTE no comportamento e só
        não desceu. É o que este backfill resolve.
      • `cinza_real`  — rótulos com tempo observado cujo COMPORTAMENTO está sem
        categoria. Esses NÃO são resolvidos por propagação nenhuma: alguém (IA
        ou gestor) precisa classificar o comportamento. É daqui que sai o
        "não classificado" do dashboard.
    """
    def _fab_ev():
        return (
            sb.table("eventos")
            .select("id, comportamento_label, label_corrigido, tempo_inicio_s, "
                    "tempo_fim_s, categoria_lean, categoria_lean_origem, principal, "
                    "validacao_correto")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .order("id")
        )

    try:
        eventos = _scan_todos(_fab_ev)
    except Exception as e:
        return {"erro": f"leitura de eventos falhou: {e}"}
    try:
        comps = varrer(sb, "comportamentos", "label, categoria_lean, categoria_lean_origem",
                       empresa=empresa, processo=processo)
    except Exception as e:
        return {"erro": f"leitura de comportamentos falhou: {e}"}
    cat_por_label = {c["label"]: c.get("categoria_lean") for c in comps}

    def _dur(e):
        return max(0.0, float(e.get("tempo_fim_s") or 0) - float(e.get("tempo_inicio_s") or 0))

    elegiveis: dict = {}                 # label → {n, seg, categoria}
    fora = {"override_humano": {"n": 0, "seg": 0.0},
            "ja_classificado": {"n": 0, "seg": 0.0},
            "sem_categoria_no_comportamento": {"n": 0, "seg": 0.0},
            "rotulo_sem_comportamento": {"n": 0, "seg": 0.0}}
    cinza: dict = {}                     # label → {n, seg}

    for e in eventos:
        label = e.get("label_corrigido") or e.get("comportamento_label") or "?"
        seg = _dur(e)
        cat_comp = cat_por_label.get(label)
        origem_ev = e.get("categoria_lean_origem")

        if not cat_comp:
            # A resposta NÃO está no banco: é cinza de verdade.
            chave = ("sem_categoria_no_comportamento" if label in cat_por_label
                     else "rotulo_sem_comportamento")
            fora[chave]["n"] += 1
            fora[chave]["seg"] += seg
            # Só conta no diagnóstico o que pesa no dashboard (principal, não refutado).
            if e.get("principal") is not False and e.get("validacao_correto") is not False:
                c = cinza.setdefault(label, {"n": 0, "seg": 0.0})
                c["n"] += 1
                c["seg"] += seg
            continue

        if origem_ev == "humano":
            fora["override_humano"]["n"] += 1
            fora["override_humano"]["seg"] += seg
            continue
        # PRECEDÊNCIA: elegível = sem categoria OU já herdado.
        if e.get("categoria_lean") and origem_ev != "herdado":
            fora["ja_classificado"]["n"] += 1
            fora["ja_classificado"]["seg"] += seg
            continue
        if e.get("categoria_lean") == cat_comp and origem_ev == "herdado":
            continue                     # já está certo → idempotência
        d = elegiveis.setdefault(label, {"n": 0, "seg": 0.0, "categoria": cat_comp})
        d["n"] += 1
        d["seg"] += seg

    escritos = 0
    if not dry_run:
        for label, d in elegiveis.items():
            escritos += propagar_categoria_para_eventos(
                sb, empresa, processo, label, d["categoria"])

    def _fmt(m: dict) -> list:
        return sorted(
            ({"label": k, "eventos": v["n"], "minutos": round(v["seg"] / 60, 1),
              **({"categoria": v["categoria"]} if "categoria" in v else {})}
             for k, v in m.items()),
            key=lambda x: -x["minutos"],
        )

    return {
        "dry_run": dry_run,
        "processo": processo,
        "propagacao": {
            "por_rotulo": _fmt(elegiveis),
            "eventos": sum(v["n"] for v in elegiveis.values()),
            "minutos": round(sum(v["seg"] for v in elegiveis.values()) / 60, 1),
            "escritos": escritos,
        },
        "fora": {k: {"eventos": v["n"], "minutos": round(v["seg"] / 60, 1)}
                 for k, v in fora.items()},
        # ⚠️ O que REALMENTE vira "não classificado" no dashboard — a propagação
        # não resolve isto; precisa alguém classificar o COMPORTAMENTO.
        "cinza_real": {
            "por_rotulo": _fmt(cinza),
            "minutos": round(sum(v["seg"] for v in cinza.values()) / 60, 1),
        },
    }


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
        humanos = varrer(
            sb, "comportamentos", "label, categoria_lean, categoria_lean_origem, processo",
            empresa=empresa, 
            ajustes=lambda q: q.eq("categoria_lean_origem", "humano"),
        )
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
        f"(VA:{len(exemplos.get('valor_agregado', []))}, "
        f"Desp:{len(exemplos.get('desperdicio', []))})"
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
        "valor_agregado": "VALOR AGREGADO / PRODUTIVO (o cliente considera estas como valor):",
        "desperdicio": "NÃO-PRODUTIVO / DESPERDÍCIO (o cliente considera estas como não-produtivas):",
    }
    for cat in ("valor_agregado", "desperdicio"):
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

Classifique CADA comportamento abaixo de forma BINÁRIA — em UMA destas DUAS categorias:
- "valor_agregado": a atividade AGREGA VALOR — transforma o produto/serviço de modo que o cliente final pagaria por ela (ex.: usinar/operar a peça no torno, montar, soldar, embalar a peça que será entregue, executar o serviço contratado).
- "desperdicio": a atividade NÃO agrega valor ao cliente. Inclui espera, ociosidade, deslocamento, retrabalho, movimentação excessiva, busca por itens E TAMBÉM as atividades de APOIO que, por si só, não transformam o produto (conferir, registrar, organizar, abastecer, preparar máquina, comunicar).

{bloco_dominio}{bloco_categoria}REGRAS:
- Decisão BINÁRIA e sem meio-termo: ou a ação agrega valor DIRETO ao produto/serviço ("valor_agregado"), ou não ("desperdicio"). Não existe categoria intermediária de "apoio".
- Só use "valor_agregado" quando a ação de fato TRANSFORMA o produto/executa o serviço. Na dúvida entre apoiar e agregar valor, classifique como "desperdicio".
- Comportamentos como "andar", "esperar", "ocioso", "parado", "buscar", "operar_computador" (registro/conferência), "preparar", "abastecer" tendem a "desperdicio", A MENOS que a descrição do processo deixe claro que aquilo É o trabalho que agrega valor.
- PRIORIDADE: se o "critério de categoria deste cliente" (acima) cobre o caso, alinhe a essa decisão — esse é o critério REAL do cliente.
- Responda APENAS um JSON estrito (categoria SEM espaços, snake_case):
{{"classificacoes": [{{"label": "operar_torno", "categoria": "valor_agregado"}}, ...]}}

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
        # Fase 28: posto_vazio é regra DURA — posto parado = espera/desperdício
        # no racional Lean do posto. Sem LLM; o gestor pode reclassificar pela
        # UI (vira origem='humano' e nunca mais é tocado).
        if c.get("label") == POSTO_VAZIO_LABEL:
            if c.get("categoria_lean") != "desperdicio":
                try:
                    sb.table("comportamentos").update(
                        {"categoria_lean": "desperdicio", "categoria_lean_origem": "ia"}
                    ).eq("id", c["id"]).execute()
                    propagar_categoria_para_eventos(
                        sb, empresa, processo, POSTO_VAZIO_LABEL, "desperdicio")
                except Exception as e:
                    log.warning(f"Lean: posto_vazio não atualizado: {e}")
            continue
        # Fase 63: a ação que a visão não nomeou DEIXA de ficar sem categoria.
        # Ela recebe não-produtivo pela convenção Lean (sem prova de que agrega
        # valor, não agrega) com origem 'fallback' — que é o que a manda para a
        # fila de dúvidas. Antes ela virava tempo cinza: sem categoria, sem
        # dono e sem prazo para alguém olhar.
        # Sem LLM: não há descrição para o modelo classificar, é justamente o
        # caso em que a visão não conseguiu nomear nada.
        if c.get("label") == LABEL_INDEFINIDA:
            if (c.get("categoria_lean") != CATEGORIA_SEM_EVIDENCIA
                    or c.get("categoria_lean_origem") != ORIGEM_SEM_EVIDENCIA):
                try:
                    sb.table("comportamentos").update(
                        {"categoria_lean": CATEGORIA_SEM_EVIDENCIA,
                         "categoria_lean_origem": ORIGEM_SEM_EVIDENCIA}
                    ).eq("id", c["id"]).execute()
                    propagar_categoria_para_eventos(
                        sb, empresa, processo, LABEL_INDEFINIDA, CATEGORIA_SEM_EVIDENCIA)
                except Exception as e:
                    log.warning(f"Lean: acao_indefinida não marcada: {e}")
            continue
        # 'aprendido' e 'ia' são candidatos a refinamento se reclassificar_ia
        if c.get("categoria_lean") and origem in ("ia", "aprendido") and not reclassificar_ia:
            continue
        candidatos.append(c)

    if not candidatos:
        return 0

    # ─── Nível 1: match exato pela memória humana (escopo empresa) ────
    mem_cat = carregar_memoria_categoria(sb, empresa, processo)
    mapa_humano: dict[str, str] = mem_cat.get("mapa_humano") or {}
    # Fase 62: o nível 1 pega a decisão Lean que a pessoa tomou em OUTRO
    # processo e a aplica aqui — é generalização automática, mesmo formato do
    # incidente. Com a chave desligada, ele não roda; o nível 2 (LLM) continua,
    # porque classificar é o trabalho do sistema e a saída sai marcada 'ia',
    # sem se passar por decisão humana.
    if not aprendizado_automatico(sb, empresa, processo):
        if mapa_humano:
            log.info(
                "Lean: generalização DESLIGADA — %d precedente(s) humano(s) de "
                "outros processos não aplicados.", len(mapa_humano),
            )
        mapa_humano = {}

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
                # Fase 55: desce na hora para os eventos já existentes deste
                # rótulo — sem isto, o cinza volta a se acumular a cada rodada.
                propagar_categoria_para_eventos(sb, empresa, processo, lbl, cat)
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
        classifs = []

    por_label = {c["label"]: c["id"] for c in para_llm}
    atualizados_ia = 0
    decididos: set = set()      # quem o LLM de fato classificou
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
            # Fase 55: a IA classifica DEPOIS dos eventos existirem — era aqui
            # que os 336 eventos de `operar_torno` ficavam para trás.
            propagar_categoria_para_eventos(sb, empresa, processo, label, cat)
        except Exception as e:
            log.warning(f"Lean: falha ao atualizar {label}: {e}")
        else:
            decididos.add(label)

    # ─── Fase 63: FECHAMENTO. Ninguém sai daqui sem categoria ──────────
    # O que o LLM não classificou (recusou, devolveu categoria inválida, ou a
    # chamada falhou inteira) recebe não-produtivo com origem 'fallback'. Não é
    # um chute disfarçado de decisão: 'fallback' é o que joga o rótulo para a
    # fila de dúvidas e o mantém lá até alguém julgar. O cinza sumia da vista;
    # isto não some.
    assumidos = 0
    for c in para_llm:
        lbl = c.get("label") or ""
        if lbl in decididos:
            continue
        try:
            sb.table("comportamentos").update(
                {"categoria_lean": CATEGORIA_SEM_EVIDENCIA,
                 "categoria_lean_origem": ORIGEM_SEM_EVIDENCIA}
            ).eq("id", c["id"]).execute()
            propagar_categoria_para_eventos(
                sb, empresa, processo, lbl, CATEGORIA_SEM_EVIDENCIA)
            assumidos += 1
        except Exception as e:
            log.warning(f"Lean: falha ao assumir categoria de {lbl}: {e}")
    if assumidos:
        log.info(
            "Lean: %d comportamento(s) SEM evidência → não-produtivo por convenção; "
            "vão para a fila de dúvidas.", assumidos,
        )

    log.info(
        f"Lean: {empresa}/{processo} · {aprendidos} aprendidos (match humano) "
        f"+ {atualizados_ia} via IA + {assumidos} assumidos (de {len(para_llm)} candidatos novos)."
    )
    return aprendidos + atualizados_ia + assumidos


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
    (d) Distinguir o que AGREGA VALOR do que NÃO agrega (preparação / verificação / espera / deslocamento).
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
        evs = varrer(
            sb, "eventos",
            "id, video_id, comportamento_label, label_corrigido, "
            "tempo_inicio_s, tempo_fim_s, confianca, validado_humano, "
            "validacao_correto, criado_em",
            empresa=empresa, processo=processo,
            ajustes=lambda q: (q.is_("validado_humano", "null").gte("criado_em", corte)),
        )
        if not evs:
            return 0

        _anexar_meta_video(evs, sb)
        grupos, _ = agrupar_eventos_multicamera(evs)
        if not grupos:
            return 0

        # Coleta pares (A↔B) divergentes — uma signatura por par único.
        existentes = varrer(sb, "perguntas_processo", "id, pergunta",
                            empresa=empresa, processo=processo)
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

    # Fase 82: `bbox_inicio` agora pode ser NULL (posto vazio, ponte temporal —
    # e todo evento antigo cujo zero foi corrigido). Cai no mesmo caminho que o
    # bbox degenerado já tinha: frame inteiro, sem retângulo desenhado.
    bbox = evento.get("bbox_inicio")
    if isinstance(bbox, dict):
        x1, y1, x2, y2 = int(bbox["x1"]), int(bbox["y1"]), int(bbox["x2"]), int(bbox["y2"])
    elif bbox:
        x1, y1, x2, y2 = (int(v) for v in bbox)
    else:
        x1 = y1 = x2 = y2 = 0

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
        # Fase 28: bbox degenerado (posto_vazio, track -1) → frame inteiro sem
        # anotação (não há pessoa a destacar).
        bbox_valido = (x2 - x1) > 1 and (y2 - y1) > 1
        # Anotação PROPORCIONAL ao tamanho do frame — assim a caixa não vira um
        # "blob" verde em frames pequenos nem some em frames grandes.
        if bbox_valido:
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


# ═════════════════════════════════════════════════════════════════════════
# Fase 74 — TAMANHO DO FRAME DE VALIDAÇÃO.
#
# Medido em produção: 4605 frames × ~71 KB = 324,8 MB, um terço do free tier.
# Isso é qualidade de arquivo para uma miniatura de 180px de altura numa faixa
# de três — o olho humano não usa nada disso para dizer "o operador está no
# torno". Dois cortes, ambos regulados por env:
#   • LARGURA: o frame era gravado na resolução do vídeo (tipicamente 1280).
#     640 é mais que suficiente para a faixa e corta a área em 4×.
#   • QUALIDADE: 85 → 60. Abaixo de ~50 começam a aparecer artefatos de bloco
#     que atrapalham julgar mão/ferramenta, então 60 é o piso confortável.
#
# Só afeta frames NOVOS. Os antigos continuam servindo do cache como estão —
# reprocessá-los custaria egress e não devolveria nada além de espaço.
FRAME_QUALIDADE = int(os.environ.get("KV_FRAME_QUALIDADE", "60"))
FRAME_MAX_W = int(os.environ.get("KV_FRAME_MAX_W", "640"))


def frame_para_jpeg_bytes(frame_bgr: np.ndarray, qualidade: int | None = None,
                          max_w: int | None = None) -> bytes:
    """JPEG da miniatura de validação. Reduz para `max_w` antes de codificar:
    diminuir a resolução economiza muito mais que baixar a qualidade, e sem os
    artefatos que a compressão agressiva traz."""
    q = FRAME_QUALIDADE if qualidade is None else qualidade
    largura_max = FRAME_MAX_W if max_w is None else max_w
    try:
        h, w = frame_bgr.shape[:2]
        if largura_max > 0 and w > largura_max:
            escala = largura_max / float(w)
            frame_bgr = cv2.resize(
                frame_bgr, (largura_max, max(1, int(round(h * escala)))),
                interpolation=cv2.INTER_AREA)   # INTER_AREA é o certo p/ reduzir
    except Exception as e:  # noqa: BLE001
        log.warning("[frames] redimensionamento falhou (%s) — mantendo original.", e)
    ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, q])
    assert ok
    return buf.tobytes()


# ═════════════════════════════════════════════════════════════════════════
# Fase 54 — CACHE DE FRAMES + EXPIRAÇÃO DO VÍDEO
#
# A campanha de 30 dias roda no free tier do Supabase (1GB de Storage). O vídeo
# nunca era apagado → o bucket estourava em ~2 dias e a coleta morria.
#
# Não dá pra simplesmente apagar: a tela de eventos extraía os frames de forma
# PREGUIÇOSA, baixando o vídeo inteiro no 1º acesso. A ordem certa é inverter —
# AQUECER o cache no fim do processamento (o vídeo ainda está no disco local do
# worker, egress adicional = ZERO) e só então apagar o binário.
#
# ⚠️ A limpeza SEMPRE opera sobre caminhos REGISTRADOS no banco (videos.caminho
# / segmentos.storage_path). NUNCA listar o bucket e apagar por prefixo: os
# JPEGs de __frames/ moram no mesmo bucket e são a evidência PERMANENTE — sem
# o vídeo de origem, não há como regenerá-los.
# ═════════════════════════════════════════════════════════════════════════
# Versão do formato dos frames. FONTE ÚNICA: quem grava (pré-extração) e quem lê
# (GET /eventos/{id}/frames) importam esta constante. Hardcodar "v2" nos dois
# lados faria o cache silenciosamente deixar de casar no dia em que mudasse.
FRAMES_VER = "v2"

# 0 = apaga o binário assim que o processamento confirma; >0 = o vídeo sobrevive
# N horas e a varredura o remove depois. Começa em 0 p/ caber no free tier.
RETER_VIDEO_HORAS = float(os.environ.get("KV_RETER_VIDEO_HORAS", "0"))


def _prefixo_frames(caminho: str) -> str:
    """__frames/ ao lado do objeto — mesmo prefixo usado pelos endpoints."""
    import posixpath
    return posixpath.dirname(caminho) + "/__frames"


def chave_frame_evento(caminho: str, evento_id: str, k: int) -> str:
    """Chave do k-ésimo frame de um evento. Usada nos DOIS caminhos."""
    return f"{_prefixo_frames(caminho)}/{evento_id}_{FRAMES_VER}_{k}.jpg"


def chave_frame_segmento(caminho_seg: str, segmento_id: str,
                         ini_s: float, fim_s: float, k: int) -> str:
    """Chave do k-ésimo frame de uma JANELA de tempo do segmento (2º ângulo).
    O arredondamento tem que ser idêntico ao do endpoint, senão o pré-aquecido
    nunca é encontrado."""
    chave_t = f"{int(round(ini_s))}_{int(round(fim_s))}"
    return f"{_prefixo_frames(caminho_seg)}/seg_{segmento_id}_{chave_t}_{k}.jpg"


def _nomes_no_prefixo(sb: Client, bucket: str, prefixo: str) -> set:
    """Nomes já existentes no prefixo — UMA chamada de METADADOS (não baixa
    arquivo, logo não gera egress). Serve à idempotência: chave que já existe
    não é reescrita."""
    try:
        itens = sb.storage.from_(bucket).list(prefixo) or []
        return {i.get("name") for i in itens if i.get("name")}
    except Exception:
        return set()          # sem listagem, seguimos gravando (upsert)


def _tamanho_objeto(sb: Client, bucket: str, caminho: str) -> float:
    """Tamanho em MB pelo METADADO da listagem (sem baixar). 0 se indisponível."""
    import posixpath
    try:
        pasta, nome = posixpath.dirname(caminho), posixpath.basename(caminho)
        for i in (sb.storage.from_(bucket).list(pasta) or []):
            if i.get("name") == nome:
                return float(((i.get("metadata") or {}).get("size") or 0)) / 1e6
    except Exception:
        pass
    return 0.0


def pre_extrair_frames(
    sb: Client,
    caminho_storage: str | None,
    video_path_local: str,
    eventos: list[dict],
    ids_eventos: list,
    video_id: str,
    cam_id: str | None,
    *,
    video_path_sec: str | None = None,
    storage_path_sec: str | None = None,
    segmento_id_sec: str | None = None,
    cam_id_sec: str | None = None,
    offset_s: float = 0.0,
) -> dict:
    """Fase 36 — pré-gera TODOS os JPEGs de visualização enquanto os vídeos
    ainda estão no DISCO LOCAL do worker, com as MESMAS chaves de cache dos
    endpoints de frames. Antes, o 1º acesso a cada evento baixava o vídeo
    INTEIRO do Storage (20-40MB) só para extrair 3 frames — a maior fonte de
    egress. Agora visualizar custa só os JPEGs (~50KB). Uploads são ingress
    (grátis). Tudo não-fatal. KV_PREEXTRAIR_FRAMES=off desliga."""
    import posixpath

    stats = {"eventos": 0, "cam2": 0, "refs": 0, "falhas": 0, "pulados": 0}
    if os.environ.get("KV_PREEXTRAIR_FRAMES", "on") in ("off", "0", "false", "False"):
        return stats
    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")

    import posixpath as _pp
    _ja_tem: dict = {}          # prefixo → nomes existentes (1 listagem por prefixo)

    def _up(key: str, jpeg: bytes) -> None:
        # Idempotente: chave que já existe NÃO é reescrita (reprocessar um vídeo
        # não deve gastar ingress nem invalidar o que a tela já mostra).
        pasta, nome = _pp.dirname(key), _pp.basename(key)
        if pasta not in _ja_tem:
            _ja_tem[pasta] = _nomes_no_prefixo(sb, bucket, pasta)
        if nome in _ja_tem[pasta]:
            stats["pulados"] += 1
            return
        try:
            sb.storage.from_(bucket).upload(
                key, jpeg, {"content-type": "image/jpeg", "upsert": "true"}
            )
            _ja_tem[pasta].add(nome)
        except Exception as e:
            stats["falhas"] += 1
            log.warning(f"[pre-frames] upload falhou {key}: {e}")

    # 1) Frames dos eventos PRINCIPAIS (chaves de GET /eventos/{id}/frames).
    if caminho_storage and not caminho_storage.startswith(("/", "\\")):
        prefix1 = posixpath.dirname(caminho_storage) + "/__frames"
        for eid, ev in zip(ids_eventos or [], eventos or []):
            if not eid:
                continue
            try:
                jpegs = [frame_para_jpeg_bytes(c)
                         for c in extrair_3_frames_evento(ev, video_path_local)]
                for k, j in enumerate(jpegs):
                    _up(chave_frame_evento(caminho_storage, eid, k), j)
                stats["eventos"] += 1
            except Exception as e:
                stats["falhas"] += 1
                log.warning(f"[pre-frames] evento {eid}: {e}")
        # Frame de referência da câmera primária (editor de zonas).
        if cam_id:
            try:
                cap = cv2.VideoCapture(video_path_local)
                try:
                    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                    if total > 1:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
                    ok, frame = cap.read()
                finally:
                    cap.release()
                if ok and frame is not None:
                    # Referência de ZONAS: resolução CHEIA de propósito —
                    # é sobre ela que o cliente desenha os polígonos, e
                    # reduzir tornaria as coordenadas imprecisas. São
                    # poucos objetos (1 por câmera/vídeo), não 3 por evento.
                    _up(f"{prefix1}/ref_{cam_id}_{video_id}.jpg",
                        frame_para_jpeg_bytes(frame, qualidade=85, max_w=0))
                    stats["refs"] += 1
            except Exception as e:
                log.warning(f"[pre-frames] ref {cam_id}: {e}")

    # 2) Strips da cam2 por janela dos eventos (chaves de GET /segmentos/{id}/
    #    frames, com o MESMO offset de relógio que o front soma — Fase 30).
    if video_path_sec and storage_path_sec and segmento_id_sec:
        prefix2 = posixpath.dirname(storage_path_sec) + "/__frames"
        for ev in eventos or []:
            try:
                ini_c = max(0.0, float(ev.get("tempo_inicio_s") or 0) + offset_s)
                fim_c = max(0.0, float(ev.get("tempo_fim_s") or 0) + offset_s)
                jpegs = [frame_para_jpeg_bytes(c)
                         for c in extrair_3_frames_tempo(video_path_sec, ini_c, fim_c)]
                for k, j in enumerate(jpegs):
                    _up(chave_frame_segmento(storage_path_sec, segmento_id_sec,
                                             ini_c, fim_c, k), j)
                stats["cam2"] += 1
            except Exception as e:
                stats["falhas"] += 1
                log.warning(f"[pre-frames] strip cam2: {e}")
        if cam_id_sec:
            try:
                cap = cv2.VideoCapture(video_path_sec)
                try:
                    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                    if total > 1:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
                    ok, frame = cap.read()
                finally:
                    cap.release()
                if ok and frame is not None:
                    # Mesma razão da cam1: referência de zonas em cheia.
                    _up(f"{prefix2}/ref_{cam_id_sec}_{segmento_id_sec}.jpg",
                        frame_para_jpeg_bytes(frame, qualidade=85, max_w=0))
                    stats["refs"] += 1
            except Exception as e:
                log.warning(f"[pre-frames] ref {cam_id_sec}: {e}")

    log.info(
        "[pre-frames] %d evento(s), %d strip(s) cam2, %d referência(s), "
        "%d já existiam, %d falha(s) — visualização servida do cache (egress ~zero)",
        stats["eventos"], stats["cam2"], stats["refs"], stats["pulados"], stats["falhas"],
    )
    # `ok` é o portão da remoção do binário: UMA falha de upload já basta para
    # segurar o vídeo. Bucket que cresce é problema visível; evento sem
    # evidência é dado perdido e irrecuperável.
    stats["ok"] = stats["falhas"] == 0
    return stats


def _remover_objeto(sb: Client, bucket: str, caminho: str) -> float:
    """Apaga UM objeto pelo caminho REGISTRADO. Devolve os MB liberados
    (0 se já não existia). Nunca levanta."""
    mb = _tamanho_objeto(sb, bucket, caminho)
    try:
        sb.storage.from_(bucket).remove([caminho])
        return mb
    except Exception as e:
        log.warning(f"[retencao] falha ao remover {caminho}: {e}")
        return 0.0


def expirar_binarios_do_video(
    sb: Client,
    video_id: str,
    caminho_storage: str | None,
    *,
    frames_ok: bool,
    storage_path_sec: str | None = None,
    segmento_id_sec: str | None = None,
) -> dict:
    """Fase 54 — apaga os BINÁRIOS (vídeo da cam1 + segmento da cam2) depois do
    processamento confirmado, mantendo a LINHA em `videos` intacta.

    Só apaga com as três condições verdadeiras:
      1) o processamento chegou até aqui (sucesso, não parcial);
      2) o aquecimento do cache terminou SEM falha (`frames_ok`);
      3) as colunas de rastreio foram atualizadas.
    Qualquer uma falhando: não apaga, loga warning e segue.

    Com KV_RETER_VIDEO_HORAS > 0 apenas CARIMBA `frames_aquecidos_em` — quem
    apaga depois do prazo é a varredura (`varrer_videos_expirados`)."""
    resultado = {"removido": False, "mb": 0.0, "motivo": ""}
    if not caminho_storage or caminho_storage.startswith(("/", "\\")):
        resultado["motivo"] = "sem caminho no Storage (upload legado/local)"
        return resultado
    if not frames_ok:
        resultado["motivo"] = "cache de frames incompleto"
        log.warning("[retencao] vídeo %s MANTIDO: %s — sem os JPEGs, apagar o "
                    "binário destruiria a evidência.", video_id, resultado["motivo"])
        return resultado

    agora = datetime.now(timezone.utc).isoformat()
    try:
        sb.table("videos").update({"frames_aquecidos_em": agora}).eq("id", video_id).execute()
    except Exception as e:
        resultado["motivo"] = f"não consegui carimbar frames_aquecidos_em ({e})"
        log.warning("[retencao] vídeo %s MANTIDO: %s", video_id, resultado["motivo"])
        return resultado

    if RETER_VIDEO_HORAS > 0:
        resultado["motivo"] = f"retenção de {RETER_VIDEO_HORAS:.0f}h ativa"
        log.info("[retencao] vídeo %s fica no Storage por %.0fh (a varredura "
                 "remove depois).", video_id, RETER_VIDEO_HORAS)
        return resultado

    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")
    mb = _remover_objeto(sb, bucket, caminho_storage)
    # O 2º ângulo é outro objeto e NÃO tem linha em `videos` — sem removê-lo
    # aqui, metade do bucket continuaria crescendo num setup de 2 câmeras.
    if storage_path_sec:
        mb += _remover_objeto(sb, bucket, storage_path_sec)
    try:
        sb.table("videos").update({"video_removido_em": agora}).eq("id", video_id).execute()
    except Exception as e:
        log.warning("[retencao] binário apagado mas video_removido_em não gravou "
                    "(%s) — a varredura reconcilia depois.", e)
    if segmento_id_sec:
        try:
            sb.table("segmentos").update({"storage_removido_em": agora}).eq(
                "id", segmento_id_sec).execute()
        except Exception:
            pass          # coluna opcional; a evidência já está no cache
    resultado.update({"removido": True, "mb": round(mb, 2), "motivo": "ok"})
    log.info("[retencao] vídeo %s: binário removido (%.1f MB liberados) — os "
             "frames seguem no cache.", video_id, mb)
    return resultado


def varrer_videos_expirados(sb: Client, empresa: str | None = None,
                            limite: int = 500, dry_run: bool = False) -> dict:
    """Fase 54 — rede de segurança: acha vídeos JÁ processados, com frames
    aquecidos e binário ainda no Storage, e os apaga. Seguro rodar N vezes
    (a 2ª passada não encontra nada, porque `video_removido_em` já está
    preenchido). Respeita KV_RETER_VIDEO_HORAS.

    Fase 74 — VARRE TAMBÉM A CAM2. Este era o furo que estourou o bucket: o
    segmento da cam2 é outro objeto e NÃO tem linha em `videos`.
    `expirar_binarios_do_video` o apagava inline, mas só no caminho
    `RETER_VIDEO_HORAS == 0`; com retenção ligada ele retorna cedo (só carimba)
    e a varredura, que só olhava `videos.caminho`, nunca o alcançava. Num setup
    de 2 câmeras isso significa METADE do bucket crescendo para sempre.

    `dry_run=True` conta e mede sem apagar nada.
    """
    bucket = os.environ.get("SUPABASE_BUCKET_VIDEOS", "videos")
    corte = datetime.now(timezone.utc) - timedelta(hours=max(0.0, RETER_VIDEO_HORAS))
    try:
        q = (
            sb.table("videos")
            .select("id, caminho, empresa, processado_em, frames_aquecidos_em")
            .is_("video_removido_em", "null")
            .not_.is_("frames_aquecidos_em", "null")
            .limit(limite)
        )
        if empresa:
            q = q.eq("empresa", empresa)
        candidatos = q.execute().data or []
    except Exception as e:
        log.warning(f"[varredura] leitura falhou: {e}")
        return {"apagados": 0, "mb": 0.0, "erro": str(e)}

    apagados, mb_total, pulados = 0, 0.0, 0
    ids_liberados: list[str] = []
    for v in candidatos:
        caminho = v.get("caminho")
        if not caminho or str(caminho).startswith(("/", "\\")):
            pulados += 1
            continue                      # upload legado com path local
        aquecido = _parse_iso_utc_pipe(v.get("frames_aquecidos_em"))
        if RETER_VIDEO_HORAS > 0 and aquecido and aquecido > corte:
            pulados += 1
            continue                      # ainda dentro da janela de retenção
        if dry_run:
            apagados += 1
            mb_total += _tamanho_objeto(sb, bucket, caminho)
            ids_liberados.append(v["id"])
            continue
        mb = _remover_objeto(sb, bucket, caminho)
        try:
            sb.table("videos").update({
                "video_removido_em": datetime.now(timezone.utc).isoformat()
            }).eq("id", v["id"]).execute()
            apagados += 1
            mb_total += mb
            ids_liberados.append(v["id"])
        except Exception as e:
            log.warning(f"[varredura] {v['id']}: removeu o objeto mas não carimbou ({e})")

    # ── CAM2: os segmentos do 2º ângulo, que não têm linha em `videos` ──
    cam2_apagados, cam2_mb, cam2_pulados = 0, 0.0, 0
    try:
        q2 = (
            sb.table("segmentos")
            .select("id, storage_path, empresa, processado_em, status")
            .is_("storage_removido_em", "null")
            .eq("status", "concluido")
            .limit(limite)
        )
        if empresa:
            q2 = q2.eq("empresa", empresa)
        segs = q2.execute().data or []
    except Exception as e:
        log.warning(f"[varredura] leitura de segmentos falhou: {e}")
        segs = []

    for sg in segs:
        cam = sg.get("storage_path")
        if not cam or str(cam).startswith(("/", "\\")):
            cam2_pulados += 1
            continue
        # A régua da cam2 é o `processado_em` DELA: o segmento só é marcado
        # 'concluido' depois de o par ter sido processado, então os frames já
        # foram aquecidos a partir dele.
        proc = _parse_iso_utc_pipe(sg.get("processado_em"))
        if RETER_VIDEO_HORAS > 0 and proc and proc > corte:
            cam2_pulados += 1
            continue
        if dry_run:
            cam2_apagados += 1
            cam2_mb += _tamanho_objeto(sb, bucket, cam)
            continue
        mb = _remover_objeto(sb, bucket, cam)
        try:
            sb.table("segmentos").update({
                "storage_removido_em": datetime.now(timezone.utc).isoformat()
            }).eq("id", sg["id"]).execute()
            cam2_apagados += 1
            cam2_mb += mb
        except Exception as e:
            log.warning(f"[varredura] segmento {sg['id']}: objeto removido mas "
                        f"não carimbou ({e})")

    log.info("[varredura]%s %d vídeo(s) + %d segmento(s) cam2 · %.1f MB · "
             "%d dentro da retenção/ignorados",
             " (DRY-RUN)" if dry_run else "", apagados, cam2_apagados,
             mb_total + cam2_mb, pulados + cam2_pulados)
    return {
        "dry_run": dry_run,
        "apagados": apagados,
        "mb": round(mb_total, 2),
        "pulados": pulados,
        "cam2_apagados": cam2_apagados,
        "cam2_mb": round(cam2_mb, 2),
        "cam2_pulados": cam2_pulados,
        "total_objetos": apagados + cam2_apagados,
        "total_mb": round(mb_total + cam2_mb, 2),
    }


def _parse_iso_utc_pipe(s):
    """ISO → datetime aware em UTC (None se não parsear)."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


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
        evs = varrer(
            sb, "eventos",
            "id, video_id, comportamento_label, label_corrigido, "
            "tempo_inicio_s, tempo_fim_s, validacao_correto, validado_humano, "
            "confianca, principal",
            empresa=empresa, processo=processo,
        )
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
        vids = varrer(sb, "videos", "id, duracao_s", empresa=empresa, processo=processo)
    else:
        vids = videos
    dur_total = sum((v.get("duracao_s") or 0) for v in vids)

    if comportamentos is None:
        comps = varrer(sb, "comportamentos", "label, descricao",
                       empresa=empresa, processo=processo)
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

    # Fase 20: % sobre o tempo de ATIVIDADE (soma dos eventos), não sobre a
    # duração bruta dos vídeos — é a MESMA régua da composição Lean e das
    # frases do dashboard ("30min de 1h06" = 45%, não 13%). A duração bruta
    # segue exposta em tempo_total_observado_min.
    total_atividade = sum(a["dur"] for a in agg.values())
    distrib = []
    for l, a in sorted(agg.items(), key=lambda kv: kv[1]["dur"], reverse=True):
        distrib.append(
            {
                "comportamento": l,
                "descricao": desc_por_label.get(l, l),
                "ocorrencias": a["oc"],
                "tempo_total_s": round(a["dur"], 1),
                "pct_tempo": round(a["dur"] / max(1.0, total_atividade) * 100, 1),
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
    if not _prism_chat_ativo():
        return PRISM_CHAT_EM_BREVE   # Fase 25: chat desativado — 0 token
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
    if not _prism_chat_ativo():
        return None   # Fase 25: chat desativado — sem LLM p/ título
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
    if not _prism_chat_ativo():
        return []   # Fase 25: chat desativado — sem sugestões, 0 token
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
def _scan_todos(fabrica, pagina: int = 1000) -> list[dict]:
    """Lê TODAS as linhas paginando por .range().

    O PostgREST trunca respostas no seu teto de linhas (`max-rows`, tipicamente
    1000) MESMO com `.limit()` alto — então uma varredura da empresa inteira
    voltava truncada e escondia os eventos de alguns processos (o card da home
    zerava enquanto o dashboard, escopado a 1 processo, ficava sob o teto e
    mostrava os números certos). `fabrica` deve devolver uma query NOVA a cada
    chamada, já com um `.order()` estável (chave única) p/ a paginação não
    pular nem repetir linhas."""
    linhas: list[dict] = []
    ini = 0
    while True:
        lote = fabrica().range(ini, ini + pagina - 1).execute().data or []
        linhas.extend(lote)
        if len(lote) < pagina:
            break
        ini += pagina
    return linhas


# Teto real do PostgREST. `.limit(50000)` NÃO é um pedido de 50 mil linhas: é um
# pedido que o servidor corta em `max-rows` sem avisar ninguém — sem erro, sem
# header, sem nada. A resposta truncada tem a mesma cara de uma resposta
# completa, e é por isso que este bug sobreviveu tanto tempo.
TETO_POSTGREST = 1000


def varrer(sb, tabela: str, colunas: str, *, empresa: str | None = None,
           processo: str | None = None, ordem: str = "id",
           ajustes=None) -> list[dict]:
    """Lê a tabela INTEIRA (paginando), com o filtro de empresa/processo.

    Fase 81 — por que toda leitura de tabela grande passa por aqui.
    O "Dia a dia" lia `eventos` com `.limit(100000)` e `videos` com
    `.limit(50000)`, acreditando estar lendo tudo. O PostgREST devolvia as
    primeiras 1000 linhas de cada um. Com a campanha rodando, os vídeos
    passaram de 1000: os que ficaram de fora não tinham instante de gravação
    conhecido, e TODOS os eventos deles foram descartados no laço
    (`dt0 is None: continue`). O resultado na tela era um dia gravado o dia
    inteiro aparecendo como algumas faixinhas soltas — e um dia que estava
    cheio ontem ganhando um buraco hoje, conforme o corte se desloca.

    Nenhuma leitura nova deve usar `.limit()` acima de TETO_POSTGREST; há teste
    varrendo o fonte para impedir a volta do padrão.
    """
    def _fab():
        q = sb.table(tabela).select(colunas)
        if empresa is not None:
            q = q.eq("empresa", empresa)
        if processo is not None:
            q = q.eq("processo", processo)
        if ajustes is not None:
            q = ajustes(q)
        return q.order(ordem)
    return _scan_todos(_fab, pagina=TETO_POSTGREST)


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

    def _fab_vid():
        q = (
            sb.table("videos")
            .select("processo, duracao_s, processado_em")
            .eq("empresa", empresa)
            .order("id")
        )
        return q.eq("processo", processo) if processo is not None else q
    videos = _scan_todos(_fab_vid)
    for v in videos:
        p = base.get(v.get("processo"))
        if not p:
            continue
        p["n_videos"] += 1
        p["tempo_total_s"] += v.get("duracao_s") or 0
        pe = v.get("processado_em")
        if pe and (p["ultimo_video_em"] is None or pe > p["ultimo_video_em"]):
            p["ultimo_video_em"] = pe

    def _fab_cmp():
        q = (
            sb.table("comportamentos")
            .select("processo, label, categoria_lean, categoria_lean_origem")
            .eq("empresa", empresa)
            .order("id")
        )
        return q.eq("processo", processo) if processo is not None else q
    comps = _scan_todos(_fab_cmp)
    cat_por_pl: dict[tuple, str | None] = {}
    orig_por_pl: dict[tuple, str | None] = {}
    for c in comps:
        cat_por_pl[(c.get("processo"), c.get("label"))] = c.get("categoria_lean")
        orig_por_pl[(c.get("processo"), c.get("label"))] = c.get("categoria_lean_origem")

    def _fab_ev():
        q = (
            sb.table("eventos")
            .select(
                "id, video_id, processo, comportamento_label, label_corrigido, "
                "tempo_inicio_s, tempo_fim_s, validacao_correto, validado_humano, "
                "origem_validacao, confianca, principal"
            )
            .eq("empresa", empresa)
            .order("id")
        )
        return q.eq("processo", processo) if processo is not None else q
    eventos = _scan_todos(_fab_ev)
    # Fase 16: preferir os PRINCIPAIS (1/min), deixando os crus de auditoria de
    # fora. Fallback (Fase 26): se um processo não tem NENHUM evento principal
    # (todos com principal=False), usar todos os eventos DELE — senão o card da
    # home zera (pendências/validado/valor agregado) enquanto o dashboard, que
    # não filtra por principal, mostra os números reais.
    _proc_com_principal = {
        e.get("processo") for e in eventos if e.get("principal") is not False
    }
    eventos = [
        e for e in eventos
        if e.get("principal") is not False
        or e.get("processo") not in _proc_com_principal
    ]

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

    def _fab_sug():
        q = (
            sb.table("sugestoes_melhoria")
            .select("processo, prioridade, status")
            .eq("empresa", empresa)
            .eq("status", "pendente")
            .order("id")
        )
        return q.eq("processo", processo) if processo is not None else q
    sugs = _scan_todos(_fab_sug)
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
        pads = varrer(sb, "padroes_processo", "id, processo, confianca",
                      empresa=empresa, processo=processo)
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
        perg = varrer(sb, "perguntas_processo", "id, processo, status",
                      empresa=empresa, processo=processo,
                      ajustes=lambda q: q.eq("status", "respondida"))
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
        # Fase 63: duas fatias, sempre. `n_sem_evidencia` continua sendo
        # contado — não para virar uma fatia cinza, mas para alimentar a
        # maturidade e a fila de dúvidas.
        soma_cat = {"valor_agregado": 0.0, "desperdicio": 0.0}
        n_comp_local = 0
        n_sem_evidencia = 0
        for lbl, d in agg.items():
            cat_bruta = cat_por_pl.get((n, lbl))
            n_comp_local += 1
            if not categoria_tem_evidencia(cat_bruta, orig_por_pl.get((n, lbl))):
                n_sem_evidencia += 1
            soma_cat[categoria_efetiva(cat_bruta)] += d["dur"]
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
        # Fase 63: com tudo classificado, medir 'preenchimento' daria 100%
        # sempre e viraria ponto grátis. A cobertura passa a medir
        # EVIDÊNCIA: quanto do catálogo foi decidido, não assumido.
        cobertura_lean = 1 - (n_sem_evidencia / max(1, n_comp_local))
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
    comp_cons = {"valor_agregado": 0.0, "desperdicio": 0.0}
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
    if not _prism_chat_ativo():
        return PRISM_CHAT_EM_BREVE   # Fase 25: chat desativado — 0 token
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
    if not _prism_chat_ativo():
        return []   # Fase 25: chat desativado — sem sugestões, 0 token
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
    if not _insights_globais_ativo():
        return 0   # Fase 25: visão global desativada — não gasta token nem apaga
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

# Fase 24: a análise de padrões (LLM) está DESATIVADA por padrão p/ cortar
# tokens (a tela de Padrões está "em breve"). Reative com KV_PADROES_ENABLE=on
# sem tocar em código — nenhum padrão já gravado é apagado quando desligada.
_PADROES_ENABLE = os.environ.get("KV_PADROES_ENABLE", "off") not in ("off", "0", "false", "False", "")
_padroes_desativado_logado = False


def _padroes_ativo() -> bool:
    global _padroes_desativado_logado
    if not _PADROES_ENABLE and not _padroes_desativado_logado:
        log.info("[padroes] análise de padrões DESATIVADA (KV_PADROES_ENABLE=off) — 0 tokens")
        _padroes_desativado_logado = True
    return _PADROES_ENABLE


# Fase 25: mesma lógica p/ a VISÃO GLOBAL (insights de portfólio) e o CHAT do
# Prism — ambos "em breve" e DESATIVADOS por padrão p/ zerar tokens. Reative com
# KV_INSIGHTS_GLOBAIS_ENABLE=on / KV_PRISM_CHAT_ENABLE=on. Nada é apagado.
_INSIGHTS_GLOBAIS_ENABLE = os.environ.get("KV_INSIGHTS_GLOBAIS_ENABLE", "off") not in ("off", "0", "false", "False", "")
_PRISM_CHAT_ENABLE = os.environ.get("KV_PRISM_CHAT_ENABLE", "off") not in ("off", "0", "false", "False", "")
_insights_desativado_logado = False
_chat_desativado_logado = False

# Resposta fixa do chat quando desativado (sem chamar o LLM).
PRISM_CHAT_EM_BREVE = "O Prism está temporariamente em manutenção e voltará em breve. 🛠️"


def _insights_globais_ativo() -> bool:
    global _insights_desativado_logado
    if not _INSIGHTS_GLOBAIS_ENABLE and not _insights_desativado_logado:
        log.info("[insights] visão global DESATIVADA (KV_INSIGHTS_GLOBAIS_ENABLE=off) — 0 tokens")
        _insights_desativado_logado = True
    return _INSIGHTS_GLOBAIS_ENABLE


def _prism_chat_ativo() -> bool:
    global _chat_desativado_logado
    if not _PRISM_CHAT_ENABLE and not _chat_desativado_logado:
        log.info("[prism] chat DESATIVADO (KV_PRISM_CHAT_ENABLE=off) — 0 tokens")
        _chat_desativado_logado = True
    return _PRISM_CHAT_ENABLE


def _media(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _desvio_padrao(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _media(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


# ═════════════════════════════════════════════════════════════════════════
# Insights QUANTITATIVOS (determinísticos, sem IA) — Fase 17
# ═════════════════════════════════════════════════════════════════════════
def _fmt_dur_h(seg: float) -> str:
    """Duração legível p/ gestor: 3h20 / 45min / 12s."""
    s = int(round(seg or 0))
    h, resto = divmod(s, 3600)
    m = resto // 60
    if h > 0:
        return f"{h}h{m:02d}" if m else f"{h}h"
    if m > 0:
        return f"{m}min"
    return f"{s}s"


_LEAN_ROTULO = {
    "valor_agregado": "produtivo",
    "desperdicio": "desperdício",
}


def _inicio_video_dt(v: dict) -> datetime | None:
    """Instante REAL de gravação do vídeo: relógio no nome (edge, token
    seg_YYYYMMDD_HHMMSS) com fallback em processado_em. None se nada parsear."""
    iso = _parse_gravado_em_nome(v.get("nome")) or v.get("processado_em")
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════════════
# Fase 56 (Parte A) — DENOMINADOR HONESTO: um só helper para toda métrica
#
# O denominador NUNCA é o relógio de parede — é o TEMPO OBSERVADO (a soma das
# durações dos eventos daquele recorte). Com a amostragem sistemática (5 min
# gravando, 5 pulando) só existe vídeo para ~metade da hora; dividir por 60 min
# transformaria a metade não gravada numa faixa cinza indistinguível de dúvida
# real — e, como a amostragem é permanente, esse cinza jamais cairia.
#
# `posto_vazio` é uma CATEGORIA PRÓPRIA, não cinza. Ele já vinha somando ao
# denominador sem entrar em nenhuma fatia, então aparecia como "não
# classificado" nas barras — falso cinza que este helper elimina.
#
# Todas as agregações (dia, hora, dashboard) passam por aqui: era a divergência
# entre caminhos de cálculo que fazia a tela principal e os gráficos discordarem.
# ═════════════════════════════════════════════════════════════════════════
def compor_tempo_observado(va_s: float, desp_s: float, vazio_s: float,
                           total_s: float) -> dict:
    """Percentuais sobre o TEMPO OBSERVADO.

    Fase 63: não há mais fatia cinza. Produtivo + não-produtivo fecham 100%,
    e `vazio` é um DETALHE do não-produtivo (quanto dele foi posto vazio), não
    uma terceira fatia — por isso não entra na soma.

    Qualquer resíduo (tempo observado que não caiu em nenhuma categoria por
    arredondamento ou por evento sem rótulo) vai para NÃO-PRODUTIVO, na mesma
    convenção do resto do sistema: sem prova de que agrega valor, não agrega.
    Somar o resíduo ao produtivo inflaria o número que o cliente mostra para
    a diretoria — é o único erro aqui que não pode acontecer.
    """
    tot = float(total_s or 0)
    if tot <= 0:
        return {"va_pct": 0.0, "desp_pct": 0.0, "vazio_pct": 0.0, "observado_s": 0.0}
    va = min(max(0.0, float(va_s)), tot)
    desp = max(0.0, tot - va)          # absorve o resíduo
    vazio = min(max(0.0, float(vazio_s)), desp)
    return {
        "va_pct": round(va / tot * 100, 1),
        "desp_pct": round(desp / tot * 100, 1),
        "vazio_pct": round(vazio / tot * 100, 1),
        "observado_s": round(tot, 1),
    }


def _cat_do_evento(e: dict, cat_por_label: dict) -> tuple[str, str, float]:
    """(label efetivo, categoria lean, duração) de um evento principal."""
    label = e.get("label_corrigido") or e.get("comportamento_label") or "?"
    cat = categoria_efetiva(cat_por_label.get(label))
    dur = max(0.0, float(e.get("tempo_fim_s") or 0) - float(e.get("tempo_inicio_s") or 0))
    return label, cat, dur


def _montar_placar(
    eventos: list[dict],
    videos: list[dict],
    cat_por_label: dict,
    min_unidade_s: float | None = None,
) -> dict | None:
    """Fase 19 — Placar do PROCESSO comparado com ELE MESMO.

    Agrupa os eventos principais e compara a unidade mais recente com a melhor
    unidade observada (maior % produtivo). A unidade é o DIA real de gravação
    (relógio no nome do vídeo); só cai para SESSÃO (vídeo) quando nenhum vídeo
    tem data confiável. Com uma unidade só — o caso típico de 1 dia — entra em
    modo REFERÊNCIA sobre o dia AGREGADO (a base a partir da qual os próximos
    dias serão comparados), em vez de comparar vídeo-a-vídeo (o que deixava um
    clipe curto/atípico do fim do dia zerar o placar). Nunca cita pessoa. None
    só se não houver observação mínima (KV_PLACAR_MIN_UNIDADE_S, default 60s)."""
    if min_unidade_s is None:
        min_unidade_s = float(os.environ.get("KV_PLACAR_MIN_UNIDADE_S", "60"))

    # Metadados por vídeo: dia + rótulos + ordem cronológica.
    meta: dict[str, dict] = {}
    for v in videos or []:
        vid = v.get("id")
        if not vid:
            continue
        dt = _inicio_video_dt(v)
        if dt:
            dia_iso = dt.date().isoformat()
            p = dia_iso.split("-")
            meta[vid] = {
                "dia_iso": dia_iso,
                "dia_rot": f"{p[2]}/{p[1]}",
                "sessao_rot": dt.strftime("%d/%m %Hh%M"),
                "ordem": dt.isoformat(),
            }
        else:
            meta[vid] = {
                "dia_iso": None,
                "dia_rot": None,
                "sessao_rot": (v.get("nome") or vid)[:18],
                "ordem": v.get("processado_em") or "",
            }

    def _agregar(chave_fn, rotulo_fn, ordem_fn) -> dict[str, dict]:
        grupos: dict[str, dict] = {}
        for e in eventos or []:
            m = meta.get(e.get("video_id"))
            if not m:
                continue
            ch = chave_fn(m, e.get("video_id"))
            if ch is None:
                continue
            label, cat, dur = _cat_do_evento(e, cat_por_label)
            if dur <= 0:
                continue
            g = grupos.get(ch)
            if g is None:
                g = grupos[ch] = {
                    "tot": 0.0, "va": 0.0, "desp": 0.0, "acoes": {},
                    "rotulo": rotulo_fn(m, e.get("video_id")), "ordem": ordem_fn(m, e.get("video_id")),
                }
            g["tot"] += dur
            if cat == "valor_agregado":
                g["va"] += dur
            elif cat == "desperdicio":
                g["desp"] += dur
            a = g["acoes"].setdefault(label, {"seg": 0.0, "cat": cat})
            a["seg"] += dur
        return {k: g for k, g in grupos.items() if g["tot"] >= min_unidade_s}

    # 1) por DIA (só vídeos com dia confiável). 2) fallback por SESSÃO (vídeo)
    #    SÓ quando não há nenhum dia confiável — assim, com 1 dia só, o placar
    #    entra em modo REFERÊNCIA sobre o dia AGREGADO (Fase 26), em vez de
    #    comparar vídeo-a-vídeo e deixar um clipe curto/atípico zerar o placar.
    grupos = _agregar(lambda m, _v: m["dia_iso"], lambda m, _v: m["dia_rot"], lambda m, _v: m["dia_iso"])
    unidade = "dia"
    if not grupos:
        grupos = _agregar(lambda _m, v: v, lambda m, _v: m["sessao_rot"], lambda m, _v: m["ordem"])
        unidade = "sessão"
    if not grupos:
        return None

    def _va_pct(g: dict) -> float:
        return g["va"] / g["tot"] * 100

    def _desp_pct(g: dict) -> float:
        return g["desp"] / g["tot"] * 100

    def _bloco(g: dict) -> dict:
        return {
            "dia": g["rotulo"], "va_pct": round(_va_pct(g), 1),
            "desp_pct": round(_desp_pct(g), 1), "seg": round(g["tot"], 1),
        }

    atual_k = max(grupos, key=lambda k: grupos[k]["ordem"])
    atual = grupos[atual_k]

    # Modo REFERÊNCIA: uma unidade só — vira a linha de base do processo.
    if len(grupos) < 2:
        return {
            "modo": "referencia",
            "unidade": unidade,
            "score": int(round(_va_pct(atual))),
            "eh_melhor": True,
            "dia_atual": _bloco(atual),
            "dia_melhor": _bloco(atual),
            "puxou": [],
            "vs_anterior": None,
            "n_unidades": 1,
            "ganho": None,
        }

    melhor_k = max(grupos, key=lambda k: (_va_pct(grupos[k]), grupos[k]["ordem"]))
    melhor = grupos[melhor_k]
    eh_melhor = melhor_k == atual_k
    score = 100 if eh_melhor else min(
        100, int(round(_va_pct(atual) / max(_va_pct(melhor), 0.1) * 100))
    )

    # O que puxou pra baixo: ações NÃO-produtivas cujo share cresceu ≥5 pts
    # em relação à melhor unidade.
    puxou: list[tuple[float, str]] = []
    if not eh_melhor:
        for label, a in atual["acoes"].items():
            if a["cat"] == "valor_agregado":
                continue
            share_atual = a["seg"] / atual["tot"] * 100
            share_melhor = melhor["acoes"].get(label, {}).get("seg", 0.0) / melhor["tot"] * 100
            delta = share_atual - share_melhor
            if delta >= 5:
                puxou.append((
                    delta,
                    f"'{label}' subiu de {share_melhor:.0f}% para {share_atual:.0f}% (+{delta:.0f} pts)",
                ))
        puxou.sort(key=lambda t: t[0], reverse=True)

    # Vs as unidades ANTERIORES (média ponderada pelo tempo observado).
    anteriores = [g for k, g in grupos.items() if k != atual_k]
    vs_anterior = None
    if anteriores:
        tot_ant = sum(g["tot"] for g in anteriores) or 1.0
        va_ant = sum(g["va"] for g in anteriores) / tot_ant * 100
        desp_ant = sum(g["desp"] for g in anteriores) / tot_ant * 100
        vs_anterior = {
            "produtivo": {
                "antes": round(va_ant, 1), "atual": round(_va_pct(atual), 1),
                "delta_pp": round(_va_pct(atual) - va_ant, 1),
            },
            "desperdicio": {
                "antes": round(desp_ant, 1), "atual": round(_desp_pct(atual), 1),
                "delta_pp": round(_desp_pct(atual) - desp_ant, 1),
            },
        }

    # Fase 20 — quanto VALE fechar o gap: se o processo rodar como o melhor
    # dia/sessão, quantas horas produtivas cada turno ganha (e por mês).
    # Determinístico: gap de % produtivo × horas do turno (KV_TURNO_H, default
    # 8h) × turnos/mês (KV_TURNOS_MES, default 22).
    ganho = None
    gap_pp = round(_va_pct(melhor) - _va_pct(atual), 1)
    if not eh_melhor and gap_pp >= 2:
        turno_h = float(os.environ.get("KV_TURNO_H", "8"))
        turnos_mes = float(os.environ.get("KV_TURNOS_MES", "22"))
        por_turno_s = gap_pp / 100.0 * turno_h * 3600
        ganho = {
            "gap_pp": gap_pp,
            "turno_h": round(turno_h, 1),
            "por_turno_s": round(por_turno_s, 1),
            "por_mes_s": round(por_turno_s * turnos_mes, 1),
        }

    return {
        "modo": "comparativo",
        "unidade": unidade,
        "score": score,
        "eh_melhor": eh_melhor,
        "dia_atual": _bloco(atual),
        "dia_melhor": _bloco(melhor),
        "puxou": [t for _, t in puxou[:3]],
        "vs_anterior": vs_anterior,
        "n_unidades": len(grupos),
        "ganho": ganho,
    }


def _montar_perguntas_gestor(
    eventos: list[dict],
    videos: list[dict],
    cat_por_label: dict,
    por_roi: list[dict],
    tempo_por_acao: list[dict],
    tendencia_pp: float | None,
    placar: dict | None = None,
    por_categoria: dict | None = None,
) -> list[dict]:
    """Fase 19/20 — os números viram PERGUNTAS prontas pro gestor levar ao chão
    de fábrica (com número e horário real quando possível). Determinístico, sem
    IA. Foco no PROCESSO: nenhuma pergunta cita pessoa. Sempre tenta entregar
    algo útil: além das anomalias (parada, posto, tendência), pergunta como
    REPETIR o melhor dia e pede nome pro tempo ainda sem categoria."""
    from datetime import timedelta

    perguntas: list[dict] = []

    # 1) Maior parada contínua (sequência de eventos de desperdício, com o
    #    horário do relógio real do vídeo quando o nome carrega o token).
    inicio_por_video: dict[str, datetime] = {}
    for v in videos or []:
        dt = _inicio_video_dt(v)
        if v.get("id") and dt:
            inicio_por_video[v["id"]] = dt
    por_video: dict[str, list[dict]] = {}
    for e in eventos or []:
        vid = e.get("video_id")
        if vid:
            por_video.setdefault(vid, []).append(e)
    pior: tuple[float, str, float, float, str] | None = None  # (dur, vid, ini, fim, label)
    for vid, evs in por_video.items():
        evs.sort(key=lambda x: float(x.get("tempo_inicio_s") or 0))
        run_ini: float | None = None
        run_fim = 0.0
        run_label = ""
        def _fecha():
            nonlocal pior
            if run_ini is not None:
                dur = run_fim - run_ini
                if pior is None or dur > pior[0]:
                    pior = (dur, vid, run_ini, run_fim, run_label)
        for e in evs:
            label, cat, dur = _cat_do_evento(e, cat_por_label)
            i = float(e.get("tempo_inicio_s") or 0)
            f = float(e.get("tempo_fim_s") or 0)
            if cat == "desperdicio":
                if run_ini is not None and i - run_fim <= 90:
                    run_fim = max(run_fim, f)
                else:
                    _fecha()
                    run_ini, run_fim, run_label = i, f, label
            else:
                _fecha()
                run_ini = None
        _fecha()
    if pior and pior[0] >= float(os.environ.get("KV_PERGUNTA_PARADA_MIN_S", "600")):
        dur, vid, i, f, label = pior
        dt0 = inicio_por_video.get(vid)
        quando = (
            f"entre {(dt0 + timedelta(seconds=i)).strftime('%Hh%M')} e "
            f"{(dt0 + timedelta(seconds=f)).strftime('%Hh%M')}"
            if dt0 else "num trecho contínuo"
        )
        perguntas.append({
            "texto": f"Houve uma parada contínua de '{label}' {quando} — "
                     "o que travou o processo nesse horário?",
            "contexto": "maior parada contínua observada",
        })

    # 2) Aprender com o MELHOR dia (Fase 20 — pergunta positiva, sempre que há
    #    comparação e o gap é relevante). Kaizen clássico: estudar o dia bom.
    if placar and placar.get("modo") == "comparativo" and not placar.get("eh_melhor") \
            and placar.get("score", 100) <= 90:
        uni = placar.get("unidade") or "dia"
        m, a = placar["dia_melhor"], placar["dia_atual"]
        perguntas.append({
            "texto": f"O melhor {uni} ({m['dia']}) rodou a {m['va_pct']:.0f}% produtivo "
                     f"vs {a['va_pct']:.0f}% agora — o que estava diferente lá "
                     "(preparação, material, ritmo)? Dá para repetir?",
            "contexto": "aprenda com o melhor — é a melhoria mais barata",
        })

    # 3) Posto (ROI) com mais desperdício — recorte por POSTO, não por pessoa.
    for r in por_roi or []:
        if r.get("desp_pct", 0) >= 35 and r.get("seg", 0) >= 600:
            perguntas.append({
                "texto": f"O posto '{r['zona']}' passou {r['desp_pct']:.0f}% do tempo em "
                         "desperdício — falta material, equipamento ou instrução ali?",
                "contexto": "posto com maior desperdício",
            })
            break

    # 4) Fase 63: não existe mais "tempo cinza" para batizar — todo tempo já é
    #    produtivo ou não-produtivo. O que resta perguntar é mais preciso: a
    #    maior ação cuja categoria o sistema ASSUMIU (não decidiu). Ela já está
    #    contando como não-produtiva no placar; se for produtiva, o número está
    #    subestimado agora, e só o gestor resolve isso.
    # Derivado de `tempo_por_acao` (que já carrega o sinal por ação) em vez de
    # vir por parâmetro: um número só, calculado da mesma fonte que a lista
    # abaixo usa — não dá para os dois discordarem.
    sem_evid_pct = sum(float(a.get("pct") or 0)
                       for a in (tempo_por_acao or []) if a.get("sem_evidencia"))
    if sem_evid_pct >= 10:
        maior_assumida = next(
            (a for a in (tempo_por_acao or [])
             if a.get("sem_evidencia") and a.get("seg", 0) >= 120),
            None,
        )
        if maior_assumida:
            perguntas.append({
                "texto": f"'{maior_assumida['acao']}' tomou "
                         f"{maior_assumida['pct']:.0f}% do tempo e está contando "
                         "como NÃO-produtivo porque o sistema teve de assumir, "
                         "sem evidência. Isso agrega valor ao produto? Se sim, o "
                         "placar está subestimando a produtividade hoje.",
                "contexto": f"{sem_evid_pct:.0f}% do tempo classificado por suposição",
            })

    # 5) Ação de desperdício que mais consome o processo.
    for a in tempo_por_acao or []:
        if a.get("categoria") == "desperdicio" and a.get("pct", 0) >= 15:
            perguntas.append({
                "texto": f"'{a['acao']}' consumiu {a['pct']:.0f}% do tempo observado "
                         "— essa espera é evitável ou faz parte do ciclo?",
                "contexto": "ação que mais consome tempo sem agregar valor",
            })
            break

    # 6) Tendência piorando.
    if tendencia_pp is not None and tendencia_pp >= 3:
        perguntas.append({
            "texto": f"O desperdício subiu {tendencia_pp:.0f} pts nos vídeos mais "
                     "recentes — mudou algo no processo (equipe, material, demanda)?",
            "contexto": "tendência de piora",
        })

    return perguntas[:4]


def montar_insights_quantitativos(
    dist: list[dict],
    composicao: dict,
    eventos: list[dict],
    videos: list[dict],
    cat_por_label: dict,
    min_videos_tendencia: int = 4,
) -> dict:
    """Bloco de insights SIMPLES e NUMÉRICOS (sem IA): tempo por ação, split Lean,
    por ROI (zona) e tendência — com frases prontas p/ o gestor. Determinístico:
    todos os números saem dos eventos principais já agregados.

    `dist` = distribuicao_comportamentos enriquecida (tempo_total_s, pct_tempo,
    categoria_lean). `composicao` = composicao_valor (por_categoria_s + *_pct).
    `eventos` = eventos JÁ filtrados (principais, não-descartados) com
    zona_contexto/tempo_inicio_s/tempo_fim_s. `videos` = lista com id+processado_em.
    """
    total = float(composicao.get("tempo_total_s") or 0) or \
        float(sum(d.get("tempo_total_s", 0) for d in (dist or []))) or 1.0
    frases: list[dict] = []

    # Fase 53: NADA de minutagem de vídeo no dashboard. O cliente gerencia um
    # posto de trabalho, não um acervo de gravações — dizer "40min em 9 vídeos"
    # não o ajuda a decidir nada e ainda confunde. Pior: com a amostragem
    # sistemática (Fase 51) a duração absoluta é a do RECORTE amostrado, não a
    # do turno, então exibi-la seria enganoso. As PROPORÇÕES são a leitura
    # correta e continuam todas aqui.

    # 1) Onde o tempo foi (top ações) — % recalculada sobre a MESMA régua do
    #    tempo de atividade, independente da base do `dist`.
    tempo_por_acao = [
        {"acao": d.get("comportamento"), "seg": round(d.get("tempo_total_s", 0), 1),
         "pct": round(float(d.get("tempo_total_s", 0)) / total * 100, 1),
         "categoria": categoria_efetiva(d.get("categoria_lean")),
         # Fase 63: a categoria sempre existe; o que varia é se ela foi
         # DECIDIDA ou ASSUMIDA. É esse sinal que vira pergunta e vira fila.
         "sem_evidencia": not categoria_tem_evidencia(
             d.get("categoria_lean"), d.get("categoria_lean_origem"))}
        for d in sorted(dist or [], key=lambda x: x.get("tempo_total_s", 0), reverse=True)
    ]
    if tempo_por_acao:
        topo = tempo_por_acao[:3]
        partes = [f"{a['acao']} {a['pct']:.0f}%" for a in topo]
        tom = "high" if (topo[0].get("categoria") == "desperdicio") else "info"
        frases.append({"texto": "Onde o tempo foi: " + " · ".join(partes) + ".", "tom": tom})

    # 3) Split binário (produtivo/desperdício/não-classificado) com tempo absoluto
    por_cat_s = composicao.get("por_categoria_s") or {}
    por_categoria = {}
    partes_lean = []
    for k in ("valor_agregado", "desperdicio"):
        seg = float(por_cat_s.get(k, 0) or 0)
        pct = round(seg / total * 100, 1)
        por_categoria[k] = {"seg": round(seg, 1), "pct": pct}
        if seg > 0:
            partes_lean.append(f"{_LEAN_ROTULO[k]} {pct:.0f}%")
    desp_pct = por_categoria["desperdicio"]["pct"]
    if partes_lean:
        tom = "high" if desp_pct >= 40 else ("warn" if desp_pct >= 25 else "ok")
        frases.append({"texto": " · ".join(partes_lean) + ".", "tom": tom})

    # 3b) Fase 28: posto vazio (operador ausente) — frase dedicada, direta.
    vazio_s = sum(
        max(0.0, float(e.get("tempo_fim_s") or 0) - float(e.get("tempo_inicio_s") or 0))
        for e in (eventos or [])
        if (e.get("label_corrigido") or e.get("comportamento_label")) == POSTO_VAZIO_LABEL
    )
    if vazio_s > 0:
        vazio_pct = round(vazio_s / total * 100, 1)
        frases.append({
            "texto": f"Posto vazio (operador ausente) em {vazio_pct:.0f}% do "
                     "tempo observado.",
            "tom": "high" if vazio_pct >= 20 else "warn",
        })

    # 4) Por ROI (zona_contexto): tempo + % produtivo/desperdício
    por_zona: dict[str, dict] = {}
    for e in (eventos or []):
        zona = (e.get("zona_contexto") or "").strip()
        if not zona:
            continue
        dur = max(0.0, float(e.get("tempo_fim_s", 0)) - float(e.get("tempo_inicio_s", 0)))
        if dur <= 0:
            continue
        label = e.get("label_corrigido") or e.get("comportamento_label")
        cat = categoria_efetiva(cat_por_label.get(label))
        z = por_zona.setdefault(zona, {"seg": 0.0, "va": 0.0, "desp": 0.0})
        z["seg"] += dur
        if cat == "valor_agregado":
            z["va"] += dur
        elif cat == "desperdicio":
            z["desp"] += dur
    por_roi = []
    for zona, z in sorted(por_zona.items(), key=lambda kv: kv[1]["seg"], reverse=True):
        seg = z["seg"] or 1.0
        por_roi.append({
            "zona": zona, "seg": round(z["seg"], 1),
            "pct": round(z["seg"] / total * 100, 1),
            "va_pct": round(z["va"] / seg * 100, 1),
            "desp_pct": round(z["desp"] / seg * 100, 1),
        })
    if len(por_roi) > 1:
        partes_roi = [
            f"ROI '{r['zona']}': {r['va_pct']:.0f}% produtivo, {r['desp_pct']:.0f}% desperdício"
            for r in por_roi[:3]
        ]
        frases.append({"texto": " · ".join(partes_roi) + ".", "tom": "info"})

    # 5) Tendência de desperdício ao longo dos vídeos
    periodo = None
    quando = {v.get("id"): v.get("processado_em") for v in (videos or [])}
    desp_por_video: dict = {}
    for e in (eventos or []):
        vid = e.get("video_id")
        if not vid:
            continue
        dur = max(0.0, float(e.get("tempo_fim_s", 0)) - float(e.get("tempo_inicio_s", 0)))
        if dur <= 0:
            continue
        label = e.get("label_corrigido") or e.get("comportamento_label")
        cat = categoria_efetiva(cat_por_label.get(label))
        d = desp_por_video.setdefault(vid, {"tot": 0.0, "desp": 0.0})
        d["tot"] += dur
        if cat == "desperdicio":
            d["desp"] += dur
    ordenados = sorted(
        (vid for vid in desp_por_video if quando.get(vid)),
        key=lambda vid: quando.get(vid) or "",
    )
    if len(ordenados) >= min_videos_tendencia:
        metade = len(ordenados) // 2
        def _media_desp(ids):
            vals = [desp_por_video[i]["desp"] / (desp_por_video[i]["tot"] or 1) * 100 for i in ids]
            return sum(vals) / len(vals) if vals else 0.0
        antes = _media_desp(ordenados[:metade])
        depois = _media_desp(ordenados[metade:])
        delta = round(depois - antes, 1)
        direcao = "subiu" if delta > 0 else ("caiu" if delta < 0 else "estável")
        periodo = {
            "texto": f"Tendência do desperdício: {antes:.0f}% → {depois:.0f}% ({direcao} {abs(delta):.0f} pts).",
            "tendencia_desp_pp": delta,
        }
        frases.append({"texto": periodo["texto"], "tom": "high" if delta > 0 else "ok"})

    # 6) Fase 21 — Ritmo por hora do relógio REAL (junta todos os dias): em
    #    que horas o processo rende e em que horas trava. Só entra quando o
    #    nome do vídeo carrega o token de relógio (edge) ou há processado_em.
    inicio_por_video = {}
    for v in (videos or []):
        dt0 = _inicio_video_dt(v)
        if v.get("id") and dt0:
            inicio_por_video[v["id"]] = dt0
    ritmo_agg: dict[int, dict] = {}
    for e in (eventos or []):
        dt0 = inicio_por_video.get(e.get("video_id"))
        if dt0 is None:
            continue
        _label, cat, dur = _cat_do_evento(e, cat_por_label)
        if dur <= 0:
            continue
        hora = (dt0 + timedelta(seconds=float(e.get("tempo_inicio_s") or 0))).hour
        h = ritmo_agg.setdefault(hora, {"seg": 0.0, "va": 0.0, "desp": 0.0})
        h["seg"] += dur
        if cat == "valor_agregado":
            h["va"] += dur
        elif cat == "desperdicio":
            h["desp"] += dur
    por_hora = []
    for hora in sorted(ritmo_agg):
        h = ritmo_agg[hora]
        if h["seg"] < 120:  # menos de 2 min na hora = ruído
            continue
        s = h["seg"]
        por_hora.append({
            "hora": hora, "seg": round(s, 1),
            "va_pct": round(h["va"] / s * 100, 1),
            "desp_pct": round(h["desp"] / s * 100, 1),
        })
    if len(por_hora) >= 3:
        melhor_h = max(por_hora, key=lambda x: x["va_pct"])
        pior_h = min(por_hora, key=lambda x: x["va_pct"])
        if melhor_h["hora"] != pior_h["hora"] and melhor_h["va_pct"] - pior_h["va_pct"] >= 10:
            frases.append({
                "texto": f"Ritmo do dia: melhor hora {melhor_h['hora']}h "
                         f"({melhor_h['va_pct']:.0f}% produtivo) · pior hora "
                         f"{pior_h['hora']}h ({pior_h['va_pct']:.0f}%).",
                "tom": "info",
            })

    # Fase 19/20 — Placar do processo (vs melhor dia, com ganho projetado) +
    # perguntas prontas pro gestor. Determinísticos; olham o PROCESSO, nunca a
    # pessoa. O placar entra ANTES: as perguntas usam o melhor dia dele.
    placar = _montar_placar(eventos, videos, cat_por_label)
    perguntas = _montar_perguntas_gestor(
        eventos, videos, cat_por_label, por_roi, tempo_por_acao,
        (periodo or {}).get("tendencia_desp_pp"),
        placar=placar, por_categoria=por_categoria,
    )

    return {
        "frases": frases,
        "tempo_por_acao": tempo_por_acao,
        "por_categoria": por_categoria,
        "por_roi": por_roi,
        "por_hora": por_hora,
        "periodo": periodo,
        "placar": placar,
        "perguntas": perguntas,
    }


# ═════════════════════════════════════════════════════════════════════════
# Fase 35 — ANÁLISE DIÁRIA ("Dia a dia"): como foi o dia do operador, dia a
# dia, comparando JANELAS de tempo (7/30 dias — nunca dia contra dia) e
# marcando dias SEM TRABALHO. Python puro, zero token de IA.
# ═════════════════════════════════════════════════════════════════════════
def montar_analise_diaria(sb: Client, empresa: str, processo: str, dias: int = 30) -> dict:
    """Agrega os eventos por DIA REAL (relógio dos vídeos) e devolve:
      dias:      [{dia, rot, dow, tempo_obs_s, va_pct/desp_pct/vazio_pct,
                   posto_vazio_s/pct, n_videos, visitas, primeira_h, ultima_h,
                   top_acao, por_hora[], sem_trabalho}]  (calendário contínuo —
                   dia sem vídeo vira sem_trabalho='sem_captura'; dia filmado
                   mas ~só posto_vazio vira 'posto_vazio')
      janelas:   últimos 7 vs 7 anteriores e últimos 30 vs 30 anteriores
                 (agregado + delta de % produtivo)
      tendencia: inclinação (pts de produtivo por dia trabalhado) + direção.
    """
    videos = varrer(sb, "videos", "id, nome, duracao_s, gravado_em, processado_em",
                    empresa=empresa, processo=processo)
    inicio_por_video: dict[str, datetime] = {}
    for v in videos:
        dt0 = _inicio_video_dt(v)
        if v.get("id") and dt0:
            inicio_por_video[v["id"]] = dt0
    if not inicio_por_video:
        return {"dias": [], "janelas": None, "tendencia": None}

    comps = varrer(sb, "comportamentos", "label, categoria_lean",
                   empresa=empresa, processo=processo)
    cat_por_label = {c["label"]: c.get("categoria_lean") for c in comps}

    eventos = varrer(
        sb, "eventos",
        "video_id, comportamento_label, label_corrigido, tempo_inicio_s, "
        "tempo_fim_s, validacao_correto, principal, papel_pessoa, "
        "confianca, em_duvida, duvida_motivo, validado_humano, n_rotulos_no_minuto, "
        # Fase 66: sem `n_amostras` o ramo 'sem_evidencia' nunca disparava
        # aqui (vinha None) e o KPI mostrava 0 para sempre; sem
        # `origem_validacao` não dava para excluir o determinístico;
        # sem `camadas_disparadas` a garantia da sombra caía no fallback.
        "n_amostras, origem_validacao, camadas_disparadas",
        empresa=empresa, processo=processo,
    )
    eventos = [
        e for e in eventos
        if e.get("validacao_correto") is not False and e.get("principal") is not False
    ]

    # B5: limiar do processo, lido UMA vez (a checagem por evento é pura).
    try:
        _lim_duvida = limiar_duvida(sb, empresa, processo)
    except Exception:
        _lim_duvida = DUVIDA_LIMIAR_PADRAO
    # Fase 63: o KPI da dúvida tem de bater com a FILA. Se a categoria assumida
    # manda o trecho para a fila mas não entra na curva, a tela diz uma coisa e
    # a fila outra — e o número deixa de servir para acompanhar a campanha.
    _assumidos = labels_com_categoria_assumida(sb, empresa, processo) if processo else set()

    # ── Agregação por dia (e por hora dentro do dia) ──
    por_dia: dict[str, dict] = {}
    videos_por_dia: dict[str, set] = defaultdict(set)
    for vid, dt0 in inicio_por_video.items():
        videos_por_dia[dt0.date().isoformat()].add(vid)

    for e in eventos:
        dt0 = inicio_por_video.get(e.get("video_id"))
        if dt0 is None:
            continue
        label, cat, dur = _cat_do_evento(e, cat_por_label)
        if dur <= 0:
            continue
        inst = dt0 + timedelta(seconds=float(e.get("tempo_inicio_s") or 0))
        dia = inst.date().isoformat()
        d = por_dia.setdefault(dia, {
            "tot": 0.0, "va": 0.0, "desp": 0.0, "duvida": 0.0, "sem_evidencia": 0.0,
            "duvida_resolvida": 0.0, "sem_evidencia_resolvida": 0.0,
            "vazio": 0.0, "visitas": 0, "acoes": defaultdict(float),
            "horas": defaultdict(lambda: {"seg": 0.0, "va": 0.0, "desp": 0.0,
                                          "vazio": 0.0}),
            # Fase 35.2: "jornada" — buckets de 15 min do dia (96) com segundos
            # por categoria, p/ desenhar o filme do dia em uma faixa.
            # Fase 63: sem bucket "none" — `categoria_efetiva` garante que
            # todo evento cai em va ou desp (ou vazio, que é detalhe do desp).
            "buckets": defaultdict(lambda: {"va": 0.0, "desp": 0.0, "vazio": 0.0}),
            "primeiro": inst, "ultimo": inst,
        })
        d["tot"] += dur
        eh_vazio = (e.get("papel_pessoa") == "posto_vazio") or (label == POSTO_VAZIO_LABEL)
        if eh_vazio:
            d["vazio"] += dur
        elif cat == "valor_agregado":
            d["va"] += dur
        elif cat == "desperdicio":
            d["desp"] += dur
        # B5 — A CURVA DO VEREDITO. Fase 66: `incluir_resolvidas=True`.
        #
        # Antes, validar um trecho o APAGAVA do histórico: a curva media "o que
        # ainda está em dúvida hoje", não "o que o sistema não soube naquele
        # dia". O passado se reescrevia a cada validação, e o gráfico que existe
        # para provar aprendizado era zerado justamente pelo ato de aprender —
        # nunca poderia mostrar uma queda, só um chão liso em 0%.
        #
        # Agora a curva é histórica (não se reescreve) e a parte já julgada vem
        # separada, para a tela mostrar o trabalho feito em vez de escondê-lo.
        _dv, _, _tp = (evento_em_duvida(e, _lim_duvida, _assumidos,
                                        incluir_resolvidas=True)
                       if _lim_duvida is not None else (False, "", ""))
        if _dv:
            _resolvida = bool(e.get("validado_humano"))
            if _tp == "sem_evidencia":
                d["sem_evidencia"] += dur
                if _resolvida:
                    d["sem_evidencia_resolvida"] += dur
            else:
                d["duvida"] += dur
                if _resolvida:
                    d["duvida_resolvida"] += dur
        if e.get("papel_pessoa") == "visitante":
            d["visitas"] += 1
        if not eh_vazio:
            d["acoes"][label] += dur
        h = d["horas"][inst.hour]
        h["seg"] += dur
        if eh_vazio:
            h["vazio"] += dur          # categoria própria, NUNCA cinza
        elif cat == "valor_agregado":
            h["va"] += dur
        elif cat == "desperdicio":
            h["desp"] += dur
        if inst < d["primeiro"]:
            d["primeiro"] = inst
        fim = dt0 + timedelta(seconds=float(e.get("tempo_fim_s") or 0))
        if fim > d["ultimo"]:
            d["ultimo"] = fim
        # Fase 35.2: pinta os buckets de 15 min pela sobreposição real do
        # evento no relógio do dia (o "filme" da jornada).
        chave_cat = ("vazio" if eh_vazio else
                     "va" if cat == "valor_agregado" else "desp")
        m_ini = inst.hour * 60 + inst.minute + inst.second / 60.0
        m_fim = min(1440.0, m_ini + dur / 60.0)
        b = int(m_ini // 15)
        while b * 15 < m_fim and b < 96:
            ov = min(m_fim, (b + 1) * 15) - max(m_ini, b * 15)
            if ov > 0:
                d["buckets"][b][chave_cat] += ov * 60.0
            b += 1

    # ── Calendário contínuo: 60 dias internos (p/ mês vs mês anterior);
    #    o retorno exibe só os últimos `dias`. ──
    todos_dias = sorted(set(por_dia) | set(videos_por_dia))
    fim_cal = datetime.fromisoformat(todos_dias[-1]).date()
    ini_cal = fim_cal - timedelta(days=59)
    DOW = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
    saida_dias: list[dict] = []
    cursor = ini_cal
    while cursor <= fim_cal:
        iso = cursor.isoformat()
        rot = f"{cursor.day:02d}/{cursor.month:02d}"
        dow = DOW[cursor.weekday()]
        d = por_dia.get(iso)
        if d is None or d["tot"] <= 0:
            saida_dias.append({
                "dia": iso, "rot": rot, "dow": dow, "tempo_obs_s": 0.0,
                "va_pct": 0.0, "desp_pct": 0.0, "vazio_pct": 0.0,
                "duvida_pct": 0.0, "sem_evidencia_pct": 0.0,
                "duvida_resolvida_pct": 0.0, "sem_evidencia_resolvida_pct": 0.0,
                "posto_vazio_s": 0.0, "posto_vazio_pct": 0.0,
                "atipico_vazio": False, "n_videos": len(videos_por_dia.get(iso, ())),
                "visitas": 0, "primeira_h": None, "ultima_h": None, "top_acao": None,
                "top_acoes": [], "linha_tempo": [],
                "por_hora": [],
                # sem vídeo nenhum = sem captura; com vídeo mas sem evento = vazio
                "sem_trabalho": "sem_captura" if not videos_por_dia.get(iso) else "posto_vazio",
            })
        else:
            tot = d["tot"]
            vazio_pct = d["vazio"] / tot * 100
            atividade = d["va"] + d["desp"]
            # Dia filmado mas ~só posto vazio (ou atividade desprezível) =
            # "máquina vazia o dia todo" → o dono precisa VER isso.
            sem_trab = "posto_vazio" if (vazio_pct >= 90 or atividade < 120) else None
            top = max(d["acoes"].items(), key=lambda kv: kv[1]) if d["acoes"] else None
            # Fase 35.2: top 5 ações do dia (mini-pareto do dia selecionado).
            top_acoes = [
                {"label": lbl, "seg": round(s, 1)}
                for lbl, s in sorted(d["acoes"].items(), key=lambda kv: kv[1], reverse=True)[:5]
            ]
            # Fase 50: linha do tempo da JORNADA — PROPORCIONAL por bucket de 15
            # min. Antes pintava só a categoria DOMINANTE de cada bloco, o que
            # ESCONDIA minorias (um desperdício curto dentro de um bloco quase
            # todo produtivo sumia, contradizendo o "ritmo por hora"). Agora cada
            # bloco é fatiado na proporção real de cada categoria (ordem fixa p/
            # ficar limpo); faixas contíguas de mesma categoria são fundidas.
            ORDEM_CAT = ("va", "desp", "vazio")
            linha_tempo: list[dict] = []
            for b in sorted(d["buckets"]):
                bk = d["buckets"][b]
                seg_b = sum(bk.values())
                if seg_b < 60:            # menos de 1 min no bucket = buraco
                    continue
                corte_m = b * 15.0
                for cat in ORDEM_CAT:
                    s = bk.get(cat, 0.0)
                    if s <= 0:
                        continue
                    seg_ini = corte_m
                    seg_fim = corte_m + 15.0 * (s / seg_b)   # largura proporcional (min)
                    corte_m = seg_fim
                    if linha_tempo and linha_tempo[-1]["cat"] == cat \
                            and abs(linha_tempo[-1]["fim_m"] - seg_ini) < 0.02:
                        linha_tempo[-1]["fim_m"] = round(seg_fim, 2)
                    else:
                        linha_tempo.append({"ini_m": round(seg_ini, 2),
                                            "fim_m": round(seg_fim, 2), "cat": cat})
            por_hora = []
            for hora in sorted(d["horas"]):
                h = d["horas"][hora]
                if h["seg"] < 60:
                    continue
                por_hora.append({
                    "hora": hora, "seg": round(h["seg"], 1),
                    **compor_tempo_observado(h["va"], h["desp"], h["vazio"], h["seg"]),
                })
            saida_dias.append({
                "dia": iso, "rot": rot, "dow": dow,
                "tempo_obs_s": round(tot, 1),
                **compor_tempo_observado(d["va"], d["desp"], d["vazio"], tot),
                # B5 — a curva que é o veredito do produto: se cai semana a
                # semana o sistema aprende; se estabiliza em 20-30%, a tese
                # está errada. Fica VISÍVEL e permanente, não escondida.
                "duvida_pct": round(d["duvida"] / tot * 100, 1),
                # Quanto dessa dúvida JÁ foi julgada por gente. A curva total
                # não se mexe; esta sobe conforme a fila é trabalhada.
                "duvida_resolvida_pct": round(d["duvida_resolvida"] / tot * 100, 1),
                "sem_evidencia_resolvida_pct": round(
                    d["sem_evidencia_resolvida"] / tot * 100, 1),
                # Trecho curto demais para afirmar OU duvidar — resolve-se com
                # mais amostragem, não com melhor decisão.
                "sem_evidencia_pct": round(d["sem_evidencia"] / tot * 100, 1),
                "posto_vazio_s": round(d["vazio"], 1),
                "posto_vazio_pct": round(vazio_pct, 1),
                # Fase 79: dia quase todo posto vazio ou é falta real, ou é
                # falha grave de detecção. As duas merecem olhada — e nenhuma
                # chamava atenção, porque esses eventos saem da fila por
                # mecanismo e o dia fica invisível.
                "atipico_vazio": bool(vazio_pct >= VAZIO_ATIPICO_PCT),
                "n_videos": len(videos_por_dia.get(iso, ())),
                "visitas": d["visitas"],
                "primeira_h": d["primeiro"].strftime("%H:%M"),
                "ultima_h": d["ultimo"].strftime("%H:%M"),
                "top_acao": ({"label": top[0], "seg": round(top[1], 1)} if top else None),
                "top_acoes": top_acoes,
                "linha_tempo": linha_tempo,
                "por_hora": por_hora,
                "sem_trabalho": sem_trab,
            })
        cursor += timedelta(days=1)

    # ── Janelas (rolando a partir do dia mais recente — NUNCA dia vs dia) ──
    def _janela(ds: list[dict]) -> dict:
        trab = [x for x in ds if not x["sem_trabalho"] and x["tempo_obs_s"] > 0]
        tot = sum(x["tempo_obs_s"] for x in trab)
        va_s = sum(x["tempo_obs_s"] * x["va_pct"] / 100 for x in trab)
        desp_s = sum(x["tempo_obs_s"] * x["desp_pct"] / 100 for x in trab)
        vazio_s = sum(x["posto_vazio_s"] for x in ds)
        return {
            "dias": len(ds),
            "dias_trabalhados": len(trab),
            "dias_sem_trabalho": sum(1 for x in ds if x["sem_trabalho"]),
            "tempo_obs_s": round(tot, 1),
            "va_pct": round(va_s / tot * 100, 1) if tot > 0 else 0.0,
            "desp_pct": round(desp_s / tot * 100, 1) if tot > 0 else 0.0,
            "vazio_pct": round(vazio_s / tot * 100, 1) if tot > 0 else 0.0,
            "posto_vazio_s": round(vazio_s, 1),
            "visitas": sum(x["visitas"] for x in ds),
            "horas_produtivas_dia": round(va_s / 3600 / max(1, len(trab)), 2),
        }

    def _fatia(n_fim: int, n_ini: int) -> list[dict]:
        # dias [len-n_ini : len-n_fim] contando do fim (calendário contínuo)
        a = max(0, len(saida_dias) - n_ini)
        b = max(0, len(saida_dias) - n_fim)
        return saida_dias[a:b]

    janelas = None
    if saida_dias:
        j7 = _janela(_fatia(0, 7))
        j7_ant = _janela(_fatia(7, 14))
        j30 = _janela(_fatia(0, 30))
        j30_ant = _janela(_fatia(30, 60))
        janelas = {
            "semana": {"atual": j7, "anterior": j7_ant,
                       "delta_va_pp": round(j7["va_pct"] - j7_ant["va_pct"], 1)
                       if j7_ant["dias_trabalhados"] else None},
            "mes": {"atual": j30, "anterior": j30_ant,
                    "delta_va_pp": round(j30["va_pct"] - j30_ant["va_pct"], 1)
                    if j30_ant["dias_trabalhados"] else None},
        }

    # ── Tendência: inclinação da % produtiva pelos dias TRABALHADOS ──
    tendencia = None
    dias_exibidos = saida_dias[-max(1, dias):]
    trabalhados = [d for d in dias_exibidos if not d["sem_trabalho"] and d["tempo_obs_s"] > 0]
    if len(trabalhados) >= 3:
        ys = [d["va_pct"] for d in trabalhados]
        n = len(ys)
        xm = (n - 1) / 2.0
        ym = sum(ys) / n
        den = sum((i - xm) ** 2 for i in range(n)) or 1.0
        slope = sum((i - xm) * (y - ym) for i, y in enumerate(ys)) / den
        direcao = "ascendente" if slope >= 0.3 else ("descendente" if slope <= -0.3 else "estável")
        tendencia = {
            "slope_pts_dia": round(slope, 2),
            "direcao": direcao,
            "dias_considerados": n,
        }

    return {"dias": dias_exibidos, "janelas": janelas, "tendencia": tendencia}


def montar_serie_temporal(sb: Client, empresa: str, processo: str) -> dict:
    """Série por vídeo (ordenada por processado_em) com o SHARE de % do
    tempo por comportamento (label efetivo) e por categoria Lean. Tudo
    normalizado para ser comparável entre turnos de durações diferentes.
    """
    # Fase 81 — os 500 eram os 500 MAIS ANTIGOS (`desc=False` + `limit`). Passados
    # 500 vídeos, a "evolução" congelava no começo da campanha e nenhum turno
    # novo entrava mais na curva. Pega os mais recentes e reinverte para plotar
    # em ordem cronológica.
    videos = (
        sb.table("videos")
        .select("id, nome, duracao_s, processado_em")
        .eq("empresa", empresa)
        .eq("processo", processo)
        .order("processado_em", desc=True)
        .limit(500)
        .execute()
        .data
    ) or []
    videos = list(reversed(videos))

    eventos = varrer(
        sb, "eventos",
        "video_id, comportamento_label, label_corrigido, tempo_inicio_s, "
        "tempo_fim_s, validacao_correto, pessoa_track_id, principal",
        empresa=empresa, processo=processo,
    )

    comps = varrer(sb, "comportamentos", "label, categoria_lean",
                   empresa=empresa, processo=processo)
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
            cat = categoria_efetiva(cat_por_label.get(lbl))
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
- categoria_relacionada: "valor_agregado" | "desperdicio" | null
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
    if not _padroes_ativo():
        return 0   # Fase 24: desativado — não gasta token nem apaga vigentes
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
    if not _padroes_ativo():
        return 0   # Fase 24: desativado — não gasta token nem apaga vigentes
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
    rois_contexto_secundario: dict | None = None,
    nome_secundario: str | None = None,
    storage_path_secundario: str | None = None,
    segmento_id_secundario: str | None = None,
) -> dict:
    """Roda o pipeline completo. Devolve dict com video_id, n_eventos,
    n_auto_validados, n_sugestoes.

    Fase 6 (dual-angle): se `video_path_secundario` for dado, a cam2 entra como
    2º ângulo na MESMA chamada ao VLM (a cam1 dirige detecção/segmentação). Gera
    UMA trilha de eventos (na cam1) — sem duplicidade na validação/métricas.

    Fase 28: `rois_contexto` com zona papel='posto_operador' liga o MODO
    OPERADOR (só o titular do posto é analisado; transeuntes descartados;
    ausência vira posto_vazio). `rois_contexto_secundario` (zonas da cam2)
    habilita a CONFIRMAÇÃO em profundidade pela câmera lateral.
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

    # Fase 72: barra a duplicata ANTES de qualquer inferência. A guarda do
    # `etapa_persistir` é a garantia; esta é a que evita pagar VLM e YOLO por
    # um vídeo que seria recusado no fim.
    _barrar_duplicata(sb, empresa, processo, caminho_storage)

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

    amostras, info_video, ids_unicos, descritores_track = etapa_detectar_e_amostrar(
        yolo, video_path, intervalo_amostragem_s, rois_contexto, progress_cb,
        cam_id=cam_id,
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

    # Fase 28: nome da zona do posto (liga posto_vazio + confirmação dupla).
    zona_posto = None
    if _OPERADOR_FILTRO_ENABLE:
        zona_posto = next(
            (n for n, i in rois_contexto.items() if i.get("papel") == "posto_operador"),
            None,
        )

    # Fase 6: 2º ângulo (cam2) anexado a cada amostra para o VLM concluir com os
    # dois pontos de vista. cam1 já dirigiu detecção/tracking acima.
    # Fase 28: com zonas na cam2, o mesmo passe roda um predict leve por slot e
    # marca op_cam2 (o operador está mesmo ATRÁS da máquina?).
    offset_cam2 = 0.0
    if video_path_secundario:
        # Fase 30: offset REAL de relógio entre os dois segmentos (podem começar
        # com segundos/minutos de diferença) — sem isso a confirmação e o frame
        # do 2º ângulo olham o instante errado.
        offset_cam2 = _offset_entre_nomes(nome_video, nome_secundario)
        desc_acc_cam2: dict = {}
        n_sec = _anexar_segundo_angulo(
            amostras, video_path_secundario,
            yolo=(yolo if zona_posto else None),
            rois_sec=rois_contexto_secundario,
            offset_s=offset_cam2,
            desc_acc=desc_acc_cam2,
        )
        # Fase 84 — o descritor da cam2. Antes disto, `descritores_track` só
        # tinha a câmera PRIMÁRIA: o pareamento elege sempre a de menor id
        # (cam1) para dirigir detecção/tracking, e a cam2 entrava só como
        # imagem de confirmação — sem tracker, sem id, sem descritor. As
        # únicas linhas de cam2 no banco eram de segmentos processados SOLO,
        # quando a cam2 virava primária por não ter par.
        if desc_acc_cam2:
            _int_cam2 = intervalo_amostragem_s * _CAM2_CONFIRM_STRIDE
            try:
                _info2 = inspecionar_video(video_path_secundario)
                _w2, _h2 = int(_info2["largura"]), int(_info2["altura"])
            except Exception:
                _w2 = _h2 = 0
            descritores_track = list(descritores_track) + fechar_descritores(
                desc_acc_cam2, _int_cam2, cam_id_secundario, _w2, _h2,
            )
            log.info("[descritor] cam2 (%s): %d track(s) descritos.",
                     cam_id_secundario or "cam2", len(desc_acc_cam2))
        log.info(
            f"[dual-angle] {cam_id or 'cam1'} + {cam_id_secundario or 'cam2'}: "
            f"2º ângulo em {n_sec}/{len(amostras)} amostras"
        )

    # Fase 28: veredito por slot (dupla quando a cam2 tem zona de posto).
    if zona_posto:
        tem_posto_sec = any(
            i.get("papel") == "posto_operador"
            for i in (rois_contexto_secundario or {}).values()
        )
        politica = _OPERADOR_CONFIRMACAO if (tem_posto_sec and video_path_secundario) else "cam1"
        stats_op = etapa_confirmar_operador(amostras, politica)
        log.info(
            "[operador] política=%s · %d slots: %d com operador (%d resgatados "
            "pela cam2, %d por ponte temporal), %d vazios, %d rebaixados pela cam2",
            politica, stats_op["slots"], stats_op["presentes"],
            stats_op["resgatados_cam2"], stats_op["pontes"],
            stats_op["vazios"], stats_op["rebaixados"],
        )

    observacoes = etapa_analise_vlm(
        groq_client, amostras, descricao, memoria, progress_cb,
        conhecimento_adquirido=conhecimento,
        zona_posto=zona_posto,
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

    # Fase 62: chave por processo. Desligada, nenhuma correção generaliza e
    # nenhum rótulo sai como aprendido — tudo vai para a fila.
    _aprende = aprendizado_automatico(sb, empresa, processo)
    log.info(
        "[aprendizado] generalização automática %s para %s/%s",
        "LIGADA" if _aprende else "DESLIGADA", empresa, processo,
    )
    _, catalogo, label_de, origem_de = etapa_clusterizar(
        groq_client, observacoes, descricao, memoria, limiar_auto_validacao, progress_cb,
        conhecimento_adquirido=conhecimento,
        aprendizado_auto=_aprende,
    )

    progress_cb("segmentar", 0, "Formando eventos contínuos")
    eventos_crus = etapa_segmentar_eventos(observacoes, label_de, intervalo_amostragem_s)

    # Fase 16: reduz a ~1 evento PRINCIPAL por minuto (a ação que resume o minuto).
    # Os crus viram AUDITORIA; os principais alimentam validação e métricas.
    principais: list[dict] = []
    if os.environ.get("KV_PRINCIPAL_ENABLE", "on") not in ("off", "0", "false", "False"):
        try:
            # Fase 57: camadas de dúvida carregadas do banco — DADOS, não
            # código. O dono do processo escreve a décima regra sem deploy.
            principais = etapa_consolidar_principais(
                eventos_crus, catalogo, info_video["duracao_s"],
                camadas=carregar_camadas_duvida(sb, empresa, processo),
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
    video_id, n_auto, ids_principais = etapa_persistir(
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
        descritores_track=descritores_track,
    )
    progress_cb("persistir", 100, f"{len(eventos)} eventos · {n_auto} auto-validados")

    # Fase 36: PRÉ-EXTRAI todos os JPEGs de visualização (frames dos eventos,
    # strips da cam2 e frames de referência) enquanto os vídeos ainda estão no
    # DISCO LOCAL — ver um evento depois custa ~50KB de egress, não o vídeo
    # inteiro (era a maior fonte de egress do Storage). Não-fatal.
    frames_stats = {"ok": False}
    try:
        frames_stats = pre_extrair_frames(
            sb, caminho_storage, video_path, eventos, ids_principais,
            video_id, cam_id,
            video_path_sec=video_path_secundario,
            storage_path_sec=storage_path_secundario,
            segmento_id_sec=segmento_id_secundario,
            cam_id_sec=cam_id_secundario,
            offset_s=offset_cam2,
        )
    except Exception as e:  # noqa: BLE001
        log.warning(f"[pre-frames] pré-extração falhou (não-fatal): {e}")

    # Fase 54: com o cache aquecido, o BINÁRIO do vídeo já cumpriu seu papel —
    # apagá-lo é o que mantém a campanha de 30 dias dentro de 1GB. A linha em
    # `videos` e os JPEGs de __frames/ permanecem. Não-fatal: falhar aqui só
    # significa bucket maior, nunca dado perdido.
    try:
        expirar_binarios_do_video(
            sb, video_id, caminho_storage,
            frames_ok=bool(frames_stats.get("ok")),
            storage_path_sec=storage_path_secundario,
            segmento_id_sec=segmento_id_secundario,
        )
    except Exception as e:  # noqa: BLE001
        log.warning(f"[retencao] expiração do binário falhou (não-fatal): {e}")

    # Fase 18: as sugestões NÃO são mais geradas por vídeo (isso empilhava — 47
    # p/ 24 vídeos). O debouncer as recomputa 1× por rajada, sobre o AGREGADO,
    # de forma curada (recomputar_sugestoes_processo). O `contexto` agregado
    # ainda é montado aqui porque as PERGUNTAS proativas abaixo o consomem.
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
    sugestoes: list = []  # geradas no flush do debouncer (agregado, curadas)

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
        # Fase 23: economia do gate de repetição (None se KV_GATE_ENABLE=off).
        "gate": getattr(etapa_analise_vlm, "_ultima_economia", None) if _GATE_ENABLE else None,
    }
