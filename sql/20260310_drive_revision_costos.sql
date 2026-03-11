create table if not exists public.catalogo_drive_fuentes (
  id uuid primary key default gen_random_uuid(),
  id_empresa uuid not null,
  nombre text not null,
  folder_id text not null,
  proveedor text,
  activa boolean not null default true,
  ultima_sincronizacion timestamp without time zone,
  ultimo_resumen jsonb not null default '{}'::jsonb,
  fecha_creacion timestamp without time zone not null default now(),
  fecha_actualizacion timestamp without time zone not null default now()
);

create unique index if not exists idx_catalogo_drive_fuentes_empresa_folder
  on public.catalogo_drive_fuentes(id_empresa, folder_id);

create table if not exists public.catalogo_drive_items (
  id uuid primary key default gen_random_uuid(),
  id_empresa uuid not null,
  id_fuente uuid not null references public.catalogo_drive_fuentes(id) on delete cascade,
  producto_id uuid,
  drive_file_id text not null,
  drive_parent_id text,
  drive_parent_name text,
  categoria text,
  nombre_archivo text not null,
  mime_type text,
  web_view_link text,
  modified_time text,
  size_bytes bigint not null default 0,
  signature text,
  extracted_data jsonb not null default '{}'::jsonb,
  estado_sync text not null default 'vigente',
  last_seen_at timestamp without time zone,
  synced_at timestamp without time zone,
  fecha_creacion timestamp without time zone not null default now()
);

create unique index if not exists idx_catalogo_drive_items_empresa_file
  on public.catalogo_drive_items(id_empresa, drive_file_id);

create index if not exists idx_catalogo_drive_items_fuente_estado
  on public.catalogo_drive_items(id_fuente, estado_sync);

create table if not exists public.catalogo_drive_revisiones (
  id uuid primary key default gen_random_uuid(),
  id_empresa uuid not null,
  id_fuente uuid not null references public.catalogo_drive_fuentes(id) on delete cascade,
  drive_item_id uuid not null references public.catalogo_drive_items(id) on delete cascade,
  producto_id uuid,
  tipo_cambio text not null,
  titulo text,
  detalle text,
  datos_anteriores jsonb not null default '{}'::jsonb,
  datos_propuestos jsonb not null default '{}'::jsonb,
  estado_revision text not null default 'pendiente',
  fecha_detectada timestamp without time zone not null default now(),
  fecha_resuelta timestamp without time zone
);

create index if not exists idx_catalogo_drive_revisiones_empresa_estado
  on public.catalogo_drive_revisiones(id_empresa, estado_revision, fecha_detectada desc);

create table if not exists public.catalogo_costos_proveedor (
  id uuid primary key default gen_random_uuid(),
  id_empresa uuid not null,
  codigo_producto text not null,
  costo_adquisicion numeric not null default 0,
  proveedor text,
  notas text,
  fecha_actualizacion timestamp without time zone not null default now(),
  fecha_creacion timestamp without time zone not null default now()
);

create unique index if not exists idx_catalogo_costos_empresa_codigo
  on public.catalogo_costos_proveedor(id_empresa, lower(codigo_producto));

alter table if exists public.productos
  add column if not exists codigo_producto text,
  add column if not exists piezas_por_caja integer,
  add column if not exists proveedor_catalogo text,
  add column if not exists origen_drive_file_id text,
  add column if not exists precio_publico numeric;

update public.productos
set precio_publico = coalesce(precio_publico, precio, precio_venta)
where precio_publico is null;

create index if not exists idx_productos_empresa_codigo
  on public.productos(id_empresa, lower(codigo_producto))
  where codigo_producto is not null;

create table if not exists public.catalogo_costos_importaciones (
  id uuid primary key default gen_random_uuid(),
  id_empresa uuid not null,
  nombre_archivo text not null,
  proveedor text,
  resumen jsonb not null default '{}'::jsonb,
  fecha_creacion timestamp without time zone not null default now()
);

create index if not exists idx_catalogo_costos_importaciones_empresa_fecha
  on public.catalogo_costos_importaciones(id_empresa, fecha_creacion desc);

