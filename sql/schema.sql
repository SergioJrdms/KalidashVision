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
    processado_em timestamptz default now()
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
    status text not null default 'pendente',  -- pendente | respondida | dispensada
    resposta text,
    respondida_em timestamptz,
    criada_em timestamptz default now()
);

create index if not exists idx_videos_ctx        on videos(empresa, processo);
create index if not exists idx_comportamentos_ctx on comportamentos(empresa, processo);
create index if not exists idx_eventos_ctx       on eventos(empresa, processo);
create index if not exists idx_eventos_video     on eventos(video_id);
create index if not exists idx_eventos_label     on eventos(comportamento_label);
create index if not exists idx_eventos_pessoa    on eventos(pessoa_track_id);
create index if not exists idx_eventos_origem    on eventos(origem_validacao);
create index if not exists idx_sugestoes_ctx     on sugestoes_melhoria(empresa, processo);
create index if not exists idx_contexto_proc     on contexto_processo(empresa, processo);
create index if not exists idx_perguntas_ctx     on perguntas_processo(empresa, processo, status);


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
  foreach t in array array['videos','comportamentos','eventos','sugestoes_melhoria','contexto_processo','perguntas_processo'] loop
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
