from fastapi import APIRouter, Depends, HTTPException
from database import supabase
from dependencies import get_current_user
from datetime import datetime, timedelta

router = APIRouter(tags=["Dashboard Empresarial"])


@router.get("/dashboard")
def dashboard(usuario=Depends(get_current_user)):

    id_empresa = usuario.get("id_empresa")

    if not id_empresa:
        raise HTTPException(status_code=403, detail="Empresa no seleccionada")

    ahora = datetime.utcnow()
    inicio_mes = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    inicio_dia = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    mes_anterior_inicio = (inicio_mes - timedelta(days=1)).replace(day=1)

    # =========================
    # CONTEOS BÁSICOS
    # =========================

    total_productos = supabase.table("productos") \
        .select("id", count="exact") \
        .eq("id_empresa", id_empresa) \
        .execute().count or 0

    total_clientes = supabase.table("clientes") \
        .select("id", count="exact") \
        .eq("id_empresa", id_empresa) \
        .execute().count or 0

    total_sucursales = supabase.table("sucursales") \
        .select("id", count="exact") \
        .eq("id_empresa", id_empresa) \
        .execute().count or 0

    total_vendedores = supabase.table("vendedores") \
        .select("id", count="exact") \
        .eq("id_empresa", id_empresa) \
        .eq("activo", True) \
        .execute().count or 0

    # =========================
    # VENTAS
    # =========================

    ventas_hoy_data = supabase.table("ventas") \
        .select("total") \
        .eq("id_empresa", id_empresa) \
        .gte("fecha", inicio_dia.isoformat()) \
        .execute().data or []

    total_hoy = sum(v["total"] for v in ventas_hoy_data)

    ventas_mes_data = supabase.table("ventas") \
        .select("id,total") \
        .eq("id_empresa", id_empresa) \
        .gte("fecha", inicio_mes.isoformat()) \
        .execute().data or []

    total_mes = sum(v["total"] for v in ventas_mes_data)
    total_ventas_mes = len(ventas_mes_data)

    ventas_mes_anterior_data = supabase.table("ventas") \
        .select("total") \
        .eq("id_empresa", id_empresa) \
        .gte("fecha", mes_anterior_inicio.isoformat()) \
        .lt("fecha", inicio_mes.isoformat()) \
        .execute().data or []

    total_mes_anterior = sum(v["total"] for v in ventas_mes_anterior_data)

    # Crecimiento %
    crecimiento = 0
    if total_mes_anterior > 0:
        crecimiento = ((total_mes - total_mes_anterior) / total_mes_anterior) * 100

    # Ticket promedio
    ticket_promedio = 0
    if total_ventas_mes > 0:
        ticket_promedio = total_mes / total_ventas_mes

    # =========================
    # INVENTARIO BAJO
    # =========================

    inventario_bajo = supabase.table("inventario") \
        .select("id", count="exact") \
        .eq("id_empresa", id_empresa) \
        .lte("stock", 5) \
        .execute().count or 0

    # =========================
    # CAJA
    # =========================

    caja_data = supabase.table("caja") \
        .select("total_ingresos, total_egresos") \
        .eq("id_empresa", id_empresa) \
        .execute().data or []

    total_ingresos = sum(c["total_ingresos"] for c in caja_data)
    total_egresos = sum(c["total_egresos"] for c in caja_data)
    saldo_caja = total_ingresos - total_egresos

    # =========================
    # KPI AVANZADOS (RPC)
    # =========================

    top_productos = supabase.rpc(
        "top_productos_empresa",
        {"empresa_id": id_empresa}
    ).execute().data or []

    ventas_vendedores = supabase.rpc(
        "ventas_por_vendedor",
        {"empresa_id": id_empresa}
    ).execute().data or []

    margen_data = supabase.rpc(
        "margen_bruto_empresa",
        {"empresa_id": id_empresa}
    ).execute().data or []

    margen_total = margen_data[0]["margen"] if margen_data and margen_data[0]["margen"] else 0

    margen_porcentaje = 0
    if total_mes > 0:
        margen_porcentaje = (margen_total / total_mes) * 100

    # =========================
    # RESPUESTA
    # =========================

    return {
        "resumen": {
            "productos": total_productos,
            "clientes": total_clientes,
            "sucursales": total_sucursales,
            "vendedores_activos": total_vendedores
        },
        "ventas": {
            "hoy": total_hoy,
            "mes_actual": total_mes,
            "mes_anterior": total_mes_anterior,
            "crecimiento_porcentaje": round(crecimiento, 2),
            "cantidad_mes": total_ventas_mes,
            "ticket_promedio": round(ticket_promedio, 2)
        },
        "inventario": {
            "bajo_stock": inventario_bajo
        },
        "caja": {
            "ingresos": total_ingresos,
            "egresos": total_egresos,
            "saldo": saldo_caja
        },
        "kpi": {
            "top_productos": top_productos,
            "ranking_vendedores": ventas_vendedores,
            "margen_total": margen_total,
            "margen_porcentaje": round(margen_porcentaje, 2)
        }
    }