create table if not exists public.mr_juzgados_catalogo (
  id uuid primary key default gen_random_uuid(),
  ciudad text not null,
  distrito_judicial text not null,
  nombre_juzgado text not null,
  label text not null unique,
  normalizado text not null unique,
  created_at timestamp without time zone not null default now()
);

create index if not exists idx_mr_juzgados_catalogo_normalizado
  on public.mr_juzgados_catalogo(normalizado);

create index if not exists idx_mr_juzgados_catalogo_ciudad
  on public.mr_juzgados_catalogo(ciudad);

create index if not exists idx_mr_juzgados_catalogo_distrito
  on public.mr_juzgados_catalogo(distrito_judicial);
