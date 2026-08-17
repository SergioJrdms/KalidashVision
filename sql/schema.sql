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


-- ════════════════════════════════════════════════════════════════════════
-- Fase 69 — MAIS CONTRADIÇÕES LÓGICAS (C1, C2) + duas em SOMBRA.
--
-- A linha que separa ATIVA de SOMBRA aqui é uma só: ATIVA quando os dois
-- sinais NÃO PODEM estar certos ao mesmo tempo; SOMBRA quando é forte
-- suspeita mas existe cenário legítimo. Regra que tem exceção legítima e
-- nasce ativa enche a fila de alarme falso e mata o mecanismo por
-- descrédito — foi o risco levantado quando o placar foi desenhado.
--
-- ⚠️ `operador_presente` só existe em processo que RASTREIA PAPEL (com zona
-- `posto_operador` desenhada). Sem a zona, a chave é OMITIDA do fato e
-- TODAS as regras abaixo ficam quietas — ausência de zona é ausência de
-- informação, não afirmação de que o posto está vazio.
-- ════════════════════════════════════════════════════════════════════════

-- ── C1 · ATIVA — ato do titular sem o titular presente ─────────────────
-- Estes sete rótulos só o operador do posto executa. Se o rastreamento diz
-- que ele NÃO está lá e o rótulo afirma que ele está fazendo isso, é
-- impossível. Confirmado nos dados: `operar_torno` tinha 5 eventos com
-- papel_pessoa='posto_vazio' — a alucinação original, o dia em que o
-- operador faltou e o VLM descreveu alguém operando.
insert into camadas_duvida
    (empresa, processo, nome, quando_rotulo, se, entao, motivo, modo, ordem)
select c.empresa, c.processo,
       'contradicao_ato_do_operador_sem_operador',
       '["operar_torno","monitorar_maquina","ajustar_maquina","preparar_maquina",
         "medir_peca","limpando_cavaco","lendo_desenho_tecnico"]'::jsonb,
       '{"operador_presente": false}'::jsonb,
       'duvida',
       'Este rótulo descreve um ato que só o operador do posto executa, mas o '
       || 'rastreamento não identificou o operador no posto neste minuto. Ou a '
       || 'leitura da ação está errada, ou a presença não foi detectada — '
       || 'confira nos dois ângulos.',
       'ativa', 11
  from contexto_processo c
on conflict (empresa, processo, nome) do update
   set quando_rotulo = excluded.quando_rotulo, se = excluded.se,
       modo = excluded.modo, motivo = excluded.motivo, atualizado_em = now();

-- ── C2 · ATIVA — posto vazio com mão na máquina ────────────────────────
-- Sinal INDEPENDENTE do rastreamento de corpo: pega o caso em que o corpo
-- do operador está ocluso (o ventilador do episódio da Fase 42) mas o punho
-- aparece dentro da zona da máquina. Se há mão na máquina, o posto não está
-- vazio. `maos_na_maquina` só existe onde há zona `maquina` desenhada.
insert into camadas_duvida
    (empresa, processo, nome, quando_rotulo, se, entao, motivo, modo, ordem)
select c.empresa, c.processo,
       'contradicao_posto_vazio_com_maos_na_maquina',
       '["posto_vazio"]'::jsonb,
       '{"maos_na_maquina": true}'::jsonb,
       'duvida',
       'Há mão dentro da zona da máquina neste minuto, mas o rótulo diz que o '
       || 'posto estava vazio. O sinal da mão não depende do rastreamento do '
       || 'corpo — ele enxerga o operador mesmo quando o corpo está ocluso.',
       'ativa', 12
  from contexto_processo c
on conflict (empresa, processo, nome) do update
   set quando_rotulo = excluded.quando_rotulo, se = excluded.se,
       modo = excluded.modo, motivo = excluded.motivo, atualizado_em = now();

-- ── S1 · SOMBRA — conversa sem o titular no posto ──────────────────────
-- Parece exigir operador, mas NÃO exige: um visitante pode conversar na zona
-- do posto com o titular ausente. Fica medindo; o placar decide.
insert into camadas_duvida
    (empresa, processo, nome, quando_rotulo, se, entao, motivo, modo, ordem)
select c.empresa, c.processo,
       'suspeita_conversa_sem_operador',
       '["conversando_colega","interagir_com_colega_ou_lider"]'::jsonb,
       '{"operador_presente": false}'::jsonb,
       'duvida',
       'Rótulo de conversa sem o operador identificado no posto. Pode ser '
       || 'legítimo (dois visitantes conversando na zona) — por isso está em '
       || 'sombra: mede antes de alarmar.',
       'sombra', 20
  from contexto_processo c
on conflict (empresa, processo, nome) do update
   set quando_rotulo = excluded.quando_rotulo, se = excluded.se,
       modo = excluded.modo, motivo = excluded.motivo, atualizado_em = now();

-- ── S2 (rótulo × zona) NÃO FOI ESCRITA, e é decisão, não esquecimento ──
-- Ela precisaria de: (1) um operador novo no motor para "lista vazia"; e
-- (2) saber QUAL zona cada rótulo exige — o nome da zona é do cliente, não
-- do sistema, então "zonas_ocupadas vazia" seria um proxy fraco. Pior: em
-- processo sem zona nenhuma desenhada ela dispararia em todo evento, e
-- mesmo em sombra isso sujaria o placar com ruído que ninguém pediu.
-- Fica para depois da campanha, com o nome das zonas em mãos.

