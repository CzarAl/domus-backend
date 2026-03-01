from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from dependencies import get_current_user
from database import supabase

router = APIRouter(
    prefix="/empresa",
    tags=["Dashboard Empresa"]
)

# =====================================================
# DASHBOARD EMPRESA
# =====================================================

@router.get("/dashboard")
def dashboard_empresa(usuario=Depends(get_current_user)):

    id_empresa = usuario.get("id_empresa")

    if not id_empresa:
        raise HTTPException(
            status_code=403,
            detail="Empresa no seleccionada"
        )

    ahora = datetime.utcnow()
    inicio_mes = ahora.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    # =========================
    # VENTAS DEL MES
    # =========================
    ventas_resp = supabase.table("ventas") \
        .select("total") \
        .eq("id_empresa", id_empresa) \
        .gte("fecha", inicio_mes.isoformat()) \
        .execute()

    ventas_mes = ventas_resp.data or []

    total_ventas_mes = sum(
        venta.get("total", 0) for venta in ventas_mes
    )

    total_transacciones = len(ventas_mes)

    # =========================
    # CAJA ACTUAL
    # =========================
    caja_resp = supabase.table("caja") \
        .select("saldo_actual") \
        .eq("id_empresa", id_empresa) \
        .limit(1) \
        .execute()

    saldo_actual = 0

    if caja_resp.data:
        saldo_actual = caja_resp.data[0].get("saldo_actual", 0)

    # =========================
    # RESPUESTA
    # =========================
    return {
        "ventas_mes": total_ventas_mes,
        "transacciones_mes": total_transacciones,
        "saldo_actual": saldo_actual
    }