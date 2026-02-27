from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from database import supabase
from datetime import datetime
from main import get_current_user
import uuid

router = APIRouter(prefix="/caja", tags=["Caja"])


# ==============================
# MODELOS
# ==============================

class AperturaCaja(BaseModel):
    monto_inicial: float
    id_sucursal: str | None = None


class CierreCaja(BaseModel):
    monto_final: float


# ==============================
# FUNCIÓN AUXILIAR
# ==============================

def resolver_sucursal(usuario, id_sucursal_frontend=None):
    nivel = usuario.get("nivel")
    id_sucursal_usuario = usuario.get("id_sucursal")

    if nivel in ["admin_master", "usuario"]:
        if not id_sucursal_frontend:
            raise HTTPException(status_code=400, detail="Debe especificar sucursal")
        return id_sucursal_frontend

    if not id_sucursal_usuario:
        raise HTTPException(status_code=403, detail="Vendedor sin sucursal asignada")

    return id_sucursal_usuario


# ==============================
# ABRIR CAJA
# ==============================

@router.post("/abrir")
def abrir_caja(datos: AperturaCaja, usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")
    id_usuario = usuario.get("id_usuario")

    id_sucursal = resolver_sucursal(usuario, datos.id_sucursal)

    # Verificar que no haya otra abierta
    existente = supabase.table("sesiones_caja") \
        .select("*") \
        .eq("id_raiz", id_raiz) \
        .eq("id_sucursal", id_sucursal) \
        .eq("abierta", True) \
        .execute()

    if existente.data:
        raise HTTPException(status_code=400, detail="Ya existe caja abierta")

    sesion = supabase.table("sesiones_caja").insert({
        "id": str(uuid.uuid4()),
        "id_raiz": id_raiz,
        "id_sucursal": id_sucursal,
        "id_usuario_apertura": id_usuario,
        "monto_inicial": datos.monto_inicial,
        "fecha_apertura": datetime.utcnow().isoformat(),
        "abierta": True
    }).execute()

    return {"mensaje": "Caja abierta correctamente", "data": sesion.data}


# ==============================
# CERRAR CAJA
# ==============================

@router.post("/cerrar/{id_sesion}")
def cerrar_caja(id_sesion: str, datos: CierreCaja, usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")
    nivel = usuario.get("nivel")
    id_sucursal = usuario.get("id_sucursal")

    query = supabase.table("sesiones_caja") \
        .select("*") \
        .eq("id", id_sesion) \
        .eq("id_raiz", id_raiz)

    if nivel not in ["admin_master", "usuario"]:
        query = query.eq("id_sucursal", id_sucursal)

    sesion = query.execute()

    if not sesion.data:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    supabase.table("sesiones_caja") \
        .update({
            "monto_final": datos.monto_final,
            "fecha_cierre": datetime.utcnow().isoformat(),
            "abierta": False
        }) \
        .eq("id", id_sesion) \
        .execute()

    return {"mensaje": "Caja cerrada correctamente"}


# ==============================
# LISTAR SESIONES
# ==============================

@router.get("/sesiones")
def listar_sesiones(usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")
    nivel = usuario.get("nivel")
    id_sucursal = usuario.get("id_sucursal")

    query = supabase.table("sesiones_caja") \
        .select("*") \
        .eq("id_raiz", id_raiz)

    if nivel not in ["admin_master", "usuario"]:
        query = query.eq("id_sucursal", id_sucursal)

    return query.execute().data


# ==============================
# LISTAR MOVIMIENTOS
# ==============================

@router.get("/movimientos")
def listar_movimientos(usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")
    nivel = usuario.get("nivel")
    id_sucursal = usuario.get("id_sucursal")

    query = supabase.table("movimientos_caja") \
        .select("*") \
        .eq("id_raiz", id_raiz)

    if nivel not in ["admin_master", "usuario"]:
        query = query.eq("id_sucursal", id_sucursal)

    return query.execute().data