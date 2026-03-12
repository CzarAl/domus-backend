alter table if exists public.mr_actividades
  add column if not exists hora_actividad time null,
  add column if not exists tipo_otro text null;

create index if not exists idx_mr_actividades_hora
  on public.mr_actividades(hora_actividad);