-- Conferência depois de rodar:
--   select nome, modo, quando_rotulo, se from camadas_duvida order by ordem;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 70 — `descricao_invalida`: o VLM ALUCINOU a cena.
--
-- Estado PRÓPRIO, separado de `descartado`, porque são coisas diferentes:
--   • descartado          → não havia ação aqui (falso positivo da detecção);
--   • descricao_invalida  → HAVIA uma cena, e o modelo mentiu sobre ela.
--
-- Os dois saem das métricas. A diferença está nas consequências:
--   • a frase entra na lista de QUEIMADAS e nunca mais funda aprendizado —
--     nem hoje, nem quando o mecanismo declarativo existir. Uma regra
--     fundamentada numa frase que nunca descreveu nada seria falsa;
--   • a contagem vira a TAXA DE ALUCINAÇÃO do VLM, número que precisa ser
--     acompanhado durante a campanha.
--
-- Misturar os dois perderia exatamente o sinal que revelou o contágio de
-- rótulo. (A medição posterior mostrou que aqueles eventos eram de
-- auditoria e correções humanas, e nunca entraram em métrica — mas a
-- contradição em si é real e a camada é a rede que a pega no 1º vídeo.)
--
-- ESCOPO: vale para o EVENTO marcado. A lista de queimadas bloqueia
-- aprendizado FUTURO; nunca reclassifica o passado a partir de uma frase —
-- generalizar por descrição foi a causa raiz e não se repete aqui.
-- ════════════════════════════════════════════════════════════════════════
alter table eventos add column if not exists descricao_invalida boolean not null default false;
create index if not exists idx_eventos_desc_invalida
    on eventos(empresa, processo) where descricao_invalida;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 82 — A CAIXA DA PESSOA VOLTA A SER UMA MEDIDA.
--
-- `bbox_inicio` vinha {x1:0,y1:0,x2:0,y2:0} em todo evento de 'operador' e
-- 'posto_vazio'; só 'visitante' tinha coordenada real. Duas causas, ambas de
-- escrita, nenhuma de leitura:
--
--   1. RESGATE PELA CAM2 — quando a cam1 não vê o operador (atrás do torno) e
--      a cam2 vê, o evento nasce do 2º ângulo. O detector da cam2 CALCULAVA a
--      caixa e ela era jogada fora: o laço guardava só o booleano `achou`.
--      A observação era gravada com (0,0,0,0).
--   2. POSTO VAZIO — não há pessoa, e mesmo assim gravava-se (0,0,0,0).
--
-- Zero não é ausência: é a afirmação de uma pessoa de tamanho nenhum no canto
-- superior esquerdo da imagem. E ela era lida como medida — `montar_fato_evento`
-- somava esse ponto fantasma no cálculo de deslocamento, o que fazia o sinal
-- `movimento` dizer "andando" em minuto de gente parada.
--
-- Agora: caixa que não mede nada → NULL. Ausência declarada.
--
-- `bbox_cam` diz de QUAL câmera são as coordenadas. Sem isso, altura da cam1 e
-- altura da cam2 seriam comparadas como se fossem a mesma régua — e não são:
-- ângulo, distância e resolução diferentes.
--
-- `bbox_stats` é o resumo do corpo no evento (mediana das amostras, não um
-- frame só) com `altura_rel` = altura ÷ altura do frame, que é o que torna a
-- medida comparável entre vídeos e resoluções.
--
-- HISTÓRICO: os zeros antigos ficam como estão. Não se apaga dado de produção
-- no meio de campanha, e a distinção importa — o filtro para qualquer análise é
--   where bbox_inicio is not null
--     and (bbox_inicio->>'y2')::numeric - (bbox_inicio->>'y1')::numeric > 1
-- ════════════════════════════════════════════════════════════════════════
alter table eventos add column if not exists bbox_cam   text;
alter table eventos add column if not exists bbox_stats jsonb;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 83 — DESCRITOR POR TRACK. Insumo do experimento de separabilidade.
--
-- NÃO identifica ninguém e não consolida nada. Guarda, por (video_id,
-- pessoa_track_id), o que a detecção JÁ calculava e jogava fora:
--
--   razoes    — razões entre segmentos rígidos do corpo (ombro/tronco,
--               quadril/ombro, cabeça/tronco), medianas + dispersão + n.
--               Adimensionais: cancelam a distância à câmera, que é o
--               confundidor que a altura aparente sozinha não resolve.
--               Vêm dos keypoints do yolo11n-POSE, que já saem em toda
--               detecção e eram descartados.
--   hist_sup  — histograma de cor HSV (matiz × saturação) da METADE SUPERIOR
--   hist_inf  — idem da METADE INFERIOR (camisa e calça, separadas).
--               V (brilho) fica FORA de propósito: é ele que muda entre a luz
--               das 6h e a das 15h, e essa variação não pode virar "outra
--               pessoa". Faixa central da caixa, para não medir o fundo.
--   altura_rel/aspecto — o que já estava no bbox_stats, por track.
--   tempo_posto_s — tempo estimado do track DENTRO da zona posto_operador
--               (nº de amostras × intervalo). É o sinal de "quem fica", e
--               provavelmente separa titular de visitante melhor que qualquer
--               aparência.
--
-- `cam_id` é obrigatório na leitura: cam1 e cam2 não são a mesma régua.
-- Nunca compare pixels — nem histogramas — entre câmeras diferentes.
--
-- Custo: zero de inferência. Nenhum modelo novo.
-- ════════════════════════════════════════════════════════════════════════
create table if not exists descritores_track (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    video_id uuid references videos(id) on delete cascade,
    pessoa_track_id int not null,
    cam_id text,
    gravado_em timestamptz,
    n_amostras int not null default 0,
    n_amostras_posto int not null default 0,
    tempo_posto_s numeric,
    tempo_visivel_s numeric,
    papel_predominante text,
    altura_rel numeric,
    aspecto numeric,
    razoes jsonb,
    hist_sup jsonb,
    hist_inf jsonb,
    hist_bins jsonb,
    bbox_ref jsonb,          -- caixa NORMALIZADA (0-1) do melhor frame do track
    frame_ref int,
    frame_w int,
    frame_h int,
    criado_em timestamptz default now(),
    unique (video_id, pessoa_track_id)
);
create index if not exists idx_desc_track_ctx
    on descritores_track(empresa, processo, gravado_em);
