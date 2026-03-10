alter table if exists public.productos
  add column if not exists categoria text,
  add column if not exists slug text,
  add column if not exists visible_publico boolean not null default true,
  add column if not exists destacado boolean not null default false,
  add column if not exists origen_catalogo text not null default 'manual',
  add column if not exists imagenes_extra jsonb not null default '[]'::jsonb;

create unique index if not exists idx_productos_empresa_slug_unique
  on public.productos (id_empresa, slug)
  where slug is not null;

create index if not exists idx_productos_publicos_empresa
  on public.productos (id_empresa, visible_publico, activo);

alter table if exists public.clientes
  add column if not exists tipo_persona text,
  add column if not exists razon_social text,
  add column if not exists requiere_factura boolean not null default false,
  add column if not exists tipo_entrega_preferida text,
  add column if not exists ciudad_envio text,
  add column if not exists costo_envio_estimado numeric not null default 0,
  add column if not exists requiere_logistica boolean not null default false;


alter table if exists public.ventas
  add column if not exists origen_venta text not null default 'fisica';

create index if not exists idx_ventas_origen_venta
  on public.ventas (origen_venta);
