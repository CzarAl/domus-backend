create table if not exists public.mr_actividades (
  id uuid primary key default gen_random_uuid(),
  expediente_id uuid null references public.mr_expedientes(id) on delete set null,
  expediente text not null,
  juzgado text not null,
  tipo text not null default 'general',
  descripcion text not null,
  fecha_actividad date null,
  observaciones text null,
  resultado text null,
  cumplido boolean not null default false,
  fecha_cumplimiento timestamp without time zone null,
  created_at timestamp without time zone not null default now(),
  updated_at timestamp without time zone not null default now()
);

create index if not exists idx_mr_actividades_expediente
  on public.mr_actividades(expediente);

create index if not exists idx_mr_actividades_juzgado
  on public.mr_actividades(juzgado);

create index if not exists idx_mr_actividades_tipo
  on public.mr_actividades(tipo);

create index if not exists idx_mr_actividades_cumplido
  on public.mr_actividades(cumplido);

create index if not exists idx_mr_actividades_fecha
  on public.mr_actividades(fecha_actividad);
