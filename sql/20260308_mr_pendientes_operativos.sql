alter table if exists public.mr_pendientes
  add column if not exists juzgado text,
  add column if not exists fecha_pendiente date,
  add column if not exists fecha_reprogramada date,
  add column if not exists actividad_relacionada text,
  add column if not exists responsable text;

alter table if exists public.mr_pendientes
  add column if not exists estado text default 'pendiente',
  add column if not exists resultado text,
  add column if not exists realizado boolean default false,
  add column if not exists resuelto_en timestamp without time zone,
  add column if not exists fecha_creacion timestamp without time zone default now();

create index if not exists idx_mr_pendientes_fecha_pendiente on public.mr_pendientes(fecha_pendiente);
create index if not exists idx_mr_pendientes_estado on public.mr_pendientes(estado);
