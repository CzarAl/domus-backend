from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from database import supabase
from datetime import datetime
from dependencies import get_current_user
import uuid

router = APIRouter(prefix="/caja", tags=["Caja"])


# ==============================
# MODELOS
# ==============================

class AperturaCaja(BaseModel):
    monto_inicial: float
    id_sucursal: str


class CierreCaja(BaseModel):
    monto_final: float


# ==============================
# ABRIR CAJA
# ==============================

@router.post("/abrir")
def abrir_caja(datos: AperturaCaja, usuario=Depends(get_current_user)):

    id_empresa = usuario["id_raiz"]
    id_usuario = usuario["id_usuario"]

    existente = supabase.table("sesiones_caja") \
        .select("*") \
        .eq("id_empresa", id_empresa) \
        .eq("id_sucursal", datos.id_sucursal) \
        .eq("abierta", True) \
        .execute()

    if existente.data:
        raise HTTPException(400, "Ya existe caja abierta")

    supabase.table("sesiones_caja").insert({
        "id": str(uuid.uuid4()),
        "id_empresa": id_empresa,
        "id_sucursal": datos.id_sucursal,
        "id_usuario_apertura": id_usuario,
        "monto_inicial": datos.monto_inicial,
        "fecha_apertura": datetime.utcnow().isoformat(),
        "abierta": True
    }).execute()

    return {"mensaje": "Caja abierta"}


# ==============================
# CERRAR CAJA
# ==============================

@router.post("/cerrar/{id_sesion}")
def cerrar_caja(id_sesion: str, datos: CierreCaja, usuario=Depends(get_current_user)):

    id_empresa = usuario["id_raiz"]

    respuesta = supabase.table("sesiones_caja") \
        .update({
            "monto_final": datos.monto_final,
            "fecha_cierre": datetime.utcnow().isoformat(),
            "abierta": False
        }) \
        .eq("id", id_sesion) \
        .eq("id_empresa", id_empresa) \
        .execute()

    if not respuesta.data:
        raise HTTPException(404, "Sesión no encontrada")

    return {"mensaje": "Caja cerrada"}