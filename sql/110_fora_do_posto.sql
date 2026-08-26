-- Fase 110 — fora do posto != posto vazio.
-- Idempotente: pode ser executado novamente sem backfill de histórico.

alter table eventos
    add column if not exists fora_do_posto text;

alter table eventos
    add column if not exists fora_amostras_zona int;

alter table eventos
    add column if not exists pessoas_cena_cam2 int;

alter table comportamentos
    add column if not exists exige_decisao_humana boolean not null default false;

create index if not exists idx_eventos_fora_do_posto
    on eventos (empresa, processo, fora_do_posto)
    where fora_do_posto is not null;

-- Conferência pós-migração (deve retornar as quatro linhas):
-- select table_name, column_name, data_type, is_nullable, column_default
-- from information_schema.columns
-- where table_schema = 'public'
--   and (table_name, column_name) in (
--       ('eventos', 'fora_do_posto'),
--       ('eventos', 'fora_amostras_zona'),
--       ('eventos', 'pessoas_cena_cam2'),
--       ('comportamentos', 'exige_decisao_humana')
--   )
-- order by table_name, column_name;
