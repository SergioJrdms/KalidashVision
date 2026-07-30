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


-- ════════════════════════════════════════════════════════════════════════
-- Fase 54 — EXPIRAÇÃO DO BINÁRIO DE VÍDEO (cache de frames aquecido antes)
--
-- A campanha de 30 dias roda no free tier (1GB de Storage). O vídeo nunca era
-- apagado → o bucket estourava em ~2 dias. Agora o cache de frames é AQUECIDO
-- no fim do processamento (com o arquivo ainda no disco do worker, egress
-- adicional = ZERO) e só então o binário é removido. A LINHA em `videos`
-- permanece: some o arquivo, não o dado.
--
-- ⚠️ NUNCA apagar por prefixo de diretório: os JPEGs de `__frames/` moram no
-- MESMO bucket e são a evidência permanente. Toda limpeza opera sobre os
-- caminhos REGISTRADOS (videos.caminho / segmentos.storage_path).
-- ════════════════════════════════════════════════════════════════════════
alter table videos    add column if not exists frames_aquecidos_em timestamptz;
alter table videos    add column if not exists video_removido_em   timestamptz;
-- O 2º ângulo (cam2) é outro objeto e NÃO tem linha em `videos`; sem este
-- carimbo, metade do bucket continuaria crescendo num setup de 2 câmeras.
alter table segmentos add column if not exists storage_removido_em timestamptz;

-- A varredura filtra por estas colunas — sem índice ela varre a tabela inteira.
create index if not exists idx_videos_expirar
    on videos(empresa, video_removido_em, frames_aquecidos_em);

-- ⚠️ GRANT EXPLÍCITO (projeto criado depois de 30/05/2026, quando o Supabase
-- parou de expor `public` automaticamente à Data API). Sem grant, a leitura
-- volta VAZIA e sem erro — o pior modo de falha possível.
grant select, insert, update, delete on table videos    to service_role;
grant select, insert, update, delete on table segmentos to service_role;
grant select on table videos    to authenticated;
grant select on table segmentos to authenticated;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 55 — PROPAGAÇÃO da categoria Lean: comportamento → eventos
--
-- A categoria nasce no COMPORTAMENTO (IA ou gestor) e precisa descer para os
-- EVENTOS. Antes isso só acontecia no caminho humano, e mesmo lá com escopo
-- errado — a IA classificava depois dos eventos existirem e eles ficavam para
-- trás (336 eventos de `operar_torno` sem categoria, por exemplo).
--
-- PRECEDÊNCIA (inviolável):  humano (no evento) > aprendido > herdado
-- Só recebem escrita eventos com categoria_lean NULL ou origem 'herdado'.
-- ════════════════════════════════════════════════════════════════════════
-- A propagação filtra por (empresa, processo, label efetivo). O índice antigo
-- não tinha `processo` e a busca por `label_corrigido` não tinha índice nenhum
-- — numa tabela recebendo escrita da campanha, isso é varredura cara.
create index if not exists idx_eventos_lean_prop
    on eventos(empresa, processo, comportamento_label);
create index if not exists idx_eventos_lean_prop_corrigido
    on eventos(empresa, processo, label_corrigido)
    where label_corrigido is not null;

-- Backfill idempotente (o endpoint POST .../manutencao/lean/propagar faz o
-- mesmo, com relatório e dry-run). Respeita a precedência: nunca toca em
-- evento com origem 'humano' ou 'aprendido'.
update eventos e
   set categoria_lean        = c.categoria_lean,
       categoria_lean_origem = 'herdado'
  from comportamentos c
 where c.empresa  = e.empresa
   and c.processo = e.processo
   and c.label    = coalesce(e.label_corrigido, e.comportamento_label)
   and c.categoria_lean is not null
   and (e.categoria_lean is null or e.categoria_lean_origem = 'herdado')
   and (e.categoria_lean is distinct from c.categoria_lean
        or e.categoria_lean_origem is distinct from 'herdado');

