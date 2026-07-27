-- ════════════════════════════════════════════════════════════════════════
-- KALIDASH VISION · Schema
-- Rode no SQL Editor do Supabase.
-- ════════════════════════════════════════════════════════════════════════

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
    cam_id text,
    gravado_em timestamptz,
    processado_em timestamptz default now()
);

-- Inbox de segmentos do edge (Fase 6): o edge sobe tudo no storage antes de a
-- plataforma processar; o orquestrador pareia cam1/cam2 por gravado_em e
-- processa 1 por 1. `videos` continua sendo só o que já foi processado.
create table if not exists segmentos (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    storage_path text not null,
    nome text,
    cam_id text,
    gravado_em timestamptz,
    status text default 'pendente',    -- pendente|enfileirado|processando|concluido|erro
    video_id uuid,
    erro text,
    recebido_em timestamptz default now(),
    processado_em timestamptz,
    score numeric,                     -- Fase 22: pontuação de atividade do edge (0-100)
    selecao text                       -- Fase 22: motivo da subida (topk|calibracao|retry)
);

-- Migrações idempotentes p/ bases já existentes (Fase 1 multi-câmera)
alter table videos add column if not exists cam_id text;
alter table videos add column if not exists gravado_em timestamptz;

-- Fase 22 — seleção top-K no edge (auditoria: quais segmentos subiram e por quê)
alter table segmentos add column if not exists score numeric;
alter table segmentos add column if not exists selecao text;

create table if not exists comportamentos (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    label text not null,
    descricao text,
    total_ocorrencias int default 0,
    primeira_observacao timestamptz default now(),
    ultima_observacao timestamptz default now(),
    -- Classificação Lean do comportamento (preenchida pela IA, sobrescrita pelo gestor)
    categoria_lean text,        -- 'valor_agregado' | 'apoio' | 'desperdicio' | null
    categoria_lean_origem text, -- 'ia' | 'humano' | null
    unique (empresa, processo, label)
);

-- Migração idempotente para bases já existentes
alter table comportamentos add column if not exists categoria_lean        text;
alter table comportamentos add column if not exists categoria_lean_origem text;

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
    categoria_lean text,        -- Lean por evento: 'valor_agregado' | 'apoio' | 'desperdicio' | null
    categoria_lean_origem text, -- 'herdado' (do comportamento) | 'aprendido' | 'humano' (override) | null
    criado_em timestamptz default now()
);

alter table eventos add column if not exists categoria_lean        text;
alter table eventos add column if not exists categoria_lean_origem text;

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
    marcada_em timestamptz,
    voltou_apos_realizada boolean not null default false,
    criado_em timestamptz default now()
);
alter table sugestoes_melhoria add column if not exists status text not null default 'pendente';
alter table sugestoes_melhoria add column if not exists marcada_em timestamptz;
alter table sugestoes_melhoria add column if not exists voltou_apos_realizada boolean not null default false;

create table if not exists contexto_processo (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    descricao text,
    area text,
    atualizado_em timestamptz default now(),
    unique (empresa, processo)
);
alter table contexto_processo add column if not exists area text;

-- Perguntas que a IA faz proativamente ao cliente sobre o processo.
-- Ciclo: IA detecta lacuna → cria pergunta → cliente responde no fluxo
-- de validação → resposta é injetada como domínio nos próximos prompts.
create table if not exists perguntas_processo (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    pergunta text not null,
    motivo text,
    comportamentos_relacionados jsonb,
    respostas_rapidas jsonb,                  -- 3 respostas curtas geradas pela LLM (chips)
    status text not null default 'pendente',  -- pendente | respondida | dispensada
    resposta text,
    respondida_em timestamptz,
    criada_em timestamptz default now()
);
alter table perguntas_processo add column if not exists respostas_rapidas jsonb;

-- Turnos de gravação por processo. Consumido pelo runner da borda (Pi)
-- para abrir e fechar a captura RTSP nos horários definidos pelo gestor.
-- intervalos: [{"inicio":"07:00","fim":"12:00"}, {"inicio":"13:00","fim":"17:00"}]
-- dias_semana: ISO 8601 (1=seg .. 7=dom). Pausa = gap entre intervalos.
create table if not exists turnos_processo (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    nome text not null,
    intervalos jsonb not null default '[]'::jsonb,
    dias_semana int[] not null default array[1,2,3,4,5,6,7],
    ativo boolean not null default true,
    criado_em timestamptz default now(),
    atualizado_em timestamptz default now()
);

-- Zonas nomeadas por câmera (Fase 28). Coordenadas NORMALIZADAS [0-1] no
-- ESPAÇO DO VÍDEO ENVIADO (= recorte CAMn_ROI feito pelo edge). O Pi converte
-- para o quadro cheio quando aplica localmente (x_full = roi.x + x*roi.w).
-- papel: 'posto_operador' = onde o operador titular trabalha (máx. 1 ativa
-- por câmera); 'maquina' = área da máquina (contexto, não classifica pessoa);
-- 'interacao' = área onde terceiros interagem com o posto (analisados como
-- visita/interação). Pessoa fora de todas as zonas = ignorada pela análise.
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

