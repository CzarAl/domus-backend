from fastapi import APIRouter, Depends, HTTPException
from database import supabase
from datetime import datetime
from fastapi.responses import JSONResponse
from dependencies import require_role
from pydantic import BaseModel, Field
from typing import Literal
import uuid

router = APIRouter(prefix="/admin-saas", tags=["Admin SaaS"])

ADMIN_MASTER_ROLE = "admin_master"
ESTADO_VENCIDA = "vencida"
ESTADO_PAGADA = "pagada"
ESTADOS_VENCIDA_ALIAS = [ESTADO_VENCIDA, "vencido"]
ESTADOS_PAGADA_ALIAS = [ESTADO_PAGADA, "pagado"]
TIPOS_RECURSO = ("vendedor", "sucursal", "web_publica")


class AutorizacionRecursoCreate(BaseModel):
    id_empresa: str
    tipo_recurso: Literal["vendedor", "sucursal", "web_publica"]
    cantidad_autorizada: int = Field(gt=0)
    costo_mensual: float = Field(gt=0)
    cerrar_autorizaciones_previas: bool = False


class AutorizacionRecursoUpdate(BaseModel):
    cantidad_autorizada: int | None = Field(default=None, gt=0)
    costo_mensual: float | None = Field(default=None, gt=0)
    activo: bool | None = None
    fecha_fin: datetime | None = None


def _obtener_cuentas_vencidas(empresa_id: str):
    return (
        supabase.table("cuentas_matriz")
        .select("id")
        .eq("id_empresa_matriz", empresa_id)
        .in_("estado", ESTADOS_VENCIDA_ALIAS)
        .execute()
    )


def _obtener_fecha_fin_ultima_pagada(empresa_id: str):
    cuenta = (
        supabase.table("cuentas_matriz")
        .select("periodo_fin,fecha_vencimiento")
        .eq("id_empresa_matriz", empresa_id)
        .in_("estado", ESTADOS_PAGADA_ALIAS)
        .order("periodo_fin", desc=True)
        .limit(1)
        .execute()
    )

    if not cuenta.data:
        return None

    registro = cuenta.data[0]
    return registro.get("periodo_fin") or registro.get("fecha_vencimiento")


@router.post("/autorizaciones")
def crear_autorizacion_recurso(
    datos: AutorizacionRecursoCreate,
    usuario=Depends(require_role(ADMIN_MASTER_ROLE))
):
    if datos.tipo_recurso not in TIPOS_RECURSO:
        raise HTTPException(status_code=400, detail="Tipo de recurso inválido")

    if datos.cerrar_autorizaciones_previas:
        (
            supabase.table("autorizaciones_admin_empresa")
            .update({
                "activo": False,
                "fecha_fin": datetime.utcnow().isoformat()
            })
            .eq("id_empresa", datos.id_empresa)
            .eq("tipo_recurso", datos.tipo_recurso)
            .eq("activo", True)
            .execute()
        )

    payload = {
        "id": str(uuid.uuid4()),
        "id_empresa": datos.id_empresa,
        "tipo_recurso": datos.tipo_recurso,
        "cantidad_autorizada": datos.cantidad_autorizada,
        "costo_mensual": datos.costo_mensual,
        "activo": True,
        "fecha_autorizacion": datetime.utcnow().isoformat(),
    }

    response = supabase.table("autorizaciones_admin_empresa").insert(payload).execute()

    return {
        "mensaje": "Autorización creada correctamente",
        "data": response.data,
    }


@router.get("/autorizaciones/{empresa_id}")
def listar_autorizaciones_recurso(
    empresa_id: str,
    usuario=Depends(require_role(ADMIN_MASTER_ROLE))
):
    response = (
        supabase.table("autorizaciones_admin_empresa")
        .select("*")
        .eq("id_empresa", empresa_id)
        .order("fecha_autorizacion", desc=True)
        .execute()
    )
    return response.data


