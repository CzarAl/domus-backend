from fastapi import APIRouter, Depends, HTTPException
from dependencies import get_current_user
from database import supabase
from datetime import datetime, timedelta

router = APIRouter(prefix="/admin", tags=["Admin SaaS"])


# =====================================================
# VALIDAR ADMIN MASTER
# =====================================================

def validar_admin(usuario):
    if usuario.get("nivel_global") != "admin_master":
        raise HTTPException(status_code=403, detail="Acceso solo para admin_master")
    return usuario


# =====================================================
# LISTAR EMPRESAS
# =====================================================

@router.get("/empresas")
def listar_empresas(usuario=Depends(get_current_user)):

    validar_admin(usuario)

    empresas = supabase.table("empresas") \
        .select("*") \
        .order("fecha_creacion", desc=True) \
        .execute()

    return empresas.data


# =====================================================
# LISTAR USUARIOS
# =====================================================

@router.get("/usuarios")
def listar_usuarios(usuario=Depends(get_current_user)):

    validar_admin(usuario)

    usuarios = supabase.table("usuarios") \
        .select("id,email,nivel_global,activo,fecha_creacion") \
        .order("fecha_creacion", desc=True) \
        .execute()

    return usuarios.data


# =====================================================
# DASHBOARD SaaS GLOBAL
# =====================================================

@router.get("/saas-metrics")
def saas_metrics(usuario=Depends(get_current_user)):

    validar_admin(usuario)

    ahora = datetime.utcnow()
    inicio_mes = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # =========================
    # EMPRESAS
    # =========================

    total_empresas = supabase.table("empresas") \
        .select("id", count="exact") \
        .execute().count or 0

    empresas_activas = supabase.table("empresas") \
        .select("id", count="exact") \
        .eq("estado", "activa") \
        .execute().count or 0

    empresas_suspendidas = supabase.table("empresas") \
        .select("id", count="exact") \
        .eq("estado", "suspendida") \
        .execute().count or 0

    empresas_trial = supabase.table("suscripciones") \
        .select("id", count="exact") \
        .eq("tipo", "trial") \
        .eq("estado", "activa") \
        .execute().count or 0

    nuevas_empresas_mes = supabase.table("empresas") \
        .select("id", count="exact") \
        .gte("fecha_creacion", inicio_mes.isoformat()) \
        .execute().count or 0

    # =========================
    # INGRESOS
    # =========================

    cuentas_pagadas = supabase.table("cuentas_matriz") \
        .select("monto") \
        .eq("estado", "pagado") \
        .gte("fecha_pago", inicio_mes.isoformat()) \
        .execute().data or []

    ingresos_mes = sum(c["monto"] for c in cuentas_pagadas)

    cuentas_pendientes = supabase.table("cuentas_matriz") \
        .select("monto") \
        .eq("estado", "pendiente") \
        .execute().data or []

    ingresos_pendientes = sum(c["monto"] for c in cuentas_pendientes)

    cuentas_vencidas = supabase.table("cuentas_matriz") \
        .select("monto") \
        .eq("estado", "vencido") \
        .execute().data or []

    ingresos_vencidos = sum(c["monto"] for c in cuentas_vencidas)

    # =========================
    # MRR / ARR
    # =========================

    suscripciones_activas = supabase.table("suscripciones") \
        .select("precio") \
        .eq("estado", "activa") \
        .execute().data or []

    mrr = sum(s["precio"] for s in suscripciones_activas)
    arr = mrr * 12

    # =========================
    # CONVERSIÓN TRIAL
    # =========================

    total_trials = supabase.table("suscripciones") \
        .select("id", count="exact") \
        .eq("tipo", "trial") \
        .execute().count or 0

    suscripciones_pago = supabase.table("suscripciones") \
        .select("id", count="exact") \
        .neq("tipo", "trial") \
        .execute().count or 0

    conversion = 0
    if total_trials > 0:
        conversion = (suscripciones_pago / total_trials) * 100

    # =========================
    # CHURN BÁSICO
    # =========================

    suscripciones_vencidas = supabase.table("suscripciones") \
        .select("id", count="exact") \
        .eq("estado", "vencida") \
        .execute().count or 0

    churn = 0
    if total_empresas > 0:
        churn = (suscripciones_vencidas / total_empresas) * 100

    # =========================
    # LTV SIMPLE
    # =========================

    ltv = 0
    if churn > 0:
        ltv = mrr / (churn / 100)

    return {
        "empresas": {
            "total": total_empresas,
            "activas": empresas_activas,
            "trial": empresas_trial,
            "suspendidas": empresas_suspendidas,
            "nuevas_mes": nuevas_empresas_mes
        },
        "ingresos": {
            "mes_actual": ingresos_mes,
            "pendientes": ingresos_pendientes,
            "vencidos": ingresos_vencidos,
            "mrr": mrr,
            "arr": arr
        },
        "metricas": {
            "conversion_trial_porcentaje": round(conversion, 2),
            "churn_porcentaje": round(churn, 2),
            "ltv_estimado": round(ltv, 2)
        }
    }

# =====================================================
# DASHBOARD FINANCIERO EJECUTIVO
# =====================================================

@router.get("/dashboard")
def dashboard_financiero(usuario=Depends(get_current_user)):

    validar_admin(usuario)

    response = supabase.table("dashboard_admin_financiero") \
        .select("*") \
        .execute()

    if not response.data:
        return {}

    return response.data[0]

@router.get("/crecimiento-mensual")
def crecimiento_mensual(usuario=Depends(get_current_user)):

    validar_admin(usuario)

    response = supabase.table("dashboard_crecimiento_mensual") \
        .select("*") \
        .order("mes") \
        .execute()

    return response.data