-- Fase 28: papel da pessoa no evento ('operador'|'visitante'|'posto_vazio'|null=legado)
alter table eventos add column if not exists papel_pessoa text;
create index if not exists idx_eventos_papel on eventos(papel_pessoa);

-- Prism · conversas e mensagens do chat lateral (persistência + tópicos)
create table if not exists prism_conversas (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    titulo text not null default 'Nova conversa',
    titulo_auto boolean not null default true,  -- true até o usuário renomear
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

-- Insights consolidados de portfólio (por empresa)
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
    tipo text,
    camada text,                           -- temporal | estrutural
    titulo text,
    descricao text,
    comportamentos_relacionados jsonb,
    categoria_relacionada text,
    confianca text,                        -- alta | media | baixa
    relevancia text,                       -- alta | media | info
    recomendacao text,
    evidencia jsonb,
    n_videos_analisados int,
    criado_em timestamptz default now()
);

-- Padrões globais (entre processos da empresa)
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

-- Prism: suporte a conversas globais (escopo='global', processo null)
alter table prism_conversas add column if not exists escopo text not null default 'processo';
alter table prism_conversas alter column processo drop not null;
alter table prism_mensagens alter column processo drop not null;

create index if not exists idx_videos_ctx        on videos(empresa, processo);
create index if not exists idx_segmentos_par     on segmentos(empresa, processo, gravado_em);
create index if not exists idx_segmentos_status  on segmentos(empresa, processo, status);
create index if not exists idx_comportamentos_ctx on comportamentos(empresa, processo);
create index if not exists idx_eventos_ctx       on eventos(empresa, processo);
create index if not exists idx_eventos_video     on eventos(video_id);
create index if not exists idx_eventos_label     on eventos(comportamento_label);
create index if not exists idx_eventos_pessoa    on eventos(pessoa_track_id);
create index if not exists idx_eventos_origem    on eventos(origem_validacao);
create index if not exists idx_sugestoes_ctx     on sugestoes_melhoria(empresa, processo);
create index if not exists idx_contexto_proc     on contexto_processo(empresa, processo);
create index if not exists idx_perguntas_ctx     on perguntas_processo(empresa, processo, status);
create index if not exists idx_turnos_ctx        on turnos_processo(empresa, processo);
create index if not exists idx_prism_conversas_ctx on prism_conversas(empresa, escopo, atualizada_em desc);
create index if not exists idx_prism_mensagens_conv on prism_mensagens(conversa_id, criada_em);
create index if not exists idx_insights_globais_emp on insights_globais(empresa, criado_em desc);
create index if not exists idx_padroes_proc_ctx on padroes_processo(empresa, processo, criado_em desc);
create index if not exists idx_padroes_globais_emp on padroes_globais(empresa, criado_em desc);


-- ════════════════════════════════════════════════════════════════════════
-- RPC transacional: exclui um processo inteiro (todas as tabelas-folha).
-- O backend remove os vídeos do Storage ANTES de chamar isto.
-- ════════════════════════════════════════════════════════════════════════
create or replace function excluir_processo(p_empresa text, p_processo text)
returns void
language plpgsql
as $$
begin
  delete from prism_mensagens    where empresa = p_empresa and processo = p_processo;
  delete from prism_conversas    where empresa = p_empresa and processo = p_processo;
  delete from eventos            where empresa = p_empresa and processo = p_processo;
  delete from sugestoes_melhoria where empresa = p_empresa and processo = p_processo;
  delete from comportamentos     where empresa = p_empresa and processo = p_processo;
  delete from padroes_processo   where empresa = p_empresa and processo = p_processo;
  delete from perguntas_processo where empresa = p_empresa and processo = p_processo;
  delete from turnos_processo    where empresa = p_empresa and processo = p_processo;
  delete from zonas_camera       where empresa = p_empresa and processo = p_processo;
  delete from videos             where empresa = p_empresa and processo = p_processo;
  delete from contexto_processo  where empresa = p_empresa and processo = p_processo;
end;
$$;


-- ════════════════════════════════════════════════════════════════════════
-- ROW LEVEL SECURITY
-- O cliente acessa o banco SOMENTE via API (que usa service_role).
-- Mas habilitamos RLS por defesa em profundidade: se um dia algum cliente
-- usar a chave anônima diretamente, ele só vê linhas da própria empresa.
-- A correspondência entre usuário e empresa vive em auth.users.user_metadata.empresa
-- ════════════════════════════════════════════════════════════════════════
alter table videos              enable row level security;
alter table comportamentos      enable row level security;
alter table eventos             enable row level security;
alter table sugestoes_melhoria  enable row level security;
alter table contexto_processo   enable row level security;
alter table perguntas_processo  enable row level security;
alter table turnos_processo     enable row level security;
alter table prism_conversas     enable row level security;
alter table prism_mensagens     enable row level security;
alter table insights_globais    enable row level security;
alter table padroes_processo    enable row level security;
alter table padroes_globais     enable row level security;
alter table zonas_camera        enable row level security;

