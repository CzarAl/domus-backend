-- Wallet module (multi-tenant by id_empresa)

create table if not exists public.wallet_cuentas (
    id uuid primary key,
    id_empresa uuid not null,
    id_usuario_creador uuid null,
    nombre text not null,
    tipo text not null check (tipo in ('efectivo', 'credito', 'debito')),
    saldo_actual numeric not null default 0,
    limite_credito numeric null,
    fecha_pago date null,
    activo boolean default true,
    fecha_creacion timestamp without time zone default now(),
    fecha_actualizacion timestamp without time zone default now()
);

create table if not exists public.wallet_movimientos (
    id uuid primary key,
    id_cuenta uuid not null references public.wallet_cuentas(id) on delete cascade,
    id_empresa uuid not null,
    id_usuario uuid null,
    tipo_movimiento text not null check (tipo_movimiento in ('cargo', 'abono')),
    monto numeric not null check (monto > 0),
    nombre text not null,
    es_msi boolean default false,
    meses_msi integer null,
    fecha_movimiento date default current_date,
    fecha_creacion timestamp without time zone default now()
);

create index if not exists idx_wallet_cuentas_empresa on public.wallet_cuentas (id_empresa);
create index if not exists idx_wallet_cuentas_tipo on public.wallet_cuentas (tipo);
create index if not exists idx_wallet_movimientos_empresa on public.wallet_movimientos (id_empresa);
create index if not exists idx_wallet_movimientos_cuenta on public.wallet_movimientos (id_cuenta);
create index if not exists idx_wallet_movimientos_fecha on public.wallet_movimientos (fecha_creacion desc);
