alter table if exists public.usuarios
  add column if not exists nombre text,
  add column if not exists username text,
  add column if not exists permisos_portal jsonb not null default '{}'::jsonb;

create unique index if not exists idx_usuarios_username_unique
  on public.usuarios (lower(username))
  where username is not null;
