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
import random
import re
import time
from pathlib import Path as _Path

try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(_Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from groq import Groq
from supabase import Client, create_client
from .productivity import (
    CONFIANCA_COR_GESTOR_MIN,
    LABEL_CONVERSANDO_COLEGA,
    LABEL_CONVERSANDO_GESTOR,
    LABEL_CONVERSANDO_INCERTO,
    TIPO_INTERLOCUTOR_COLEGA,
    TIPO_INTERLOCUTOR_GESTOR,
    TIPO_INTERLOCUTOR_INCERTO,
    decisao_conversa_evidenciada,
)
from .roupa_superior import avaliar_roupa_superior
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
# Fase 64/111A: `KV_TRACKER=fixa` usa a câmera fixa; `reid` ativa aparência
# apenas dentro do vídeo atual. Default e valor desconhecido usam o arquivo de
# fábrica: trocar tracker no meio da campanha é decisão explícita, nunca efeito
# colateral de deploy.
_TRACKER_FIXA = str(Path(__file__).resolve().parent / "trackers" / "botsort_camera_fixa.yaml")
_TRACKER_REID = str(Path(__file__).resolve().parent / "trackers" / "botsort_camera_fixa_reid.yaml")


def selecionar_tracker_config(valor: str | None = None) -> str:
    modo = (os.environ.get("KV_TRACKER", "") if valor is None else valor).strip().lower()
    if modo in ("fixa", "fixed", "camera_fixa") and Path(_TRACKER_FIXA).is_file():
        return _TRACKER_FIXA
    if modo in ("reid", "fixa_reid") and Path(_TRACKER_REID).is_file():
        return _TRACKER_REID
    return "botsort.yaml"


TRACKER_CONFIG = selecionar_tracker_config()

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

-- Fase 85: com qual instrumento o evento foi medido (1 = instante isolado,
-- 2 = sequência por minuto). A quebra da série fica DENTRO do dado.
alter table eventos add column if not exists versao_instrumento int default 1;

-- Fase 86: onde está a máquina em relação à câmera ('camera'|'oposta'|'perfil').
alter table zonas_camera add column if not exists frente_maquina text;

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

    # ⚠️ Fase 100 — CONFIRMAR A ABSTENÇÃO NÃO A PROMOVE. Foi por aqui que o
    # vazamento entrou: 65 eventos `acao_indefinida` confirmados na fila ("sim,
    # é indefinida mesmo") a fizeram virar LABEL CANÔNICO VALIDADO no prompt do
    # cluster, ao lado de `operar_torno`. Confirmar "não sei o que é" é
    # informação sobre a FILA, não sobre o vocabulário.
    _abstencoes = sum(n for l, n in confirmados.items() if rotulo_e_ausencia(l))
    if _abstencoes:
        log.info("Memória: %d confirmação(ões) de ausência de rótulo NÃO entram "
                 "no vocabulário (abstenção não é atividade).", _abstencoes)
    for _l in list(confirmados):
        if rotulo_e_ausencia(_l):
            del confirmados[_l]

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
    identidade_shadow: dict | None = None,
    cam_id: str | None = None,
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

    def _marcar_falha_safety_pendente(erro: str) -> None:
        """Falha fechado quando a cam2 assumiu o safety e não conseguiu medir."""
        if posto_sec is None:
            return
        for am in amostras:
            if (
                not am.pessoas
                and not am.fora_posto
                and am.op_cam2 is None
                and not am.presenca_safety_gate
            ):
                _marcar_presenca_safety(am, {
                    "status": "erro",
                    "motivo": "falha_presence_safety_gate",
                    "erro": erro,
                }, str(cam_id or "cam2"))

    try:
        cap = cv2.VideoCapture(video_path_secundario)
        if not cap.isOpened():
            log.warning(f"2º ângulo: não abriu {video_path_secundario}")
            _marcar_falha_safety_pendente("cam2_nao_abriu")
            return 0
        dur_ms = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 30.0) * 1000.0
        if abs(offset_s) > 0.5:
            log.info(f"[dual-angle] offset de relógio cam1→cam2 = {offset_s:+.0f}s (alinhado pelo nome)")
        rois2 = None
        rois2_maq = None   # Fase 44: zonas 'maquina' da cam2 (mãos no torno)
        for idx, am in enumerate(amostras):
            obs_identidade_cam2 = None
            if identidade_shadow is not None:
                obs_identidade_cam2 = {
                    "cam_id": str(cam_id or "cam2"),
                    "tempo_s": round(float(am.tempo_s), 3),
                    "medido": False,
                    "tracks": {},
                }
                identidade_shadow.setdefault("observacoes", []).append(
                    obs_identidade_cam2
                )
            alvo_ms = (am.tempo_s + offset_s) * 1000.0
            fora_da_cam2 = alvo_ms < 0 or (bool(dur_ms) and alvo_ms > dur_ms)
            if fora_da_cam2:  # instante não existe na cam2 — clampa (só imagem)
                alvo_ms = min(max(0.0, alvo_ms), max(0.0, dur_ms - 1.0))
            cap.set(cv2.CAP_PROP_POS_MSEC, alvo_ms)
            ok, frame = cap.read()
            if not ok or frame is None:
                if posto_sec is not None and not am.pessoas and not am.fora_posto:
                    _marcar_presenca_safety(am, {
                        "status": "erro",
                        "motivo": "falha_presence_safety_gate",
                        "erro": "frame_cam2_indisponivel",
                    }, str(cam_id or "cam2"))
                continue
            # Fase 33: anexa SEMPRE — amostras vazias na cam1 usam esta imagem
            # p/ o RESGATE pela lateral (a cam2 vê o operador que a cam1 não vê).
            am.img_b64_secundario = frame_para_base64(frame)
            n += 1
            # Confirmação do operador pela cam2 (Fase 28) — barata: predict
            # (sem tracker/estado) só nos slots de amostra, imgsz pequeno.
            if posto_sec is None:
                continue
            if fora_da_cam2 or (idx % _CAM2_CONFIRM_STRIDE) != 0:
                if not am.pessoas and not am.fora_posto:
                    _marcar_presenca_safety(am, {
                        "status": "erro",
                        "motivo": "falha_presence_safety_gate",
                        "erro": (
                            "instante_fora_da_cam2" if fora_da_cam2
                            else "slot_cam2_nao_medido_por_stride"
                        ),
                    }, str(cam_id or "cam2"))
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
                if obs_identidade_cam2 is not None:
                    obs_identidade_cam2["medido"] = True
                achou = False
                maos = False
                n_posto2 = 0              # Fase 91: quantas pessoas no posto
                n_cena2 = 0               # Fase 91: quantas pessoas no quadro
                bbox_no_posto = None      # Fase 82: a caixa de quem está no posto
                candidatos_posto2: list[tuple[dict, int, bool]] = []
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
                        # ⭐ A ZONA DO POSTO É LEI, também na lateral. A cam2
                        # tinha a MESMA frouxidão da cam1 — qualquer um dos 17
                        # keypoints dentro do polígono contava — e ela alimenta
                        # `n_posto_cam2`, que vira `pessoas_no_posto` (o máximo
                        # das duas câmeras) e o resgate do operador que a cam1
                        # não vê. Corrigir só a cam1 deixaria o braço estendido
                        # entrando pela porta de trás. `rois2` já é só
                        # `posto_operador`; o que falta é a âncora.
                        if _ZONA_ESTRITA:
                            ax2, ay2 = _ponto_ancora(pessoa2, w2, h2)
                            no_posto2 = any(
                                _ponto_em_roi(ax2, ay2, i["polygon"])
                                for i in rois2.values()
                            )
                        else:
                            pontos2 = _pontos_da_pessoa(pessoa2, w2, h2)
                            no_posto2 = any(
                                _ponto_em_roi(px, py, i["polygon"])
                                for i in rois2.values() for px, py in pontos2
                            )
                        if (
                            obs_identidade_cam2 is not None
                            and ids2 is not None and j < len(ids2)
                        ):
                            obs_identidade_cam2["tracks"][int(ids2[j])] = (
                                "dentro" if no_posto2 else "fora"
                            )
                        n_cena2 += 1
                        if no_posto2:
                            n_posto2 += 1
                            bx1, by1, bx2, by2 = pessoa2["bbox"]
                            area2 = max(0, bx2 - bx1) * max(0, by2 - by1)
                            maos_pessoa = bool(
                                rois2_maq
                                and _maos_na_maquina(pessoa2, rois2_maq, w2, h2)
                            )
                            candidatos_posto2.append((pessoa2, area2, maos_pessoa))
                        # Fase 84: descritor da cam2, do MESMO frame já decodificado
                        # e da MESMA inferência — custo adicional zero.
                        # Detecção sem id não vira descritor: sem chave estável,
                        # a linha seria uma pessoa diferente a cada amostra.
                        if desc_acc is not None and ids2 is not None and j < len(ids2):
                            pessoa2["frame_idx"] = None
                            acumular_descritor(
                                desc_acc, int(ids2[j]), frame=frame, pessoa=pessoa2,
                                w=w2, h=h2, tempo_s=am.tempo_s,
                                # Fase 92: a cam2 mandava papel=None e os 192
                                # tracks do dia saíram sem rótulo — sem ele não
                                # dá nem para medir separabilidade na lateral.
                                no_posto=no_posto2,
                                papel=(
                                    None
                                    if PRODUTIVIDADE_OPERADOR_ESTRUTURADA
                                    else ("operador" if no_posto2 else "visitante")
                                ),
                            )
                    if PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
                        # A lateral só confirma identidade geométrica com um
                        # único ocupante. Duas pessoas não viram "a maior bbox"
                        # e mãos pertencem exclusivamente ao candidato único.
                        if len(candidatos_posto2) == 1:
                            pessoa2, area2, maos = candidatos_posto2[0]
                            achou = True
                            bbox_no_posto = (pessoa2["bbox"], area2)
                        elif len(candidatos_posto2) > 1:
                            achou = None
                            maos = False
                    else:
                        achou = bool(candidatos_posto2)
                        maos = any(c[2] for c in candidatos_posto2)
                        if candidatos_posto2:
                            pessoa2, area2, _ = max(
                                candidatos_posto2, key=lambda c: c[1]
                            )
                            bbox_no_posto = (pessoa2["bbox"], area2)
                    if bbox_no_posto is not None:
                        am.bbox_cam2 = bbox_no_posto[0]
                        am.dim_cam2 = (w2, h2)
                # C1: cam1 já não viu ninguém e o track da lateral não devolveu
                # pessoa dentro. Só agora o detector bruto independente roda no
                # mesmo frame. O resultado não altera op_cam2/contagens/bbox.
                if (
                    not am.pessoas
                    and not am.fora_posto
                    and n_posto2 == 0
                    and not am.presenca_safety_gate
                ):
                    resultado_safety = _presenca_safety_gate(
                        yolo,
                        frame,
                        rois2,
                        w2,
                        h2,
                        conf_min=_CAM2_CONF,
                        imgsz=416,
                        boundary_safety=_CAM2_BOUNDARY_SAFETY,
                    )
                    _marcar_presenca_safety(
                        am, resultado_safety, str(cam_id or "cam2")
                    )
                am.op_cam2 = achou
                am.maos_cam2 = maos
                am.n_posto_cam2 = n_posto2
                am.n_cena_cam2 = n_cena2
            except Exception as e:
                log.warning(f"[operador] confirmação cam2 falhou no slot {am.tempo_s:.0f}s ({e})")
                am.op_cam2 = None
                if not am.pessoas and not am.fora_posto:
                    _marcar_presenca_safety(am, {
                        "status": "erro",
                        "motivo": "falha_presence_safety_gate",
                        "erro": f"confirmacao_cam2: {type(e).__name__}: {e}"[:240],
                    }, str(cam_id or "cam2"))
        cap.release()
    except Exception as e:
        log.warning(f"2º ângulo falhou ({e}) — segue só com a cam1")
        _marcar_falha_safety_pendente(
            f"cam2_abortou: {type(e).__name__}: {e}"[:240]
        )
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
        if PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
            # A frontal produz candidatos, não identidade. A lateral só abre
            # resgate quando a frontal realmente não viu pessoa; havendo
            # candidato, os dois ângulos alimentam a mesma decisão visual.
            rebaixaria = False
            resgataria = (
                politica == "dupla"
                and not am.pessoas
                and am.op_cam2 is True
            )
        else:
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
             "inconclusivos": 0, "rebaixados": 0,
             "resgatados_cam2": 0, "pontes": 0,
             "safety_vetos": 0, "safety_erros": 0}
    for am, op_cam1, rebaixaria, resgataria in decisoes:
        if aplicar_negacao and rebaixaria:
            am.pessoas = [p for p in am.pessoas if p.get("papel") != "operador"]
            stats["rebaixados"] += 1
            op_cam1 = False
        if PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
            if am.pessoas:
                # A identidade será decidida pelo contrato visual estruturado.
                presente = None
            elif politica == "dupla":
                # Triestado: True/False são medições; None significa que a
                # lateral não mediu (stride, defasagem ou falha).
                presente = am.op_cam2 if isinstance(am.op_cam2, bool) else None
            else:
                # Política explicitamente cam1: quadro vazio é a própria medida.
                presente = False
        else:
            presente = op_cam1 or resgataria
        # C1 não estabelece presença/identidade. Se nenhuma câmera trouxe uma
        # presença normal, o detector bruto (ou sua falha técnica) transforma a
        # afirmação de ausência em abstenção, nunca em `True`.
        if getattr(am, "presenca_safety_gate", False) and presente is not True:
            presente = None
            if am.presenca_safety_motivo == "falha_presence_safety_gate":
                stats["safety_erros"] += 1
            else:
                stats["safety_vetos"] += 1
        if resgataria and not op_cam1:
            stats["resgatados_cam2"] += 1
        am.operador_presente = presente
        am.operador_ponte = False

    # C3: o candidato fraco/moderado só pode vetar `posto_vazio` depois de
    # CAM1 e CAM2 normais terem terminado. A ponte vem depois e não pode
    # transformar este veto em presença.
    stats["c3_vetos"] = _aplicar_c3_confidence_temporal(amostras)
    stats["safety_vetos"] += stats["c3_vetos"]

    # Fase 34: PONTE TEMPORAL — o operador não se teletransporta. Ausência de
    # até _OPERADOR_GAP_SLOTS slots ENTRE duas presenças vira presença (o
    # YOLO "pisca" em oclusão momentânea; cada piscada virava posto_vazio).
    if _OPERADOR_GAP_SLOTS > 0 and len(amostras) > 2:
        pres = [bool(a.operador_presente) for a in amostras]
        for i, a in enumerate(amostras):
            # C1 é uma abstenção deliberada, não um buraco de tracking. A ponte
            # não pode convertê-la em presença e, por consequência, atividade.
            if pres[i] or getattr(a, "presenca_safety_gate", False):
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
        if am.operador_presente is True:
            stats["presentes"] += 1
        elif am.operador_presente is False and not am.pessoas:
            stats["vazios"] += 1
        elif am.operador_presente is None:
            stats["inconclusivos"] += 1
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
# C4.2 — segunda opinião opcional, somente para candidatos reais a posto vazio.
_C42_IMGSZ = 640
_C42_DELTA_MAX_S = 8.01
# C2 — extensão geométrica conservadora da C1, exclusiva da CAM2. A margem é
# deliberadamente fixa até haver evidência suficiente para configuração.
_CAM2_BOUNDARY_SAFETY = True
_CAM2_BOUNDARY_MARGIN_RATIO = 0.05
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
POSTO_INCONCLUSIVO_TID = -3
POSTO_INCONCLUSIVO_DESC = "presença do operador não confirmada"
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


# ═════════════════════════════════════════════════════════════════════════
# ⭐ A ZONA DO POSTO É LEI. Só é analisado quem está DENTRO dela.
#
# Duas frouxidões somadas faziam a zona não ser respeitada na prática:
#
#  1. QUALQUER PONTO DO CORPO contava. Os 17 keypoints entravam no teste, então
#     um pé esticado ou um braço estendido encostando na borda do polígono já
#     classificava a pessoa como "no posto". Alguém parado AO LADO do posto,
#     de braço estendido, virava o operador. A regra nasceu para sobreviver à
#     oclusão pelo torno, mas a ÂNCORA (ombros → um ombro → nariz → topo do
#     tronco) já resolve oclusão sem abrir a mão da localização: ela diz ONDE A
#     PESSOA ESTÁ, não até onde ela alcança.
#
#  2. A ZONA `interacao` CLASSIFICAVA PESSOA. Quem passava por ali virava
#     `visitante`, gerava evento, descrição e card de validação — sem nunca ter
#     estado no posto. O gestor via na fila gente que não é do posto dele.
#
# Agora: só o `posto_operador`, e só pela âncora. Quem está fora é descartado
# ANTES de virar pessoa, evento ou métrica. Visitante continua existindo — mas
# só o visitante que está DENTRO do posto, que é o caso real de duas pessoas
# disputando a estação.
#
# `KV_ZONA_ESTRITA=off` volta ao comportamento antigo, para comparar o número
# dos dois jeitos no mesmo dia.
# ═════════════════════════════════════════════════════════════════════════
_ZONA_ESTRITA = os.environ.get("KV_ZONA_ESTRITA", "on").strip().lower() not in (
    "off", "0", "false", "no", "")


# ═════════════════════════════════════════════════════════════════════════
# ⭐ FORA DO POSTO ≠ POSTO VAZIO.
#
# `posto_vazio` significava, literalmente, `am.pessoas == []` — e essa lista já
# passou pelo portão estrito da zona. Uma pessoa 30 cm fora do polígono
# produzia o MESMO objeto que um chão de fábrica deserto: descartada antes de
# virar contador, descritor, recorte ou linha de log. O gestor via "posto
# vazio" num minuto em que o operador estava ali, operando a ponte rolante.
#
# ⚠️ E A RESTRIÇÃO QUE NÃO PODE SER QUEBRADA: há uma semana o mesmo cliente
# exigiu o oposto — "só devemos analisar quem está dentro da zona e ponto
# final" — porque transeuntes inflavam a presença. Este recurso NÃO PODE
# readmitir transeuntes. Por isso a pessoa de fora vai para uma lista
# PARALELA e o `continue` do portão permanece: ela nunca entra em
# `am.pessoas`, e todas as garantias (não ser eleita operador, não virar
# visitante, não entrar em `presenca_zona`, não deslocar a numeração P1..Pn,
# não ser desenhada para o VLM) vêm por CONSTRUÇÃO, não por filtro.
#
# O TESTE DO PASSANTE decide entre os dois casos, e ele é de CONTINUIDADE, não
# de identidade: a Fase 91 mediu que aparência separa operador × visitante por
# +0,025 onde seria preciso ~+0,15. Aparência entra só como VETO.
#
# `sombra` observa sem emitir nada, mas hoje entrega somente contagens por
# amostra nos logs. Elas servem para inspecionar candidatos prospectivamente;
# não preservam intervalos nem autorizam estimar duração/delta multiplicando
# contagens pela cadência.
# ═════════════════════════════════════════════════════════════════════════
_FORA_MODO = os.environ.get("KV_FORA_DO_POSTO", "off").strip().lower()
if _FORA_MODO not in ("off", "sombra", "on"):
    _FORA_MODO = "off"
# Amostras dentro da zona que o track precisa ter acumulado ANTES de sair para
# ser considerado "o operador que saiu". Abaixo disso é transeunte.
_FORA_MIN_ZONA = int(os.environ.get("KV_FORA_MIN_ZONA", "3"))
# Há quanto tempo, no máximo, ele foi visto dentro. Passado isso, quem está
# fora não é mais "o operador que acabou de sair".
_FORA_GAP_S = float(os.environ.get("KV_FORA_GAP_S", "30"))
# Veto de aparência. DELIBERADAMENTE abaixo de `_TIT_SIM_COR` (0,62): aqui a
# cor não identifica ninguém, só recusa o absurdo.
_FORA_SIM_VETO = float(os.environ.get("KV_FORA_SIM_VETO", "0.45"))
# Teto de chamadas de VLM por vídeo para descrever atividade fora do posto.
_FORA_MAX_CHAMADAS = int(os.environ.get("KV_FORA_MAX_CHAMADAS", "40"))

# `papel_pessoa` do operador fora da zona. NÃO pode ser "fora_do_posto": esse é
# o valor literal de `EST_FORA`, e colidir faria o papel ser lido como estado
# de permanência nos logs e em `comparar_arvore`.
PAPEL_OPERADOR_FORA = "operador_fora"
FORA_POSTO_TID = -4          # sentinela de último recurso; o caminho normal
                             # usa o track id REAL (ver a emissão da observação)


def _fora_ativo() -> bool:
    """O recurso só roda com a zona ESTRITA ligada.

    Com `KV_ZONA_ESTRITA=off`, `presenca_zona` contou outra noção de "dentro"
    (qualquer keypoint), e o teste do passante — que se apoia inteiramente
    nesse contador — estaria medindo outra coisa sem avisar.
    """
    if _FORA_MODO == "off":
        return False
    if not _ZONA_ESTRITA:
        return False
    return True


def _zona_da_pessoa(pontos: list[tuple[float, float]], rois: dict,
                    ancora: tuple[float, float] | None = None
                    ) -> tuple[str | None, str | None, str | None]:
    """(nome_zona, papel, descricao) da pessoa.

    ESTRITO (padrão): pertence ao posto se a ÂNCORA — o ponto que representa
    onde a pessoa ESTÁ — cair no polígono do `posto_operador`. Nenhuma outra
    zona classifica pessoa. Fora dele: (None, None, None), e o track é
    descartado.

    FROUXO (`KV_ZONA_ESTRITA=off`): o comportamento antigo — qualquer keypoint
    em `posto_operador` ou `interacao`.
    """
    if _ZONA_ESTRITA:
        alvo = ancora if ancora is not None else (pontos[-1] if pontos else None)
        if alvo is None:
            return (None, None, None)
        for nome, info in rois.items():
            if info.get("papel") != "posto_operador":
                continue
            if _ponto_em_roi(alvo[0], alvo[1], info["polygon"]):
                return (nome, "posto_operador", info.get("descricao_contexto"))
        return (None, None, None)

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


# C1 — PRESENCE SAFETY GATE. O resultado de `YOLO.track()` já passou pelo
# estado temporal do BoT-SORT e, portanto, não é prova suficiente de AUSÊNCIA.
# A checagem abaixo usa o detector bruto somente no slot que seria candidato a
# vazio. Ela não cria pessoa, track, papel, identidade ou atividade.
def _callback_eh_tracker(callback) -> bool:
    """Reconhece somente callbacks instalados pelo tracker do Ultralytics."""
    alvo = getattr(callback, "func", callback)  # functools.partial nas versões atuais
    modulo = str(getattr(alvo, "__module__", "") or "")
    return modulo.startswith("ultralytics.") and ".trackers" in modulo


def _predict_sem_tracker(yolo, frame, **kwargs):
    """Executa detector bruto no mesmo modelo sem deixar o tracker filtrar.

    `Model.track()` registra callbacks persistentes no objeto YOLO; chamar
    `predict()` ingenuamente no mesmo objeto continuaria passando pelo tracker.
    Suspendemos SOMENTE esses callbacks e restauramos as listas exatas em
    `finally`. O worker processa um job por vez, então não há inferências
    concorrentes sobre este singleton.
    """
    predictor = getattr(yolo, "predictor", None)
    mapas = []
    vistos = set()
    for dono in (yolo, predictor):
        callbacks = getattr(dono, "callbacks", None)
        if isinstance(callbacks, dict) and id(callbacks) not in vistos:
            vistos.add(id(callbacks))
            mapas.append(callbacks)

    eventos = ("on_predict_start", "on_predict_postprocess_end")
    originais: list[tuple[dict, str, list]] = []
    removidos = 0
    removidos_por_evento = {evento: 0 for evento in eventos}
    tracker_inicializado = predictor is not None and hasattr(predictor, "trackers")
    try:
        for callbacks in mapas:
            for evento in eventos:
                lista = list(callbacks.get(evento) or [])
                filtrada = [cb for cb in lista if not _callback_eh_tracker(cb)]
                n_removidos = len(lista) - len(filtrada)
                removidos += n_removidos
                removidos_por_evento[evento] += n_removidos
                originais.append((callbacks, evento, lista))
                callbacks[evento] = filtrada
        if tracker_inicializado or removidos:
            faltantes = [
                evento for evento, total in removidos_por_evento.items()
                if total == 0
            ]
            if faltantes:
                raise RuntimeError(
                    "callbacks_do_tracker_incompletos:" + ",".join(faltantes)
                )
        return yolo.predict(frame, **kwargs)
    finally:
        for callbacks, evento, lista in reversed(originais):
            callbacks[evento] = lista


def _presenca_safety_gate(
    yolo,
    frame,
    rois_posto: dict,
    w: int,
    h: int,
    *,
    conf_min: float,
    imgsz: int,
    area_min_px: float = 0.0,
    boundary_safety: bool = False,
    capturar_c3: bool = False,
) -> dict:
    """Nega `posto_vazio` se o detector bruto vê âncora forte no posto.

    `boundary_safety` é um opt-in da C2: preserva a âncora como referência,
    mas aceita um caso limítrofe somente com os dois ombros válidos e pelo
    menos um deles dentro da ROI. O retorno continua sendo apenas veto de
    segurança, nunca uma presença positiva.
    """
    try:
        # A C3 compartilha esta única inferência com o gate normal da C1/C2.
        # O detector recebe o piso baixo, mas a lógica normal continua usando
        # `conf_min`; assim as caixas entre 0.08 e o threshold não entram em
        # pessoas nem alteram C1/C2.
        conf_probe = 0.08 if capturar_c3 else float(conf_min)
        resultados = _predict_sem_tracker(
            yolo,
            frame,
            classes=[0],
            conf=conf_probe,
            imgsz=int(imgsz),
            verbose=False,
            save=False,
        )
        resultado = resultados[0] if resultados else None
        boxes_obj = getattr(resultado, "boxes", None)
        if boxes_obj is None or len(boxes_obj) == 0:
            return {"status": "livre", "motivo": "detector_sem_pessoas"}

        conf_obj = getattr(boxes_obj, "conf", None)
        if conf_obj is None:
            raise RuntimeError("detector_sem_confidence")
        boxes = boxes_obj.xyxy.cpu().numpy()
        confs = conf_obj.cpu().numpy()
        kpts_all = None
        keypoints = getattr(resultado, "keypoints", None)
        if keypoints is not None and getattr(keypoints, "xyn", None) is not None:
            kpts_all = keypoints.xyn.cpu().numpy()

        veto_normal = None
        candidato_c3 = None
        for i, box in enumerate(boxes):
            if i >= len(confs):
                raise RuntimeError("confidence_desalinhada")
            confidence = float(confs[i])

            # C3 é somente uma observação transitória: não usa área mínima,
            # não usa keypoint adicional e exige a âncora estritamente dentro.
            if (
                capturar_c3
                and 0.08 <= confidence < float(_OPERADOR_CONF)
            ):
                x1_c3, y1_c3, x2_c3, y2_c3 = (float(v) for v in box[:4])
                pessoa_c3 = {"bbox": (x1_c3, y1_c3, x2_c3, y2_c3)}
                if kpts_all is not None and i < len(kpts_all):
                    pessoa_c3["kpts"] = kpts_all[i]
                ancora_c3 = _ponto_ancora(pessoa_c3, int(w), int(h))
                ancora_dentro = any(
                    info.get("papel") == "posto_operador"
                    and cv2.pointPolygonTest(
                        info["polygon"],
                        (float(ancora_c3[0]), float(ancora_c3[1])),
                        False,
                    ) > 0
                    for info in (rois_posto or {}).values()
                )
                if ancora_dentro and (
                    candidato_c3 is None
                    or confidence > candidato_c3["confidence"]
                ):
                    candidato_c3 = {
                        "confidence": confidence,
                        "bbox": (x1_c3, y1_c3, x2_c3, y2_c3),
                        "ancora": (
                            float(ancora_c3[0]), float(ancora_c3[1])
                        ),
                    }

            if confidence < float(conf_min):
                continue
            x1, y1, x2, y2 = (float(v) for v in box[:4])
            if max(0.0, x2 - x1) * max(0.0, y2 - y1) < float(area_min_px):
                continue
            pessoa = {"bbox": (x1, y1, x2, y2)}
            if kpts_all is not None and i < len(kpts_all):
                pessoa["kpts"] = kpts_all[i]
            ancora = _ponto_ancora(pessoa, int(w), int(h))
            dentro = any(
                info.get("papel") == "posto_operador"
                and _ponto_em_roi(ancora[0], ancora[1], info["polygon"])
                for info in (rois_posto or {}).values()
            )
            if dentro:
                veto_normal = {
                    "status": "veto",
                    "motivo": "veto_posto_vazio_por_deteccao_independente",
                    "confidence": confidence,
                    "bbox": (x1, y1, x2, y2),
                    "ancora": (float(ancora[0]), float(ancora[1])),
                }
                continue
            if not boundary_safety:
                continue

            kpts = pessoa.get("kpts")
            if kpts is None or len(kpts) < 7:
                continue
            ombros = (kpts[5], kpts[6])
            ombros_validos = all(
                len(ombro) >= 2
                and float(ombro[0]) > 0
                and float(ombro[1]) > 0
                for ombro in ombros
            )
            if not ombros_validos:
                continue
            ombros_px = tuple(
                (float(ombro[0]) * int(w), float(ombro[1]) * int(h))
                for ombro in ombros
            )
            ombros_dentro = tuple(
                any(
                    info.get("papel") == "posto_operador"
                    and _ponto_em_roi(ombro[0], ombro[1], info["polygon"])
                    for info in (rois_posto or {}).values()
                )
                for ombro in ombros_px
            )
            if not any(ombros_dentro):
                continue

            margem_px = _CAM2_BOUNDARY_MARGIN_RATIO * min(int(w), int(h))
            for info in (rois_posto or {}).values():
                if info.get("papel") != "posto_operador":
                    continue
                distancia_px = float(cv2.pointPolygonTest(
                    info["polygon"],
                    (float(ancora[0]), float(ancora[1])),
                    True,
                ))
                if veto_normal is None and -margem_px <= distancia_px < 0:
                    veto_normal = {
                        "status": "veto",
                        "motivo": "veto_posto_vazio_por_limite_geometrico",
                        "confidence": confidence,
                        "bbox": (x1, y1, x2, y2),
                        "ancora": (float(ancora[0]), float(ancora[1])),
                        "distancia_borda_px": distancia_px,
                        "margem_borda_px": margem_px,
                        "ombros_dentro": ombros_dentro,
                    }
                    break
        if veto_normal is not None:
            return veto_normal
        resultado = {"status": "livre", "motivo": "sem_ancora_forte_no_posto"}
        if candidato_c3 is not None:
            resultado["c3_candidate"] = candidato_c3
        return resultado
    except Exception as e:  # noqa: BLE001 — erro também deve falhar seguro
        return {
            "status": "erro",
            "motivo": "falha_presence_safety_gate",
            "erro": f"{type(e).__name__}: {e}"[:240],
        }


def _marcar_presenca_safety(am: "Amostra", resultado: dict, camera: str) -> None:
    """Materializa somente o veto transitório e sua telemetria auditável."""
    status = str((resultado or {}).get("status") or "erro")
    if status not in {"veto", "erro"} or am.presenca_safety_gate:
        return
    am.presenca_safety_gate = True
    am.presenca_safety_motivo = str(
        (resultado or {}).get("motivo") or "falha_presence_safety_gate"
    )
    am.presenca_safety_camera = str(camera or "cam1")
    confidence = (resultado or {}).get("confidence")
    am.presenca_safety_confidence = (
        float(confidence) if confidence is not None else None
    )
    bbox = (resultado or {}).get("bbox")
    am.presenca_safety_bbox = tuple(float(v) for v in bbox) if bbox else None
    campos = {
        "presenca_safety_gate": True,
        "cam": am.presenca_safety_camera,
        "tempo_s": round(float(am.tempo_s), 3),
        "motivo": am.presenca_safety_motivo,
        "confidence": am.presenca_safety_confidence,
    }
    for campo in (
        "distancia_borda_px", "margem_borda_px", "ombros_dentro",
        "tier", "vizinhos_fortes", "bbox", "ancora",
    ):
        if campo in (resultado or {}):
            campos[campo] = (resultado or {}).get(campo)
    if status == "erro":
        campos["erro"] = (resultado or {}).get("erro")
        log.warning("[presenca-safety] %s", json.dumps(
            campos, ensure_ascii=False, separators=(",", ":")
        ))
    else:
        log.info("[presenca-safety] %s", json.dumps(
            campos, ensure_ascii=False, separators=(",", ":")
        ))


def etapa_consenso_multicamera_640(
    amostras: list,
    video_path_cam1: str,
    video_path_cam2: str | None,
    yolo,
    rois_cam1: dict | None,
    rois_cam2: dict | None,
    offset_s: float = 0.0,
) -> int:
    """C4.2 — segunda opinião temporal antes de afirmar ``posto_vazio``.

    Só observa slots que, após C1+C2+C3 e a confirmação normal, continuam
    com ``operador_presente is False``. Uma detecção 640 em cada câmera é
    obrigatória; os tempos são comparados na timeline da cam1 e o offset já
    calculado só é usado para localizar a imagem correspondente na cam2.
    Nenhuma detecção desta etapa entra em pessoas, tracks ou identidade.
    Falhas são fail-open para C4.2: o resultado C1/C2/C3 permanece intacto.
    """
    candidatos = [
        (i, am) for i, am in enumerate(amostras or [])
        if not am.pessoas
        and not am.fora_posto
        and am.operador_presente is False
        and not getattr(am, "presenca_safety_gate", False)
    ]
    if not candidatos or not video_path_cam2:
        return 0

    def _tem_posto(rois: dict | None) -> bool:
        return any(
            info.get("papel") == "posto_operador"
            for info in (rois or {}).values()
        )

    # Sem zona de posto não existe evidência C4.2 válida. A checagem também
    # evita pagar inferência 640 quando o par não tem configuração geométrica.
    if not _tem_posto(rois_cam1) or not _tem_posto(rois_cam2):
        return 0

    cap1 = cap2 = None
    cache_cam1: dict[float, dict] = {}
    cache_cam2: dict[float, dict] = {}

    def _ler_e_medir(
        cap,
        tempo_s: float,
        cache: dict[float, dict],
        rois_base: dict,
        camera: str,
        conf_min: float,
        boundary_safety: bool,
    ) -> dict:
        """Lê e mede um instante uma única vez por vídeo/tempo."""
        chave = round(float(tempo_s), 6)
        if chave in cache:
            return cache[chave]
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(tempo_s)) * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                resultado = {
                    "status": "erro",
                    "motivo": "falha_presence_safety_gate",
                    "erro": f"frame_{camera}_indisponivel",
                }
            else:
                altura, largura = frame.shape[:2]
                rois = _build_rois(rois_base, int(largura), int(altura))
                resultado = _presenca_safety_gate(
                    yolo,
                    frame,
                    rois,
                    int(largura),
                    int(altura),
                    conf_min=conf_min,
                    imgsz=_C42_IMGSZ,
                    boundary_safety=boundary_safety,
                )
        except Exception as e:  # noqa: BLE001 — C4.2 é opcional
            resultado = {
                "status": "erro",
                "motivo": "falha_presence_safety_gate",
                "erro": f"{type(e).__name__}: {e}"[:240],
            }
        cache[chave] = resultado
        if resultado.get("status") == "erro":
            log.warning(
                "[presenca-safety/c4.2] inferência 640 falhou na %s em %.3fs: %s",
                camera, float(tempo_s), resultado.get("erro"),
            )
        return resultado

    try:
        cap1 = cv2.VideoCapture(video_path_cam1)
        if not cap1.isOpened():
            log.warning("[presenca-safety/c4.2] não abriu CAM1 %s", video_path_cam1)
            return 0

        # Primeiro passe: CAM1 640 apenas nos candidatos a posto vazio.
        hits_cam1: list[tuple[int, float, dict]] = []
        for indice, am in candidatos:
            resultado = _ler_e_medir(
                cap1, am.tempo_s, cache_cam1, rois_cam1, "cam1",
                _OPERADOR_CONF, False,
            )
            if resultado.get("status") == "veto":
                hits_cam1.append((indice, float(am.tempo_s), resultado))

        # Um único veto 640 não vale nada e, sem hit CAM1, nem abrimos CAM2.
        if not hits_cam1:
            return 0

        cap2 = cv2.VideoCapture(video_path_cam2)
        if not cap2.isOpened():
            log.warning("[presenca-safety/c4.2] não abriu CAM2 %s", video_path_cam2)
            return 0

        fps2 = cap2.get(cv2.CAP_PROP_FPS) or 30.0
        n_frames2 = cap2.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        duracao_cam2 = float(n_frames2) / float(fps2) if n_frames2 else None

        # Segundo passe: CAM2 só nos slots cuja timeline CAM1 fica a <=8.01s
        # de algum hit CAM1. O seek usa exatamente tempo_cam1 + offset_s.
        hits_cam2: list[tuple[int, float, float, dict]] = []
        for indice, am in candidatos:
            tempo_cam1 = float(am.tempo_s)
            if not any(
                abs(tempo_cam1 - tempo_hit_cam1) <= _C42_DELTA_MAX_S
                for _, tempo_hit_cam1, _ in hits_cam1
            ):
                continue
            tempo_cam2 = tempo_cam1 + float(offset_s)
            if tempo_cam2 < 0 or (
                duracao_cam2 is not None and tempo_cam2 > duracao_cam2
            ):
                continue
            resultado = _ler_e_medir(
                cap2, tempo_cam2, cache_cam2, rois_cam2, "cam2",
                _CAM2_CONF, True,
            )
            if resultado.get("status") == "veto":
                # `tempo_cam1` é a posição da evidência na timeline de
                # comparação; `tempo_cam2` é o relógio relativo do vídeo CAM2.
                hits_cam2.append((indice, tempo_cam1, tempo_cam2, resultado))

        pares: list[
            tuple[tuple[int, float, dict], tuple[int, float, float, dict], float]
        ] = []
        for hit1 in hits_cam1:
            for hit2 in hits_cam2:
                delta_s = abs(hit1[1] - hit2[1])
                if delta_s <= _C42_DELTA_MAX_S:
                    pares.append((hit1, hit2, delta_s))
        if not pares:
            return 0

        vetados: set[int] = set()
        for hit1, hit2, delta_s in pares:
            indice1, tempo1, resultado1 = hit1
            indice2, _tempo1_cam2, tempo2, resultado2 = hit2
            for indice in (indice1, indice2):
                if indice in vetados:
                    continue
                am = amostras[indice]
                # Revalidação defensiva: nada que deixou de ser candidato pode
                # receber o veto, mesmo se a lista for modificada futuramente.
                if (
                    am.pessoas
                    or am.fora_posto
                    or am.operador_presente is not False
                    or getattr(am, "presenca_safety_gate", False)
                ):
                    continue
                _marcar_presenca_safety(am, {
                    "status": "veto",
                    "motivo": "veto_posto_vazio_por_consenso_multicamera_640",
                    "confidence": resultado1.get("confidence"),
                    "bbox": resultado1.get("bbox"),
                    "ancora": resultado1.get("ancora"),
                }, "cam1+cam2")
                am.operador_presente = None
                am.operador_ponte = False
                am.presenca_safety_tempo_cam1 = round(float(tempo1), 3)
                am.presenca_safety_tempo_cam2 = round(float(tempo2), 3)
                am.presenca_safety_delta_s = round(float(delta_s), 3)
                am.presenca_safety_confidence_cam1 = resultado1.get("confidence")
                am.presenca_safety_confidence_cam2 = resultado2.get("confidence")
                am.presenca_safety_bbox_cam1 = resultado1.get("bbox")
                am.presenca_safety_bbox_cam2 = resultado2.get("bbox")
                log.info("[presenca-safety/c4.2] %s", json.dumps({
                    "presenca_safety_gate": True,
                    "motivo": am.presenca_safety_motivo,
                    "tempo_cam1_s": am.presenca_safety_tempo_cam1,
                    "tempo_cam2_s": am.presenca_safety_tempo_cam2,
                    "delta_s": am.presenca_safety_delta_s,
                    "confidence_cam1": am.presenca_safety_confidence_cam1,
                    "confidence_cam2": am.presenca_safety_confidence_cam2,
                }, ensure_ascii=False, separators=(",", ":")))
                vetados.add(indice)
        return len(vetados)
    except Exception as e:  # noqa: BLE001 — segunda opinião não derruba o fluxo
        log.warning(
            "[presenca-safety/c4.2] etapa falhou (%s: %s); sem veto C4.2",
            type(e).__name__, e,
        )
        return 0
    finally:
        for cap in (cap2, cap1):
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass


def _guardar_candidato_c3(am: "Amostra", resultado: dict | None) -> None:
    """Guarda somente a telemetria transitória do candidato C3 da CAM1."""
    candidato = (resultado or {}).get("c3_candidate")
    if not isinstance(candidato, dict):
        return
    confidence = candidato.get("confidence")
    bbox = candidato.get("bbox")
    ancora = candidato.get("ancora")
    if confidence is None or not bbox or not ancora:
        return
    am.presenca_c3_confidence = float(confidence)
    am.presenca_c3_bbox = tuple(float(v) for v in bbox)
    am.presenca_c3_ancora = tuple(float(v) for v in ancora)


def _aplicar_c3_confidence_temporal(amostras: list) -> int:
    """Converte candidato C3 em veto somente com continuidade física forte."""
    fortes = [bool(am.pessoas) or am.op_cam2 is True for am in amostras]
    n_vetos = 0
    for i, am in enumerate(amostras):
        if fortes[i] or getattr(am, "presenca_safety_gate", False):
            continue
        confidence = getattr(am, "presenca_c3_confidence", None)
        bbox = getattr(am, "presenca_c3_bbox", None)
        ancora = getattr(am, "presenca_c3_ancora", None)
        if confidence is None or not bbox or not ancora:
            continue
        confidence = float(confidence)
        if not 0.08 <= confidence < float(_OPERADOR_CONF):
            continue
        tier = "moderado" if confidence >= 0.20 else "fraco"
        vizinhos = [j for j in (i - 1, i + 1) if 0 <= j < len(amostras)]
        vizinhos_fortes = [j for j in vizinhos if fortes[j]]
        if tier == "fraco":
            autorizado = (
                len(vizinhos) == 2
                and len(vizinhos_fortes) == 2
            )
        else:
            autorizado = bool(vizinhos_fortes)
        if not autorizado:
            continue
        _marcar_presenca_safety(am, {
            "status": "veto",
            "motivo": "veto_posto_vazio_por_confianca_temporal",
            "confidence": confidence,
            "bbox": bbox,
            "ancora": ancora,
            "tier": tier,
            "vizinhos_fortes": vizinhos_fortes,
        }, "cam1")
        if am.presenca_safety_motivo == "veto_posto_vazio_por_confianca_temporal":
            am.operador_presente = None
            am.operador_ponte = False
            n_vetos += 1
    return n_vetos


# Fase 44 — MÃOS NA MÁQUINA: a zona 'maquina' (torno) desenhada em cima do
# equipamento não classifica a pessoa (é cenário), MAS se um PUNHO do operador
# cai dentro dela, ele está manipulando/operando — mesmo com o TRONCO na zona
# do posto. Sinal geométrico (pose) que desfaz o falso "esperar_ciclo_maquina".
_MAOS_KPTS = (9, 10)   # punhos COCO (esquerdo, direito)


# ── ORIENTAÇÃO ──────────────────────────────────────────────────────────
# Fase 86. "de frente ao torno" virou muleta: aparecia em quase toda descrição
# do dia, inclusive quando o operador estava de COSTAS para o torno lendo
# desenho técnico. O VLM não enxerga orientação nessa resolução e preenche com
# o plausível — o mesmo mecanismo que produzia "monitorando a máquina".
#
# O sinal existe e é grátis: o yolo11n-pose já entrega os 17 keypoints.
_KP_OLHO_E, _KP_OLHO_D, _KP_ORELHA_E, _KP_ORELHA_D = 1, 2, 3, 4


def orientacao_pessoa(pessoa: dict, w: int, h: int) -> str | None:
    """'frente' | 'costas' | 'perfil' em relação à CÂMERA. None sem pose.

    Rosto visível (nariz ou olhos) → de frente. Ombros presentes sem rosto
    nenhum → de costas. Ombros quase colados no eixo x → de perfil,
    independentemente do rosto (é a assinatura de quem está de lado).

    ATENÇÃO: isto é orientação em relação à CÂMERA, que é objetiva. De frente
    para a câmera NÃO é de frente para a máquina — essa tradução depende de
    onde a máquina está, e só a configuração da zona sabe (`frente_maquina`).
    """
    kpts = pessoa.get("kpts")
    if kpts is None or len(kpts) <= _KP_ORELHA_D:
        return None
    om_e = _kp_px(kpts, _KP_OMB_E, w, h)
    om_d = _kp_px(kpts, _KP_OMB_D, w, h)
    if om_e is None or om_d is None:
        return None
    largura_ombros = abs(om_e[0] - om_d[0])
    # A referência de escala NÃO pode ser a distância entre os ombros: ela é a
    # própria grandeza que estamos medindo, e a razão daria ~1 sempre. Usa o
    # TRONCO (rígido) e, sem quadril visível, a altura da caixa.
    tronco = _dist(_meio(om_e, om_d),
                   _meio(_kp_px(kpts, _KP_QUA_E, w, h), _kp_px(kpts, _KP_QUA_D, w, h)))
    if not tronco:
        b = pessoa.get("bbox")
        tronco = (float(b[3]) - float(b[1])) * 0.35 if _bbox_valido(b) else None
    rosto = [i for i in (_KP_NARIZ, _KP_OLHO_E, _KP_OLHO_D)
             if _kp_px(kpts, i, w, h) is not None]
    orelhas = [i for i in (_KP_ORELHA_E, _KP_ORELHA_D)
               if _kp_px(kpts, i, w, h) is not None]

    # Ombros projetados quase no mesmo x = corpo de lado.
    if tronco and largura_ombros < tronco * _PERFIL_RAZAO:
        return "perfil"
    if rosto:
        return "frente"
    if orelhas:
        # Uma orelha só, sem nariz nem olhos: cabeça virada — perfil.
        return "perfil" if len(orelhas) == 1 else "frente"
    return "costas"


# Abaixo desta razão (largura projetada dos ombros ÷ comprimento do tronco) o
# corpo está de lado. Uma pessoa de frente tem ombros ~0.5-0.7 do tronco; de
# perfil a projeção colapsa para perto de zero.
_PERFIL_RAZAO = float(os.environ.get("KV_PERFIL_RAZAO", "0.22"))

# Tradução câmera→máquina, configurada por zona (ver `frente_maquina`).
_FRENTE_MAQUINA_VALIDOS = ("camera", "oposta", "perfil")


def orientacao_vs_maquina(orient_camera: str | None,
                          frente_maquina: str | None) -> str | None:
    """Traduz a orientação OBJETIVA (vs câmera) em orientação vs MÁQUINA.

    Sem `frente_maquina` configurado devolve None — e é de propósito: nesse
    caso o sistema afirma só o que sabe ("de costas para a câmera") e proíbe o
    VLM de afirmar o resto. É o que mata a muleta sem depender de configuração.
    """
    if not orient_camera or frente_maquina not in _FRENTE_MAQUINA_VALIDOS:
        return None
    if frente_maquina == "perfil":
        return None                      # o eixo é perpendicular: não dá para inferir
    if orient_camera == "perfil":
        return "de lado para a máquina"
    if frente_maquina == "camera":
        return "de frente para a máquina" if orient_camera == "frente" else "de costas para a máquina"
    # 'oposta': quem está de costas para a câmera está de frente para a máquina
    return "de costas para a máquina" if orient_camera == "frente" else "de frente para a máquina"


def _e_o_operador_que_saiu(pessoa: dict, *, tempo_s: float, presenca_zona: dict,
                           ultimo_no_posto: dict, desc_acc: dict,
                           frame, candidatos: int) -> tuple[bool, str]:
    """(é o operador que saiu, motivo) para UMA pessoa fora do polígono.

    ⚠️ ESTE É O TESTE QUE IMPEDE O TRANSEUNTE DE VOLTAR. Ele decide se alguém
    fora da zona é o operador que saiu do posto (descrever) ou apenas gente
    passando (ignorar, exatamente como hoje).

    NÃO É IDENTIFICAÇÃO — é CONTINUIDADE. A Fase 91 mediu que aparência separa
    operador × visitante por +0,025 onde seria preciso ~+0,15, com distribuição
    unimodal e sem vale. Então aparência entra só como VETO, no mesmo espírito
    da GUARDA 4 de `ancora_por_continuidade`: "não identifica, rejeita o
    absurdo".

    Quatro condições, todas obrigatórias:
      1. este track FOI MEDIDO dentro da zona neste vídeo (`_FORA_MIN_ZONA`);
      2. RECENTEMENTE (`_FORA_GAP_S`);
      3. ele é o ÚNICO candidato nesta amostra;
      4. a cor não desmente (`_FORA_SIM_VETO`).

    Falhar qualquer uma devolve `passante` — e o fail-closed é o que preserva a
    correção da zona estrita: quem nunca foi medido dentro do polígono neste
    vídeo não pode produzir nada, em hipótese nenhuma.
    """
    tid = pessoa.get("track_id")
    if tid is None:
        return (False, "passante")
    # 1 — foi medido dentro. `presenca_zona` é construído PARA A FRENTE no laço,
    # então aqui ele só contém amostras anteriores: sem lookahead, sem vazamento.
    if presenca_zona.get(tid, 0) < _FORA_MIN_ZONA:
        return (False, "passante")
    # 2 — recentemente.
    visto = ultimo_no_posto.get(tid)
    if visto is None or (tempo_s - visto) > _FORA_GAP_S:
        return (False, "passante")
    # 3 — sem ambiguidade. Dois ex-ocupantes fora ao mesmo tempo (troca de
    # turno) não viram um palpite: viram "não sei".
    if candidatos != 1:
        return (False, "indeciso")
    # 4 — veto de aparência. Se qualquer um dos lados for incomputável, NÃO
    # veta: ausência de medida não é medida, nem a favor nem contra.
    try:
        agora = histograma_cor(frame, pessoa.get("bbox"))
        ref = _media_hist((desc_acc.get(tid) or {}).get("hist_sup") or [])
        sim = _sim_hist((agora or {}).get("sup"), ref)
        if sim is not None and sim < _FORA_SIM_VETO:
            return (False, "indeciso")
    except Exception:   # noqa: BLE001 — veto é opcional, nunca fatal
        pass
    return (True, "operador")


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


# ═════════════════════════════════════════════════════════════════════════
# Fase 85 — SEQUÊNCIA, e o fim do desempate que só tinha saídas produtivas.
#
# O prompt anterior MANDAVA o rótulo produtivo no caso ambíguo:
#   "Se ele está PARADO ... é 'monitorando o ciclo da máquina' ou 'observando
#    a operação' ... Na dúvida entre operar e monitorar, escolha MONITORAR."
# Duas saídas, as duas produtivas. Era por isso que `monitorar_maquina` comia
# 31% do tempo e concentrava 100% da dúvida: a dúvida não tinha para onde ir.
#
# A correção de fundo NÃO é acrescentar rótulos improdutivos — é devolver o
# VLM ao papel dele. "é monitorando" já é uma ESCOLHA DE RÓTULO feita na etapa
# que deveria só DESCREVER. A arquitetura é descrição → cluster → rótulo →
# categoria Lean; com a descrição no lugar, o vocabulário aberto volta a
# funcionar sozinho e a improdutividade nasce sem lista fechada.
#
# ⚠️ Fase 99 — OS EXEMPLOS DE ESTADO DA MÁQUINA SAÍRAM. Eles ensinavam o
# modelo a afirmar "máquina em ciclo"/"máquina parada", e a Fase 89 provou que
# ele não consegue julgar isso a partir de imagem parada: o estado afirmado
# não tinha persistência entre minutos seguidos. Exemplo é a instrução mais
# forte de um prompt — enquanto a frase estivesse aqui, a proibição lá embaixo
# competia com ela e perdia.
#
# A sequência é o que torna imobilidade OBSERVÁVEL: três frames idênticos ao
# longo de 24s são evidência; um frame só nunca foi.
# ═════════════════════════════════════════════════════════════════════════
_BLOCO_EXEMPLOS_DESCRICAO = """EXEMPLOS DE DESCRIÇÃO (o que se vê, não o julgamento):
- "operando o torno, mãos na peça"
- "parado junto ao torno, sem tocar em nada"
- "parado ao lado do torno, braços abaixados"   (orientação só quando o CONTEXTO informa)
- "medindo a peça com paquímetro"
- "de costas para o posto, mexendo no celular"
- "conversando com colega, sem tocar na máquina"
- "sem mudança de posição na sequência, olhando para o lado"
- "andando entre o posto e a bancada, mãos vazias"
"""

_REGRAS_DESCRICAO_V8 = """- Descreva o OBSERVÁVEL, em UMA FRASE CURTA (até 10 palavras) por instante.
  NÃO classifique o trabalho como produtivo ou improdutivo, e não escolha
  rótulos de eficiência: isso é decidido depois, por outra etapa, a partir da
  sua descrição.
- AUSÊNCIA DE MUDANÇA É UMA OBSERVAÇÃO, não uma falha da imagem. Se a pessoa
  está na mesma posição em todas as imagens, diga isso. NÃO preencha com a
  ação mais provável nem com a ação anterior.
- ⚠️ NÃO AFIRME O ESTADO DA MÁQUINA. Nada de "máquina parada", "em ciclo",
  "torno rodando", "eixo girando". Fase 89: isso foi MEDIDO e o estado que
  você afirmava não tinha persistência entre minutos seguidos — trocava como
  cara ou coroa. Um torno em ciclo e um parado são idênticos num quadro; a
  diferença é MOVIMENTO, e imagem parada não tem movimento. Quem mede a
  máquina é sensor. Descreva a PESSOA.
- Só diga que ele OPERA, manipula, prepara, ajusta ou mede se você VÊ as MÃOS
  dele na máquina, na ferramenta ou na peça, em ação, em alguma das imagens.
- EXCEÇÃO (o CONTEXTO manda): se o CONTEXTO disser que ele está com as MÃOS na
  máquina, isso vem da posição REAL das mãos (sensor) — ele ESTÁ operando,
  mesmo que o corpo pareça parado.
- ORIENTAÇÃO NÃO SE ADIVINHA. Só diga que a pessoa está de frente, de costas ou
  de lado para alguma coisa se o CONTEXTO disser — ele vem da pose (sensor). Se
  o CONTEXTO não disser, NÃO mencione orientação nenhuma na descrição, e em
  hipótese alguma escreva "de frente ao torno" por hábito.
- Se não der para dizer o que acontece, escreva "ação não identificada".
- NÃO invente o que não está visível."""


_REGRAS_DESCRICAO_V9 = """- Descreva o OBSERVÁVEL, em UMA FRASE CURTA (até 10 palavras) por instante.
  Não esconda julgamento dentro da frase e não escolha rótulo Lean. A decisão
  binária fica SOMENTE no campo `trabalho`, usando a regra de produtividade
  declarada abaixo do contexto.
- AUSÊNCIA DE MUDANÇA É UMA OBSERVAÇÃO, não uma falha da imagem. Se a pessoa
  está na mesma posição em todas as imagens, diga isso. NÃO preencha com a
  ação mais provável nem com a ação anterior.
- ⚠️ NÃO AFIRME O ESTADO DA MÁQUINA. Nada de "máquina parada", "em ciclo",
  "torno rodando", "eixo girando". Fase 89: isso foi MEDIDO e o estado que
  você afirmava não tinha persistência entre minutos seguidos — trocava como
  cara ou coroa. Um torno em ciclo e um parado são idênticos num quadro; a
  diferença é MOVIMENTO, e imagem parada não tem movimento. Quem mede a
  máquina é sensor. Descreva a PESSOA.
- Só diga que ele OPERA, manipula, prepara, ajusta ou mede se você VÊ as MÃOS
  dele na máquina, na ferramenta ou na peça, em ação, em alguma das imagens.
- EXCEÇÃO (o CONTEXTO manda): se o CONTEXTO disser que ele está com as MÃOS na
  máquina, isso vem da posição REAL das mãos (sensor) — ele ESTÁ operando,
  mesmo que o corpo pareça parado.
- ORIENTAÇÃO NÃO SE ADIVINHA. Só diga que a pessoa está de frente, de costas ou
  de lado para alguma coisa se o CONTEXTO disser — ele vem da pose (sensor). Se
  o CONTEXTO não disser, NÃO mencione orientação nenhuma na descrição, e em
  hipótese alguma escreva "de frente ao torno" por hábito.
- Se não der para dizer o que acontece, escreva "ação não identificada".
- NÃO invente o que não está visível."""


# ── O DISCRIMINADOR ─────────────────────────────────────────────────────
# Fase 86. O par calibrador da Fase 85 sobrevivia à DESCRIÇÃO e morria no
# CLUSTER: "parado ... máquina em ciclo" e "parado ... máquina parada" viravam
# o mesmo `monitorar_maquina`. O prompt do cluster foi escrito para colapsar
# SINÔNIMOS ("digitando no PC" = "operando o computador") e manda usar labels
# de AÇÃO "não da localização" — então o estado da máquina lia-se como enfeite.
#
# A correção não é pedir ao cluster que não colapse: é tornar o colapso
# IMPOSSÍVEL. O estado da máquina e a imobilidade saem da frase e viram CAMPOS;
# as descrições são PARTICIONADAS por eles antes de chegar à LLM; e o sufixo do
# label é aplicado por código depois. Duas situações opostas não caem no mesmo
# grupo porque nunca estiveram na mesma lista.
_MAQUINA_VALIDOS = ("ciclo", "parada")

# ── CACHE DE CONSISTÊNCIA DO CLUSTER ────────────────────────────────────
# Fase 86. O cluster roda POR VÍDEO: `mapa_descricao_label` é local, e cada
# vídeo re-agrupa do zero, com lista diferente, num modelo estocástico. Daí a
# mesma frase virar `monitorar_maquina` em três eventos e `lendo_desenho_tecnico`
# em dois — não é bug, é o desenho.
#
# ⚠️ ISTO NÃO É A FASE 67. Lá o problema era propagar uma DECISÃO HUMANA por
# semelhança SEMÂNTICA e reescrever labels ("descrições parecidas"). Aqui:
#   • match EXATO de string normalizada, sem semelhança nenhuma;
#   • origem MÁQUINA (o que o próprio cluster já decidiu antes), não humana;
#   • não reescreve nada — só evita re-perguntar o que já foi perguntado;
#   • não toca `validado_humano` nem `origem_validacao`.
# Atrás de flag mesmo assim, ligada por padrão, para poder desligar sem deploy.
_CACHE_CLUSTER = os.environ.get("KV_CACHE_CLUSTER", "on") not in ("off", "0", "false", "False", "")


def cache_desc_label(sb, empresa: str, processo: str) -> dict[str, str]:
    """{descricao_bruta normalizada → label} do histórico DESTE processo.

    Uma descrição com mais de um label no histórico é DESCARTADA do cache: se o
    passado já foi ambíguo, fixar um dos lados seria escolher no escuro.
    """
    if not _CACHE_CLUSTER:
        return {}
    try:
        linhas = varrer(
            sb, "eventos", "id, descricao_bruta, comportamento_label",
            empresa=empresa, processo=processo,
            ajustes=lambda q: q.not_.is_("descricao_bruta", "null"),
        )
    except Exception as e:   # noqa: BLE001
        log.warning("[cluster] cache não lido (%s) — segue sem.", e)
        return {}
    vistos: dict[str, set] = defaultdict(set)
    for l in linhas:
        d = (l.get("descricao_bruta") or "").strip().lower()
        # ⚠️ Fase 99 — O CACHE LÊ O HISTÓRICO, e o histórico tem 896 eventos
        # com sufixo de estado. Sem esta limpeza acontecia o pior dos dois
        # mundos: o rótulo guardado (`monitorar_maquina_parada`) não casava
        # com a checagem de sufixo logo abaixo, então a descrição ia ao LLM de
        # novo — que a mandava de volta para o vocabulário canônico, com
        # sufixo. O cache não só deixava passar o resíduo: ele PAGAVA uma
        # chamada para reintroduzi-lo.
        lbl = limpar_sufixo_estado((l.get("comportamento_label") or "").strip())
        # ⚠️ Fase 100 — A CATRACA. Aqui estava o motivo de o vazamento ser
        # RAMPA e não degrau: 0,5% → 5,4% → 4,6% → 11,4% → 38,7%.
        #
        # O cache guarda a descrição que teve UM ÚNICO label no histórico. Com
        # o balde contando como label, a primeira vez que uma frase saía
        # `acao_indefinida` — e só isso — ela ficava TRAVADA: dali em diante o
        # cache servia `acao_indefinida` para aquela frase de graça, sem
        # chamar modelo nenhum, para sempre. Não havia nem a chance de o
        # cluster revisar.
        #
        # MEDIDO em 14/08, e é a maior parte do dia: de 330 eventos no balde,
        # 285 (51 das 56 descrições) foram servidos pelo CACHE, determinística
        # e gratuitamente. Só 45 chegaram a passar pelo modelo.
        #
        # (Os outros 45 são o segundo efeito, menor: frases que tinham label
        # de verdade E o balde no histórico — "operador parado junto ao torno,
        # sem manipulação visível" era monitorar_maquina:28 / acao_indefinida:21
        # — viravam AMBÍGUAS e perdiam a proteção do cache. Sem o balde na
        # conta, essa volta a ser monitorar_maquina sem ambiguidade nenhuma.)
        #
        # Abstenção não é evidência do passado: é a ausência dela. Não se
        # guarda, não se serve e não desempata.
        if d and lbl and not _e_desistencia(lbl):
            vistos[d].add(lbl)
    return {d: next(iter(ls)) for d, ls in vistos.items() if len(ls) == 1}


def _maquina_do_vlm(t: dict) -> None:
    """SEMPRE None. Fase 99 — o prompt parou de PEDIR estado de máquina; este é
    o lado de parar de ACEITAR.

    O campo continua sendo lido só para registrar a violação: se o modelo
    volunteer `"maquina": "ciclo"` mesmo com a proibição explícita, queremos ver
    isso no log — não gravado numa coluna, onde viraria a mesma afirmação não
    medida de sempre, só que fora do nome. Nada mede estado de máquina hoje: o
    sensor de movimento mede MOVIMENTO na zona, e isso já vive em
    `movimento_maquina`, medido, separado e com detalhe de qualidade.

    `imovel` NÃO cai junto: imobilidade é da PESSOA, atravessa vários quadros e
    é exatamente o tipo de coisa que o modelo consegue ver."""
    bruto = _normalizar_maquina(t.get("maquina"))
    if bruto:
        log.info("[vlm] o modelo afirmou estado da máquina (%r) mesmo com a "
                 "proibição no prompt — descartado, não vira coluna.", bruto)
    return None


def _normalizar_maquina(v) -> str | None:
    """'ciclo' | 'parada' | None. Qualquer outra coisa vira None — desconhecido
    é uma resposta legítima e não pode ser convertido em discriminador."""
    t = (str(v or "")).strip().lower()
    return t if t in _MAQUINA_VALIDOS else None


# ═════════════════════════════════════════════════════════════════════════
# Fase 88 — A PARTIÇÃO SAI DO RÓTULO E VIRA COLUNA SOB OBSERVAÇÃO
#
# A Fase 86 partiu o cluster pelo estado da máquina e colou o estado no NOME
# do rótulo. Medimos o discriminador contra o próprio dado e ele não mede:
# em minutos adjacentes com a MESMA ação, o estado troca tanto quanto uma
# moeda com a mesma taxa-base (operar_torno 34,5% × 28,7% esperado;
# monitorar_maquina 41,7% × 30,9%). Estado físico de máquina não se comporta
# assim — o VLM está DEDUZINDO o estado da ação que ele mesmo descreveu
# (76% "ciclo" quando opera, 19% quando monitora) e devolvendo como se
# tivesse observado.
#
# Duas consequências, e a segunda é a cara:
#   1. A partição separava por ruído: o vocabulário triplicava e cada
#      variante nascia sem categoria Lean, ou seja, contando como desperdício.
#   2. O RÓTULO AFIRMA. `monitorar_maquina_parada` diz que a máquina estava
#      parada, e isso vai para relatório que o sócio lê. Afirmação errada
#      custa mais caro que informação faltando.
#
# Então o estado sai do nome e vira COLUNA (`cena_maquina`/`cena_imovel`):
# continua sendo coletado, continua podendo ser analisado, mas não afirma
# nada no rótulo enquanto não houver com o que confrontá-lo. O caminho para
# responder a pergunta de verdade é o movimento medido a 6 fps — não este.
#
# Fica atrás de flag DESLIGADA por padrão em vez de ser removido: a partição
# volta a fazer sentido no dia em que o discriminador for medido, e código
# apagado não volta testado.
# ═════════════════════════════════════════════════════════════════════════
_PARTICAO_CENA = os.environ.get("KV_PARTICAO_CENA", "off") not in (
    "off", "0", "false", "False", "")


def chave_cena(maquina: str | None, imovel: bool | None) -> str:
    """Chave de PARTIÇÃO do cluster. Descrições com chaves diferentes nunca
    entram na mesma chamada, logo não podem ser agrupadas no mesmo label.

    Com `KV_PARTICAO_CENA` desligado devolve SEMPRE a mesma chave: uma
    partição só. Colapsar as partições é o comportamento correto quando o
    discriminador é ruído — era o que o cluster fazia antes da Fase 86.
    """
    if not _PARTICAO_CENA:
        return ""
    return f"{_normalizar_maquina(maquina) or ''}|{'imovel' if imovel else ''}"


def sufixo_cena(maquina: str | None, imovel: bool | None) -> str:
    """Sufixo MECÂNICO do label. Descreve o observado e nada mais.

    Não batiza de `esperar_ciclo` nem de `ocioso`: isso seria a máquina
    decidindo o Lean, que é a decisão do gestor. `maquina_parada` + `imovel` é
    um FATO; se isso é ociosidade ou não, quem diz é quem classifica.

    Aplicado SEMPRE que o discriminador existe — nunca só quando as duas
    variantes coexistem no lote. Se dependesse do conteúdo do vídeo, a mesma
    situação ganharia labels diferentes em dias diferentes, que é exatamente o
    problema de inconsistência que estamos consertando em outro lugar.

    Fase 88: vazio com a partição desligada. O estado passa a viver em
    `cena_maquina`/`cena_imovel`, onde pode ser medido sem afirmar nada.
    """
    if not _PARTICAO_CENA:
        return ""
    m = _normalizar_maquina(maquina)
    partes = []
    if m:
        partes.append(m)
    # A imobilidade só entra como sufixo quando o estado da máquina é
    # DESCONHECIDO. Com a máquina conhecida ela seria redundante e multiplicaria
    # o vocabulário por dois sem separar nada que já não esteja separado.
    elif imovel:
        partes.append("imovel")
    return ("_" + "_".join(partes)) if partes else ""


def _partes_da_chave(ck: str) -> tuple[str | None, bool]:
    """Desfaz `chave_cena` — usado para aplicar o sufixo do lado do cluster."""
    maq, _, imo = (ck or "").partition("|")
    return (maq or None), (imo == "imovel")


def _descricao_com_cena(desc: str, maquina: str | None, imovel: bool | None) -> str:
    """LÁPIDE (Fase 99). Anexava o estado da máquina à descrição do catálogo,
    para o gestor distinguir na tela `x_ciclo` de `x_parada`.

    Com a guarda estrutural, nenhum rótulo carrega estado — nem com
    `KV_PARTICAO_CENA` ligada. Sobrava, então, o pior dos mundos: as duas cenas
    colapsam no MESMO label, e a última partição a rodar sobrescrevia o catálogo
    com "— com a MÁQUINA PARADA". Um rótulo que cobre ciclo E parada passava a
    ser descrito ao gestor como parada. Afirmação não medida, agora em prosa e
    ainda por cima decidida por ordem de iteração.

    Fica como no-op para não reabrir o caminho por engano."""
    return desc


def familia_label(label: str | None) -> str:
    """Label RAIZ de uma família (`monitorar_maquina_ciclo` → `monitorar_maquina`).

    É o que mantém a leitura de tendência íntegra depois do deploy: a SOMA da
    família é comparável entre semanas — julho tem 100% dela sem discriminador,
    agosto tem 40% ciclo / 50% parada / 10% sem —, o que mudou foi a RESOLUÇÃO
    com que sabemos decompor esse tempo, não o tempo.

    Fase 88 — TIRA EM LAÇO, não uma vez só. O LLM do cluster batizava o rótulo
    já com o estado dentro (`monitorar_maquina_parada`) e o sufixo mecânico era
    colado por cima: nasceram `monitorar_maquina_parada_ciclo`,
    `operar_torno_ciclo_ciclo`, `conversando_colega_parada_imovel`. Tirando um
    sufixo só, a família de `monitorar_maquina_parada_ciclo` dava
    `monitorar_maquina_parada` — um IRMÃO, não a raiz —, e a árvore da tela de
    rótulos apontava para o lugar errado. Esses labels existem no histórico e
    continuam existindo depois de a partição ser desligada.
    """
    base = (label or "").strip()
    mudou = True
    while mudou:
        mudou = False
        for suf in ("_ciclo", "_parada", "_imovel"):
            # `_ciclo` sozinho não é rótulo: só descasca enquanto sobrar raiz.
            if base.endswith(suf) and len(base) > len(suf):
                base, mudou = base[: -len(suf)], True
                break
    return base


# ═════════════════════════════════════════════════════════════════════════
# ⭐ A NARRATIVA NÃO PERTENCE A UMA FLAG DE IDENTIFICAÇÃO.
#
# Este bloco vivia SÓ dentro de `PROMPT_VLM_SEQUENCIA` (o caminho V9). O
# `PROMPT_VLM_SEQUENCIA_V8` — que é o PADRÃO, porque
# `KV_PRODUTIVIDADE_OPERADOR_V9` é fail-closed e nasce desligada — nunca pediu
# o campo `resumo`. Com ele ativo, `bruto.get("resumo")` era None SEMPRE, e a
# descrição completa simplesmente não existia.
#
# É por isso que ela aparecia em alguns cards e não em outros: quem decidia não
# era a qualidade da cena, era qual prompt tinha rodado naquele minuto. Duas
# funcionalidades sem relação nenhuma — quem é o operador e o que se viu no
# posto — estavam presas na mesma chave por acidente.
#
# Agora o bloco é UM só e entra nos dois prompts. A narrativa passa a depender
# apenas de `KV_NARRATIVA`, que é o que o nome dela promete.
# ═════════════════════════════════════════════════════════════════════════
_BLOCO_RESUMO = """⚠️ O CAMPO MAIS IMPORTANTE DESTA RESPOSTA é "resumo": a NARRATIVA FIEL de tudo o que se vê ao longo da sequência inteira. As frases por imagem são índices curtos; o "resumo" é a observação de verdade, e é o que uma pessoa vai ler.

COMO ESCREVER O "resumo":
- Percorra as imagens EM ORDEM. Conte a passagem: o que havia na primeira, o que mudou na seguinte, o que permaneceu igual até o fim.
- COBRIR, quando visível: (a) ONDE a pessoa está em relação ao torno e à bancada — à esquerda, à direita, em frente, afastada; (b) O QUE AS MÃOS FAZEM — sobre a máquina, segurando peça ou ferramenta, abaixadas junto ao corpo, fora de vista; (c) A POSTURA e para onde o corpo aponta; (d) O QUE MUDOU entre uma imagem e outra, mesmo que pouco — meio passo, virar o tronco, levantar o braço; (e) OUTRAS PESSOAS, se entram, saem ou permanecem; (f) OBJETOS manipulados.
- NÃO RESUMA e NÃO CONCLUA. Não escolha "a" ação do trecho. Se houve duas coisas, conte as duas, na ordem em que aconteceram.
- SEJA ESPECÍFICO, não econômico. Três a cinco frases é o normal. Um trecho rico merece mais; escrever pouco quando havia o que ver é o pior erro possível aqui.
- Se NADA mudou entre as imagens, diga isso com todas as letras e descreva o estado que se manteve. Permanecer parado é um fato observado, não falta de informação — e é uma resposta tão boa quanto qualquer outra.
- Só o que se VÊ. Nada de rótulo, categoria, produtividade, julgamento, estado da máquina ou suposição sobre a intenção. Se você não vê as mãos, escreva que não vê as mãos — não adivinhe o que elas fazem.

⚠️ QUEM LÊ ISTO É UM DONO DE FÁBRICA, não um técnico. Escreva como se estivesse contando a ele o que aconteceu no posto. Isso PROÍBE, sem exceção:
- NÚMERO DE IMAGEM. Nada de "imagem 3", "nas imagens 0-2", "no frame 5", "na primeira imagem", "a sequência". Use o TEMPO: "no começo", "logo depois", "no meio do trecho", "até o fim", "em seguida", "durante todo o trecho".
- CÓDIGO DE PESSOA. Nada de "P1", "P2", "P3". Diga "o operador" para o titular do posto e "outra pessoa" / "um colega" para os demais — e, se houver como distinguir, use algo visível: "outra pessoa de camiseta roxa".
- VOCABULÁRIO DE SISTEMA. Nada de "contexto", "sensor", "detecção", "câmera lateral", "segundo ângulo", "conforme indicado", "identificação do operador", "área da zona". A pessoa não sabe que existem duas câmeras nem o que é uma zona; ela quer saber o que o funcionário dela estava fazendo.
- Se as duas câmeras mostram a mesma cena, conte UMA história só. Nunca diga "a outra câmera mostra" — junte o que as duas mostram numa narrativa única.

- Exemplo do nível de detalhe E da linguagem esperada: "O operador está de pé à direita do torno, com o corpo voltado para a máquina. No começo do trecho ele está com as duas mãos sobre o equipamento, mexendo na região do carro do torno. Um pouco depois, outra pessoa de camiseta roxa aparece pela esquerda e fica na área da bancada, andando pouco por ali até o fim. O operador não sai do lugar e continua voltado para o torno o tempo todo. Ninguém mais entra no posto e nenhuma peça é carregada."
"""


PROMPT_VLM_SEQUENCIA_V8 = """Você é um analista de processos industriais observando UM posto de trabalho.

Você recebe uma SEQUÊNCIA de {n_frames} imagens da câmera principal, EM ORDEM CRONOLÓGICA, cobrindo {duracao_s} segundos ({intervalo_s}s entre imagens consecutivas).{linha_cam2}

P1 é o OPERADOR TITULAR do posto. P2, P3... são outras pessoas dentro da área do posto. ATENÇÃO: os rótulos são desenhados em CADA imagem separadamente — sempre se refira à pessoa pelo rótulo que aparece NAQUELA imagem.

Sua tarefa é dizer o que aconteceu AO LONGO da sequência, não o que se vê num frame isolado. COMPARE as imagens entre si: o que mudou de uma para a outra? O que ficou igual?

{bloco_processo}{bloco_vocabulario}REGRAS:
{regras}

{exemplos}
CONTEXTO: {contexto_zonas}

NÃO AFIRME O ESTADO DA MÁQUINA. Não diga "máquina parada", "em ciclo", "torno rodando" nem equivalente — nem na descrição, nem em campo nenhum. Você NÃO consegue julgar isso a partir de imagens paradas, e isso foi MEDIDO: o estado que você afirmava não se mantinha entre minutos seguidos, trocava como cara ou coroa. Descreva o que a PESSOA está fazendo; a máquina, quem mede é sensor.

Responda APENAS um JSON com UMA ENTRADA POR IMAGEM, na ordem, onde "i" é o índice da imagem (0 = a primeira).
Devolva também, por imagem:
- "imovel": true se a pessoa está na MESMA posição da imagem anterior, false se mudou.
Responda também "trabalho" por imagem: a atividade descrita é TRABALHO DO POSTO?
- true  = ler desenho técnico, medir peça, buscar ferramenta ou material, organizar bancada, limpar cavaco, conversar SOBRE O SERVIÇO
- false = celular, conversa paralela, parado sem atividade aparente, ausente do enquadramento
- null  = não dá para dizer
Julgue a ATIVIDADE, não a pessoa: a pergunta é se aquilo é serviço do posto, não se ele é produtivo em geral. Na dúvida, null — nunca chute true.

"""  + _BLOCO_RESUMO + """
{{"resumo": "TRÊS A CINCO FRASES contando a sequência em ordem, sem concluir uma ação. É o campo mais longo desta resposta.", "trechos": [{{"i": 0, "acoes": {{"P1": "..."}}, "imovel": true, "trabalho": true}}, {{"i": 1, "acoes": {{"P1": "..."}}, "imovel": false, "trabalho": null}}]}}"""


PROMPT_VLM_SEQUENCIA = """Você é um analista visual observando UM operador de torno mecânico.

Você recebe uma SEQUÊNCIA de {n_frames} imagens da câmera principal, EM ORDEM CRONOLÓGICA, cobrindo {duracao_s} segundos ({intervalo_s}s entre imagens consecutivas).{linha_cam2}

P1, P2, P3... são CANDIDATOS. NÃO presuma que P1 é o operador. A eleição automática pode ter confundido um colega ou visitante. Em CADA imagem, escolha como "operador" a pessoa que ocupa funcionalmente o posto do torno.

EXCEÇÃO AUTORITATIVA: quando o CONTEXTO de uma imagem disser explicitamente que um rótulo é "o OPERADOR", a identidade já foi fixada pela janela completa. Nesse caso, devolva exatamente esse mesmo rótulo em "operador", não reeleja por atividade, postura ou posição e julgue "trabalho" somente para essa pessoa. Só faça a eleição visual quando o CONTEXTO disser que a identidade está em aberto.

IDENTIDADE E PRODUTIVIDADE SÃO PERGUNTAS SEPARADAS. Conversar, virar de costas, usar celular ou ficar improdutivo NÃO transforma o operador em visitante. Use continuidade, roupa, posição habitual e relação funcional com o torno para decidir quem é o ocupante do posto. Só marque visitante quando estiver claro que a pessoa apenas passa, entrega algo ou interage com o ocupante sem assumir o posto. Se isso não estiver claro, use "incerto".

ATENÇÃO: os rótulos são desenhados em CADA imagem separadamente — sempre se refira à pessoa pelo rótulo que aparece NAQUELA imagem. O mesmo rótulo pode representar outra pessoa no quadro seguinte; use posição, roupa, continuidade e relação com o torno, nunca o número P1 por hábito.

Sua tarefa é identificar o operador, descrever somente o OBSERVÁVEL e decidir a produtividade pela regra simples abaixo. COMPARE as imagens entre si: o que mudou de uma para a outra? O que ficou igual?

REGRAS DE DESCRIÇÃO:
{regras}

{exemplos}
CONTEXTO: {contexto_zonas}

NÃO AFIRME O ESTADO DA MÁQUINA. Não diga "máquina parada", "em ciclo", "torno rodando" nem equivalente — nem na descrição, nem em campo nenhum. Você NÃO consegue julgar isso a partir de imagens paradas, e isso foi MEDIDO: o estado que você afirmava não se mantinha entre minutos seguidos, trocava como cara ou coroa. Descreva o que a PESSOA está fazendo; a máquina, quem mede é sensor.

REGRA ÚNICA DE PRODUTIVIDADE DO OPERADOR ESCOLHIDO:
- true: está com a mão no torno/peça/ferramenta OU está voltado para o torno, acompanhando a operação;
- false: está de costas ou de lado para o torno, conversando, no celular ou sem atenção ao posto;
- null: não foi possível identificar o operador ou a evidência visual é insuficiente.
O CONTEXTO de mãos/orientação vem de sensores e prevalece sobre impressão visual. Não use categoria Lean, vocabulário, estado mecânico da máquina ou conhecimento presumido do processo.

Responda APENAS um JSON com UMA ENTRADA POR IMAGEM, na ordem, onde "i" é o índice da imagem (0 = a primeira). Em "acoes", descreva cada pessoa marcada para a decisão ser auditável.

"""  + _BLOCO_RESUMO + """- "operador_estado" deve ser "identificado" quando exatamente um candidato ocupa funcionalmente o posto, "ausente" quando está claro que nenhum candidato é o operador, ou "incerto" quando há oclusão/evidência insuficiente;
- "operador" é obrigatório somente em "identificado" e deve ser um rótulo visível naquela imagem; nos outros estados deve ser null;
- "trabalho" só pode ser true/false em "identificado"; nos outros estados deve ser null;
- "motivo" deve ser um destes valores: "maos_no_torno", "voltado_para_torno", "costas_ou_lado", "conversa_ou_celular", "sem_atividade", "sem_leitura".
- "conversa_estado" deve ser "identificada" SOMENTE quando o operador está claramente conversando e há exatamente um interlocutor marcado; "incerta" quando a conversa é visível mas não dá para associar com segurança a uma única pessoa; "ausente" quando não há conversa;
- "interlocutor" é obrigatório somente em conversa "identificada" e deve ser o rótulo da OUTRA pessoa, nunca o operador. Em conversa incerta/ausente deve ser null. Com duas pessoas possíveis, NÃO escolha arbitrariamente: use "incerta".
- NÃO julgue cor de roupa nem diga quem é gestor. O sistema mede a roupa superior separadamente; você apenas associa a conversa à pessoa certa.

{{"resumo": "TRÊS A CINCO FRASES contando a sequência em ordem, sem concluir uma ação. É o campo mais longo desta resposta.", "trechos": [{{"i": 0, "operador_estado": "identificado", "operador": "P1", "acoes": {{"P1": "mãos no torno, ajustando a peça", "P2": "observando ao lado"}}, "imovel": false, "trabalho": true, "motivo": "maos_no_torno", "conversa_estado": "ausente", "interlocutor": null}}, {{"i": 1, "operador_estado": "identificado", "operador": "P1", "acoes": {{"P1": "conversando ao lado do torno", "P2": "conversando com o operador"}}, "imovel": true, "trabalho": false, "motivo": "conversa_ou_celular", "conversa_estado": "identificada", "interlocutor": "P2"}}]}}"""


PROMPT_VLM_SEQUENCIA_CAM2_V8 = """Você é um analista de processos industriais observando UM posto de trabalho pela CÂMERA LATERAL (com profundidade).
O OPERADOR TITULAR está DENTRO da área de trabalho dele, atrás da máquina — visível nestas imagens (a câmera frontal não o enxerga nestes instantes porque a máquina o esconde).

Você recebe uma SEQUÊNCIA de {n_frames} imagens EM ORDEM CRONOLÓGICA, cobrindo {duracao_s} segundos ({intervalo_s}s entre imagens consecutivas).

Diga o que o operador fez AO LONGO da sequência, não o que se vê num frame isolado. COMPARE as imagens entre si: o que mudou? O que ficou igual?

{bloco_processo}{bloco_vocabulario}REGRAS:
{regras}

{exemplos}
CONTEXTO: {contexto_zonas}

NÃO AFIRME O ESTADO DA MÁQUINA — nem "parada", nem "em ciclo", nem equivalente. Você não consegue julgar isso a partir de imagens paradas, e isso foi medido. Descreva o que a PESSOA faz.

Responda APENAS um JSON com UMA ENTRADA POR IMAGEM, na ordem, onde "i" é o índice da imagem (0 = a primeira).
Devolva também "imovel" (true/false) por imagem — true se a pessoa está na MESMA posição da imagem anterior:
{{"trechos": [{{"i": 0, "acao": "...", "imovel": true}}, {{"i": 1, "acao": "...", "imovel": false}}]}}"""


# ═════════════════════════════════════════════════════════════════════════
# ⭐ O QUE ELE FAZ QUANDO NÃO ESTÁ NO POSTO.
#
# Este prompt existe para um instante em que o polígono do posto está VAZIO e o
# operador está no quadro, em outro lugar. Antes isso era `posto_vazio` e ponto.
#
# ⚠️ ELE NÃO PERGUNTA SE É TRABALHO. Os campos `trabalho`/`motivo` dos outros
# prompts significam, literalmente, "isto é serviço DO POSTO?" — e a pessoa não
# está no posto. Perguntar aqui fabricaria exatamente o julgamento que o gestor
# quer fazer com o próprio olho. A IA descreve; a categoria nasce vazia e espera
# um humano.
# ═════════════════════════════════════════════════════════════════════════
PROMPT_VLM_FORA_POSTO = """Você é um analista de processos industriais observando um operador de torno.

⚠️ ATENÇÃO — ESTA PESSOA NÃO ESTÁ NO POSTO DE TRABALHO DELA. Ela apareceu em outro ponto do galpão, fora da área do torno. Nestas imagens ela está marcada com uma caixa.

Você recebe uma SEQUÊNCIA de {n_frames} imagens EM ORDEM CRONOLÓGICA, cobrindo {duracao_s} segundos ({intervalo_s}s entre imagens consecutivas).

Sua tarefa é UMA só: dizer O QUE ELA ESTÁ FAZENDO ALI. Não julgue se é útil, se é trabalho ou se ela deveria estar no posto — isso quem decide é o gestor da fábrica, olhando a sua descrição.

{bloco_processo}{bloco_vocabulario}REGRAS:
{regras}

{exemplos}
CONTEXTO DO GALPÃO: {contexto_zonas}

NÃO AFIRME O ESTADO DA MÁQUINA — nem "parada", nem "em ciclo", nem equivalente. Você não consegue julgar isso a partir de imagens paradas, e isso foi medido. Descreva o que a PESSOA faz.

O QUE COBRIR na "acao" de cada imagem, quando visível: onde ela está em relação ao torno dela (longe, ao lado, atrás), o que as MÃOS fazem, que EQUIPAMENTO ou OBJETO ela manipula (ponte rolante, empilhadeira, bancada, armário de ferramentas, outra máquina), e se há outra pessoa junto.

⛔ NÃO diga "fora do posto", "ausente", "afastado do posto" nem equivalente — isso o sistema já sabe, e repetir isso no lugar da ação transforma a descrição em nada. Diga O QUE ELA FAZ.
⛔ NÃO responda "ação não identificada" quando der para ver alguma coisa. Se realmente não der para ver o que ela faz, descreva o que dá: a postura, para onde ela olha, para onde ela se desloca.

Responda APENAS um JSON com UMA ENTRADA POR IMAGEM, na ordem, onde "i" é o índice da imagem (0 = a primeira).
"""  + _BLOCO_RESUMO + """
{{"resumo": "TRÊS A CINCO FRASES contando a sequência em ordem, sem concluir uma ação. É o campo mais longo desta resposta.", "trechos": [{{"i": 0, "acao": "operando a ponte rolante, com as duas mãos no controle pendente"}}, {{"i": 1, "acao": "caminhando em direção à bancada de ferramentas, carregando uma peça"}}]}}"""


PROMPT_VLM_SEQUENCIA_CAM2 = """Você é um analista visual observando o posto de um torno pela CÂMERA LATERAL (com profundidade).
As pessoas visíveis são CANDIDATAS. A câmera principal não conseguiu identificar o operador neste instante; não presuma que qualquer pessoa dentro da zona seja o titular.

Você recebe uma SEQUÊNCIA de {n_frames} imagens EM ORDEM CRONOLÓGICA, cobrindo {duracao_s} segundos ({intervalo_s}s entre imagens consecutivas).

Diga o que o operador fez AO LONGO da sequência, não o que se vê num frame isolado. COMPARE as imagens entre si: o que mudou? O que ficou igual?

REGRAS DE DESCRIÇÃO:
{regras}

{exemplos}
CONTEXTO: {contexto_zonas}

NÃO AFIRME O ESTADO DA MÁQUINA — nem "parada", nem "em ciclo", nem equivalente. Você não consegue julgar isso a partir de imagens paradas, e isso foi medido. Descreva o que a PESSOA faz.

IDENTIDADE E PRODUTIVIDADE SÃO PERGUNTAS SEPARADAS. Um operador conversando, de costas ou no celular continua sendo o operador — apenas fica improdutivo. Use "ausente" somente quando estiver claro que a pessoa visível não assumiu o posto; na dúvida use "incerto".

REGRA ÚNICA DE PRODUTIVIDADE:
- true: mão no torno/peça/ferramenta OU voltado para o torno acompanhando a operação;
- false: de costas/de lado, conversando, no celular ou sem atenção ao posto;
- null: evidência insuficiente.

Responda APENAS um JSON com UMA ENTRADA POR IMAGEM, na ordem, onde "i" é o índice da imagem (0 = a primeira). "operador_estado" deve ser "identificado", "ausente" ou "incerto". "trabalho" só pode ser true/false quando identificado; nos demais casos deve ser null. "motivo" deve ser: "maos_no_torno", "voltado_para_torno", "costas_ou_lado", "conversa_ou_celular", "sem_atividade" ou "sem_leitura".
{{"trechos": [{{"i": 0, "operador_estado": "identificado", "acao": "mãos no torno, ajustando a peça", "imovel": false, "trabalho": true, "motivo": "maos_no_torno"}}, {{"i": 1, "operador_estado": "incerto", "acao": "pessoa parcialmente oclusa ao lado", "imovel": true, "trabalho": null, "motivo": "sem_leitura"}}]}}"""


_MOTIVOS_PRODUTIVOS_V9 = frozenset({
    "maos_no_torno", "voltado_para_torno",
})
_MOTIVOS_IMPRODUTIVOS_V9 = frozenset({
    "costas_ou_lado", "conversa_ou_celular", "sem_atividade",
})
_MOTIVOS_V9 = (
    _MOTIVOS_PRODUTIVOS_V9
    | _MOTIVOS_IMPRODUTIVOS_V9
    | {"sem_leitura"}
)


def _trabalho_v9_validado(valor, motivo: str, acao: str | None):
    """Aceita decisão apenas quando JSON, motivo e auditoria concordam."""
    texto = str(acao or "").strip().lower()
    if (
        not isinstance(valor, bool)
        or not texto
        or "ação não identificada" in texto
        or "acao nao identificada" in texto
    ):
        return None
    if valor is True and motivo in _MOTIVOS_PRODUTIVOS_V9:
        return True
    if valor is False and motivo in _MOTIVOS_IMPRODUTIVOS_V9:
        return False
    return None


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
- ⚠️ NÃO EXISTE label de desistência. Não use "acao_indefinida", "indefinido", "outro", "diverso" nem equivalente. Uma pessoa parada ao lado da máquina, sem tocar nela, ESTÁ fazendo algo — está acompanhando a máquina, e isso tem nome. "Sem manipulação visível" descreve as MÃOS, não a ausência de atividade.
- Se uma descrição realmente não permitir nomear ação nenhuma, OMITA-A da resposta. Ela vai para revisão humana. Nunca a jogue num grupo genérico só para não deixá-la de fora.
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


# ═════════════════════════════════════════════════════════════════════════
# Fase 102 — DESCRIÇÃO SEM OBSERVAÇÃO. AUSÊNCIA DE MEDIDA VIRANDO MEDIDA,
# pela QUINTA vez: bbox (0,0,0,0) na Fase 82, MAD=0 com uma amostra na 84,
# share=1,00 em minuto herdado na 97, `acao_indefinida` como vocabulário na
# 100, e agora descrição com `n_amostras = 0`.
#
# É PADRÃO, não caso. A forma é sempre a mesma: um valor calculado sobre nada
# tem a MESMA APARÊNCIA de um valor calculado sobre evidência, e nada no tipo
# de dado distingue os dois. A defesa também é sempre a mesma — o dado tem de
# CARREGAR a própria origem, e quem lê tem de ser obrigado a olhar.
#
# AS ORIGENS (`origem_gate`, por observação; o evento guarda o histograma em
# `observacoes_origem`):
#   analisado              → o quadro FOI ao VLM.                    ✅ observou
#   resgate_cam2           → a cam2 FOI ao VLM naquele instante.     ✅ observou
#   interpolado_sequencia  → o quadro não foi; um vizinho ANALISADO
#                            da MESMA chamada cobriu o instante.     ⚠️ deriva
#   repeticao*             → o gate suprimiu; herdou a ÂNCORA.       ⚠️ deriva
#   indefinida_herdada     → herdou a última ação conhecida.         ❌ afirma
#   ponte_temporal         → herdou SEM ver imagem nenhuma.          ❌ afirma
#   posto_vazio            → ninguém na zona; o detector mediu.      ✅ não afirma
#                            atividade — afirma AUSÊNCIA, e isso é medido.
#
# ⚠️ `resgate_cam2` ESTAVA SENDO CONTADO COMO ZERO, e é um erro do CONTADOR,
# não da descrição: `_analisar_sequencia_cam2` faz uma chamada de visão de
# verdade e olha aquele instante pela lateral. Chamar isso de "sem observação"
# é o espelho do problema que esta fase conserta — negar medida que existe.
#
# A REGRA (a do dono, literal): herança é legítima quando o EVENTO tem pelo
# menos uma amostra analisada — uma olhada seguida de quadros idênticos
# suprimidos é herança honesta. ZERO amostras no evento inteiro, não.
# ═════════════════════════════════════════════════════════════════════════
# `fora_do_posto` entra aqui porque é uma chamada de visão de verdade: o VLM
# olha aquele instante e descreve o que a pessoa está fazendo. Deixá-la de fora
# a marcaria como "sem observação" e o evento nasceria com n_amostras=0 — o
# mesmo erro de contador que a Fase 102 consertou para `resgate_cam2`.
ORIGENS_OBSERVADAS = frozenset({"analisado", "resgate_cam2", "fora_do_posto"})
ORIGENS_DERIVADAS = frozenset({"interpolado_sequencia", "indefinida_herdada",
                               "ponte_temporal"})


def origem_foi_observada(origem: str | None) -> bool:
    """True quando ALGUÉM olhou aquele instante — cam1 ou cam2."""
    return (origem or "analisado") in ORIGENS_OBSERVADAS


def descricao_foi_observada(e: dict) -> bool:
    """True quando o EVENTO tem ao menos uma amostra analisada.

    É o único teste que autoriza a descrição a afirmar o que aconteceu. Note
    que `posto_vazio` não precisa dele: ele não afirma atividade nenhuma.
    """
    if (e.get("papel_pessoa") == "posto_vazio"
            or (e.get("label_corrigido") or e.get("comportamento_label")) == POSTO_VAZIO_LABEL):
        return True
    # Correção humana é observação de gente, que vale mais que a do modelo.
    if e.get("label_corrigido"):
        return True
    return int(e.get("n_amostras") or 0) > 0


def origem_da_descricao(e: dict) -> str:
    """De onde veio a descrição deste evento, para exibir e para medir."""
    if not descricao_foi_observada(e):
        og = e.get("observacoes_origem") or {}
        if og:
            dominante = max(og, key=lambda k: og.get(k) or 0)
            return dominante
        return "desconhecida"
    return "observada"


def descricao_para_exibir(e: dict) -> tuple[str | None, bool]:
    """(texto, observada). Quando NÃO houve observação, a descrição não é
    apresentada como o que aconteceu — ela é substituída por uma frase que diz
    a verdade sobre a própria origem.

    ⚠️ A descrição bruta NÃO é apagada do banco: ela continua auditável, e é
    dela que sai o diagnóstico de por que o sistema errou. O que muda é que
    ela deixa de ser exibida como observação.
    """
    if descricao_foi_observada(e):
        return (e.get("descricao_bruta") or None), True
    return _FRASE_SEM_OBSERVACAO.get(
        origem_da_descricao(e),
        "Nenhum quadro deste minuto foi analisado — o tempo é real, "
        "a atividade não foi observada."), False


_FRASE_SEM_OBSERVACAO = {
    "ponte_temporal": ("Nenhum quadro deste minuto foi analisado. A presença "
                       "veio da continuidade do rastreamento; a atividade não "
                       "foi observada."),
    "indefinida_herdada": ("Nenhum quadro deste minuto foi analisado. O texto "
                          "anterior seria repetido aqui — não é observação."),
    "interpolado_sequencia": ("Nenhum quadro deste minuto foi analisado; a "
                              "descrição viria de um quadro vizinho."),
    "desconhecida": ("Nenhum quadro deste minuto foi analisado — o tempo é "
                     "real, a atividade não foi observada."),
}


def origens_sem_observacao(eventos: list) -> dict:
    """A MEDIDA da Parte 1: quantos eventos afirmam sem ter olhado, por origem.

    Função pura, zero chamada de API. É o que vira endpoint e o que permite
    responder "consertou?" com número em vez de impressão.
    """
    por_origem: dict[str, int] = {}
    total = observados = 0
    for e in eventos or []:
        if e.get("principal") is False:
            continue
        total += 1
        if descricao_foi_observada(e):
            observados += 1
            continue
        por_origem[origem_da_descricao(e)] = por_origem.get(
            origem_da_descricao(e), 0) + 1
    sem = total - observados
    return {
        "total_principais": total,
        "com_observacao": observados,
        "sem_observacao": sem,
        "pct_sem_observacao": round(100.0 * sem / total, 1) if total else 0.0,
        "por_origem": dict(sorted(por_origem.items(), key=lambda kv: -kv[1])),
    }


def sortear_amostra_cega(eventos: list, n: int, semente: int) -> list:
    """Sorteia N eventos para a medição cega. SORTEIO DE VERDADE.

    ⚠️ NÃO FILTRA POR SUSPEITA — nem por dúvida, nem por confiança baixa, nem
    por rótulo feio. Filtrar mediria a desconfiança do gestor, não o sistema, e
    devolveria uma taxa pessimista que pareceria medida.

    Entram apenas eventos que AFIRMAM atividade: `posto_vazio` não tem
    descrição a julgar, e incluí-lo inflaria o acerto com acertos triviais.

    `semente` torna o sorteio reproduzível: dois gestores sorteando o mesmo dia
    julgam o MESMO conjunto, e a taxa passa a ser comparável entre pessoas.
    """
    candidatos = [
        e for e in (eventos or [])
        if e.get("principal") is not False
        and (e.get("descricao_bruta") or "").strip()
        and e.get("papel_pessoa") != "posto_vazio"
        and (e.get("label_corrigido") or e.get("comportamento_label")) != POSTO_VAZIO_LABEL
    ]
    candidatos.sort(key=lambda e: str(e.get("id") or ""))
    rnd = random.Random(semente)
    rnd.shuffle(candidatos)
    return candidatos[:max(0, int(n))]


def taxa_de_acerto(linhas: list) -> dict:
    """A taxa MEDIDA, a partir dos vereditos já dados.

    Os três resultados ficam SEPARADOS. Nenhuma média ponderada que
    transformasse "bate em parte" em meio-acerto: isso inventaria um número
    intermediário que ninguém julgou e apagaria a distinção que diz o que
    consertar.
    """
    julgadas = [l for l in (linhas or []) if l.get("veredito")]
    n = len(julgadas)
    cont = {"bate": 0, "bate_em_parte": 0, "nao_bate": 0}
    for l in julgadas:
        v = l.get("veredito")
        if v in cont:
            cont[v] += 1

    def pct(x):
        return round(100.0 * x / n, 1) if n else 0.0

    # Cruzamento que a Parte 1 torna obrigatório: descrição sem observação
    # deveria acertar MENOS. Se acertar igual, a herança está boa — e isso
    # também é um achado.
    sem_obs = [l for l in julgadas if not int(l.get("n_amostras_no_sorteio") or 0)]
    return {
        "n_julgadas": n,
        "n_pendentes": len([l for l in (linhas or []) if not l.get("veredito")]),
        "bate": cont["bate"], "bate_pct": pct(cont["bate"]),
        "bate_em_parte": cont["bate_em_parte"],
        "bate_em_parte_pct": pct(cont["bate_em_parte"]),
        "nao_bate": cont["nao_bate"], "nao_bate_pct": pct(cont["nao_bate"]),
        "sem_observacao": {
            "n": len(sem_obs),
            "bate_pct": (round(100.0 * len([l for l in sem_obs
                                            if l.get("veredito") == "bate"])
                               / len(sem_obs), 1) if sem_obs else None),
        },
        # ⚠️ Com poucas julgadas a taxa oscila demais para valer como leitura.
        # Dizer isso é parte da medida, não uma ressalva cosmética.
        "confiavel": n >= 20,
    }


def descricoes_que_afirmam_estado(eventos: list) -> dict:
    """A MEDIDA da Parte 2: quantas descrições ainda afirmam estado da máquina.

    Separa por versão do instrumento, porque texto anterior à proibição é
    histórico e não prova que a proibição falhou — só que existe passado.
    """
    n = antigas = novas = 0
    exemplos: list[str] = []
    for e in eventos or []:
        if e.get("principal") is False:
            continue
        d = e.get("descricao_bruta") or ""
        if not texto_afirma_estado_maquina(d):
            continue
        n += 1
        if int(e.get("versao_instrumento") or 0) >= 8:
            novas += 1
            if len(exemplos) < 10:
                exemplos.append(d)
        else:
            antigas += 1
    return {"total": n, "anteriores_a_proibicao": antigas,
            "posteriores_a_proibicao": novas, "exemplos_novos": exemplos}


# ═════════════════════════════════════════════════════════════════════════
# Fase 102 — PROSA DE ESTADO DA MÁQUINA. A Fase 99 baniu o SUFIXO do rótulo;
# faltou o texto. Estado de máquina não é observável em imagem parada — a Fase
# 89 mediu: o estado afirmado não persistia entre minutos consecutivos e era
# função da ação que o próprio modelo tinha acabado de descrever.
#
# Recorta só a AFIRMAÇÃO, nunca a frase inteira: "operador parado junto ao
# torno, com a máquina parada" tem de virar "operador parado junto ao torno",
# não sumir. A observação da PESSOA é boa e é o produto.
# ═════════════════════════════════════════════════════════════════════════
_TRECHOS_ESTADO = (
    r",?\s*(?:e\s+)?(?:com|com\s+a|a)\s+m[áa]quina\s+(?:parada|em\s+ciclo|"
    r"rodando|ligada|desligada|em\s+opera[çc][ãa]o|em\s+funcionamento)",
    r",?\s*(?:com\s+o\s+)?torno\s+(?:parado|girando|rodando|em\s+ciclo|ligado|desligado)",
    r"\s*(?:—|-|,)?\s*m[áa]quina\s+(?:parada|em\s+ciclo)\s*\.?",
    r"\s*durante\s+o\s+ciclo(?:\s+de\s+usinagem)?",
    r"\s*(?:com\s+)?(?:o\s+)?ciclo\s+(?:autom[áa]tico\s+)?em\s+(?:curso|andamento)",
    r",?\s*(?:sem|com)\s+ciclo\s+autom[áa]tico(?:\s+em\s+curso)?",
    r",?\s*aguardando\s+o?\s*ciclo(?:\s+da\s+m[áa]quina)?",
)


def texto_sem_estado_maquina(texto: str | None) -> str:
    """Tira a AFIRMAÇÃO de estado da máquina de uma frase, preservando o resto.

    Devolve "" só quando não sobra observação nenhuma — nesse caso a frase era
    apenas a afirmação não medida, e não há o que preservar.
    """
    t = (texto or "").strip()
    if not t:
        return ""
    for padrao in _TRECHOS_ESTADO:
        t = re.sub(padrao, "", t, flags=re.IGNORECASE)
    # Sobras de pontuação depois do recorte.
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"\s*,\s*(,\s*)+", ", ", t)
    t = t.strip(" ,;—-").strip()
    if t and not t.endswith("."):
        pass
    # Uma frase que virou fragmento inútil não volta ao prompt.
    return t if len(t) >= 12 else ""


def texto_afirma_estado_maquina(texto: str | None) -> bool:
    """True se a frase afirma estado da máquina. É o medidor da Parte 2."""
    return texto_sem_estado_maquina(texto) != (texto or "").strip()


def construir_bloco_vocabulario(memoria: dict, max_itens: int = 20) -> str:
    if not memoria.get("vocabulario"):
        return ""
    # Fase 70: frase queimada não volta como "vocabulário conhecido" — seria
    # ensinar ao VLM a mesma alucinação que o humano acabou de rejeitar.
    _queimadas = set(memoria.get("descricoes_queimadas") or [])
    linhas = [
        "VOCABULÁRIO OPERACIONAL CONHECIDO deste cliente (use estes termos quando a ação corresponder, mantendo consistência com observações anteriores):"
    ]
    # ⚠️ Fase 102 — AQUI ESTAVA O FURO DA PROIBIÇÃO DA FASE 99, e ele explica
    # por que 101 das 495 descrições continuavam afirmando estado da máquina.
    #
    # A Fase 99 filtrou o LABEL (`vocabulario_sem_estado`) e deixou passar a
    # DESCRIÇÃO. Mas o que entra no prompt do VLM é a descrição — e o catálogo
    # está cheio de prosa de estado:
    #   monitorar_maquina: "...durante o CICLO DE USINAGEM, sem manipulação"
    #   deslocamento_interno_posto: "...junto ao torno, COM A MÁQUINA PARADA"
    #
    # E a linha logo acima manda "manter consistência com observações
    # anteriores". Ou seja: o prompt PROIBIA afirmar estado da máquina e, duas
    # linhas depois, entregava exemplos que afirmam estado da máquina com ordem
    # de imitá-los. Exemplo vence regra — é a mesma lição do
    # `_BLOCO_EXEMPLOS_DESCRICAO` na Fase 99, num bloco que ninguém olhou.
    #
    # É também o "cache servindo descrição velha indefinidamente" que o dono
    # suspeitou: não é o cache de rótulo, é este bloco, que lê
    # `comportamentos.descricao` — texto histórico, anterior à proibição, que
    # volta ao prompt em todo vídeo, para sempre, até alguém limpá-lo.
    for v in vocabulario_sem_estado(memoria["vocabulario"])[:max_itens]:
        if (v.get("descricao") or "").strip().lower() in _queimadas:
            continue
        _d = texto_sem_estado_maquina(v.get("descricao"))
        if not _d:
            continue
        linhas.append(f'- {_d}')
    linhas.append("")
    linhas.append(
        "Se reconhecer uma das ações conhecidas, descreva usando vocabulário CONSISTENTE com o catálogo acima. Se for ação genuinamente nova, descreva livremente."
    )
    return "\n".join(linhas) + "\n\n"



# ═════════════════════════════════════════════════════════════════════════
# Fase 99 — O SUFIXO DE ESTADO NÃO PODE NASCER. POR CONSTRUÇÃO.
#
# A Fase 88 desligou a partição e o sufixo DUPLO morreu em 07/08. Mas cinco
# rótulos continuaram nascendo — 896 eventos desde 12/08:
#   monitorar_maquina_parada 370 · operar_torno_parada 257 ·
#   operar_torno_ciclo 119 · conversando_colega_parada 92 ·
#   monitorar_maquina_ciclo 58
#
# A porta que ficou aberta: esses nomes entraram no VOCABULÁRIO CANÔNICO
# enquanto a partição existia, e o prompt do cluster diz "REGRA DURA: se uma
# descrição corresponde a um destes labels, REUSE". O modelo obedecia — e
# passou a colar "parada" em rótulo novo sem nada ter medido estado nenhum.
#
# O rótulo AFIRMA "máquina parada", e a Fase 89 provou que ninguém mediu
# isso: o estado que o VLM afirmava não tinha persistência entre minutos
# consecutivos, trocava como moeda (+0,025 de separação onde um classificador
# precisaria de ~+0,15).
#
# ⚠️ POR ISSO A GUARDA É ESTRUTURAL, NÃO UMA SUGESTÃO AO MODELO. Depender de
# o modelo "não escolher" foi exatamente o que falhou. O sufixo é removido
# DEPOIS que o cluster responde e ANTES de virar rótulo — impossível, não
# improvável.
#
# Histórico NÃO é renomeado: os 896 eventos ficam como estão. Renomear
# reescreve o passado, e a série comparável entre semanas é a da família (que
# `familia_label` já entrega).
# ═════════════════════════════════════════════════════════════════════════
_SUFIXOS_ESTADO = ("_parada", "_parado", "_ciclo", "_imovel", "_imóvel")

# Os cinco que já entraram no vocabulário canônico enquanto a partição
# existia. Ficam BANIDOS de voltar como sugestão ao modelo — mas continuam
# existindo no histórico e nas telas (traduzidos pela família).
ROTULOS_BANIDOS_DO_VOCABULARIO = frozenset({
    "monitorar_maquina_parada", "operar_torno_parada", "operar_torno_ciclo",
    "conversando_colega_parada", "monitorar_maquina_ciclo",
    # os de sufixo duplo, que morreram em 07/08 mas podem estar no catálogo
    "monitorar_maquina_parada_parada", "operar_torno_ciclo_ciclo",
    "conversando_colega_parada_imovel", "monitorar_maquina_parada_imovel",
    "monitorar_maquina_ciclo_ciclo", "operar_torno_parada_parada",
    "monitorar_maquina_parada_ciclo",
})


def limpar_sufixo_estado(label: str | None) -> str:
    """Tira QUALQUER sufixo de estado de máquina de um rótulo, em laço.

    É a guarda estrutural: roda sobre o que o cluster devolve, antes de o nome
    virar rótulo. `monitorar_maquina_parada` → `monitorar_maquina`.

    Nunca esvazia o rótulo: se sobrar só o sufixo, devolve o original (um
    rótulo chamado literalmente `_parada` seria pior que o sufixo).
    """
    base = (label or "").strip()
    if not base:
        return base
    mudou = True
    while mudou:
        mudou = False
        for suf in _SUFIXOS_ESTADO:
            if base.lower().endswith(suf) and len(base) > len(suf):
                base, mudou = base[: -len(suf)], True
                break
    return base or (label or "").strip()


def rotulo_afirma_estado(label: str | None) -> bool:
    """True se o rótulo carrega afirmação de estado da máquina — o que nenhum
    sinal do sistema mede hoje."""
    return limpar_sufixo_estado(label) != (label or "").strip()


# ═════════════════════════════════════════════════════════════════════════
# Fase 100 — `acao_indefinida` NUNCA É VOCABULÁRIO. Esta é a causa raiz do
# vazamento de 14/08 (38,7% do dia), e ela não tem nada a ver com sufixo.
#
# O que aconteceu, medido: 65 eventos `acao_indefinida` foram CONFIRMADOS por
# um humano na fila ("sim, é indefinida mesmo"). `carregar_memoria_do_negocio`
# monta o vocabulário canônico a partir de exatamente isso — label confirmado
# sem correção. Então `acao_indefinida` entrou na lista de LABELS CANÔNICOS
# JÁ VALIDADOS, com a descrição que o catálogo tinha:
#
#     acao_indefinida: "Operador parado próximo ao torno, sem manipulação,
#                       monitoramento ativo ou conversa identificável,
#                       com a máquina parada."
#
# E logo abaixo dela, no mesmo prompt: "REGRA DURA: se uma descrição
# corresponde semanticamente a um destes labels, REUSE o label existente."
#
# O modelo não falhou. Ele OBEDECEU. Demos a ele um balde com nome de
# atividade, uma descrição que casa com "parado junto ao torno, sem
# manipulação visível", e uma ordem para reusar.
#
# O balde não é atividade nenhuma — é a ABSTENÇÃO. Abstenção não se aprende,
# não se confirma e não se sugere. Confirmar "é indefinida mesmo" tem de
# significar "mandei para a fila", nunca "promova a canônica".
# ═════════════════════════════════════════════════════════════════════════
# O carimbo do NÃO-NOMEADO. `comportamento_label` é NOT NULL, então a
# abstenção precisa de algum valor — mas ele não é um rótulo, é o registro de
# que rótulo não houve. Nome novo de propósito: separa o regime novo ("o
# cluster não nomeou, está na fila") do `acao_indefinida` histórico ("o modelo
# escolheu um balde"), sem reescrever o passado.
LABEL_NAO_NOMEADO = "nao_nomeado"

# Os dois valores que significam AUSÊNCIA de rótulo. Nenhum deles é atividade:
# ficam fora da árvore, do Pareto, do vocabulário, da tela e da conta de
# produtividade — e sempre na fila.
ROTULOS_AUSENCIA = frozenset({"acao_indefinida", LABEL_NAO_NOMEADO})
NAO_SAO_VOCABULARIO = ROTULOS_AUSENCIA


def rotulo_e_ausencia(label: str | None) -> bool:
    """True quando o 'rótulo' é, na verdade, a ausência de um."""
    return (label or "").strip() in ROTULOS_AUSENCIA


# Variantes de desistência que o modelo inventa quando o balde canônico some.
# Tirar `acao_indefinida` do prompt sem isto só troca o nome do balde.
_RAIZES_DESISTENCIA = (
    "indefinid", "indeterminad", "nao_identificad", "não_identificad",
    "nao_definid", "desconhecid", "outros", "outro_", "diverso", "generic",
    "sem_acao", "sem_atividade", "nao_classificad", "inconclusiv",
)


def _e_desistencia(label: str | None) -> bool:
    """True se o nome é uma forma de 'não sei' disfarçada de atividade.

    Comparação por RAIZ, não por lista fechada: `acao_indefinida`,
    `atividade_indefinida`, `acao_nao_identificada` e `comportamento_generico`
    são a mesma desistência com roupa diferente."""
    n = (label or "").strip().lower()
    return bool(n) and any(r in n for r in _RAIZES_DESISTENCIA)


def vocabulario_sem_estado(vocab: list) -> list:
    """Filtra o vocabulário que vai ao PROMPT do cluster.

    Tira os banidos, qualquer rótulo com sufixo de estado, e a ABSTENÇÃO. Não
    apaga nada do banco: só deixa de SUGERIR ao modelo o que ele não deveria
    reusar.
    """
    saida = []
    for v in vocab or []:
        lbl = (v.get("label") or "").strip()
        if lbl in ROTULOS_BANIDOS_DO_VOCABULARIO or rotulo_afirma_estado(lbl):
            continue
        if lbl in NAO_SAO_VOCABULARIO:
            log.info("[vocabulario] %r não entra no prompt: é abstenção, não "
                     "atividade — sugeri-la é ensinar o modelo a desistir.", lbl)
            continue
        saida.append(v)
    return saida

def construir_bloco_memoria_cluster(
    memoria: dict,
    max_vocab: int = 25,
    max_correcoes: int = 15,
    max_descartes: int = 10,
) -> str:
    blocos: list[str] = []

    # Fase 99: o vocabulário sugerido ao modelo NÃO pode conter rótulo que
    # afirme estado da máquina. Eles entraram aqui quando a partição existia,
    # e a "REGRA DURA" abaixo os fazia ser reusados para sempre.
    _vocab = vocabulario_sem_estado(memoria.get("vocabulario"))
    if _vocab:
        linhas = ["LABELS CANÔNICOS JÁ VALIDADOS por humanos neste cliente:"]
        for v in _vocab[:max_vocab]:
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
    # Fase 91 — a cam2 passa a CONTAR, não só a dizer sim/não. `op_cam2` é um
    # booleano: com duas pessoas no posto ele diz a mesma coisa que com uma, e
    # a segunda pessoa simplesmente não existia para o sistema. None = a cam2
    # não mediu este slot (stride/fora do vídeo) — que é diferente de zero.
    n_posto_cam2: int | None = None
    n_cena_cam2: int | None = None
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
    # ⭐ Fase 110 — quem está no QUADRO mas FORA do polígono do posto. Lista
    # SEPARADA de propósito: enquanto estiver aqui e não em `pessoas`, ninguém
    # de fora pode ser eleito operador, virar visitante, entrar em
    # `presenca_zona`, deslocar a numeração P1..Pn ou ser desenhado para o VLM.
    # Isso é garantia por construção — a única que não quebra quando alguém
    # mexer no código daqui a três meses.
    fora_posto: list = field(default_factory=list)
    # Frame anotado só com essas pessoas, para a chamada de VLM que descreve o
    # que elas fazem. Nulo quando não há nenhuma (custo zero no caso comum).
    img_b64_fora: str | None = None
    # Um candidato fora da zona que não passou pelo gate continua contando
    # numericamente como posto vazio. O carimbo abaixo preserva a diferença
    # entre "não havia ninguém" e "havia alguém, mas não deu para afirmar que
    # era o operador" sem recolocar essa pessoa em `pessoas`.
    fora_auditoria: str | None = None
    fora_auditoria_amostras_zona: int | None = None
    # Fase 111D — autoridade LOCAL do segmento, resolvida somente depois de a
    # janela inteira ser fechada. Estes campos morrem com a Amostra: não são
    # schema, não são persistidos e nunca atravessam vídeos/câmeras.
    identidade_autoritativa: bool = False
    identidade_estado: str | None = None       # dentro | fora | ausente
    identidade_track_id: int | None = None     # track físico deste slot
    # C1 — Presence Safety Gate. Estes campos são somente telemetria transitória:
    # não criam pessoa/track/papel e não entram em evento ou persistência. True
    # significa apenas que NÃO é seguro afirmar `posto_vazio` neste slot.
    presenca_safety_gate: bool = False
    presenca_safety_motivo: str | None = None
    presenca_safety_camera: str | None = None
    presenca_safety_confidence: float | None = None
    presenca_safety_bbox: tuple | None = None
    # C4.2 — telemetria transitória do par 640 confirmado na timeline da cam1.
    presenca_safety_tempo_cam1: float | None = None
    presenca_safety_tempo_cam2: float | None = None
    presenca_safety_delta_s: float | None = None
    presenca_safety_confidence_cam1: float | None = None
    presenca_safety_confidence_cam2: float | None = None
    presenca_safety_bbox_cam1: tuple | None = None
    presenca_safety_bbox_cam2: tuple | None = None
    # C3 — candidato CAM1 de baixa confiança, sem pessoa/track/papel.
    presenca_c3_confidence: float | None = None
    presenca_c3_bbox: tuple | None = None
    presenca_c3_ancora: tuple | None = None


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

# ── Fase 85 · SEQUÊNCIA e os TETOS DE HERANÇA ───────────────────────────
# Uma amostra a cada 8s, cada uma julgada sozinha: uma foto de alguém em pé
# perto do torno é ambígua por natureza. Agrupar as amostras consecutivas de
# um minuto numa única chamada troca "o que se vê" por "o que aconteceu" — e
# de quebra sai mais barato, porque o texto do prompt passa a ser pago 1× por
# minuto em vez de 7,5×.
_SEQ_ENABLE = os.environ.get("KV_SEQUENCIA_ENABLE", "on") not in ("off", "0", "false", "False", "")
_SEQ_BUCKET_S = float(os.environ.get("KV_SEQUENCIA_BUCKET_S", "60"))
# Teto de imagens por chamada. A API aceita 100 (modelos de 200k) e nós usamos
# 8-12; o teto existe para o caso de intervalo de amostragem pequeno, não por
# limite de plataforma. Acima de 20 imagens/requisição entraria um limite de
# dimensão mais estrito (2000px), que nunca alcançamos.
_SEQ_MAX_IMG = max(2, int(os.environ.get("KV_SEQUENCIA_MAX_IMG", "12")))

# ── OS TRÊS TETOS ───────────────────────────────────────────────────────
# Um princípio, três lugares: HERDAR É ACEITÁVEL POR INSTANTES, NÃO POR
# MINUTOS. Toda herança sem evidência nova tem um limite de quantas amostras
# seguidas pode durar; passado o limite, o sistema volta a OLHAR.
#
# Sem isso, três caminhos independentes fabricam produtividade:
#   1. o gate suprime pose idêntica — que é exatamente o sinal de imobilidade;
#   2. "ação não identificada" herda a última ação conhecida (Fase 34), o que
#      converte DESCONHECIDO em PRODUTIVO;
#   3. a ponte temporal herda sem ver imagem nenhuma.
_GATE_MAX_REPETICOES = max(1, int(os.environ.get("KV_GATE_MAX_REPETICOES", "6")))
_HERANCA_MAX_SEGUIDAS = max(1, int(os.environ.get("KV_HERANCA_MAX_SEGUIDAS", "2")))

# ── VERSÃO DO INSTRUMENTO ───────────────────────────────────────────────
# A Fase 85 muda o instrumento de medição no meio de uma campanha de 30 dias:
# a produtividade ANTES e DEPOIS não são a mesma medida. Carimbar a versão em
# cada evento põe a quebra da série DENTRO DO DADO, onde ela é consultável —
# em vez de depender da memória de alguém ou de uma linha num documento.
#
#   1 = instante isolado; o desempate do prompt só tinha saídas produtivas
#   2 = sequência por minuto; o VLM descreve e não classifica; heranças com teto
#   3 = discriminador de cena (máquina/imobilidade) particiona o cluster;
#       orientação vem da pose; cluster com cache exato e temperatura 0
#   4 = partição de cena DESLIGADA (o discriminador media ruído); o estado da
#       máquina sai do rótulo e vira coluna sob observação; rastro de que as
#       camadas foram avaliadas
#   6 = terceiro estado de trabalho (`modo_operacao`): operação MANUAL deixa
#       de cair como parada; oclusão pesada por ONDE (parte móvel), não só por
#       quanto
#   5 = quadro OLHADO deixa de ser o mesmo que minuto COBERTO: herdada e
#       interpolada mantêm o tempo e não votam na concordância; "não olhei"
#       vira curva própria, separada da dúvida; cam2 só quando desambigua
#   7 = a produtividade vem da PERMANÊNCIA (posição + orientação + julgamento
#       do VLM). O rótulo deixa de decidir; a cadeia Lean sai do caminho
#   8 = (14/08) o NÚMERO PRINCIPAL passa a ser a permanência pura — presença
#       na zona, contada direto, sem VLM e sem rótulo. A descrição vira
#       EVIDÊNCIA (Pareto e fila), não insumo do número. Nenhuma superfície do
#       cliente mostra duração absoluta: a captura amostra ~50% de cada hora,
#       então só o percentual é estimativa correta do turno.
#   9 = caso de uso comercial do torno: P1/P2 são candidatos, o VLM escolhe o
#       ocupante funcional por frame e decide `trabalho` diretamente. Sinais de
#       visitante não entram no operador e identidade ambígua vira inconclusiva.
#  10 = conversa associa um interlocutor estruturado e mede S+V somente no
#       tronco superior dele; cinza seguro vira gestor, demais casos fecham para
#       colega/incerto sem deixar label ou prosa promover produtividade.
#  11 = a identidade lógica local do segmento (111A/B/C) assume autoridade
#       sobre operador/operador_fora antes do VLM, com fallback legado por slot.
def _env_ligada(nome: str, padrao: str = "off") -> bool:
    """Flags críticas são fail-closed: só uma allowlist explícita liga."""
    return os.environ.get(nome, padrao).strip().lower() in {
        "1", "true", "on", "yes",
    }


PRODUTIVIDADE_OPERADOR_V9 = _env_ligada("KV_PRODUTIVIDADE_OPERADOR_V9")
try:
    _VERSAO_LEGADA = int(os.environ.get("KV_VERSAO_INSTRUMENTO", "8"))
except (TypeError, ValueError):
    _VERSAO_LEGADA = 8


# Fase 111B–D — identidade lógica do operador dentro de UM segmento.
_OPERADOR_SEGMENTO_MODO = os.environ.get(
    "KV_OPERADOR_SEGMENTO", "off"
).strip().lower()
if _OPERADOR_SEGMENTO_MODO not in ("off", "sombra", "on"):
    _OPERADOR_SEGMENTO_MODO = "off"


def operador_segmento_autoridade_configurada(
    modo: str | None = None,
    tracker: str | None = None,
    fora: str | None = None,
    tracker_config: str | None = None,
) -> bool:
    """As três chaves da 111D, fail-closed e verificando o YAML real."""
    modo_n = (_OPERADOR_SEGMENTO_MODO if modo is None else modo).strip().lower()
    tracker_n = (
        os.environ.get("KV_TRACKER", "") if tracker is None else tracker
    ).strip().lower()
    fora_n = (_FORA_MODO if fora is None else fora).strip().lower()
    config_n = (
        selecionar_tracker_config(tracker_n)
        if tracker_config is None else str(tracker_config)
    )
    try:
        reid_real = Path(config_n).resolve() == Path(_TRACKER_REID).resolve()
    except (OSError, ValueError, TypeError):
        reid_real = False
    return (
        modo_n == "on"
        and tracker_n in {"reid", "fixa_reid"}
        and reid_real
        and fora_n == "on"
    )


AUTORIDADE_111D_CONFIGURADA = operador_segmento_autoridade_configurada()
# A tríade da 111D é autossuficiente: ela não cria uma quarta chave implícita.
# Sob autoridade, o caminho estruturado anterior também é parte do V11.
PRODUTIVIDADE_OPERADOR_ESTRUTURADA = (
    PRODUTIVIDADE_OPERADOR_V9 or AUTORIDADE_111D_CONFIGURADA
)
# O carimbo acompanha a semântica configurada. Um segmento V11 que caiu em
# fallback continua V11: ele foi medido sob o instrumento novo e a abstenção é
# parte auditável desse instrumento. Histórico nunca é reescrito.
VERSAO_INSTRUMENTO = (
    11 if AUTORIDADE_111D_CONFIGURADA
    else 10 if PRODUTIVIDADE_OPERADOR_V9
    else min(8, _VERSAO_LEGADA)
)


def _operador_segmento_env_float(nome: str, padrao: float,
                                  minimo: float, maximo: float) -> float:
    try:
        valor = float(os.environ.get(nome, str(padrao)))
    except (TypeError, ValueError):
        return padrao
    return valor if minimo <= valor <= maximo else padrao


_OPERADOR_SEG_MIN_TEMPO_POSTO_S = _operador_segmento_env_float(
    "KV_OPERADOR_SEGMENTO_MIN_TEMPO_POSTO_S", 60.0, 0.1, 300.0,
)
_OPERADOR_SEG_MIN_OBS_POSTO = int(_operador_segmento_env_float(
    "KV_OPERADOR_SEGMENTO_MIN_OBS_POSTO", 6.0, 1.0, 300.0,
))
_OPERADOR_SEG_MIN_SHARE = _operador_segmento_env_float(
    "KV_OPERADOR_SEGMENTO_MIN_SHARE", 0.60, 0.0, 1.0,
)
_OPERADOR_SEG_MIN_GAP = _operador_segmento_env_float(
    "KV_OPERADOR_SEGMENTO_MIN_GAP", 0.25, 0.0, 1.0,
)


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
    if frame is None or not hasattr(frame, "shape") or not _bbox_valido(bbox):
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
        # Fase 92: as pontas do track. A costura pergunta "onde este terminou e
        # onde aquele começou?" — o recorte de MAIOR ÁREA (bbox_ref) não serve
        # para isso, porque é o melhor quadro, não a borda.
        "bbox_ini": None, "bbox_fim": None,
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
        _cx = [round((x1 + x2) / 2 / max(1, w), 5), round((y1 + y2) / 2 / max(1, h), 5),
               round(alt / max(1, h), 5)]
        if d["bbox_ini"] is None or tempo_s <= d["t_ini"]:
            d["bbox_ini"] = _cx
        if d["bbox_fim"] is None or tempo_s >= d["t_fim"]:
            d["bbox_fim"] = _cx
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
            # Fase 92: as pontas do track, em coordenada NORMALIZADA
            # [cx, cy, altura_rel] — é o insumo da costura geométrica, e
            # normalizado para não depender da resolução do vídeo.
            "t_ini_s": round(float(d["t_ini"]), 2),
            "t_fim_s": round(float(d["t_fim"]), 2),
            "bbox_ini": d["bbox_ini"],
            "bbox_fim": d["bbox_fim"],
            "frame_w": int(w), "frame_h": int(h),
        })
    return saida


def eleger_operador_segmento(descritores: list[dict]) -> dict:
    """Elege o operador lógico de uma câmera usando a janela completa.

    A função é pura: não lê atividade/VLM, não usa aparência, não persiste e
    não altera os descritores. Todos os gates precisam passar; na dúvida o
    track fica nulo e o estado é ``indefinido``.
    """
    campos = {
        "status": "indefinido",
        "track_id": None,
        "confianca": 0.0,
        "tempo_posto_s": 0.0,
        "tempo_visivel_s": 0.0,
        "share_dominancia": 0.0,
        "gap_segundo": 0.0,
        "n_observacoes": 0,
        "motivo": "sem_evidencia_posto",
    }

    candidatos: list[dict] = []
    for d in descritores or []:
        try:
            track_id = int(d.get("pessoa_track_id"))
            tempo_posto = max(0.0, float(d.get("tempo_posto_s") or 0.0))
            tempo_visivel = max(0.0, float(d.get("tempo_visivel_s") or 0.0))
            n_posto = max(0, int(d.get("n_amostras_posto") or 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if (
            tempo_posto != tempo_posto
            or tempo_visivel != tempo_visivel
            or tempo_posto in (float("inf"), float("-inf"))
            or tempo_visivel in (float("inf"), float("-inf"))
        ):
            continue
        if tempo_posto <= 0.0:
            continue
        candidatos.append({
            "track_id": track_id,
            "tempo_posto_s": tempo_posto,
            "tempo_visivel_s": tempo_visivel,
            "n_observacoes": n_posto,
        })

    if not candidatos:
        return campos

    candidatos.sort(key=lambda d: (-d["tempo_posto_s"], d["track_id"]))
    lider = candidatos[0]
    total_posto = sum(d["tempo_posto_s"] for d in candidatos)
    share = lider["tempo_posto_s"] / total_posto if total_posto > 0.0 else 0.0
    share_segundo = (
        candidatos[1]["tempo_posto_s"] / total_posto
        if len(candidatos) > 1 and total_posto > 0.0 else 0.0
    )
    gap = max(0.0, share - share_segundo)
    campos.update({
        "tempo_posto_s": round(lider["tempo_posto_s"], 1),
        "tempo_visivel_s": round(lider["tempo_visivel_s"], 1),
        "share_dominancia": round(share, 4),
        "gap_segundo": round(gap, 4),
        "n_observacoes": lider["n_observacoes"],
    })

    if (
        lider["tempo_posto_s"] < _OPERADOR_SEG_MIN_TEMPO_POSTO_S
        or lider["n_observacoes"] < _OPERADOR_SEG_MIN_OBS_POSTO
    ):
        campos["motivo"] = "evidencia_insuficiente"
        return campos
    if gap < _OPERADOR_SEG_MIN_GAP:
        campos["motivo"] = "dominancia_ambigua"
        return campos
    if share < _OPERADOR_SEG_MIN_SHARE:
        campos["motivo"] = "dominancia_insuficiente"
        return campos

    campos.update({
        "status": "confirmado",
        "track_id": lider["track_id"],
        # Sem pesos arbitrários: a menor das duas margens relativas é a força
        # diagnóstica da eleição. Os quatro gates acima continuam sendo a lei.
        "confianca": round(min(share, gap), 4),
        "motivo": "dominante_claro",
    })
    return campos


def _registrar_operador_segmento_sombra(
    descritores: list[dict], cameras: list[str],
) -> list[dict]:
    """Registra uma decisão por câmera; nunca alimenta eventos ou métricas."""
    if _OPERADOR_SEGMENTO_MODO != "sombra":
        return []

    por_camera: dict[str, list[dict]] = defaultdict(list)
    for d in descritores or []:
        por_camera[str(d.get("cam_id") or "cam1")].append(d)

    diagnosticos: list[dict] = []
    for camera in dict.fromkeys(str(c or "cam1") for c in cameras):
        try:
            decisao = eleger_operador_segmento(por_camera.get(camera, []))
            diagnostico = {"cam_id": camera, **decisao}
            diagnosticos.append(diagnostico)
            log.info(
                "[operador-segmento] %s",
                json.dumps(diagnostico, ensure_ascii=False, separators=(",", ":")),
            )
        except Exception as exc:  # noqa: BLE001 — sombra nunca derruba produção
            log.warning("[operador-segmento] cam=%s erro=%s", camera, exc)
    return diagnosticos


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
    mapa_movimento: dict | None = None,
    identidade_shadow: dict | None = None,
    presenca_safety_cam1: bool = True,
) -> tuple[list[Amostra], dict, list[int], list[dict], dict, dict]:
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
    # ⭐ Fase 110 — QUANDO cada track foi visto dentro pela última vez. É a
    # segunda metade do teste do passante: `presenca_zona` diz "ele já esteve
    # aqui", este diz "há quanto tempo". Sem os dois, alguém que passou pelo
    # posto às 7h viraria "o operador que saiu" às 15h.
    ultimo_no_posto: dict[int, float] = {}
    # Telemetria prospectiva do recurso. Em `sombra` é a ÚNICA saída, mas
    # estes contadores são por amostra: não formam intervalos nem reproduzem a
    # semântica temporal usada pelas métricas de produção.
    n_fora_visto = n_fora_operador = n_fora_indeciso = n_fora_passante = 0
    # Fase 83: acumulador do descritor por track. Vive só aqui, onde o frame
    # ainda está na mão — depois desta etapa não há mais imagem para tirar cor.
    desc_acc: dict[int, dict] = {}
    # Fase 111C: coletor PARALELO, apenas quando a sombra foi explicitamente
    # ligada. Inclui também detecções fora do posto para permitir a releitura
    # lógica posterior, mas nunca entra em `descritores_track`/persistência.
    desc_acc_identidade: dict[int, dict] | None = (
        {} if identidade_shadow is not None and modo_op else None
    )
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

    # Fase 89: o medidor de movimento vive AQUI porque é aqui que o frame já
    # está decodificado (a 6 fps) e as bboxes do YOLO do mesmo instante estão
    # na mão. Fora deste laço, medir movimento custaria decodificar de novo.
    medidor = MedidorMovimento(rois, w, h, cam_id=cam_id, mapa=mapa_movimento)
    if medidor.ativo:
        log.info("[movimento] medindo na zona '%s' (%s) a ~%.0f fps",
                 medidor.zona_nome, medidor.rect, fps / max(1, track_stride))

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
            # Fase 89 — TODO frame decodificado alimenta o movimento, não só os
            # que viram amostra do VLM. São ~360 pares por minuto contra 7: é a
            # diferença entre conseguir e não conseguir ver "intermitente".
            if medidor.ativo:
                _bbs = []
                try:
                    if results[0].boxes is not None and len(results[0].boxes):
                        _bbs = results[0].boxes.xyxy.cpu().numpy().tolist()
                except Exception:  # noqa: BLE001
                    _bbs = []
                medidor.passo(frame, tempo_s, _bbs)
            if tempo_s >= prox_amostra_s:
                prox_amostra_s += intervalo_s   # consome este slot (~1 amostra / intervalo_s)
                pessoas = []
                observacoes_identidade: dict[int, str] = {}
                detalhes_identidade: dict[int, dict] = {}
                obs_identidade = None
                precisa_frame_identidade = False
                resultado_safety_cam1 = None
                candidato_c3_cam1 = None
                # Fase 110: quem aparece no quadro mas fora do polígono. Vive
                # separada de `pessoas` do começo ao fim.
                fora_frame: list = []
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
                            # ⭐ A ZONA DO POSTO É LEI (ver _zona_da_pessoa).
                            # A ÂNCORA — ombros → um ombro → nariz → topo do
                            # tronco — diz ONDE A PESSOA ESTÁ, sobrevivendo à
                            # oclusão pelo torno sem contar quem só ESTICA um
                            # braço/pé para dentro do polígono. Só o papel
                            # `posto_operador` classifica.
                            pontos = _pontos_da_pessoa(pessoa, w, h)
                            nome_z, papel_z, desc_z = _zona_da_pessoa(
                                pontos, rois, ancora=_ponto_ancora(pessoa, w, h))
                            if desc_acc_identidade is not None:
                                estado_id = (
                                    "dentro" if papel_z == "posto_operador" else "fora"
                                )
                                observacoes_identidade[int(tid)] = estado_id
                                kpts_memoria = pessoa.get("kpts")
                                try:
                                    kpts_memoria = kpts_memoria.tolist()
                                except AttributeError:
                                    pass
                                detalhes_identidade[int(tid)] = {
                                    "track_id": int(tid),
                                    "bbox": tuple(int(v) for v in pessoa["bbox"]),
                                    "kpts": kpts_memoria,
                                    "estado": estado_id,
                                }
                                acumular_descritor(
                                    desc_acc_identidade, int(tid), frame=frame,
                                    pessoa={**pessoa, "frame_idx": frame_idx},
                                    w=w, h=h, tempo_s=tempo_s,
                                    no_posto=(estado_id == "dentro"), papel=None,
                                )
                            # ⭐ Fora do posto = NÃO ENTRA em `pessoas`, antes de
                            # virar operador, visitante, evento ou métrica.
                            #
                            # Fase 110: o `continue` FICA. O que mudou é que a
                            # pessoa passa a ser guardada numa lista PARALELA,
                            # para o teste do passante decidir depois se ela é
                            # o operador que saiu do posto (descrever) ou gente
                            # passando (ignorar, como sempre foi). Guardar não
                            # é admitir: enquanto ela não estiver em `pessoas`,
                            # nada nela pode virar número.
                            if papel_z != "posto_operador":
                                if _fora_ativo() and _bbox_valido(pessoa.get("bbox")):
                                    pessoa["_fora_do_posto"] = True
                                    fora_frame.append(pessoa)
                                continue
                            pessoa["zona"] = nome_z
                            pessoa["zona_desc"] = desc_z
                            pessoa["_papel_zona"] = papel_z
                            # Fase 44: punho na zona 'maquina' → mãos no torno
                            # (operando), mesmo com o tronco no posto.
                            pessoa["maos_maquina"] = _maos_na_maquina(pessoa, rois, w, h)
                            # Fase 86: orientação vs CÂMERA — determinística.
                            pessoa["orientacao"] = orientacao_pessoa(pessoa, w, h)
                        else:
                            pessoa["zona"] = _zona_contexto(cx, cy, rois)
                        if _GATE_ENABLE:
                            pessoa["crop"] = _crop_cinza_pequeno(frame, pessoa["bbox"])
                        pessoa["frame_idx"] = frame_idx
                        pessoas.append(pessoa)
                    if modo_op and pessoas:
                        no_posto = [p for p in pessoas if p["_papel_zona"] == "posto_operador"]
                        for p in no_posto:
                            presenca_zona[p["track_id"]] = presenca_zona.get(p["track_id"], 0) + 1
                            ultimo_no_posto[p["track_id"]] = tempo_s
                        if PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
                            # Na V9 esta etapa só produz CANDIDATOS. Nenhum
                            # track recebe identidade por tempo de zona ou bbox;
                            # a decisão funcional pertence ao contrato visual.
                            for p in pessoas:
                                p["papel"] = None
                            pessoas.sort(
                                key=lambda p: (
                                    p.get("bbox", (0, 0, 0, 0))[0],
                                    p.get("track_id", 0),
                                )
                            )
                        elif no_posto:
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
                        if modo_op and PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
                            # A identidade ainda está em aberto, então a medida
                            # é calculada para todos os candidatos enquanto o
                            # frame existe. Só o track que o contrato estrutural
                            # apontar como INTERLOCUTOR será consumido depois;
                            # a roupa do operador nunca decide a regra.
                            p["roupa_superior"] = avaliar_roupa_superior(
                                frame,
                                p.get("bbox"),
                                kpts=p.get("kpts"),
                                exigir_pose=True,
                            )
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
                if identidade_shadow is not None and modo_op:
                    obs_identidade = {
                        "cam_id": str(cam_id or "cam1"),
                        "tempo_s": round(float(tempo_s), 3),
                        "medido": True,
                        "tracks": dict(observacoes_identidade),
                    }
                    if identidade_shadow.get("guardar_frames"):
                        obs_identidade["pessoas"] = detalhes_identidade
                        obs_identidade["dim"] = (w, h)
                        # Só há reconstrução quando alguém foi visto fora.
                        # O JPEG será a MESMA string já usada pela Amostra
                        # sempre que ela existir; não uma segunda codificação.
                        precisa_frame_identidade = any(
                            d.get("estado") == "fora"
                            for d in detalhes_identidade.values()
                        )
                        obs_identidade["frame_b64"] = None
                        if precisa_frame_identidade:
                            try:
                                obs_identidade["frame_b64"] = frame_para_base64(
                                    frame, qualidade=70
                                )
                            except Exception:  # noqa: BLE001 — fallback legado
                                identidade_shadow["frames_falhos"] = (
                                    int(identidade_shadow.get("frames_falhos") or 0) + 1
                                )
                    identidade_shadow.setdefault("observacoes", []).append(
                        obs_identidade
                    )
                # ⭐ Fase 110 — O TESTE DO PASSANTE, aplicado só quando NINGUÉM
                # está dentro do posto. Com alguém dentro, o minuto é um `cam1`
                # normal e quem está fora é irrelevante — exatamente como antes.
                fora_ok: list = []
                fora_indecisos: list = []
                if fora_frame and not pessoas and _fora_ativo():
                    # Quantos ex-ocupantes recentes há neste instante decide a
                    # regra da unicidade — por isso é contado ANTES do laço.
                    n_cand = sum(
                        1 for p in fora_frame
                        if presenca_zona.get(p.get("track_id"), 0) >= _FORA_MIN_ZONA
                        and ultimo_no_posto.get(p.get("track_id")) is not None
                        and (tempo_s - ultimo_no_posto[p["track_id"]]) <= _FORA_GAP_S
                    )
                    for p in fora_frame:
                        eh, motivo = _e_o_operador_que_saiu(
                            p, tempo_s=tempo_s, presenca_zona=presenca_zona,
                            ultimo_no_posto=ultimo_no_posto, desc_acc=desc_acc,
                            frame=frame, candidatos=n_cand,
                        )
                        p["_fora_motivo"] = motivo
                        p["_fora_amostras_zona"] = presenca_zona.get(p.get("track_id"), 0)
                        if eh:
                            fora_ok.append(p)
                        elif motivo == "indeciso":
                            fora_indecisos.append(p)
                    n_fora_visto += len(fora_frame)
                    if fora_ok:
                        n_fora_operador += 1
                    elif any(p.get("_fora_motivo") == "indeciso" for p in fora_frame):
                        n_fora_indeciso += 1
                    else:
                        n_fora_passante += 1

                # C1/C3: este é o último ponto em que o frame da cam1 ainda
                # existe antes de uma Amostra vazia. O predict independente só
                # roda sem pessoa normal no posto e sem fora_posto válido; o
                # retorno jamais entra em `pessoas`. Em fluxo com cam2, C1
                # continua aguardando a lateral, mas C3 precisa guardar o
                # candidato da CAM1 antes de o frame ser descartado.
                if modo_op and not pessoas and not fora_ok:
                    resultado_probe_cam1 = _presenca_safety_gate(
                        yolo,
                        frame,
                        {
                            nome: info_roi for nome, info_roi in rois.items()
                            if info_roi.get("papel") == "posto_operador"
                        },
                        w,
                        h,
                        conf_min=conf_deteccao,
                        imgsz=imgsz,
                        area_min_px=area_min_px,
                        capturar_c3=True,
                    )
                    candidato_c3_cam1 = resultado_probe_cam1.get("c3_candidate")
                    if presenca_safety_cam1:
                        resultado_safety_cam1 = resultado_probe_cam1
                    elif resultado_probe_cam1.get("status") == "erro":
                        log.warning(
                            "[presenca-c3] falha no probe CAM1 do slot %.3fs: %s",
                            float(tempo_s),
                            resultado_probe_cam1.get("erro") or "erro_desconhecido",
                        )

                if pessoas:
                    # Codifica imediatamente em base64 (mesma pipeline:
                    # anotar→resize→JPEG, com defaults max_lado=1024 e
                    # qualidade=85). Guardamos só a string e descartamos
                    # o numpy do frame, evitando reter ~1–2 GB de RAM em
                    # vídeos longos até a etapa VLM. anotar_frame_com_ids
                    # já copia internamente, então não precisa frame.copy().
                    if (
                        obs_identidade is not None
                        and precisa_frame_identidade
                        and obs_identidade.get("frame_b64")
                    ):
                        # JPEG cru único e compartilhado. A anotação é adiada
                        # até a decisão da janela completa saber quem é R1.
                        img_b64 = obs_identidade["frame_b64"]
                    else:
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
                    #
                    # Fase 110: "sem ninguém de interesse" deixou de ser sinônimo
                    # de "quadro vazio". Se o teste do passante reconheceu o
                    # operador fora do polígono, a amostra carrega essa gente na
                    # lista PARALELA e um frame anotado só com ela. `pessoas`
                    # continua vazia — é isso que mantém todo o resto intacto.
                    am_fora = fora_ok if _FORA_MODO == "on" else []
                    # Em sombra nada chega ao downstream. Em on, a indecisão
                    # viaja só como auditoria da observação de posto vazio;
                    # a pessoa continua fora de `pessoas` e não vira evento.
                    fora_auditoria = (
                        "indeciso"
                        if _FORA_MODO == "on" and fora_indecisos and not am_fora
                        else None
                    )
                    fora_auditoria_amostras = (
                        max(
                            int(p.get("_fora_amostras_zona") or 0)
                            for p in fora_indecisos
                        )
                        if fora_auditoria else None
                    )
                    for i_f, p_f in enumerate(am_fora):
                        p_f["rotulo"] = f"P{i_f + 1}"
                        p_f["frame_idx"] = frame_idx
                    img_fora_legado = (
                        obs_identidade["frame_b64"]
                        if am_fora and obs_identidade is not None
                        and precisa_frame_identidade
                        and obs_identidade.get("frame_b64")
                        else frame_para_base64(
                            anotar_frame_com_ids(frame, am_fora)
                        ) if am_fora else None
                    )
                    am_nova = Amostra(
                        frame_idx=frame_idx, tempo_s=tempo_s,
                        img_b64="", pessoas=[], dim=(w, h),
                        fora_posto=am_fora,
                        img_b64_fora=img_fora_legado,
                        fora_auditoria=fora_auditoria,
                        fora_auditoria_amostras_zona=fora_auditoria_amostras,
                    )
                    _guardar_candidato_c3(am_nova, {
                        "c3_candidate": candidato_c3_cam1,
                    })
                    if resultado_safety_cam1 is not None:
                        _marcar_presenca_safety(
                            am_nova, resultado_safety_cam1, str(cam_id or "cam1")
                        )
                    amostras.append(am_nova)
        frame_idx += 1
        if frame_idx % 60 == 0:
            pct = int(frame_idx / max(1, total_frames) * 100)
            progress_cb(
                "deteccao",
                min(pct, 99),
                f"frame {frame_idx}/{total_frames} · {len(amostras)} amostras",
            )

    cap.release()
    # ⭐ Telemetria do recurso. Em `sombra` ela é a única saída: nenhuma
    # observação é emitida, nenhuma coluna é escrita e nenhuma chamada de VLM
    # é feita. As contagens abaixo NÃO são duração nem delta de produtividade;
    # multiplicá-las pelo intervalo divergiria da segmentação/consolidação real.
    if _fora_ativo() and n_fora_visto:
        log.info("[fora-do-posto/%s] %d detecção(ões) fora do polígono · "
                 "%d amostra(s) com o operador reconhecido · %d indecisa(s) · "
                 "%d só com transeunte (descartadas, como antes)",
                 _FORA_MODO, n_fora_visto, n_fora_operador, n_fora_indeciso,
                 n_fora_passante)
    ids_unicos = sorted({p["track_id"] for a in amostras for p in a.pessoas})
    descritores = fechar_descritores(desc_acc, intervalo_s, cam_id, w, h)
    if desc_acc_identidade is not None:
        identidade_shadow.setdefault("descritores", []).extend(
            fechar_descritores(desc_acc_identidade, intervalo_s, cam_id, w, h)
        )
    progress_cb("deteccao", 100, f"{len(amostras)} amostras · {len(ids_unicos)} pessoas")
    # Fase 89: o veredito por MINUTO (mesma unidade do evento principal) e a
    # grade deste vídeo, que soma ao mapa do processo.
    mov_min = medidor.por_minuto()
    if mov_min:
        _cont = Counter(v["movimento"] for v in mov_min.values())
        log.info("[movimento] %d minuto(s): %s", len(mov_min), dict(_cont))
    grade_video = ({"grade": medidor.grade, "n_pares": medidor.n_pares_grade,
                    "cam_id": cam_id, "zona": medidor.zona_nome}
                   if medidor.ativo and medidor.n_pares_grade else {})
    return amostras, info, ids_unicos, descritores, mov_min, grade_video


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


def _subamostrar(itens: list, teto: int) -> list:
    """Reduz a lista a `teto` itens preservando SEMPRE o primeiro e o último —
    são eles que carregam o 'começou assim, terminou assado' da sequência."""
    if len(itens) <= teto:
        return list(itens)
    if teto == 1:
        return [itens[0]]
    passo = (len(itens) - 1) / (teto - 1)
    return [itens[int(round(i * passo))] for i in range(teto)]


def _interpolar_sequencia(descricoes: dict, idx_cam1: list) -> set:
    """Preenche os índices sem descrição com a do quadro ANALISADO mais
    próximo. Devolve o conjunto de índices interpolados (para marcá-los).

    Não inventa: só estende para dentro do minuto uma descrição que o modelo
    produziu olhando a sequência. Se nenhum quadro foi analisado, não há o que
    estender e o buraco continua buraco.
    """
    com_desc = sorted(i for i in idx_cam1 if descricoes.get(i))
    if not com_desc:
        return set()
    interpolados = set()
    for i in idx_cam1:
        if descricoes.get(i):
            continue
        j = min(com_desc, key=lambda k: (abs(k - i), k))
        if not PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
            descricoes[i] = descricoes[j]
            interpolados.add(i)
            continue
        vizinho = descricoes[j] or {}
        # Só o texto cobre o buraco entre dois quadros vistos. Identidade e
        # produtividade pertencem ao frame analisado e não podem ser fabricadas
        # por proximidade temporal — especialmente quando P1/P2 trocam de track.
        descricoes[i] = {
            "acoes": dict(vizinho.get("acoes") or {}),
            "maquina": None,
            "imovel": None,
            "operador_estado": "incerto",
            "operador_track_id": None,
            "trabalho": None,
            "produtividade_motivo": "sem_leitura",
            "interlocutor_evidencia": None,
        }
        interpolados.add(i)
    return interpolados


def _cam2_ajuda(grupo: list) -> bool:
    """Fase 90 — a lateral tem o que desambiguar neste minuto?

    Ela existe para ver o que a máquina esconde. Num minuto em que a cam1 vê
    o operador inteiro, ela não acrescenta nada e custa uma imagem inteira por
    chamada. Na dúvida devolve True: perder desambiguação custa um rótulo
    errado, e rótulo errado é mais caro que uma imagem.
    """
    for am in grupo or []:
        if (
            PRODUTIVIDADE_OPERADOR_ESTRUTURADA
            and isinstance(getattr(am, "op_cam2", None), bool)
        ):
            # Tanto a confirmação quanto a negação medida desambiguam quem a
            # frontal vê. O booleano não decide sozinho; libera a imagem
            # lateral para o mesmo julgamento estruturado.
            return True
        # Operador presente no posto mas invisível na cam1 = oclusão total: é
        # exatamente o caso que a lateral foi posta para resolver.
        if getattr(am, "operador_presente", None) and not any(
                p.get("papel") == "operador" for p in am.pessoas):
            return True
        if getattr(am, "maos_cam2", False):
            return True
        for p in am.pessoas:
            k = p.get("kpts")
            if k is None:
                return True          # sem pose: não dá para saber se está inteiro
            try:
                # Pose parcial = corpo cortado pela máquina/quadro. `xyn` traz
                # (0,0) no keypoint não detectado.
                visiveis = sum(1 for x, y in k if x > 0 or y > 0)
                if visiveis < len(k) * 0.6:
                    return True
            except Exception:  # noqa: BLE001
                return True
    return False


def _contexto_zonas(amostra: Amostra, modo_op: bool,
                    frente_maquina: str | None = None,
                    movimento: dict | None = None,
                    identidade_em_aberto: bool = False) -> str:
    """Linha de CONTEXTO do prompt para UMA amostra (zonas, mãos, orientação).

    Fase 89: o MOVIMENTO da máquina entra aqui, no mesmo lugar e do mesmo
    jeito que `maos_maquina` e `orientacao` — como FATO do sensor. O VLM
    continua decidindo se aquilo é ciclo ou parada; ele só deixa de ter que
    adivinhar a partir de um frame que não tem movimento nenhum."""
    maos_cam2 = getattr(amostra, "maos_cam2", False)
    partes = []
    for p in amostra.pessoas:
        zona_txt = p.get("zona_desc") or p.get("zona")
        if not zona_txt:
            continue
        # Na sequência de produtividade o próprio objetivo é descobrir quem é
        # o operador. Repetir aqui a eleição heurística como se fosse fato
        # faria o VLM apenas confirmar o erro de entrada.
        quem = ("candidato ao posto" if identidade_em_aberto
                else ("o OPERADOR" if p.get("papel") == "operador" else p["rotulo"]))
        linha = (f"{p['rotulo']} ({quem}) está em: {zona_txt}"
                 if modo_op else f"{p['rotulo']} está em {zona_txt}")
        op_maos = p.get("maos_maquina") or (
            not identidade_em_aberto
            and p.get("papel") == "operador"
            and maos_cam2
        )
        if op_maos:
            linha += (" — e está com as MÃOS na máquina (torno), tocando/"
                      "manipulando o equipamento (logo, OPERANDO, não apenas monitorando)")
        # Fase 86: a orientação vem do SENSOR (keypoints da pose), não do olho
        # do modelo. Injetada como fato, do mesmo jeito que as mãos.
        orientacao_liberada = _env_ligada("KV_ORIENTACAO_VERIFICADA")
        orient = (
            p.get("orientacao")
            if not PRODUTIVIDADE_OPERADOR_ESTRUTURADA or orientacao_liberada
            else None
        )
        if orient:
            _MAPA = {"frente": "DE FRENTE para a câmera",
                     "costas": "DE COSTAS para a câmera",
                     "perfil": "DE PERFIL para a câmera"}
            linha += f" — e está {_MAPA[orient]} (medido pela pose, não é opinião)"
            vs_maq = orientacao_vs_maquina(orient, frente_maquina)
            if vs_maq:
                linha += f", ou seja, {vs_maq}"
        partes.append(linha)
    txt = ". ".join(partes) if partes else "sem zonas pré-definidas"
    if identidade_em_aberto and getattr(amostra, "op_cam2", None) is False:
        txt += (
            ". A câmera lateral foi medida e não detectou pessoa na zona do "
            "posto neste instante; use esse fato junto com as duas imagens, "
            "sem concluir identidade apenas por ele"
        )
    elif identidade_em_aberto and getattr(amostra, "op_cam2", None) is True:
        txt += (
            ". A câmera lateral detectou uma pessoa na zona do posto; use a "
            "imagem para relacioná-la ao candidato correto"
        )
    if identidade_em_aberto and maos_cam2:
        txt += (". A câmera lateral detectou mãos de uma pessoa na máquina; "
                "use a imagem para atribuir esse fato ao candidato correto")
    # Atrás de `KV_MOVIMENTO_INJETAR`: o sinal GRAVA desde já, mas só passa a
    # influenciar o modelo quando o dono olhar os números e ligar a chave.
    # Medir e influenciar são decisões diferentes e não precisam do mesmo deploy.
    if _MOV_INJETAR and movimento:
        # Fase 94: entra o MODO (uma frase, três estados), não a medição crua —
        # é o que o dono pediu: quanto menos o VLM tiver que inferir, melhor.
        # A medição continua gravada em coluna própria, para poder ser auditada.
        f = frase_modo(movimento.get("modo")) or frase_movimento(
            movimento.get("movimento"), movimento.get("detalhe"))
        if f:
            txt += ". " + f
    return txt


# ═════════════════════════════════════════════════════════════════════════
# A NARRATIVA DO MINUTO. Descrever primeiro, rotular depois.
#
# O PROBLEMA, medido no código: o VLM já descreve TODOS os quadros — o prompt
# pede uma entrada por imagem e ele devolve 12 num minuto. Mas `_abrir_evento`
# guarda `descricao_bruta` da PRIMEIRA observação do bloco dominante e nunca a
# atualiza conforme o bloco cresce. As outras onze são descartadas.
#
# Resultado: o card de 180s→240s mostra a frase de UM instante como se fosse o
# minuto. Não é "sempre o último frame" — é o primeiro do bloco vencedor —, mas
# o efeito é o mesmo: um instante falando pelo minuto inteiro.
#
# ⚠️ A NARRATIVA ACOMPANHA, NÃO SUBSTITUI. As descrições por instante seguem
# existindo: são elas que permitem `etapa_segmentar_eventos` cortar o minuto
# quando a ação muda ("operou 40s, saiu 20s"). Trocá-las por uma narrativa só
# colapsaria o minuto num bloco e apagaria transições que são REAIS. A
# narrativa é para o humano ler; as por instante são para a máquina cortar.
#
# ⚠️ E ELA NÃO DECIDE NADA. Desde a Fase 101 o número vem da permanência, que
# não lê descrição. É por isso que mexer no prompt ficou seguro — não era, duas
# semanas atrás.
#
# CUSTO: nenhuma chamada nova (o campo entra no MESMO JSON, e o modelo já está
# olhando todos os quadros). ~60 tokens de saída por minuto analisado.
# ═════════════════════════════════════════════════════════════════════════
_NARRATIVA = os.environ.get("KV_NARRATIVA", "on").strip().lower() not in (
    "off", "0", "false", "no", "")


# Sobras de linguagem de máquina que o modelo às vezes deixa passar apesar da
# proibição no prompt. Quem lê a narrativa é um dono de fábrica: "P1" e
# "imagem 3" denunciam que aquilo saiu de um sistema, e é justamente isso que
# não pode aparecer numa tela que vai virar material comercial.
#
# ⚠️ É REDE, NÃO CONSERTO. O lugar de resolver isto é o prompt — substituição
# por regex não entende contexto e, aplicada demais, estraga a frase. Aqui só
# entram trocas seguras: rótulo de pessoa e referência a número de imagem, que
# têm forma fixa e não se confundem com nada do chão de fábrica.
_TROCAS_HUMANAS = (
    (r"\bP1\b", "o operador"),
    (r"\bP(?:[2-9]|\d{2,})\b", "outra pessoa"),
    (r"\b[Nn]as imagens?\s+\d+\s*[-–a]\s*\d+\b", "no começo do trecho"),
    (r"\b[Nn]a imagem\s+\d+\b", "em seguida"),
    (r"\b[Aa] partir da imagem\s+\d+\b", "depois"),
    (r"\b[Nn]o frame\s+\d+\b", "em seguida"),
    (r"\s*\(imagens?\s+[\d\s,\-–a]+\)", ""),
)


def _narrativa_humana(texto: str) -> str:
    """Tira as sobras de linguagem de máquina da narrativa."""
    t = texto
    for padrao, troca in _TROCAS_HUMANAS:
        t = re.sub(padrao, troca, t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    # A troca entra em minúscula e pode cair em começo de frase ("P1 permanece"
    # → "o operador permanece"). Sem isto a narrativa fica com cara de erro
    # justo na primeira palavra, que é a que o gestor lê primeiro.
    def _maiuscula(m):
        return m.group(1) + m.group(2).upper()

    t = re.sub(r"(^|[.!?]\s+)([a-zà-ú])", _maiuscula, t)
    return t


# Palavras de TEMPO para a narrativa montada. Nunca número de imagem: quem lê
# é dono de fábrica, e "imagem 3" denuncia que aquilo saiu de um sistema.
_MOMENTOS = ("Logo depois", "Em seguida", "Mais adiante", "Perto do fim")


def _narrativa_dos_instantes(bruto: dict) -> str | None:
    """A narrativa montada a partir das frases POR INSTANTE, em ordem.

    ⭐ A REDE QUE GARANTE QUE TODO CARD TENHA DESCRIÇÃO. O filtro de 120
    caracteres existe por um bom motivo — resumo de uma linha é a frase curta
    de novo com outro nome —, mas ele é tudo-ou-nada: quando o modelo devolve
    pouco, o card ficava mudo, e card mudo é pior que card com texto simples.

    Isto NÃO INVENTA NADA. As frases são as mesmas que o modelo escreveu para
    cada imagem; o que fazemos é enfileirá-las na ordem do tempo e colapsar as
    repetições. É, literalmente, a descrição baseada em todos os frames — só
    que montada por nós em vez de escrita pelo modelo. Sai menos fluida, e é
    por isso que ela é a segunda opção e não a primeira.
    """
    trechos = (bruto or {}).get("trechos")
    if not isinstance(trechos, list):
        return None
    passos: list[str] = []
    for t in sorted(
        (x for x in trechos if isinstance(x, dict)),
        key=lambda x: x.get("i") if isinstance(x.get("i"), int) else 0,
    ):
        acoes = t.get("acoes")
        if not isinstance(acoes, dict):
            continue
        partes = [f"{rot}: {txt.strip()}" for rot, txt in acoes.items()
                  if isinstance(txt, str) and txt.strip()]
        if partes:
            passos.append("; ".join(partes))
    if not passos:
        return None

    # Instantes iguais seguidos viram um só. Sem isto, um minuto em que nada
    # mudou vira cinco frases idênticas — que é o oposto de descrever.
    blocos: list[list] = []
    for p in passos:
        if blocos and blocos[-1][0] == p:
            blocos[-1][1] += 1
        else:
            blocos.append([p, 1])

    frases: list[str] = []
    n = len(blocos)
    for k, (texto, vezes) in enumerate(blocos):
        if k == 0:
            quando = "No começo do trecho"
        elif k == n - 1:
            quando = "Até o fim do trecho"
        else:
            quando = _MOMENTOS[min(k - 1, len(_MOMENTOS) - 1)]
        # "e segue assim" é fato observado (a cena não mudou entre amostras),
        # não suposição sobre o que aconteceu entre elas.
        segue = ", e segue assim" if vezes > 1 else ""
        frases.append(f"{quando}, {texto}{segue}.")

    txt = _narrativa_humana(" ".join(frases))
    # Um instante só, de duas palavras, não vira narrativa nem montada.
    return txt if len(txt) >= 60 else None


def _resumo_da_sequencia(bruto: dict) -> str | None:
    """A narrativa do minuto: a do modelo se ela resumir de verdade, senão a
    montada a partir dos instantes.

    Fora da flag devolve None — e None não vira texto nenhum no banco, em vez
    de virar string vazia que depois ninguém sabe se é "não pedimos" ou "o
    modelo não respondeu"."""
    if not _NARRATIVA:
        return None
    r = (bruto or {}).get("resumo")
    if isinstance(r, str):
        r = _narrativa_humana(r.strip())
        # Uma ou duas palavras não é narrativa — é ruído com cara de resposta.
        # 120 caracteres: com a instrução de três a cinco frases, qualquer
        # coisa abaixo disso é o modelo devolvendo a frase curta de novo — e
        # uma narrativa de uma linha não é narrativa, é a descrição antiga com
        # outro nome.
        if len(r) >= 120:
            return r
    # O modelo não resumiu (ou resumiu de menos). Em vez de deixar o card mudo,
    # monta a narrativa com as frases por instante que ele JÁ escreveu.
    montada = _narrativa_dos_instantes(bruto)
    if montada:
        log.info("[narrativa] resumo curto/ausente — montada a partir de %d "
                 "instante(s)", len((bruto or {}).get("trechos") or []))
    return montada


def _evidencia_conversa_do_trecho(
    trecho: dict,
    amostra: Amostra,
    mapa_rotulo_track: dict[str, int],
    operador_tid: int | None,
    motivo: str,
) -> dict | None:
    """Resolve conversa + interlocutor sem usar texto ou escolher pessoa.

    O VLM associa a conversa a um rótulo visível; a CPU consulta a medida HSV
    que já foi feita no tronco daquele track. Se a associação ou a cor não
    fechar, o resultado é ``incerto``. Campo ausente/malformado não confirma
    nem mesmo a conversa e preserva o fluxo anterior.
    """
    if operador_tid is None or motivo != "conversa_ou_celular":
        return None
    estado = str(trecho.get("conversa_estado") or "").strip().lower()
    interlocutor_rotulo = trecho.get("interlocutor")
    if estado == "ausente" and interlocutor_rotulo is None:
        return None
    if estado not in {"identificada", "incerta"}:
        return None

    base = {
        "conversa_estado": estado,
        "tipo": TIPO_INTERLOCUTOR_INCERTO,
        "cor_superior": "incerto",
        "confianca_cor": 0.0,
        "origem": "vlm_interlocutor+roupa_superior_hsv",
    }
    if estado == "incerta":
        return {**base, "motivo_cor": "interlocutor_ambiguo"}
    if not isinstance(interlocutor_rotulo, str):
        return {**base, "motivo_cor": "interlocutor_ausente"}

    interlocutor_tid = mapa_rotulo_track.get(interlocutor_rotulo)
    # A roupa do próprio operador é terminantemente inelegível. Rótulo
    # inexistente, igual ao operador ou duplicado pelo modelo vira incerteza.
    if interlocutor_tid is None or interlocutor_tid == operador_tid:
        return {**base, "motivo_cor": "associacao_invalida"}
    pessoa = next(
        (p for p in amostra.pessoas if p.get("track_id") == interlocutor_tid),
        None,
    )
    roupa = pessoa.get("roupa_superior") if isinstance(pessoa, dict) else None
    if not isinstance(roupa, dict):
        return {
            **base,
            "interlocutor_track_id": int(interlocutor_tid),
            "motivo_cor": "medida_ausente",
        }

    evidencia = {
        **base,
        "interlocutor_track_id": int(interlocutor_tid),
        **{
            k: roupa.get(k)
            for k in (
                "cor_superior", "confianca_cor", "qualidade",
                "pixels_utilizaveis", "saturacao_mediana", "brilho_mediano",
                "brilho_p10", "brilho_p90", "fracao_neutra",
                "fracao_cinza_compativel", "motivo_cor",
            )
            if roupa.get(k) is not None
        },
    }
    cor = evidencia.get("cor_superior")
    try:
        confianca = float(evidencia.get("confianca_cor") or 0.0)
    except (TypeError, ValueError):
        confianca = 0.0
    if cor == "cinza" and confianca >= CONFIANCA_COR_GESTOR_MIN:
        evidencia["tipo"] = TIPO_INTERLOCUTOR_GESTOR
    elif cor == "nao_cinza" and confianca >= CONFIANCA_COR_GESTOR_MIN:
        evidencia["tipo"] = TIPO_INTERLOCUTOR_COLEGA
    else:
        evidencia.update({
            "tipo": TIPO_INTERLOCUTOR_INCERTO,
            "cor_superior": "incerto",
        })
    return evidencia


def _aplicar_regra_conversa(
    evidencia: dict | None,
    trabalho,
    motivo: str,
) -> tuple[bool | None, str]:
    """Aplica somente os três estados produzidos pelo contrato acima."""
    tipo = (evidencia or {}).get("tipo")
    if tipo == TIPO_INTERLOCUTOR_GESTOR:
        return True, "conversa_gestor_cinza"
    if tipo == TIPO_INTERLOCUTOR_COLEGA:
        return False, "conversa_colega_nao_cinza"
    if tipo == TIPO_INTERLOCUTOR_INCERTO:
        return False, "conversa_interlocutor_incerto"
    return trabalho, motivo


_LABEL_POR_INTERLOCUTOR = {
    TIPO_INTERLOCUTOR_GESTOR: LABEL_CONVERSANDO_GESTOR,
    TIPO_INTERLOCUTOR_COLEGA: LABEL_CONVERSANDO_COLEGA,
    TIPO_INTERLOCUTOR_INCERTO: LABEL_CONVERSANDO_INCERTO,
}
_DESCRICOES_CONVERSA_VISUAL = {
    LABEL_CONVERSANDO_GESTOR: "conversando com gestor de roupa superior cinza",
    LABEL_CONVERSANDO_COLEGA: "conversando com colega",
    LABEL_CONVERSANDO_INCERTO: "conversando com interlocutor não determinado",
}


def _label_conversa_evidenciada(evidencia: dict | None) -> str | None:
    if not isinstance(evidencia, dict):
        return None
    return _LABEL_POR_INTERLOCUTOR.get(evidencia.get("tipo"))


def _analisar_sequencia_vlm(
    groq_client: Groq,
    grupo: list[Amostra],
    descricao_processo: str,
    memoria: dict,
    intervalo_s: float,
    conhecimento_adquirido: str = "",
    frente_maquina: str | None = None,
    movimento: dict | None = None,
) -> dict[int, dict]:
    """Fase 85 — UMA chamada para a sequência inteira de um minuto.

    Devolve {índice da amostra no grupo → {track_id → descrição}} — uma
    observação POR INSTANTE, exatamente como antes. Isso é o que mantém
    `etapa_segmentar_eventos` capaz de quebrar o evento no meio do minuto se a
    ação mudar, e o que preserva a `concordancia` (que é a fração do minuto do
    rótulo vencedor e precisa de várias observações para existir).

    A cam2 entra com UM frame, o do meio — ela existe para desambiguar oclusão,
    e para isso um instante resolve. Mandá-la em todos os instantes dobraria as
    imagens e comeria o ganho de custo.

    Fase 90 — E SÓ ENTRA QUANDO HÁ O QUE DESAMBIGUAR. Ela custa ~8% das
    imagens da chamada e ia em TODO minuto, inclusive nos em que a cam1 vê o
    operador inteiro e nada está oculto. A lateral existe para responder "o
    que a máquina esconde"; sem oclusão ela não responde nada e é imagem paga
    à toa. Critério: alguém sem pose completa (corpo cortado/ocluso), ou
    operador ausente da cam1 no minuto, ou mãos na máquina pela cam2 — os três
    casos em que a cam1 sozinha erra.
    """
    if not grupo:
        return {}
    usados = _subamostrar(grupo, _SEQ_MAX_IMG - 1)
    imgs = [a.img_b64 for a in usados if a.img_b64]
    if not imgs:
        return {}

    meio = usados[len(usados) // 2]
    img_cam2 = meio.img_b64_secundario if _cam2_ajuda(grupo) else None
    linha_cam2 = ""
    if img_cam2:
        imgs.append(img_cam2)
        linha_cam2 = (
            f"\nA ÚLTIMA imagem ({len(imgs)}ª) é da CÂMERA LATERAL, tirada no instante "
            "do meio da sequência — use-a para ver o que a máquina esconde na "
            "câmera principal. Ela NÃO é um instante a mais: não gere entrada "
            "para ela."
        )

    modo_op = any(p.get("papel") for a in usados for p in a.pessoas)
    tem_operador = any(p.get("papel") == "operador" for a in usados for p in a.pessoas)
    n_cam1 = len(usados)
    dur = round((n_cam1 - 1) * float(intervalo_s), 1) if n_cam1 > 1 else float(intervalo_s)

    contexto_prompt = (
        "\n".join(
            f"IMAGEM {i}: " + _contexto_zonas(
                am, modo_op, frente_maquina, movimento,
                identidade_em_aberto=not bool(
                    getattr(am, "identidade_autoritativa", False)
                ),
            )
            for i, am in enumerate(usados)
        )
        if PRODUTIVIDADE_OPERADOR_ESTRUTURADA
        else _contexto_zonas(meio, modo_op, frente_maquina, movimento)
    )
    template_sequencia = (
        PROMPT_VLM_SEQUENCIA
        if PRODUTIVIDADE_OPERADOR_ESTRUTURADA
        else PROMPT_VLM_SEQUENCIA_V8
    )
    prompt = template_sequencia.format(
        n_frames=n_cam1,
        duracao_s=dur,
        intervalo_s=round(float(intervalo_s), 1),
        linha_cam2=linha_cam2,
        bloco_processo=construir_bloco_dominio(descricao_processo, conhecimento_adquirido),
        bloco_vocabulario=construir_bloco_vocabulario(memoria),
        regras=(
            _REGRAS_DESCRICAO_V9
            if PRODUTIVIDADE_OPERADOR_ESTRUTURADA
            else _REGRAS_DESCRICAO_V8
        ),
        exemplos=_BLOCO_EXEMPLOS_DESCRICAO,
        # O contexto antigo vinha apenas do quadro do meio e era apresentado
        # como se valesse para a sequência toda. Assim, "mãos no torno" em um
        # único instante contaminava todos os demais. Agora cada imagem leva
        # os próprios fatos de zona, mãos e orientação.
        contexto_zonas=contexto_prompt,
    )
    if not tem_operador:
        prompt = prompt.replace(
            "P1 é o OPERADOR TITULAR do posto. P2, P3... são outras pessoas "
            "dentro da área do posto.",
            "P1, P2, P3... são as pessoas marcadas.",
        )

    try:
        resposta = groq_vision_call(
            groq_client, imgs[0], prompt, json_mode=True,
            # O teto cobre as frases por instante MAIS a narrativa. Uma
            # narrativa cortada no meio é pior que uma curta: some justamente
            # o fim da sequência, que é onde mora a mudança. E JSON truncado
            # perde o minuto inteiro, não só o resumo — por isso a folga é
            # generosa: três a cinco frases custam ~150 tokens, e o preço de
            # errar para baixo é perder tudo.
            max_tokens=220 * max(1, n_cam1) + 650,
            imagens_extra=imgs[1:],
        )
        # Guarda o objeto INTEIRO: `resumo` é irmão de `trechos`, não filho.
        bruto = json.loads(resposta) or {}
        trechos = bruto.get("trechos", [])
    except json.JSONDecodeError:
        log.warning("[sequencia] JSON inválido no grupo de %d frames", n_cam1)
        return {}
    except Exception as e:   # noqa: BLE001
        log.warning("[sequencia] falha na análise do grupo (%s)", e)
        return {}

    # O índice devolvido é o da imagem NA SEQUÊNCIA — mapeia de volta para a
    # amostra correspondente. Rótulos (P1, P2...) são desenhados em CADA imagem
    # separadamente, então a tradução rótulo→track usa o mapa DAQUELA amostra.
    idx_no_grupo = {id(a): i for i, a in enumerate(grupo)}
    saida: dict[int, dict] = {}
    if not PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
        for t in trechos if isinstance(trechos, list) else []:
            if not isinstance(t, dict):
                continue
            try:
                i = int(t.get("i"))
            except Exception:  # noqa: BLE001
                continue
            if not (0 <= i < len(usados)):
                continue
            am = usados[i]
            mapa = {p["rotulo"]: p["track_id"] for p in am.pessoas}
            por_track = {
                mapa[rot]: d.strip().lower()
                for rot, d in (t.get("acoes") or {}).items()
                if rot in mapa and isinstance(d, str) and d.strip()
            }
            if por_track:
                saida[idx_no_grupo[id(am)]] = {
                    "acoes": por_track,
                    "maquina": _maquina_do_vlm(t),
                    "imovel": bool(t.get("imovel")),
                    "trabalho": (
                        t.get("trabalho")
                        if isinstance(t.get("trabalho"), bool)
                        else None
                    ),
                    # A narrativa é do MINUTO, não do instante — por isso ela
                    # viaja igual em todas as observações do grupo. Quem a
                    # grava é o evento, uma vez só.
                    "resumo": _resumo_da_sequencia(bruto),
                }
        return saida

    indices_vistos: set[int] = set()
    for t in trechos if isinstance(trechos, list) else []:
        if not isinstance(t, dict):
            continue
        try:
            i = int(t.get("i"))
        except Exception:   # noqa: BLE001
            continue
        if not (0 <= i < len(usados)):
            continue
        am = usados[i]
        mapa = {p["rotulo"]: p["track_id"] for p in am.pessoas}
        acoes = t.get("acoes") if isinstance(t.get("acoes"), dict) else {}
        por_track = {
            tid: (
                str(acoes.get(rot)).strip().lower()
                if isinstance(acoes.get(rot), str) and str(acoes.get(rot)).strip()
                else "ação não identificada"
            )
            for rot, tid in mapa.items()
        }

        estado = str(t.get("operador_estado") or "").strip().lower()
        operador_rotulo = t.get("operador")
        operador_tid = (
            mapa.get(operador_rotulo)
            if isinstance(operador_rotulo, str)
            else None
        )
        # Contrato fechado: exatamente um rótulo válido em `identificado`;
        # nenhum nos outros estados. Qualquer desvio vira incerto.
        if estado not in {"identificado", "ausente", "incerto"}:
            estado = "incerto"
        if estado == "identificado" and operador_tid is None:
            estado = "incerto"
        if estado != "identificado" and operador_rotulo is not None:
            estado = "incerto"
        if estado != "identificado":
            operador_tid = None

        motivo = str(t.get("motivo") or "sem_leitura").strip().lower()
        if motivo not in _MOTIVOS_V9:
            motivo = "sem_leitura"
        acao_operador = (
            acoes.get(operador_rotulo)
            if isinstance(operador_rotulo, str)
            else None
        )
        trabalho = (
            _trabalho_v9_validado(
                t.get("trabalho"), motivo, acao_operador
            )
            if estado == "identificado"
            else None
        )
        evidencia_interlocutor = (
            _evidencia_conversa_do_trecho(
                t, am, mapa, operador_tid, motivo,
            )
            if estado == "identificado"
            # Reaproveita a guarda do contrato V9 para exigir uma descrição
            # observada, mas não deixa o booleano genérico decidir a nova regra.
            and _trabalho_v9_validado(False, motivo, acao_operador) is False
            else None
        )
        trabalho, motivo = _aplicar_regra_conversa(
            evidencia_interlocutor, trabalho, motivo,
        )

        destino = idx_no_grupo[id(am)]
        bloco = {
            "acoes": por_track,
            "maquina": _maquina_do_vlm(t),
            "imovel": (t.get("imovel")
                       if isinstance(t.get("imovel"), bool) else None),
            "operador_estado": estado,
            "operador_track_id": operador_tid,
            "trabalho": trabalho,
            "produtividade_motivo": motivo,
            "interlocutor_evidencia": evidencia_interlocutor,
            # A narrativa é do MINUTO, não do instante — por isso ela viaja
            # igual em todas as observações do grupo. Quem a grava é o evento,
            # uma vez só.
            "resumo": _resumo_da_sequencia(bruto),
        }
        if i in indices_vistos:
            # Duas respostas para a mesma imagem são uma contradição do
            # instrumento. Conserva o texto para auditoria, mas não a decisão.
            anterior = saida.get(destino) or bloco
            anterior.update({
                "operador_estado": "incerto",
                "operador_track_id": None,
                "trabalho": None,
                "produtividade_motivo": "sem_leitura",
                "interlocutor_evidencia": None,
            })
            saida[destino] = anterior
            continue
        indices_vistos.add(i)
        saida[destino] = bloco
    return saida


def _analisar_sequencia_fora(
    groq_client: Groq,
    grupo: list[Amostra],
    descricao_processo: str,
    memoria: dict,
    intervalo_s: float,
    conhecimento_adquirido: str = "",
    zona_desc: str | None = None,
) -> dict[int, dict]:
    """Fase 110 — descreve o que o operador faz FORA do posto.

    Chamada SEPARADA por necessidade, não por escolha: por construção uma
    amostra `fora_posto` não tem amostra cam1 no mesmo instante (o polígono
    está vazio), então não existe chamada de grupo onde pegar carona.

    Devolve {índice no grupo: {"acao": str, "resumo": str|None}}.
    """
    if not grupo:
        return {}
    usados = _subamostrar(grupo, _SEQ_MAX_IMG)
    usados = [a for a in usados if a.img_b64_fora]
    imgs = [a.img_b64_fora for a in usados]
    if not imgs:
        return {}

    n = len(usados)
    dur = round((n - 1) * float(intervalo_s), 1) if n > 1 else float(intervalo_s)
    contexto = (zona_desc or "posto do torno") + (
        " — a área marcada como posto está VAZIA nestes instantes; a pessoa "
        "está em outro ponto do galpão")
    prompt = PROMPT_VLM_FORA_POSTO.format(
        n_frames=n,
        duracao_s=dur,
        intervalo_s=round(float(intervalo_s), 1),
        bloco_processo=construir_bloco_dominio(descricao_processo, conhecimento_adquirido),
        bloco_vocabulario=construir_bloco_vocabulario(memoria),
        regras=_REGRAS_DESCRICAO_V8,
        exemplos=_BLOCO_EXEMPLOS_DESCRICAO,
        contexto_zonas=contexto,
    )
    try:
        resposta = groq_vision_call(
            groq_client, imgs[0], prompt, json_mode=True,
            max_tokens=160 * max(1, n) + 650, imagens_extra=imgs[1:],
        )
        bruto = json.loads(resposta) or {}
        trechos = bruto.get("trechos", [])
    except Exception as e:   # noqa: BLE001
        # Não-fatal: sem descrição, o minuto volta a ser `posto_vazio`, que é o
        # comportamento de hoje. Falhar aqui nunca pode custar o vídeo.
        log.warning("[fora-do-posto] descrição falhou (%s) — volta a posto vazio", e)
        return {}

    resumo = _resumo_da_sequencia(bruto)
    idx_no_grupo = {id(a): i for i, a in enumerate(grupo)}
    saida: dict[int, dict] = {}
    vistos: set[int] = set()
    for t in trechos if isinstance(trechos, list) else []:
        if not isinstance(t, dict):
            continue
        try:
            i = int(t.get("i"))
        except Exception:   # noqa: BLE001
            continue
        if not (0 <= i < len(usados)):
            continue
        acao = (t.get("acao") or "").strip().lower()
        if not acao:
            continue
        destino = idx_no_grupo[id(usados[i])]
        if i in vistos:
            # Mesma falha segura dos outros parsers: duas respostas para a
            # mesma imagem são uma contradição do instrumento. A última nunca
            # vence — o instante vira não-nomeado e sai da decisão.
            saida[destino] = {"acao": "ação não identificada", "resumo": resumo}
            continue
        vistos.add(i)
        saida[destino] = {"acao": acao, "resumo": resumo}
    return saida


def _analisar_sequencia_cam2(
    groq_client: Groq,
    grupo: list[Amostra],
    descricao_processo: str,
    memoria: dict,
    intervalo_s: float,
    conhecimento_adquirido: str = "",
    zona_desc: str | None = None,
) -> dict[int, dict]:
    """Fase 85 — o RESGATE pela cam2 também vira sequência.

    Terceira porta dos fundos: sem isto, o caminho do resgate continuaria com o
    prompt antigo (o do desempate que só tinha saídas produtivas) e viraria a
    rota preferencial da produtividade — justamente nos instantes em que a cam1
    não vê nada.
    """
    if not grupo:
        return {}
    usados = _subamostrar(grupo, _SEQ_MAX_IMG)
    imgs = [a.img_b64_secundario for a in usados if a.img_b64_secundario]
    if not imgs:
        return {}
    usados = [a for a in usados if a.img_b64_secundario]

    contexto = zona_desc or "área de trabalho do operador, atrás da máquina"
    if PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
        fatos_maos = [
            f"IMAGEM {i}: o sensor detectou mãos na zona do torno"
            for i, a in enumerate(usados)
            if getattr(a, "maos_cam2", False)
        ]
        if fatos_maos:
            contexto += ". " + ". ".join(fatos_maos)
    elif any(getattr(a, "maos_cam2", False) for a in usados):
        contexto += (" — em algum destes instantes ele está com as MÃOS na máquina "
                     "(torno), tocando/manipulando o equipamento")
    n = len(usados)
    dur = round((n - 1) * float(intervalo_s), 1) if n > 1 else float(intervalo_s)
    template_cam2 = (
        PROMPT_VLM_SEQUENCIA_CAM2
        if PRODUTIVIDADE_OPERADOR_ESTRUTURADA
        else PROMPT_VLM_SEQUENCIA_CAM2_V8
    )
    prompt = template_cam2.format(
        n_frames=n,
        duracao_s=dur,
        intervalo_s=round(float(intervalo_s), 1),
        bloco_processo=construir_bloco_dominio(descricao_processo, conhecimento_adquirido),
        bloco_vocabulario=construir_bloco_vocabulario(memoria),
        regras=(
            _REGRAS_DESCRICAO_V9
            if PRODUTIVIDADE_OPERADOR_ESTRUTURADA
            else _REGRAS_DESCRICAO_V8
        ),
        exemplos=_BLOCO_EXEMPLOS_DESCRICAO,
        contexto_zonas=contexto,
    )
    try:
        resposta = groq_vision_call(
            groq_client, imgs[0], prompt, json_mode=True,
            max_tokens=120 * max(1, n), imagens_extra=imgs[1:],
        )
        trechos = (json.loads(resposta) or {}).get("trechos", [])
    except Exception as e:   # noqa: BLE001
        log.warning("[sequencia] resgate pela cam2 falhou (%s)", e)
        return {}

    idx_no_grupo = {id(a): i for i, a in enumerate(grupo)}
    saida: dict[int, dict] = {}
    indices_vistos: set[int] = set()
    for t in trechos if isinstance(trechos, list) else []:
        if not isinstance(t, dict):
            continue
        try:
            i = int(t.get("i"))
        except Exception:   # noqa: BLE001
            continue
        if not (0 <= i < len(usados)):
            continue
        destino = idx_no_grupo[id(usados[i])]
        if i in indices_vistos:
            # Mesma falha segura do parser da câmera principal: respostas
            # duplicadas são contraditórias; a última nunca pode vencer.
            anterior = saida.get(destino) or {
                "acao": "ação não identificada",
                "maquina": None,
                "imovel": None,
            }
            anterior.update({
                "operador_estado": "incerto",
                "trabalho": None,
                "produtividade_motivo": "sem_leitura",
            })
            saida[destino] = anterior
            continue
        indices_vistos.add(i)
        acao = (t.get("acao") or "").strip().lower()
        if acao:
            estado = str(t.get("operador_estado") or "").strip().lower()
            if PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
                if estado not in {"identificado", "ausente", "incerto"}:
                    estado = "incerto"
            else:
                estado = "identificado"
            motivo = str(t.get("motivo") or "sem_leitura").strip().lower()
            if motivo not in _MOTIVOS_V9:
                motivo = "sem_leitura"
            trabalho = (
                _trabalho_v9_validado(t.get("trabalho"), motivo, acao)
                if PRODUTIVIDADE_OPERADOR_ESTRUTURADA and estado == "identificado"
                else (
                    t.get("trabalho")
                    if not PRODUTIVIDADE_OPERADOR_ESTRUTURADA
                    and estado == "identificado"
                    and isinstance(t.get("trabalho"), bool)
                    else None
                )
            )
            saida[destino] = {
                "acao": acao,
                "maquina": _maquina_do_vlm(t),
                "imovel": (t.get("imovel")
                           if isinstance(t.get("imovel"), bool) else None),
                "operador_estado": estado,
                "trabalho": trabalho,
                "produtividade_motivo": motivo,
            }
    return saida


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
        # Fase 90 — SEM a lateral. A pergunta é "ainda é a MESMA ação da
        # âncora?", e a âncora é da cam1; a lateral não ajuda a respondê-la e
        # DOBRAVA o custo da checagem. Com duas imagens o break-even do gate
        # era ~7 checagens por minuto — acima disso ele gastava mais que a
        # chamada de sequência que estava evitando, e é justamente com o teto
        # alto que mais amostras chegam aqui. Com uma imagem o break-even vai
        # para ~13 e o gate não tem como sair no prejuízo.
        r = groq_vision_call(
            groq_client, amostra.img_b64, prompt,
            json_mode=False, max_tokens=3, temperatura=0.0,
        )
        return (r or "").strip().lower().startswith("s")
    except Exception as e:
        log.warning(f"[gate] binário falhou no frame {amostra.frame_idx} ({e}) — analisando.")
        return False


# ── Fase 92 — A ÂNCORA DO GATE SOBREVIVE À TROCA DE ID ───────────────────
# Cada troca de ID mata a âncora e força a análise do minuto inteiro. Com o
# track mediano da cam1 em 8 s, isso é a maior parte dos minutos — e com o teto
# do gate em 12 o desperdício cresceu, porque o teto passaria a permitir
# suprimir minutos que a troca de ID reabre.
#
# ⚠️ NÃO REMAPEIA ID NENHUM. Papel, contagem, descritor, evento e eleição do
# operador continuam exatamente como estão. Muda SÓ contra qual âncora o gate
# compara — a superfície mínima que captura o ganho.
#
# O RISCO, e ele é real: duas pessoas coladas no tempo e no espaço fariam o
# gate comparar contra a âncora errada, e o minuto inteiro herdaria a descrição
# de outra pessoa. Quatro guardas, e a assimetria manda em todas:
#   falso "diferente" custa UMA chamada · falso "igual" custa um RÓTULO ERRADO.
# Na dúvida, NÃO herda.
_COSTURA_ANCORA = os.environ.get("KV_COSTURA_ANCORA", "off") not in (
    "off", "0", "false", "False", "")
# Veto de aparência. NÃO é identificação — medimos que cor não identifica aqui
# (+0,025 de separação onde um classificador precisaria de ~+0,15). Mas o
# PISO da distribuição é real: os pares mais diferentes ficam em 0,15-0,30.
# Rejeitar o absurdo é problema diferente de decidir quem é quem: o veto só
# precisa pegar a cauda, e a cauda existe.
_ANCORA_VETO_VISUAL = float(os.environ.get("KV_ANCORA_VETO_VISUAL", "0.75"))


def _centro_rel(pessoa: dict, w: int, h: int):
    b = pessoa.get("bbox")
    if not _bbox_valido(b):
        return None
    x1, y1, x2, y2 = (float(v) for v in b[:4])
    return (round((x1 + x2) / 2 / max(1, w), 5),
            round((y1 + y2) / 2 / max(1, h), 5),
            round((y2 - y1) / max(1, h), 5))


def _n_no_posto(amostra) -> int:
    """Quantas pessoas havia no posto neste instante — contando as DUAS
    câmeras (Fase 91). Com mais de uma, quem continuou é ambíguo e a herança
    se recusa a adivinhar."""
    n1 = sum(1 for p in amostra.pessoas if p.get("papel") == "operador")
    n2 = getattr(amostra, "n_posto_cam2", None)
    return max(n1, int(n2) if n2 is not None else 0)


def ancora_por_continuidade(ancoras: dict, tid_novo: int, pessoa: dict,
                            amostra, w: int, h: int, vivos: set) -> tuple:
    """(tid da âncora herdada, motivo) — ou (None, motivo da recusa).

    Track NOVO sem âncora própria: existe um track que acabou de sumir, ali
    perto, que é plausivelmente a mesma pessoa? Se sim, o gate compara contra
    a âncora dele em vez de forçar uma análise cheia.
    """
    if not _COSTURA_ANCORA:
        return None, "desligado"
    centro = _centro_rel(pessoa, w, h)
    if centro is None:
        return None, "sem caixa"
    # GUARDA 1 — ambiguidade: com mais de uma pessoa no posto, quem continuou
    # não é dedutível. Recusa em vez de sortear.
    if _n_no_posto(amostra) > 1:
        return None, "mais de uma pessoa no posto"
    melhor, melhor_gap, motivo = None, None, "nenhum candidato"
    for tid, anc in ancoras.items():
        if tid == tid_novo or tid in vivos:
            continue                      # âncora de track AINDA ativo não vale
        t_anc, c_anc = anc.get("t"), anc.get("centro")
        if t_anc is None or c_anc is None:
            continue
        gap = amostra.tempo_s - float(t_anc)
        # GUARDA 2 — geometria: pouco tempo, pouca distância, sem salto de
        # profundidade. Mesma regra da costura dos descritores.
        if gap <= 0 or gap > _COSTURA_GAP_S:
            continue
        alt = max(1e-6, (c_anc[2] + centro[2]) / 2)
        dist = ((c_anc[0] - centro[0]) ** 2 + (c_anc[1] - centro[1]) ** 2) ** 0.5 / alt
        if dist > _COSTURA_DIST:
            motivo = "candidato longe demais"
            continue
        if abs(c_anc[2] - centro[2]) / max(c_anc[2], centro[2]) > _COSTURA_ALTURA_TOL:
            motivo = "salto de profundidade"
            continue
        # GUARDA 3 — papel: visitante não herda âncora de operador. É o caso
        # perigoso, porque converteria terceiro em titular sem ninguém ver.
        if anc.get("papel") != pessoa.get("papel"):
            motivo = "papel diferente"
            continue
        # GUARDA 4 — VETO de aparência. Não identifica: rejeita o absurdo.
        dv = _dist_movimento(anc.get("crop"), pessoa.get("crop"))
        if dv is not None and dv > _ANCORA_VETO_VISUAL:
            motivo = f"veto visual ({dv:.2f})"
            continue
        if anc.get("n_no_posto", 1) > 1:
            motivo = "âncora nasceu ambígua"
            continue
        if melhor is None or gap < melhor_gap:
            melhor, melhor_gap = tid, gap
    return (melhor, f"herdou de {melhor} (gap {melhor_gap:.1f}s)") if melhor is not None \
        else (None, motivo)


def _agrupar_amostras(amostras: list, bucket_s: float) -> list[list]:
    """Fase 85 — agrupa amostras consecutivas em blocos do mesmo MINUTO.

    O minuto não é arbitrário: é o mesmo balde da consolidação
    (`etapa_consolidar_principais`), então a chamada da sequência cobre
    exatamente o intervalo que vira um evento principal.
    """
    if bucket_s <= 0:
        return [[a] for a in amostras]
    grupos: list[list] = []
    atual: list = []
    balde_atual = None
    for am in amostras:
        b = int(float(am.tempo_s) // bucket_s)
        if balde_atual is None or b != balde_atual:
            if atual:
                grupos.append(atual)
            atual, balde_atual = [], b
        atual.append(am)
    if atual:
        grupos.append(atual)
    return grupos


def etapa_analise_vlm(
    groq_client: Groq,
    amostras: list[Amostra],
    descricao_processo: str,
    memoria: dict,
    progress_cb: ProgressCb,
    conhecimento_adquirido: str = "",
    zona_posto: str | None = None,
    intervalo_s: float = DEFAULT_INTERVALO_AMOSTRAGEM_S,
    frente_maquina: str | None = None,
    movimento_por_minuto: dict | None = None,
) -> list[dict]:
    """Analisa as amostras com o VLM.

    Fase 85 — SEQUÊNCIA. As amostras consecutivas de um mesmo minuto vão numa
    ÚNICA chamada, em ordem cronológica, e a pergunta deixa de ser "o que se vê
    neste frame" para ser "o que aconteceu ao longo destes 60 segundos". Uma
    foto de alguém em pé perto do torno é ambígua por natureza — nenhum humano
    acertaria com uma foto só. Três frames idênticos, por outro lado, são
    evidência de imobilidade, e é isso que torna a improdutividade OBSERVÁVEL.
    A saída continua sendo UMA OBSERVAÇÃO POR INSTANTE: o downstream não muda,
    e o trecho segue divisível no meio do minuto se a ação mudar.
    De quebra, sai mais barato — o texto do prompt passa a ser pago 1× por
    minuto em vez de 1× por amostra.

    Gate de repetição (Fase 23, KV_GATE_ENABLE): por track, mantém uma ÂNCORA e
    decide barato se a amostra REPETE o padrão — herdando a descrição sem gastar
    VLM. Fase 85 põe um TETO nessa herança (`KV_GATE_MAX_REPETICOES`): passado o
    teto, o gate PARA de herdar e força uma chamada, porque "parado há 2
    minutos" é informação e não é a mesma coisa que "parado há 8 segundos".

    Fase 28: `zona_posto` (nome da zona posto_operador) liga a síntese de
    POSTO VAZIO — amostras vazias viram observação determinística sem VLM."""
    progress_cb("vlm", 0, f"Analisando {len(amostras)} amostras com VLM"
                + (" · sequência ON" if _SEQ_ENABLE else "")
                + (" · gate ON" if _GATE_ENABLE else ""))
    observacoes: list[dict] = []

    def _emitir_posto_vazio(
        am: Amostra,
        narrativa: str | None,
        *,
        auditoria: str | None = None,
        amostras_zona: int | None = None,
    ) -> bool:
        """Emite a única forma canônica de posto vazio desta etapa.

        O retorno permite aos testes provar que um fallback não sumiu. Campos
        de auditoria são opcionais: sem o SQL da Fase 110, a persistência os
        remove e repete o insert sem derrubar a ingestão.
        """
        if (
            getattr(am, "presenca_safety_gate", False)
            or not (_POSTO_VAZIO_ENABLE and zona_posto and not am.operador_presente)
        ):
            return False
        observacao = {
            "tempo_s": am.tempo_s,
            "frame_idx": am.frame_idx,
            "track_id": POSTO_VAZIO_TID,
            "descricao": POSTO_VAZIO_DESC,
            # Não há pessoa — a caixa é NULA, não uma caixa sintética.
            "bbox": None, "bbox_cam": None, "bbox_dim": None,
            "zona": zona_posto,
            "papel": "posto_vazio",
            "origem_gate": "posto_vazio",
            "mudanca_contexto": False,
            "maquina": None, "imovel": None,
            # O QUE TORNA O "POSTO VAZIO" AUDITÁVEL: quando houve narrativa
            # do minuto, ela acompanha também a ausência determinística.
            "narrativa": narrativa,
        }
        if auditoria:
            observacao["fora_do_posto"] = auditoria
        if amostras_zona is not None:
            observacao["fora_amostras_zona"] = int(amostras_zona)
        observacoes.append(observacao)
        return True

    ancoras: dict[int, dict] = {}   # track_id → {kpts, crop, zona, descricao}
    # Fase 92: dimensões do quadro, para normalizar centro/altura da âncora.
    _dim0 = next((a.dim for a in amostras if getattr(a, "dim", None)), None)
    _W_VID, _H_VID = (_dim0 if _dim0 else (1280, 720))
    # Fase 85: quantas amostras SEGUIDAS cada track já herdou do gate. É o
    # contador que faz o teto existir.
    repeticoes_seguidas: dict[int, int] = {}
    n_completo = n_binario = n_repeticao = n_teto_gate = 0
    # Fase 34: última ação CONHECIDA do operador (qualquer track) — herdada
    # nas pontes temporais e quando o VLM devolve "ação não identificada".
    # Fase 85: com TETO — ver `_HERANCA_MAX_SEGUIDAS`.
    ultima_desc_op: str | None = None
    ultimo_tid_op: int | None = None
    n_herdadas = n_teto_heranca = n_interpoladas = 0
    # Lista de um elemento para o contador atravessar o laço de grupos sem
    # virar `global`. Conta CHAMADAS, não amostras: um grupo inteiro cabe numa.
    n_chamadas_fora = [0]
    n_ancora_herdada = n_ancora_nova = 0
    heranca_seguidas = 0

    def _eh_indefinida(d: str | None) -> bool:
        return bool(d) and ("não identificada" in d or "nao identificada" in d)

    grupos = _agrupar_amostras(amostras, _SEQ_BUCKET_S if _SEQ_ENABLE else 0.0)
    feitas = 0
    for grupo in grupos:
        # Fase 89: o minuto do grupo — a chave do movimento medido. O bucket da
        # sequência e o do movimento são o mesmo minuto de propósito.
        minuto = int((grupo[0].tempo_s if grupo else 0.0) // 60.0)
        # ── 1) Classifica cada amostra do grupo, sem chamar nada ainda ──
        plano: list[tuple[str, Amostra]] = []
        for am in grupo:
            if getattr(am, "identidade_autoritativa", False):
                # Fase 111D: cam1 já conhece a identidade física deste slot.
                # A cam2 continua evidência de atividade, mas não pode resgatar
                # outra pessoa nem esconder o operador visto fora da ROI.
                if am.identidade_estado == "fora" and am.fora_posto:
                    plano.append(("fora_posto", am))
                elif am.pessoas:
                    plano.append(("cam1", am))
                elif getattr(am, "presenca_safety_gate", False):
                    # C1 tem autoridade apenas para negar o vazio. Não desfaz
                    # identidade positiva, mas veta a ausência autoritativa se
                    # o detector bruto viu alguém (ou falhou tecnicamente).
                    plano.append(("inconclusivo", am))
                else:
                    plano.append(("vazio", am))
                continue
            tem_op_cam1 = any(p.get("papel") == "operador" for p in am.pessoas)
            if (
                zona_posto
                and not tem_op_cam1
                and (
                    am.operador_presente
                    or (
                        PRODUTIVIDADE_OPERADOR_ESTRUTURADA
                        and int(getattr(am, "n_posto_cam2", 0) or 0) > 0
                    )
                )
            ):
                # Fase 33: RESGATE pela lateral — a cam1 não vê o operador
                # (oclusão total) e a cam2 vê.
                plano.append(("ponte" if am.operador_ponte else "resgate", am))
            elif (
                not am.pessoas
                and getattr(am, "presenca_safety_gate", False)
            ):
                # Estado interno já existente: há evidência suficiente para
                # proibir vazio, mas insuficiente para atribuir operador.
                plano.append(("inconclusivo", am))
            elif (
                PRODUTIVIDADE_OPERADOR_ESTRUTURADA
                and not am.pessoas
                and am.operador_presente is None
            ):
                plano.append(("inconclusivo", am))
            elif (
                # ⭐ Fase 110 — O OPERADOR SAIU DO POSTO, e isso não é posto
                # vazio. Vem DEPOIS de `resgate`/`ponte` de propósito: a cam2
                # vendo alguém DENTRO do polígono ganha da cam1 vendo alguém
                # fora dele, e a ponte temporal ganha de tudo (uma saída de 10 s
                # continua sendo "presente", como sempre foi).
                _FORA_MODO == "on"
                and zona_posto
                and not am.pessoas
                and not am.operador_presente
                and am.fora_posto
            ):
                plano.append(("fora_posto", am))
            elif not am.pessoas:
                plano.append(("vazio", am))
            else:
                plano.append(("cam1", am))

        # ── 2) Gate sobre o GRUPO: alguém aqui precisa de VLM? ──
        # A decisão continua sendo por amostra/track (é ela que dá a economia),
        # mas a CHAMADA é uma só para o grupo inteiro.
        decisoes: dict[int, dict[int, tuple[str, str]]] = {}
        precisa_vlm = False
        idx_cam1 = [i for i, (tipo, _) in enumerate(plano) if tipo == "cam1"]
        for i in idx_cam1:
            am = plano[i][1]
            if not _GATE_ENABLE:
                precisa_vlm = True
                continue
            d_am: dict[int, tuple[str, str]] = {}
            if zona_posto and len(am.pessoas) > 1:
                # Identidade ambígua nunca herda âncora. O custo de uma chamada
                # extra é pequeno; o custo de descrever o visitante como
                # operador contamina presença e produtividade juntas.
                d_am = {p["track_id"]: ("analisar", "") for p in am.pessoas}
                decisoes[i] = d_am
                precisa_vlm = True
                continue
            for p in am.pessoas:
                tid = p["track_id"]
                anc = ancoras.get(tid)
                if anc is None:
                    # Fase 92: antes de pagar uma análise cheia, pergunta se
                    # este track novo é a CONTINUAÇÃO de um que acabou de
                    # sumir. Não remapeia ID: só empresta a âncora.
                    _vivos = {q["track_id"] for q in am.pessoas}
                    _tid_anc, _motivo = ancora_por_continuidade(
                        ancoras, tid, p, am, _W_VID, _H_VID, _vivos)
                    if _tid_anc is not None:
                        anc = ancoras[_tid_anc]
                        ancoras[tid] = dict(anc)      # o novo id passa a tê-la
                        n_ancora_herdada += 1
                    else:
                        d_am[tid] = ("analisar", "")  # 1ª vez do track → analisa
                        precisa_vlm = True
                        n_ancora_nova += 1
                        continue
                if repeticoes_seguidas.get(tid, 0) >= _GATE_MAX_REPETICOES:
                    # TETO: já herdou demais seguidas. Parar de herdar aqui é o
                    # que transforma "parado há 2 minutos" numa observação em
                    # vez de num eco da última coisa que ele fez de fato.
                    d_am[tid] = ("analisar", "")
                    precisa_vlm = True
                    n_teto_gate += 1
                    continue
                dist = _gate_distancia(anc, p)
                if dist <= _GATE_LIMIAR_IGUAL:
                    d_am[tid] = ("repeticao_pose", anc["descricao"])
                elif dist >= _GATE_LIMIAR_DIFERENTE:
                    d_am[tid] = ("analisar", "")
                    precisa_vlm = True
                else:
                    n_binario += 1
                    if _gate_vlm_binario(groq_client, am, anc["descricao"]):
                        d_am[tid] = ("repeticao_gate", anc["descricao"])
                    else:
                        d_am[tid] = ("analisar", "")
                        precisa_vlm = True
            decisoes[i] = d_am

        if idx_cam1 and zona_posto and PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
            # O gate continua economizando DESCRIÇÃO repetida por track, mas
            # não pode suprimir a pergunta comercial do minuto. Uma sequência
            # ocupada sempre passa pelo contrato estruturado de identidade e
            # produtividade; é uma chamada por bucket, não uma por pessoa.
            precisa_vlm = True

        # ── 3) As chamadas do grupo: no máximo uma cam1 + uma cam2 ──
        descricoes_seq: dict[int, dict[int, str]] = {}
        interp: set = set()
        if idx_cam1 and precisa_vlm:
            descricoes_seq = _analisar_sequencia_vlm(
                groq_client, [plano[i][1] for i in idx_cam1], descricao_processo,
                memoria, intervalo_s, conhecimento_adquirido,
                frente_maquina=frente_maquina,
                movimento=(movimento_por_minuto or {}).get(minuto),
            )
            # Reindexa: a função devolve o índice DENTRO da lista que recebeu.
            descricoes_seq = {idx_cam1[k]: v for k, v in descricoes_seq.items()
                              if 0 <= k < len(idx_cam1)}
            # Fase 90 — INTERPOLA OS BURACOS. `_subamostrar` manda no máximo
            # KV_SEQUENCIA_MAX_IMG-1 quadros; os demais NÃO recebiam descrição
            # e a observação morria no `if not desc: continue`. O efeito não
            # era perder detalhe: era o minuto se PARTIR (o intervalo passa da
            # janela de continuidade) e o `tempo_obs_s` — denominador de toda
            # métrica — cair junto. Com 12 amostras e MAX_IMG=6 a cobertura
            # medida caía de 55s para 25s no minuto.
            #
            # Interpolar aqui é honesto de um jeito que a ponte não é: o VLM
            # analisou o minuto COMO SEQUÊNCIA e descreveu os quadros de t e
            # t+10s; o de t+5s está ENTRE dois quadros vistos. Marcado como
            # `interpolado_sequencia` e — o que importa — NÃO VOTA na
            # concordância.
            interp = _interpolar_sequencia(descricoes_seq, idx_cam1)
            n_interpoladas += len(interp)
            n_completo += 1

        idx_resg = [i for i, (tipo, _) in enumerate(plano) if tipo == "resgate"]
        desc_resgate: dict[int, str] = {}
        if idx_resg:
            bruto = _analisar_sequencia_cam2(
                groq_client, [plano[i][1] for i in idx_resg], descricao_processo,
                memoria, intervalo_s, conhecimento_adquirido, zona_desc=zona_posto,
            )
            desc_resgate = {idx_resg[k]: v for k, v in bruto.items()
                            if 0 <= k < len(idx_resg)}
            n_completo += 1

        # ⭐ Fase 110 — a descrição do que ele faz FORA do posto. Teto por vídeo
        # porque é chamada de VLM de verdade: no pior caso ela substitui todos os
        # minutos que hoje são `posto_vazio`, e o Groq tem cota diária.
        idx_fora = [i for i, (tipo, _) in enumerate(plano) if tipo == "fora_posto"]
        desc_fora: dict[int, dict] = {}
        auditoria_fora: dict[int, str] = {}
        if idx_fora:
            if n_chamadas_fora[0] >= _FORA_MAX_CHAMADAS:
                log.info("[fora-do-posto] teto de %d chamadas atingido neste vídeo "
                         "— %d amostra(s) voltam a contar como posto vazio",
                         _FORA_MAX_CHAMADAS, len(idx_fora))
                auditoria_fora.update({i: "teto_chamadas" for i in idx_fora})
            else:
                try:
                    bruto_f = _analisar_sequencia_fora(
                        groq_client, [plano[i][1] for i in idx_fora],
                        descricao_processo, memoria, intervalo_s,
                        conhecimento_adquirido, zona_desc=zona_posto,
                    )
                except Exception as e:   # noqa: BLE001
                    # A função já trata falhas do provider, mas esta fronteira
                    # também protege exceções de parser/subamostragem e dublês
                    # de teste. Uma falha opcional nunca pode apagar o slot.
                    log.warning("[fora-do-posto] análise falhou (%s) — "
                                "volta a posto vazio", e)
                    bruto_f = {}
                desc_fora = {idx_fora[k]: v for k, v in bruto_f.items()
                             if 0 <= k < len(idx_fora)}
                n_chamadas_fora[0] += 1
                n_completo += 1

        # ═════════════════════════════════════════════════════════════
        # ⭐ A NARRATIVA É DO MINUTO — e o minuto tem MAIS TIPOS DE
        #    OBSERVAÇÃO do que só a cam1.
        #
        # O código repetia em três comentários que "a narrativa é do MINUTO,
        # não do instante — por isso ela viaja igual em todas as observações do
        # grupo". Só que ela era lida dentro do ramo `tipo == "cam1"` e
        # anexada só ali. As observações de POSTO VAZIO, de INCONCLUSIVO e as
        # da CAM2 (resgate/ponte) saíam sem ela — e são justamente as mais
        # numerosas quando o posto esvazia, que é o caso em que o gestor mais
        # precisa de contexto para julgar.
        #
        # Efeito no banco: a maioria das linhas com `narrativa` NULA. Não era o
        # modelo falhando; era a narrativa não atravessando a fronteira.
        #
        # ⚠️ Quando o minuto INTEIRO foi vazio não há chamada de VLM na cam1,
        # `descricoes_seq` vem vazio e a narrativa continua NULA — e está certo:
        # não houve nada para observar. "Não temos" segue diferente de
        # "não pedimos".
        #
        # ⚠️ `orientacao`, `maos_maquina` e `trabalho` continuam NULOS nessas
        # observações, DE PROPÓSITO: não há pessoa (posto vazio/inconclusivo) ou
        # não há pose retida (cam2). Preencher seria fabricar medida onde só há
        # ausência — o erro que este projeto já cometeu quatro vezes.
        # ═════════════════════════════════════════════════════════════
        narrativa_grupo = next(
            (b.get("resumo") for b in descricoes_seq.values()
             if isinstance(b, dict) and b.get("resumo")),
            None,
        )

        # ── 4) Emite as observações, em ordem de tempo ──
        for i, (tipo, am) in enumerate(plano):
            if tipo in ("resgate", "ponte"):
                origem_resgate = "resgate_cam2" if tipo == "resgate" else "ponte_temporal"
                _r = desc_resgate.get(i) or {}
                desc_cam2 = _r.get("acao") if tipo == "resgate" else ultima_desc_op
                cena_maq, cena_imovel = _r.get("maquina"), _r.get("imovel")
                cena_trabalho = (_r.get("trabalho") if tipo == "resgate" else None)
                estado_cam2 = (
                    _r.get("operador_estado")
                    if tipo == "resgate"
                    else "incerto"
                )
                if tipo == "ponte":
                    # A ponte herda SEM ver imagem nenhuma. Com teto, como todo
                    # o resto: passado o limite, o operador presente por
                    # continuidade deixa de "estar operando" por herança.
                    if heranca_seguidas >= _HERANCA_MAX_SEGUIDAS:
                        desc_cam2 = None
                        n_teto_heranca += 1
                    else:
                        heranca_seguidas += 1
                        n_herdadas += 1
                elif _eh_indefinida(desc_cam2) and ultima_desc_op:
                    if heranca_seguidas >= _HERANCA_MAX_SEGUIDAS:
                        n_teto_heranca += 1      # fica "ação não identificada"
                    else:
                        desc_cam2 = ultima_desc_op
                        origem_resgate = "indefinida_herdada"
                        heranca_seguidas += 1
                        n_herdadas += 1
                elif desc_cam2 and not _eh_indefinida(desc_cam2):
                    heranca_seguidas = 0
                if PRODUTIVIDADE_OPERADOR_ESTRUTURADA and not desc_cam2:
                    desc_cam2 = "ação não identificada"
                    origem_resgate = "falha_descricao_vlm"
                    estado_cam2 = "incerto"
                if desc_cam2:
                    if not PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
                        papel_cam2 = "operador"
                    elif tipo == "ponte":
                        papel_cam2 = None
                    elif estado_cam2 == "identificado":
                        papel_cam2 = "operador"
                    elif estado_cam2 == "ausente":
                        papel_cam2 = "visitante"
                    else:
                        papel_cam2 = None
                    # Fase 82: a caixa vem da câmera que VIU a pessoa; na ponte
                    # ninguém foi visto em instante nenhum, e ali ela é nula.
                    bbox_obs = am.bbox_cam2 if tipo == "resgate" else None
                    observacoes.append({
                        "tempo_s": am.tempo_s,
                        "frame_idx": am.frame_idx,
                        "track_id": (ultimo_tid_op if tipo == "ponte"
                                     and ultimo_tid_op is not None else OPERADOR_CAM2_TID),
                        "descricao": desc_cam2,
                        "bbox": bbox_obs,
                        "bbox_cam": "cam2" if bbox_obs else None,
                        "bbox_dim": am.dim_cam2 if bbox_obs else None,
                        "zona": zona_posto,
                        "papel": papel_cam2,
                        "origem_gate": origem_resgate,
                        "mudanca_contexto": origem_resgate == "resgate_cam2",
                        "maquina": cena_maq,
                        "imovel": cena_imovel,
                        "maos_maquina": (
                            True
                            if papel_cam2 == "operador"
                            and tipo == "resgate"
                            and getattr(am, "maos_cam2", False)
                            else None
                        ),
                        # Sem pose retida na cam2 — orientação é ausência, não zero.
                        "orientacao": None,
                        "narrativa": narrativa_grupo,
                        "trabalho": (
                            cena_trabalho if papel_cam2 == "operador" else None
                        ),
                        "produtividade_motivo": _r.get("produtividade_motivo"),
                        "produtividade_observada": (
                            papel_cam2 == "operador"
                            and tipo == "resgate"
                            and isinstance(cena_trabalho, bool)
                        ),
                    })
                    if papel_cam2 == "operador" and not _eh_indefinida(desc_cam2):
                        ultima_desc_op = desc_cam2
                        if origem_resgate == "resgate_cam2":
                            ultimo_tid_op = OPERADOR_CAM2_TID
                continue

            if tipo == "inconclusivo":
                observacoes.append({
                    "tempo_s": am.tempo_s,
                    "frame_idx": am.frame_idx,
                    "track_id": POSTO_INCONCLUSIVO_TID,
                    "descricao": POSTO_INCONCLUSIVO_DESC,
                    "bbox": None, "bbox_cam": None, "bbox_dim": None,
                    "zona": zona_posto,
                    "papel": None,
                    "origem_gate": "confirmacao_presenca_indisponivel",
                    "mudanca_contexto": False,
                    "maquina": None, "imovel": None,
                    "maos_maquina": None, "orientacao": None,
                    "trabalho": None,
                    # O minuto foi observado mesmo quando ESTE instante não foi.
                    "narrativa": narrativa_grupo,
                    "produtividade_motivo": "sem_leitura",
                    "produtividade_observada": False,
                })
                continue

            if tipo == "fora_posto":
                # ⭐ O OPERADOR ESTÁ NO QUADRO, FORA DO POSTO. Uma observação
                # por pessoa reconhecida pelo teste do passante.
                _f = desc_fora.get(i) or {}
                acao = (_f.get("acao") or "").strip()
                if not acao:
                    # Falha/JSON inválido/ação ausente/teto: restaura
                    # EXATAMENTE o estado numérico anterior. A auditoria diz
                    # por que o candidato reconhecido não virou atividade.
                    _emitir_posto_vazio(
                        am,
                        _f.get("resumo") or narrativa_grupo,
                        auditoria=auditoria_fora.get(i) or "falha_vlm",
                        amostras_zona=max(
                            (int(p.get("_fora_amostras_zona") or 0)
                             for p in am.fora_posto),
                            default=0,
                        ),
                    )
                    continue
                for pf in am.fora_posto:
                    observacoes.append({
                        "tempo_s": am.tempo_s,
                        "frame_idx": am.frame_idx,
                        # ⚠️ TRACK ID REAL, não sentinela. `etapa_segmentar_eventos`
                        # agrupa por track: um `-4` compartilhado fundiria duas
                        # pessoas diferentes num evento só.
                        "track_id": pf.get("track_id"),
                        "descricao": acao,
                        "bbox": list(pf["bbox"]),
                        "bbox_cam": "cam1",
                        "bbox_dim": am.dim,
                        "zona": zona_posto,
                        "papel": PAPEL_OPERADOR_FORA,
                        "origem_gate": "fora_do_posto",
                        "mudanca_contexto": True,
                        # ⛔ Nada de estado da máquina, mãos, orientação ou
                        # trabalho: ele NÃO está no posto. `trabalho` significa
                        # "isto é serviço do posto?" e a pergunta não cabe.
                        "maquina": None, "imovel": None,
                        "maos_maquina": None, "orientacao": None,
                        "trabalho": None,
                        "produtividade_motivo": None,
                        "produtividade_observada": False,
                        "narrativa": _f.get("resumo") or narrativa_grupo,
                        # Auditoria do teste do passante: sem isto a decisão
                        # não é reconstituível depois.
                        "fora_do_posto": pf.get("_fora_motivo") or "operador",
                        "fora_amostras_zona": pf.get("_fora_amostras_zona"),
                    })
                if getattr(am, "identidade_autoritativa", False):
                    # R1 fora não apaga quem está fisicamente dentro. Esses
                    # tracks já foram travados como visitantes pela 111D; a
                    # observação é determinística e não custa outra chamada VLM.
                    for visitante in am.pessoas:
                        observacoes.append({
                            "tempo_s": am.tempo_s,
                            "frame_idx": am.frame_idx,
                            "track_id": visitante.get("track_id"),
                            "descricao": "outra pessoa visível no posto",
                            "bbox": list(visitante.get("bbox") or []),
                            "bbox_cam": "cam1",
                            "bbox_dim": am.dim,
                            "zona": visitante.get("zona") or zona_posto,
                            "papel": "visitante",
                            "origem_gate": "identidade_autoritativa_visitante",
                            "mudanca_contexto": True,
                            "maquina": None,
                            "imovel": None,
                            "maos_maquina": None,
                            "orientacao": None,
                            "trabalho": None,
                            "produtividade_motivo": "sem_leitura",
                            "produtividade_observada": False,
                            "interlocutor_evidencia": None,
                            "narrativa": _f.get("resumo") or narrativa_grupo,
                        })
                continue

            if tipo == "vazio":
                _emitir_posto_vazio(
                    am,
                    narrativa_grupo,
                    auditoria=am.fora_auditoria,
                    amostras_zona=am.fora_auditoria_amostras_zona,
                )
                continue

            # tipo == "cam1"
            _bloco = descricoes_seq.get(i) or {}
            do_instante = _bloco.get("acoes") or {}
            cena_maq, cena_imovel = _bloco.get("maquina"), _bloco.get("imovel")
            cena_trabalho = _bloco.get("trabalho")
            # Cai no grupo quando ESTE índice veio de interpolação (bloco sem
            # `resumo` próprio). O valor é o mesmo por construção — o que muda
            # é não perder a narrativa nos instantes interpolados.
            cena_narrativa = _bloco.get("resumo") or narrativa_grupo
            cena_motivo = _bloco.get("produtividade_motivo")
            cena_interlocutor = _bloco.get("interlocutor_evidencia")
            operador_estado = _bloco.get("operador_estado")
            operador_tid = _bloco.get("operador_track_id")
            if PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
                # Falha, ausência de campo e `incerto` permanecem abstenção.
                # Nem candidato único autoriza ressuscitar a eleição por bbox.
                if operador_estado not in {"identificado", "ausente", "incerto"}:
                    operador_estado = "incerto"
                    operador_tid = None
            else:
                # Compatibilidade V8 atrás da flag de rollback.
                if operador_estado == "incerto" and len(am.pessoas) == 1:
                    unico = am.pessoas[0]
                    if unico.get("papel") == "operador":
                        operador_estado = "identificado"
                        operador_tid = unico.get("track_id")
                if operador_estado not in {"identificado", "ausente", "incerto"}:
                    if len(am.pessoas) == 1 and am.pessoas[0].get("papel") == "operador":
                        operador_estado = "identificado"
                        operador_tid = am.pessoas[0].get("track_id")
                    elif len(am.pessoas) > 1:
                        operador_estado = "incerto"
                        operador_tid = None
            autoridade_slot = bool(getattr(am, "identidade_autoritativa", False))
            if autoridade_slot:
                if am.identidade_estado == "dentro":
                    tid_autoritativo = am.identidade_track_id
                    # A decisão `trabalho` pertence ao operador que o VLM
                    # afirmou analisar. Se ele escolheu outro track, identidade
                    # continua autoritativa mas produtividade se abstém.
                    vlm_coincide = (
                        operador_estado == "identificado"
                        and _num(operador_tid) == _num(tid_autoritativo)
                    )
                    if not vlm_coincide:
                        cena_trabalho = None
                        cena_motivo = "sem_leitura"
                        cena_interlocutor = None
                    operador_estado = "identificado"
                    operador_tid = tid_autoritativo
                else:
                    operador_estado = "ausente"
                    operador_tid = None
                    cena_trabalho = None
                    cena_motivo = "sem_leitura"
                    cena_interlocutor = None
            d_am = decisoes.get(i, {})
            for p in am.pessoas:
                tid = p["track_id"]
                if autoridade_slot:
                    papel_obs = p.get("papel")
                elif not PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
                    papel_obs = p.get("papel")
                elif operador_estado == "identificado":
                    papel_obs = "operador" if tid == operador_tid else "visitante"
                elif operador_estado == "ausente":
                    papel_obs = "visitante"
                elif operador_estado == "incerto":
                    papel_obs = None
                else:
                    papel_obs = p.get("papel")
                if PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
                    # A chamada V9 já observou este instante para decidir
                    # identidade/produtividade. Usar a âncora antiga só no
                    # texto criava contradição (ex.: descrição "operando" com
                    # trabalho=False) e ainda registrava n_amostras=0. A saída
                    # fresca é a única descrição válida; quadros realmente
                    # interpolados continuam explicitamente não observados.
                    desc = do_instante.get(tid)
                    origem_gate = (
                        "interpolado_sequencia" if i in interp else "analisado"
                    )
                    repeticoes_seguidas[tid] = 0
                elif not _GATE_ENABLE:
                    desc = do_instante.get(tid)
                    origem_gate = "analisado"
                else:
                    origem, desc_ancora = d_am.get(tid, ("analisar", ""))
                    if origem == "analisar":
                        desc = do_instante.get(tid)
                        # Fase 90: o quadro não foi enviado; a descrição veio
                        # do quadro analisado vizinho. Cobre o tempo, não vota.
                        origem_gate = ("interpolado_sequencia" if i in interp
                                       else "analisado")
                        repeticoes_seguidas[tid] = 0
                    else:
                        desc = desc_ancora            # herda o padrão (sem token)
                        origem_gate = origem
                        n_repeticao += 1
                        repeticoes_seguidas[tid] = repeticoes_seguidas.get(tid, 0) + 1
                if not desc:
                    if not PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
                        continue
                    # Detecção/posição continuam sendo uma observação mesmo
                    # quando o VLM falha ou omite uma pessoa. Apagar a linha
                    # apagava presença junto com a descrição. A origem deixa
                    # explícito que ninguém descreveu este instante e não vota
                    # como evidência semântica.
                    desc = "ação não identificada"
                    origem_gate = "falha_descricao_vlm"
                # Fase 34/85: operador com "ação não identificada" herda a última
                # ação conhecida — mas só por `_HERANCA_MAX_SEGUIDAS` amostras.
                # Sem o teto, um trecho longo de ilegível vira trabalho
                # produtivo por eco, que é a conversão mais silenciosa de
                # DESCONHECIDO em PRODUTIVO que este sistema tinha.
                if papel_obs == "operador":
                    if _eh_indefinida(desc) and ultima_desc_op:
                        if heranca_seguidas >= _HERANCA_MAX_SEGUIDAS:
                            n_teto_heranca += 1      # fica "ação não identificada"
                        else:
                            desc = ultima_desc_op
                            origem_gate = "indefinida_herdada"
                            heranca_seguidas += 1
                            n_herdadas += 1
                    elif not _eh_indefinida(desc):
                        ultima_desc_op = desc
                        ultimo_tid_op = tid
                        heranca_seguidas = 0
                # Âncora só se instala quando a amostra foi de fato ANALISADA.
                if _GATE_ENABLE and origem_gate == "analisado":
                    ancoras[tid] = {
                        "kpts": p.get("kpts"), "crop": p.get("crop"),
                        "zona": p.get("zona"), "descricao": desc,
                        # Fase 92: quando/onde/quem — o que a herança por
                        # continuidade precisa para decidir se este track e o
                        # próximo são a mesma pessoa.
                        "t": am.tempo_s, "centro": _centro_rel(p, _W_VID, _H_VID),
                        "papel": papel_obs,
                        "n_no_posto": _n_no_posto(am),
                    }
                observacoes.append({
                    "tempo_s": am.tempo_s,
                    "frame_idx": am.frame_idx,
                    "track_id": tid,
                    "descricao": desc,
                    "bbox": p["bbox"],
                    "bbox_cam": "cam1",
                    "bbox_dim": am.dim,
                    "zona": p["zona"],
                    "papel": papel_obs,
                    # Fase 82: `maos_maquina` era LIDO em montar_fato_evento e a
                    # chave morria aqui — o sinal do punho na zona da máquina
                    # nunca entrou em fato nenhum.
                    "maos_maquina": p.get("maos_maquina"),
                    # Fase 97: a ORIENTAÇÃO passa a ser persistida. Ela era
                    # calculada desde a Fase 86, injetada no prompt e JOGADA
                    # FORA — e agora ela decide produtividade, então tinha de
                    # existir no dado. É a terceira vez que este padrão morde
                    # (maquina/imovel na 88, t_ini/t_fim na 92): sinal que só
                    # existe em memória não pode ser verificado nem auditado.
                    "orientacao": p.get("orientacao"),
                    "origem_gate": origem_gate,
                    "mudanca_contexto": origem_gate == "analisado",
                    # Fase 86: o discriminador viaja com a observação até o
                    # cluster, que particiona por ele.
                    "maquina": cena_maq,
                    "imovel": cena_imovel,
                    # A decisão é sobre o operador escolhido, não sobre a cena
                    # inteira. Visitante nunca herda o mesmo booleano.
                    "trabalho": (
                        cena_trabalho
                        if papel_obs == "operador"
                        or (not PRODUTIVIDADE_OPERADOR_ESTRUTURADA and not autoridade_slot)
                        else None
                    ),
                    "produtividade_observada": (
                        PRODUTIVIDADE_OPERADOR_ESTRUTURADA
                        and papel_obs == "operador"
                        and i not in interp
                        and isinstance(cena_trabalho, bool)
                    ),
                    "produtividade_motivo": (
                        cena_motivo if papel_obs == "operador" else None
                    ),
                    # Associação + S/V do recorte pertencem exclusivamente ao
                    # operador escolhido neste instante. A observação do
                    # visitante não herda a decisão da cena.
                    "interlocutor_evidencia": (
                        cena_interlocutor
                        if papel_obs == "operador" and i not in interp
                        else None
                    ),
                    # A narrativa é do MINUTO inteiro, então é a MESMA em todas
                    # as observações do grupo — inclusive nas de visitante: ela
                    # conta a cena, não julga a pessoa.
                    "narrativa": cena_narrativa,
                    # Fase 91: o que a LATERAL contou no mesmo instante. Viaja
                    # junto para o fato das camadas — sem virar observação
                    # própria, porque descrever a segunda pessoa exigiria uma
                    # chamada de VLM que não podemos pagar.
                    "n_posto_cam2": am.n_posto_cam2,
                    "n_cena_cam2": am.n_cena_cam2,
                })

        feitas += len(grupo)
        pct = int(feitas / max(1, len(amostras)) * 100)
        progress_cb("vlm", pct,
                    f"{feitas}/{len(amostras)} amostras · {len(observacoes)} observações")

    if n_ancora_herdada or n_ancora_nova:
        # A CONTAGEM QUE O DONO PEDIU PARA VER ANTES DE CONFIAR: quantas
        # âncoras sobreviveram à troca de ID, contra quantas custaram análise.
        log.info("[ancora] %d herdada(s) por continuidade · %d nova(s) "
                 "(pagaram análise) · KV_COSTURA_ANCORA=%s",
                 n_ancora_herdada, n_ancora_nova,
                 "on" if _COSTURA_ANCORA else "off")
    if n_herdadas or n_teto_heranca:
        log.info("[operador] %d observação(ões) herdaram a última ação conhecida; "
                 "%d recusadas pelo teto de herança (KV_HERANCA_MAX_SEGUIDAS=%d).",
                 n_herdadas, n_teto_heranca, _HERANCA_MAX_SEGUIDAS)
    if _GATE_ENABLE:
        chamadas = n_completo + n_binario
        base = len(amostras)
        economia = round((1 - chamadas / max(1, base)) * 100, 1)
        log.info(
            "[gate] %d amostras em %d grupo(s) → %d chamada(s) de sequência + %d "
            "binário (%d repetições herdadas, %d forçadas pelo teto) · ~%.0f%% "
            "menos chamadas",
            base, len(grupos), n_completo, n_binario, n_repeticao, n_teto_gate, economia,
        )
        etapa_analise_vlm._ultima_economia = {   # type: ignore[attr-defined]
            "amostras": base, "grupos": len(grupos), "vlm_completo": n_completo,
            "vlm_binario": n_binario, "repeticoes": n_repeticao,
            "teto_gate": n_teto_gate, "teto_heranca": n_teto_heranca,
            # Fase 92: o ganho da costura de âncora, mensurável por vídeo.
            "ancora_herdada": n_ancora_herdada,
            "ancora_nova": n_ancora_nova,
            "economia_pct": economia,
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
    cache_labels: dict[str, str] | None = None,
) -> tuple[dict, dict[str, str], Callable[..., str], Callable[..., str]]:
    """Retorna (mapa_desc_label, catalogo, label_de, origem_de).

    Fase 86: `label_de` e `origem_de` passaram a receber o DISCRIMINADOR da
    cena — `(descricao, maquina, imovel)`. A descrição sozinha deixou de ser
    chave suficiente: a mesma frase com a máquina em ciclo e com a máquina
    parada descreve situações opostas.

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

    # Fase 86 — PARTIÇÃO PELO DISCRIMINADOR. A unidade do cluster deixa de ser
    # a descrição e passa a ser (descrição, cena). Duas frases idênticas com
    # estados de máquina diferentes são situações OPOSTAS e não podem cair no
    # mesmo grupo — aqui elas nem chegam na mesma lista.
    cenas_por_desc: dict[str, set] = defaultdict(set)
    for o in observacoes_brutas:
        cenas_por_desc[(o["descricao"] or "").lower().strip()].add(
            chave_cena(o.get("maquina"), o.get("imovel")))

    pares: list[tuple[str, str]] = []      # (descricao, chave_cena)
    for d in descricoes_unicas:
        for ck in sorted(cenas_por_desc.get(d.lower().strip(), {""})):
            pares.append((d, ck))

    descricoes_conhecidas: dict[tuple[str, str], str] = {}
    novas_por_cena: dict[str, list[str]] = defaultdict(list)
    _cache = cache_labels or {}
    n_cache = 0
    for d, ck in pares:
        d_lower = d.lower().strip()
        if d_lower == POSTO_VAZIO_DESC:
            # Fase 28: label determinístico — nunca passa pela LLM.
            continue
        if d_lower in correcoes:
            descricoes_conhecidas[(d_lower, ck)] = correcoes[d_lower]
            continue
        # Fase 86 — CONSISTÊNCIA. Frase idêntica já clusterizada neste processo
        # reusa o label, em vez de ser re-perguntada a um modelo estocástico.
        #
        # Fase 99 — o cache deixou de olhar a cena. Ele exigia que a cena
        # BATESSE com o sufixo do label guardado, para não desfazer a partição.
        # Só que o histórico vem cheio de `x_ciclo`/`x_parada` e a leitura agora
        # limpa o sufixo: o label guardado NUNCA mais bate com uma cena
        # discriminada. O efeito, com a partição ligada, era o cache nunca
        # acertar e o sistema PAGAR uma chamada por descrição já conhecida —
        # para receber de volta um nome que a guarda ia limpar de novo.
        # Não há mais partição a preservar: nenhum rótulo pode nomear estado,
        # então a mesma frase é o mesmo rótulo, em qualquer cena.
        em_cache = limpar_sufixo_estado(_cache.get(d_lower))
        if em_cache:
            descricoes_conhecidas[(d_lower, ck)] = em_cache
            n_cache += 1
            continue
        novas_por_cena[ck].append(d)
    if n_cache:
        log.info("[cluster] %d descrição(ões) reusaram o label do histórico "
                 "(match exato, KV_CACHE_CLUSTER=on).", n_cache)
    descricoes_novas = [d for lista in novas_por_cena.values() for d in lista]

    # Fase 12: capa a lista enviada ao gpt-oss. Sem isso, um vídeo com MUITAS
    # descrições únicas monta um prompt gigante e a chamada estoura os 8K TPM/req
    # do Free Tier (erro 413 "Request too large"). 120 já cobre vídeos reais; o
    # excedente (raro) só não recebe rótulo canônico (cai no fallback).
    _max_desc = int(os.environ.get("KV_CLUSTER_MAX_DESC", "200"))

    mapa_descricao_label: dict[tuple[str, str], str] = {}
    catalogo: dict[str, str] = {}

    # Fase 28: posto_vazio é semeado direto (curto-circuito determinístico).
    if any(d.lower().strip() == POSTO_VAZIO_DESC for d in descricoes_unicas):
        for ck in cenas_por_desc.get(POSTO_VAZIO_DESC, {""}):
            mapa_descricao_label[(POSTO_VAZIO_DESC, ck)] = POSTO_VAZIO_LABEL
        catalogo[POSTO_VAZIO_LABEL] = (
            "Posto de trabalho vazio — operador ausente do posto"
        )

    for (d_lower, ck), label in descricoes_conhecidas.items():
        mapa_descricao_label[(d_lower, ck)] = label
        if label not in catalogo:
            for v in memoria.get("vocabulario", []):
                if v["label"] == label:
                    catalogo[label] = v["descricao"]
                    break
            if label not in catalogo:
                catalogo[label] = label.replace("_", " ").capitalize()

    # Fase 86 — UMA CHAMADA POR PARTIÇÃO. O modelo continua fazendo o que faz
    # bem (colapsar sinônimos) DENTRO de cada cena; o discriminador é garantido
    # por fora. Listas pequenas, modelo de texto: o custo extra é marginal.
    _n_chamadas = 0
    for ck, lista in sorted(novas_por_cena.items()):
        lista = sorted(set(lista))
        if not lista:
            continue
        if len(lista) > _max_desc:
            log.warning("cluster[%s]: %d descrições > %d — truncando.",
                        ck or "sem-cena", len(lista), _max_desc)
            lista = lista[:_max_desc]
        prompt_completo = PROMPT_CLUSTER.format(
            bloco_processo=construir_bloco_dominio(descricao_processo, conhecimento_adquirido),
            # `_generalizar` diz ao bloco se `descartados` pode entrar. O
            # vocabulário entra sempre: sugere NOMES já usados, não remapeia
            # nada, e é o que impede o mesmo comportamento de ganhar três
            # nomes ao longo da campanha.
            bloco_memoria=construir_bloco_memoria_cluster(
                {**memoria, "_generalizar": aprendizado_auto}),
        )
        lista_formatada = "\n".join(f"- {d}" for d in lista)
        try:
            resposta = groq_text_call(
                groq_client,
                prompt_completo + lista_formatada,
                model=GROQ_MODEL_ANALISE,
                json_mode=True,
                max_tokens=4000,
                # Fase 86: ZERO. Com 0.1 a mesma lista podia sair agrupada de
                # dois jeitos em vídeos diferentes — parte da inconsistência
                # que o dono mediu.
                temperatura=0.0,
            )
            clusters = json.loads(resposta)["comportamentos"]
            _n_chamadas += 1
        except Exception as e:   # noqa: BLE001
            log.warning("cluster[%s] falhou (%s) — descrições caem no fallback.",
                        ck or "sem-cena", e)
            continue
        maq, imo = _partes_da_chave(ck)
        for c in clusters:
            # O SUFIXO É APLICADO AQUI, por código. A LLM pode ter devolvido
            # `monitorar_maquina` nas duas partições; é este passo que impede
            # as duas de virarem o mesmo rótulo.
            label = (c.get("label") or "").strip()
            # ⚠️ Fase 100 — GUARDA DA ABSTENÇÃO. O prompt já proíbe, mas
            # depender de o modelo obedecer foi exatamente o que falhou na
            # Fase 99. Se ele devolver o balde mesmo assim, isto NÃO vira
            # rótulo: as descrições do grupo ficam sem mapeamento e caem no
            # caminho da fila logo abaixo, com a descrição preservada.
            if not label or label in NAO_SAO_VOCABULARIO or _e_desistencia(label):
                log.warning("[cluster] o modelo devolveu desistência (%r) para "
                            "%d descrição(ões) — vão para a fila com a descrição "
                            "visível, sem rótulo que finja atividade.",
                            label or "(vazio)", len(c.get("descricoes_originais") or []))
                continue
            if label != POSTO_VAZIO_LABEL:
                label = label + sufixo_cena(maq, imo)
            # ⚠️ Fase 99 — A GUARDA ESTRUTURAL. Roda DEPOIS do cluster e ANTES
            # de o nome virar rótulo: mesmo que o modelo devolva
            # `monitorar_maquina_parada`, o sufixo cai aqui. Depender de o
            # modelo não escolher foi exatamente o que falhou por 896 eventos.
            _antes = label
            label = limpar_sufixo_estado(label)
            if label != _antes:
                log.info("[cluster] sufixo de estado removido: %s → %s "
                         "(nenhum sinal mede estado de máquina)", _antes, label)
            for d in c.get("descricoes_originais", []):
                mapa_descricao_label[(d.strip().lower(), ck)] = label
            catalogo[label] = _descricao_com_cena(c.get("descricao") or label, maq, imo)

    # ⚠️ Fase 100 — DESCRIÇÃO UTILIZÁVEL QUE NÃO FOI NOMEADA NÃO VIRA BALDE.
    # Antes, tudo que o cluster não devolvesse caía em `acao_indefinida` — um
    # rótulo com cara de atividade, que entrava na árvore, no Pareto, no
    # vocabulário e (por ter categoria) na conta de produtividade.
    #
    # Agora o não-nomeado fica FORA do mapa. `label_de` devolve None, e quem
    # monta o evento manda para a fila com a descrição visível. O gestor nomeia
    # — que é exatamente o pedido: nunca um rótulo que finge ser atividade.
    _nao_nomeadas = 0
    for d, ck in pares:
        if (d.lower().strip(), ck) not in mapa_descricao_label:
            _nao_nomeadas += 1
            log.warning("[cluster] NÃO NOMEADA (vai para a fila com a descrição "
                        "visível): %r (cena %r)", d, ck)
    if _nao_nomeadas:
        log.warning("[cluster] %d de %d descrição(ões) sem nome — o gestor "
                    "nomeia na fila. Nenhuma virou rótulo genérico.",
                    _nao_nomeadas, len(pares))

    def label_de(desc: str, maquina: str | None = None,
                 imovel: bool | None = None) -> str | None:
        """None = o cluster NÃO conseguiu nomear. Não é um rótulo; é a ausência
        de um. Quem chama manda o evento para a fila."""
        return mapa_descricao_label.get(
            (desc.lower().strip(), chave_cena(maquina, imovel)))

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
    origem_por_desc: dict[tuple[str, str], str] = {}
    for desc, ck in pares:
        d_lower = desc.lower().strip()
        if d_lower in correcoes:
            origem_por_desc[(d_lower, ck)] = "correcao_aprendida"
        elif mapa_descricao_label.get((d_lower, ck)) in vocab_estabelecido:
            origem_por_desc[(d_lower, ck)] = "vocabulario_canonico"
        else:
            origem_por_desc[(d_lower, ck)] = "pendente"

    def origem_de(desc: str, maquina: str | None = None,
                  imovel: bool | None = None) -> str:
        return origem_por_desc.get(
            (desc.lower().strip(), chave_cena(maquina, imovel)), "pendente")

    progress_cb("cluster", 100,
                f"{len(catalogo)} comportamentos canônicos "
                f"({_n_chamadas} partição(ões) de cena)")
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


def _melhor_evidencia_interlocutor(*evidencias) -> dict | None:
    candidatas = [e for e in evidencias if isinstance(e, dict)]
    if not candidatas:
        return None
    tipos = {e.get("tipo") for e in candidatas}
    if len(tipos) > 1:
        # A mesma fatia não pode alternar gestor/colega e continuar afirmando
        # certeza. O label normalmente já quebra o evento antes; esta é defesa
        # adicional para chamadas diretas/testes e futuras refatorações.
        return {
            "conversa_estado": "incerta",
            "tipo": TIPO_INTERLOCUTOR_INCERTO,
            "cor_superior": "incerto",
            "confianca_cor": 0.0,
            "motivo_cor": "evidencias_em_conflito",
            "origem": "vlm_interlocutor+roupa_superior_hsv",
        }

    def _score(e):
        try:
            return float(e.get("confianca_cor") or 0.0) * float(
                e.get("qualidade") or 1.0
            )
        except (TypeError, ValueError):
            return 0.0

    return dict(max(candidatas, key=_score))


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
        # ⚠️ Fase 101 — A ORIENTAÇÃO ESTAVA SENDO PERDIDA AQUI, e é por isso
        # que a coluna `orientacao` tinha ZERO linhas em 3.447 eventos.
        #
        # A Fase 97 gravou a orientação na OBSERVAÇÃO (`descritores_track`) e
        # escreveu a coluna no evento. O que faltava era o meio do caminho:
        # `_abrir_evento` copiava `maos_maquina` e não copiava `orientacao`, e
        # `_orientacao_do_minuto` lê justamente daqui. O minuto nunca via nada,
        # a coluna nascia nula sempre, e o gate da orientação ficou travado
        # esperando um dado que o código jogava fora um passo antes.
        #
        # É a QUARTA vez que este padrão morde (maquina/imovel na 88, t_ini/
        # t_fim na 92, a própria orientação na 97). Sinal calculado que não
        # atravessa TODAS as fronteiras até o banco não existe.
        "orientacao": o.get("orientacao"),
        # A NARRATIVA DO MINUTO. Diferente de `descricao_bruta`, que é a frase
        # de UM instante (a primeira do bloco), esta conta a sequência inteira.
        # Ela não é atualizada conforme o bloco cresce porque não precisa: é a
        # mesma em todas as observações do minuto, por construção.
        "narrativa": o.get("narrativa"),
        # Fase 110 — por que este minuto NÃO é "posto vazio", e com quanta
        # evidência. Sem isto a decisão do teste do passante não é
        # reconstituível depois de o vídeo ser apagado.
        "fora_do_posto": o.get("fora_do_posto"),
        "fora_amostras_zona": o.get("fora_amostras_zona"),
        # Moda por evento cru: o minuto pondera as modas dos crus, então o cru
        # precisa da sua. Um quadro isolado não decide para onde a pessoa olha.
        "_orient": Counter([o["orientacao"]] if o.get("orientacao") else []),
        # Fase 86: o discriminador acompanha o evento até a origem/validação e
        # até o fato das camadas.
        "maquina": o.get("maquina"),
        "imovel": o.get("imovel"),
        "trabalho": (o.get("trabalho")
                     if o.get("produtividade_observada")
                     and isinstance(o.get("trabalho"), bool) else None),
        "produtividade_motivo": o.get("produtividade_motivo"),
        "interlocutor_evidencia": o.get("interlocutor_evidencia"),
        # Votos separados do texto/label. O evento pode manter a mesma ação e
        # mudar de produtividade; guardar apenas o primeiro frame congelava o
        # julgamento até o fim do trecho.
        "_trabalho": Counter(
            [o["trabalho"]]
            if o.get("produtividade_observada")
            and isinstance(o.get("trabalho"), bool)
            else []
        ),
        "zona_contexto": o["zona"],
        "papel_pessoa": o.get("papel"),
        # Fase 91: o MÁXIMO que a lateral contou dentro deste evento.
        "n_posto_cam2": o.get("n_posto_cam2"),
        "n_cena_cam2": o.get("n_cena_cam2"),
        # Fase 90 — DOIS CONTADORES, porque são duas perguntas.
        # `n_observacoes` = quanto do minuto está COBERTO (mantém o evento
        # inteiro e o denominador honesto). `n_amostras` = quantos quadros
        # foram de fato OLHADOS — é o que vira evidência e confiança. Herdada
        # e interpolada cobrem tempo e NÃO votam: doze observações com a mesma
        # descrição herdada dariam share 1,00, ou seja, certeza máxima num
        # minuto em que ninguém olhou nada.
        "n_observacoes": 1,
        "n_amostras": 1 if origem_foi_observada(o.get("origem_gate")) else 0,
        "origens": {(o.get("origem_gate") or "analisado"): 1},
    }


def etapa_segmentar_eventos(
    observacoes_brutas: list[dict],
    label_de: Callable[..., str | None],
    intervalo_s: float,
) -> list[dict]:
    for o in observacoes_brutas:
        lbl_forcado = _label_conversa_evidenciada(
            o.get("interlocutor_evidencia")
        )
        lbl_cluster = label_de(
            o["descricao"], o.get("maquina"), o.get("imovel")
        )
        if lbl_forcado:
            # Única porta de entrada de ``conversando_gestor``: associação do
            # VLM + classe objetiva do recorte superior. O cluster não decide.
            lbl = lbl_forcado
        elif familia_label(lbl_cluster) == LABEL_CONVERSANDO_GESTOR:
            # Um nome plausível vindo só da prosa não pode afirmar gestor.
            # Volta à taxonomia genérica histórica, sem backfill nem promoção.
            lbl = LABEL_CONVERSANDO_COLEGA
        else:
            lbl = lbl_cluster
        # Fase 100: None = o cluster não nomeou. O evento continua existindo (a
        # pessoa estava lá, o minuto é real), mas nasce SEM NOME e marcado para
        # a fila. `nao_nomeado` é o carimbo do estado — a coluna é NOT NULL e a
        # abstenção precisa de um valor —, e ele é tratado como ausência de
        # rótulo em todo lugar: fora da árvore, fora do Pareto, fora do
        # vocabulário, sem categoria e sempre na fila.
        o["label"] = lbl or LABEL_NAO_NOMEADO
        o["nao_nomeado"] = lbl is None

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
            estado_comercial_igual = (
                atual.get("trabalho")
                == (
                    o.get("trabalho")
                    if o.get("produtividade_observada")
                    and isinstance(o.get("trabalho"), bool)
                    else None
                )
                and atual.get("maos_maquina") == o.get("maos_maquina")
                and atual.get("orientacao") == o.get("orientacao")
                and atual.get("produtividade_motivo")
                == o.get("produtividade_motivo")
            )
            if (
                o["label"] == atual["comportamento_label"]
                and o.get("papel") == atual.get("papel_pessoa")
                # Auditorias diferentes não podem ser fundidas num evento só:
                # isso faria um trecho realmente vazio herdar "indeciso" (ou
                # esconder o teto/falha que ocorreu no trecho seguinte).
                and o.get("fora_do_posto") == atual.get("fora_do_posto")
                and gap <= janela_continuidade_s
                and (
                    not PRODUTIVIDADE_OPERADOR_ESTRUTURADA
                    or estado_comercial_igual
                )
            ):
                atual["tempo_fim_s"] = o["tempo_s"]
                atual["frame_fim"] = o["frame_idx"]
                atual["n_observacoes"] += 1
                if origem_foi_observada(o.get("origem_gate")):
                    atual["n_amostras"] += 1
                _og = o.get("origem_gate") or "analisado"
                atual["origens"][_og] = atual["origens"].get(_og, 0) + 1
                # Fase 101: acumula a orientação de cada amostra do cru.
                if o.get("orientacao"):
                    atual["_orient"][o["orientacao"]] += 1
                for _k in ("n_posto_cam2", "n_cena_cam2"):
                    _v = o.get(_k)
                    if _v is not None:
                        atual[_k] = max(atual.get(_k) or 0, int(_v))
                if o.get("fora_amostras_zona") is not None:
                    atual["fora_amostras_zona"] = max(
                        int(atual.get("fora_amostras_zona") or 0),
                        int(o["fora_amostras_zona"]),
                    )
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
                if (o.get("produtividade_observada")
                        and isinstance(o.get("trabalho"), bool)):
                    atual["_trabalho"][o["trabalho"]] += 1
                atual["interlocutor_evidencia"] = _melhor_evidencia_interlocutor(
                    atual.get("interlocutor_evidencia"),
                    o.get("interlocutor_evidencia"),
                )
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
        # Fase 101: fecha a orientação do cru pela MODA das suas amostras.
        # Vazio → None, e None nunca vira "de frente" por omissão.
        _oc = e.pop("_orient", None)
        e["orientacao"] = max(_oc, key=_oc.get) if _oc else None
        _tc = e.pop("_trabalho", None)
        _sim = (_tc or {}).get(True, 0)
        _nao = (_tc or {}).get(False, 0)
        e["trabalho"] = (True if _sim > _nao else False if _nao > _sim else None)
        # Fase 82: fecha o resumo do corpo com as caixas acumuladas do evento.
        e["bbox_stats"] = _resumo_bbox(e.pop("_caixas", []), e.pop("_dim", None),
                                       e.get("bbox_cam"))
        _ev_interlocutor = e.pop("interlocutor_evidencia", None)
        if _ev_interlocutor:
            e["bbox_stats"] = dict(e.get("bbox_stats") or {})
            e["bbox_stats"]["interlocutor"] = _ev_interlocutor

    return eventos


# ═════════════════════════════════════════════════════════════════════════
# ETAPA 4b · Consolidação em 1 evento PRINCIPAL por minuto (Fase 16)
# ═════════════════════════════════════════════════════════════════════════
def _orientacao_do_minuto(no_bucket: list) -> str | None:
    """Orientação DOMINANTE do minuto (moda ponderada pela sobreposição).

    O minuto é um regime, não um instante: o operador vira a cabeça o tempo
    todo, e um quadro isolado não decide nada. None quando nenhuma amostra teve
    pose — e None NUNCA vira "de frente" por omissão."""
    peso: dict = defaultdict(float)
    for e, ov in no_bucket:
        o = e.get("orientacao")
        if o:
            peso[o] += float(ov or 1.0)
    return max(peso, key=peso.get) if peso else None


def _papel_do_minuto(
    no_bucket: list, inicio_bucket: float, fim_bucket: float
) -> str | None:
    """Papel dominante em fatias exclusivas, nunca em pessoa-segundos.

    Dois visitantes simultâneos ocupam um instante, não dois. Operador junto a
    visitante continua operador; identidade indefinida ou vazio contradito por
    pessoa tornam somente aquela fatia inconclusiva.
    """
    peso: dict[str | None, float] = defaultdict(float)
    limites = {float(inicio_bucket), float(fim_bucket)}
    for e, _ov in no_bucket:
        limites.add(max(float(inicio_bucket), float(e.get("tempo_inicio_s") or 0)))
        limites.add(min(float(fim_bucket), float(e.get("tempo_fim_s") or 0)))
    ordenados = sorted(l for l in limites if inicio_bucket <= l <= fim_bucket)
    for inicio, fim in zip(ordenados, ordenados[1:]):
        if fim <= inicio:
            continue
        ativos = [
            e for e, _ov in no_bucket
            if float(e.get("tempo_inicio_s") or 0) < fim
            and float(e.get("tempo_fim_s") or 0) > inicio
        ]
        if not ativos:
            continue
        # Fase 110: `operador_fora` é papel reconhecido. Fora deste conjunto ele
        # viraria `None` e a fatia inteira ficaria inconclusiva.
        papeis = {
            e.get("papel_pessoa")
            if e.get("papel_pessoa") in {"operador", "visitante", "posto_vazio",
                                         PAPEL_OPERADOR_FORA}
            else None
            for e in ativos
        }
        # `posto_vazio` contradito por PESSOA NO POSTO é inconclusivo. Mas
        # `posto_vazio` + `operador_fora` NÃO é contradição: os dois dizem a
        # mesma coisa — não há ninguém no polígono. O segundo só acrescenta
        # onde ele está.
        if None in papeis or (
            "posto_vazio" in papeis
            and ("operador" in papeis or "visitante" in papeis)
        ):
            papel = None
        elif "operador" in papeis:
            papel = "operador"
        elif PAPEL_OPERADOR_FORA in papeis:
            # Ganha de `posto_vazio`: saber ONDE ele está é mais informativo
            # que saber apenas que o posto está vazio. Na 111D também ganha de
            # visitante: outra pessoa no posto não esconde o titular visto fora.
            papel = PAPEL_OPERADOR_FORA
        elif "visitante" in papeis:
            papel = "visitante"
        elif "posto_vazio" in papeis:
            papel = "posto_vazio"
        else:
            papel = None
        peso[papel] += fim - inicio
    if not peso:
        return None
    maior = max(peso.values())
    empatados = {papel for papel, valor in peso.items() if abs(valor - maior) < 1e-9}
    if len(empatados) == 1:
        return next(iter(empatados))
    if empatados == {"operador", "visitante"}:
        return "operador"
    return None


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
    movimento_por_minuto: dict | None = None,
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
        for e in eventos_crus:
            ov = min(e["tempo_fim_s"], we) - max(e["tempo_inicio_s"], ws)
            if ov > 0:
                no_bucket.append((e, ov))
        if not no_bucket:
            continue  # minuto sem atividade → sem principal

        # Identidade/presença é resolvida ANTES do rótulo. O desenho anterior
        # escolhia a ação dominante e copiava o papel do representante dessa
        # ação; assim um visitante podia virar o "operador" do minuto.
        if PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
            papel_minuto = _papel_do_minuto(no_bucket, ws, we)
            bucket_papel = [
                (e, ov) for e, ov in no_bucket
                if (
                    e.get("papel_pessoa") == papel_minuto
                    if papel_minuto is not None
                    else e.get("papel_pessoa") not in {
                        "operador", "visitante", "posto_vazio",
                        PAPEL_OPERADOR_FORA,
                    }
                )
            ]
            if not bucket_papel:
                bucket_papel = no_bucket
        else:
            # Rollback real: V8 escolhia o rótulo sobre a cena inteira e só
            # depois copiava o papel do representante.
            papel_minuto = None
            bucket_papel = no_bucket
        dur_por_label: dict[str, float] = defaultdict(float)
        for e, ov in bucket_papel:
            dur_por_label[e["comportamento_label"]] += ov
        total = sum(dur_por_label.values())
        top_label, top_dur = max(dur_por_label.items(), key=lambda kv: kv[1])
        share = (top_dur / total) if total > 0 else 1.0
        escolhido = top_label
        if share < dominancia and len(dur_por_label) > 1:
            escolhido = _principal_por_ia(bucket_papel, catalogo) or top_label
        # Representante = evento do rótulo escolhido com MAIOR sobreposição no minuto.
        # Fase 90: VOTOS são quadros olhados. Herdada/interpolada mantêm o
        # minuto coberto mas não afirmam nada — quem não olhou não vota.
        _n_votos = sum(e.get("n_amostras", 0) for e, _ in bucket_papel)
        _n_obs = sum(e.get("n_observacoes", e.get("n_amostras", 0))
                     for e, _ in bucket_papel)
        _origens: dict = {}
        for _e, _ in bucket_papel:
            for k, v in (_e.get("origens") or {}).items():
                _origens[k] = _origens.get(k, 0) + v
        reps = [(e, ov) for (e, ov) in bucket_papel
                if e["comportamento_label"] == escolhido]
        rep = (max(reps, key=lambda x: x[1])
               if reps else max(bucket_papel, key=lambda x: x[1]))[0]
        # Auditoria da Fase 110 também precisa atravessar o principal. Escolhe
        # o estado auditável de maior sobreposição e conserva o maior piso de
        # evidência do bucket; ambos são metadados, nunca alteram o papel nem
        # a duração escolhidos acima.
        _fora_partes = [
            (e, ov) for e, ov in bucket_papel if e.get("fora_do_posto")
        ]
        _fora_rep = (
            max(_fora_partes, key=lambda x: x[1])[0]
            if _fora_partes else None
        )
        _fora_amostras = [
            int(e.get("fora_amostras_zona") or 0)
            for e, _ov in _fora_partes
            if e.get("fora_amostras_zona") is not None
        ]
        if PRODUTIVIDADE_OPERADOR_ESTRUTURADA:
            bucket_operador = (
                [(e, ov) for e, ov in bucket_papel
                 if e.get("papel_pessoa") == "operador"]
                if papel_minuto == "operador" else []
            )
        else:
            papel_minuto = rep.get("papel_pessoa")
            bucket_operador = no_bucket
        _bbox_principal = _merge_bbox_stats(
            [e for e, _ in bucket_papel
             if e.get("pessoa_track_id") == rep.get("pessoa_track_id")]
        )
        # A evidência do interlocutor acompanha SOMENTE os crus do label
        # vencedor. Misturar uma conversa curta com outra ação do mesmo track
        # faria a aparência de P2 decidir um principal que não é conversa.
        _interlocutor_principal = _melhor_evidencia_interlocutor(*[
            (e.get("bbox_stats") or {}).get("interlocutor")
            for e, _ov in reps
            if isinstance(e.get("bbox_stats"), dict)
        ])
        if _interlocutor_principal:
            _bbox_principal = dict(_bbox_principal or {})
            _bbox_principal["interlocutor"] = _interlocutor_principal
        principais.append({
            "pessoa_track_id": rep["pessoa_track_id"],
            # A narrativa do minuto vem de qualquer observação do balde — é a
            # mesma em todas. `rep` primeiro por consistência com o resto.
            "narrativa": (rep.get("narrativa")
                          or next((e.get("narrativa") for e, _ in bucket_papel
                                   if e.get("narrativa")), None)),
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
            "bbox_stats": _bbox_principal,
            "maos_maquina": (True if any(e.get("maos_maquina")
                                         for e, _ in bucket_operador) else None),
            # Fase 97: a orientação DOMINANTE do minuto — é ela que decide o
            # nível 2. Moda simples: o minuto é um regime, não um instante.
            "orientacao": _orientacao_do_minuto(bucket_operador),
            "maquina": rep.get("maquina"),
            "imovel": rep.get("imovel"),
            # Fase 97: o julgamento do minuto — maioria simples entre os crus,
            # e `None` vence empate (na dúvida, dúvida).
            "trabalho": _trabalho_do_minuto(bucket_operador),
            "zona_contexto": rep["zona_contexto"],
            "papel_pessoa": papel_minuto,
            "fora_do_posto": (
                _fora_rep.get("fora_do_posto") if _fora_rep else None
            ),
            "fora_amostras_zona": (
                max(_fora_amostras) if _fora_amostras else None
            ),
            "n_amostras": _n_votos,
            # Cobertura e composição: é o par que permite dizer "este minuto
            # ficou sem evidência POR supressão do gate" em vez de o teto
            # agressivo ser descoberto por acaso, semanas depois.
            "n_observacoes": _n_obs,
            "observacoes_origem": _origens or None,
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
        # Fase 89: o movimento medido cola no PRINCIPAL porque os dois são o
        # mesmo minuto. Gravado sempre — a injeção no prompt é que é opcional.
        _mov = (movimento_por_minuto or {}).get(b)
        if _mov:
            principais[-1]["movimento_maquina"] = _mov.get("movimento")
            principais[-1]["movimento_detalhe"] = _mov.get("detalhe")
            # Fase 94: o MODO é composto aqui porque é aqui que `maos_maquina`
            # existe — ele vem dos crus do minuto, não do sensor.
            _maos = any(e.get("maos_maquina") for e, _ in bucket_operador)
            principais[-1]["modo_operacao"] = modo_operacao(
                _mov.get("movimento"), _mov.get("detalhe"), _maos)
        # Fase 95: o NÍVEL que a árvore usaria é calculado e gravado mesmo com
        # a flag desligada — é o que permite comparar antes/depois sem
        # reprocessar. Ele não muda a categoria enquanto a flag não liga.
        #
        # ⚠️ Só o NÍVEL, não o "candidato a improdutivo": este depende da
        # categoria Lean do rótulo, que na ingestão AINDA NÃO EXISTE (ela é
        # atribuída depois, por `classificar_comportamentos_lean`). Gravá-lo
        # aqui seria gravar uma conta feita com um insumo faltando — o
        # candidato é derivado na LEITURA, onde o mapa de categorias existe.
        _c, _niv, _mot, _cand = arvore_decidir(principais[-1], None)
        principais[-1]["decidido_por"] = _niv
        # Fase 57: CAMADAS DE DÚVIDA — determinísticas, em CPU, ZERO chamada
        # extra ao VLM. Camada nunca corrige o rótulo: só marca dúvida.
        if camadas:
            # As camadas auditam a CENA inteira (inclusive contradições entre
            # posto vazio e uma pessoa em parte do minuto). Só os sinais
            # comerciais acima são restritos ao operador escolhido.
            fato = montar_fato_evento(principais[-1], no_bucket, share,
                                      len(dur_por_label), rastreia_papel=_rastreia_papel)
            em_duvida, disparos, avaliacao = avaliar_camadas(fato, escolhido, camadas)
            # Fase 88: o rastro é gravado SEMPRE que o motor rodou — inclusive
            # (e principalmente) quando nada disparou. É a única forma de
            # distinguir "nenhuma contradição" de "camada nenhuma foi olhada".
            principais[-1]["camadas_avaliadas"] = avaliacao
            # Fase 89 — o ÚNICO poder do sensor, e ele não troca rótulo: manda
            # para a fila. Só existe com a injeção ligada (sem o fato no prompt
            # o VLM não teve como considerar o movimento).
            _veto = veto_movimento(principais[-1].get("movimento_maquina"),
                                   principais[-1].get("movimento_detalhe"),
                                   principais[-1].get("maquina"),
                                   principais[-1].get("modo_operacao"))
            if _veto:
                disparos = list(disparos) + [
                    {"nome": "sensor_movimento_contradiz_ciclo",
                     "modo": "ativa", "motivo": _veto}]
                em_duvida = True
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

        # ⚠️ Fase 100 — SEM NOME ⇒ FILA, sempre. Independe de camadas: se o
        # cluster não nomeou, não há rótulo para uma camada contradizer. Este é
        # o requisito "o evento vai direto pra fila de validação com a
        # descrição visível, e eu nomeio" — e é o que garante que nenhum
        # não-nomeado passe silencioso para o dashboard.
        if rotulo_e_ausencia(principais[-1].get("comportamento_label")):
            principais[-1]["em_duvida"] = True
            if not principais[-1].get("duvida_motivo"):
                _d = (principais[-1].get("descricao_bruta") or "").strip()
                principais[-1]["duvida_motivo"] = (
                    "o sistema NÃO nomeou esta ação — a descrição observada foi: "
                    f"“{_d}”. Nomeie você." if _d else
                    "o sistema não nomeou esta ação e não há descrição utilizável.")
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
    '''(em_duvida, disparos, avaliacao) — FUNÇÃO PURA, testável sem banco.

    `em_duvida` só considera camadas ATIVAS. As em SOMBRA entram em `disparos`
    (para o placar contar) mas não marcam o evento: é assim que o dono do
    processo mede o impacto de uma regra nova antes de ligá-la.

    Fase 88 — O RASTRO. `avaliacao` é o terceiro elemento e é o conserto mais
    importante desta fase, embora não mude decisão nenhuma.

    Até aqui `camadas_disparadas` NULL queria dizer duas coisas incompatíveis:
    "as camadas rodaram e nenhuma disparou" e "as camadas nunca rodaram" — a
    carga falha em silêncio (`carregar_camadas_duvida` devolve [] em qualquer
    exceção) e a consolidação pula com um `if camadas:`. Sem separar os dois,
    silêncio não é evidência de nada, e nenhuma camada é confiável — nem as
    que acabamos de consertar.

    `avaliacao` separa três estados que antes eram um só:
      • ausente        → o motor NÃO rodou neste evento
      • aplicaveis=[]  → rodou, mas nenhuma regra mira este rótulo. É a
                         assinatura EXATA da regressão da Fase 86: os sufixos
                         `_ciclo`/`_parada` fizeram `quando_rotulo` parar de
                         casar, e toda camada de rótulo nomeado morreu calada.
      • aplicaveis=[X] → X foi avaliada contra o fato e não disparou.
    '''
    disparos, em_duvida = [], False
    aplicaveis, com_erro = [], []
    n_carregadas = 0
    for c in sorted(camadas or [], key=lambda x: x.get("ordem", 100)):
        modo = (c.get("modo") or "sombra").lower()
        if modo == "off":
            continue
        n_carregadas += 1
        if not _rotulo_casa(c.get("quando_rotulo") or ["*"], label):
            continue
        # "Aplicável" = o rótulo casou, logo a condição FOI olhada. É este o
        # conjunto que responde "a regra chegou a ser perguntada?".
        aplicaveis.append(c.get("nome"))
        try:
            if not _avaliar_condicao(c.get("se") or {}, fato):
                continue
        except Exception as e:
            log.warning("[camadas] %s falhou ao avaliar (ignorada): %s", c.get("nome"), e)
            # Regra que explode é diferente de regra que não disparou, e a
            # diferença tem de sobreviver ao log — log some, dado fica.
            com_erro.append(c.get("nome"))
            continue
        disparos.append({"nome": c.get("nome"), "modo": modo,
                         "motivo": c.get("motivo") or ""})
        if modo == "ativa":
            em_duvida = True
    avaliacao = {"carregadas": n_carregadas, "aplicaveis": aplicaveis}
    if com_erro:
        avaliacao["erro"] = com_erro
    return em_duvida, disparos, avaliacao


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

    # ── Fase 91 — AS DUAS CÂMERAS CONTAM ────────────────────────────────
    # A cam1 era a única fonte de contagem. Num caso medido, a cam2 mostrava
    # DUAS pessoas no posto e a cam1 uma: a segunda não existia para o sistema,
    # e o dia inteiro saiu com zero eventos de `visitante`.
    #
    # SEM CASAMENTO ENTRE CÂMERAS: usa o MÁXIMO. Se a cam1 vê 1 e a cam2 vê 2,
    # são PELO MENOS 2. É um piso honesto e não exige identidade — casar tracks
    # entre câmeras é o problema difícil, e resolvê-lo mal produziria contagem
    # dupla, que é pior que contagem baixa. O offset de relógio entre as duas
    # já é compensado na amostragem (`alvo_ms = tempo_s + offset_s`), então os
    # dois números são do MESMO instante.
    #
    # A cam2 NÃO vira fonte de descrição: contar é grátis (o track já roda),
    # descrever custaria uma chamada de VLM por pessoa.
    _n_cam2_posto = max((int(e["n_posto_cam2"]) for e in eventos
                         if e.get("n_posto_cam2") is not None), default=0)
    _n_cam2_cena = max((int(e["n_cena_cam2"]) for e in eventos
                        if e.get("n_cena_cam2") is not None), default=0)
    fato = {
        "pessoas_na_cena": max(len(pessoas), _n_cam2_cena),
        "pessoas_no_posto": max(len(no_posto), _n_cam2_posto),
        # Quantas a cam1 NÃO viu. É este número que faz uma camada poder
        # perguntar "havia alguém aqui que o sistema não descreveu?".
        "pessoas_so_na_cam2": max(0, _n_cam2_posto - len(no_posto)),
        "pessoas_cam1_posto": len(no_posto),
        "pessoas_cam2_posto": _n_cam2_posto,
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
        # Fase 91: presença é das DUAS. `am.operador_presente` já considerava a
        # lateral (política 'dupla', Fase 33); aqui o FATO passa a considerar
        # também — antes ele olhava só o papel dos tracks da cam1 e podia dizer
        # "sem operador" num minuto resgatado pela cam2.
        fato["operador_presente"] = bool(no_posto) or _n_cam2_posto > 0
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



# ═════════════════════════════════════════════════════════════════════════
# Fase 89 — MOVIMENTO DA MÁQUINA, MEDIDO. EM SOMBRA.
#
# O discriminador `ciclo`/`parada` do VLM media ruído (Fase 88): em minutos
# adjacentes com a MESMA ação, o estado trocava tanto quanto uma moeda. A
# causa não é o modelo ser ruim — é a pergunta ser impossível no material que
# ele recebe. Um torno em ciclo e um torno parado são IDÊNTICOS num frame; a
# diferença é MOVIMENTO, e frame não tem movimento.
#
# POR QUE 6 fps E NÃO OS FRAMES DO VLM
# A sequência manda ~8 imagens por minuto, ~5 s entre elas. A 5 s de distância
# o carro que avança 0,1 mm/rev pode ser sub-pixel, e a placa girando tem fase
# aleatória — o diff satura e não informa nada. O laço de tracking JÁ decodifica
# a `KV_TRACK_FPS` (6 fps): ~360 pares de frames por minuto, com as bboxes do
# YOLO do MESMO instante para a máscara de pessoa. O custo de decodificação já
# está pago; sobra o diff num recorte pequeno.
#
# O QUE É MEDIDO, E O QUE NÃO É
# Isto NÃO diz "a máquina está em ciclo". Diz "houve movimento na zona da
# máquina que não é explicado por gente passando na frente". A tradução para
# ciclo/parada continua sendo do VLM, agora informado — este sinal entra como
# FATO no prompt, ao lado de `maos_maquina` e `orientacao`, e não sobrescreve
# nada. Sobrescrita silenciosa é inauditável, e o pixel tem modos de falha
# próprios (contraste baixo, oclusão, turno noturno) que uma regra dura
# herdaria inteiros.
#
# A REGRA QUE ATRAVESSA TUDO AQUI
# AUSÊNCIA DE MEDIÇÃO NÃO É MEDIÇÃO DE AUSÊNCIA. Zona ocupada por gente,
# contraste insuficiente ou par descartado por incoerência produzem
# `indisponivel` — NUNCA `ausente`. É a mesma lição do `_mad` devolvendo 0.0
# com n=1 (Fase 84): um número que diz "estável" quando a verdade é "não sei"
# é pior que nenhum número.
# ═════════════════════════════════════════════════════════════════════════
_MOV_ENABLE = os.environ.get("KV_MOVIMENTO", "on") not in ("off", "0", "false", "False", "")
# A INJEÇÃO no prompt é separada da MEDIÇÃO: o sinal grava desde o primeiro
# vídeo, e só passa a influenciar o VLM quando o dono olhar os números e ligar
# a chave. Ligar não precisa de deploy.
_MOV_INJETAR = os.environ.get("KV_MOVIMENTO_INJETAR", "off") not in ("off", "0", "false", "False", "")

# ── Limiares, todos mexíveis por ambiente (sem deploy) ──────────────────
# Largura do recorte da zona depois do downscale. Menor = mais barato e menos
# sensível a ruído de sensor; abaixo de ~120 o cavaco some junto com o ruído.
_MOV_LARGURA = max(64, int(os.environ.get("KV_MOV_LARGURA", "192")))
# Limiar do diff de GRADIENTE, RELATIVO ao contraste estrutural da própria
# zona. Relativo e não absoluto porque uma máquina escura num turno noturno
# tem gradiente menor em tudo — um limiar fixo mediria a iluminação.
_MOV_LIMIAR_REL = float(os.environ.get("KV_MOV_LIMIAR_REL", "0.35"))
# Contraste mínimo para a zona ser mensurável. Abaixo disto não se afirma
# "parada": se afirma "não dá para ver".
_MOV_ESCALA_MIN = float(os.environ.get("KV_MOV_ESCALA_MIN", "8.0"))
# Fração dos pixels VÁLIDOS que precisa mudar para o par contar como "houve
# movimento". 2% de uma zona de torno é a placa girando; 0,1% é ruído.
_MOV_FRACAO_PIXEL = float(os.environ.get("KV_MOV_FRACAO_PIXEL", "0.02"))
# Blob único cobrindo mais que isto da zona válida = iluminação/sombra/oclusor
# grande, não peça girando. O par é DESCARTADO (não vira "sem movimento").
_MOV_BLOB_MAX = float(os.environ.get("KV_MOV_BLOB_MAX", "0.40"))
# Zona coberta por gente acima disto = não dá para medir a máquina.
_MOV_OCUPACAO_MAX = float(os.environ.get("KV_MOV_OCUPACAO_MAX", "0.50"))
# Dilatação da bbox da pessoa: a caixa do YOLO é justa e membros escapam.
_MOV_DILATA_PESSOA = float(os.environ.get("KV_MOV_DILATA_PESSOA", "0.10"))
# Fração dos pares do minuto que precisa ser válida para o minuto ter veredito.
_MOV_MIN_VALIDOS = float(os.environ.get("KV_MOV_MIN_VALIDOS", "0.50"))
# Fronteiras do veredito, sobre a fração de pares VÁLIDOS com movimento.
_MOV_CONTINUO = float(os.environ.get("KV_MOV_CONTINUO", "0.70"))
_MOV_INTERMITENTE = float(os.environ.get("KV_MOV_INTERMITENTE", "0.15"))
# Mapa aprendido: lado da grade e quantos pares acumulados antes de PESAR por
# ela. Antes disso o agregado sem peso é mais honesto que um mapa de 3 vídeos.
_MOV_GRADE = max(4, int(os.environ.get("KV_MOV_GRADE", "16")))
_MOV_MAPA_MIN_PARES = int(os.environ.get("KV_MOV_MAPA_MIN_PARES", "20000"))
_MOV_MAPA_PESO_MIN = float(os.environ.get("KV_MOV_MAPA_PESO_MIN", "0.25"))

# Fase 94 — quanto da PARTE MÓVEL pode estar coberta antes de a medição
# deixar de valer. Diferente de `_MOV_OCUPACAO_MAX`, que olha a zona inteira:
# o operador em pé ao lado do torno cobre ~20% da zona, mas se esses 20% forem
# a placa, o que sobra não responde nada.
_MOV_MOVEL_OCLUIDA_MAX = float(os.environ.get("KV_MOV_MOVEL_OCLUIDA_MAX", "0.35"))
# Célula "móvel" = que se mexe com frequência acima desta fração do topo do
# mapa. Só faz sentido com o mapa já com base.
_MOV_CELULA_MOVEL = float(os.environ.get("KV_MOV_CELULA_MOVEL", "0.50"))

MOV_VALORES = ("continuo", "intermitente", "ausente", "indisponivel")
MODO_VALORES = ("automatico", "manual", "parado", "indeterminado")


def _retangulo_zonas_maquina(rois: dict, w: int, h: int) -> tuple | None:
    """Bounding box (x1,y1,x2,y2) que cobre TODAS as zonas com papel 'maquina'.

    Retângulo e não polígono de propósito: o recorte precisa ser um array
    contíguo para o Sobel, e a máscara do polígono entra depois, como peso.
    """
    caixas = []
    for info in (rois or {}).values():
        if info.get("papel") != "maquina":
            continue
        poly = info.get("polygon")
        if poly is None or len(poly) < 3:
            continue
        xs, ys = poly[:, 0], poly[:, 1]
        caixas.append((int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())))
    if not caixas:
        return None
    x1 = max(0, min(c[0] for c in caixas))
    y1 = max(0, min(c[1] for c in caixas))
    x2 = min(w, max(c[2] for c in caixas))
    y2 = min(h, max(c[3] for c in caixas))
    if x2 - x1 < 16 or y2 - y1 < 16:
        return None
    return (x1, y1, x2, y2)


class MedidorMovimento:
    """Mede movimento na zona da máquina, quadro a quadro, a 6 fps.

    Vive dentro do laço de tracking, onde o frame já está decodificado e as
    bboxes das pessoas do MESMO instante estão na mão. Não decodifica nada,
    não chama modelo nenhum.

    Sem zona 'maquina' desenhada, `ativo` é False e o medidor não produz
    sinal — do mesmo jeito que `maos_na_maquina` só existe quando há zona.
    Ausência de zona é ausência de informação, não ausência de movimento.
    """

    def __init__(self, rois: dict, w: int, h: int, cam_id: str | None = None,
                 mapa: dict | None = None):
        self.cam_id = cam_id
        self.rect = _retangulo_zonas_maquina(rois, w, h) if _MOV_ENABLE else None
        self.ativo = self.rect is not None
        self.zona_nome = next((n for n, i in (rois or {}).items()
                               if i.get("papel") == "maquina"), None)
        self.pares: list[dict] = []
        self._ant = None
        self._t_ant = None
        # Grade acumulada DESTE vídeo (some ao mapa do processo no fim).
        self.grade = [[0] * _MOV_GRADE for _ in range(_MOV_GRADE)]
        self.n_pares_grade = 0
        self._mapa = self._normalizar_mapa(mapa)
        self._peso = None            # expansão do mapa, calculada uma vez só
        self._peso_movel = None      # máscara das células que SE MEXEM
        self._movel_exp = None       # a mesma, expandida ao tamanho do recorte
        if self.ativo and self._mapa is not None:
            try:
                topo = max(max(l) for l in self._mapa) or 1.0
                movel = [[1.0 if v >= _MOV_CELULA_MOVEL * topo else 0.0 for v in linha]
                         for linha in self._mapa]
                self._peso_movel = np.asarray(movel, dtype=np.float32)
            except Exception:  # noqa: BLE001
                self._peso_movel = None
        if self.ativo:
            x1, y1, x2, y2 = self.rect
            self.escala_px = _MOV_LARGURA / max(1, x2 - x1)
            self.dim = (max(16, int(round((x2 - x1) * self.escala_px))),
                        max(16, int(round((y2 - y1) * self.escala_px))))

    @staticmethod
    def _normalizar_mapa(mapa: dict | None) -> list | None:
        """O mapa só PESA depois de base suficiente. Antes disso devolve None e
        o agregado roda sem peso — um mapa de três vídeos é mais chute que o
        próprio agregado."""
        if not mapa:
            return None
        try:
            if int(mapa.get("n_pares") or 0) < _MOV_MAPA_MIN_PARES:
                return None
            g = mapa.get("grade") or []
            if len(g) != _MOV_GRADE or any(len(l) != _MOV_GRADE for l in g):
                return None
            topo = max((max(l) for l in g), default=0) or 1
            # Peso 0..1 pela frequência com que a célula se mexe, com PISO: uma
            # célula que nunca se mexeu ainda pode ser onde a peça nova aparece.
            return [[max(_MOV_MAPA_PESO_MIN, min(1.0, v / topo)) for v in linha]
                    for linha in g]
        except Exception:  # noqa: BLE001
            return None

    def _preparar(self, frame):
        x1, y1, x2, y2 = self.rect
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        g = cv2.resize(g, self.dim, interpolation=cv2.INTER_AREA)
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        # GRADIENTE e não intensidade: sombra desloca brilho e preserva a
        # textura; peça girando e cavaco MOVEM as bordas. É o que separa o
        # operador fazendo sombra na máquina da máquina trabalhando.
        return cv2.magnitude(gx, gy)

    def _mascara_pessoas(self, bboxes) -> tuple:
        """(máscara booleana de pixels VÁLIDOS, fração ocupada por gente)."""
        alt, larg = self.dim[1], self.dim[0]
        ocupado = np.zeros((alt, larg), dtype=np.uint8)
        rx1, ry1, _, _ = self.rect
        for b in bboxes or []:
            try:
                bx1, by1, bx2, by2 = (float(v) for v in b[:4])
            except Exception:  # noqa: BLE001
                continue
            dw = (bx2 - bx1) * _MOV_DILATA_PESSOA
            dh = (by2 - by1) * _MOV_DILATA_PESSOA
            cx1 = int((bx1 - dw - rx1) * self.escala_px)
            cy1 = int((by1 - dh - ry1) * self.escala_px)
            cx2 = int((bx2 + dw - rx1) * self.escala_px)
            cy2 = int((by2 + dh - ry1) * self.escala_px)
            cx1, cy1 = max(0, cx1), max(0, cy1)
            cx2, cy2 = min(larg, cx2), min(alt, cy2)
            if cx2 > cx1 and cy2 > cy1:
                ocupado[cy1:cy2, cx1:cx2] = 1
        n_ocup = int(ocupado.sum())
        # Fase 94: quanto da PARTE MÓVEL ficou coberta. Sem mapa com base não
        # há como saber quais células são móveis, e aí devolve None — que é
        # "não sei", nunca 0.
        movel_ocl = None
        if self._peso_movel is not None:
            # A grade é 16x16; a máscara é do tamanho do recorte. Expande UMA
            # vez (o mapa não muda dentro do vídeo).
            if self._movel_exp is None:
                self._movel_exp = cv2.resize(
                    self._peso_movel, (larg, alt), interpolation=cv2.INTER_NEAREST)
            tot = float(self._movel_exp.sum())
            if tot > 0:
                movel_ocl = float((self._movel_exp * ocupado).sum() / tot)
        return (ocupado == 0), n_ocup / float(alt * larg), movel_ocl

    def passo(self, frame, tempo_s: float, bboxes_pessoas) -> None:
        """Um frame decodificado. Barato: recorte pequeno, dois Sobel, um diff."""
        if not self.ativo:
            return
        try:
            mag = self._preparar(frame)
        except Exception as e:  # noqa: BLE001
            log.debug("[movimento] frame ignorado (%s)", e)
            return
        if mag is None:
            return
        ant, self._ant = self._ant, mag
        t_ant, self._t_ant = self._t_ant, tempo_s
        if ant is None:
            return
        validos, ocupacao, movel_ocl = self._mascara_pessoas(bboxes_pessoas)
        n_validos = int(validos.sum())
        par = {"t": round(tempo_s, 2), "ocupacao": round(ocupacao, 3),
               "movel_ocluida": (round(movel_ocl, 3) if movel_ocl is not None else None),
               "valido": False, "movimento": False, "fracao": 0.0,
               "motivo": ""}
        # ⚠️ Fase 94 — OCLUSÃO PESADA POR ONDE, NÃO SÓ POR QUANTO.
        # O caso que o dono achou no vídeo: pct_com_movimento=0 num minuto de
        # OPERAÇÃO MANUAL. Não era imobilidade — era ponto cego. A máscara de
        # pessoa removeu exatamente os pixels onde a manipulação acontecia, e a
        # ocupação TOTAL ficou abaixo do teto (o operador cobre ~20% da zona),
        # então o par saiu como "ausente" em vez de "indisponivel".
        # Aqui a pergunta muda: a parte que SE MEXE está coberta?
        if movel_ocl is not None and movel_ocl > _MOV_MOVEL_OCLUIDA_MAX:
            par["motivo"] = "parte_movel_ocluida"
            self.pares.append(par)
            return
        # Zona tomada por gente: não é "sem movimento", é "não dá para ver".
        if ocupacao > _MOV_OCUPACAO_MAX or n_validos < 64:
            par["motivo"] = "ocluida"
            self.pares.append(par)
            return
        # Contraste estrutural da própria zona — a régua do limiar relativo.
        # Amostrado de 4 em 4 pixels: o percentil 90 de um recorte de textura
        # não muda com a subamostragem, e o percentil cheio era o ponto mais
        # caro do laço (roda a 6 fps, o vídeo inteiro).
        escala = float(np.percentile(ant[::2, ::2][validos[::2, ::2]], 90))
        par["escala"] = round(escala, 2)
        if escala < _MOV_ESCALA_MIN:
            # Máquina escura/lisa demais: qualquer veredito aqui seria sobre a
            # iluminação, não sobre a máquina.
            par["motivo"] = "contraste_baixo"
            self.pares.append(par)
            return
        d = np.abs(mag - ant)
        mascara = ((d > (_MOV_LIMIAR_REL * escala)) & validos).astype(np.uint8)
        n_mov = int(mascara.sum())
        # Coerência espacial: um blob único cobrindo meia zona é iluminação ou
        # um oclusor grande; torno se mexe em pedaços pequenos e localizados.
        if n_mov > 0:
            n_lab, _, stats, _ = cv2.connectedComponentsWithStats(mascara, 8)
            maior = int(stats[1:, cv2.CC_STAT_AREA].max()) if n_lab > 1 else 0
            if maior > _MOV_BLOB_MAX * n_validos:
                par["motivo"] = "blob_grande"
                self.pares.append(par)
                return
        par["valido"] = True
        if self._mapa is not None:
            # Pesa cada pixel pela frequência histórica de movimento da célula.
            # O mapa não muda dentro do vídeo — expandir a grade a cada quadro
            # seria refazer o mesmo resize ~3600 vezes por vídeo.
            if self._peso is None:
                self._peso = cv2.resize(
                    np.asarray(self._mapa, dtype=np.float32),
                    (self.dim[0], self.dim[1]), interpolation=cv2.INTER_NEAREST)
            fracao = float((mascara * self._peso).sum() / max(1, n_validos))
        else:
            fracao = n_mov / float(n_validos)
        par["fracao"] = round(fracao, 4)
        par["movimento"] = fracao >= _MOV_FRACAO_PIXEL
        self.pares.append(par)
        self._acumular_grade(mascara)

    def _acumular_grade(self, mascara) -> None:
        """Onde a máquina se mexe, célula a célula. É o mapa que dispensa o
        dono de desenhar sub-região: as células que se mexem SEMPRE, ao longo
        dos dias, são as partes móveis."""
        try:
            alt, larg = mascara.shape
            ph, pw = max(1, alt // _MOV_GRADE), max(1, larg // _MOV_GRADE)
            for gy in range(_MOV_GRADE):
                for gx in range(_MOV_GRADE):
                    bloco = mascara[gy * ph:(gy + 1) * ph, gx * pw:(gx + 1) * pw]
                    if bloco.size and bloco.any():
                        self.grade[gy][gx] += 1
            self.n_pares_grade += 1
        except Exception:  # noqa: BLE001
            pass

    def por_minuto(self, bucket_s: float = 60.0) -> dict:
        """{índice do minuto → veredito + detalhe}. O minuto é a mesma unidade
        do evento principal, então o sinal cola no evento sem interpolação."""
        if not self.ativo:
            return {}
        grupos: dict = defaultdict(list)
        for p in self.pares:
            grupos[int(p["t"] // bucket_s)].append(p)
        saida = {}
        for m, ps in grupos.items():
            r = classificar_movimento(ps, cam_id=self.cam_id, zona=self.zona_nome)
            # O modo aqui é PRELIMINAR (sem `maos_maquina`, que só existe no
            # minuto consolidado); serve ao prompt. A versão que vai ao banco é
            # recomposta em `etapa_consolidar_principais`, com as mãos.
            r["modo"] = modo_operacao(r["movimento"], r["detalhe"], None)
            saida[m] = r
        return saida


def classificar_movimento(pares: list, cam_id: str | None = None,
                          zona: str | None = None) -> dict:
    """(veredito, detalhe) de uma lista de pares — FUNÇÃO PURA, testável.

    `continuo` × `intermitente` × `ausente` é o que 360 pares por minuto
    compram e 7 não compravam: `intermitente` é a assinatura do torno manual
    (avança, para, mede, avança), diferente do corte automático contínuo e
    diferente da máquina realmente parada.
    """
    n = len(pares)
    validos = [p for p in pares if p.get("valido")]
    nv = len(validos)
    ocup = [p.get("ocupacao") for p in pares if p.get("ocupacao") is not None]
    detalhe = {
        "pares": n,
        "pares_validos": nv,
        "pct_zona_ocupada": round(100.0 * sum(ocup) / len(ocup), 1) if ocup else None,
        # Fase 94: é este número que separa "a máquina estava parada" de "a
        # parte que se mexe estava coberta pelo operador".
        "pct_movel_ocluida": (
            round(100.0 * sum(m) / len(m), 1)
            if (m := [p["movel_ocluida"] for p in pares
                      if p.get("movel_ocluida") is not None]) else None),
        "descartados": {
            m: sum(1 for p in pares if p.get("motivo") == m)
            for m in ("ocluida", "contraste_baixo", "blob_grande",
                      "parte_movel_ocluida")
            if any(p.get("motivo") == m for p in pares)
        } or None,
        "cam": cam_id,
        "zona": zona,
        "mapa_pesado": False,
    }
    if n == 0 or nv < max(1, int(_MOV_MIN_VALIDOS * n)):
        # Poucos pares mensuráveis: o minuto não recebe veredito. Dizer
        # "ausente" aqui seria converter oclusão em máquina parada.
        detalhe["pct_intervalos_com_movimento"] = None
        return {"movimento": "indisponivel", "detalhe": detalhe}
    com_mov = sum(1 for p in validos if p.get("movimento"))
    frac = com_mov / float(nv)
    detalhe["pct_intervalos_com_movimento"] = round(100.0 * frac, 1)
    detalhe["contraste"] = round(
        sum(p.get("escala") or 0 for p in validos) / max(1, nv), 1)
    if frac >= _MOV_CONTINUO:
        v = "continuo"
    elif frac >= _MOV_INTERMITENTE:
        v = "intermitente"
    else:
        v = "ausente"
    return {"movimento": v, "detalhe": detalhe}


def modo_operacao(mov: str | None, detalhe: dict | None,
                  maos_maquina: bool | None) -> str:
    """Fase 94 — TRÊS estados de trabalho, não dois. FUNÇÃO PURA.

    O desenho anterior só tinha "a máquina se mexe" ou "não se mexe", e isso
    apagava um estado inteiro: OPERAÇÃO MANUAL — o operador manipulando a
    máquina, trabalho produtivo acontecendo, sem ciclo automático em curso.
    Ela caía como `ausente`, junto com a parada de verdade.

    ⚠️ A REGRA CENTRAL: `manual` só é afirmado quando a medição ficou
    INDISPONÍVEL **por causa das mãos** — ou seja, quando a parte móvel estava
    coberta pelo operador. Compor "ausente + mãos → manual" acertaria o caso
    do vídeo pela razão errada, e quebraria no seguinte: mão na máquina
    DURANTE um ciclo automático (ajustando o avanço com a peça girando).

    Mãos na máquina com medição BOA e sem movimento não é manual: é o operador
    tocando uma máquina parada, que pode ser mil coisas. Isso é
    `indeterminado`, e dizer `indeterminado` é a resposta honesta.
    """
    d = detalhe or {}
    if mov in ("continuo", "intermitente"):
        # A máquina se mexe. Se há mãos junto, é operação acompanhando ciclo —
        # continua sendo a máquina trabalhando.
        return "automatico"
    if mov == "indisponivel":
        cegou_pela_parte_movel = (d.get("descartados") or {}).get("parte_movel_ocluida", 0)
        if maos_maquina and cegou_pela_parte_movel:
            return "manual"
        return "indeterminado"
    if mov == "ausente":
        if maos_maquina:
            # Mediu bem, não viu movimento, e há mão na máquina: ambíguo entre
            # manipulação fina demais para o sensor e mão apoiada. Não afirma.
            return "indeterminado"
        ocup = d.get("pct_zona_ocupada")
        contraste = d.get("contraste")
        if (ocup is not None and ocup <= 100 * _MOV_OCUPACAO_MAX * 0.5
                and contraste is not None and contraste >= 2 * _MOV_ESCALA_MIN):
            return "parado"
        return "indeterminado"
    return "indeterminado"


def frase_modo(modo: str | None) -> str:
    """Como o terceiro estado entra no prompt. Descreve o observado; quem
    decide se aquilo é produtivo continua sendo o gestor, via categoria."""
    return {
        "automatico": ("SENSOR: a máquina esteve em MOVIMENTO neste minuto "
                       "(medido por diferença entre quadros, descontando as pessoas)"),
        "manual": ("SENSOR: a parte móvel da máquina esteve COBERTA PELAS MÃOS do "
                   "operador — indício de OPERAÇÃO MANUAL, não de máquina parada"),
        "parado": ("SENSOR: a máquina NÃO se moveu neste minuto, com a zona "
                   "desimpedida e contraste bom — parada de verdade"),
    }.get(modo or "", "")


def frase_movimento(mov: str | None, detalhe: dict | None) -> str:
    """Como o fato entra no PROMPT. Descreve o observado e não conclui: quem
    traduz movimento em ciclo/parada continua sendo o VLM."""
    if not mov or mov == "indisponivel":
        return ""
    pct = (detalhe or {}).get("pct_intervalos_com_movimento")
    sufixo = f" ({pct:.0f}% dos intervalos do minuto)" if pct is not None else ""
    return {
        "continuo": ("SENSOR: houve movimento CONTÍNUO na área da máquina ao "
                     f"longo do minuto{sufixo} — medido por diferença entre "
                     "quadros, descontando as pessoas"),
        "intermitente": ("SENSOR: houve movimento INTERMITENTE na área da "
                         f"máquina{sufixo} — medido por diferença entre "
                         "quadros, descontando as pessoas"),
        "ausente": ("SENSOR: NÃO houve movimento detectável na área da máquina "
                    f"neste minuto{sufixo} — medido por diferença entre "
                    "quadros, descontando as pessoas"),
    }.get(mov, "")


def veto_movimento(mov: str | None, detalhe: dict | None,
                   maquina_vlm: str | None, modo: str | None = None) -> str | None:
    """Fase 89 — o ÚNICO poder do sinal determinístico, e ele não sobrescreve.

    Movimento claramente ausente, zona pouco ocupada e contraste bom, mas o
    VLM afirmando `ciclo`: o rótulo NÃO é trocado — o evento é marcado para a
    fila. Não é corrigir, é recusar-se a ter confiança.

    Só vale com a injeção ligada: sem o fato no prompt o VLM não teve como
    considerar o movimento, e puni-lo por isso seria injusto e inútil.

    ⚠️ Fase 99 — ESTA CAMADA NÃO DISPARA MAIS, e é consequência correta. Ela
    existe para pegar o VLM afirmando `ciclo` sem movimento; o VLM parou de
    afirmar `ciclo` (`_maquina_do_vlm` devolve None sempre). Sem afirmação, não
    há contradição a marcar. Fica de pé porque o dia em que houver um
    discriminador de verdade, é aqui que ele se contradiz com o sensor.
    """
    if not _MOV_INJETAR or mov != "ausente":
        return None
    # Fase 94: `ausente` deixou de ser suficiente. Com mãos na máquina o
    # minuto pode ser OPERAÇÃO MANUAL — trabalho produtivo —, e vetar ali
    # mandaria um minuto produtivo para a fila como duvidoso. Só veta quando o
    # modo diz `parado`, que é o único estado com evidência de imobilidade.
    if modo and modo != "parado":
        return None
    if _normalizar_maquina(maquina_vlm) != "ciclo":
        return None
    d = detalhe or {}
    ocup = d.get("pct_zona_ocupada")
    contraste = d.get("contraste")
    if ocup is None or ocup > 100 * _MOV_OCUPACAO_MAX * 0.5:
        return None                      # zona muito ocupada: medição fraca
    if contraste is None or contraste < 2 * _MOV_ESCALA_MIN:
        return None                      # contraste apertado: medição fraca
    return ("o VLM afirmou máquina EM CICLO, mas o sensor não viu movimento "
            "nenhum na área da máquina neste minuto (zona desocupada e "
            "contraste bom)")


def carregar_mapa_movimento(sb, empresa: str, processo: str,
                            cam_id: str | None) -> dict | None:
    """Mapa acumulado de ONDE a máquina se mexe, por câmera. Falha → None, e o
    medidor roda sem peso: um mapa indisponível não pode parar a medição."""
    if not _MOV_ENABLE:
        return None
    try:
        r = (sb.table("mapa_movimento").select("grade, n_pares")
             .eq("empresa", empresa).eq("processo", processo)
             .eq("cam_id", cam_id or "").limit(1).execute().data or [])
        return r[0] if r else None
    except Exception as e:  # noqa: BLE001
        log.debug("[movimento] mapa não lido (%s) — seguindo sem peso.", e)
        return None


def acumular_mapa_movimento(sb, empresa: str, processo: str,
                            grade_video: dict) -> bool:
    """Soma a grade deste vídeo ao mapa do processo. NÃO-FATAL: o mapa é
    refinamento, e uma escrita falha não pode derrubar o processamento.

    Somar em vez de substituir é o que faz o mapa APRENDER ao longo dos dias —
    e é o que permite exigir base mínima antes de confiar nele.
    """
    if not grade_video or not grade_video.get("n_pares"):
        return False
    cam = grade_video.get("cam_id") or ""
    try:
        atual = (sb.table("mapa_movimento").select("grade, n_pares")
                 .eq("empresa", empresa).eq("processo", processo)
                 .eq("cam_id", cam).limit(1).execute().data or [])
        nova = [list(l) for l in grade_video["grade"]]
        n = int(grade_video["n_pares"])
        if atual:
            velha = atual[0].get("grade") or []
            if len(velha) == len(nova) and all(
                    len(a) == len(b) for a, b in zip(velha, nova)):
                nova = [[int(a) + int(b) for a, b in zip(la, lb)]
                        for la, lb in zip(velha, nova)]
            n += int(atual[0].get("n_pares") or 0)
        sb.table("mapa_movimento").upsert({
            "empresa": empresa, "processo": processo, "cam_id": cam,
            "zona": grade_video.get("zona"), "grade": nova, "n_pares": n,
            "atualizado_em": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="empresa,processo,cam_id").execute()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("[movimento] mapa não atualizado (%s) — não-fatal.", e)
        return False



def calibrar_movimento(sb, empresa: str, processo: str, dia: str | None = None,
                       limite: int = 200) -> dict:
    """Fase 89 — a tela de calibração, por MINUTO, ordenada pela DISCORDÂNCIA.

    Concordância não ensina: sensor e VLM podem estar certos ou errados
    juntos. O que ensina é o minuto em que um diz uma coisa e o outro diz
    outra — e o link do vídeo ao lado, para o dono decidir quem tem razão
    olhando a cena.

    Só leitura. Não valida, não corrige, não entra na fila.
    """
    eventos = varrer(
        sb, "eventos",
        "id, video_id, comportamento_label, label_corrigido, descricao_bruta, "
        "tempo_inicio_s, tempo_fim_s, movimento_maquina, movimento_detalhe, "
        "modo_operacao, cena_maquina, cena_imovel, papel_pessoa, principal, "
        "validacao_correto, versao_instrumento",
        empresa=empresa, processo=processo,
        ajustes=lambda q: q.not_.is_("movimento_maquina", "null"),
    )
    videos = varrer(sb, "videos", "id, nome, cam_id, caminho, processado_em",
                    empresa=empresa, processo=processo)
    meta = {v["id"]: v for v in videos}

    def _peso(mov, vlm):
        # 2 = contradição frontal; 1 = tensão; 0 = concordam ou não dá para ver.
        if mov == "ausente" and vlm == "ciclo":
            return 2
        if mov == "continuo" and vlm == "parada":
            return 2
        if mov == "intermitente" and vlm:
            return 1
        return 0

    itens, resumo = [], defaultdict(int)
    # Fase 94 — O NÚMERO QUE DECIDE LIGAR A INJEÇÃO, e por RÓTULO.
    # Se a operação manual estiver concentrada em `operar_torno`, o modo só
    # refina o estado da máquina. Se estiver espalhada em `monitorar_maquina`,
    # significa que o VLM está chamando de "monitorar" o que é trabalho manual
    # — e aí o modo corrige a MEDIÇÃO DE PRODUTIVIDADE, não só o estado.
    por_modo: dict = {}
    for e in eventos:
        if e.get("principal") is False or e.get("validacao_correto") is False:
            continue
        v = meta.get(e.get("video_id")) or {}
        dt0 = _inicio_video_dt(v)
        if dia:
            if not dt0:
                continue
            inst = dt0 + timedelta(seconds=float(e.get("tempo_inicio_s") or 0))
            if inst.date().isoformat() != dia:
                continue
        mov, vlm = e.get("movimento_maquina"), e.get("cena_maquina")
        modo = e.get("modo_operacao")
        rot = e.get("label_corrigido") or e.get("comportamento_label")
        d = e.get("movimento_detalhe") or {}
        resumo[f"{mov}×{vlm or 'null'}"] += 1
        dur = max(0.0, float(e.get("tempo_fim_s") or 0)
                  - float(e.get("tempo_inicio_s") or 0))
        _mod = por_modo.setdefault(modo or "sem_modo",
                                   {"minutos": 0.0, "eventos": 0, "rotulos": {}})
        _mod["minutos"] += dur / 60.0
        _mod["eventos"] += 1
        _mod["rotulos"][rot] = _mod["rotulos"].get(rot, 0.0) + dur / 60.0
        inst = (dt0 + timedelta(seconds=float(e.get("tempo_inicio_s") or 0))
                if dt0 else None)
        itens.append({
            "evento_id": e.get("id"), "video_id": e.get("video_id"),
            "video": v.get("nome"), "cam_id": v.get("cam_id"),
            "hora": inst.strftime("%H:%M:%S") if inst else None,
            "dia": inst.date().isoformat() if inst else None,
            "ini": e.get("tempo_inicio_s"), "fim": e.get("tempo_fim_s"),
            "sensor": mov,
            "vlm_afirmou": vlm,
            "rotulo": rot,
            "modo_operacao": modo,
            "descricao": e.get("descricao_bruta"),
            "pct_com_movimento": d.get("pct_intervalos_com_movimento"),
            "pct_zona_ocupada": d.get("pct_zona_ocupada"),
            "pct_movel_ocluida": d.get("pct_movel_ocluida"),
            "contraste": d.get("contraste"),
            "pares": d.get("pares"), "pares_validos": d.get("pares_validos"),
            "descartados": d.get("descartados"),
            "mapa_pesado": d.get("mapa_pesado"),
            "versao_instrumento": e.get("versao_instrumento"),
            "discordam": _peso(mov, vlm),
        })

    # Discordância primeiro; dentro dela, cronológico — o dono percorre o turno
    # em ordem em vez de pular pelo vídeo.
    itens.sort(key=lambda x: (-x["discordam"], x["dia"] or "", x["hora"] or ""))
    n_med = sum(1 for i in itens if i["sensor"] != "indisponivel")
    return {
        "processo": processo, "dia": dia,
        "minutos": len(itens),
        "minutos_medidos": n_med,
        "minutos_indisponiveis": len(itens) - n_med,
        "discordancias": sum(1 for i in itens if i["discordam"] == 2),
        "cruzamento": dict(sorted(resumo.items(), key=lambda kv: -kv[1])),
        "por_modo": {
            k: {"minutos": round(v["minutos"], 1), "eventos": v["eventos"],
                "pct": round(100.0 * v["minutos"]
                             / max(1e-9, sum(x["minutos"] for x in por_modo.values())), 1),
                # O corte por rótulo: é ele que diz se o modo corrige o estado
                # da máquina ou a medição de produtividade.
                "rotulos": sorted(
                    ({"rotulo": r, "minutos": round(mi, 1)}
                     for r, mi in v["rotulos"].items()),
                    key=lambda x: -x["minutos"])[:12]}
            for k, v in sorted(por_modo.items(), key=lambda kv: -kv[1]["minutos"])
        },
        "limiares": limiares_movimento(),
        "injecao_ligada": _MOV_INJETAR,
        "itens": itens[:limite],
        "truncado": len(itens) > limite,
        "nota": ("Ordenado pela DISCORDÂNCIA entre o sensor e o que o VLM "
                 "afirmou: é onde se aprende. 'indisponivel' não é 'ausente' — "
                 "é zona ocupada, contraste baixo ou par descartado."),
    }


def limiares_movimento() -> dict:
    """Os limiares em vigor, com o nome da variável de ambiente ao lado. O dono
    calibra sem deploy — e sem ter que abrir o código para achar o nome."""
    return {
        "KV_MOVIMENTO": _MOV_ENABLE,
        "KV_MOVIMENTO_INJETAR": _MOV_INJETAR,
        "KV_MOV_LARGURA": _MOV_LARGURA,
        "KV_MOV_LIMIAR_REL": _MOV_LIMIAR_REL,
        "KV_MOV_ESCALA_MIN": _MOV_ESCALA_MIN,
        "KV_MOV_FRACAO_PIXEL": _MOV_FRACAO_PIXEL,
        "KV_MOV_BLOB_MAX": _MOV_BLOB_MAX,
        "KV_MOV_OCUPACAO_MAX": _MOV_OCUPACAO_MAX,
        "KV_MOV_DILATA_PESSOA": _MOV_DILATA_PESSOA,
        "KV_MOV_MIN_VALIDOS": _MOV_MIN_VALIDOS,
        "KV_MOV_CONTINUO": _MOV_CONTINUO,
        "KV_MOV_INTERMITENTE": _MOV_INTERMITENTE,
        "KV_MOV_GRADE": _MOV_GRADE,
        "KV_MOV_MAPA_MIN_PARES": _MOV_MAPA_MIN_PARES,
    }



# ═════════════════════════════════════════════════════════════════════════
# Fase 91 — O TITULAR DO POSTO. EM SOMBRA.
#
# O PRINCÍPIO, e ele é a coisa toda: o titular NÃO é quem está na zona num
# instante — é quem DOMINA a presença na zona ao longo do dia. Instante é
# ruído (o líder passa, o colega encosta, o operador sai para o banheiro);
# domínio é regime.
#
# IDENTIDADE ANÔNIMA POR PAPEL, NUNCA CADASTRO DE PESSOA. Os grupos recebem
# rótulos posicionais (`g1`, `g2`) que valem para UM dia e UMA câmera. Não há
# nome, não há re-identificação entre dias que persista pessoa, não há galeria
# de rostos. É decisão de LGPD, não de conveniência: o produto mede o POSTO,
# e para medir o posto basta saber que "o mesmo alguém dominou o dia".
#
# POR CÂMERA E POR DIA, porque cam1 e cam2 não são a mesma régua: ângulo,
# distância, iluminação e balanço de branco diferentes fazem a MESMA pessoa ter
# histograma diferente nas duas. Cruzar as duas exigiria calibração de cor que
# não temos, e um agrupamento errado é pior que dois agrupamentos separados.
#
# ORDEM DOS SINAIS — cor primeiro, e o motivo é n=1:
#   1. HISTOGRAMA DE COR (sup/inf) — o único robusto com uma amostra só. 57
#      dos 90 tracks do primeiro dia tinham 8 s (o mínimo); qualquer sinal que
#      precise de dispersão não existe nesses.
#   2. RAZÕES CORPORAIS — só as medidas o bastante (`n` por razão). Uma razão
#      medida 3 vezes num track de 200 amostras não é a mesma coisa que uma
#      medida 180 vezes, e por isso `fechar_descritores` guarda o `n`.
#   3. ALTURA RELATIVA — último, porque numa câmera fixa ela é boa entre
#      pessoas em profundidade parecida e péssima entre profundidades
#      diferentes.
#
# TUDO CPU. Zero chamada de API: histograma é correlação de vetor, agrupamento
# é união de vizinhos. Roda DEPOIS do turno, fora do caminho de ingestão, e
# falha sozinho — se a identificação quebrar, a coleta não pode parar.
# ═════════════════════════════════════════════════════════════════════════
_TIT_SIM_COR = float(os.environ.get("KV_TITULAR_SIM_COR", "0.62"))
_TIT_RAZAO_MIN_N = int(os.environ.get("KV_TITULAR_RAZAO_MIN_N", "8"))
_TIT_RAZAO_TOL = float(os.environ.get("KV_TITULAR_RAZAO_TOL", "0.18"))
_TIT_ALTURA_TOL = float(os.environ.get("KV_TITULAR_ALTURA_TOL", "0.22"))
# GUARDA DE PISO — o dominante precisa DOMINAR. Sem isso, num dia em que o
# operador faltou e outra pessoa usou o torno por 10 minutos, o intruso seria
# coroado titular com 100% de um total minúsculo.
_TIT_PISO_PCT = float(os.environ.get("KV_TITULAR_PISO_PCT", "40"))
_TIT_PISO_MIN = float(os.environ.get("KV_TITULAR_PISO_MINUTOS", "20"))


def _sim_hist(a, b) -> float | None:
    """Similaridade 0..1 entre dois histogramas (interseção normalizada).

    Interseção e não cosseno: histograma é distribuição, e a interseção tem
    interpretação direta — "que fração da cor de um está na do outro".
    """
    if not a or not b or len(a) != len(b):
        return None
    va = [_num(x) or 0.0 for x in a]
    vb = [_num(x) or 0.0 for x in b]
    sa, sb = sum(va), sum(vb)
    if sa <= 0 or sb <= 0:
        return None
    na = [v / sa for v in va]
    nb = [v / sb for v in vb]
    return float(sum(min(x, y) for x, y in zip(na, nb)))


def _num(v) -> float | None:
    """Coage para float ou None.

    ⚠️ COLUNA `numeric` DO POSTGRES VOLTA COMO STRING no PostgREST — `altura_rel`
    e `tempo_posto_s` chegam '0.4831', não 0.4831. Comparar string com número
    levanta TypeError, e o agrupamento inteiro morreria no primeiro par. Os
    testes sintéticos não pegavam porque construíam floats: o dublê era mais
    generoso que o serviço real, que é exatamente a armadilha da Fase 81.
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sim_descritores(d1: dict, d2: dict) -> tuple[float | None, str]:
    """(similaridade, motivo) entre dois descritores da MESMA câmera.

    Devolve None quando não há sinal comparável — e isso NÃO é "são pessoas
    diferentes", é "não dá para dizer". A diferença importa: sem ela, track sem
    cor viraria automaticamente um grupo novo e o dia viraria sopa.
    """
    s_sup = _sim_hist(d1.get("hist_sup"), d2.get("hist_sup"))
    s_inf = _sim_hist(d1.get("hist_inf"), d2.get("hist_inf"))
    partes = [s for s in (s_sup, s_inf) if s is not None]
    if not partes:
        return None, "sem cor comparável"
    cor = sum(partes) / len(partes)
    if cor < _TIT_SIM_COR:
        return cor, f"cor {cor:.2f} < {_TIT_SIM_COR}"

    # ── Razões corporais: só as MEDIDAS O BASTANTE nos dois lados ──
    r1, r2 = (d1.get("razoes") or {}), (d2.get("razoes") or {})
    conflito = 0
    comparadas = 0
    for nome in set(r1) & set(r2):
        a, b = r1[nome], r2[nome]
        if not isinstance(a, dict) or not isinstance(b, dict):
            continue
        if ((_num(a.get("n")) or 0) < _TIT_RAZAO_MIN_N
                or (_num(b.get("n")) or 0) < _TIT_RAZAO_MIN_N):
            continue                      # medida de menos: não opina
        ma, mb = _num(a.get("med")), _num(b.get("med"))
        if ma is None or mb is None or max(abs(ma), abs(mb)) <= 0:
            continue
        comparadas += 1
        if abs(ma - mb) / max(abs(ma), abs(mb)) > _TIT_RAZAO_TOL:
            conflito += 1
    if comparadas and conflito > comparadas / 2:
        return cor, f"razões corporais divergem ({conflito}/{comparadas})"

    # ── Altura relativa: desempate, nunca porta de entrada ──
    h1, h2 = _num(d1.get("altura_rel")), _num(d2.get("altura_rel"))
    if h1 and h2 and max(h1, h2) > 0:
        if abs(h1 - h2) / max(h1, h2) > _TIT_ALTURA_TOL:
            return cor, f"altura difere {abs(h1-h2)/max(h1,h2):.0%}"
    return cor, "ok"


# ── Fase 92 — COSTURA GEOMÉTRICA, ANTES DA COR ───────────────────────────
# O experimento da Fase 91 respondeu: aparência sozinha não separa. Medindo
# operador × visitante (rótulo fraco mas independente da cor), a separação foi
# de +0,025 quando um limiar precisaria de ~+0,15. E a distribuição de
# similaridade é unimodal, sem vale — não existe limiar bom.
#
# A causa está à vista: o track MEDIANO da cam1 dura 8 s, o mínimo. Com 1-2
# amostras o histograma é ruído, e só 8% dos tracks da cam1 têm alguma razão
# corporal bem medida (o operador fica atrás do torno). Na cam2 o track mediano
# dura 48 s e a cobertura de razão sobe para 41% — a diferença é DURAÇÃO, não
# ângulo.
#
# Então ataca-se a duração primeiro, e por GEOMETRIA, que não depende de
# aparência: um track que termina onde outro começa poucos segundos depois é a
# mesma pessoa. Pessoa não se teletransporta — é o mesmo princípio da ponte
# temporal da Fase 34, aplicado a tracks em vez de a presença.
#
# ⚠️ Custa ZERO: é aritmética sobre dados que o detector já produziu.
_COSTURA_GAP_S = float(os.environ.get("KV_COSTURA_GAP_S", "6"))
# Distância máxima entre o fim de um e o começo do outro, em ALTURAS DE CORPO.
# Invariante de escala: 0,8 altura é aproximadamente um passo lateral.
_COSTURA_DIST = float(os.environ.get("KV_COSTURA_DIST", "0.8"))
# Altura aparente não pode saltar: quem estava perto não fica longe em 3 s.
_COSTURA_ALTURA_TOL = float(os.environ.get("KV_COSTURA_ALTURA_TOL", "0.30"))
# Piso DEPOIS da costura: track que não chegar aqui fica de fora do
# agrupamento em vez de poluir a cadeia com histograma de 1 amostra.
_COSTURA_MIN_AMOSTRAS = int(os.environ.get("KV_COSTURA_MIN_AMOSTRAS", "5"))

# Fase 111C: vetos auxiliares da costura RESIDUAL por segmento. Os gates
# principais continuam sendo tempo + geometria; aparência nunca autoriza uma
# união sozinha. Estes valores não reutilizam pisos do titular diário.
_IDENTIDADE_SEG_COR_MIN_AMOSTRAS = int(_operador_segmento_env_float(
    "KV_OPERADOR_SEGMENTO_COR_MIN_AMOSTRAS", 3.0, 1.0, 100.0,
))
_IDENTIDADE_SEG_COR_VETO = _operador_segmento_env_float(
    "KV_OPERADOR_SEGMENTO_COR_VETO", 0.45, 0.0, 1.0,
)
_IDENTIDADE_SEG_COR_FAVORAVEL = _operador_segmento_env_float(
    "KV_OPERADOR_SEGMENTO_COR_FAVORAVEL", 0.70, 0.0, 1.0,
)
_IDENTIDADE_SEG_RAZAO_TOL = _operador_segmento_env_float(
    "KV_OPERADOR_SEGMENTO_RAZAO_TOL", 0.18, 0.01, 1.0,
)
_IDENTIDADE_SEG_ASPECTO_VETO = _operador_segmento_env_float(
    "KV_OPERADOR_SEGMENTO_ASPECTO_VETO", 0.60, 0.05, 2.0,
)


def _ponta(d: dict, qual: str):
    v = d.get("bbox_fim" if qual == "fim" else "bbox_ini")
    if not v or len(v) < 3:
        return None
    try:
        return (float(v[0]), float(v[1]), float(v[2]))
    except (TypeError, ValueError):
        return None


def costurar_tracks(descritores: list) -> list:
    """Junta tracks do MESMO vídeo e da MESMA câmera que são a mesma pessoa por
    CONTINUIDADE — o track termina e outro começa logo depois, ali perto.

    Geometria, não aparência: é confiável justamente onde a cor falha. Tracks
    não cruzam vídeo (o tracker é zerado entre vídeos desde a Fase 64), então a
    costura é sempre dentro de um vídeo — que é exatamente onde a fragmentação
    acontece.

    Devolve descritores FUNDIDOS: histogramas somados (mais amostras, menos
    ruído), razões com o `n` somado, tempos somados.
    """
    por_video: dict = defaultdict(list)
    for d in descritores:
        por_video[(d.get("video_id"), d.get("cam_id"))].append(d)

    saida = []
    for _chave, ds in por_video.items():
        ds = sorted(ds, key=lambda x: (_num(x.get("t_ini_s")) or 0.0))
        pai = list(range(len(ds)))

        def achar(i):
            while pai[i] != i:
                pai[i] = pai[pai[i]]
                i = pai[i]
            return i

        for i, a in enumerate(ds):
            fa = _num(a.get("t_fim_s"))
            pa = _ponta(a, "fim")
            if fa is None or pa is None:
                continue
            melhor, melhor_gap = None, None
            for j, b in enumerate(ds):
                if i == j:
                    continue
                ib = _num(b.get("t_ini_s"))
                pb = _ponta(b, "ini")
                if ib is None or pb is None:
                    continue
                gap = ib - fa
                # Só costura para FRENTE: o outro track começa DEPOIS deste
                # terminar. Sobreposição no tempo é prova de que são duas
                # pessoas — as duas estavam em quadro ao mesmo tempo.
                if gap < 0 or gap > _COSTURA_GAP_S:
                    continue
                alt = max(1e-6, (pa[2] + pb[2]) / 2)
                dist = ((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2) ** 0.5 / alt
                if dist > _COSTURA_DIST:
                    continue
                if abs(pa[2] - pb[2]) / max(pa[2], pb[2]) > _COSTURA_ALTURA_TOL:
                    continue      # saltou de profundidade: não é a mesma pessoa
                if melhor is None or gap < melhor_gap:
                    melhor, melhor_gap = j, gap
            if melhor is not None:
                ra, rb = achar(i), achar(melhor)
                if ra != rb:
                    pai[max(ra, rb)] = min(ra, rb)

        grupos: dict = defaultdict(list)
        for i, d in enumerate(ds):
            grupos[achar(i)].append(d)
        for membros in grupos.values():
            saida.append(_fundir_tracks(membros) if len(membros) > 1 else membros[0])
    return saida


def _fundir_tracks(membros: list) -> dict:
    """Funde tracks costurados num descritor só. Histogramas SOMADOS — é daí
    que vem o ganho: dois tracks de 1 amostra viram um de 2, e o ruído cai."""
    membros = sorted(membros, key=lambda m: _num(m.get("t_ini_s")) or 0.0)
    base = max(membros, key=lambda m: _num(m.get("tempo_visivel_s")) or 0.0)
    def soma_hist(chave):
        vetores = [m.get(chave) for m in membros if m.get(chave)]
        if not vetores:
            return None
        n = min(len(v) for v in vetores)
        return [sum((_num(v[i]) or 0.0) for v in vetores) for i in range(n)]
    razoes: dict = {}
    for m in membros:
        for nome, r in (m.get("razoes") or {}).items():
            if not isinstance(r, dict):
                continue
            cur = razoes.setdefault(nome, {"med": None, "mad": None, "n": 0, "_soma": 0.0})
            n = int(_num(r.get("n")) or 0)
            med = _num(r.get("med"))
            if n and med is not None:
                cur["_soma"] += med * n
                cur["n"] += n
    for nome, r in razoes.items():
        r["med"] = round(r["_soma"] / r["n"], 4) if r["n"] else None
        r.pop("_soma", None)
    alturas = [(_num(m.get("altura_rel")), _num(m.get("tempo_visivel_s")) or 1.0)
               for m in membros if _num(m.get("altura_rel")) is not None]
    altura = (round(sum(a * p for a, p in alturas) / sum(p for _a, p in alturas), 5)
              if alturas else None)
    return {
        **base,
        "pessoa_track_id": base.get("pessoa_track_id"),
        "tracks_costurados": [m.get("pessoa_track_id") for m in membros],
        "n_costurados": len(membros),
        "t_ini_s": _num(membros[0].get("t_ini_s")),
        "t_fim_s": max((_num(m.get("t_fim_s")) or 0.0) for m in membros),
        "tempo_posto_s": sum((_num(m.get("tempo_posto_s")) or 0.0) for m in membros),
        "tempo_visivel_s": sum((_num(m.get("tempo_visivel_s")) or 0.0) for m in membros),
        "n_amostras": sum(int(_num(m.get("n_amostras")) or 0) for m in membros),
        "n_amostras_posto": sum(int(_num(m.get("n_amostras_posto")) or 0) for m in membros),
        "hist_sup": soma_hist("hist_sup"),
        "hist_inf": soma_hist("hist_inf"),
        "razoes": razoes or None,
        "altura_rel": altura,
    }


def _guardrails_identidade_segmento(a: dict, b: dict) -> dict:
    """Compara apenas guardrails visuais; ausência de medida é neutra."""
    evidencias: dict = {
        "veto": False,
        "motivo": None,
        "auxiliar_disponivel": False,
        "auxiliar_favoravel": False,
        "cor_similaridade": None,
        "razoes_comparadas": [],
    }

    sims_cor: list[float] = []
    bins_a, bins_b = a.get("hist_bins") or {}, b.get("hist_bins") or {}
    for parte, chave_n in (("sup", "n_sup"), ("inf", "n_inf")):
        if (
            int(_num(bins_a.get(chave_n)) or 0) < _IDENTIDADE_SEG_COR_MIN_AMOSTRAS
            or int(_num(bins_b.get(chave_n)) or 0) < _IDENTIDADE_SEG_COR_MIN_AMOSTRAS
        ):
            continue
        sim = _sim_hist(a.get(f"hist_{parte}"), b.get(f"hist_{parte}"))
        if sim is not None:
            sims_cor.append(sim)
    if sims_cor:
        evidencias["auxiliar_disponivel"] = True
        evidencias["cor_similaridade"] = round(min(sims_cor), 4)
        if min(sims_cor) < _IDENTIDADE_SEG_COR_VETO:
            evidencias.update({
                "veto": True,
                "motivo": "cor_incompativel",
            })
            return evidencias
        if min(sims_cor) >= _IDENTIDADE_SEG_COR_FAVORAVEL:
            evidencias["auxiliar_favoravel"] = True

    razoes_a, razoes_b = a.get("razoes") or {}, b.get("razoes") or {}
    for nome in sorted(set(razoes_a) & set(razoes_b)):
        ra, rb = razoes_a.get(nome), razoes_b.get(nome)
        if not isinstance(ra, dict) or not isinstance(rb, dict):
            continue
        if (
            int(_num(ra.get("n")) or 0) < _TIT_RAZAO_MIN_N
            or int(_num(rb.get("n")) or 0) < _TIT_RAZAO_MIN_N
        ):
            continue
        ma, mb = _num(ra.get("med")), _num(rb.get("med"))
        mada, madb = _num(ra.get("mad")), _num(rb.get("mad"))
        if None in (ma, mb, mada, madb) or max(abs(ma), abs(mb)) <= 0:
            continue
        evidencias["auxiliar_disponivel"] = True
        diferenca = abs(ma - mb)
        base = max(abs(ma), abs(mb))
        limite_base = _IDENTIDADE_SEG_RAZAO_TOL * base
        limite_veto = limite_base + 2.0 * (abs(mada) + abs(madb))
        evidencias["razoes_comparadas"].append({
            "nome": nome,
            "diferenca": round(diferenca, 4),
            "limite_veto": round(limite_veto, 4),
        })
        if diferenca > limite_veto:
            evidencias.update({
                "veto": True,
                "motivo": f"razao_corporal_incompativel:{nome}",
            })
            return evidencias
        if diferenca <= limite_base:
            evidencias["auxiliar_favoravel"] = True

    aspecto_a, aspecto_b = _num(a.get("aspecto")), _num(b.get("aspecto"))
    if aspecto_a and aspecto_b:
        delta_aspecto = abs(aspecto_a - aspecto_b) / max(aspecto_a, aspecto_b)
        if delta_aspecto > _IDENTIDADE_SEG_ASPECTO_VETO:
            evidencias.update({
                "veto": True,
                "motivo": "aspecto_incompativel",
            })
    return evidencias


def _avaliar_costura_identidade_segmento(
    grupo: list[dict], novo: dict,
) -> tuple[bool, dict]:
    """Aplica gates sem score: continuidade no último + vetos no grupo todo."""
    ultimo = max(
        grupo,
        key=lambda d: (
            _num(d.get("t_fim_s")) or float("-inf"),
            int(_num(d.get("pessoa_track_id")) or -1),
        ),
    )
    cam_novo = str(novo.get("cam_id") or "cam1")
    if any(str(m.get("cam_id") or "cam1") != cam_novo for m in grupo):
        return False, {"motivo": "camera_diferente"}

    ini_novo, fim_novo = _num(novo.get("t_ini_s")), _num(novo.get("t_fim_s"))
    fim_ultimo = _num(ultimo.get("t_fim_s"))
    if None in (ini_novo, fim_novo, fim_ultimo) or fim_novo < ini_novo:
        return False, {"motivo": "intervalo_invalido"}

    for membro in grupo:
        ini_m, fim_m = _num(membro.get("t_ini_s")), _num(membro.get("t_fim_s"))
        if None in (ini_m, fim_m):
            return False, {"motivo": "intervalo_invalido"}
        if ini_novo < fim_m and fim_novo > ini_m:
            return False, {"motivo": "sobreposicao_temporal"}

    gap = ini_novo - fim_ultimo
    if gap < 0:
        return False, {"motivo": "sobreposicao_temporal", "gap_s": round(gap, 3)}
    if gap > _COSTURA_GAP_S:
        return False, {"motivo": "gap_excessivo", "gap_s": round(gap, 3)}

    ponta_fim, ponta_ini = _ponta(ultimo, "fim"), _ponta(novo, "ini")
    if ponta_fim is None or ponta_ini is None:
        return False, {"motivo": "sem_geometria_pontas"}
    altura_media = max(1e-6, (ponta_fim[2] + ponta_ini[2]) / 2.0)
    distancia = (
        (ponta_fim[0] - ponta_ini[0]) ** 2
        + (ponta_fim[1] - ponta_ini[1]) ** 2
    ) ** 0.5 / altura_media
    if distancia > _COSTURA_DIST:
        return False, {
            "motivo": "geometria_incompativel",
            "distancia_alturas": round(distancia, 4),
        }
    salto_altura = abs(ponta_fim[2] - ponta_ini[2]) / max(
        1e-6, ponta_fim[2], ponta_ini[2]
    )
    if salto_altura > _COSTURA_ALTURA_TOL:
        return False, {
            "motivo": "altura_ponta_incompativel",
            "salto_altura": round(salto_altura, 4),
        }

    guardrails = []
    auxiliar_disponivel = auxiliar_favoravel = False
    for membro in grupo:
        g = _guardrails_identidade_segmento(membro, novo)
        guardrails.append({
            "track_id": int(_num(membro.get("pessoa_track_id")) or -1),
            **g,
        })
        if g["veto"]:
            return False, {"motivo": g["motivo"], "guardrails": guardrails}
        auxiliar_disponivel = auxiliar_disponivel or g["auxiliar_disponivel"]
        auxiliar_favoravel = auxiliar_favoravel or g["auxiliar_favoravel"]
    if auxiliar_disponivel and not auxiliar_favoravel:
        return False, {
            "motivo": "sem_evidencia_auxiliar_favoravel",
            "guardrails": guardrails,
        }

    return True, {
        "de_track": int(_num(ultimo.get("pessoa_track_id")) or -1),
        "para_track": int(_num(novo.get("pessoa_track_id")) or -1),
        "gap_s": round(gap, 3),
        "distancia_alturas": round(distancia, 4),
        "salto_altura": round(salto_altura, 4),
        "auxiliar_favoravel": auxiliar_favoravel,
        "guardrails": guardrails,
    }


def _agregar_identidade_logica(
    membros: list[dict], identidade: str, evidencias: list[dict],
) -> dict:
    membros_ord = sorted(membros, key=lambda d: (
        _num(d.get("t_ini_s")) or 0.0,
        int(_num(d.get("pessoa_track_id")) or -1),
    ))
    track_ids = sorted({
        int(_num(d.get("pessoa_track_id")) or -1) for d in membros_ord
    })
    representante = min(track_ids)
    referencia = max(
        membros_ord,
        key=lambda d: (
            _num(d.get("n_amostras")) or 0.0,
            -int(_num(d.get("pessoa_track_id")) or -1),
        ),
    )
    inicios = [_num(d.get("t_ini_s")) for d in membros_ord]
    fins = [_num(d.get("t_fim_s")) for d in membros_ord]
    inicios = [v for v in inicios if v is not None]
    fins = [v for v in fins if v is not None]
    return {
        "identidade_logica": identidade,
        "cam_id": str(referencia.get("cam_id") or "cam1"),
        "track_ids": track_ids,
        "track_representante": representante,
        # Compatibilidade direta com eleger_operador_segmento().
        "pessoa_track_id": representante,
        "tempo_posto_s": round(sum(
            _num(d.get("tempo_posto_s")) or 0.0 for d in membros_ord
        ), 1),
        "tempo_visivel_s": round(sum(
            _num(d.get("tempo_visivel_s")) or 0.0 for d in membros_ord
        ), 1),
        "n_amostras": sum(
            int(_num(d.get("n_amostras")) or 0) for d in membros_ord
        ),
        "n_amostras_posto": sum(
            int(_num(d.get("n_amostras_posto")) or 0) for d in membros_ord
        ),
        "t_ini_s": round(min(inicios), 3) if inicios else None,
        "t_fim_s": round(max(fins), 3) if fins else None,
        "bbox_ini": membros_ord[0].get("bbox_ini"),
        "bbox_fim": membros_ord[-1].get("bbox_fim"),
        "evidencias_costura": evidencias,
        "confianca_costura": (
            "nao_aplicavel" if len(membros_ord) == 1
            else "alta" if evidencias and all(
                bool(e.get("auxiliar_favoravel")) for e in evidencias
            ) else "continuidade_forte"
        ),
        # Aparência segue apenas como referência diagnóstica, nunca como voto
        # da 111B consolidada.
        "hist_sup": referencia.get("hist_sup"),
        "hist_inf": referencia.get("hist_inf"),
        "hist_bins": referencia.get("hist_bins"),
        "razoes": referencia.get("razoes"),
        "altura_rel": referencia.get("altura_rel"),
        "aspecto": referencia.get("aspecto"),
    }


def construir_identidades_logicas_segmento(descritores: list[dict]) -> list[dict]:
    """Costura residual cronológica, determinística e estritamente local."""
    por_camera: dict[str, list[dict]] = defaultdict(list)
    for d in descritores or []:
        if not isinstance(d, dict) or _num(d.get("pessoa_track_id")) is None:
            continue
        por_camera[str(d.get("cam_id") or "cam1")].append(d)

    identidades: list[dict] = []
    for camera in sorted(por_camera):
        fragmentos = sorted(por_camera[camera], key=lambda d: (
            _num(d.get("t_ini_s")) or 0.0,
            _num(d.get("t_fim_s")) or 0.0,
            int(_num(d.get("pessoa_track_id")) or -1),
        ))
        grupos: list[dict] = []
        pos = 0
        while pos < len(fragmentos):
            # Sucessores simultâneos são decididos juntos. Assim a mesma
            # identidade não pode escolher arbitrariamente o primeiro track
            # quando dois candidatos coexistentes passam pelos mesmos gates.
            primeiro = fragmentos[pos]
            fim_lote = _num(primeiro.get("t_fim_s"))
            lote = [primeiro]
            pos += 1
            if fim_lote is not None:
                while pos < len(fragmentos):
                    ini_proximo = _num(fragmentos[pos].get("t_ini_s"))
                    if ini_proximo is None or ini_proximo >= fim_lote:
                        break
                    proximo = fragmentos[pos]
                    lote.append(proximo)
                    fim_proximo = _num(proximo.get("t_fim_s"))
                    if fim_proximo is not None:
                        fim_lote = max(fim_lote, fim_proximo)
                    pos += 1

            por_grupo: dict[int, list[tuple[int, dict]]] = defaultdict(list)
            por_fragmento: dict[int, list[tuple[int, dict]]] = defaultdict(list)
            for indice_grupo, grupo in enumerate(grupos):
                for indice_fragmento, fragmento in enumerate(lote):
                    combina, evidencia = _avaliar_costura_identidade_segmento(
                        grupo["membros"], fragmento
                    )
                    if not combina:
                        continue
                    por_grupo[indice_grupo].append((indice_fragmento, evidencia))
                    por_fragmento[indice_fragmento].append((indice_grupo, evidencia))

            fundidos: set[int] = set()
            for indice_grupo in sorted(por_grupo):
                arestas = por_grupo[indice_grupo]
                if len(arestas) != 1:
                    continue
                indice_fragmento, evidencia = arestas[0]
                inversas = por_fragmento.get(indice_fragmento) or []
                if len(inversas) != 1:
                    continue
                grupos[indice_grupo]["membros"].append(lote[indice_fragmento])
                grupos[indice_grupo]["evidencias"].append(evidencia)
                fundidos.add(indice_fragmento)

            for indice_fragmento, fragmento in enumerate(lote):
                if indice_fragmento not in fundidos:
                    grupos.append({"membros": [fragmento], "evidencias": []})

        grupos.sort(key=lambda g: (
            min(_num(d.get("t_ini_s")) or 0.0 for d in g["membros"]),
            min(int(_num(d.get("pessoa_track_id")) or -1) for d in g["membros"]),
        ))
        for indice, grupo in enumerate(grupos, start=1):
            identidades.append(_agregar_identidade_logica(
                grupo["membros"], f"R{indice}", grupo["evidencias"]
            ))
    return identidades


def construir_timeline_identidade_segmento(
    observacoes: list[dict], identidade: dict, duracao_s: float,
) -> dict:
    """Relê a grade amostral em memória para uma identidade já eleita."""
    camera = str(identidade.get("cam_id") or "cam1")
    tracks_alvo = {int(t) for t in identidade.get("track_ids") or []}
    base = {
        "status": "indisponivel",
        "cam_id": camera,
        "identidade_logica": identidade.get("identidade_logica"),
        "track_ids": sorted(tracks_alvo),
        "resolucao": "amostral",
        "intervalos": [],
    }
    slots = sorted(
        (o for o in (observacoes or []) if str(o.get("cam_id") or "cam1") == camera),
        key=lambda o: _num(o.get("tempo_s")) or 0.0,
    )
    if not slots or not tracks_alvo:
        return base

    intervalos: list[dict] = []
    for i, slot in enumerate(slots):
        inicio = _num(slot.get("tempo_s")) or 0.0
        proximo = (
            _num(slots[i + 1].get("tempo_s"))
            if i + 1 < len(slots) else None
        )
        fim = proximo if proximo is not None and proximo >= inicio else max(
            inicio, float(duracao_s or 0.0)
        )
        tracks_slot = slot.get("tracks") or {}
        estados = set()
        observados = []
        for tid_raw, estado in tracks_slot.items():
            try:
                tid = int(tid_raw)
            except (TypeError, ValueError):
                continue
            if tid in tracks_alvo:
                estados.add(str(estado))
                observados.append(tid)

        if "dentro" in estados and "fora" in estados:
            estado, motivo, leitura = "nao_observado", "conflito_estado", "sem_inferencia"
        elif "dentro" in estados:
            estado, motivo, leitura = "no_posto", "identidade_observada", "seria_operador"
        elif "fora" in estados:
            estado = "fora_posto_candidato"
            motivo, leitura = "identidade_observada", "seria_operador_fora"
        else:
            estado, leitura = "nao_observado", "sem_inferencia"
            motivo = "identidade_nao_observada" if slot.get("medido") else "camera_nao_medida"

        atual = {
            "t_ini_s": round(inicio, 3),
            "t_fim_s": round(float(fim), 3),
            "estado": estado,
            "motivo": motivo,
            "leitura_shadow": leitura,
            "n_observacoes": 1,
            "track_ids_observados": sorted(set(observados)),
        }
        if (
            intervalos
            and intervalos[-1]["estado"] == atual["estado"]
            and intervalos[-1]["motivo"] == atual["motivo"]
            and abs(intervalos[-1]["t_fim_s"] - atual["t_ini_s"]) < 1e-6
        ):
            anterior = intervalos[-1]
            anterior["t_fim_s"] = atual["t_fim_s"]
            anterior["n_observacoes"] += 1
            anterior["track_ids_observados"] = sorted(set(
                anterior["track_ids_observados"] + atual["track_ids_observados"]
            ))
        else:
            intervalos.append(atual)
    return {**base, "status": "disponivel", "intervalos": intervalos}


def _frame_b64_para_bgr(frame_b64: str | None):
    """Decodifica somente o JPEG temporário em memória; nunca abre o vídeo."""
    if not frame_b64:
        return None
    try:
        bruto = base64.b64decode(frame_b64, validate=True)
        vetor = np.frombuffer(bruto, dtype=np.uint8)
        return cv2.imdecode(vetor, cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001 — o slot deve cair no legado
        return None


def _imagem_pessoas_identidade(
    frame, pessoas: list[dict], dim_original, *, rotulo_unico: str | None = None,
) -> str | None:
    """Anota bboxes já medidos no JPEG cru; não abre vídeo nem infere nada."""
    if frame is None:
        return None
    try:
        h_jpeg, w_jpeg = frame.shape[:2]
        w_orig, h_orig = dim_original or (w_jpeg, h_jpeg)
        sx = w_jpeg / max(1.0, float(w_orig))
        sy = h_jpeg / max(1.0, float(h_orig))
        anotadas = []
        for pessoa in pessoas or []:
            if not _bbox_valido(pessoa.get("bbox")):
                continue
            x1, y1, x2, y2 = pessoa["bbox"]
            anotadas.append({
                "bbox": (
                    int(round(float(x1) * sx)), int(round(float(y1) * sy)),
                    int(round(float(x2) * sx)), int(round(float(y2) * sy)),
                ),
                "rotulo": rotulo_unico or pessoa.get("rotulo") or "P?",
            })
        if not anotadas:
            return None
        return frame_para_base64(
            anotar_frame_com_ids(frame, anotadas), qualidade=70
        )
    except Exception:  # noqa: BLE001 — slot deve permanecer legado
        return None


def _imagem_fora_identidade(frame, detalhe: dict, dim_original) -> str | None:
    """Anota somente R1 no frame cru já decodificado em memória."""
    return _imagem_pessoas_identidade(
        frame, [detalhe], dim_original, rotulo_unico="OP"
    )


def _materializar_imagens_legadas_identidade(
    amostras: list[Amostra], dados_temporarios: dict, camera: str,
) -> None:
    """Converte JPEGs crus adiados antes de qualquer retorno pelo legado."""
    por_tempo = {
        round(float(t), 3): obs
        for obs in (dados_temporarios.get("observacoes") or [])
        if str(obs.get("cam_id") or "cam1") == camera
        and (t := _num(obs.get("tempo_s"))) is not None
    }
    for am in amostras or []:
        obs = por_tempo.get(round(float(am.tempo_s), 3))
        bruto = obs.get("frame_b64") if isinstance(obs, dict) else None
        if not bruto:
            continue
        # Testes/caminhos antigos podem já ter uma imagem anotada diferente.
        # Nesse caso basta soltar a cópia auxiliar; não há o que reconstruir.
        compartilhado_img = am.img_b64 == bruto
        compartilhado_fora = am.img_b64_fora == bruto
        if not compartilhado_img and not compartilhado_fora:
            obs["frame_b64"] = None
            continue
        frame = _frame_b64_para_bgr(bruto)
        if frame is None:
            # Mantém uma única representação na Amostra como fallback não-fatal.
            obs["frame_b64"] = None
            continue
        alvos = am.pessoas if am.pessoas else am.fora_posto
        destino_fora = not am.pessoas and bool(am.fora_posto)
        # Remove TODAS as referências comprimidas antes de criar a definitiva.
        obs["frame_b64"] = None
        am.img_b64 = ""
        am.img_b64_fora = None
        del bruto
        imagem = _imagem_pessoas_identidade(frame, alvos, am.dim) if alvos else None
        if destino_fora:
            am.img_b64_fora = imagem
        elif alvos:
            am.img_b64 = imagem or ""


def aplicar_identidade_logica_segmento(
    amostras: list[Amostra],
    resultados_identidade: list[dict],
    dados_temporarios: dict,
    cam_id_primaria: str,
) -> dict:
    """Aplica a 111D somente às Amostras seguras da câmera primária.

    Mapping e decodificação são validados antes da primeira mutação; a imagem
    final substitui a única string comprimida slot a slot. Slots ambíguos
    permanecem no caminho legado; R1 nunca chega a evento ou persistência.
    """
    base = {
        "status": "fallback_legado",
        "cam_id": str(cam_id_primaria or "cam1"),
        "identidade_logica": None,
        "track_ids": [],
        "reatribuicoes_dentro": 0,
        "reatribuicoes_fora": 0,
        "reatribuicoes_ausente": 0,
        "slots_fallback": len(amostras or []),
        "motivo": "configuracao_incompleta",
    }
    if not AUTORIDADE_111D_CONFIGURADA:
        return base

    camera = base["cam_id"]
    observacoes = {}
    for obs in dados_temporarios.get("observacoes") or []:
        if str(obs.get("cam_id") or "cam1") != camera:
            continue
        tempo = _num(obs.get("tempo_s"))
        if tempo is not None:
            observacoes[round(tempo, 3)] = obs

    def _fallback(motivo: str, **extras) -> dict:
        # Nenhum retorno da autoridade pode deixar o JPEG cru chegar ao VLM.
        _materializar_imagens_legadas_identidade(
            amostras, dados_temporarios, camera
        )
        return {**base, **extras, "motivo": motivo}

    resultado = next(
        (r for r in (resultados_identidade or [])
         if str(r.get("cam_id") or "cam1") == camera),
        None,
    )
    if not isinstance(resultado, dict):
        return _fallback("resultado_primario_ausente")
    decisao = resultado.get("decisao") or {}
    if decisao.get("status") != "confirmado":
        return _fallback(decisao.get("motivo") or "identidade_indefinida")
    timeline = resultado.get("timeline") or {}
    if timeline.get("status") != "disponivel":
        return _fallback("timeline_indisponivel")

    track_ids = {
        int(t) for t in (decisao.get("track_ids") or [])
        if _num(t) is not None
    }
    identidade_nome = decisao.get("identidade_logica")
    vencedora = next((i for i in (resultado.get("identidades") or []) if (
        i.get("identidade_logica") == identidade_nome
        and set(int(t) for t in (i.get("track_ids") or [])) == track_ids
    )), None)
    if not track_ids or not isinstance(vencedora, dict):
        return _fallback("mapping_vencedor_ausente")
    amostras_zona = int(_num(vencedora.get("n_amostras_posto")) or 0)

    # Planos prontos primeiro: se qualquer helper interno quebrar, nenhuma
    # Amostra terá sido parcialmente reatribuída.
    planos: list[tuple[Amostra, dict] | None] = []
    try:
        for am in amostras or []:
            # C3 é veto transitório da afirmação de ausência. Não tem track
            # físico para a autoridade lógica reatribuir e não pode ganhar
            # identidade/estado ao passar por esta etapa posterior.
            if (
                getattr(am, "presenca_safety_motivo", None)
                == "veto_posto_vazio_por_confianca_temporal"
            ):
                planos.append(None)
                continue
            obs = observacoes.get(round(float(am.tempo_s), 3))
            if not isinstance(obs, dict) or obs.get("medido") is not True:
                planos.append(None)
                continue
            tracks_slot = obs.get("tracks") or {}
            vistos: list[tuple[int, str]] = []
            for tid_raw, estado in tracks_slot.items():
                try:
                    tid = int(tid_raw)
                except (TypeError, ValueError):
                    continue
                if tid in track_ids and estado in {"dentro", "fora"}:
                    vistos.append((tid, str(estado)))
            # Dois fragmentos do mesmo R simultâneos ou estados divergentes
            # são conflito de mapping: fallback somente neste slot.
            if len(vistos) > 1:
                planos.append(None)
                continue

            if not vistos:
                planos.append((am, {
                    "estado": "ausente", "track_id": None, "obs": obs,
                }))
                continue

            tid, estado = vistos[0]
            if estado == "dentro":
                candidatos = [
                    p for p in am.pessoas if int(p.get("track_id")) == tid
                ]
                if len(candidatos) != 1:
                    planos.append(None)
                    continue
                planos.append((am, {
                    "estado": "dentro", "track_id": tid,
                    "pessoa": candidatos[0], "obs": obs,
                }))
                continue

            detalhes = obs.get("pessoas") or {}
            detalhe = detalhes.get(tid) or detalhes.get(str(tid))
            if (
                not isinstance(detalhe, dict)
                or not obs.get("frame_b64")
            ):
                planos.append(None)
                continue
            planos.append((am, {
                "estado": "fora", "track_id": tid,
                "detalhe": detalhe, "obs": obs,
            }))

        # Valida todos os JPEGs crus antes da primeira mutação. A segunda
        # decodificação, abaixo, continua sendo só de memória; nunca do vídeo.
        for item in planos:
            if item is None:
                continue
            am, plano = item
            bruto = plano["obs"].get("frame_b64")
            if bruto and _frame_b64_para_bgr(bruto) is None:
                raise ValueError(f"jpeg_temporario_invalido:{am.tempo_s}")
    except Exception:  # noqa: BLE001 — fallback transacional do segmento
        return _fallback(
            "erro_preparacao",
            identidade_logica=identidade_nome,
            track_ids=sorted(track_ids),
        )

    # Slots sem mapping voltam ao JPEG legado antes de qualquer reatribuição.
    _materializar_imagens_legadas_identidade(
        [am for am, item in zip(amostras or [], planos) if item is None],
        dados_temporarios,
        camera,
    )

    dentro = fora = ausente = fallback = 0
    for item in planos:
        if item is None:
            fallback += 1
            continue
        am, plano = item
        estado, tid = plano["estado"], plano.get("track_id")
        obs = plano["obs"]
        bruto = obs.get("frame_b64")
        teve_bruto = bool(bruto)
        imagem_final = None
        if teve_bruto:
            # A validação transacional acima já decodificou com sucesso. Agora
            # retiramos a ÚNICA string comprimida antes de criar a definitiva.
            frame = _frame_b64_para_bgr(bruto)
            legado_pessoas = list(am.pessoas)
            legado_fora = list(am.fora_posto)
            obs["frame_b64"] = None
            am.img_b64 = ""
            am.img_b64_fora = None
            del bruto
            try:
                if estado == "fora":
                    imagem_final = _imagem_fora_identidade(
                        frame, plano["detalhe"], obs.get("dim") or am.dim
                    )
                elif am.pessoas:
                    imagem_final = _imagem_pessoas_identidade(
                        frame, am.pessoas, obs.get("dim") or am.dim
                    )
            except Exception:  # noqa: BLE001 — fallback por slot
                imagem_final = None

            precisa_imagem = estado == "fora" or bool(am.pessoas)
            if precisa_imagem and not imagem_final:
                # O mesmo numpy já decodificado tenta restaurar o legado; não
                # reabre vídeo e não deixa mutação parcial de papel neste slot.
                alvos_legado = legado_pessoas or legado_fora
                imagem_legada = _imagem_pessoas_identidade(
                    frame, alvos_legado, obs.get("dim") or am.dim
                ) if alvos_legado else None
                if legado_pessoas:
                    am.img_b64 = imagem_legada or ""
                elif legado_fora:
                    am.img_b64_fora = imagem_legada
                fallback += 1
                continue

        # Limpa a decisão causal antiga somente nos slots realmente assumidos.
        am.fora_posto = []
        am.img_b64_fora = None
        am.fora_auditoria = None
        am.fora_auditoria_amostras_zona = None
        am.operador_ponte = False
        for pessoa in am.pessoas:
            pessoa["papel"] = (
                "operador"
                if estado == "dentro" and int(pessoa.get("track_id")) == tid
                else "visitante"
            )
        if estado == "dentro":
            if teve_bruto:
                am.img_b64 = imagem_final or ""
            am.operador_presente = True
            dentro += 1
        elif estado == "fora":
            detalhe = plano["detalhe"]
            pessoa_fora = {
                "track_id": int(tid),
                "bbox": tuple(int(v) for v in detalhe["bbox"]),
                "kpts": detalhe.get("kpts"),
                "rotulo": "OP",
                "frame_idx": am.frame_idx,
                "_fora_do_posto": True,
                "_fora_motivo": "operador",
                "_fora_amostras_zona": amostras_zona,
            }
            am.fora_posto = [pessoa_fora]
            am.img_b64_fora = imagem_final
            am.img_b64 = ""
            am.operador_presente = False
            fora += 1
        else:
            if teve_bruto and am.pessoas:
                am.img_b64 = imagem_final or ""
            am.operador_presente = False
            ausente += 1
        am.identidade_autoritativa = True
        am.identidade_estado = estado
        am.identidade_track_id = int(tid) if tid is not None else None

    # Defesa final: nenhum JPEG cru do coletor pode chegar ao VLM.
    for obs in dados_temporarios.get("observacoes") or []:
        if str(obs.get("cam_id") or "cam1") == camera:
            obs["frame_b64"] = None

    aplicados = dentro + fora + ausente
    return {
        **base,
        "status": "aplicado" if aplicados else "fallback_legado",
        "identidade_logica": identidade_nome,
        "track_ids": sorted(track_ids),
        "reatribuicoes_dentro": dentro,
        "reatribuicoes_fora": fora,
        "reatribuicoes_ausente": ausente,
        "slots_fallback": fallback,
        "motivo": "identidade_confirmada" if aplicados else "slots_sem_mapping",
    }


def _registrar_identidades_segmento_sombra(
    dados_shadow: dict, cameras: list[str], *, duracao_s: float,
) -> list[dict]:
    """Executa 111C após a 111B RAW; resultado vive apenas em log/memória."""
    if _OPERADOR_SEGMENTO_MODO not in {"sombra", "on"}:
        return []

    por_camera: dict[str, list[dict]] = defaultdict(list)
    for d in dados_shadow.get("descritores") or []:
        por_camera[str(d.get("cam_id") or "cam1")].append(d)
    saida: list[dict] = []
    for camera in dict.fromkeys(str(c or "cam1") for c in cameras):
        try:
            identidades = construir_identidades_logicas_segmento(
                por_camera.get(camera, [])
            )
            decisao = eleger_operador_segmento(identidades)
            vencedora = next((i for i in identidades if (
                decisao.get("status") == "confirmado"
                and i.get("pessoa_track_id") == decisao.get("track_id")
            )), None)
            decisao_logica = {
                "cam_id": camera,
                **decisao,
                "identidade_logica": (
                    vencedora.get("identidade_logica") if vencedora else None
                ),
                "track_ids": vencedora.get("track_ids", []) if vencedora else [],
            }
            timeline = (
                construir_timeline_identidade_segmento(
                    dados_shadow.get("observacoes") or [], vencedora, duracao_s
                )
                if vencedora else {
                    "status": "nao_gerada",
                    "cam_id": camera,
                    "motivo": "operador_logico_indefinido",
                    "intervalos": [],
                }
            )
            resumo_identidades = {
                "cam_id": camera,
                "identidades": [{
                    "identidade_logica": i["identidade_logica"],
                    "track_ids": i["track_ids"],
                    "tempo_posto_s": i["tempo_posto_s"],
                    "tempo_visivel_s": i["tempo_visivel_s"],
                    "confianca_costura": i["confianca_costura"],
                } for i in identidades],
            }
            if _OPERADOR_SEGMENTO_MODO == "sombra":
                log.info(
                    "[identidade-segmento] %s",
                    json.dumps(resumo_identidades, ensure_ascii=False, separators=(",", ":")),
                )
                log.info(
                    "[operador-segmento-logico] %s",
                    json.dumps(decisao_logica, ensure_ascii=False, separators=(",", ":")),
                )
                if vencedora:
                    log.info(
                        "[timeline-operador-segmento] %s",
                        json.dumps(timeline, ensure_ascii=False, separators=(",", ":")),
                    )
            saida.append({
                "cam_id": camera,
                "identidades": identidades,
                "decisao": decisao_logica,
                "timeline": timeline,
            })
        except Exception as exc:  # noqa: BLE001 — sombra nunca derruba produção
            log.warning("[identidade-segmento] cam=%s erro=%s", camera, exc)
    return saida


def agrupar_descritores(descritores: list) -> list[dict]:
    """Agrupa tracks da MESMA câmera que parecem a mesma pessoa.

    Fase 92 — TRÊS MUDANÇAS, na ordem em que o experimento pediu:

    1. COSTURA GEOMÉTRICA ANTES. Tracks fragmentados são costurados por
       continuidade (ver `costurar_tracks`) antes de qualquer comparação de
       aparência. É o que dá amostras suficientes para o histograma e para as
       razões corporais existirem.

    2. PISO DE AMOSTRAS. Track que, mesmo depois da costura, não chega a
       `KV_COSTURA_MIN_AMOSTRAS` fica FORA do agrupamento — vira grupo próprio,
       marcado `indefinido`. Com 1-2 amostras o histograma é ruído, e ruído
       encadeado por single-link foi o que transformou 222 dos 224 tracks da
       cam1 num grupo só.

    3. COMPLETE-LINK no lugar de single-link. Agora que os tracks são longos, a
       cadeia deixa de ser necessária e passa a ser o problema: em single-link
       basta A~B e B~C para juntar A e C mesmo que A e C não se pareçam em nada,
       e com 24,5% dos pares passando isso funde o dia inteiro. Complete-link
       exige que TODOS os membros se pareçam entre si.
    """
    grosso = costurar_tracks(descritores)
    aptos, curtos = [], []
    for d in grosso:
        n_am = int(_num(d.get("n_amostras")) or 0)
        (aptos if n_am >= _COSTURA_MIN_AMOSTRAS else curtos).append(d)

    # Complete-link aglomerativo: só funde dois grupos se TODO par entre eles
    # passar. O custo é O(n²) por rodada, e n aqui é dezenas — não centenas.
    clusters = [[d] for d in aptos]
    def _combina(ca, cb) -> bool:
        for a in ca:
            for b in cb:
                sim, motivo = _sim_descritores(a, b)
                # `None` é "não dá para dizer", não "são iguais": sem sinal
                # comparável, não funde.
                if sim is None or motivo != "ok":
                    return False
        return True

    fundiu = True
    while fundiu and len(clusters) > 1:
        fundiu = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                if _combina(clusters[i], clusters[j]):
                    clusters[i] = clusters[i] + clusters[j]
                    clusters.pop(j)
                    fundiu = True
                    break
            if fundiu:
                break

    por_raiz: dict = {}
    for k, membros in enumerate(clusters):
        por_raiz[k] = membros
    # Cada track curto vira grupo próprio: aparece na tela como o que é — um
    # pedaço solto que não deu para atribuir —, em vez de ser distribuído por
    # adivinhação entre os grupos grandes.
    for k, d in enumerate(curtos):
        por_raiz[f"curto{k}"] = [d]

    grupos = []
    for _k, membros in por_raiz.items():
        tempo_posto = sum(_num(m.get("tempo_posto_s")) or 0.0 for m in membros)
        tempo_vis = sum(_num(m.get("tempo_visivel_s")) or 0.0 for m in membros)
        # Recorte de referência: o track com MAIS tempo no posto é o que tem a
        # melhor chance de mostrar a pessoa inteira e nítida.
        ref = max(membros, key=lambda m: _num(m.get("tempo_posto_s")) or 0.0)
        grupos.append({
            "n_tracks": len(membros),
            "n_tracks_originais": sum(int(m.get("n_costurados") or 1) for m in membros),
            "n_amostras": sum(int(_num(m.get("n_amostras")) or 0) for m in membros),
            # Grupo abaixo do piso não é "outra pessoa": é pedaço solto.
            "indefinido": str(_k).startswith("curto"),
            # ASSINATURA = a cor do track de referência. É o que a verificação
            # de continuidade entre dias compara. Não é biometria: é a roupa,
            # e ela muda — por isso a divergência vira ALERTA, não correção.
            "assinatura": {"hist_sup": ref.get("hist_sup"),
                           "hist_inf": ref.get("hist_inf"),
                           "altura_rel": ref.get("altura_rel")},
            "tempo_posto_s": round(tempo_posto, 1),
            "tempo_visivel_s": round(tempo_vis, 1),
            "minutos_posto": round(tempo_posto / 60, 1),
            "altura_rel": _num(ref.get("altura_rel")),
            "referencia": {
                "descritor_id": ref.get("id"),
                "video_id": ref.get("video_id"),
                "pessoa_track_id": ref.get("pessoa_track_id"),
                "frame_ref": ref.get("frame_ref"),
                "bbox_ref": ref.get("bbox_ref"),
                "frame_w": ref.get("frame_w"), "frame_h": ref.get("frame_h"),
            },
            "tracks": sorted(
                ({"video_id": m.get("video_id"),
                  "pessoa_track_id": m.get("pessoa_track_id"),
                  "tempo_posto_s": _num(m.get("tempo_posto_s")) or 0.0,
                  "descritor_id": m.get("id")}
                 for m in membros),
                key=lambda t: -t["tempo_posto_s"],
            )[:60],
        })
    grupos.sort(key=lambda g: -g["tempo_posto_s"])
    for i, g in enumerate(grupos):
        # Rótulo POSICIONAL e do dia: `g1` de hoje não é `g1` de ontem. Não há
        # pessoa aqui, há "o grupo que mais ocupou o posto hoje".
        g["grupo"] = f"g{i + 1}"
    return grupos


def identificar_titular_do_dia(sb, empresa: str, processo: str, dia: str,
                               persistir: bool = True) -> dict:
    """Passe DIÁRIO: quem dominou o posto neste dia, por câmera. SOMBRA.

    Não toca em evento, não muda papel_pessoa, não entra em métrica nenhuma.
    Grava a atribuição para poder ser CONFERIDA a olho antes de valer.
    """
    try:
        date.fromisoformat(dia)
    except Exception:
        return {"erro": f"data inválida: {dia!r} (esperado AAAA-MM-DD)"}

    try:
        linhas = varrer(
            sb, "descritores_track",
            "id, video_id, pessoa_track_id, cam_id, gravado_em, n_amostras, "
            "n_amostras_posto, tempo_posto_s, tempo_visivel_s, papel_predominante, "
            "altura_rel, aspecto, razoes, hist_sup, hist_inf, hist_bins, "
            "bbox_ref, frame_ref, frame_w, frame_h",
            empresa=empresa, processo=processo,
        )
    except Exception as e:  # noqa: BLE001
        return {"erro": f"leitura de descritores falhou: {e}"}

    videos = varrer(sb, "videos", "id, nome, cam_id, processado_em",
                    empresa=empresa, processo=processo)
    dia_por_video = {}
    for v in videos:
        dt0 = _inicio_video_dt(v)
        if v.get("id") and dt0:
            dia_por_video[v["id"]] = dt0.date().isoformat()

    do_dia = [d for d in linhas if dia_por_video.get(d.get("video_id")) == dia]
    if not do_dia:
        return {"dia": dia, "cameras": [], "n_descritores": 0,
                "nota": "Nenhum descritor com gravação nesta data."}

    por_cam: dict = defaultdict(list)
    for d in do_dia:
        por_cam[d.get("cam_id") or "?"].append(d)

    saida_cams = []
    for cam, ds in sorted(por_cam.items()):
        grupos = agrupar_descritores(ds)
        total_posto = sum(g["tempo_posto_s"] for g in grupos)
        titular = None
        motivo = ""
        if grupos and total_posto > 0:
            top = grupos[0]
            pct = top["tempo_posto_s"] / total_posto * 100
            top["pct_do_posto"] = round(pct, 1)
            minutos = top["tempo_posto_s"] / 60
            # ── GUARDA DE PISO ──
            # Dominar é passar dos DOIS pisos. Num dia em que o titular faltou
            # e um terceiro usou o torno por 10 min, ele teria 100% de um total
            # minúsculo — e coroá-lo seria pior que não ter titular. Dia sem
            # dominante é dia INDEFINIDO, e isso é uma resposta.
            if pct < _TIT_PISO_PCT:
                motivo = (f"o grupo mais presente tem {pct:.0f}% do tempo no "
                          f"posto (piso {_TIT_PISO_PCT:.0f}%) — sem dominante")
            elif minutos < _TIT_PISO_MIN:
                motivo = (f"o grupo mais presente tem {minutos:.0f} min no posto "
                          f"(piso {_TIT_PISO_MIN:.0f} min) — presença insuficiente")
            else:
                titular = top["grupo"]
                motivo = f"{pct:.0f}% do tempo no posto, {minutos:.0f} min"
        for g in grupos:
            g["pct_do_posto"] = (round(g["tempo_posto_s"] / total_posto * 100, 1)
                                 if total_posto > 0 else 0.0)
            g["eh_titular"] = (g["grupo"] == titular)
        saida_cams.append({
            "cam_id": cam, "n_tracks": len(ds), "n_grupos": len(grupos),
            "minutos_posto_total": round(total_posto / 60, 1),
            "titular": titular, "motivo": motivo, "grupos": grupos,
        })

    rel = {
        "dia": dia, "empresa": empresa, "processo": processo,
        "n_descritores": len(do_dia),
        "cameras": saida_cams,
        "modo": "sombra",
        "limiares": {
            "KV_TITULAR_SIM_COR": _TIT_SIM_COR,
            "KV_TITULAR_RAZAO_MIN_N": _TIT_RAZAO_MIN_N,
            "KV_TITULAR_RAZAO_TOL": _TIT_RAZAO_TOL,
            "KV_TITULAR_ALTURA_TOL": _TIT_ALTURA_TOL,
            "KV_TITULAR_PISO_PCT": _TIT_PISO_PCT,
            "KV_TITULAR_PISO_MINUTOS": _TIT_PISO_MIN,
        },
        "nota": ("SOMBRA: nada aqui muda papel_pessoa, evento ou métrica. Os "
                 "rótulos de grupo são posicionais e valem para UM dia e UMA "
                 "câmera — não são pessoas, não há cadastro."),
    }
    rel["continuidade"] = _continuidade_titular(sb, empresa, processo, dia, saida_cams)
    if persistir:
        _gravar_titular_dia(sb, empresa, processo, dia, rel)
    return rel


def _continuidade_titular(sb, empresa: str, processo: str, dia: str,
                          cams: list) -> list:
    """O titular de hoje deveria PARECER o de ontem. Se não parecer, ALERTA —
    nunca correção automática.

    Mudança de titular pode ser troca de turno, férias, camisa nova ou erro do
    agrupamento, e nenhuma dessas o sistema consegue distinguir. Quem decide é
    gente — a mesma lição do `conversando_colega`: correção automática sobre
    sinal ambíguo espalha o erro em vez de corrigi-lo.
    """
    try:
        ontem = (date.fromisoformat(dia) - timedelta(days=1)).isoformat()
        ant = (sb.table("titular_dia").select("cam_id, assinatura, titular")
               .eq("empresa", empresa).eq("processo", processo)
               .eq("dia", ontem).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.debug("[titular] continuidade não avaliada (%s)", e)
        return []
    por_cam = {a.get("cam_id"): a for a in ant}
    alertas = []
    for c in cams:
        a = por_cam.get(c["cam_id"])
        if not a or not a.get("titular") or not c.get("titular"):
            continue
        hoje_ass = _assinatura_do_titular(c)
        sim = _sim_hist((a.get("assinatura") or {}).get("hist_sup"),
                        (hoje_ass or {}).get("hist_sup"))
        if sim is None:
            continue
        if sim < _TIT_SIM_COR:
            alertas.append({
                "cam_id": c["cam_id"], "similaridade": round(sim, 2),
                "ontem": ontem,
                "alerta": ("o titular de hoje não parece o de ontem "
                           f"(similaridade de cor {sim:.2f} < {_TIT_SIM_COR}). "
                           "Pode ser troca de turno, roupa diferente ou erro do "
                           "agrupamento — quem decide é você."),
            })
    return alertas


def _assinatura_do_titular(cam: dict) -> dict | None:
    for g in cam.get("grupos") or []:
        if g.get("eh_titular"):
            return g.get("assinatura")
    return None


def _gravar_titular_dia(sb, empresa: str, processo: str, dia: str, rel: dict) -> bool:
    """Grava a atribuição do dia. NÃO-FATAL: identificação é sombra, e sombra
    que derruba a coleta não é sombra."""
    try:
        for c in rel.get("cameras") or []:
            sb.table("titular_dia").upsert({
                "empresa": empresa, "processo": processo, "dia": dia,
                "cam_id": c["cam_id"], "titular": c.get("titular"),
                "motivo": c.get("motivo"),
                "n_grupos": c.get("n_grupos"), "n_tracks": c.get("n_tracks"),
                "minutos_posto_total": c.get("minutos_posto_total"),
                "grupos": c.get("grupos"),
                "assinatura": _assinatura_do_titular(c),
                "atualizado_em": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="empresa,processo,dia,cam_id").execute()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("[titular] não gravado (%s) — não-fatal.", e)
        return False


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
# Fase 90 — observação que COBRE o tempo sem ter olhado quadro novo. Ela é
# legítima (sem ela o minuto se parte e o denominador despenca) e não é
# evidência. A distinção entre "não olhei" e "olhei e não sei" mora aqui.
_ORIGENS_SEM_OLHAR = frozenset({
    "repeticao_pose", "repeticao_gate", "interpolado_sequencia",
    "indefinida_herdada", "ponte_temporal",
})


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
    # Fase 110 já preserva este caso sem atribuí-lo ao operador. A fila só dá
    # visibilidade para a revisão humana: não altera presença nem produtividade.
    if e.get("fora_do_posto") == "indeciso":
        return (True,
                "Pessoa fora do posto — não foi possível confirmar se era o operador titular.",
                "operador_fora_indeciso")
    # AUSÊNCIA DE EVIDÊNCIA vem primeiro e é EXCLUSIVA: com menos de duas
    # amostras não existe concordância a medir — falar em "amostras
    # discordantes" aqui seria mentira. São problemas diferentes: este se
    # resolve com mais evidência (amostrar mais denso), o outro com melhor
    # decisão (rótulo, prompt, camada).
    # Fase 98: sem descrição utilizável é ESTADO, e ele vai direto para a fila.
    # Vem antes da checagem de evidência porque a causa é outra: aqui houve
    # observação, o que faltou foi o modelo conseguir nomeá-la.
    if sem_descricao_utilizavel(e):
        return (True,
                "o modelo não conseguiu descrever o que estava acontecendo "
                "neste trecho — precisa de olho humano",
                "sem_descricao")
    n_am = e.get("n_amostras")
    if n_am is not None and int(n_am) < MIN_AMOSTRAS_EVIDENCIA:
        # Fase 90 — DUAS COISAS DIFERENTES, e misturá-las estraga a única
        # métrica que responde se o produto funciona:
        #   "olhei e não sei"  → dúvida real, resolve-se com melhor decisão
        #   "NÃO OLHEI"        → cobertura, resolve-se com mais amostragem
        # O segundo caso nasceu quando o gate passou a suprimir minutos
        # inteiros: o tempo continua coberto (a descrição é herdada da âncora),
        # mas nenhum quadro novo foi visto. Chamar isso de dúvida seria dizer
        # que o sistema ficou inseguro, quando ele só ficou barato.
        _org = e.get("observacoes_origem") or {}
        _herdadas = sum(v for k, v in _org.items()
                        if k in _ORIGENS_SEM_OLHAR)
        if _herdadas:
            return (True,
                    f"nenhum quadro novo foi analisado neste minuto — "
                    f"{_herdadas} observação(ões) herdada(s) do instante "
                    "anterior mantêm o tempo coberto, mas não são evidência",
                    "nao_observado")
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
            "n_rotulos_no_minuto", "rotulos_competindo", "fora_do_posto"]

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
    origem_de: Callable[..., str],
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
    # A categoria da árvore nasce da MESMA evidência visual do KPI. Só eventos
    # que fecham o contrato completo entram neste mapa; um label de cluster não
    # consegue fabricar ``valor_agregado``.
    _categoria_conversa_visual: dict[str, str] = {}
    for _e in eventos:
        _dec = decisao_conversa_evidenciada(_e)
        if _dec is None:
            continue
        _categoria_conversa_visual[_e["comportamento_label"]] = (
            "valor_agregado" if _dec[0] == "produtivo" else "desperdicio"
        )
    catalogo = dict(catalogo)
    for _lbl in _categoria_conversa_visual:
        if _lbl in _DESCRICOES_CONVERSA_VISUAL:
            catalogo.setdefault(_lbl, _DESCRICOES_CONVERSA_VISUAL[_lbl])
    # ⭐ Fase 110 — QUAIS RÓTULOS NASCERAM FORA DO POSTO. Um rótulo que só
    # aparece em eventos `operador_fora` descreve atividade que o sistema não
    # tem como julgar: "operando a ponte rolante" pode ser trabalho ou não, e
    # só o gestor sabe. Ele é marcado para o classificador de IA PULAR, e por
    # isso chega à árvore em "Sem classificação".
    #
    # A condição é `todos`, não `algum`: se o mesmo rótulo também aparece com o
    # operador DENTRO do posto, o sistema tem evidência para julgar e o
    # classificador segue valendo.
    _por_fora: dict[str, bool] = {}
    for _e in eventos:
        _lbl = _e.get("comportamento_label")
        if not _lbl:
            continue
        _eh_fora = _e.get("papel_pessoa") == PAPEL_OPERADOR_FORA
        _por_fora[_lbl] = _por_fora.get(_lbl, True) and _eh_fora
    for label, descricao in catalogo.items():
        n_neste_video = por_label.get(label, 0)
        if n_neste_video == 0:
            continue
        so_fora = bool(_por_fora.get(label))
        existente = (
            sb.table("comportamentos")
            .select("id, total_ocorrencias, categoria_lean, categoria_lean_origem")
            .eq("empresa", empresa)
            .eq("processo", processo)
            .eq("label", label)
            .execute()
        )
        if existente.data:
            atual = existente.data[0]
            _upd = {
                "total_ocorrencias": atual["total_ocorrencias"] + n_neste_video,
                "ultima_observacao": datetime.utcnow().isoformat(),
            }
            # Só LIGA a marca, nunca desliga: um rótulo que já exigiu decisão
            # humana não volta a ser candidato da IA porque um vídeo o trouxe
            # de dentro do posto.
            if so_fora:
                _upd["exige_decisao_humana"] = True
            _cat_visual = _categoria_conversa_visual.get(label)
            # O histórico ``conversando_colega`` não é reclassificado. Para a
            # tag nova de gestor/incerto, preserva uma eventual decisão humana.
            if (
                _cat_visual
                and label != LABEL_CONVERSANDO_COLEGA
                and atual.get("categoria_lean_origem") != "humano"
            ):
                _upd.update({
                    "categoria_lean": _cat_visual,
                    "categoria_lean_origem": "ia",
                })
            _upsert_comportamento(sb, atual["id"], _upd)
        else:
            _novo_comportamento = {
                "empresa": empresa,
                "processo": processo,
                "label": label,
                "descricao": descricao,
                "total_ocorrencias": n_neste_video,
                "exige_decisao_humana": so_fora,
            }
            if label in _categoria_conversa_visual:
                _novo_comportamento.update({
                    "categoria_lean": _categoria_conversa_visual[label],
                    "categoria_lean_origem": "ia",
                })
            _inserir_comportamento(sb, _novo_comportamento)

    # Fase 55 — HERANÇA NA INGESTÃO: se o comportamento já tem categoria, o
    # evento NASCE com ela (origem 'herdado'). Sem isto, todo vídeo novo
    # reacumula cinza que a propagação teria de limpar depois.
    # Rótulo sem categoria (ex.: `acao_indefinida`, que fica sem categoria de
    # propósito desde a Fase 49) simplesmente não entra no mapa — nunca é chutado.
    cat_ingestao: dict[str, str] = {}
    origem_cat_ingestao: dict[str, str | None] = {}
    try:
        _cats = varrer(
            sb, "comportamentos",
            "label, categoria_lean, categoria_lean_origem",
                       empresa=empresa, processo=processo)
        cat_ingestao = {c["label"]: c["categoria_lean"]
                        for c in _cats if c.get("categoria_lean")}
        origem_cat_ingestao = {
            c["label"]: c.get("categoria_lean_origem")
            for c in _cats if c.get("categoria_lean")
        }
    except Exception as e:
        log.warning(f"[lean] herança na ingestão indisponível (não-fatal): {e}")

    linhas_eventos: list[dict] = []
    n_auto_validados = 0
    for e in eventos:
        origem = origem_de(e["descricao_bruta"], e.get("maquina"), e.get("imovel"))
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
        # ⚠️ Fase 110 — `operador_fora` NÃO entra aqui, e é de propósito.
        # `posto_vazio` é determinístico e afirma AUSÊNCIA de atividade; o
        # fora-do-posto é uma descrição de VLM afirmando uma ATIVIDADE.
        # Auto-validá-lo colocaria uma afirmação de máquina dentro de
        # `validado_humano`, que é a base do dataset de 30 dias e do placar das
        # camadas. Ele vai para a fila como qualquer outra descrição.
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
            # Fase 85: com qual instrumento este número foi medido.
            "versao_instrumento": VERSAO_INSTRUMENTO,
            # Fase 88 — O DISCRIMINADOR VIRA COLUNA, e ela NÃO é verdade: é a
            # resposta crua do VLM, guardada para poder ser confrontada com o
            # movimento medido depois. Nenhum leitor de métrica a consome hoje,
            # e é de propósito — sem isso não há como medir a discordância que
            # a Fase 89 vai precisar, e foi a falta desta coluna que obrigou a
            # análise do discriminador a ser feita lendo string de rótulo.
            "cena_maquina": _normalizar_maquina(e.get("maquina")),
            "cena_imovel": (bool(e["imovel"]) if e.get("imovel") is not None else None),
            # Fase 89 — o que o SENSOR mediu, ao lado do que o VLM AFIRMOU.
            # Guardar os dois é o que torna a discordância mensurável; era a
            # falta exata disso que obrigou a análise do discriminador a ser
            # feita lendo string de rótulo.
            "movimento_maquina": e.get("movimento_maquina"),
            "movimento_detalhe": e.get("movimento_detalhe"),
            # Fase 94: a MEDIÇÃO e a COMPOSIÇÃO em campos separados. Colapsar
            # os dois num só impediria, daqui a duas semanas, responder "a
            # composição estava certa?" — que é exatamente a pergunta que não
            # pôde ser feita sobre o ciclo/parada do VLM.
            "modo_operacao": e.get("modo_operacao"),
            # Fase 95 — QUEM DECIDIU. Sem isto, "por que este minuto é
            # produtivo?" volta a não ter resposta, que é o problema original
            # com outra roupa. Gravado SEMPRE, mesmo com a flag desligada: é
            # assim que dá para comparar antes/depois no mesmo dado.
            "maos_maquina": e.get("maos_maquina"),
            "orientacao": e.get("orientacao"),
            # A NARRATIVA DO MINUTO (`KV_NARRATIVA`). Só é escrita quando existe
            # — assim o `insert` não carrega a chave em banco que ainda não tem
            # a coluna, e a fase pode subir antes do SQL rodar.
            **({"narrativa": e["narrativa"]} if e.get("narrativa") else {}),
            # A decisão binária já era produzida pelo VLM e atravessava a
            # consolidação, mas era descartada exatamente nesta fronteira.
            # Sem persistência, todo reload apagava a produtividade direta e
            # forçava o dashboard a voltar para rótulo/Lean.
            "trabalho": (
                e.get("trabalho") if PRODUTIVIDADE_OPERADOR_ESTRUTURADA else None
            ),
            "decidido_por": e.get("decidido_por"),
            # Fase 90: cobertura e composição, ao lado da evidência.
            "n_observacoes": e.get("n_observacoes"),
            "observacoes_origem": e.get("observacoes_origem"),
            # Fase 91: o que a LATERAL contou, e quantos ela viu que a cam1 não.
            "pessoas_posto_cam2": (e.get("_fato") or {}).get("pessoas_cam2_posto"),
            "pessoas_so_na_cam2": (e.get("_fato") or {}).get("pessoas_so_na_cam2"),
            # Fase 110 — a cam2 já CONTAVA a cena inteira (`n_cena_cam2`) e o
            # número morria na fronteira do insert. Fase 91 terminou pela
            # metade. ⚠️ É MÁXIMO SEM CASAMENTO ENTRE CÂMERAS: serve de
            # auditoria, NUNCA de teste de passante — ele não sabe dizer QUEM.
            "pessoas_cena_cam2": (e.get("_fato") or {}).get("pessoas_na_cena"),
            **({"fora_do_posto": e["fora_do_posto"]} if e.get("fora_do_posto") else {}),
            **({"fora_amostras_zona": e["fora_amostras_zona"]}
               if e.get("fora_amostras_zona") is not None else {}),
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
        # Fase 88: o RASTRO. Condicional só porque a chave não existe quando a
        # consolidação roda sem camadas — e é exatamente essa ausência que
        # passa a significar "o motor não rodou aqui", em vez de se confundir
        # com "rodou e nada disparou".
        if e.get("camadas_avaliadas") is not None:
            row["camadas_avaliadas"] = e["camadas_avaliadas"]
        _cat_h = cat_ingestao.get(e["comportamento_label"])
        _dec_visual = decisao_conversa_evidenciada(e)
        _cat_visual_evento = (
            "valor_agregado" if _dec_visual and _dec_visual[0] == "produtivo"
            else "desperdicio" if _dec_visual else None
        )
        if _cat_visual_evento:
            row["categoria_lean"] = _cat_visual_evento
            row["categoria_lean_origem"] = "ia"
        elif _cat_h:
            row["categoria_lean"] = _cat_h
            # Decisão humana do rótulo continua valendo nas ocorrências
            # futuras, mas a exceção P3 permanece estreita: só um
            # `operador_fora` recebe o carimbo que pode mover produtividade.
            row["categoria_lean_origem"] = (
                ORIGEM_HUMANO_ROTULO
                if e.get("papel_pessoa") == PAPEL_OPERADOR_FORA
                and origem_cat_ingestao.get(e["comportamento_label"]) == "humano"
                else "herdado"
            )
        if auto_validado:
            row["validacao_correto"] = True
            row["validado_em"] = datetime.utcnow().isoformat()
            n_auto_validados += 1
        linhas_eventos.append(row)

    # Fase 16: eventos crus só como AUDITORIA (principal=False) — não contam em
    # comportamentos/total_eventos nem viram sugestão de validação.
    for e in (eventos_auditoria or []):
        _dec_visual_aud = decisao_conversa_evidenciada(e)
        _cat_visual_aud = (
            "valor_agregado"
            if _dec_visual_aud and _dec_visual_aud[0] == "produtivo"
            else "desperdicio" if _dec_visual_aud else None
        )
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
            "maos_maquina": (
                e.get("maos_maquina") if PRODUTIVIDADE_OPERADOR_ESTRUTURADA else None
            ),
            "orientacao": (
                e.get("orientacao") if PRODUTIVIDADE_OPERADOR_ESTRUTURADA else None
            ),
            "trabalho": (
                e.get("trabalho") if PRODUTIVIDADE_OPERADOR_ESTRUTURADA else None
            ),
            "versao_instrumento": VERSAO_INSTRUMENTO,
            "n_amostras": e["n_amostras"], "confianca": e["confianca"],
            "n_observacoes": e.get("n_observacoes"),
            "observacoes_origem": e.get("origens") or None,
            **({"fora_do_posto": e["fora_do_posto"]}
               if e.get("fora_do_posto") else {}),
            **({"fora_amostras_zona": e["fora_amostras_zona"]}
               if e.get("fora_amostras_zona") is not None else {}),
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
            **({
                "categoria_lean": _cat_visual_aud,
                "categoria_lean_origem": "ia",
            } if _cat_visual_aud else {
                "categoria_lean": cat_ingestao[e["comportamento_label"]],
                "categoria_lean_origem": (
                    ORIGEM_HUMANO_ROTULO
                    if e.get("papel_pessoa") == PAPEL_OPERADOR_FORA
                    and origem_cat_ingestao.get(e["comportamento_label"]) == "humano"
                    else "herdado"
                ),
            } if cat_ingestao.get(e["comportamento_label"]) else {}),
        })

    CHUNK = 100
    inseridos: list[dict] = []
    for i in range(0, len(linhas_eventos), CHUNK):
        lote = linhas_eventos[i : i + CHUNK]
        removidas: set[str] = set()
        while True:
            try:
                resp = sb.table("eventos").insert(lote).execute()
                break
            except Exception as erro:   # noqa: BLE001
                # ANOTAÇÃO NÃO PODE DERRUBAR UM VÍDEO DA CAMPANHA: o vídeo
                # carrega presença e pose, que são o produto; estas colunas
                # apenas enriquecem a auditoria enquanto o SQL não foi rodado.
                # PostgREST pode revelar apenas UMA coluna desconhecida por
                # tentativa. Remover e repetir uma única vez ainda derrubava a
                # ingestão quando as três colunas da Fase 110 faltavam. O laço
                # é limitado pela tupla de opcionais e nunca engole outro erro.
                faltando = [
                    c for c in _COLUNAS_OPCIONAIS_EVENTO
                    if c not in removidas and c in str(erro)
                ]
                if not faltando:
                    raise
                removidas.update(faltando)
                log.warning(
                    "[eventos] coluna(s) %s não existe(m) neste banco — "
                    "gravando sem ela(s) (rode o schema.sql para tê-la(s)).",
                    ", ".join(faltando),
                )
                for _l in lote:
                    for _c in faltando:
                        _l.pop(_c, None)
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
def _tz_edge():
    """Fuso da FÁBRICA. Render roda em UTC, e usar o fuso do container
    deslocava toda a jornada três horas para a frente."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(os.environ.get("KV_TZ", "America/Sao_Paulo"))
    except Exception:   # noqa: BLE001
        # Sem tzdata na imagem, o offset explícito preserva o relógio da
        # instalação atual em vez de silenciosamente virar UTC.
        return timezone(timedelta(hours=-3), "UTC-3")


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
    """Extrai o relógio do nome do segmento → ISO 8601 no fuso da fábrica.

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
        return dt.replace(tzinfo=_tz_edge()).isoformat()
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

# ⭐ Fase 110 — A ORIGEM QUE FAZ A DECISÃO DO GESTOR CHEGAR AO NÚMERO.
#
# O bug que ninguém sabia que existia: classificar um rótulo na árvore NÃO
# mudava a produtividade do dashboard. A decisão chegava ao evento — mas com
# `categoria_lean_origem = "herdado"`, exatamente a mesma string que o
# classificador de IA escreve. Indistinguíveis. E `decidir_permanencia` ignora
# rótulo de propósito, então a categoria simplesmente não era consultada.
#
# `humano_rotulo` é a marca que só o clique de um humano produz. Nenhum VLM,
# nenhum cluster, nenhum classificador automático escreve esta string — e é
# por isso que ela pode ter poder de decisão sem reabrir o buraco do 41%→81%.
ORIGEM_HUMANO_ROTULO = "humano_rotulo"


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
# Fase 98 — `acao_indefinida` DEIXA DE SER RÓTULO. Vira ESTADO.
#
# "Ação indefinida" não é o que o operador estava fazendo. Como rótulo, ela
# aparecia na árvore e no Pareto como se fosse uma atividade — e no dia 10/08
# até saía PRODUTIVA, porque alguém teve de lhe dar uma categoria.
#
# ⚠️ MAS A ABSTENÇÃO CONTINUA EXISTINDO, e isso é o ponto: proibir o VLM de
# admitir que não soube o faria CHUTAR, e chute confiante é pior que dúvida
# declarada. Foi assim que `monitorar_maquina` virou depósito de tudo que ele
# não entendia.
#
# Então: o evento é marcado `sem_descricao_utilizavel`, vai direto para a
# fila, NÃO aparece na árvore nem no Pareto, e não decide produtividade
# (com a Fase 97 nenhum rótulo decide, então isto é reforço, não novidade).
#
# TAMANHO DO PROBLEMA, medido no dia 10/08: 1,0 min de 248,2 = 0,4% (1
# evento). Pequeno — o VLM está conseguindo ver. Se fosse grande, o conserto
# seria outro: o problema seria a visão, não o rótulo.
# ═════════════════════════════════════════════════════════════════════════
def sem_descricao_utilizavel(e: dict) -> bool:
    """True quando o evento está SEM RÓTULO. É ESTADO, não rótulo.

    Fase 100 — cobre os DOIS carimbos: `acao_indefinida` (histórico, quando o
    modelo escolhia um balde) e `nao_nomeado` (regime novo, quando o cluster
    não nomeia e o evento vai direto para a fila). Os dois significam a mesma
    coisa para quem lê: ninguém sabe ainda o que era.
    """
    lbl = e.get("label_corrigido") or e.get("comportamento_label")
    # Correção humana tira o evento deste estado: se alguém disse o que era,
    # passou a haver descrição utilizável.
    if e.get("label_corrigido"):
        return False
    return rotulo_e_ausencia(lbl)


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
# ═════════════════════════════════════════════════════════════════════════
# Escrita de `comportamentos` tolerante à coluna que ainda não existe.
#
# `exige_decisao_humana` é da Fase 110 e o SQL pode não ter rodado ainda. Sem
# esta rede, o primeiro vídeo depois do deploy quebraria na ingestão inteira —
# e perder o vídeo para salvar uma anotação é a troca errada. Mesmo padrão da
# `narrativa` (Fase 104) e do `impacto_pct` (Fase 105).
# ═════════════════════════════════════════════════════════════════════════
_COLUNAS_OPCIONAIS_COMPORTAMENTO = ("exige_decisao_humana",)
# Idem para `eventos`. Ordem irrelevante; o que importa é que TODAS sejam
# anuláveis — coluna NOT NULL não pode entrar aqui (ver a armadilha do
# `em_duvida`, que precisa ser escrita explicitamente em toda linha).
_COLUNAS_OPCIONAIS_EVENTO = ("narrativa", "fora_do_posto", "fora_amostras_zona",
                             "pessoas_cena_cam2")


def _sem_colunas_opcionais(linha: dict, erro: str) -> dict | None:
    """A linha sem a coluna que o banco recusou, ou None se o erro é outro."""
    faltando = [c for c in _COLUNAS_OPCIONAIS_COMPORTAMENTO if c in erro]
    if not faltando:
        return None
    log.warning("[comportamentos] coluna(s) %s não existe(m) neste banco — "
                "rode sql/schema.sql. Gravando sem ela(s).", ", ".join(faltando))
    return {k: v for k, v in linha.items() if k not in faltando}


def _inserir_comportamento(sb: Client, linha: dict) -> None:
    try:
        sb.table("comportamentos").insert(linha).execute()
    except Exception as e:   # noqa: BLE001
        sem = _sem_colunas_opcionais(linha, str(e))
        if sem is None:
            raise
        sb.table("comportamentos").insert(sem).execute()


def _upsert_comportamento(sb: Client, comportamento_id: str, campos: dict) -> None:
    try:
        sb.table("comportamentos").update(campos).eq("id", comportamento_id).execute()
    except Exception as e:   # noqa: BLE001
        sem = _sem_colunas_opcionais(campos, str(e))
        if sem is None:
            raise
        if sem:
            sb.table("comportamentos").update(sem).eq("id", comportamento_id).execute()


def propagar_categoria_para_eventos(
    sb: Client, empresa: str, processo: str, label: str, categoria: str | None,
    *, dry_run: bool = False, origem: str = "herdado",
) -> int:
    """Desce a categoria do comportamento para os eventos ELEGÍVEIS daquele
    (empresa, processo, label). Retorna quantos eventos foram (ou seriam)
    afetados. Não-fatal: falha aqui nunca derruba quem chamou.

    Casa pelo label EFETIVO — `label_corrigido` quando existe, senão
    `comportamento_label`. Filtrar só por `comportamento_label` deixaria de
    fora justamente os eventos que o gestor renomeou na validação, que são os
    que ele mais espera ver classificados.

    `categoria=None` NÃO limpa evento nenhum: liberar o comportamento para a IA
    reclassificar não pode apagar o que já foi herdado.

    ⭐ `origem` distingue QUEM decidiu. `herdado` (padrão) é a IA descendo a
    categoria; `humano_rotulo` é o gestor clicando na árvore. Antes as duas
    escreviam a mesma string, e por isso a decisão dele era indistinguível da
    automática — que é o motivo de classificar na árvore não mexer no número.
    """
    if not categoria or not label:
        return 0

    def _aplica(coluna_filtro: str, so_sem_correcao: bool) -> int:
        try:
            q = (
                sb.table("eventos")
                .select("id") if dry_run else sb.table("eventos").update(
                    {"categoria_lean": categoria, "categoria_lean_origem": origem}
                )
            )
            q = q.eq("empresa", empresa).eq("processo", processo)
            q = q.eq(coluna_filtro, label)
            if so_sem_correcao:
                q = q.is_("label_corrigido", "null")
            # PRECEDÊNCIA: NULL, já-herdado ou já-decidido-por-humano na árvore.
            # `humano_rotulo` entra para que uma NOVA decisão do gestor
            # sobrescreva a anterior; o caminho da IA nunca chama com essa
            # origem, então ele continua sem poder apagar decisão humana.
            q = q.or_("categoria_lean.is.null,categoria_lean_origem.eq.herdado,"
                      "categoria_lean_origem.eq." + ORIGEM_HUMANO_ROTULO)
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


def rotulos_sem_categoria(sb: Client, empresa: str, processo: str,
                          limite: int = 60) -> dict:
    """Fase 85 — SÓ LEITURA. Rótulos sem categoria Lean, do mais CARO para o
    mais barato em tempo acumulado.

    POR QUE ESTA TELA EXISTE, e por que ela entra JUNTO com a mudança do prompt.

    Rótulo novo nasce sem categoria. Desde a Fase 63 `categoria_efetiva()` nunca
    devolve None: sem categoria, o tempo conta como NÃO-PRODUTIVO. Isso é a
    convenção certa (sem prova de que agrega valor, não agrega), mas cria um
    efeito perverso no dia do deploy: se a mudança de prompt fizer nascer seis
    rótulos de uma vez, parte deles será de trabalho produtivo de verdade — e a
    produtividade cai por CONTABILIDADE antes de cair por MEDIÇÃO.

    Num dia de campanha as duas quedas são indistinguíveis no gráfico. A saída
    não é adiar a mudança: é conseguir classificar rápido, e começar pelo rótulo
    que representa mais tempo — porque é ele que move o número.

    Ordenado por SEGUNDOS, não por número de eventos: 4 eventos de 15 min pesam
    mais que 300 de 8 s, e é o peso que decide a ordem de trabalho.
    """
    def _fab_ev():
        return (
            sb.table("eventos")
            .select("id, comportamento_label, label_corrigido, descricao_bruta, "
                    "tempo_inicio_s, tempo_fim_s, principal, validacao_correto, "
                    "video_id, criado_em, versao_instrumento")
            .eq("empresa", empresa).eq("processo", processo).order("id")
        )
    try:
        eventos = _scan_todos(_fab_ev)
    except Exception as e:   # noqa: BLE001
        return {"erro": f"leitura de eventos falhou: {e}"}
    try:
        comps = varrer(sb, "comportamentos",
                       "id, label, descricao, categoria_lean, categoria_lean_origem, "
                       "total_ocorrencias",
                       empresa=empresa, processo=processo)
    except Exception as e:   # noqa: BLE001
        return {"erro": f"leitura de comportamentos falhou: {e}"}

    por_label = {c["label"]: c for c in comps}
    agg: dict[str, dict] = {}
    seg_total = 0.0
    for e in eventos:
        # Mesmo filtro de toda métrica: crus de auditoria e descartados fora.
        if e.get("principal") is False or e.get("validacao_correto") is False:
            continue
        lbl = e.get("label_corrigido") or e.get("comportamento_label")
        if not lbl:
            continue
        dur = max(0.0, float(e.get("tempo_fim_s") or 0) - float(e.get("tempo_inicio_s") or 0))
        seg_total += dur
        c = por_label.get(lbl) or {}
        if categoria_tem_evidencia(c.get("categoria_lean"), c.get("categoria_lean_origem")):
            continue
        a = agg.setdefault(lbl, {
            "label": lbl,
            # Fase 86: a FAMÍLIA é o que mantém a tendência legível. A soma da
            # família é comparável entre semanas — o histórico tem 100% dela sem
            # discriminador, hoje tem ciclo/parada/sem —, o que mudou foi a
            # RESOLUÇÃO com que sabemos decompor esse tempo, não o tempo.
            "familia": familia_label(lbl),
            "comportamento_id": c.get("id"),
            "descricao": c.get("descricao"),
            # Distingue "nunca foi classificado" de "foi ASSUMIDO pelo fallback".
            # São dois problemas: o primeiro espera decisão, o segundo esconde
            # uma decisão que a máquina tomou sozinha.
            "categoria_atual": c.get("categoria_lean"),
            "origem_atual": c.get("categoria_lean_origem"),
            "n_eventos": 0, "segundos": 0.0, "exemplos": [], "versoes": set(),
        })
        a["n_eventos"] += 1
        a["segundos"] += dur
        a["versoes"].add(int(e.get("versao_instrumento") or 1))
        d = (e.get("descricao_bruta") or "").strip()
        if d and len(a["exemplos"]) < 3 and d not in a["exemplos"]:
            a["exemplos"].append(d)

    # O total ANTES de mexer nos itens: `itens` são as MESMAS referências que
    # estão em `agg`, então limpar `segundos` lá apaga a chave aqui também.
    # Retrato da FAMÍLIA inteira, incluindo as variantes que já têm categoria.
    # Sem isto o gestor veria `monitorar_maquina_parada` sozinho e não saberia
    # que existe um `monitorar_maquina` histórico, produtivo, com 31% do tempo —
    # e são justamente os dois que ele precisa comparar para decidir.
    fam_total: dict[str, dict] = {}
    for e in eventos:
        if e.get("principal") is False or e.get("validacao_correto") is False:
            continue
        lbl = e.get("label_corrigido") or e.get("comportamento_label")
        if not lbl:
            continue
        c = por_label.get(lbl) or {}
        f = fam_total.setdefault(familia_label(lbl), {"segundos": 0.0, "variantes": {}})
        dur = max(0.0, float(e.get("tempo_fim_s") or 0) - float(e.get("tempo_inicio_s") or 0))
        f["segundos"] += dur
        v = f["variantes"].setdefault(lbl, {
            "label": lbl, "segundos": 0.0,
            "categoria": c.get("categoria_lean"),
            "origem": c.get("categoria_lean_origem"),
            "versoes": set(),
        })
        v["segundos"] += dur
        v["versoes"].add(int(e.get("versao_instrumento") or 1))

    seg_sem_cat = sum(v["segundos"] for v in agg.values())
    itens = sorted(agg.values(), key=lambda x: -x["segundos"])[:limite]
    for a in itens:
        a["minutos"] = round(a["segundos"] / 60, 1)
        a["pct_do_tempo"] = round(a["segundos"] / seg_total * 100, 1) if seg_total else 0.0
        a["versoes"] = sorted(a.pop("versoes"))
        fam = fam_total.get(a["familia"]) or {"segundos": 0.0, "variantes": {}}
        a["familia_minutos"] = round(fam["segundos"] / 60, 1)
        a["familia_variantes"] = sorted(
            ({"label": v["label"], "minutos": round(v["segundos"] / 60, 1),
              "categoria": v["categoria"], "origem": v["origem"],
              "versoes": sorted(v["versoes"])}
             for v in fam["variantes"].values()),
            key=lambda v: -v["minutos"],
        )
        a.pop("segundos", None)
    return {
        "itens": itens,
        "n_rotulos": len(agg),
        "minutos_sem_categoria": round(seg_sem_cat / 60, 1),
        "pct_sem_categoria": (round(seg_sem_cat / seg_total * 100, 1) if seg_total else 0.0),
        "minutos_observados": round(seg_total / 60, 1),
        "nota": ("Sem categoria, o tempo conta como NÃO-PRODUTIVO. Classificar do "
                 "topo para baixo é o que separa queda por medição de queda por "
                 "contabilidade."),
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


# Fase 97: com a permanência decidindo, classificar vocabulário deixou de ter
# efeito no número. O mecanismo NÃO é apagado — fica atrás de flag, desligado,
# porque ele ainda serve a quem quiser o Pareto por categoria.
_LEAN_AUTO = os.environ.get("KV_LEAN_AUTO", "off") not in (
    "off", "0", "false", "False", "")


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
    # ⚠️ Fase 97 — DESLIGADA POR PADRÃO. Com a permanência decidindo, a
    # categoria do rótulo não move número nenhum: classificar vocabulário
    # deixou de ser trabalho obrigatório do usuário, e a "queda por
    # contabilidade" quando nasce rótulo novo deixou de existir. O mecanismo
    # fica, para quem quiser o Pareto por categoria — mas não roda sozinho,
    # nem gasta chamada de IA, enquanto ninguém pedir.
    if not _LEAN_AUTO:
        log.info("[lean] classificação automática DESLIGADA (KV_LEAN_AUTO=off) "
                 "— a produtividade vem da permanência, não do rótulo.")
        return 0

    _COLS = ("id, label, descricao, categoria_lean, categoria_lean_origem, "
             "exige_decisao_humana")

    def _ler(cols: str):
        return (
            sb.table("comportamentos")
            .select(cols)
            .eq("empresa", empresa)
            .eq("processo", processo)
            .limit(500)
            .execute()
        ).data or []

    try:
        todos = _ler(_COLS)
    except Exception as e:
        if "exige_decisao_humana" not in str(e):
            log.warning(f"Lean: falha ao carregar comportamentos: {e}")
            return 0
        # Coluna ainda não existe: lê sem ela e AVISA que a guarda está inativa.
        # Silenciar isto faria o rótulo de fora do posto ser classificado pela
        # IA sem ninguém perceber — exatamente o que a Fase 110 impede.
        log.warning("[lean] coluna `exige_decisao_humana` não existe neste banco "
                    "— rode sql/schema.sql. A guarda do 'fora do posto' está "
                    "INATIVA nesta passada.")
        try:
            todos = _ler("id, label, descricao, categoria_lean, categoria_lean_origem")
        except Exception as e2:
            log.warning(f"Lean: falha ao carregar comportamentos: {e2}")
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
        # ⚠️ Fase 100 — COBRE OS DOIS CARIMBOS, e é CORRETIVO. Em 14/08 a linha
        # `acao_indefinida` estava em `valor_agregado` com origem `humano`
        # (alguém a classificou uma vez, na fila), e por herança 320 eventos do
        # dia contaram como PRODUTIVOS. Ausência de rótulo não pode agregar
        # valor: não há o que agregue. O `!=` abaixo é o que reverte isso
        # sozinho no próximo passe — inclusive por cima da origem humana, que é
        # a única exceção à inviolabilidade da marcação manual e existe porque
        # aqui não se está classificando uma atividade, e sim desfazendo a
        # classificação de uma não-atividade.
        # ⭐ Fase 110 — O RÓTULO QUE NASCEU FORA DO POSTO ESPERA UM HUMANO.
        #
        # Sem esta guarda, "operando a ponte rolante" cairia em `candidatos`
        # logo abaixo e a LLM carimbaria produtivo ou improdutivo com origem
        # `ia` — derrotando a decisão de produto: o sistema DESCREVE, o gestor
        # CLASSIFICA. A atividade fora do posto é justamente a que o sistema
        # não tem como julgar sozinho.
        #
        # A POSIÇÃO IMPORTA. Depois da regra do `posto_vazio` acima, que
        # continua forçando desperdício; antes de `rotulo_e_ausencia` abaixo,
        # que é CORRETIVA e tem de manter prioridade mesmo aqui.
        #
        # `and not categoria_lean` deixa a marca inerte assim que alguém
        # decide: decisão humana já é inviolável no topo do laço.
        if c.get("exige_decisao_humana") and not c.get("categoria_lean"):
            continue
        if rotulo_e_ausencia(c.get("label")):
            if (c.get("categoria_lean") != CATEGORIA_SEM_EVIDENCIA
                    or c.get("categoria_lean_origem") != ORIGEM_SEM_EVIDENCIA):
                try:
                    sb.table("comportamentos").update(
                        {"categoria_lean": CATEGORIA_SEM_EVIDENCIA,
                         "categoria_lean_origem": ORIGEM_SEM_EVIDENCIA}
                    ).eq("id", c["id"]).execute()
                    propagar_categoria_para_eventos(
                        sb, empresa, processo, c["label"], CATEGORIA_SEM_EVIDENCIA)
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

⭐ A REGRA QUE MANDA EM TODAS AS OUTRAS — 80/20:
O gestor está no chão de fábrica e cada pergunta interrompe o trabalho dele. Você tem direito a MUITO POUCAS perguntas por semana. Portanto: pergunte SÓ o que muda um NÚMERO ou uma CLASSIFICAÇÃO da análise. Se a resposta não reclassificaria tempo nem corrigiria um rótulo, a pergunta NÃO EXISTE.

O teste antes de escrever qualquer pergunta — ela precisa passar nos TRÊS:
  1. TEMPO: o comportamento envolvido ocupa uma fatia GRANDE do dia observado (veja os % abaixo). Pergunta sobre algo que ocupa 1% do dia é curiosidade, não análise. Só pergunte sobre o que está no topo da lista.
  2. DECISÃO: a resposta muda como você classifica dali em diante. "É produtivo ou é espera?" muda. "Como vocês chamam isso?" quase nunca muda.
  3. SÓ O CLIENTE SABE: a resposta não está na descrição do processo, no conhecimento já adquirido, nem é dedutível das imagens. Se dá para descobrir olhando mais vídeo, NÃO pergunte — descubra.

EXEMPLOS DO QUE NÃO PERGUNTAR (todos já foram perguntados e nenhum foi respondido):
  ✗ "Limpar cavaco costuma acontecer durante o ciclo automático ou depois?" — decide 0,3% do dia.
  ✗ "Vocês chamam essa etapa de X ou de Y?" — nome não reclassifica tempo.
  ✗ "O operador costuma conferir a peça?" — dá para ver no vídeo.
  ✓ "Quando o operador fica parado de frente pro torno enquanto ele corta, isso é trabalho dele ou é espera?" — decide 18% do dia e só o cliente sabe.

REGRAS DURAS:
- Você está LIVRE para perguntar o que quiser. NÃO use um catálogo pré-fabricado. As perguntas têm que nascer de incertezas REAIS nos dados abaixo.
- NÃO repita nenhuma pergunta que você já fez antes (lista mais abaixo) e NÃO crie variantes dela.
- NÃO pergunte o que a "descrição do processo" e o "conhecimento já adquirido" já respondem.
- Cada pergunta deve ser CURTA (1 frase), ESPECÍFICA e em linguagem de chão de fábrica. Nada de termos de IA, estatística ou Lean.
- Toda pergunta DEVE citar em "comportamentos_relacionados" os rótulos que ela decide, usando EXATAMENTE os nomes da lista de comportamentos abaixo. Pergunta sem rótulo, ou com rótulo inventado, é DESCARTADA automaticamente — ela não tem como provar que decide alguma coisa.
- Priorize perguntas que ajudam a:
    (a) Distinguir o que AGREGA VALOR do que NÃO agrega (preparação / verificação / espera / deslocamento) — este é o que mais muda número;
    (b) NOMEAR corretamente ações que ficaram sem nome ou de baixa confiança, quando elas ocupam tempo relevante;
    (c) DESAMBIGUAR comportamentos parecidos que somados pesam muito (mesmo objeto/ação descritos de formas diferentes; ou labels distintos que talvez sejam o mesmo);
    (d) Entender ORDEM e OBRIGATORIEDADE de passos, só quando isso muda a classificação.
- Gere NO MÁXIMO {max_perguntas} perguntas — e é um TETO, não uma meta. Devolver UMA pergunta ótima é melhor que {max_perguntas} razoáveis. Se não há lacuna que passe nos três testes, devolva uma lista VAZIA. NÃO invente perguntas para preencher cota.
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
    """Bloco em texto plano com os indícios mais úteis para a IA perguntar.

    ⭐ ORDENADO E CORTADO POR TEMPO. Antes este bloco despejava as 12 descrições
    mais frequentes, os 12 comportamentos, 8 indefinidos, 8 transições e 8
    entradas de vocabulário — uma superfície tão grande que convidava à
    trivialidade: era daqui que saía "limpar cavaco acontece durante o ciclo?",
    sobre um rótulo de 0,3% do dia. Frequência não é peso: uma descrição pode
    aparecer muito e durar nada.

    Agora o que vai para o prompt é o que OCUPA TEMPO, com o % ao lado de cada
    linha, e o que está abaixo do corte de impacto nem é oferecido. A IA não
    consegue perguntar o que não vê.
    """
    linhas: list[str] = []
    pct_label = pct_por_comportamento(contexto_agregado)

    # Catálogo de comportamentos — PRIMEIRO, porque é a lista de onde as
    # perguntas têm de citar rótulo, e ordenada por tempo.
    if contexto_agregado and contexto_agregado.get("distribuicao_comportamentos"):
        relevantes = [c for c in contexto_agregado["distribuicao_comportamentos"]
                      if float(c.get("pct_do_tempo_observado") or 0) >= PERG_IMPACTO_MIN]
        # Se NADA passa do corte, o processo está pulverizado em rótulos
        # pequenos: oferecemos os 5 maiores mesmo assim, senão a IA fica sem
        # vocabulário e inventa nomes — que o filtro descarta depois.
        if not relevantes:
            relevantes = contexto_agregado["distribuicao_comportamentos"][:5]
        linhas.append(
            "Comportamentos que OCUPAM TEMPO (use EXATAMENTE estes nomes em "
            "comportamentos_relacionados; os de baixo peso foram omitidos de "
            "propósito, não pergunte sobre eles):")
        for c in relevantes[:8]:
            linhas.append(
                f"  - {c['comportamento']} ({c.get('descricao','')}) — "
                f"{c.get('pct_do_tempo_observado', 0)}% DO TEMPO · "
                f"{c.get('ocorrencias_totais', 0)} ocorrências"
            )
        linhas.append("")

    # Descrições brutas — só as ligadas a rótulo que pesa. O texto cru ajuda a
    # IA a perceber ambiguidade, mas sem o vínculo com tempo ele é ruído.
    if observacoes_brutas:
        pesadas = [o for o in observacoes_brutas if o.get("descricao") and (
            not pct_label
            or _pct_do_label(str(o.get("label") or ""), pct_label) >= PERG_IMPACTO_MIN)]
        contagem = Counter(o["descricao"] for o in pesadas)
        if contagem:
            linhas.append("Descrições brutas dos comportamentos que pesam (texto cru do VLM, antes da clusterização):")
            for desc, n in contagem.most_common(8):
                linhas.append(f"  - {n}× \"{desc}\"")
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
        em_formacao = [
            v for v in em_formacao
            if not pct_label or _pct_do_label(str(v.get("label") or ""), pct_label) >= PERG_IMPACTO_MIN
        ]
        if em_formacao:
            linhas.append("Comportamentos com poucas confirmações ainda (vocabulário em formação) que OCUPAM TEMPO:")
            for v in em_formacao[:5]:
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


# ═════════════════════════════════════════════════════════════════════════
# ⭐ O 80/20 DAS PERGUNTAS — poucas, e só as que valem a interrupção.
#
# A fila chegou a 200 perguntas abertas e nenhuma respondida. A causa não era
# a IA perguntar mal: era ONDE e QUANTAS VEZES ela era chamada.
# `gerar_perguntas_processo` roda a cada VÍDEO PROCESSADO, com teto de 4. O
# runner da borda sobe dezenas de segmentos por dia — 4 × dezenas = centenas
# por semana. E o único freio era o dedupe por Jaccard, que só barra o texto
# quase igual: qualquer reformulação passava.
#
# Duas travas, nesta ordem:
#
#  1. ORÇAMENTO (determinístico, ANTES de gastar token). O teto que mais
#     importa é o de perguntas ABERTAS: com a fila cheia, o orçamento é ZERO e
#     nada novo nasce até o gestor trabalhar o que já está lá. Isso se
#     autorregula sozinho — sem cron, sem limpeza, sem alguém lembrando.
#     Os tetos por dia e por semana são o pedido literal ("não pode ultrapassar
#     de 10 por dia ou semana").
#
#  2. IMPACTO MEDIDO, não suposto. Uma pergunta vale o quanto de TEMPO
#     OBSERVADO ela decide. "Limpar cavaco acontece durante o ciclo?" sobre um
#     rótulo que ocupa 0,3% do dia não muda número nenhum — é curiosidade. A
#     mesma pergunta sobre um rótulo de 18% redesenha o Pareto.
#
#     ⚠️ E pergunta SEM comportamento relacionado, ou com comportamento que não
#     aparece na distribuição, tem impacto ZERO — não impacto desconhecido
#     promovido a alto. É o mesmo erro que já nos custou caro em outras quatro
#     ocasiões (bbox 0,0,0,0; MAD=0; share=1.00; acao_indefinida): AUSÊNCIA DE
#     MEDIDA VIRANDO MEDIDA. Aqui ela é barrada por desenho.
#
# As chaves existem para afrouxar em campo sem deploy, não para desligar a
# ideia: `KV_PERGUNTAS_MAX_ABERTAS`, `_MAX_DIA`, `_MAX_SEMANA`,
# `KV_PERGUNTA_IMPACTO_MIN`.
# ═════════════════════════════════════════════════════════════════════════
def _int_env(chave: str, padrao: int) -> int:
    try:
        return max(0, int(os.environ.get(chave, str(padrao)).strip()))
    except Exception:
        return padrao


PERG_MAX_ABERTAS = _int_env("KV_PERGUNTAS_MAX_ABERTAS", 3)
PERG_MAX_DIA = _int_env("KV_PERGUNTAS_MAX_DIA", 3)
PERG_MAX_SEMANA = _int_env("KV_PERGUNTAS_MAX_SEMANA", 10)
try:
    PERG_IMPACTO_MIN = float(os.environ.get("KV_PERGUNTA_IMPACTO_MIN", "5").strip())
except Exception:
    PERG_IMPACTO_MIN = 5.0

# Rótulos que significam "o sistema não soube nomear". Perguntar sobre eles é
# de alto valor por definição — é a pergunta que transforma tempo cego em
# tempo classificado —, então eles entram no cálculo de impacto pelo tempo que
# ocupam, como qualquer outro.
_RAIZES_SEM_NOME = ("indefinid", "nao_nomeado", "nao_identificad", "generic")


def _pct_do_label(label: str, pct_por_label: dict[str, float]) -> float:
    """% do tempo observado de um rótulo, tolerante à família.

    O cluster cria variantes (`monitorar_maquina`, `monitorar_maquina_ciclo`) e
    a pergunta costuma citar a raiz. Casar só exato jogaria para zero uma
    pergunta que decide a família inteira.
    """
    lbl = (label or "").strip().lower()
    if not lbl:
        return 0.0
    if lbl in pct_por_label:
        return pct_por_label[lbl]
    filhos = [p for k, p in pct_por_label.items() if k.startswith(lbl + "_")]
    if filhos:
        return sum(filhos)
    # A pergunta cita a variante e a distribuição tem a raiz.
    pais = [p for k, p in pct_por_label.items() if lbl.startswith(k + "_")]
    return max(pais) if pais else 0.0


def pct_por_comportamento(contexto_agregado: dict | None) -> dict[str, float]:
    """{label: % do tempo observado} a partir do contexto agregado."""
    saida: dict[str, float] = {}
    for c in ((contexto_agregado or {}).get("distribuicao_comportamentos") or []):
        lbl = str(c.get("comportamento") or "").strip().lower()
        if not lbl:
            continue
        try:
            saida[lbl] = max(saida.get(lbl, 0.0), float(c.get("pct_do_tempo_observado") or 0.0))
        except Exception:
            continue
    return saida


def impacto_da_pergunta(labels, pct_por_label: dict[str, float]) -> float:
    """% do tempo observado que a pergunta decide. SEM rótulo = 0.0.

    Soma (sem contar duas vezes) os rótulos citados. É o número que separa a
    pergunta que redesenha o Pareto da que só satisfaz curiosidade.
    """
    if not labels or not pct_por_label:
        return 0.0
    vistos: set[str] = set()
    total = 0.0
    for l in labels:
        lbl = str(l or "").strip().lower()
        if not lbl or lbl in vistos:
            continue
        vistos.add(lbl)
        total += _pct_do_label(lbl, pct_por_label)
    return round(min(100.0, total), 1)


def orcamento_de_perguntas(sb: Client, empresa: str, processo: str) -> dict:
    """Quantas perguntas PODEM nascer agora. Determinístico, antes do token.

    Devolve `{vagas, abertas, hoje, semana, motivo}`. `vagas == 0` é o caso
    NORMAL e esperado — a conversa rápida só volta a falar quando o gestor
    responde o que já está aberto.
    """
    try:
        linhas = varrer(sb, "perguntas_processo", "id, status, criada_em",
                        empresa=empresa, processo=processo)
    except Exception as e:
        # Sem conseguir contar, NÃO liberamos cota: um erro de leitura não pode
        # virar licença para inundar a fila de novo.
        log.warning(f"[perguntas] não deu para ler o orçamento ({e}) — cota zero.")
        return {"vagas": 0, "abertas": None, "hoje": None, "semana": None,
                "motivo": "não foi possível ler a fila de perguntas"}

    agora = datetime.now(timezone.utc)
    abertas = hoje = semana = 0
    for l in linhas:
        if (l.get("status") or "pendente") == "pendente":
            abertas += 1
        dt = _parse_iso_utc_pipe(l.get("criada_em"))
        if dt is None:
            continue
        idade = (agora - dt).total_seconds()
        if idade < 86400:
            hoje += 1
        if idade < 7 * 86400:
            semana += 1

    vagas = min(PERG_MAX_ABERTAS - abertas,
                PERG_MAX_DIA - hoje,
                PERG_MAX_SEMANA - semana)
    vagas = max(0, vagas)
    if vagas == 0:
        if abertas >= PERG_MAX_ABERTAS:
            motivo = (f"{abertas} pergunta(s) ainda sem resposta — o teto é "
                      f"{PERG_MAX_ABERTAS} abertas")
        elif hoje >= PERG_MAX_DIA:
            motivo = f"o teto de {PERG_MAX_DIA} pergunta(s) por dia já foi usado"
        else:
            motivo = f"o teto de {PERG_MAX_SEMANA} pergunta(s) por semana já foi usado"
    else:
        motivo = f"{vagas} vaga(s) — abertas {abertas}, hoje {hoje}, semana {semana}"
    return {"vagas": vagas, "abertas": abertas, "hoje": hoje, "semana": semana,
            "motivo": motivo}


def pct_por_label_do_processo(sb: Client, empresa: str, processo: str) -> dict[str, float]:
    """{label: % do tempo observado}, lido do banco.

    O mesmo número de `pct_por_comportamento`, mas sem depender do contexto
    agregado que só existe durante o processamento de um vídeo. É o que permite
    medir o impacto de perguntas que JÁ estão na fila — inclusive as 200 que
    nasceram antes de existir filtro.
    """
    try:
        evs = varrer(sb, "eventos",
                     "comportamento_label, label_corrigido, tempo_inicio_s, "
                     "tempo_fim_s, validacao_correto, principal",
                     empresa=empresa, processo=processo)
    except Exception as e:
        log.warning(f"[perguntas] não deu para medir tempo por rótulo: {e}")
        return {}
    dur: dict[str, float] = {}
    for e in evs:
        if e.get("validacao_correto") is False or e.get("principal") is False:
            continue
        lbl = (_label_efetivo(e) or "").strip().lower()
        if not lbl:
            continue
        d = (e.get("tempo_fim_s") or 0) - (e.get("tempo_inicio_s") or 0)
        if d > 0:
            dur[lbl] = dur.get(lbl, 0.0) + float(d)
    total = sum(dur.values())
    if total <= 0:
        return {}
    return {k: round(v / total * 100, 1) for k, v in dur.items()}


def priorizar_perguntas_abertas(sb: Client, empresa: str, processo: str,
                                *, topo: int | None = None) -> dict:
    """As perguntas ABERTAS ordenadas pelo impacto MEDIDO agora.

    A fila de 200 nasceu sem nenhuma noção de peso e é servida por ordem de
    criação — por isso o topo dela é trivialidade. Aqui cada pergunta é
    reavaliada contra o tempo que os rótulos dela ocupam HOJE, e o que fica
    abaixo do corte é separado (não apagado: `abaixo_do_corte` só descreve).
    """
    try:
        linhas = varrer(sb, "perguntas_processo",
                        "id, pergunta, motivo, comportamentos_relacionados, "
                        "respostas_rapidas, status, resposta, respondida_em, "
                        "criada_em, impacto_pct",
                        empresa=empresa, processo=processo)
    except Exception:
        # Banco ainda sem `impacto_pct`: lê sem ela e mede tudo agora.
        linhas = varrer(sb, "perguntas_processo",
                        "id, pergunta, motivo, comportamentos_relacionados, "
                        "respostas_rapidas, status, resposta, respondida_em, "
                        "criada_em",
                        empresa=empresa, processo=processo)
    abertas = [l for l in linhas if (l.get("status") or "pendente") == "pendente"]
    pct = pct_por_label_do_processo(sb, empresa, processo)
    for l in abertas:
        rel = l.get("comportamentos_relacionados")
        medido = impacto_da_pergunta(rel if isinstance(rel, list) else [], pct)
        # O impacto gravado no nascimento e o medido agora podem divergir (o
        # Pareto muda). Vale o MAIOR: uma pergunta que já foi importante não
        # vira trivial só porque o rótulo dela encolheu esta semana.
        gravado = l.get("impacto_pct")
        try:
            gravado = float(gravado) if gravado is not None else 0.0
        except Exception:
            gravado = 0.0
        l["impacto_pct"] = round(max(medido, gravado), 1)
    abertas.sort(key=lambda l: (-(l["impacto_pct"] or 0.0),
                                str(l.get("criada_em") or "")))
    vale = [l for l in abertas if (l["impacto_pct"] or 0.0) >= PERG_IMPACTO_MIN]
    fraco = [l for l in abertas if (l["impacto_pct"] or 0.0) < PERG_IMPACTO_MIN]
    if topo is not None:
        vale = vale[:topo]
    return {"abertas": len(abertas), "valem": vale, "abaixo_do_corte": fraco,
            "corte_pct": PERG_IMPACTO_MIN, "medidas": len(pct)}


def arquivar_perguntas_de_baixo_impacto(sb: Client, empresa: str, processo: str,
                                        *, manter: int = PERG_MAX_ABERTAS) -> dict:
    """Marca como `dispensada` toda pergunta aberta abaixo do corte de impacto,
    guardando as `manter` de maior impacto.

    NÃO APAGA NADA: `dispensada` é um estado, o texto e a resposta continuam no
    banco e o gestor pode reabrir pelo SQL. É uma ação EXPLÍCITA — nada aqui
    roda sozinho, porque limpar a fila de alguém sem pedir é decidir por ele.
    """
    r = priorizar_perguntas_abertas(sb, empresa, processo)
    guardar = {l["id"] for l in r["valem"][:manter]}
    alvo = [l for l in (r["valem"] + r["abaixo_do_corte"]) if l["id"] not in guardar]
    ids = [l["id"] for l in alvo if l.get("id")]
    n = 0
    for i in range(0, len(ids), 100):
        lote = ids[i: i + 100]
        try:
            sb.table("perguntas_processo").update({"status": "dispensada"}) \
              .in_("id", lote).execute()
            n += len(lote)
        except Exception as e:
            log.warning(f"[perguntas] falha ao arquivar lote: {e}")
    log.info(f"[perguntas] {n} arquivada(s); {len(guardar)} mantida(s) em "
             f"{empresa}/{processo}.")
    return {"arquivadas": n, "mantidas": len(guardar),
            "mantidas_texto": [l["pergunta"] for l in r["valem"][:manter]],
            "corte_pct": PERG_IMPACTO_MIN}


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

    ⭐ ORÇAMENTO PRIMEIRO. Esta função roda a cada vídeo processado, e o runner
    da borda sobe dezenas por dia. Sem a trava de cota ela sozinha explica a
    fila de 200 perguntas abertas. A cota é lida ANTES da chamada ao Groq — sem
    vaga, nem o token é gasto.
    """
    orc = orcamento_de_perguntas(sb, empresa, processo)
    if orc["vagas"] <= 0:
        log.info(f"[perguntas] nada perguntado: {orc['motivo']}.")
        return []
    max_perguntas = min(max_perguntas, orc["vagas"])

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
    pct_label = pct_por_comportamento(contexto_agregado)
    # ⭐ Não cortamos em `max_perguntas` aqui: primeiro medimos TODAS as
    # candidatas por impacto e ordenamos. Cortar antes de medir seria entregar
    # as primeiras que a LLM escreveu, não as que mais decidem — que é
    # exatamente o oposto do 80/20.
    for p in cruas:
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
        # ⭐ O 80/20: quanto do tempo observado esta pergunta decide.
        # Sem rótulo relacionado o impacto é ZERO, não "desconhecido" —
        # ausência de medida não vira medida.
        impacto = impacto_da_pergunta(comp_rel, pct_label)
        if impacto < PERG_IMPACTO_MIN:
            log.info(f"[perguntas] descartada por baixo impacto "
                     f"({impacto}% < {PERG_IMPACTO_MIN}%): {texto[:60]}…")
            continue
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
                # Fica GRAVADO: é o que ordena a fila na tela e o que permite
                # conferir depois se o filtro está calibrado.
                "impacto_pct": impacto,
            }
        )
        tokens_acc.append(_normalizar_pergunta(texto))

    # Maior impacto primeiro, e só então o corte pela cota.
    novas_linhas.sort(key=lambda x: -(x.get("impacto_pct") or 0.0))
    if len(novas_linhas) > max_perguntas:
        log.info(f"[perguntas] {len(novas_linhas)} candidatas passaram no filtro; "
                 f"guardando as {max_perguntas} de maior impacto.")
        novas_linhas = novas_linhas[:max_perguntas]

    if not novas_linhas:
        log.info("Nenhuma pergunta nova após dedupe/impacto.")
        return []

    try:
        r = sb.table("perguntas_processo").insert(novas_linhas).execute()
        salvas = r.data or novas_linhas
        log.info(f"{len(salvas)} pergunta(s) proativa(s) persistida(s) em {empresa}/{processo} "
                 f"(impacto {[l['impacto_pct'] for l in novas_linhas]}%).")
        return salvas
    except Exception as e:
        # Banco ainda sem a coluna nova: a pergunta é mais importante que a
        # anotação dela. Grava sem o impacto em vez de perder a rodada — e diz
        # em voz alta qual SQL falta.
        if "impacto_pct" in str(e):
            log.warning("[perguntas] coluna `impacto_pct` não existe neste banco — "
                        "rode sql/schema.sql. Gravando sem ela.")
            try:
                sem = [{k: v for k, v in l.items() if k != "impacto_pct"}
                       for l in novas_linhas]
                r = sb.table("perguntas_processo").insert(sem).execute()
                return r.data or sem
            except Exception as e2:
                log.warning(f"Falha ao persistir perguntas: {e2}")
                return []
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
        # Fase 98/100: rótulo de AUSÊNCIA fica FORA da árvore e do Pareto —
        # `acao_indefinida` (histórico) e `nao_nomeado` (novo). Não são
        # atividades; são a ausência de uma. O tempo continua no denominador
        # (foi observado), mas não vira folha nem barra.
        if rotulo_e_ausencia(l):
            continue
        # ⚠️ RÓTULO SEM TEMPO MEDIDO NÃO É ATIVIDADE DO POSTO. Ele chegava à
        # tela como "Lendo o desenho técnico — 0%", ao lado de atividades
        # reais, e o cliente lia isso como "o sistema mediu e deu zero" quando
        # o que houve foi NÃO TER MEDIDO NADA. É a mesma ausência-de-medida-
        # virando-medida das Fases 82, 84, 97, 100 e 102 — a sexta aparição.
        # Não altera conta nenhuma: `total_atividade` já foi somado acima e
        # estes itens contribuem com zero.
        if round(a["dur"], 1) <= 0:
            continue
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
                # `papel_pessoa` entra no scan que JÁ EXISTE: é o que permite o
                # card da home mostrar presença sem uma consulta a mais.
                "origem_validacao, confianca, principal, papel_pessoa"
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
        # PRESENÇA por processo, pela mesma regra do dashboard: quem não é
        # posto_vazio nem visitante estava no posto. Determinístico, sem IA.
        _dur = max(0.0, float(e.get("tempo_fim_s") or 0)
                   - float(e.get("tempo_inicio_s") or 0))
        if _dur > 0:
            p["_presenca_total_s"] = p.get("_presenca_total_s", 0.0) + _dur
            _lbl = e.get("label_corrigido") or e.get("comportamento_label")
            # Fase 110: `operador_fora` entra aqui. Sem esta linha o card de
            # processo contaria como PRESENTE no posto o tempo em que ele está
            # fora dele — inflando exatamente o número de que o cliente
            # reclamou.
            _fora = (e.get("papel_pessoa") in ("posto_vazio", "visitante",
                                               PAPEL_OPERADOR_FORA)
                     or (_lbl == POSTO_VAZIO_LABEL and not e.get("label_corrigido")))
            if not _fora:
                p["_no_posto_s"] = p.get("_no_posto_s", 0.0) + _dur

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
            # PRESENÇA no card da home: o mesmo número do dashboard, pela mesma
            # regra determinística. `None` quando não há tempo observado — a
            # tela diz isso em vez de mostrar zero como se fosse resultado.
            "presenca_pct": (
                round(100.0 * p.get("_no_posto_s", 0.0) / p["_presenca_total_s"], 1)
                if p.get("_presenca_total_s") else None
            ),
            "posto_vazio_pct": (
                round(100.0 * (p["_presenca_total_s"] - p.get("_no_posto_s", 0.0))
                      / p["_presenca_total_s"], 1)
                if p.get("_presenca_total_s") else None
            ),
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
    """Instante REAL do trecho, SEMPRE no relógio da fábrica.

    ⚠️ DUAS CORREÇÕES, e as duas vinham do mesmo lugar: a jornada aparecia das
    6h às 18h quando a captura real ia das 3h às 15h20. Exatamente três horas
    à frente.

    (1) O FUSO. `gravado_em` é `timestamptz` e o PostgREST o devolve em UTC.
        Quem fazia `dt.hour` lia a hora UTC e desenhava a barra três colunas
        adiante. Render roda em UTC, então nem o fuso do container salvava.
        Agora a conversão é explícita e acontece AQUI, num lugar só — todo
        gráfico que pergunta "que horas foi isto" recebe a hora de parede.

    (2) A ORDEM DAS FONTES. O NOME DO SEGMENTO vem primeiro, e é o pedido do
        dono. Ele é o carimbo que a borda escreveu no instante da gravação —
        já em hora local, imune a reinterpretação de fuso no banco e imune a
        um `gravado_em` preenchido com a hora do upload. `gravado_em` vira
        reserva; `processado_em` (quando o vídeo foi PROCESSADO, que não tem
        relação com quando a cena aconteceu) fica por último e só existe para
        o vídeo não sumir do dia.
    """
    iso = (
        _parse_gravado_em_nome(v.get("nome"))
        or v.get("gravado_em")
        or v.get("processado_em")
    )
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except Exception:   # noqa: BLE001
        return None
    # Carimbo sem fuso é hora de parede da fábrica — é assim que a borda grava.
    return dt.replace(tzinfo=_tz_edge()) if dt.tzinfo is None else dt.astimezone(_tz_edge())


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


_FRENTE_CACHE: dict = {}


def frente_maquina_do_processo(sb, empresa: str, processo: str) -> str | None:
    """Como esta câmera traduz 'de frente para a CÂMERA' em 'de frente para o
    TORNO'. Lido UMA vez por processo e memorizado — é configuração fixa.

    None sem configuração, e aí o nível 2 não afirma nada: a orientação em
    relação à máquina é indedutível sem saber onde a máquina está.
    """
    ch = (empresa, processo)
    if ch in _FRENTE_CACHE:
        return _FRENTE_CACHE[ch]
    v = None
    try:
        for z in (sb.table("zonas_camera")
                  .select("papel, frente_maquina, cam_id")
                  .eq("empresa", empresa).eq("processo", processo)
                  .execute().data or []):
            if z.get("papel") == "maquina" and z.get("frente_maquina"):
                v = z["frente_maquina"]
                break
    except Exception as e:  # noqa: BLE001
        log.debug("[permanencia] frente_maquina não lida (%s)", e)
    _FRENTE_CACHE[ch] = v
    return v


def carimbar_frente(eventos: list, frente: str | None) -> list:
    """Cola a configuração da câmera em cada evento, para `_cat_do_evento`
    poder decidir sem ter que consultar o banco por evento."""
    for e in eventos:
        e["_frente_maquina"] = frente
    return eventos


# ⚠️ CONTRATO: com KV_PERMANENCIA ligado, quem varre `eventos` precisa chamar
# `carimbar_frente(eventos, frente_maquina_do_processo(...))` ANTES de passar
# por aqui. Sem o carimbo, `_frente_maquina` vem None e o nível 2 não afirma —
# degradação segura (nada vira produtivo por engano), mas o número fica baixo.
def evento_conta_no_vocabulario(e: dict) -> bool:
    """Fase 98: o que entra na ÁRVORE e no PARETO. `acao_indefinida` fica de
    fora — ela não é uma atividade, é a ausência de uma."""
    return not sem_descricao_utilizavel(e)


def _cat_do_evento(e: dict, cat_por_label: dict) -> tuple[str, str, float]:
    """(label efetivo, categoria lean, duração) de um evento principal.

    Fase 95: com `KV_ARVORE_DECIDE` ligado, quem decide a categoria é a ÁRVORE
    (sinal determinístico primeiro, rótulo por último). Desligado, devolve
    exatamente o de antes — este é o ponto único por onde toda métrica passa, e
    é por isso que a inversão cabe numa flag só."""
    label = e.get("label_corrigido") or e.get("comportamento_label") or "?"
    dur = max(0.0, float(e.get("tempo_fim_s") or 0) - float(e.get("tempo_inicio_s") or 0))
    if _PERMANENCIA:
        # ⚠️ Fase 97 — NENHUM RÓTULO ENTRA NA DECISÃO. `cat_por_label` só é
        # consultado no caminho de correção humana (onde a decisão é dela, não
        # do rótulo). É isto que faz rótulo novo não mexer em número nenhum, e
        # é o que acaba com a "queda por contabilidade".
        e2 = dict(e)
        # A categoria do rótulo entra APENAS no caminho humano — é a decisão
        # dela sobre aquele rótulo, não o rótulo decidindo sozinho.
        e2["_cat_humana"] = cat_por_label.get(label)
        cat, _niv, _mot, _est = decidir_permanencia(e2, e.get("_frente_maquina"))
        return label, cat, dur
    _label, cat, dur, _nivel, _cand = _cat_com_arvore(e, cat_por_label)
    return label, cat, dur



# ═════════════════════════════════════════════════════════════════════════
# Fase 95 — A ÁRVORE DECIDE. ATRÁS DE FLAG.
#
# O PROBLEMA, e ele é o teto de 75-80%: a produtividade vinha do NOME que o
# VLM dá à ação, e quase todo nome que ele dá é produtivo. Não era medição —
# era o único resultado possível. Um instrumento que só pode dizer "sim" não
# está medindo nada.
#
# A INVERSÃO: quem decide passa a ser o sinal determinístico, e o rótulo vira
# o ÚLTIMO recurso em vez do primeiro.
#
#   1. operador presente?   zonas + tracking   não → IMPRODUTIVO (posto vazio)
#   2. máquina em movimento? sensor 6 fps      sim → PRODUTIVO
#   3. operação manual?      pose + oclusão    sim → PRODUTIVO
#   4. nada disso                              → o rótulo decide (como hoje)
#
# ⚠️ PRECEDÊNCIA, de cima para baixo, e ela é absoluta:
#      correção HUMANA  >  sinal determinístico  >  rótulo do VLM
#   Se o sensor vê a máquina trabalhando, o minuto é produtivo mesmo que o
#   rótulo seja `conversando_colega`. O sensor mede o mundo; o rótulo é uma
#   opinião sobre o mundo.
#
# ⚠️ O NÍVEL 3 NÃO É "MÃOS NA MÁQUINA". É `modo_operacao == 'manual'`, que
# exige que a medição tenha ficado cega POR CAUSA das mãos. Mão apoiada numa
# máquina visivelmente parada não é trabalho — é a mesma armadilha do
# "ausente + mãos" que já recusamos na Fase 94, e aceitá-la aqui reintroduziria
# o viés que esta árvore existe para eliminar.
#
# ⚠️ E O NÍVEL QUE DECIDIU FICA GRAVADO. Sem isso, "por que este minuto é
# produtivo?" volta a não ter resposta — que é o problema original com outra
# roupa.
# ═════════════════════════════════════════════════════════════════════════
_ARVORE_DECIDE = os.environ.get("KV_ARVORE_DECIDE", "off") not in (
    "off", "0", "false", "False", "")

NIVEL_HUMANO = "humano"
NIVEL_PRESENCA = "presenca"
NIVEL_MOVIMENTO = "movimento"
NIVEL_MANUAL = "manual"
NIVEL_ROTULO = "rotulo"
NIVEIS_ARVORE = (NIVEL_HUMANO, NIVEL_PRESENCA, NIVEL_MOVIMENTO,
                 NIVEL_MANUAL, NIVEL_ROTULO)

# O nível 4 que merece OLHADA: operador presente, máquina medida e parada, sem
# mãos. Hoje conta como produtivo pelo rótulo; pode ser ler desenho, esperar
# material ou preparar setup — trabalho de verdade. A árvore NÃO afirma
# improdutivo sozinha aqui: marca CANDIDATO e a camada manda para a fila.
NIVEL_CANDIDATO_IMPRODUTIVO = "parado_sem_maos"


def arvore_decidir(e: dict, cat_do_rotulo: str | None) -> tuple:
    """(categoria, nivel, motivo, candidato) — FUNÇÃO PURA, sem banco.

    `cat_do_rotulo` é o que o caminho atual decidiria. A árvore só o usa no
    nível 4 — e devolvê-lo intacto ali é de propósito: o nível 4 é 'não sei
    melhor que hoje', não 'é improdutivo'.
    """
    # ── PRECEDÊNCIA 0 — correção humana vence tudo, sempre ──
    if e.get("validado_humano") and (e.get("label_corrigido")
                                     or e.get("validacao_correto") is True):
        return (categoria_efetiva(cat_do_rotulo), NIVEL_HUMANO,
                "categoria do rótulo confirmado ou corrigido por você", None)

    # ── NÍVEL 1 — presença. Determinístico: zonas + tracking (as DUAS câmeras) ──
    papel = e.get("papel_pessoa")
    if papel == "posto_vazio" or (e.get("comportamento_label") == POSTO_VAZIO_LABEL
                                  and not e.get("label_corrigido")):
        return ("desperdicio", NIVEL_PRESENCA,
                "ninguém no posto — medido por zona e rastreamento", None)

    # Fase 110 — ele está no quadro, fora do posto. Aqui o RÓTULO decide, e é
    # de propósito: a atividade fora do posto pode ser produtiva (ponte
    # rolante) ou não, e quem sabe dizer é o gestor. Enquanto ele não
    # classificar, `cat_do_rotulo` é nulo e `categoria_efetiva` devolve
    # improdutivo — o mesmo que hoje, sem nenhum salto.
    #
    # Não cai nos níveis 2-3 de propósito: máquina em movimento e mãos na
    # máquina são sinais SOBRE O POSTO, e ele não está nele.
    if papel == PAPEL_OPERADOR_FORA:
        return (categoria_efetiva(cat_do_rotulo), NIVEL_PRESENCA,
                "fora do posto, com atividade identificada — a categoria é a "
                "que você deu a esta atividade", None)

    # ── NÍVEL 2 — a máquina se mexe. O sensor mede o mundo ──
    mov = e.get("movimento_maquina")
    if mov in ("continuo", "intermitente"):
        return ("valor_agregado", NIVEL_MOVIMENTO,
                f"o sensor viu a máquina em movimento ({mov}) neste minuto", None)

    # ── NÍVEL 3 — operação manual. NÃO é "mãos na máquina": é a medição ter
    #    ficado cega POR CAUSA das mãos (ver Fase 94) ──
    if e.get("modo_operacao") == "manual":
        return ("valor_agregado", NIVEL_MANUAL,
                "a parte móvel estava coberta pelas mãos do operador — "
                "operação manual", None)

    # ── NÍVEL 4 — o rótulo decide, como hoje. Mas fica MARCADO que foi ele ──
    candidato = None
    if e.get("modo_operacao") == "parado" and categoria_efetiva(cat_do_rotulo) == "valor_agregado":
        # Presente, máquina medida e parada, sem mãos, e o rótulo diz produtivo.
        # Pode ser ler desenho, esperar material, preparar setup — ou pode ser
        # ociosidade. A árvore NÃO decide: aponta.
        candidato = NIVEL_CANDIDATO_IMPRODUTIVO
    return (categoria_efetiva(cat_do_rotulo), NIVEL_ROTULO,
            "nenhum sinal determinístico decidiu — vale o rótulo do VLM",
            candidato)


def _cat_com_arvore(e: dict, cat_por_label: dict) -> tuple:
    """(label, categoria, duracao, nivel, candidato) — o `_cat_do_evento` com a
    árvore por cima. Com a flag desligada devolve exatamente o de hoje."""
    label = e.get("label_corrigido") or e.get("comportamento_label") or "?"
    dur = max(0.0, float(e.get("tempo_fim_s") or 0) - float(e.get("tempo_inicio_s") or 0))
    cat_rotulo = cat_por_label.get(label)
    if not _ARVORE_DECIDE:
        return label, categoria_efetiva(cat_rotulo), dur, None, None
    cat, nivel, _motivo, candidato = arvore_decidir(e, cat_rotulo)
    return label, cat, dur, nivel, candidato


def comparar_arvore(sb, empresa: str, processo: str, dia: str | None = None) -> dict:
    """Fase 95 — O ANTES E O DEPOIS, no MESMO dado, sem reprocessar nada.

    A árvore é determinística e lê só campos JÁ persistidos, então dá para
    calcular os dois números sobre os mesmos eventos: o de hoje (rótulo manda)
    e o da árvore. Não precisa ligar a flag, não precisa esperar um dia, não
    custa chamada nenhuma.

    Devolve de ONDE saiu cada ponto — sem isso a queda é inexplicável, e uma
    queda inexplicável não se apresenta a sócio nenhum.
    """
    eventos = varrer(
        sb, "eventos",
        "video_id, comportamento_label, label_corrigido, tempo_inicio_s, "
        "tempo_fim_s, principal, validacao_correto, validado_humano, "
        "papel_pessoa, movimento_maquina, modo_operacao, versao_instrumento, "
        # Fase 97: os sinais da permanência.
        # Fase 110: e quem decidiu a categoria — o comparativo tem de enxergar
        # a decisão humana, senão mede os dois motores sem ela.
        "categoria_lean, categoria_lean_origem, "
        "orientacao, trabalho",
        empresa=empresa, processo=processo,
    )
    frente = frente_maquina_do_processo(sb, empresa, processo)
    comps = varrer(sb, "comportamentos", "label, categoria_lean",
                   empresa=empresa, processo=processo)
    cat_por_label = {c["label"]: c.get("categoria_lean") for c in comps}

    if dia:
        videos = varrer(sb, "videos", "id, nome, processado_em",
                        empresa=empresa, processo=processo)
        do_dia = set()
        for v in videos:
            dt0 = _inicio_video_dt(v)
            if dt0 and dt0.date().isoformat() == dia:
                do_dia.add(v["id"])
        eventos = [e for e in eventos if e.get("video_id") in do_dia]

    tot = va_hoje = va_arvore = 0.0
    por_nivel: dict = defaultdict(lambda: {"minutos": 0.0, "va": 0.0, "eventos": 0})
    mudou: dict = defaultdict(float)
    candidatos = {"minutos": 0.0, "eventos": 0, "rotulos": defaultdict(float)}
    # Fase 97: os TRÊS estados que somam 100% do tempo observado.
    por_estado: dict = defaultdict(float)
    for e in eventos:
        if e.get("principal") is not True or e.get("validacao_correto") is False:
            continue
        label = e.get("label_corrigido") or e.get("comportamento_label") or "?"
        dur = max(0.0, float(e.get("tempo_fim_s") or 0) - float(e.get("tempo_inicio_s") or 0))
        if dur <= 0:
            continue
        cat_hoje = categoria_efetiva(cat_por_label.get(label))
        e["_frente_maquina"] = frente
        if _PERMANENCIA:
            cat_arv, nivel, _m, _est = decidir_permanencia(e, frente)
            cand = None
        else:
            cat_arv, nivel, _m, cand = arvore_decidir(e, cat_por_label.get(label))
        tot += dur
        va_hoje += dur if cat_hoje == "valor_agregado" else 0.0
        va_arvore += dur if cat_arv == "valor_agregado" else 0.0
        n = por_nivel[nivel]
        n["minutos"] += dur / 60.0
        n["eventos"] += 1
        n["va"] += (dur / 60.0) if cat_arv == "valor_agregado" else 0.0
        if cat_hoje != cat_arv:
            mudou[f"{nivel}: {cat_hoje} → {cat_arv}"] += dur / 60.0
        est, _v = estado_permanencia(e, frente)
        por_estado[est] += dur / 60.0
        if cand:
            candidatos["minutos"] += dur / 60.0
            candidatos["eventos"] += 1
            candidatos["rotulos"][label] += dur / 60.0

    pct = lambda x: round(x / tot * 100, 1) if tot else 0.0
    return {
        "dia": dia, "minutos_observados": round(tot / 60, 1),
        "produtivo_hoje_pct": pct(va_hoje),
        "produtivo_arvore_pct": pct(va_arvore),
        "delta_pp": round(pct(va_arvore) - pct(va_hoje), 1),
        # De onde saiu cada ponto — a decomposição que torna a queda explicável.
        "por_nivel": {
            k: {"minutos": round(v["minutos"], 1), "eventos": v["eventos"],
                "pct_do_tempo": round(100.0 * v["minutos"] * 60 / tot, 1) if tot else 0.0,
                "minutos_produtivos": round(v["va"], 1)}
            for k, v in sorted(por_nivel.items(), key=lambda kv: -kv[1]["minutos"])
        },
        "mudancas": dict(sorted(mudou.items(), key=lambda kv: -kv[1])),
        # O caso que a árvore NÃO decide sozinha: presente, máquina parada, sem
        # mãos, rótulo dizendo produtivo. Pode ser ler desenho ou esperar
        # material. Vira fila, não vira número.
        "candidatos_improdutivo": {
            "minutos": round(candidatos["minutos"], 1),
            "eventos": candidatos["eventos"],
            "pct_do_tempo": pct(candidatos["minutos"] * 60),
            "rotulos": sorted(({"rotulo": r, "minutos": round(m, 1)}
                               for r, m in candidatos["rotulos"].items()),
                              key=lambda x: -x["minutos"])[:12],
        },
        "flag_ligada": _ARVORE_DECIDE,
        "permanencia_ligada": _PERMANENCIA,
        # Os três estados. Somam 100% por construção: todo evento cai em
        # exatamente um deles.
        "por_estado": {
            k: {"minutos": round(v, 1),
                "pct": round(100.0 * v * 60 / tot, 1) if tot else 0.0}
            for k, v in sorted(por_estado.items(), key=lambda kv: -kv[1])
        },
        "nota": ("Simulação sobre os eventos JÁ gravados — a árvore é "
                 "determinística e lê só campos persistidos, então não precisa "
                 "reprocessar nem ligar a flag. `rotulo` é o nível em que "
                 "nenhum sinal determinístico decidiu: quanto maior essa fatia, "
                 "menos a árvore mudou o instrumento."),
    }



# ═════════════════════════════════════════════════════════════════════════
# Fase 97 — A PRODUTIVIDADE VEM DO QUE FOI OBSERVADO, NÃO DO NOME DO RÓTULO
#
# Decisão dos sócios (12/08). O produto é TEMPO DE PERMANÊNCIA NO POSTO.
# Fernando: "se o cara está de frente para o torno, ele está trabalhando".
#
# O DIAGNÓSTICO: as descrições do VLM estão boas. O que estava quebrado é o
# que vinha depois — descrição → rótulo (cluster) → categoria Lean →
# produtividade. Duas traduções, cada uma perdendo informação e somando erro.
# O caso que fechou a decisão: "parado junto ao torno, máquina parada" virava
# `acao_indefinida` e saía PRODUTIVO. A descrição está certa; o rótulo é lixo;
# a categoria contradiz a descrição.
#
#   ANTES  descrição → rótulo → categoria Lean → produtivo/improdutivo
#   AGORA  posição + orientação + julgamento do VLM → produtivo/improdutivo
#          (o rótulo continua existindo, mas só para AGRUPAR na tela)
#
# O rótulo deixa de carregar peso. Se ele errar, o número não estraga — e é
# isso que faz a "queda por contabilidade" deixar de existir.
#
# PRECEDÊNCIA:
#   0. correção humana  — inviolável
#   1. fora do posto    → IMPRODUTIVO (determinístico: zona + tracking)
#   2. no posto, voltado para o torno → PRODUTIVO (determinístico: pose)
#   3. no posto, voltado para outro lado → o VLM julga (`trabalho`)
#
# ⚠️ `trabalho=null` NUNCA vira produtivo por omissão: vira dúvida e vai para
# a fila. Omissão que rende ponto é exatamente o viés que derrubamos aqui.
# ═════════════════════════════════════════════════════════════════════════
_PERMANENCIA = os.environ.get("KV_PERMANENCIA", "on") not in (
    "off", "0", "false", "False", "")
# ⚠️ O NÍVEL 2 NÃO AFIRMA ENQUANTO A ORIENTAÇÃO NÃO FOR VERIFICADA COM DADO.
#
# `zonas_camera.frente_maquina` da cam1 está em 'camera' — "de frente para a
# câmera = de frente para o torno". Mas nos vídeos o operador aparece DE
# COSTAS quando trabalha no torno. Se a configuração estiver invertida,
# produtivo e improdutivo trocam de lugar — a métrica inteira.
#
# A verificação exige cruzar `orientacao` com `maos_maquina`, e `orientacao`
# NUNCA FOI PERSISTIDA (era calculada desde a Fase 86, injetada no prompt e
# jogada fora). Ela passa a ser gravada nesta fase; a verificação é possível
# a partir do primeiro vídeo processado depois do deploy.
#
# Até lá o nível 2 ABSTÉM-SE: o minuto cai no nível 3 e o VLM julga. Isso
# degrada com segurança (nada vira produtivo por engano) e liga com uma
# variável de ambiente, sem deploy, no dia em que o dado confirmar.
_ORIENTACAO_VERIFICADA = _env_ligada("KV_ORIENTACAO_VERIFICADA")

EST_FORA = "fora_do_posto"
EST_NO_TORNO = "no_posto_torno"
EST_OUTRO_LADO = "no_posto_outro_lado"
EST_INCONCLUSIVO = "inconclusivo"
ESTADOS_PERMANENCIA = (EST_NO_TORNO, EST_OUTRO_LADO, EST_FORA, EST_INCONCLUSIVO)


def _trabalho_do_minuto(no_bucket: list):
    """Maioria ponderada do julgamento do operador no minuto.

    O chamador entrega somente eventos do operador. A sobreposição temporal
    evita que um evento curto valha o mesmo que o regime dominante; empate
    continua sendo dúvida.
    """
    sim = nao = 0.0
    for e, ov in no_bucket:
        t = e.get("trabalho")
        if t is True:
            sim += float(ov or 0.0) if PRODUTIVIDADE_OPERADOR_ESTRUTURADA else 1.0
        elif t is False:
            nao += float(ov or 0.0) if PRODUTIVIDADE_OPERADOR_ESTRUTURADA else 1.0
    if abs(sim - nao) < 1e-9:
        return None
    return sim > nao


def estado_permanencia(e: dict, frente_maquina: str | None) -> tuple:
    """(estado, voltado_para_o_torno) — onde a pessoa esteve e para onde olhava.

    `voltado` é None quando não houve pose ou quando a câmera não sabe traduzir
    orientação-para-a-câmera em orientação-para-a-máquina (`frente_maquina` não
    configurado). None não é "não estava voltado": é "não dá para dizer".
    """
    papel = e.get("papel_pessoa")
    lbl = e.get("label_corrigido") or e.get("comportamento_label")
    if papel == "posto_vazio" or (lbl == POSTO_VAZIO_LABEL and not e.get("label_corrigido")):
        return EST_FORA, None
    if papel == "visitante":
        # Visitante não é o titular do posto: o tempo dele não é permanência
        # do operador. Conta como fora.
        return EST_FORA, None
    if papel == PAPEL_OPERADOR_FORA:
        # ⭐⭐ A LINHA QUE IMPEDE O NÚMERO DE SE MEXER NO DIA DO DEPLOY.
        #
        # `operador_fora` é o MESMO estado que `posto_vazio` para a
        # permanência: em ambos o operador não está no posto. Se caísse no ramo
        # `papel != "operador"` abaixo, viraria EST_INCONCLUSIVO — que SAI do
        # denominador (`permanencia_do_dia`) — e a presença SUBIRIA sozinha, por
        # mudança de contabilidade e não de mundo. É exatamente o desastre
        # documentado logo abaixo, na outra direção.
        #
        # O que muda com a Fase 110 é a CATEGORIA LEAN daquele tempo (ver
        # `decidir_permanencia`), nunca a permanência.
        return EST_FORA, None
    if papel != "operador":
        # ⭐ REGRA PRIMÁRIA: SÓ CONTA QUEM ESTÁ NO POSTO.
        #
        # Ausência de identidade NÃO É PRESENÇA. Estava certo, mas atrás de
        # `KV_PRODUTIVIDADE_OPERADOR_V9` — e com a chave desligada (o padrão,
        # fail-closed) o `None` voltava a cair no mesmo ramo do operador.
        #
        # O caso que isso deixava passar é o pior possível: quando NÃO EXISTE
        # zona de posto desenhada, a eleição inteira é pulada, `papel_pessoa`
        # nasce nulo para todo mundo, e qualquer pessoa detectada em qualquer
        # canto do quadro contava como "operador no posto". A permanência ia a
        # 100% justamente quando o sistema não sabia onde o posto fica.
        #
        # Agora vale sempre, com ou sem flag: quem não foi identificado como
        # operador não entra no numerador NEM no denominador — vira
        # INCONCLUSIVO, que aparece como cobertura. "Não sei" não pode ser
        # promovido a "sei que sim".
        return EST_INCONCLUSIVO, None
    # Sem verificação, a orientação não decide — ver `_ORIENTACAO_VERIFICADA`.
    voltado = (orientacao_vs_maquina(e.get("orientacao"), frente_maquina)
               if _ORIENTACAO_VERIFICADA else None)
    if voltado is None:
        return EST_OUTRO_LADO, None
    de_frente = "de frente" in voltado.lower() and "costas" not in voltado.lower()
    return (EST_NO_TORNO if de_frente else EST_OUTRO_LADO), de_frente


def decidir_permanencia(e: dict, frente_maquina: str | None) -> tuple:
    """(categoria, nivel, motivo, estado) — a decisão nova. FUNÇÃO PURA.

    Nenhum rótulo de atividade entra aqui. É a garantia de que rótulo novo não
    move número nenhum.
    """
    # ── 0 — correção humana, inviolável ──
    # ⚠️ `validado_humano=True` NÃO É DECISÃO HUMANA quando veio de MECANISMO.
    # `posto_vazio` e `auditoria` usam a flag só para ficar fora da fila (Fase
    # 62), e no dia 10/08 isso é 255 de 255 eventos — tratá-los como decisão
    # humana faria a arquitetura nova nunca rodar. A Fase 88 já tinha
    # documentado esta armadilha; aqui ela voltaria pela porta da precedência.
    _mecanico = (e.get("origem_validacao") or "") in _ORIGENS_MECANICAS
    if (not _mecanico) and e.get("validado_humano") and (
            e.get("label_corrigido") or e.get("validacao_correto") is True):
        # ⚠️ CONFIRMAR NÃO É APROVAR. "o rótulo está certo" diz que o RÓTULO
        # está certo — não que o trecho é produtivo. Confirmar
        # `conversando_colega` mantém improdutivo. A primeira versão desta
        # função devolvia `valor_agregado` na confirmação, e o comparativo com
        # o dia real acusou na hora: 41% viraram 81%, quase tudo vindo dos
        # eventos auto-validados por mecanismo (posto_vazio e auditoria).
        est, _v = estado_permanencia(e, frente_maquina)
        return (categoria_efetiva(e.get("_cat_humana")),
                NIVEL_HUMANO, "você decidiu este trecho", est)

    estado, voltado = estado_permanencia(e, frente_maquina)

    if estado == EST_INCONCLUSIVO:
        return (CATEGORIA_SEM_EVIDENCIA, "identidade",
                "não foi possível identificar o operador neste trecho", estado)

    # ── 1 — fora do posto ──
    if estado == EST_FORA:
        # ⭐ Fase 110 — NEM TODO "FORA DO POSTO" É DESPERDÍCIO. O operador
        # pode estar operando a ponte rolante: fora do polígono, e trabalhando.
        #
        # ⚠️ ESTE RAMO É ALCANÇÁVEL SÓ POR `operador_fora`, e isso é por
        # CONSTRUÇÃO, não por filtro. `posto_vazio` e `visitante` caem em 1c
        # abaixo e continuam desperdício — a massa auto-validada por MECANISMO
        # (`_ORIGENS_MECANICAS`) nunca chega aqui. Foi exatamente por essa
        # porta que a produtividade saltou de 41% para 81% numa versão
        # anterior desta função.
        if e.get("papel_pessoa") == PAPEL_OPERADOR_FORA:
            # 1a — o gestor JÁ classificou esta atividade na árvore.
            # A origem `humano_rotulo` só existe se um humano clicou: nenhum
            # rótulo de VLM, cluster ou classificador de IA a produz.
            cat = e.get("categoria_lean")
            if (e.get("categoria_lean_origem") == ORIGEM_HUMANO_ROTULO
                    and cat in CATEGORIAS_LEAN_VALIDAS):
                return (cat, NIVEL_HUMANO,
                        "fora do posto, e você classificou esta atividade", estado)
            # 1b — ainda sem decisão humana. `CATEGORIA_SEM_EVIDENCIA` é
            # `desperdicio`: numericamente IGUAL ao de hoje, mas dizendo
            # "ainda não decidimos" em vez de "é desperdício". A diferença
            # aparece na fila de dúvidas, não no número.
            return (CATEGORIA_SEM_EVIDENCIA, "fora_sem_classificacao",
                    "fora do posto, fazendo algo que ainda não foi classificado",
                    estado)
        # 1c — posto vazio / visitante: inalterado.
        return ("desperdicio", NIVEL_PRESENCA,
                "fora do posto — medido por zona e rastreamento", estado)

    # Regra de conversa só depois de identidade e do ramo Fase 110, mas antes
    # de orientação. Assim gestor/colega vencem a pose apenas quando o recorte
    # auditável, o label canônico original e `trabalho` são coerentes.
    conversa = decisao_conversa_evidenciada(e)
    if conversa is not None:
        _estado_conversa, _motivo_conversa = conversa
        return (
            "valor_agregado" if _estado_conversa == "produtivo" else "desperdicio",
            "julgamento_visual_roupa",
            _motivo_conversa.replace("_", " "),
            estado,
        )

    # ── 2 — no posto, voltado para o torno ──
    if estado == EST_NO_TORNO:
        return ("valor_agregado", "orientacao",
                "no posto e voltado para o torno — medido pela pose", estado)

    # ── 3 — no posto, voltado para outro lado: o VLM julga ──
    t = e.get("trabalho")
    if t is True:
        return ("valor_agregado", "julgamento",
                "no posto, de outro lado, e a atividade é serviço do posto", estado)
    if t is False:
        return ("desperdicio", "julgamento",
                "no posto, de outro lado, e a atividade não é serviço do posto",
                estado)
    # `None` — e aqui está a regra que impede o viés voltar pela porta dos
    # fundos: omissão NÃO rende ponto. Vira desperdício E vira dúvida.
    return (CATEGORIA_SEM_EVIDENCIA, "duvida",
            "no posto, de outro lado, e não deu para dizer se é serviço — "
            "entra na fila", estado)


# ═════════════════════════════════════════════════════════════════════════
# Fase 101 — O NÚMERO PRINCIPAL É A PERMANÊNCIA. Decisão dos sócios, 12/08.
#
# Fernando: "a única coisa que a gente conseguiu medir é tempo de permanência".
# Iago: "se ele está apontando pra máquina, ele está trabalhando. Não vamos
# inventar mais nada, até pelo prazo".
#
# A esteira antiga — frames → VLM descreve → cluster nomeia → nome recebe
# categoria → produtivo — erra em cada etapa e o erro chega ao número. Em três
# dias a produtividade deu 41%, 49%, 56% e 80% com o MESMO operador. A esteira
# mudou, não o chão de fábrica.
#
# ⚠️ ESTA FUNÇÃO NÃO LÊ RÓTULO, DESCRIÇÃO, CATEGORIA NEM `trabalho`.
# É por CONSTRUÇÃO que rótulo errado não move o número — não por filtro. Um
# filtro precisaria estar certo sobre o que excluir; não ler nada não precisa
# estar certo sobre nada. É a diferença entre "não deve influenciar" e "não
# tem como influenciar".
#
# ⚠️ SOBRE EXCLUIR "TRACK SINTÉTICO", QUE O PEDIDO PEDIA — NÃO FOI FEITO, E É
# DE PROPÓSITO. Medido no banco: só existem dois ids negativos, e nenhum dos
# dois é fabricação.
#   -1 (1.187 eventos) = `posto_vazio`: o detector NÃO achou ninguém na zona.
#      Isso não é um track inventado — é a própria medição de "fora do posto".
#      Excluí-lo apagaria TODA a evidência de ausência e a permanência daria
#      ~100% por construção. Ficaria ótimo na tela e seria falso.
#   -2 (1.617 eventos) = operador visto pela CAM2 quando a cam1 não o vê
#      (oclusão total pela máquina). Também é detecção real, de outra câmera —
#      e é justamente o minuto em que ele está mais colado no torno. Excluir
#      derrubaria a permanência para baixo pelo motivo oposto.
# Excluir os dois enviesaria o número nas DUAS direções ao mesmo tempo.
#
# ⚠️ SOBRE `n_amostras = 0`: esse contador é de amostras que foram ao VLM. O
# DETECTOR roda em todo minuto — o gate suprime a chamada de visão, não a
# detecção. Presença continua medida. Como esta função só lê presença, minuto
# sem amostra analisada entra normalmente e o denominador fica honesto.
# ═════════════════════════════════════════════════════════════════════════
def permanencia_do_dia(eventos: list, frente_maquina: str | None = None) -> dict:
    """Os três estados, em PERCENTUAL do tempo observado. Função pura.

    Zero chamada de API. Zero leitura de rótulo. Os três somam 100%.

    Com a orientação NÃO verificada, `no_posto_torno` e `no_posto_outro_lado`
    colapsam em `no_posto` — melhor um número simples e certo do que um detalhe
    que pode estar invertido.
    """
    seg = {
        EST_NO_TORNO: 0.0,
        EST_OUTRO_LADO: 0.0,
        EST_FORA: 0.0,
        EST_INCONCLUSIVO: 0.0,
    }
    for e in eventos or []:
        if e.get("principal") is False:
            continue
        dur = max(0.0, float(e.get("tempo_fim_s") or 0)
                  - float(e.get("tempo_inicio_s") or 0))
        if dur <= 0:
            continue
        estado, _voltado = estado_permanencia(e, frente_maquina)
        seg[estado] = seg.get(estado, 0.0) + dur

    total_bruto = sum(seg.values())
    total = total_bruto - seg[EST_INCONCLUSIVO]
    if total <= 0:
        return {"no_posto_pct": 0.0, "no_posto_torno_pct": 0.0,
                "no_posto_outro_lado_pct": 0.0, "fora_pct": 0.0,
                "cobertura_pct": 0.0,
                "inconclusivo_pct": (100.0 if total_bruto > 0 else 0.0),
                "orientacao_verificada": _ORIENTACAO_VERIFICADA,
                "detalhado": False, "sem_dado": True}

    def pct(x):
        return round(100.0 * x / total, 1)

    no_posto = seg[EST_NO_TORNO] + seg[EST_OUTRO_LADO]
    saida = {
        "no_posto_pct": pct(no_posto),
        "fora_pct": pct(seg[EST_FORA]),
        "cobertura_pct": round(100.0 * total / total_bruto, 1)
        if total_bruto > 0 else 0.0,
        "inconclusivo_pct": round(
            100.0 * seg[EST_INCONCLUSIVO] / total_bruto, 1
        ) if total_bruto > 0 else 0.0,
        "orientacao_verificada": _ORIENTACAO_VERIFICADA,
        # `detalhado=False` é a instrução para a tela mostrar SÓ permanência.
        "detalhado": bool(_ORIENTACAO_VERIFICADA),
        "sem_dado": False,
    }
    if _ORIENTACAO_VERIFICADA:
        saida["no_posto_torno_pct"] = pct(seg[EST_NO_TORNO])
        saida["no_posto_outro_lado_pct"] = pct(seg[EST_OUTRO_LADO])
    else:
        # Colapsados: expor os dois separados aqui seria oferecer à tela um
        # detalhe que pode estar invertido.
        saida["no_posto_torno_pct"] = None
        saida["no_posto_outro_lado_pct"] = None

    # ⚠️ Os três (ou dois) TÊM de fechar 100%. Sem estado "indefinido", sem
    # "não classificado", sem sobra: todo instante observado está em um e só um.
    _soma = saida["no_posto_pct"] + saida["fora_pct"]
    if abs(_soma - 100.0) > 0.2:
        log.warning("[permanencia] os estados somaram %.1f%%, não 100%% — "
                    "há tempo observado fora dos estados.", _soma)
    return saida


def frase_permanencia(p: dict) -> str:
    """A frase que o cliente lê. Português de chão de fábrica, sem jargão e —
    ⚠️ REGRA ABSOLUTA — SEM DURAÇÃO. Só percentual.

    O motivo é estatístico, não estético: a captura amostra ~50% de cada hora,
    então o percentual é estimativa correta do turno e o minuto absoluto seria
    METADE da verdade. Mostrar minuto seria ERRADO, não apenas feio.
    """
    if p.get("sem_dado"):
        return "Ainda não há observação suficiente para medir o turno."
    base = f"O operador esteve no posto em {p['no_posto_pct']:.0f}% do turno."
    if p.get("detalhado") and p.get("no_posto_torno_pct") is not None:
        base += (f" Desse tempo no posto, "
                 f"{p['no_posto_torno_pct']:.0f}% do turno voltado para o torno.")
    return base


# ═════════════════════════════════════════════════════════════════════════
# Fase 98 — REAVALIAÇÃO QUANDO O HUMANO CORRIGE. É DIAGNÓSTICO, NÃO APRENDIZADO.
#
# Quando o gestor corrige um rótulo, o sistema não sabe POR QUE errou. Duas
# causas completamente diferentes, com consertos opostos:
#   (a) a DESCRIÇÃO estava errada e o rótulo apenas a seguiu  → o VLM é cego
#       naquele enquadramento, e o conserto é de captura/prompt;
#   (b) a descrição estava CERTA e o rótulo a traiu           → o conserto é
#       de clusterização.
# Sem separá-las, toda correção vira anedota.
#
# ⚠️ REGRA INVIOLÁVEL: a reavaliação vale SÓ PARA O EVENTO CORRIGIDO. Não
# propaga por descrição parecida, não vira regra, não entra no vocabulário
# como canônico, não religa aprendizado nenhum. Foi exatamente a propagação
# por descrição que espalhou `conversando_colega` errado na Fase 67 — e a
# chave de aprendizado automático continua desligada.
#
# CUSTO: uma chamada por correção HUMANA, e só quando `KV_REAVALIAR_CORRECAO`
# estiver ligada. Não roda em lote, não roda em ingestão, não roda sozinha.
# ═════════════════════════════════════════════════════════════════════════
_REAVALIAR = os.environ.get("KV_REAVALIAR_CORRECAO", "off") not in (
    "off", "0", "false", "False", "")

PROMPT_REAVALIACAO = """Você descreveu um trecho de vídeo de um posto de trabalho industrial e um SUPERVISOR HUMANO corrigiu a classificação. Sua tarefa é DIAGNOSTICAR o próprio erro — não se defender, não reclassificar.

O que você havia descrito: "{descricao}"
Rótulo que saiu disso: "{rotulo_antigo}"
Rótulo que o supervisor escolheu: "{rotulo_novo}"

Olhando as imagens de novo, com a correção do supervisor em mãos, responda:

1. A sua DESCRIÇÃO estava correta? Se não, o que faltou ver?
2. A atividade corrigida ("{rotulo_novo}") é TRABALHO DO POSTO?
   - trabalho = ler desenho, medir peça, buscar ferramenta ou material, organizar bancada, limpar cavaco, conversar sobre o serviço, operar a máquina
   - não é trabalho = celular, conversa paralela, parado sem atividade aparente
   - null se não der para dizer

Responda em JSON:
{{"descricao_estava_correta": true|false,
  "causa": "descricao_errada" | "rotulo_traiu_descricao" | "indeterminado",
  "o_que_faltou": "uma frase curta, ou null se a descrição estava boa",
  "descricao_revisada": "a descrição correta em uma frase, ou null se a original já estava certa",
  "trabalho": true|false|null}}

Seja honesto sobre a própria cegueira: "não dava para ver o paquímetro nesse ângulo" é uma resposta útil; "estava tudo certo" quando não estava, não é."""


def reavaliar_correcao(groq_client, evento: dict, imgs_b64: list,
                       rotulo_novo: str) -> dict | None:
    """UMA chamada de visão para diagnosticar por que o rótulo saiu errado.

    Devolve None quando a flag está desligada ou não há imagem — e None é
    resposta legítima: sem diagnóstico é melhor que com diagnóstico inventado.
    """
    if not _REAVALIAR:
        return None
    if not imgs_b64:
        return {"erro": "sem frames aquecidos para reavaliar"}
    prompt = PROMPT_REAVALIACAO.format(
        descricao=(evento.get("descricao_bruta") or "(sem descrição)"),
        rotulo_antigo=(evento.get("comportamento_label") or "?"),
        rotulo_novo=rotulo_novo,
    )
    try:
        bruto = groq_vision_call(
            groq_client, imgs_b64[0], prompt, json_mode=True,
            max_tokens=320, temperatura=0.0,
            imagens_extra=imgs_b64[1:3],
        )
        r = json.loads(bruto) or {}
    except Exception as e:  # noqa: BLE001
        log.warning("[reavaliacao] falhou (%s) — não-fatal.", e)
        return {"erro": str(e)[:200]}
    causa = str(r.get("causa") or "indeterminado")
    if causa not in ("descricao_errada", "rotulo_traiu_descricao", "indeterminado"):
        causa = "indeterminado"
    return {
        "causa": causa,
        "descricao_estava_correta": (bool(r["descricao_estava_correta"])
                                     if isinstance(r.get("descricao_estava_correta"), bool)
                                     else None),
        "o_que_faltou": (str(r["o_que_faltou"])[:400]
                         if r.get("o_que_faltou") else None),
        "descricao_revisada": (str(r["descricao_revisada"])[:400]
                               if r.get("descricao_revisada") else None),
        # ⚠️ Revisado porque é ele que move o número na Fase 97 — mas vale SÓ
        # para este evento. `null` continua sendo `null`.
        "trabalho": (bool(r["trabalho"]) if isinstance(r.get("trabalho"), bool)
                     else None),
        "rotulo_antigo": evento.get("comportamento_label"),
        "rotulo_novo": rotulo_novo,
        "em": datetime.now(timezone.utc).isoformat(),
        # O escopo, escrito no próprio dado: quem ler isto daqui a seis meses
        # precisa saber que não virou regra.
        "escopo": "somente este evento — não propaga, não vira vocabulário",
    }


def custo_reavaliacao_usd(n_imagens: int = 3) -> float:
    """Custo de UMA reavaliação, para o dono decidir se liga a chave.

    Prompt ~330 tokens + n imagens de 1024x576 (~786 tokens cada) + ~120 de
    saída, a $1/$5 por MTok (Haiku 4.5).
    """
    tin = 330 + n_imagens * 786
    tout = 120
    return round(tin / 1e6 * 1.0 + tout / 1e6 * 5.0, 5)

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
# A MANCHETE DO POSTO — o que o dono da fábrica quer ler primeiro.
#
# O topo do dashboard mostrava "CAPTURA DESATUALIZADA · aguardando nova
# captura", em amarelo de alerta. Três problemas, e o terceiro é o que importa:
#
#  1. CONTAVA A HISTÓRIA ERRADA. O que atrasa não é a captura, é a FILA DE
#     PROCESSAMENTO: medido em 18/08, segmentos gravados às 06:20 foram
#     processados às 08:39. A câmera estava filmando o tempo todo.
#  2. ALARMAVA O NORMAL. A captura é amostrada e processada em lote; a última
#     leitura ser de meia hora atrás é o funcionamento esperado, não uma
#     falha. Alarme que dispara no estado normal ensina a ignorar alarme.
#  3. ⚠️ E OCUPAVA O MELHOR ESPAÇO DA TELA COM UM ASSUNTO NOSSO. Aquele é o
#     primeiro bloco que um dono de fábrica olha. Ele não quer saber do
#     relógio do nosso processamento — quer saber como foi o turno.
#
# Então o lugar passa a responder "como foi o turno?", e o horário da última
# leitura vira uma linha discreta ao lado.
#
# ⚠️ COMPARA O POSTO COM ELE MESMO. Nada de meta inventada: a régua é a média
# dos próprios dias anteriores. É a única comparação justa quando não existe
# padrão de indústria para este posto.
#
# ⛔ E SEM DURAÇÃO, como todo o resto da vitrine. Hora de relógio ("07:55") é
# localizador e pode; "há 35 minutos" é duração e não pode.
# ═════════════════════════════════════════════════════════════════════════
def leitura_do_turno(permanencia: dict | None = None,
                     produtividade: dict | None = None,
                     serie: list | None = None) -> dict:
    """A manchete: um número, uma frase e a comparação com os dias anteriores.

    Função pura, zero API. Devolve `sem_dado` quando não há o que dizer — e aí
    a tela diz isso, em vez de mostrar zero como se fosse resultado.
    """
    prod = produtividade or {}
    perm = permanencia or {}

    atual = prod.get("presenca_pct")
    if atual is None:
        atual = perm.get("no_posto_pct")
    if not isinstance(atual, (int, float)) or prod.get("sem_dado"):
        return {"sem_dado": True,
                "titulo": "Ainda não há leitura deste posto",
                "frase": "Assim que o primeiro turno for processado, o "
                         "resultado aparece aqui.",
                "tom": "neutro"}

    # A régua: os dias ANTERIORES deste mesmo posto, sem o dia corrente.
    pontos = [s.get("presenca_pct") for s in (serie or [])
              if isinstance(s.get("presenca_pct"), (int, float))]
    anteriores = pontos[:-1] if len(pontos) >= 2 else []
    media = (sum(anteriores) / len(anteriores)) if anteriores else None
    delta = round(atual - media, 1) if media is not None else None

    # ⚠️ 3 pontos é o piso para chamar de diferença. Abaixo disso é oscilação
    # normal entre turnos, e apontá-la como notícia treina o gestor a duvidar
    # do painel.
    if delta is None:
        comparacao = ("Primeiro turno medido deste posto — ele vira a régua "
                      "dos próximos.")
        tom = "neutro"
    elif delta >= 3:
        comparacao = (f"{delta:.0f} pontos acima da média dos dias anteriores "
                      "deste posto.")
        tom = "bom"
    elif delta <= -3:
        comparacao = (f"{abs(delta):.0f} pontos abaixo da média dos dias "
                      "anteriores deste posto.")
        tom = "atencao"
    else:
        comparacao = "Em linha com a média dos dias anteriores deste posto."
        tom = "neutro"

    return {
        "sem_dado": False,
        "titulo": f"O operador esteve no posto em {atual:.0f}% do turno",
        "frase": comparacao,
        "tom": tom,
        "presenca_pct": round(float(atual), 1),
        "media_anterior_pct": round(media, 1) if media is not None else None,
        "delta_pontos": delta,
        "n_dias_comparados": len(anteriores),
    }

# ═════════════════════════════════════════════════════════════════════════
# POR QUE ESTA COLUNA ESTÁ VAZIA? — o diagnóstico que faltava.
#
# Já se perdeu tempo demais nesta conversa com "a flag está ligada?", "o deploy
# subiu?", "a coluna existe?". São três perguntas diferentes com três consertos
# diferentes, e olhar para um `NULL` no banco não distingue nenhuma delas.
#
# Esta função responde as três de uma vez, para cada coluna que costuma nascer
# vazia: QUEM a preenche, QUAL chave a governa, se essa chave está ligada
# AGORA neste processo, e o que fazer se não estiver.
#
# ⚠️ Lê só variáveis de ambiente já resolvidas. Zero API, zero banco.
# ═════════════════════════════════════════════════════════════════════════
def estado_dos_sinais() -> dict:
    """O que preenche cada coluna e se está ligado NESTE processo agora."""
    sinais = [
        {
            "coluna": "narrativa",
            "o_que_e": "a narrativa do minuto, em três a cinco frases",
            "chave": "KV_NARRATIVA",
            "ligado": _NARRATIVA,
            "escrita_em": "eventos principais",
            "requer_coluna_no_banco": True,
            "se_vazia": (
                "só nasce em vídeo processado DEPOIS do deploy — histórico não "
                "é reprocessado. Se vídeos novos seguem vazios, o modelo está "
                "devolvendo resumo curto demais (o filtro corta abaixo de 120 "
                "caracteres, para uma linha não passar por narrativa)."
            ),
        },
        {
            "coluna": "trabalho",
            "o_que_e": "a decisão binária produtivo/improdutivo do VLM",
            "chave": "KV_PRODUTIVIDADE_OPERADOR_V9",
            "ligado": PRODUTIVIDADE_OPERADOR_V9,
            "escrita_em": "principais e crus",
            "requer_coluna_no_banco": True,
            "se_vazia": (
                "com a chave desligada o campo é forçado a NULL na gravação, "
                "mesmo que o modelo tenha respondido."
            ),
        },
        {
            "coluna": "maos_maquina",
            "o_que_e": "punho dentro da zona da máquina (sensor de pose)",
            "chave": "KV_PRODUTIVIDADE_OPERADOR_V9",
            "ligado": PRODUTIVIDADE_OPERADOR_V9,
            "escrita_em": "principais SEMPRE; crus só com a chave ligada",
            "requer_coluna_no_banco": True,
            "se_vazia": (
                "nos CRUS, a chave desligada zera. Nos PRINCIPAIS, vazio "
                "significa que não há zona com papel 'maquina' desenhada — sem "
                "ela o sensor não tem onde medir."
            ),
        },
        {
            "coluna": "orientacao",
            "o_que_e": "frente/costas/perfil em relação à câmera (sensor de pose)",
            "chave": "KV_PRODUTIVIDADE_OPERADOR_V9",
            "ligado": PRODUTIVIDADE_OPERADOR_V9,
            "escrita_em": "principais SEMPRE; crus só com a chave ligada",
            "requer_coluna_no_banco": True,
            "se_vazia": (
                "nos PRINCIPAIS, vazio significa que a pose não teve ombros "
                "visíveis no minuto. Note que ESCREVER a orientação independe "
                "de KV_ORIENTACAO_VERIFICADA — essa chave só decide se ela "
                "DECIDE produtividade."
            ),
        },
        {
            "coluna": "decidido_por",
            "o_que_e": "qual nível da árvore decidiu o minuto",
            "chave": None,
            "ligado": True,
            "escrita_em": "eventos principais",
            "requer_coluna_no_banco": True,
            "se_vazia": (
                "não tem chave — se está vazio num evento PRINCIPAL, o evento "
                "é anterior à Fase 95. Nos crus é vazio por desenho: eles não "
                "passam pela consolidação do minuto."
            ),
        },
        {
            "coluna": "reavaliacao",
            "o_que_e": "o diagnóstico de por que o sistema errou, quando você corrige",
            "chave": "KV_REAVALIAR_CORRECAO",
            "ligado": _REAVALIAR,
            "escrita_em": "só no evento que VOCÊ corrigiu à mão",
            "requer_coluna_no_banco": True,
            "se_vazia": (
                "é o normal: só existe em evento corrigido manualmente, com a "
                "chave ligada. Nunca aparece em ingestão."
            ),
        },
    ]
    # ⚠️ A ARMADILHA MAIS COMUM, e ela não é flag: 3 de cada 4 linhas da tabela
    # são CRUS de auditoria (`principal = false`). Eles não passam pela
    # consolidação do minuto, então quase todas essas colunas são vazias neles
    # POR DESENHO. Olhar a tabela sem filtrar `principal is true` faz tudo
    # parecer quebrado.
    return {
        "versao_instrumento": VERSAO_INSTRUMENTO,
        "sinais": sinais,
        "aviso": (
            "A maioria das linhas de `eventos` são CRUS de auditoria "
            "(principal = false) e não recebem estas colunas por desenho. "
            "Filtre por `principal is true` antes de concluir que algo falhou."
        ),
        "desligados": [s["coluna"] for s in sinais if not s["ligado"]],
    }

# ═════════════════════════════════════════════════════════════════════════
# SUGESTÕES DO POSTO — sobre o PROCESSO e o OPERADOR, por regra.
#
# As primeiras vinham de `PROMPT_ANALISE`, um consultor Lean de LLM: genéricas
# ("implantar 5S", "reduzir setup via SMED"), com nome complicado e sem dizer
# como fazer. As segundas erraram para o outro lado — falavam do SpectraAI
# ("veja se o computador da borda está ligado", "abra a Fila", "desenhe a zona
# da máquina"). O cliente não tem acesso a hardware, a vídeo bruto nem às
# entranhas do produto: aquilo era lista de tarefas NOSSA na tela DELE.
#
# ⚠️ A REGRA QUE DEFINE ESTE MÓDULO: todo passo tem de ser executável no CHÃO
# DE FÁBRICA por quem gere o posto. Falar com o operador, mudar de lugar o que
# ele busca, remarcar um horário, redistribuir uma tarefa. Nada de abrir tela,
# conferir cabo ou configurar sistema. Problema do produto vira aviso do
# produto (o topo da tela já diz quando a captura atrasou) — nunca sugestão de
# melhoria de processo.
#
# As três propriedades que continuam valendo:
#
#  1. SÓ NASCEM DE UM NÚMERO MEDIDO. Cada regra tem gatilho explícito e carrega
#     o número no título. Sem número, a sugestão não existe — é impossível
#     falar de um problema que o dado não mostra.
#  2. NOME DE CHÃO DE FÁBRICA. "O operador sai do posto em 20% do turno", não
#     "oportunidade de otimização da taxa de ocupação do recurso".
#  3. SEMPRE COM O COMO. Passos concretos, na ordem de fazer.
#
# ⚠️ E UMA QUARTA, QUE É ÉTICA E TAMBÉM É BOM PRODUTO: nenhuma sugestão acusa
# o operador. O número mede o POSTO, não a pessoa. Um gestor que usa isto para
# punir perde a cooperação de quem mais sabe onde o processo trava — e o
# primeiro passo de quase toda sugestão é PERGUNTAR a ele, porque ele
# geralmente já sabe a resposta.
#
# ⚠️ ESTABILIDADE É REQUISITO, não acaso. O gestor pediu que a lista mude com
# BAIXA frequência — e a razão é boa: um painel que sugere coisa nova todo dia
# ensina a não fazer nenhuma. O mecanismo não é congelar a lista; é PREFERIR
# ACHADO ESTRUTURAL a oscilação do dia.
#
# Cada regra declara `estrutural`:
#   · ESTRUTURAL (`True`) — fala da FORMA do processo: quanto do turno é ciclo
#     automático, como o posto está arranjado, o quanto ele varia entre dias.
#     Isso não muda de um dia para o outro, então a sugestão também não muda.
#     Ganha um bônus de peso.
#   · DO DIA (`False`) — fala do que aconteceu ontem. Só entra com magnitude
#     grande, e perde para o estrutural em caso de empate. Assim uma hora ruim
#     isolada não expulsa da tela um problema de arranjo que está lá há meses.
#
# ⚠️ ZERO CHAMADA DE API, função PURA, e não move número nenhum.
# ═════════════════════════════════════════════════════════════════════════
_ROTULOS_ACOMPANHAR = ("monitorar_maquina", "acompanhar_maquina", "observar")
_ROTULOS_CONVERSA = ("conversando_colega", "interagir_com_colega_ou_lider",
                     "conversa")
_ROTULOS_BUSCA = ("deslocar_buscar_material_ferramenta", "deslocar_pelo_posto",
                  "deslocamento", "buscar")


def _fatia(atividades, prefixos) -> float:
    """Quanto do tempo observado caiu em rótulos que começam por `prefixos`."""
    total = 0.0
    for a in atividades or []:
        lbl = str(a.get("comportamento") or "")
        if any(lbl.startswith(p) for p in prefixos):
            v = a.get("pct_tempo")
            if isinstance(v, (int, float)):
                total += v
    return total


def sugestoes_do_posto(
    permanencia: dict | None = None,
    produtividade: dict | None = None,
    por_hora: list | None = None,
    atividades: list | None = None,
    serie: list | None = None,
    max_itens: int = 4,
) -> list[dict]:
    """As sugestões que o gestor lê. Ordenadas por PESO, não por categoria.

    `max_itens` é baixo de propósito: uma lista de dez sugestões é uma lista de
    zero. Faz-se uma coisa por vez, e a primeira tem de ser a que mais pesa.
    """
    prod = produtividade or {}
    perm = permanencia or {}
    itens: list[dict] = []

    def add(peso, chave, titulo, porque, passos, tom="atencao", estrutural=False):
        # O bônus é o que faz o achado de FORMA vencer o do dia num empate.
        itens.append({"chave": chave, "titulo": titulo, "porque": porque,
                      "passos": passos, "tom": tom,
                      "_peso": peso + (12 if estrutural else 0),
                      "_estrutural": estrutural})

    # ── 1) O operador sai do posto. É o achado mais acionável que existe aqui:
    # a causa quase nunca é a pessoa, é o que falta ao alcance da mão.
    vazio = prod.get("posto_vazio_pct")
    if vazio is None:
        vazio = perm.get("fora_pct")
    if isinstance(vazio, (int, float)) and vazio >= 12:
        add(90 if vazio >= 25 else 78, "posto_vazio",
            f"O operador sai do posto em {vazio:.0f}% do turno",
            "Enquanto ele está fora, o torno não avança. Na maioria das vezes "
            "a saída é para buscar algo que poderia estar ao alcance da mão.",
            ["Pergunte ao operador o que ele mais precisa buscar durante o "
             "turno — material, ferramenta, desenho ou medição.",
             "O que ele citar duas vezes ganha um lugar fixo ao lado do torno, "
             "abastecido antes do turno começar.",
             "O que não couber ao lado do posto passa a ser trazido por quem "
             "abastece, não buscado por quem opera."],
            # Estrutural: a causa é abastecimento e arranjo, não o dia de
            # ontem. Some quando o posto for reorganizado, não quando o
            # operador tiver um dia melhor.
            estrutural=True)

    # ── 2) Uma hora fora da curva. Vazio espalhado é rotina; vazio concentrado
    # tem CAUSA, e causa tem conserto.
    horas = [h for h in (por_hora or [])
             if isinstance(h.get("desp_pct"), (int, float))
             and h.get("hora") is not None]
    if len(horas) >= 3:
        pior = max(horas, key=lambda h: h["desp_pct"])
        media = sum(h["desp_pct"] for h in horas) / len(horas)
        delta = pior["desp_pct"] - media
        if delta >= 15:
            hh = f"{int(pior['hora']):02d}h"
            add(84, "hora_ruim",
                f"O torno para bem mais por volta das {hh}",
                f"Nessa faixa o posto rende {delta:.0f} pontos abaixo da média "
                "do turno. Uma hora que destoa todo dia costuma ter uma causa "
                "só, e ela quase sempre é de agenda.",
                [f"Descubra o que acontece de rotina por volta das {hh}: troca "
                 "de turno, refeição, reunião, entrega de material ou "
                 "abastecimento.",
                 "Se for algo que vem de fora do posto, remarque para uma hora "
                 "em que o torno já estaria parado de qualquer forma.",
                 "Se for pausa da equipe, escalone: um começa mais cedo e "
                 "outro mais tarde, para a máquina não parar junto."],
                estrutural=False)

    # ── 3) Muito tempo ACOMPANHANDO a máquina. Não é ociosidade — é ciclo
    # automático rodando. O ganho não está em cobrar a pessoa; está em usar um
    # tempo que hoje é só espera.
    acompanhar = _fatia(atividades, _ROTULOS_ACOMPANHAR)
    if acompanhar >= 20:
        add(74, "tempo_de_ciclo",
            f"{acompanhar:.0f}% do turno é o operador acompanhando a máquina",
            "Esse tempo é do ciclo automático, não é parada. Ele está no posto "
            "e atento — o que dá para ganhar é aproveitar o ciclo para "
            "adiantar o que hoje é feito com a máquina parada.",
            ["Liste com o operador o que ele faz HOJE com o torno parado: "
             "medir a peça pronta, preparar o material, conferir o desenho.",
             "O que puder ser feito com a máquina rodando passa para dentro do "
             "ciclo — a peça seguinte fica preparada antes de a atual sair.",
             "Se o ciclo for longo e ele ficar sem o que fazer, avalie se dá "
             "para ele cuidar de uma segunda máquina próxima."],
            estrutural=True)

    # ── 4) Idas e vindas dentro do próprio posto. Diferente de sair: aqui ele
    # não saiu, mas o posto o faz caminhar.
    busca = _fatia(atividades, _ROTULOS_BUSCA)
    if busca >= 8:
        add(70, "arranjo_do_posto",
            f"{busca:.0f}% do turno é o operador andando pelo posto",
            "Deslocamento dentro do posto costuma ser arranjo, não pressa: as "
            "coisas mais usadas ficam longe de onde ele trabalha.",
            ["Fique dez minutos ao lado do torno e anote onde ele vai buscar "
             "cada coisa.",
             "O que ele pega mais de três vezes por hora vem para o alcance do "
             "braço, sem passo nenhum.",
             "Bancada, medidor e ferramenta de troca ficam do mesmo lado em "
             "que ele já está de pé."],
            estrutural=True)

    # ── 5) Conversa no posto. ⚠️ Só entra quando é grande, e enquadrada como
    # INTERRUPÇÃO — o problema é onde as conversas acontecem, não que existam.
    conversa = _fatia(atividades, _ROTULOS_CONVERSA)
    if conversa >= 10:
        add(60, "interrupcoes",
            f"{conversa:.0f}% do turno tem conversa no posto",
            "Boa parte disso é recado de trabalho chegando na hora errada. "
            "Não é sobre proibir conversa — é sobre a conversa não precisar "
            "acontecer com a máquina parada.",
            ["Veja quem procura o operador durante o turno e para quê.",
             "Recados que não são urgentes passam a ser dados na troca de "
             "turno ou na pausa, num momento só.",
             "Se for dúvida técnica que se repete, ela vira instrução escrita "
             "ao lado do torno."],
            estrutural=True)

    # ── 6) A presença caiu em relação aos dias anteriores. Comparar o posto
    # com ele mesmo é mais justo que comparar com uma meta inventada.
    pontos = [s for s in (serie or [])
              if isinstance(s.get("presenca_pct"), (int, float))]
    if len(pontos) >= 3:
        hoje = pontos[-1]["presenca_pct"]
        antes = [s["presenca_pct"] for s in pontos[:-1]]
        media_antes = sum(antes) / len(antes)
        if media_antes - hoje >= 10:
            add(76, "queda",
                f"A presença no posto caiu {media_antes - hoje:.0f} pontos",
                "Comparado com os dias anteriores deste mesmo posto, o "
                "operador esteve bem menos tempo no torno. Queda de um dia "
                "para o outro costuma ter motivo pontual.",
                ["Pergunte o que foi diferente nesse dia: falta de material, "
                 "manutenção, ajuda em outro posto, treinamento.",
                 "Se foi falta de material, veja com o abastecimento o que "
                 "atrasou.",
                 "Se o motivo se repetir em outro dia, deixou de ser evento e "
                 "virou processo — aí vale mudar o fluxo."],
                estrutural=False)

    # ── 7) O MELHOR DIA COMO META. Nem toda sugestão precisa ser problema —
    # e uma meta que veio do próprio chão não se discute: já aconteceu ali.
    presencas = [s.get("presenca_pct") for s in (serie or [])
                 if isinstance(s.get("presenca_pct"), (int, float))]
    if len(presencas) >= 3:
        melhor = max(presencas)
        media = sum(presencas) / len(presencas)
        if melhor - media >= 5:
            add(66, "melhor_dia",
                f"O melhor turno deste posto foi {melhor:.0f}%",
                f"A média fica em {media:.0f}%. A diferença não é sorte: já "
                "aconteceu neste posto, com este operador e esta máquina — "
                "então dá para repetir.",
                ["Descubra com o operador o que foi diferente no melhor dia: "
                 "material já separado, sem troca de ferramenta, sem "
                 "interrupção.",
                 "Escreva o que fez aquele dia funcionar e deixe à vista no "
                 "posto.",
                 "Use esse número como meta do mês — é a única meta que não "
                 "dá para contestar."],
                tom="info", estrutural=True)

        # ── 8) CONSISTÊNCIA. Variar muito entre dias é problema de método, e é
        # mais barato de resolver que ganhar pontos no topo.
        pior = min(presencas)
        if melhor - pior >= 20:
            add(72, "consistencia",
                f"O posto varia {melhor - pior:.0f} pontos entre os dias",
                "Do melhor para o pior turno a diferença é grande. Quando o "
                "mesmo posto e o mesmo operador rendem tão diferente, o que "
                "muda é o que chega até ele, não o esforço.",
                ["Compare o melhor e o pior dia com o operador: o que existia "
                 "num e faltava no outro?",
                 "Padronize o começo do turno — material, ferramenta e desenho "
                 "conferidos antes de a máquina ligar.",
                 "Repita por uma semana e veja se a diferença entre os dias "
                 "diminui."],
                estrutural=True)

    # ── 9) O COMEÇO DO TURNO. A primeira hora costuma ser a mais fácil de
    # corrigir, porque depende de preparação e não de ritmo.
    if len(horas) >= 3:
        horas_ord = sorted(horas, key=lambda h: h["hora"])
        primeira = horas_ord[0]
        resto = horas_ord[1:]
        media_resto = sum(h["desp_pct"] for h in resto) / len(resto)
        if primeira["desp_pct"] - media_resto >= 12:
            add(68, "comeco_do_turno",
                "O turno demora a engrenar",
                f"Na primeira hora o posto rende bem menos que no resto do "
                "dia. Começo lento quase sempre é preparação que só acontece "
                "depois que o turno já começou.",
                ["Veja o que o operador faz na primeira meia hora: procurar "
                 "material, achar o desenho, acertar a ferramenta.",
                 "O que der para deixar pronto na véspera passa para o fim do "
                 "turno anterior.",
                 "Defina qual é a primeira peça do dia antes de o operador "
                 "chegar."],
                estrutural=True)

    itens.sort(key=lambda x: -x["_peso"])
    for x in itens:
        x.pop("_peso", None)
        x.pop("_estrutural", None)
    return itens[:max_itens]

# ═════════════════════════════════════════════════════════════════════════
# Fase 87 — ABRIR O BIN DA JORNADA
#
# A faixa "A jornada de …" é desenhada a partir de buckets de 15 min, e cada
# bucket é FATIADO na proporção de cada categoria. Isso quer dizer uma coisa
# que a tela precisa dizer em voz alta: a largura da fatia é PROPORÇÃO, não
# horário. Uma fatia vermelha desenhada em 09:07–09:10 não afirma que o
# desperdício aconteceu às 09:07 — afirma que, dentro do bloco 09:00–09:15,
# aquele tanto do tempo foi desperdício.
#
# Por isso o clique abre o BLOCO DE 15 MIN inteiro, não a fatia: é a menor
# janela sobre a qual o dado desenhado faz alguma afirmação. Dentro dela vêm
# os eventos de verdade, com hora, rótulo e descrição — que é o que permite
# conferir se a cor bate com o que aconteceu.
#
# Mesmo filtro e mesma conta de `montar_analise_diaria`: se divergirem, o
# detalhe desmente o desenho e as duas telas ficam inúteis.
# ═════════════════════════════════════════════════════════════════════════
BIN_JORNADA_MIN = 15          # tem de ser o MESMO passo do bucket da linha_tempo


def eventos_do_bin(sb: Client, empresa: str, processo: str, dia: str | None,
                   minuto: float, limite: int = 300) -> dict:
    """Os eventos que compõem UM bloco de 15 min da jornada.

    `minuto` é qualquer minuto-do-dia dentro do bloco (a tela manda o ponto
    clicado); o bloco é derivado dele. Só leitura — não toca em validação,
    não entra na fila, não muda categoria.

    `dia=None` = MODO AGREGADO: o mesmo bloco de relógio somado sobre TODOS os
    dias. A faixa "A jornada típica — todos os dias" é desenhada assim, e sem
    isto o clique nela não tinha o que abrir: o gestor via um bloco vermelho no
    padrão do posto e não conseguia perguntar "vermelho por quê?". Cada item
    volta com o DIA a que pertence, porque no agregado a hora sozinha não
    localiza o evento.
    """
    if dia is not None:
        try:
            date.fromisoformat(dia)
        except Exception:
            return {"erro": f"data inválida: {dia!r} (esperado AAAA-MM-DD)"}
    b = int(max(0.0, min(1439.0, float(minuto))) // BIN_JORNADA_MIN)
    jan_ini = b * float(BIN_JORNADA_MIN)
    jan_fim = jan_ini + BIN_JORNADA_MIN

    videos = varrer(sb, "videos", "id, nome, cam_id, duracao_s, gravado_em, processado_em",
                    empresa=empresa, processo=processo)
    # A véspera entra porque o vídeo que começa 23:5x carrega eventos que caem
    # no dia seguinte — e é assim que `montar_analise_diaria` os conta. Quem
    # decide o dia é o instante do EVENTO, não o do vídeo.
    dias_fonte = None if dia is None else {
        dia, (date.fromisoformat(dia) - timedelta(days=1)).isoformat()}
    inicio_por_video: dict[str, datetime] = {}
    meta_video: dict[str, dict] = {}
    for v in videos:
        dt0 = _inicio_video_dt(v)
        if not v.get("id") or not dt0:
            continue
        if dias_fonte is not None and dt0.date().isoformat() not in dias_fonte:
            continue
        inicio_por_video[v["id"]] = dt0
        meta_video[v["id"]] = v
    if not inicio_por_video:
        return {"dia": dia, "agregado": dia is None, "n_dias": 0, "bin": b,
                "de": _hhmm_do_minuto(jan_ini),
                "ate": _hhmm_do_minuto(jan_fim), "n_eventos": 0, "segundos": 0.0,
                "por_categoria": {}, "acoes": [], "itens": [], "truncado": False,
                "nota": ("Nenhum vídeo processado neste processo." if dia is None
                         else "Nenhum vídeo com gravação nesta data.")}

    comps = varrer(sb, "comportamentos", "label, categoria_lean",
                   empresa=empresa, processo=processo)
    cat_por_label = {c["label"]: c.get("categoria_lean") for c in comps}
    _frente = frente_maquina_do_processo(sb, empresa, processo)

    ids = sorted(inicio_por_video)
    eventos: list[dict] = []
    # Filtra pelos vídeos DO DIA no servidor: o bin é uma janela de 15 min, não
    # faz sentido arrastar o processo inteiro para dentro do processo web.
    for i in range(0, len(ids), 200):
        lote = ids[i : i + 200]
        eventos += varrer(
            sb, "eventos",
            "id, video_id, comportamento_label, label_corrigido, descricao_bruta, "
            "tempo_inicio_s, tempo_fim_s, validacao_correto, principal, papel_pessoa, "
            "confianca, em_duvida, validado_humano, origem_validacao, n_amostras, "
            "categoria_lean, categoria_lean_origem, "
            "pessoa_track_id, versao_instrumento",
            empresa=empresa, processo=processo,
            ajustes=lambda q, _l=lote: q.in_("video_id", _l),
        )

    por_cat = {"va": 0.0, "desp": 0.0, "vazio": 0.0}
    acoes: dict[str, dict] = {}
    itens: list[dict] = []
    for e in eventos:
        if e.get("validacao_correto") is False or e.get("principal") is False:
            continue
        dt0 = inicio_por_video.get(e.get("video_id"))
        if dt0 is None:
            continue
        e["_frente_maquina"] = _frente
        label, cat, dur = _cat_do_evento(e, cat_por_label)
        if dur <= 0:
            continue
        inst = dt0 + timedelta(seconds=float(e.get("tempo_inicio_s") or 0))
        dia_do_evento = inst.date().isoformat()
        if dia is not None and dia_do_evento != dia:
            continue
        # Mesma aritmética de `montar_analise_diaria`: o evento é pintado a
        # partir do minuto do relógio em que COMEÇA e é cortado à meia-noite.
        m_ini = inst.hour * 60 + inst.minute + inst.second / 60.0
        m_fim = min(1440.0, m_ini + dur / 60.0)
        ov_min = min(m_fim, jan_fim) - max(m_ini, jan_ini)
        if ov_min <= 0:
            continue
        eh_vazio = (e.get("papel_pessoa") == "posto_vazio") or (label == POSTO_VAZIO_LABEL)
        chave = "vazio" if eh_vazio else ("va" if cat == "valor_agregado" else "desp")
        seg_bin = ov_min * 60.0
        por_cat[chave] += seg_bin
        a = acoes.setdefault(label, {"rotulo": label, "cat": chave,
                                     "segundos": 0.0, "n": 0})
        a["segundos"] += seg_bin
        a["n"] += 1
        v = meta_video.get(e["video_id"]) or {}
        itens.append({
            "id": e.get("id"), "video_id": e.get("video_id"),
            # No agregado, a hora sozinha não localiza o evento: 09:12 acontece
            # em todos os dias. O dia vem junto, sempre.
            "dia": dia_do_evento,
            "video_nome": v.get("nome"), "cam_id": v.get("cam_id"),
            "rotulo": label,
            # O rótulo é o que o cluster decidiu; a descrição é o que o VLM viu.
            # Sem a segunda não dá para saber se a primeira faz sentido.
            "descricao": e.get("descricao_bruta"),
            "cat": chave,
            "corrigido": bool(e.get("label_corrigido")),
            "hora": _hhmm_do_minuto(m_ini, com_segundos=True),
            "hora_fim": _hhmm_do_minuto(m_fim, com_segundos=True),
            "ini": e.get("tempo_inicio_s"), "fim": e.get("tempo_fim_s"),
            "segundos": round(dur, 1),
            "segundos_no_bin": round(seg_bin, 1),
            # Um evento que começa antes ou termina depois do bloco entra só com
            # a fatia dele — dizer isso evita a leitura de que o bloco "tem" um
            # evento de 4 min quando só 40 s caíram aqui dentro.
            "parcial": bool(m_ini < jan_ini - 1e-6 or m_fim > jan_fim + 1e-6),
            "papel": e.get("papel_pessoa"),
            "origem": e.get("origem_validacao"),
            "validado": bool(e.get("validado_humano")),
            "em_duvida": bool(e.get("em_duvida")),
            "confianca": e.get("confianca"),
            "n_amostras": e.get("n_amostras"),
            "track": e.get("pessoa_track_id"),
            "versao_instrumento": int(e.get("versao_instrumento") or 1),
            "_m": m_ini,
        })

    itens.sort(key=lambda x: (x["dia"], x["_m"]))
    dias_vistos = sorted({it["dia"] for it in itens})
    n_total = len(itens)
    for it in itens:
        it.pop("_m", None)
    seg_total = sum(por_cat.values())
    return {
        "dia": dia,
        "agregado": dia is None,
        "dias": dias_vistos,
        "n_dias": len(dias_vistos),
        "bin": b,
        "de": _hhmm_do_minuto(jan_ini),
        "ate": _hhmm_do_minuto(jan_fim),
        "minutos_bin": BIN_JORNADA_MIN,
        "n_eventos": n_total,
        "segundos": round(seg_total, 1),
        "por_categoria": {
            k: {"segundos": round(s, 1),
                "pct": round(s / seg_total * 100, 1) if seg_total else 0.0}
            for k, s in por_cat.items() if s > 0
        },
        "acoes": sorted(
            ({**a, "segundos": round(a["segundos"], 1),
              "pct": round(a["segundos"] / seg_total * 100, 1) if seg_total else 0.0}
             for a in acoes.values()),
            key=lambda a: -a["segundos"],
        ),
        "itens": itens[:limite],
        "truncado": n_total > limite,
        # O bucket com menos de 1 min de cobertura é BURACO no desenho (a
        # `montar_analise_diaria` o pula). O detalhe não pode fingir que há
        # jornada ali — mas também não some, senão o clique parece quebrado.
        "buraco": bool(seg_total < 60),
        "nota": (
            ("Este é o MESMO bloco de relógio somado sobre "
             f"{len(dias_vistos)} dia(s): não é um dia real, é o padrão do "
             "posto naquele horário. Cada trecho abaixo traz o dia dele. "
             if dia is None else "")
            + "As larguras desenhadas dentro do bloco são PROPORÇÃO de tempo, "
              "não horário — a ordem das cores é de desenho. A hora de verdade "
              "de cada trecho está na lista abaixo."),
    }


def _hhmm_do_minuto(m: float, com_segundos: bool = False) -> str:
    m = max(0.0, min(1440.0, float(m)))
    h, resto = int(m // 60), m - int(m // 60) * 60
    if not com_segundos:
        return f"{h:02d}:{int(resto):02d}"
    mi = int(resto)
    return f"{h:02d}:{mi:02d}:{int(round((resto - mi) * 60)) % 60:02d}"


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
        # Fase 110 — sem estas duas, `decidir_permanencia` nunca vê que a
        # categoria daquele evento veio de um HUMANO clicando na árvore, e a
        # decisão dele continua não movendo o número.
        "categoria_lean, categoria_lean_origem, "
        "confianca, em_duvida, duvida_motivo, validado_humano, n_rotulos_no_minuto, "
        # Fase 66: sem `n_amostras` o ramo 'sem_evidencia' nunca disparava
        # aqui (vinha None) e o KPI mostrava 0 para sempre; sem
        # `origem_validacao` não dava para excluir o determinístico;
        # sem `camadas_disparadas` a garantia da sombra caía no fallback.
        # Fase 85: a versão do instrumento entra na análise diária para a tela
        # poder MARCAR o dia em que a medição mudou. Sem isso, a queda de
        # produtividade do deploy seria indistinguível de queda real.
        "n_amostras, origem_validacao, camadas_disparadas, versao_instrumento, "
        "maos_maquina, orientacao, trabalho",
        empresa=empresa, processo=processo,
    )
    eventos = [
        e for e in eventos
        if e.get("validacao_correto") is not False and e.get("principal") is not False
    ]
    # Fase 97: a decisão por permanência precisa saber como esta câmera
    # traduz orientação-para-a-câmera em orientação-para-a-máquina.
    carimbar_frente(eventos, frente_maquina_do_processo(sb, empresa, processo))

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
            # Fase 90: "não olhei" é curva PRÓPRIA, nunca somada à dúvida.
            "nao_observado": 0.0, "nao_observado_gate": 0.0,
            "duvida_resolvida": 0.0, "sem_evidencia_resolvida": 0.0,
            "vazio": 0.0, "visitas": 0, "acoes": defaultdict(float),
            "versoes": set(),
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
        d["versoes"].add(int(e.get("versao_instrumento") or 1))
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
            if _tp == "nao_observado":
                # NÃO entra em `duvida` nem em `sem_evidencia`: o sistema não
                # ficou inseguro, ele não olhou. Misturar as duas estraga a
                # curva que responde se o produto funciona — e faria um corte
                # de orçamento parecer perda de confiança do modelo.
                d["nao_observado"] += dur
                _org = e.get("observacoes_origem") or {}
                if any(k.startswith("repeticao") for k in _org):
                    # A parcela que o TETO DO GATE causou. Se ela crescer, o
                    # teto está agressivo demais — e isso tem de ser visível,
                    # não descoberto por acaso.
                    d["nao_observado_gate"] += dur
            elif _tp == "sem_evidencia":
                d["sem_evidencia"] += dur
                if _resolvida:
                    d["sem_evidencia_resolvida"] += dur
            else:
                d["duvida"] += dur
                if _resolvida:
                    d["duvida_resolvida"] += dur
        if e.get("papel_pessoa") == "visitante":
            d["visitas"] += 1
        # Fase 98: `acao_indefinida` não é atividade — não entra no top de ações.
        if not eh_vazio and evento_conta_no_vocabulario(e):
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
                "nao_observado_pct": 0.0, "nao_observado_gate_pct": 0.0,
                "duvida_resolvida_pct": 0.0, "sem_evidencia_resolvida_pct": 0.0,
                "posto_vazio_s": 0.0, "posto_vazio_pct": 0.0,
                "atipico_vazio": False, "versoes_instrumento": [],
                "n_videos": len(videos_por_dia.get(iso, ())),
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
                # Fase 90 — "NÃO OLHEI", separado de "olhei e não sei". Sobe
                # quando o gate suprime; é o preço da economia, e ele fica na
                # tela em vez de virar queda silenciosa de confiança.
                "nao_observado_pct": round(d["nao_observado"] / tot * 100, 1),
                "nao_observado_gate_pct": round(
                    d["nao_observado_gate"] / tot * 100, 1),
                "posto_vazio_s": round(d["vazio"], 1),
                "posto_vazio_pct": round(vazio_pct, 1),
                # Fase 79: dia quase todo posto vazio ou é falta real, ou é
                # falha grave de detecção. As duas merecem olhada — e nenhuma
                # chamava atenção, porque esses eventos saem da fila por
                # mecanismo e o dia fica invisível.
                "atipico_vazio": bool(vazio_pct >= VAZIO_ATIPICO_PCT),
                # Fase 85: mais de uma versão no mesmo dia = o dia do deploy.
                # A tela marca a quebra; o número dos dois lados não é
                # comparável porque não foi medido com o mesmo instrumento.
                "versoes_instrumento": sorted(d["versoes"]),
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

    # Fase 89: o mapa de movimento APRENDIDO do processo — as células da zona
    # que se mexem sempre são as partes móveis. Só pesa depois de base; até lá
    # o agregado sem peso é mais honesto que um mapa de três vídeos.
    _mapa_mov = carregar_mapa_movimento(sb, empresa, processo, cam_id)
    cam_primaria_efetiva = str(cam_id or "cam1")
    cam_secundaria_efetiva = str(cam_id_secundario or "cam2")
    # C1: com uma lateral válida, o safety roda só depois de cam1 E cam2
    # falharem em estabelecer presença. Sem lateral, roda no frame da cam1.
    tem_posto_secundario_safety = bool(
        video_path_secundario
        and _OPERADOR_FILTRO_ENABLE
        and any(
            info_roi.get("papel") == "posto_operador"
            for info_roi in (rois_contexto_secundario or {}).values()
        )
    )
    identidade_shadow = (
        {
            "observacoes": [], "descritores": [],
            # JPEG por slot existe exclusivamente no on com as três chaves.
            "guardar_frames": bool(AUTORIDADE_111D_CONFIGURADA),
        }
        if (
            _OPERADOR_SEGMENTO_MODO == "sombra"
            or AUTORIDADE_111D_CONFIGURADA
        ) else None
    )
    (amostras, info_video, ids_unicos, descritores_track,
     movimento_por_minuto, grade_movimento) = etapa_detectar_e_amostrar(
        yolo, video_path, intervalo_amostragem_s, rois_contexto, progress_cb,
        cam_id=cam_id, mapa_movimento=_mapa_mov,
        identidade_shadow=identidade_shadow,
        presenca_safety_cam1=not tem_posto_secundario_safety,
    )
    descritores_raw_shadow = (
        [{**d, "cam_id": str(d.get("cam_id") or cam_primaria_efetiva)}
         for d in descritores_track]
        if identidade_shadow is not None else None
    )

    if not amostras:
        if _OPERADOR_SEGMENTO_MODO == "on":
            log.info(
                "[operador-segmento/on] %s",
                json.dumps({
                    "status": "fallback_legado",
                    "motivo": (
                        "sem_amostras" if AUTORIDADE_111D_CONFIGURADA
                        else "configuracao_incompleta"
                    ),
                }, ensure_ascii=False, separators=(",", ":")),
            )
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
    # Fase 86: câmera e torno são fixos, então a relação entre eles é uma
    # CONSTANTE por câmera — configurada na zona da máquina. Sem ela o sistema
    # afirma só a orientação vs câmera, que é objetiva.
    frente_maquina = next(
        (i.get("frente_maquina") for i in rois_contexto.values()
         if i.get("papel") == "maquina" and i.get("frente_maquina")),
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
            identidade_shadow=identidade_shadow,
            cam_id=cam_secundaria_efetiva,
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
            descritores_cam2 = fechar_descritores(
                desc_acc_cam2, _int_cam2, cam_id_secundario, _w2, _h2,
            )
            descritores_track = list(descritores_track) + descritores_cam2
            if identidade_shadow is not None:
                descritores_cam2_shadow = [
                    {**d, "cam_id": cam_secundaria_efetiva}
                    for d in descritores_cam2
                ]
                identidade_shadow["descritores"].extend(descritores_cam2_shadow)
                descritores_raw_shadow.extend(descritores_cam2_shadow)
            log.info("[descritor] cam2 (%s): %d track(s) descritos.",
                     cam_secundaria_efetiva, len(desc_acc_cam2))
        log.info(
            f"[dual-angle] {cam_primaria_efetiva} + {cam_secundaria_efetiva}: "
            f"2º ângulo em {n_sec}/{len(amostras)} amostras"
        )

    # Fase 111B/C: a janela de cada câmera já está completamente fechada aqui.
    # A decisão nasce e morre em memória/log; não toca amostras, papéis,
    # observações, eventos nem persistência. Sem ROI de posto não há eleição,
    # pois fora do modo operador `zona=None` não significa presença no posto.
    resultados_identidade: list[dict] = []
    if _OPERADOR_SEGMENTO_MODO == "sombra" or AUTORIDADE_111D_CONFIGURADA:
        cameras_posto: list[str] = []
        if zona_posto:
            cameras_posto.append(cam_primaria_efetiva)
        if video_path_secundario and any(
            i.get("papel") == "posto_operador"
            for i in (rois_contexto_secundario or {}).values()
        ):
            cameras_posto.append(cam_secundaria_efetiva)
        if _OPERADOR_SEGMENTO_MODO == "sombra":
            _registrar_operador_segmento_sombra(
                descritores_raw_shadow or descritores_track, cameras_posto
            )
        resultados_identidade = _registrar_identidades_segmento_sombra(
            identidade_shadow or {}, cameras_posto,
            duracao_s=float(info_video.get("duracao_s") or 0.0),
        )
    elif _OPERADOR_SEGMENTO_MODO == "on":
        log.info(
            "[operador-segmento/on] %s",
            json.dumps({
                "status": "fallback_legado",
                "motivo": "configuracao_incompleta",
                "tracker": os.environ.get("KV_TRACKER", "").strip().lower(),
                "fora": _FORA_MODO,
            }, ensure_ascii=False, separators=(",", ":")),
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
            "pela cam2, %d por ponte temporal), %d vazios, %d inconclusivos, "
            "%d rebaixados pela cam2, %d vetos safety (%d C3, %d erros)",
            politica, stats_op["slots"], stats_op["presentes"],
            stats_op["resgatados_cam2"], stats_op["pontes"],
            stats_op["vazios"], stats_op["inconclusivos"],
            stats_op["rebaixados"],
            stats_op["safety_vetos"], stats_op["c3_vetos"],
            stats_op["safety_erros"],
        )
        # C4.2: segunda opinião opcional somente depois da confirmação normal.
        # A ausência de CAM2 mantém exatamente o fluxo anterior.
        if video_path_secundario and tem_posto_sec:
            n_c42 = etapa_consenso_multicamera_640(
                amostras,
                video_path,
                video_path_secundario,
                yolo,
                rois_contexto,
                rois_contexto_secundario,
                offset_s=offset_cam2,
            )
            if n_c42:
                log.info(
                    "[presenca-safety/c4.2] %d slot(s) vetado(s) por consenso 640",
                    n_c42,
                )

    # Fase 111D: confirmação causal legada já terminou. Só agora a decisão da
    # janela completa pode assumir slots seguros; em falha, os objetos legados
    # permanecem intactos e o VLM recebe exatamente o caminho anterior.
    if AUTORIDADE_111D_CONFIGURADA:
        resumo_111d = aplicar_identidade_logica_segmento(
            amostras,
            resultados_identidade,
            identidade_shadow or {},
            cam_primaria_efetiva,
        )
        log.info(
            "[operador-segmento/on] %s",
            json.dumps(resumo_111d, ensure_ascii=False, separators=(",", ":")),
        )

    observacoes = etapa_analise_vlm(
        groq_client, amostras, descricao, memoria, progress_cb,
        conhecimento_adquirido=conhecimento,
        zona_posto=zona_posto,
        # Fase 85: o prompt da sequência diz quantos segundos separam as
        # imagens — sem isso o modelo não tem como julgar "parado há quanto".
        intervalo_s=intervalo_amostragem_s,
        frente_maquina=frente_maquina,
        movimento_por_minuto=movimento_por_minuto,
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
    _cache_labels = cache_desc_label(sb, empresa, processo)
    _, catalogo, label_de, origem_de = etapa_clusterizar(
        groq_client, observacoes, descricao, memoria, limiar_auto_validacao, progress_cb,
        conhecimento_adquirido=conhecimento,
        aprendizado_auto=_aprende,
        cache_labels=_cache_labels,
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
                movimento_por_minuto=movimento_por_minuto,
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

    # Fase 89: o mapa aprende DEPOIS de persistir o que importa — se ele
    # falhar, o vídeo já está salvo.
    if grade_movimento:
        acumular_mapa_movimento(sb, empresa, processo, grade_movimento)

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