@router.patch("/autorizaciones/{id_autorizacion}")
def actualizar_autorizacion_recurso(
    id_autorizacion: str,
    datos: AutorizacionRecursoUpdate,
    usuario=Depends(require_role(ADMIN_MASTER_ROLE))
):
    update_data = {}

    if datos.cantidad_autorizada is not None:
        update_data["cantidad_autorizada"] = datos.cantidad_autorizada
    if datos.costo_mensual is not None:
        update_data["costo_mensual"] = datos.costo_mensual
    if datos.activo is not None:
        update_data["activo"] = datos.activo
        if datos.activo is False and datos.fecha_fin is None:
            update_data["fecha_fin"] = datetime.utcnow().isoformat()
    if datos.fecha_fin is not None:
        update_data["fecha_fin"] = datos.fecha_fin.isoformat()

    if not update_data:
        raise HTTPException(status_code=400, detail="No hay cambios para actualizar")

    response = (
        supabase.table("autorizaciones_admin_empresa")
        .update(update_data)
        .eq("id", id_autorizacion)
        .execute()
    )

    return {
        "mensaje": "Autorización actualizada correctamente",
        "data": response.data,
    }


@router.post("/autorizaciones/{id_autorizacion}/cancelar")
def cancelar_autorizacion_recurso(
    id_autorizacion: str,
    usuario=Depends(require_role(ADMIN_MASTER_ROLE))
):
    response = (
        supabase.table("autorizaciones_admin_empresa")
        .update({
            "activo": False,
            "fecha_fin": datetime.utcnow().isoformat()
        })
        .eq("id", id_autorizacion)
        .execute()
    )

    return {
        "mensaje": "Autorización cancelada correctamente",
        "data": response.data,
    }


# =====================================================
# LISTAR EMPRESAS
# =====================================================

@router.get("/empresas")
def listar_empresas(usuario=Depends(require_role(ADMIN_MASTER_ROLE))):

    empresas = supabase.table("empresas") \
        .select("*") \
        .order("fecha_creacion", desc=True) \
        .execute()

    return empresas.data


# =====================================================
# LISTAR CUENTAS MATRIZ
# =====================================================

@router.get("/cuentas-matriz")
def listar_cuentas(usuario=Depends(require_role(ADMIN_MASTER_ROLE))):

    cuentas = supabase.table("cuentas_matriz") \
        .select("*") \
        .order("fecha_vencimiento", desc=False) \
        .execute()

    return cuentas.data


# =====================================================
# VER SOLO VENCIDAS
# =====================================================

@router.get("/cuentas-vencidas")
def cuentas_vencidas(usuario=Depends(require_role(ADMIN_MASTER_ROLE))):

    cuentas = supabase.table("cuentas_matriz") \
        .select("*") \
        .in_("estado", ESTADOS_VENCIDA_ALIAS) \
        .execute()

    return cuentas.data


# =====================================================
# SUSPENDER EMPRESA (MANUAL)
# =====================================================

@router.post("/suspender/{empresa_id}")
def suspender_empresa(empresa_id: str, usuario=Depends(require_role(ADMIN_MASTER_ROLE))):

    supabase.table("empresas") \
        .update({"estado": "suspendida"}) \
        .eq("id", empresa_id) \
        .execute()

    return {"mensaje": "Empresa suspendida manualmente"}


# =====================================================
# REACTIVAR EMPRESA (SOLO SI NO HAY VENCIDAS)
# =====================================================