-- ⚠️ GRANT EXPLÍCITO (projeto pós-30/05/2026: `public` não é mais exposto
-- automaticamente à Data API — sem grant a leitura volta VAZIA e sem erro).
grant select, insert, update, delete on table eventos         to service_role;
grant select, insert, update, delete on table comportamentos  to service_role;
grant select on table eventos        to authenticated;
grant select on table comportamentos to authenticated;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 57 — CAMADAS DE DÚVIDA (regras declarativas) + PLACAR POR CAMADA
--
-- Verificações determinísticas e baratas (CPU, ZERO chamada extra ao VLM) que
-- confrontam o rótulo com o que a cena mostra. Quando contradizem, o evento
-- NÃO é corrigido — é marcado como DÚVIDA. A máquina não sabe qual lado está
-- certo; quem sabe é o humano.
--
-- ⚠️ As camadas são DADOS, não código: o dono do processo escreve a décima
-- regra sem deploy. Se cada regra exigisse mexer em Python, o desenvolvedor
-- vira gargalo e o mecanismo morre por atrito.
--
-- MODO SOMBRA: a camada roda e CONTA quantas vezes dispararia, sem marcar
-- dúvida nenhuma. É o que permite propor uma regra e medir o impacto ANTES de
-- ligar, sem contaminar a campanha de 30 dias em andamento.
-- ════════════════════════════════════════════════════════════════════════
create table if not exists camadas_duvida (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    nome text not null,                     -- identificador legível, único por processo
    -- Lista de rótulos em que a camada se aplica. ["*"] = todos.
    quando_rotulo jsonb not null default '["*"]'::jsonb,
    -- Condição declarativa. Objeto simples = AND entre as chaves; combinadores
    -- "e" / "ou" / "nao" aninháveis. Ex.: {"pessoas_na_cena": {"<=": 1}}
    se jsonb not null,
    entao text not null default 'duvida',   -- só 'duvida' por ora (nunca corrige sozinha)
    motivo text,                            -- texto mostrado ao validador
    modo text not null default 'sombra',    -- 'ativa' | 'sombra' | 'off'
    ordem int not null default 100,
    criado_em timestamptz default now(),
    atualizado_em timestamptz default now(),
    constraint camadas_duvida_modo_chk check (modo in ('ativa','sombra','off')),
    constraint camadas_duvida_entao_chk check (entao in ('duvida'))
);
create unique index if not exists idx_camadas_nome on camadas_duvida(empresa, processo, nome);
create index if not exists idx_camadas_ctx on camadas_duvida(empresa, processo, modo);

-- O evento carrega QUAIS camadas levantaram a dúvida — sem isso não há placar,
-- e na vigésima camada ninguém sabe quais valem a pena.
-- Formato: [{"nome": "...", "modo": "ativa|sombra", "motivo": "..."}]
alter table eventos add column if not exists camadas_disparadas jsonb;
alter table eventos add column if not exists em_duvida boolean not null default false;
alter table eventos add column if not exists duvida_motivo text;
create index if not exists idx_eventos_duvida on eventos(empresa, processo, em_duvida);

alter table camadas_duvida enable row level security;
drop policy if exists camadas_duvida_select on camadas_duvida;
drop policy if exists camadas_duvida_modify on camadas_duvida;
create policy camadas_duvida_select on camadas_duvida
    for select using (empresa = auth_empresa());
create policy camadas_duvida_modify on camadas_duvida
    for all using (empresa = auth_empresa()) with check (empresa = auth_empresa());

-- ⚠️ GRANT EXPLÍCITO (projeto pós-30/05/2026: sem grant a Data API devolve
-- VAZIO e sem erro — o pior modo de falha).
grant select, insert, update, delete on table camadas_duvida to service_role;
grant select on table camadas_duvida to authenticated;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 58 — LIMIAR DE DÚVIDA POR PROCESSO
-- Medido nos dados (198 eventos): abaixo de 0.65 estão os empates reais.
-- Configurável por processo porque cada operação tem seu próprio nível de
-- ambiguidade. NULL = usa KV_DUVIDA_LIMIAR (default 0.65).
-- O corte é aplicado na LEITURA, nunca gravado no evento: ajustar o limiar
-- vale na hora, inclusive para os 30 dias já processados.
-- ════════════════════════════════════════════════════════════════════════
alter table contexto_processo add column if not exists duvida_limiar numeric;
grant select, insert, update, delete on table contexto_processo to service_role;
grant select on table contexto_processo to authenticated;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 59 — sinais do minuto que EXPLICAM a dúvida na fila.
-- A consolidação já os calculava (Fase 56/B1) e eles se perdiam na hora de
-- gravar: sem eles, o motivo mostrado ao validador fica sem "quantos rótulos
-- disputaram" e sem a lista de concorrentes.
-- ════════════════════════════════════════════════════════════════════════
alter table eventos add column if not exists concordancia        numeric;
alter table eventos add column if not exists n_rotulos_no_minuto int;
alter table eventos add column if not exists rotulos_competindo  jsonb;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 61 — A MÁQUINA NÃO ESCREVE VERDADE HUMANA.
--
-- `origem_validacao='correcao_aprendida'` marcava validado_humano=true e
-- validacao_correto=true sem nenhum limiar: UMA correção humana generalizava
-- para toda descrição idêntica e a máquina assinava o resultado como se fosse
-- decisão de gente. Isso destrói a verdade de referência do dataset dos 30
-- dias e o placar das camadas, que medem contra o julgamento humano.
--
-- Este bloco devolve esses eventos à fila. IDEMPOTENTE (na segunda passada o
-- filtro validado_humano=true não casa com nada).
--
-- PRESERVA `origem_validacao`: ela deixa de significar "validado" e passa a
-- significar "rótulo proposto por" — é o que o validador precisa saber.
--
-- NÃO TOCA em:
--   • 'humano'     → decisão da pessoa, inviolável;
--   • 'auditoria'  → secundários (principal=false) marcados de propósito para
--                    ficar FORA da fila; validado_humano=true é o mecanismo;
--   • 'posto_vazio'→ determinístico, sem VLM, mesmo mecanismo.
--
-- Confira antes de rodar (deve bater com o que o dashboard chama de "auto"):
--   select origem_validacao, count(*)
--     from eventos
--    where validado_humano = true
--    group by 1 order by 2 desc;
-- ════════════════════════════════════════════════════════════════════════
update eventos
   set validado_humano   = false,
       validacao_correto = null,
       validado_em       = null
 where origem_validacao = 'correcao_aprendida'
   and validado_humano  = true;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 62 — GENERALIZAÇÃO AUTOMÁTICA: chave de liga/desliga por processo.