alter table descritores_track enable row level security;
drop policy if exists descritores_track_select on descritores_track;
drop policy if exists descritores_track_modify on descritores_track;
create policy descritores_track_select on descritores_track
    for select using (empresa = auth_empresa());
create policy descritores_track_modify on descritores_track
    for all using (empresa = auth_empresa()) with check (empresa = auth_empresa());

-- ⚠️ GRANT EXPLÍCITO: sem ele a Data API devolve VAZIO e sem erro.
grant select, insert, update, delete on table descritores_track to service_role;
grant select on table descritores_track to authenticated;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 84 — a CÂMERA entra na chave do descritor.
--
-- O descritor só existia para a câmera PRIMÁRIA. O pareamento elege sempre a
-- de menor id (cam1) para dirigir detecção e tracking; a cam2 entrava apenas
-- como imagem de confirmação — `predict` sem tracker, sem id, e portanto sem
-- nada que pudesse ser chaveado por track. As únicas linhas de cam2 no banco
-- vinham de segmentos processados SOLO, quando a cam2 virava primária por não
-- ter par. Dia 04/08: 90 tracks de cam1 e 4 de cam2, todos de um vídeo só.
--
-- Agora o passe da cam2 usa `track` (mesmo detector, mesmas caixas, mesmo
-- veredito) e produz descritor. Como cam1 e cam2 numeram tracks de forma
-- INDEPENDENTE — as duas têm um track 1 —, a chave única passa a incluir a
-- câmera, senão o upsert de uma sobrescreveria a outra.
-- ════════════════════════════════════════════════════════════════════════
alter table descritores_track add column if not exists segmento_id uuid;
update descritores_track set cam_id = 'cam1' where cam_id is null;
alter table descritores_track alter column cam_id set default 'cam1';
alter table descritores_track alter column cam_id set not null;
alter table descritores_track
    drop constraint if exists descritores_track_video_id_pessoa_track_id_key;
create unique index if not exists uq_desc_track_video_cam
    on descritores_track(video_id, cam_id, pessoa_track_id);


-- ════════════════════════════════════════════════════════════════════════
-- Fase 85 — A VERSÃO DO INSTRUMENTO, carimbada no evento.
--
-- Esta fase muda o instrumento de medição no MEIO de uma campanha de 30 dias.
-- Duas mudanças ligadas:
--   • o VLM passa a julgar uma SEQUÊNCIA de instantes em vez de um frame
--     isolado — "o que aconteceu nestes 60s", não "o que se vê nesta foto";
--   • o prompt para de escolher o rótulo no caso ambíguo. O anterior mandava:
--     "Se ele está PARADO ... é 'monitorando o ciclo da máquina' ... Na dúvida
--     entre operar e monitorar, escolha MONITORAR" — duas saídas, as duas
--     produtivas. Era por isso que a dúvida não tinha para onde ir.
--
-- A produtividade ANTES e DEPOIS não são a mesma medida. Carimbar a versão põe
-- a quebra da série DENTRO DO DADO, consultável, em vez de depender da memória
-- de alguém:
--
--   select versao_instrumento, count(*), round(avg(duracao_s)::numeric,1)
--     from eventos where empresa = ? group by 1;
--
--   1 = instante isolado (até a Fase 85)
--   2 = sequência por minuto, VLM descrevendo em vez de classificar
--
-- O default é 1 porque todo evento que já existe foi medido com o instrumento
-- antigo — e essa é a afirmação verdadeira sobre eles.
-- ════════════════════════════════════════════════════════════════════════
alter table eventos add column if not exists versao_instrumento int default 1;
create index if not exists idx_eventos_versao
    on eventos(empresa, processo, versao_instrumento);


-- ════════════════════════════════════════════════════════════════════════
-- Fase 86 — ORIENTAÇÃO: o sinal vem da pose, a tradução vem da zona.
--
-- "de frente ao torno" virou muleta: aparecia em quase toda descrição do dia,
-- inclusive com o operador de COSTAS para o torno lendo desenho técnico. O VLM
-- não enxerga orientação nessa resolução e preenche com o plausível — o mesmo
-- mecanismo que produzia "monitorando a máquina".
--
-- A orientação em relação à CÂMERA sai dos 17 keypoints do yolo11n-pose e é
-- objetiva (rosto visível = de frente; ombros sem rosto = de costas; ombros
-- colados em x = de perfil). Mas de frente para a CÂMERA não é de frente para
-- o TORNO — e essa tradução é uma constante por câmera, porque câmera e torno
-- são fixos.
--
--   'camera'  : quem está de frente para a câmera está de frente para a máquina
--   'oposta'  : quem está de COSTAS para a câmera está de frente para a máquina
--   'perfil'  : o eixo operador→máquina é perpendicular à câmera (não dá para inferir)
--   null      : não configurado
--
-- SEM configuração o sistema afirma só o que sabe ("de costas para a câmera") e
-- PROÍBE o VLM de afirmar orientação em relação ao torno. Isso já mata a muleta;
-- o campo é refinamento, não pré-requisito.
-- ════════════════════════════════════════════════════════════════════════
alter table zonas_camera add column if not exists frente_maquina text;
alter table zonas_camera drop constraint if exists zonas_frente_maquina_chk;
alter table zonas_camera add constraint zonas_frente_maquina_chk
    check (frente_maquina is null or frente_maquina in ('camera','oposta','perfil'));


