from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from database import supabase
from datetime import datetime
from dependencies import get_current_user
import uuid

router = APIRouter(prefix="/caja", tags=["Caja"])


class AperturaCaja(BaseModel):
    monto_inicial: float
    id_sucursal: str | None = None


class CierreCaja(BaseModel):
    monto_final: float


def resolver_sucursal(usuario, id_sucursal_frontend=None):

    nivel = usuario.get("nivel_global")
    id_sucursal_usuario = usuario.get("id_sucursal")

    if nivel in ["admin_master", "usuario"]:
        if not id_sucursal_frontend:
            raise HTTPException(400, "Debe especificar sucursal")
        return id_sucursal_frontend

    if not id_sucursal_usuario:
        raise HTTPException(403, "Vendedor sin sucursal")

    return id_sucursal_usuario


@router.post("/abrir")
def abrir_caja(datos: AperturaCaja, usuario=Depends(get_current_user)):

    id_empresa = usuario.get("id_empresa")
    id_usuario = usuario.get("id_usuario")

    id_sucursal = resolver_sucursal(usuario, datos.id_sucursal)

    existente = supabase.table("sesiones_caja") \
        .select("*") \
        .eq("id_empresa", id_empresa) \
        .eq("id_sucursal", id_sucursal) \
        .eq("abierta", True) \
        .execute()

    if existente.data:
        raise HTTPException(400, "Ya existe caja abierta")

    supabase.table("sesiones_caja").insert({
        "id": str(uuid.uuid4()),
        "id_empresa": id_empresa,
        "id_sucursal": id_sucursal,
        "id_usuario_apertura": id_usuario,
        "monto_inicial": datos.monto_inicial,
        "fecha_apertura": datetime.utcnow(),
        "abierta": True
    }).execute()

    return {"mensaje": "Caja abierta"}