--
-- Durante a campanha de 30 dias o objetivo é um DATASET LIMPO rotulado por
-- gente. Com cinco mecanismos de aprendizado sobrepostos, ninguém consegue
-- prever o efeito de corrigir um evento — e aprender sobre dado ainda sujo,
-- no meio da coleta, destrói o ativo que a campanha existe para produzir.
--
-- NULL = herda o default do ambiente (KV_APRENDIZADO_AUTO, hoje 'off').
-- Isto é uma CHAVE, não uma remoção: o código dos mecanismos continua todo
-- no lugar, e religar é um UPDATE.
--
-- Cobre:     correcao_aprendida · vocabulario_canonico ·
--            precedente Lean humano de outro processo ·
--            propagação Lean para processos irmãos.
-- NÃO cobre: classificação Lean pela IA (é o trabalho do sistema, e sai
--            marcada 'ia') · vocabulário no prompt do cluster (sugere NOMES,
--            não valida nada — sem ele o mesmo comportamento ganharia três
--            nomes ao longo dos 30 dias, o que suja o dataset).
-- ════════════════════════════════════════════════════════════════════════
alter table contexto_processo add column if not exists aprendizado_automatico boolean;

-- Desliga em TODOS os processos existentes durante a campanha.
-- Para religar depois:  update contexto_processo set aprendizado_automatico = true;
update contexto_processo set aprendizado_automatico = false
 where aprendizado_automatico is distinct from false;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 62 — vocabulario_canonico também deixa de assinar verdade humana.
--
-- Contagem que motivou a decisão (eventos com validado_humano = true):
--   auditoria 907 · vocabulario_canonico 122 · humano 120 · posto_vazio 71.
-- Metade da "verdade humana" era a máquina assinando por si mesma. Os 122
-- voltam para a fila; os 120 de origem 'humano' ficam intocados.
--
-- Mesmo racional do bloco da Fase 61 — e as mesmas proteções: 'humano',
-- 'auditoria' e 'posto_vazio' não são tocados (as duas últimas usam
-- validado_humano=true apenas como mecanismo para ficar FORA da fila).
-- IDEMPOTENTE.
-- ════════════════════════════════════════════════════════════════════════
update eventos
   set validado_humano   = false,
       validacao_correto = null,
       validado_em       = null
 where origem_validacao = 'vocabulario_canonico'
   and validado_humano  = true;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 63 — "NÃO CLASSIFICADO" DEIXA DE EXISTIR.
--
-- Todo tempo observado passa a ser produtivo ou não-produtivo. Não há
-- terceira fatia, nem no banco, nem nas métricas, nem na tela.
--
-- A regra, quando falta evidência, é a convenção Lean: o ônus da prova é de
-- quem afirma que a atividade agrega valor. Sem prova, é NÃO-PRODUTIVO —
-- conservador na direção certa, porque nunca infla a produtividade que o
-- cliente leva para a diretoria.
--
-- O que substitui o cinza é a DÚVIDA DECLARADA: a decisão sem evidência
-- fica marcada com `categoria_lean_origem = 'fallback'`, e é essa marca que
-- joga o trecho para a fila de dúvidas. A pergunta deixa de ser "quanto está
-- sem classificar?" (que ninguém respondia) e passa a ser "de quanto eu
-- ainda não tenho certeza?" — mesma informação, agora acionável.
--
-- ⚠️ NÃO toca em `categoria_lean_origem = 'humano'`: decisão da pessoa é
-- inviolável, inclusive a de deixar algo como está.
--
-- Confira o tamanho do backfill antes de rodar:
--   select coalesce(categoria_lean,'(nulo)') as cat,
--          coalesce(categoria_lean_origem,'(nula)') as origem, count(*)
--     from comportamentos group by 1,2 order by 3 desc;
-- ════════════════════════════════════════════════════════════════════════