-- ════════════════════════════════════════════════════════════════════════
-- Fase 88 — O ESTADO DA MÁQUINA SAI DO RÓTULO E VIRA COLUNA SOB OBSERVAÇÃO
--
-- A Fase 86 particionou o cluster pelo estado da máquina e colou o estado no
-- NOME do rótulo (`monitorar_maquina_ciclo`). Medindo o discriminador contra
-- o próprio dado, ele não mede: em minutos ADJACENTES com a MESMA ação, o
-- estado troca tanto quanto uma moeda com a mesma taxa-base (operar_torno
-- 34,5% observado × 28,7% esperado; monitorar_maquina 41,7% × 30,9%). Estado
-- físico de máquina não se comporta assim. O VLM deduz o estado da ação que
-- ele mesmo descreveu — 76% "ciclo" quando opera, 19% quando monitora — e
-- devolve como se tivesse observado.
--
-- O rótulo AFIRMA, e vai para relatório que o sócio lê. Afirmação errada é
-- mais cara que informação faltando. Então o estado sai do nome e vem para
-- estas colunas: continua coletado, continua analisável, e não afirma nada.
--
-- ⚠️ NENHUM leitor de métrica consome estas colunas, e é de propósito. Elas
-- existem para poder ser CONFRONTADAS com o movimento medido a 6 fps (a
-- próxima fase). Foi a ausência delas que obrigou a análise do discriminador
-- a ser feita lendo string de rótulo.
--
--   select cena_maquina, count(*) from eventos
--    where versao_instrumento >= 4 and principal group by 1;
-- ════════════════════════════════════════════════════════════════════════
alter table eventos add column if not exists cena_maquina text;
alter table eventos add column if not exists cena_imovel boolean;
alter table eventos drop constraint if exists eventos_cena_maquina_chk;
alter table eventos add constraint eventos_cena_maquina_chk
    check (cena_maquina is null or cena_maquina in ('ciclo','parada'));


-- ════════════════════════════════════════════════════════════════════════
-- Fase 88 — O RASTRO DE QUE AS CAMADAS FORAM AVALIADAS
--
-- `camadas_disparadas` NULL queria dizer duas coisas incompatíveis: "rodou e
-- nada disparou" e "nunca rodou". A carga das camadas falha em silêncio
-- (devolve lista vazia em qualquer exceção) e a consolidação pula com um
-- `if camadas:`. Sem separar os dois, silêncio não prova nada — e nenhuma
-- camada é confiável, nem as recém-consertadas.
--
-- Estados que esta coluna separa:
--   null                  → o motor NÃO rodou neste evento
--   {"aplicaveis": []}    → rodou, mas nenhuma regra mira este rótulo. É a
--                           assinatura da regressão da Fase 86: os sufixos
--                           fizeram `quando_rotulo` parar de casar e as
--                           camadas de rótulo nomeado morreram caladas.
--   {"aplicaveis":["X"]}  → X foi perguntada ao fato e não disparou.
--   {"erro": ["X"]}       → X explodiu ao avaliar (≠ não disparou).
--
-- A pergunta que ela responde, e que hoje não tem resposta:
--   select count(*) filter (where camadas_avaliadas is null)          as motor_nao_rodou,
--          count(*) filter (where camadas_avaliadas->'aplicaveis' = '[]') as sem_regra_para_o_rotulo,
--          count(*) filter (where camadas_disparadas is not null)     as disparou
--     from eventos where principal and versao_instrumento >= 4;
-- ════════════════════════════════════════════════════════════════════════
alter table eventos add column if not exists camadas_avaliadas jsonb;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 89 — MOVIMENTO DA MÁQUINA, MEDIDO. EM SOMBRA.
--
-- O discriminador do VLM media ruído (item 11): um torno em ciclo e um parado
-- são IDÊNTICOS num frame, e a diferença é MOVIMENTO. O laço de tracking já
-- decodifica a 6 fps — ~360 pares de frames por minuto contra os 7 da
-- sequência —, com as bboxes do YOLO do mesmo instante para descontar as
-- pessoas. O custo de decodificação já estava pago.
--
-- `movimento_maquina` ∈ (continuo, intermitente, ausente, indisponivel).
-- `indisponivel` NUNCA é `ausente`: zona ocupada por gente, contraste
-- insuficiente ou par descartado por incoerência espacial produzem "não dá
-- para ver", não "a máquina está parada". Ausência de medição não é medição
-- de ausência — a mesma lição do `_mad` devolvendo 0.0 com n=1.
--
-- Guardar o SENSOR ao lado do que o VLM AFIRMOU (`cena_maquina`) é o que
-- torna a discordância mensurável. A calibração se faz pela discordância:
--
--   select * from v_calibracao_movimento order by discordam desc limit 50;
-- ════════════════════════════════════════════════════════════════════════
alter table eventos add column if not exists movimento_maquina text;
alter table eventos add column if not exists movimento_detalhe jsonb;
alter table eventos drop constraint if exists eventos_movimento_chk;
alter table eventos add constraint eventos_movimento_chk
    check (movimento_maquina is null or movimento_maquina in
           ('continuo','intermitente','ausente','indisponivel'));
create index if not exists idx_eventos_movimento
    on eventos(empresa, processo, movimento_maquina)
    where movimento_maquina is not null;


