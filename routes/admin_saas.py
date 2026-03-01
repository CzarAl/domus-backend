from fastapi import APIRouter, Depends, HTTPException
from database import supabase
from dependencies import get_current_user
from datetime import datetime
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/admin-saas", tags=["Admin SaaS"])


# =====================================================
# VALIDAR ADMIN MASTER
# =====================================================

def validar_admin(usuario):
    if usuario.get("nivel_global") != "admin_master":
        raise HTTPException(status_code=403, detail="Solo admin_master")
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
# LISTAR CUENTAS MATRIZ
# =====================================================

@router.get("/cuentas-matriz")
def listar_cuentas(usuario=Depends(get_current_user)):

    validar_admin(usuario)

    cuentas = supabase.table("cuentas_matriz") \
        .select("*") \
        .order("fecha_vencimiento", desc=False) \
        .execute()

    return cuentas.data


# =====================================================
# VER SOLO VENCIDAS
# =====================================================

@router.get("/cuentas-vencidas")
def cuentas_vencidas(usuario=Depends(get_current_user)):

    validar_admin(usuario)

    cuentas = supabase.table("cuentas_matriz") \
        .select("*") \
        .eq("estado", "vencida") \
        .execute()

    return cuentas.data


# =====================================================
# SUSPENDER EMPRESA (MANUAL)
# =====================================================

@router.post("/suspender/{empresa_id}")
def suspender_empresa(empresa_id: str, usuario=Depends(get_current_user)):

    validar_admin(usuario)

    supabase.table("empresas") \
        .update({"estado": "suspendida"}) \
        .eq("id", empresa_id) \
        .execute()

    return {"mensaje": "Empresa suspendida manualmente"}


# =====================================================
# REACTIVAR EMPRESA (SOLO SI NO HAY VENCIDAS)
# =====================================================

@router.post("/reactivar/{empresa_id}")
def reactivar_empresa(empresa_id: str, usuario=Depends(get_current_user)):

    validar_admin(usuario)

    vencidas = supabase.table("cuentas_matriz") \
        .select("id") \
        .eq("id_empresa_matriz", empresa_id) \
        .eq("estado", "vencida") \
        .execute()

    if vencidas.data:
        raise HTTPException(
            status_code=400,
            detail="No se puede reactivar. Existen cuentas vencidas."
        )

    supabase.table("empresas") \
        .update({
            "estado": "activa",
            "cancelacion_pendiente": False
        }) \
        .eq("id", empresa_id) \
        .execute()

    return {"mensaje": "Empresa reactivada correctamente"}


# =====================================================
# MARCAR PAGADO (PAGO TOTAL)
# =====================================================

@router.post("/marcar-pagado/{empresa_id}")
def marcar_pagado(empresa_id: str, usuario=Depends(get_current_user)):

    validar_admin(usuario)

    # Marcar TODAS las vencidas como pagadas
    supabase.table("cuentas_matriz") \
        .update({
            "estado": "pagada",
            "fecha_pago": datetime.utcnow()
        }) \
        .eq("id_empresa_matriz", empresa_id) \
        .eq("estado", "vencida") \
        .execute()

    # Reactivar empresa
    supabase.table("empresas") \
        .update({
            "estado": "activa",
            "cancelacion_pendiente": False
        }) \
        .eq("id", empresa_id) \
        .execute()

    return {"mensaje": "Pago confirmado y empresa reactivada"}


# =====================================================
# APROBAR CANCELACIÓN (SOLO SI NO HAY DEUDA)
# =====================================================

@router.post("/aprobar-cancelacion/{empresa_id}")
def aprobar_cancelacion(empresa_id: str, usuario=Depends(get_current_user)):

    validar_admin(usuario)

    vencidas = supabase.table("cuentas_matriz") \
        .select("id") \
        .eq("id_empresa_matriz", empresa_id) \
        .eq("estado", "vencida") \
        .execute()

    if vencidas.data:
        raise HTTPException(
            status_code=400,
            detail="Empresa tiene cuentas vencidas. No puede cancelarse."
        )

    supabase.table("empresas") \
        .update({
            "estado": "suspendida",
            "cancelacion_pendiente": False
        }) \
        .eq("id", empresa_id) \
        .execute()

    return {"mensaje": "Empresa suspendida tras cancelación aprobada"}


# =====================================================
# ELIMINAR DEFINITIVO CON BACKUP
# =====================================================

@router.delete("/eliminar-definitivo/{empresa_id}")
def eliminar_empresa(
    empresa_id: str,
    descargar: bool = False,
    usuario=Depends(get_current_user)
):

    validar_admin(usuario)

    empresa = supabase.table("empresas").select("*").eq("id", empresa_id).execute().data
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    sucursales = supabase.table("sucursales").select("*").eq("id_empresa", empresa_id).execute().data
    vendedores = supabase.table("vendedores").select("*").eq("id_empresa", empresa_id).execute().data
    productos = supabase.table("productos").select("*").eq("id_empresa", empresa_id).execute().data
    clientes = supabase.table("clientes").select("*").eq("id_empresa", empresa_id).execute().data
    inventario = supabase.table("inventario").select("*").eq("id_empresa", empresa_id).execute().data
    ventas = supabase.table("ventas").select("*").eq("id_empresa", empresa_id).execute().data
    cuentas = supabase.table("cuentas_matriz").select("*").eq("id_empresa_matriz", empresa_id).execute().data

    backup_data = {
        "empresa": empresa,
        "sucursales": sucursales,
        "vendedores": vendedores,
        "productos": productos,
        "clientes": clientes,
        "inventario": inventario,
        "ventas": ventas,
        "cuentas_matriz": cuentas
    }

    # Guardar backup interno
    supabase.table("empresas_backup").insert({
        "id_empresa": empresa_id,
        "datos": backup_data,
        "eliminado_por": usuario.get("id_usuario")
    }).execute()

    # Eliminar empresa (cascada)
    supabase.table("empresas").delete().eq("id", empresa_id).execute()

    if descargar:
        return JSONResponse(
            content=backup_data,
            headers={
                "Content-Disposition": f"attachment; filename=backup_{empresa_id}.json"
            }
        )

    return {"mensaje": "Empresa eliminada correctamente y respaldo guardado"}