-- 1) Comportamentos sem categoria → não-produtivo, marcado como assumido.
update comportamentos
   set categoria_lean        = 'desperdicio',
       categoria_lean_origem = 'fallback'
 where categoria_lean is null
   and coalesce(categoria_lean_origem, '') <> 'humano';

-- 2) `acao_indefinida` era zerada de propósito até a Fase 62. Agora entra na
--    mesma regra: não-produtivo por convenção, e vai para a fila de dúvidas.
update comportamentos
   set categoria_lean        = 'desperdicio',
       categoria_lean_origem = 'fallback'
 where label = 'acao_indefinida'
   and coalesce(categoria_lean_origem, '') <> 'humano';

-- 3) Eventos sem categoria herdam a do comportamento. A precedência da Fase
--    55 continua valendo: só escreve em quem está NULL ou é 'herdado'.
update eventos e
   set categoria_lean        = c.categoria_lean,
       categoria_lean_origem = 'herdado'
  from comportamentos c
 where c.empresa = e.empresa
   and c.processo = e.processo
   and c.label = coalesce(e.label_corrigido, e.comportamento_label)
   and c.categoria_lean is not null
   and (e.categoria_lean is null or e.categoria_lean_origem = 'herdado')
   and e.categoria_lean is distinct from c.categoria_lean;

-- Sobrou algo sem categoria? Deve devolver 0 linhas.
--   select count(*) from comportamentos where categoria_lean is null;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 65 — FUSO DA FÁBRICA (o relógio de parede não é o do servidor).
--
-- O painel de saúde usava o fuso do SERVIDOR. No Render o container roda em
-- UTC e a fábrica está em UTC−3, então:
--   • a faixa de 24h aparecia 3h deslocada (parecia ter começado às 03h
--     quando a gravação começou às 06h);
--   • o turno era comparado contra o relógio errado: às 11h da fábrica
--     (14h UTC) o painel dizia "em repouso" com o Pi gravando.
--
-- O Pi decide o turno pelo relógio DELE. Para o painel concordar com a
-- realidade, o backend precisa do mesmo relógio.
--
-- NULL = usa KV_TZ (default 'America/Sao_Paulo').
-- Nome IANA — o endpoint PUT /processos/{id}/fuso valida antes de gravar,
-- porque fuso errado não dá erro em lugar nenhum: só faz o painel mentir.
-- ════════════════════════════════════════════════════════════════════════
alter table contexto_processo add column if not exists fuso_horario text;

-- Deixa explícito nos processos existentes (idempotente).
update contexto_processo set fuso_horario = 'America/Sao_Paulo'
 where fuso_horario is null;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 68 — A PRIMEIRA CAMADA DE DÚVIDA, e ela é DADO, não palpite.
--
-- `papel_pessoa` vem do rastreamento + zonas: determinístico, não passa pelo
-- VLM. Quando ele diz 'operador' (alguém identificado NO POSTO) e o rótulo
-- diz `posto_vazio`, os dois não podem estar certos ao mesmo tempo. Não é
-- ambiguidade a calibrar — é contradição lógica. Por isso entra em modo
-- ATIVA, não sombra: não há limiar a ajustar numa impossibilidade.
--
-- Foi assim que o contágio por descrição (Fase 67) apareceu: 80 eventos,
-- 25,5 min, de 27/07 a 30/07, com o operador rastreado no posto e o rótulo
-- dizendo que o posto estava vazio — e o VLM descrevendo "monitorando o
-- ciclo da máquina", ou seja, dois sinais independentes contradizendo o
-- terceiro, que era o que vinha do mapa envenenado.
--
-- A camada NÃO corrige nada (`entao='duvida'` é o único valor permitido):
-- ela põe o trecho na fila com o motivo visível. É o alarme que faltava.
-- ════════════════════════════════════════════════════════════════════════
insert into camadas_duvida
    (empresa, processo, nome, quando_rotulo, se, entao, motivo, modo, ordem)
select c.empresa, c.processo,
       'contradicao_posto_vazio_com_operador',
       '["posto_vazio"]'::jsonb,
       '{"operador_presente": true}'::jsonb,
       'duvida',
       'O rastreamento identificou o OPERADOR no posto neste minuto, mas o '
       || 'rótulo diz que o posto estava vazio. Os dois não podem estar '
       || 'certos: a presença vem das zonas e do rastreamento, sem passar '
       || 'pelo modelo de visão.',
       'ativa',
       10
  from contexto_processo c
on conflict (empresa, processo, nome) do update
   set se    = excluded.se,
       modo  = excluded.modo,
       motivo = excluded.motivo,
       atualizado_em = now();