-- Onde a máquina se mexe, célula a célula, acumulado ao longo dos DIAS.
-- Dispensa o dono de desenhar sub-região: as células que se mexem sempre SÃO
-- as partes móveis. Só passa a PESAR depois de KV_MOV_MAPA_MIN_PARES pares —
-- antes disso o agregado sem peso é mais honesto que um mapa de três vídeos.
create table if not exists mapa_movimento (
    empresa text not null,
    processo text not null,
    cam_id text not null default '',
    zona text,
    grade jsonb not null,
    n_pares bigint not null default 0,
    atualizado_em timestamptz not null default now(),
    primary key (empresa, processo, cam_id)
);
alter table mapa_movimento enable row level security;
drop policy if exists mapa_movimento_rw on mapa_movimento;
create policy mapa_movimento_rw on mapa_movimento for all using (true) with check (true);
grant select, insert, update, delete on mapa_movimento to anon, authenticated;


-- ════════════════════════════════════════════════════════════════════════
-- A TELA DE CALIBRAÇÃO, EM SQL. Um minuto por linha: o que o sensor mediu, o
-- que o VLM afirmou, o rótulo que saiu e onde está o vídeo.
--
-- ORDENADA PELA DISCORDÂNCIA, de propósito: é onde se aprende. Concordância
-- não ensina nada — os dois podem estar certos ou errados juntos.
-- ════════════════════════════════════════════════════════════════════════
create or replace view v_calibracao_movimento as
select
    e.empresa, e.processo,
    v.cam_id,
    v.nome                                    as video,
    e.tempo_inicio_s, e.tempo_fim_s,
    e.movimento_maquina                       as sensor,
    e.cena_maquina                            as vlm_afirmou,
    coalesce(e.label_corrigido, e.comportamento_label) as rotulo,
    e.descricao_bruta,
    (e.movimento_detalhe->>'pct_intervalos_com_movimento')::numeric as pct_com_movimento,
    (e.movimento_detalhe->>'pct_zona_ocupada')::numeric             as pct_zona_ocupada,
    (e.movimento_detalhe->>'contraste')::numeric                    as contraste,
    (e.movimento_detalhe->>'pares_validos')::int                    as pares_validos,
    e.movimento_detalhe->'descartados'                              as descartados,
    -- Discordância explícita: sensor sem movimento × VLM afirmando ciclo é o
    -- caso mais informativo, e por isso vem primeiro.
    case
      when e.movimento_maquina = 'ausente'  and e.cena_maquina = 'ciclo'  then 2
      when e.movimento_maquina = 'continuo' and e.cena_maquina = 'parada' then 2
      when e.movimento_maquina = 'intermitente' and e.cena_maquina is not null then 1
      when e.movimento_maquina = 'indisponivel' then 0
      else 0
    end                                       as discordam,
    e.id                                      as evento_id,
    e.video_id
