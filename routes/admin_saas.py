from fastapi import APIRouter, Depends, HTTPException
from database import supabase
from datetime import date, datetime
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
TIPOS_RECURSO = ("vendedor", "sucursal", "web_publica", "wallet")


class AutorizacionRecursoCreate(BaseModel):
    id_empresa: str
    tipo_recurso: Literal["vendedor", "sucursal", "web_publica", "wallet"]
    cantidad_autorizada: int = Field(gt=0)
    costo_mensual: float = Field(gt=0)
    cerrar_autorizaciones_previas: bool = False


class AutorizacionRecursoUpdate(BaseModel):
    cantidad_autorizada: int | None = Field(default=None, gt=0)
    costo_mensual: float | None = Field(default=None, gt=0)
    activo: bool | None = None
    fecha_fin: datetime | None = None


class CancelarAutorizacionRequest(BaseModel):
    backup_wallet: bool = False
    nombre_backup: str | None = Field(default=None, min_length=3, max_length=80)


def _slug_text(texto: str) -> str:
    limpio = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in texto)
    limpio = limpio.strip("_")
    return limpio or "backup"


def _parse_date(value):
    if value is None:
        return None
    if isinstance(value, date):
        return value
    s = str(value)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None


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


def _activar_recurso_wallet(id_empresa: str, costo_mensual: float):
    hoy = date.today().isoformat()

    supabase.table("recursos_activos_empresa").update(
        {"fecha_fin": hoy}
    ).eq("id_empresa", id_empresa).eq("tipo_recurso", "wallet").is_("fecha_fin", "null").execute()

    supabase.table("recursos_activos_empresa").insert(
        {
            "id": str(uuid.uuid4()),
            "id_empresa": id_empresa,
            "tipo_recurso": "wallet",
            "fecha_inicio": hoy,
            "fecha_fin": None,
            "costo_mensual": costo_mensual,
            "fecha_creacion": datetime.utcnow().isoformat(),
        }
    ).execute()


def _cerrar_recurso_wallet(id_empresa: str):
    hoy = date.today().isoformat()
    supabase.table("recursos_activos_empresa").update(
        {"fecha_fin": hoy}
    ).eq("id_empresa", id_empresa).eq("tipo_recurso", "wallet").is_("fecha_fin", "null").execute()


def _crear_backup_wallet_empresa(id_empresa: str, nombre_backup: str, id_usuario: str | None):
    cuentas = (
        supabase.table("wallet_cuentas")
        .select("*")
        .eq("id_empresa", id_empresa)
        .execute()
    ).data or []

    ids_cuentas = [c.get("id") for c in cuentas if c.get("id")]

    movimientos = []
    if ids_cuentas:
        movimientos = (
            supabase.table("wallet_movimientos")
            .select("*")
            .in_("id_cuenta", ids_cuentas)
            .execute()
        ).data or []

    payload = {
        "tipo_respaldo": "wallet",
        "nombre_respaldo": nombre_backup,
        "id_empresa": id_empresa,
        "generado_por": id_usuario,
        "fecha": datetime.utcnow().isoformat(),
        "cuentas": cuentas,
        "movimientos": movimientos,
    }

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    nombre_archivo = f"wallet_{_slug_text(nombre_backup)}_{stamp}.json"

    respaldo = (
        supabase.table("empresas_backup")
        .insert(
            {
                "id_empresa_original": id_empresa,
                "nombre_empresa": f"WALLET::{nombre_backup}",
                "nombre_archivo": nombre_archivo,
                "datos": payload,
            }
        )
        .execute()
    )

    if not respaldo.data:
        raise HTTPException(status_code=500, detail="No se pudo guardar backup de wallet")

    return respaldo.data[0]


@router.post("/autorizaciones")
def crear_autorizacion_recurso(
    datos: AutorizacionRecursoCreate,
    usuario=Depends(require_role(ADMIN_MASTER_ROLE))
):
    if datos.tipo_recurso not in TIPOS_RECURSO:
        raise HTTPException(status_code=400, detail="Tipo de recurso inválido")

    if datos.tipo_recurso == "wallet" and datos.cantidad_autorizada != 1:
        raise HTTPException(status_code=400, detail="Wallet solo permite cantidad_autorizada = 1")

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

    if datos.tipo_recurso == "wallet":
        _activar_recurso_wallet(datos.id_empresa, datos.costo_mensual)

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
    autorizacion_actual = (
        supabase.table("autorizaciones_admin_empresa")
        .select("*")
        .eq("id", id_autorizacion)
        .limit(1)
        .execute()
    )

    if not autorizacion_actual.data:
        raise HTTPException(status_code=404, detail="Autorización no encontrada")

    auth = autorizacion_actual.data[0]

    update_data = {}

    if datos.cantidad_autorizada is not None:
        if auth.get("tipo_recurso") == "wallet" and datos.cantidad_autorizada != 1:
            raise HTTPException(status_code=400, detail="Wallet solo permite cantidad_autorizada = 1")
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

    if auth.get("tipo_recurso") == "wallet":
        if datos.activo is False:
            _cerrar_recurso_wallet(auth["id_empresa"])
        elif datos.costo_mensual is not None and (datos.activo is None or datos.activo is True):
            _activar_recurso_wallet(auth["id_empresa"], datos.costo_mensual)

    return {
        "mensaje": "Autorización actualizada correctamente",
        "data": response.data,
    }


@router.post("/autorizaciones/{id_autorizacion}/cancelar")
def cancelar_autorizacion_recurso(
    id_autorizacion: str,
    datos: CancelarAutorizacionRequest | None = None,
    usuario=Depends(require_role(ADMIN_MASTER_ROLE))
):
    autorizacion = (
        supabase.table("autorizaciones_admin_empresa")
        .select("*")
        .eq("id", id_autorizacion)
        .limit(1)
        .execute()
    )

    if not autorizacion.data:
        raise HTTPException(status_code=404, detail="Autorización no encontrada")

    auth = autorizacion.data[0]
    backup_id = None

    if auth.get("tipo_recurso") == "wallet" and datos and datos.backup_wallet:
        nombre_backup = datos.nombre_backup or f"wallet_{auth.get('id_empresa')}"
        backup = _crear_backup_wallet_empresa(
            auth["id_empresa"],
            nombre_backup,
            usuario.get("id_usuario") or usuario.get("id"),
        )
        backup_id = backup.get("id")

    response = (
        supabase.table("autorizaciones_admin_empresa")
        .update({
            "activo": False,
            "fecha_fin": datetime.utcnow().isoformat()
        })
        .eq("id", id_autorizacion)
        .execute()
    )

    if auth.get("tipo_recurso") == "wallet":
        _cerrar_recurso_wallet(auth["id_empresa"])

    return {
        "mensaje": "Autorización cancelada correctamente",
        "backup_id": backup_id,
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
