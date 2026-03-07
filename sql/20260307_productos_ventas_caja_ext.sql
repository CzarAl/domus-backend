-- Extensiones para productos, ventas y caja

alter table if exists public.productos
    add column if not exists descripcion text,
    add column if not exists costo_adquisicion numeric,
    add column if not exists precio numeric,
    add column if not exists ubicacion text,
    add column if not exists foto_url text,
    add column if not exists activo boolean default true,
    add column if not exists fecha_creacion timestamp without time zone default now();

alter table if exists public.detalle_ventas
    add column if not exists nombre_producto text,
    add column if not exists descripcion_producto text,
    add column if not exists foto_url text;

alter table if exists public.ventas
    add column if not exists solicito_pdf boolean default false,
    add column if not exists comprobante_data jsonb;

alter table if exists public.sesiones_caja
    add column if not exists id_usuario_cierre uuid,
    add column if not exists total_entradas numeric,
    add column if not exists total_salidas numeric,
    add column if not exists arqueo_esperado numeric,
    add column if not exists arqueo_real numeric,
    add column if not exists diferencia_arqueo numeric;

create table if not exists public.movimientos_caja (
    id uuid primary key,
    id_sesion uuid not null references public.sesiones_caja(id) on delete cascade,
    id_empresa uuid not null,
    id_sucursal uuid null,
    id_usuario uuid null,
    tipo_movimiento text not null check (tipo_movimiento in ('entrada','salida')),
    monto numeric not null check (monto > 0),
    concepto text not null,
    metodo_pago text null,
    referencia text null,
    fecha_creacion timestamp without time zone default now()
);

create index if not exists idx_mov_caja_sesion on public.movimientos_caja(id_sesion);
create index if not exists idx_mov_caja_empresa on public.movimientos_caja(id_empresa);
create index if not exists idx_mov_caja_fecha on public.movimientos_caja(fecha_creacion desc);