from eventos e
join videos v on v.id = e.video_id
where e.principal and e.movimento_maquina is not null;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 90 — O LEDGER QUE NUNCA EXISTIU
--
-- `ai_uso` está declarada desde a Fase 14 e NUNCA foi criada no banco. A
-- gravação é best-effort (log.warning e segue), então a ausência era
-- silenciosa. Duas consequências que só apareceram quando o saldo acabou:
--   • GET /ai/uso sempre devolveu vazio;
--   • a TRAVA DE ORÇAMENTO (KV_AI_LIMITE_<PROV>_USD) semeia o acumulador
--     lendo esta tabela — ou seja, a trava existia e nunca travou.
--
-- Custo estimado não serve para decidir parar a campanha ou recarregar. Este
-- é o único jeito de trocar uma faixa de "$5,9 a $8,0/dia" por um número.
--
--   select date(ts) dia, tier, modelo, count(*) chamadas,
--          sum(tokens_in) tin, sum(tokens_out) tout, round(sum(custo_usd)::numeric,3) usd
--     from ai_uso group by 1,2,3 order by 1 desc, usd desc;
-- ════════════════════════════════════════════════════════════════════════
create table if not exists ai_uso (
    id uuid primary key default gen_random_uuid(),
    ts timestamptz default now(),
    periodo text not null,
    provedor text not null,
    modelo text,
    tier text,
    tokens_in bigint default 0,
    tokens_out bigint default 0,
    custo_usd numeric(12,6) default 0
);
create index if not exists idx_ai_uso_periodo on ai_uso(periodo, provedor);
create index if not exists idx_ai_uso_ts on ai_uso(ts);
alter table ai_uso enable row level security;
drop policy if exists ai_uso_rw on ai_uso;
create policy ai_uso_rw on ai_uso for all using (true) with check (true);
grant select, insert on ai_uso to anon, authenticated;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 90 — QUADRO OLHADO ≠ MINUTO COBERTO
--
-- Duas perguntas diferentes estavam num contador só:
--   "quanto tempo o minuto cobre?"  → denominador de toda métrica
--   "quantos quadros a gente OLHOU?" → evidência, que vira confiança
--
-- Observação HERDADA (gate disse repetição) ou INTERPOLADA (o quadro não foi
-- enviado ao VLM, ficou entre dois enviados) mantém a continuidade do evento —
-- sem ela o minuto se parte e o `tempo_obs_s` despenca. Mas ela NÃO pode
-- votar na concordância: doze observações com a MESMA descrição herdada dão
-- share 1,00, ou seja, confiança máxima num minuto em que ninguém olhou nada.
-- Número que mente com selo de certeza é pior que número faltando.
--
-- `n_amostras` passa a ser QUADROS EFETIVAMENTE OLHADOS (o que o nome sempre
-- prometeu). `n_observacoes` guarda o total, e `observacoes_origem` guarda a
-- composição — é ela que responde "quantos minutos ficaram sem evidência POR
-- supressão do gate", em vez de o teto agressivo ser descoberto por acaso.
--
--   select count(*) filter (where n_amostras = 0) as nao_olhados,
--          count(*) filter (where (observacoes_origem->>'repeticao_pose')::int > 0) as com_heranca
--     from eventos where principal and versao_instrumento >= 5;
-- ════════════════════════════════════════════════════════════════════════
alter table eventos add column if not exists n_observacoes int;
alter table eventos add column if not exists observacoes_origem jsonb;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 91 — O TITULAR DO POSTO, EM SOMBRA
--
-- O titular NÃO é quem está na zona num instante — é quem DOMINA a presença
-- na zona ao longo do dia. Instante é ruído (o líder passa, o colega encosta);
-- domínio é regime.
--
-- ⚠️ IDENTIDADE ANÔNIMA POR PAPEL, NUNCA CADASTRO DE PESSOA. `titular` guarda
-- um rótulo POSICIONAL (`g1`, `g2`) que vale para UM dia e UMA câmera: o `g1`
-- de hoje não é o `g1` de ontem. Não há nome, não há galeria, não há
-- re-identificação persistente. `assinatura` é o histograma de cor do recorte
-- de referência — ou seja, a ROUPA, que muda. É decisão de LGPD, não de
-- conveniência: para medir o POSTO basta saber que o mesmo alguém dominou o
-- dia.
--
-- SOMBRA: nada aqui altera papel_pessoa, evento ou métrica. Existe para ser
-- conferido a olho antes de valer.
--
--   select dia, cam_id, titular, motivo, n_grupos, minutos_posto_total
--     from titular_dia order by dia desc, cam_id;
-- ════════════════════════════════════════════════════════════════════════
create table if not exists titular_dia (
    empresa text not null,
    processo text not null,
    dia date not null,
    cam_id text not null default '',
    -- null = DIA SEM TITULAR. Não é falha: é a guarda de piso dizendo que
    -- ninguém dominou o posto (o dia em que o operador faltou e um terceiro
    -- usou o torno por dez minutos). Coroar o intruso seria pior.
    titular text,
    motivo text,
    n_grupos int,
    n_tracks int,
    minutos_posto_total numeric,
    grupos jsonb,
    assinatura jsonb,
    atualizado_em timestamptz not null default now(),
    primary key (empresa, processo, dia, cam_id)
);
alter table titular_dia enable row level security;
drop policy if exists titular_dia_rw on titular_dia;
create policy titular_dia_rw on titular_dia for all using (true) with check (true);
grant select, insert, update, delete on titular_dia to anon, authenticated;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 91 — AS DUAS CÂMERAS CONTAM PRESENÇA
--
-- A cam1 era a única fonte de contagem. Num caso medido, a cam2 mostrava DUAS
-- pessoas no posto e a cam1 uma: a segunda não existia para o sistema, e o dia
-- saiu com ZERO eventos de `visitante`.
--
-- Sem casamento entre câmeras: usa o MÁXIMO. Se a cam1 vê 1 e a cam2 vê 2, são
-- PELO MENOS 2 — piso honesto que não exige identidade. Casar tracks entre
-- câmeras é o problema difícil, e resolvê-lo mal produz contagem DUPLA, que é
-- pior que contagem baixa. O offset de relógio já é compensado na amostragem.
--
-- A cam2 NÃO vira fonte de descrição: contar é grátis (o track já roda desde a
-- Fase 84), descrever custaria uma chamada de VLM por pessoa.
-- ════════════════════════════════════════════════════════════════════════
alter table eventos add column if not exists pessoas_posto_cam2 int;
alter table eventos add column if not exists pessoas_so_na_cam2 int;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 92 — AS PONTAS DO TRACK, PARA A COSTURA GEOMÉTRICA
--
-- O experimento da Fase 91 respondeu: aparência sozinha não separa. Medindo
-- operador × visitante (rótulo fraco, mas INDEPENDENTE da cor), a separação de
-- similaridade foi de +0,025 — um limiar precisaria de ~+0,15. E a
-- distribuição é unimodal, sem vale: não existe limiar bom.
--
-- A causa é DURAÇÃO, não ângulo. Track mediano da cam1 = 8 s (o mínimo), com
-- 1-2 amostras por histograma; só 8% dos tracks da cam1 têm alguma razão
-- corporal bem medida. Na cam2 o track mediano dura 48 s e a cobertura sobe
-- para 41% — a MESMA fórmula, com track mais longo.
--
-- Então costura-se por GEOMETRIA antes de olhar aparência: track que termina
-- onde outro começa poucos segundos depois é a mesma pessoa. Pessoa não se
-- teletransporta — é a ponte temporal da Fase 34 aplicada a tracks.
--
-- `t_ini_s`/`t_fim_s` já eram calculados no acumulador e jogados fora;
-- `bbox_ini`/`bbox_fim` são [cx, cy, altura] normalizados (independentes da
-- resolução). `bbox_ref` NÃO serve para isso: ela é o melhor quadro do track,
-- não a borda.
-- ════════════════════════════════════════════════════════════════════════
alter table descritores_track add column if not exists t_ini_s numeric;
alter table descritores_track add column if not exists t_fim_s numeric;
alter table descritores_track add column if not exists bbox_ini jsonb;
alter table descritores_track add column if not exists bbox_fim jsonb;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 94 — O TERCEIRO ESTADO: OPERAÇÃO MANUAL
--
-- O dono abriu o vídeo que a análise apontou (pct_com_movimento=0, VLM dizendo
-- "ciclo") e o SENSOR ESTAVA CERTO: o torno não rodava sozinho — o operador o
-- manipulava manualmente. Trabalho produtivo acontecendo, sem ciclo automático.
--
-- O desenho só tinha dois estados e esse caía como `ausente`, junto com a
-- parada de verdade. Pior: `pct=0` ali não era imobilidade, era PONTO CEGO — a
-- máscara de pessoa removeu exatamente os pixels da manipulação, e a ocupação
-- TOTAL ficou abaixo do teto porque o operador cobre só ~20% da zona.
--
-- Dois campos, de propósito:
--   `movimento_maquina` = o que foi MEDIDO
--   `modo_operacao`     = a COMPOSIÇÃO (medição + mãos na máquina)
-- Colapsar num só impediria, daqui a duas semanas, responder "a composição
-- estava certa?" — que é exatamente a pergunta que não pôde ser feita sobre o
-- ciclo/parada do VLM, por falta de coluna.
--
-- `manual` só é afirmado quando a medição ficou indisponível POR CAUSA das
-- mãos. Compor "ausente + mãos → manual" acertaria o caso do vídeo pela razão
-- errada e quebraria em mão-na-máquina-durante-ciclo.
--
--   select modo_operacao, coalesce(label_corrigido, comportamento_label) rotulo,
--          round(sum(tempo_fim_s-tempo_inicio_s)/60) min
--     from eventos where principal and modo_operacao is not null
--    group by 1,2 order by 1, min desc;
-- ════════════════════════════════════════════════════════════════════════
alter table eventos add column if not exists modo_operacao text;
alter table eventos drop constraint if exists eventos_modo_operacao_chk;
alter table eventos add constraint eventos_modo_operacao_chk
    check (modo_operacao is null or modo_operacao in
           ('automatico','manual','parado','indeterminado'));