@router.post("/reactivar/{empresa_id}")
def reactivar_empresa(
    empresa_id: str,
    usar_motor_sql: bool = False,
    usuario=Depends(require_role(ADMIN_MASTER_ROLE))
):
    vencidas = _obtener_cuentas_vencidas(empresa_id)

    if vencidas.data:
        raise HTTPException(
            status_code=400,
            detail="No se puede reactivar. Existen cuentas vencidas."
        )

    if usar_motor_sql:
        fecha_fin = _obtener_fecha_fin_ultima_pagada(empresa_id)
        if not fecha_fin:
            raise HTTPException(
                status_code=400,
                detail="No se encontró una cuenta pagada para calcular la reactivación."
            )

        try:
            supabase.rpc(
                "reactivar_empresa",
                {"p_id_empresa": empresa_id, "p_nueva_fecha": fecha_fin}
            ).execute()
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"No se pudo reactivar con función SQL: {exc}"
            )
    else:
        # Modo manual permitido para admin_master.
        supabase.table("empresas") \
            .update({"estado": "activa"}) \
            .eq("id", empresa_id) \
            .execute()

    supabase.table("empresas") \
        .update({"cancelacion_pendiente": False}) \
        .eq("id", empresa_id) \
        .execute()

    return {"mensaje": "Empresa reactivada correctamente"}


# =====================================================
# MARCAR PAGADO (PAGO TOTAL)
# =====================================================

@router.post("/marcar-pagado/{empresa_id}")
def marcar_pagado(
    empresa_id: str,
    usar_motor_sql: bool = False,
    usuario=Depends(require_role(ADMIN_MASTER_ROLE))
):

    # Marcar TODAS las vencidas como pagadas
    supabase.table("cuentas_matriz") \
        .update({
            "estado": ESTADO_PAGADA,
            "fecha_pago": datetime.utcnow().isoformat()
        }) \
        .eq("id_empresa_matriz", empresa_id) \
        .in_("estado", ESTADOS_VENCIDA_ALIAS) \
        .execute()

    # Normalizar registros legacy marcados como "pagado"
    supabase.table("cuentas_matriz") \
        .update({"estado": ESTADO_PAGADA}) \
        .eq("id_empresa_matriz", empresa_id) \
        .in_("estado", ESTADOS_PAGADA_ALIAS) \
        .execute()

    if usar_motor_sql:
        fecha_fin = _obtener_fecha_fin_ultima_pagada(empresa_id)
        if not fecha_fin:
            raise HTTPException(
                status_code=500,
                detail="Se marcó pagada la cuenta, pero no se pudo obtener fecha para reactivar."
            )

        try:
            supabase.rpc(
                "reactivar_empresa",
                {"p_id_empresa": empresa_id, "p_nueva_fecha": fecha_fin}
            ).execute()
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Pago marcado, pero falló reactivación SQL: {exc}"
            )
    else:
        # Modo manual permitido para admin_master.
        supabase.table("empresas") \
            .update({"estado": "activa"}) \
            .eq("id", empresa_id) \
            .execute()

    supabase.table("empresas") \
        .update({"cancelacion_pendiente": False}) \
        .eq("id", empresa_id) \
        .execute()

    return {"mensaje": "Pago confirmado y empresa reactivada"}


# =====================================================
# APROBAR CANCELACIÓN (SOLO SI NO HAY DEUDA)
# =====================================================

@router.post("/aprobar-cancelacion/{empresa_id}")
def aprobar_cancelacion(empresa_id: str, usuario=Depends(require_role(ADMIN_MASTER_ROLE))):
    vencidas = _obtener_cuentas_vencidas(empresa_id)

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
    _usuario=Depends(require_role(ADMIN_MASTER_ROLE))
):
    try:
        # Delegar respaldo + eliminación al motor SQL
        supabase.rpc("cancelar_empresa_definitivamente", {"p_id_empresa": empresa_id}).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo eliminar empresa desde función SQL: {exc}"
        )

    if descargar:
        backup = (
            supabase.table("empresas_backup")
            .select("id,datos")
            .eq("id_empresa_original", empresa_id)
            .order("fecha_eliminacion", desc=True)
            .limit(1)
            .execute()
        )
        if not backup.data:
            raise HTTPException(
                status_code=500,
                detail="Empresa eliminada, pero no se encontró respaldo para descargar."
            )
        backup_data = backup.data[0]["datos"] or {}

        return JSONResponse(
            content=backup_data,
            headers={
                "Content-Disposition": f"attachment; filename=backup_{empresa_id}.json"
            }
        )

    return {"mensaje": "Empresa eliminada correctamente y respaldo guardado"}