-- Função helper: empresa do usuário JWT
create or replace function auth_empresa() returns text
  language sql stable as $$
    select coalesce(
        (auth.jwt() -> 'user_metadata' ->> 'empresa'),
        ''
    );
$$;

-- Policies: cada linha cuja `empresa` bate com a do JWT é visível/editável
do $$
declare t text;
begin
  foreach t in array array['videos','comportamentos','eventos','sugestoes_melhoria','contexto_processo','perguntas_processo','turnos_processo','prism_conversas','prism_mensagens','insights_globais','padroes_processo','padroes_globais','zonas_camera'] loop
    execute format('drop policy if exists %1$s_select on %1$s', t);
    execute format('drop policy if exists %1$s_modify on %1$s', t);
    execute format($p$
      create policy %1$s_select on %1$s
        for select using (empresa = auth_empresa())
    $p$, t);
    execute format($p$
      create policy %1$s_modify on %1$s
        for all using (empresa = auth_empresa())
        with check (empresa = auth_empresa())
    $p$, t);
  end loop;
end $$;


-- ════════════════════════════════════════════════════════════════════════
-- STORAGE BUCKET para vídeos. Crie manualmente na UI do Supabase OU rode:
-- ════════════════════════════════════════════════════════════════════════
-- insert into storage.buckets (id, name, public) values ('videos','videos', false)
--   on conflict (id) do nothing;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 52 — HEARTBEAT DA BORDA (saúde do Pi)
--
-- O Pi roda sozinho dentro da fábrica numa campanha de 30 dias. Sem isto,
-- uma câmera caída / ffmpeg morto / cartão cheio só aparece dias depois como
-- BURACO no dashboard. Esta tabela é o "pulso" que o runner manda; a leitura
-- (GET /processos/{id}/saude) cruza o pulso com `turnos_processo` para
-- distinguir "parado porque é 22h" de "parado porque quebrou".
--
-- Escreve MUITO e lê pouco → retenção agressiva de 7 dias (ver o delete no
-- fim do bloco). O banco free tem 500MB e é para os dados da campanha, não
-- para log de saúde.
-- ════════════════════════════════════════════════════════════════════════
create table if not exists heartbeats_edge (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    device_id text not null,               -- estável por Pi (serial ou UUID em disco)
    runner_versao text,
    estado text not null,                  -- capturando|processando|ocioso|fora_de_turno
    -- [{cam_id, nome, gravando, ultimo_segmento_em, ultimo_segmento_bytes, falhas}]
    -- `gravando` = SEGMENTO CRESCENDO no disco, não "o RTSP respondeu": um
    -- Hikvision pode responder no socket e entregar imagem preta/congelada.
    cameras jsonb not null default '[]'::jsonb,
    disco_livre_gb numeric,
    disco_uso_pct numeric,
    cpu_temp_c numeric,
    uptime_s bigint,
    turno_janela text,                     -- "06:00-11:30" (janela ativa no envio)
    turno_deadline timestamptz,
    recebido_em timestamptz not null default now()
);

-- Leitura é sempre "os últimos N deste processo" → índice casa exatamente.
create index if not exists idx_hb_ctx on heartbeats_edge(empresa, processo, recebido_em desc);
create index if not exists idx_hb_proc_recente on heartbeats_edge(processo, recebido_em desc);
-- Suporte à limpeza por idade.
create index if not exists idx_hb_recebido on heartbeats_edge(recebido_em);

alter table heartbeats_edge enable row level security;

drop policy if exists heartbeats_edge_select on heartbeats_edge;
drop policy if exists heartbeats_edge_modify on heartbeats_edge;
create policy heartbeats_edge_select on heartbeats_edge
    for select using (empresa = auth_empresa());
create policy heartbeats_edge_modify on heartbeats_edge
    for all using (empresa = auth_empresa())
    with check (empresa = auth_empresa());

-- ⚠️ GRANT EXPLÍCITO — obrigatório para projetos criados depois de 30/05/2026,
-- quando o Supabase parou de expor as tabelas de `public` automaticamente à
-- Data API. SEM ISTO a tabela responde VAZIO, sem erro (o pior modo de falha).
grant select, insert, update, delete on table heartbeats_edge to service_role;
grant select on table heartbeats_edge to authenticated;

-- Retenção: 7 dias. O backend também roda este delete periodicamente
-- (throttle de 1x/hora em POST /edge/heartbeat), mas deixamos aqui para poder
-- limpar na mão / agendar via pg_cron se um dia fizer sentido.
delete from heartbeats_edge where recebido_em < now() - interval '7 days';