-- ════════════════════════════════════════════════════════════════════════
-- Fase 95 — A ÁRVORE DECIDE, E QUEM DECIDIU FICA GRAVADO
--
-- O teto de 75-80% não era resultado: era o único resultado possível. A
-- produtividade vinha do NOME que o VLM dá à ação, e quase todo nome que ele
-- dá é produtivo. Um instrumento que só pode dizer "sim" não mede nada.
--
-- A inversão: o sinal DETERMINÍSTICO decide, e o rótulo vira último recurso.
--   1 presenca    zonas + tracking   ninguém no posto → IMPRODUTIVO
--   2 movimento   sensor a 6 fps     máquina se mexe  → PRODUTIVO
--   3 manual      pose + oclusão     operação manual  → PRODUTIVO
--   4 rotulo      o VLM              nada acima decidiu
--
-- Precedência ABSOLUTA: humano > determinístico > rótulo. Se o sensor vê a
-- máquina trabalhando, o minuto é produtivo mesmo com rótulo
-- `conversando_colega` — o sensor mede o mundo, o rótulo opina sobre ele.
--
-- `decidido_por` é gravado SEMPRE, inclusive com KV_ARVORE_DECIDE desligado:
-- é assim que se compara o antes e o depois no MESMO dado, sem reprocessar.
-- Sem ele, "por que este minuto é produtivo?" volta a não ter resposta — o
-- problema original com outra roupa.
--
--   select decidido_por, count(*), round(sum(tempo_fim_s-tempo_inicio_s)/60) min
--     from eventos where principal group by 1 order by min desc;
-- ════════════════════════════════════════════════════════════════════════
alter table eventos add column if not exists decidido_por text;
alter table eventos add column if not exists maos_maquina boolean;
alter table eventos drop constraint if exists eventos_decidido_por_chk;
alter table eventos add constraint eventos_decidido_por_chk
    check (decidido_por is null or decidido_por in
           ('humano','presenca','movimento','manual','rotulo'));


-- ════════════════════════════════════════════════════════════════════════
-- Fase 97 — A PRODUTIVIDADE VEM DO QUE FOI OBSERVADO
--
-- Decisão dos sócios (12/08): o produto é TEMPO DE PERMANÊNCIA NO POSTO.
-- A cadeia descrição → rótulo → categoria Lean → produtividade tinha duas
-- traduções, cada uma perdendo informação. O caso que fechou a decisão:
-- "parado junto ao torno, máquina parada" virava `acao_indefinida` e saía
-- PRODUTIVO — a descrição certa, o rótulo lixo, a categoria contradizendo a
-- descrição.
--
-- `orientacao` era calculada desde a Fase 86, injetada no prompt e JOGADA
-- FORA. É a TERCEIRA vez que este padrão morde (maquina/imovel na 88,
-- t_ini/t_fim na 92): sinal que só existe em memória não pode ser verificado
-- nem auditado — e agora ela DECIDE produtividade.
--
-- `trabalho` é o julgamento do VLM, no mesmo JSON da descrição (zero chamada
-- nova). NULL é resposta legítima e NUNCA vira produtivo por omissão.
--
--   select orientacao, count(*), round(sum(tempo_fim_s-tempo_inicio_s)/60) min
--     from eventos where principal and maos_maquina group by 1 order by min desc;
-- ════════════════════════════════════════════════════════════════════════
alter table eventos add column if not exists orientacao text;
alter table eventos add column if not exists trabalho boolean;
alter table eventos drop constraint if exists eventos_orientacao_chk;
alter table eventos add constraint eventos_orientacao_chk
    check (orientacao is null or orientacao in ('frente','costas','perfil'));


