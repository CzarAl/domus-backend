-- Clientes: agregar RFC y CP
alter table if exists public.clientes
    add column if not exists rfc text,
    add column if not exists codigo_postal text;
