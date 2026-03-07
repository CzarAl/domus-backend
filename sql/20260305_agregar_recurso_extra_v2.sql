-- Ejecutar en Supabase SQL Editor
-- Reemplaza la función para:
-- 1) Cobrar extra solo cuando se rebasa el límite del plan
-- 2) Respetar autorización activa de admin_master (cantidad + costo)
-- 3) Funcionar para vendedor y sucursal

create or replace function public.agregar_recurso_extra(
    p_id_empresa uuid,
    p_tipo_recurso text
)
returns void
language plpgsql
as $function$
declare
    plan_empresa record;
    limite_incluido integer;
    total_actual integer;
    total_nuevo integer;
    extras_requeridos integer;
    extras_autorizados integer;
    v_autorizacion record;
begin
    -- 1) Plan activo
    select pl.*
    into plan_empresa
    from suscripciones s
    join planes pl on pl.id = s.id_plan
    where s.id_empresa = p_id_empresa
      and s.estado = 'activa'
    limit 1;

    if plan_empresa is null then
        raise exception 'Empresa sin suscripción activa';
    end if;

    -- 2) Límite incluido + conteo actual
    if p_tipo_recurso = 'vendedor' then
        limite_incluido := plan_empresa.limite_vendedores;
        select count(*) into total_actual
        from vendedores
        where id_empresa = p_id_empresa
          and activo = true;

    elsif p_tipo_recurso = 'sucursal' then
        limite_incluido := plan_empresa.limite_sucursales;
        select count(*) into total_actual
        from sucursales
        where id_empresa = p_id_empresa;

    else
        raise exception 'Tipo de recurso inválido';
    end if;

    -- Se asume llamada ANTES de insertar el nuevo recurso
    total_nuevo := total_actual + 1;
    extras_requeridos := greatest(total_nuevo - limite_incluido, 0);

    -- Si no hay exceso, no se registra cargo extra
    if extras_requeridos = 0 then
        return;
    end if;

    -- 3) Suma de extras autorizados por admin_master (activos)
    select coalesce(sum(cantidad_autorizada), 0)
    into extras_autorizados
    from autorizaciones_admin_empresa
    where id_empresa = p_id_empresa
      and tipo_recurso = p_tipo_recurso
      and activo = true
      and (fecha_fin is null or fecha_fin >= current_date);

    if extras_requeridos > extras_autorizados then
        raise exception 'Se alcanzó el límite autorizado por admin';
    end if;

    -- 4) Elegir costo del bloque que cubre el extra actual
    with bloques as (
        select
            id,
            costo_mensual,
            sum(cantidad_autorizada) over (order by fecha_autorizacion, id) as acumulado
        from autorizaciones_admin_empresa
        where id_empresa = p_id_empresa
          and tipo_recurso = p_tipo_recurso
          and activo = true
          and (fecha_fin is null or fecha_fin >= current_date)
    )
    select *
    into v_autorizacion
    from bloques
    where acumulado >= extras_requeridos
    order by acumulado
    limit 1;

    if v_autorizacion is null then
        raise exception 'No hay autorización admin para este extra';
    end if;

    -- 5) Registrar recurso/cargo extra activo
    insert into recursos_activos_empresa (
        id_empresa,
        tipo_recurso,
        fecha_inicio,
        costo_mensual
    )
    values (
        p_id_empresa,
        p_tipo_recurso,
        current_date,
        v_autorizacion.costo_mensual
    );
end;
$function$;