-- ════════════════════════════════════════════════════════════════════════
-- Fase 98 — A REAVALIAÇÃO É DIAGNÓSTICO, NÃO APRENDIZADO
--
-- Quando o gestor corrige um rótulo, o sistema não sabia POR QUE errou. Duas
-- causas com consertos opostos: a DESCRIÇÃO estava errada e o rótulo apenas a
-- seguiu (o VLM é cego naquele enquadramento), ou a descrição estava CERTA e o
-- rótulo a traiu (o problema é a clusterização).
--
-- ⚠️ VALE SÓ PARA O EVENTO CORRIGIDO. Não propaga por descrição parecida, não
-- vira regra, não entra no vocabulário como canônico. Foi a propagação por
-- descrição que espalhou `conversando_colega` errado na Fase 67 — o `escopo`
-- fica escrito dentro do próprio JSON para quem ler daqui a seis meses.
--
-- Atrás de KV_REAVALIAR_CORRECAO (off). Custo: US$ 0,0033 por correção com 3
-- imagens; US$ 0,33 a cada 100 correções.
--
--   select reavaliacao->>'causa' as causa, count(*)
--     from eventos where reavaliacao is not null group by 1;
-- ════════════════════════════════════════════════════════════════════════
alter table eventos add column if not exists reavaliacao jsonb;


-- ════════════════════════════════════════════════════════════════════════
-- Fase 102 — AMOSTRAGEM CEGA: a taxa de acerto MEDIDA
--
-- "Hoje a estimativa de acerto é impressão, não medida." Esta tabela é o
-- registro do único protocolo que produz um número honesto:
--
--   1. sorteia N eventos de um dia — SORTEIO DE VERDADE, sem filtrar por
--      suspeita. Filtrar por suspeita mede a desconfiança do gestor, não o
--      sistema, e o número sai pessimista por construção.
--   2. mostra os frames SEM a descrição
--   3. o gestor escreve o que vê        → `resposta_humana`
--   4. SÓ ENTÃO revela a descrição      → `revelado_em`
--   5. o gestor marca o veredito        → `veredito`
--
-- ⚠️ A ORDEM É O EXPERIMENTO. Ver a descrição antes de responder contamina a
-- resposta (ancoragem), e o número vira concordância com o que já estava
-- escrito. Por isso `resposta_humana` e `revelado_em` são colunas separadas e
-- gravadas em MOMENTOS separados: o banco guarda a prova de que a ordem foi
-- respeitada, e uma resposta que chegue depois da revelação é descartável.
--
-- ⚠️ TRÊS VEREDITOS, NÃO DOIS. "bate em parte" é informação, não meio-acerto:
-- descrição que acerta a ação e erra o detalhe tem conserto de PROMPT;
-- descrição que inventa a cena tem conserto de CAPTURA. Colapsar os dois numa
-- taxa só apagaria justamente a distinção que diz o que consertar.
-- ════════════════════════════════════════════════════════════════════════
create table if not exists amostragem_cega (
    id uuid primary key default gen_random_uuid(),
    empresa text not null,
    processo text not null,
    dia date not null,
    evento_id uuid not null,
    -- Cópia CONGELADA da descrição no instante do sorteio. Sem ela, um
    -- reprocessamento mudaria o texto e a medição passaria a se referir a algo
    -- que o gestor nunca julgou.
    descricao_no_sorteio text,
    -- Rastro da própria evidência: de quantas amostras analisadas veio a
    -- descrição julgada. É o que permite cruzar acerto × observação.
    n_amostras_no_sorteio int,
    origem_descricao text,
    sorteado_em timestamptz not null default now(),
    resposta_humana text,
    respondido_em timestamptz,
    revelado_em timestamptz,
    -- 'bate' | 'bate_em_parte' | 'nao_bate'
    veredito text,
    veredito_em timestamptz,
    observacao text,
    constraint amostragem_cega_veredito_chk
        check (veredito is null or veredito in ('bate','bate_em_parte','nao_bate'))
);
create index if not exists idx_amostragem_cega_dia
    on amostragem_cega(empresa, processo, dia);
create unique index if not exists idx_amostragem_cega_evento
    on amostragem_cega(empresa, processo, evento_id);
alter table amostragem_cega enable row level security;
drop policy if exists amostragem_cega_rw on amostragem_cega;
create policy amostragem_cega_rw on amostragem_cega for all using (true) with check (true);
grant select, insert, update, delete on amostragem_cega to anon, authenticated;


-- ════════════════════════════════════════════════════════════════════════
-- A NARRATIVA DO MINUTO (KV_NARRATIVA)
--
-- `descricao_bruta` é a frase de UM instante — a primeira do bloco dominante
-- do minuto. O VLM já descreve todos os quadros (uma entrada por imagem), mas
-- o evento guardava só uma e descartava as outras onze. O card de 180s→240s
-- mostrava um instante como se fosse o minuto inteiro.
--
-- `narrativa` é o mesmo minuto contado por inteiro: onde a pessoa estava, o
-- que mudou de um quadro para o outro, o que permaneceu igual. Sem concluir
-- uma ação, sem rótulo, sem julgamento.
--
-- ⚠️ ELA ACOMPANHA, NÃO SUBSTITUI. `descricao_bruta` continua sendo o que
-- alimenta o cluster e o corte do evento dentro do minuto. A narrativa é para
-- o humano ler; a descrição por instante é para a máquina cortar.
--
-- ⚠️ E NÃO DECIDE NADA. Desde a Fase 101 o número vem da permanência, que não
-- lê descrição nenhuma.
--
-- Sem esta coluna o sistema continua funcionando: a ingestão detecta a
-- ausência, regrava o lote sem o campo e segue. A narrativa passa a aparecer
-- sozinha assim que a coluna existir, sem redeploy.
-- ════════════════════════════════════════════════════════════════════════
alter table eventos add column if not exists narrativa text;
