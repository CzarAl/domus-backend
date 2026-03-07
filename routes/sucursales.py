from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime
import uuid

from database import supabase
from dependencies import get_current_user

router = APIRouter(prefix="/sucursales", tags=["Sucursales"])


class CrearSucursalRequest(BaseModel):
    nombre: str
    tipo: str | None = None


@router.post("/crear")
def crear_sucursal(
    datos: CrearSucursalRequest,
    usuario_actual=Depends(get_current_user),
):
    try:
        # Solo dueño de tienda o admin_master
        if usuario_actual.get("rol") not in ["usuario", "admin_master"]:
            raise HTTPException(status_code=403, detail="No autorizado")

        id_empresa = usuario_actual["id_raiz"]

        # Valida cupo y registra costo extra de sucursal si aplica
        try:
            supabase.rpc(
                "agregar_recurso_extra",
                {"p_id_empresa": id_empresa, "p_tipo_recurso": "sucursal"}
            ).execute()
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        payload = {
            "id": str(uuid.uuid4()),
            "id_empresa": id_empresa,
            "nombre": datos.nombre,
            "es_matriz": False,
            "tipo": (datos.tipo or "sucursal"),
            "fecha_creacion": datetime.utcnow().isoformat(),
        }

        nueva_sucursal = supabase.table("sucursales").insert(payload).execute()

        if not nueva_sucursal.data:
            raise HTTPException(status_code=400, detail="No se pudo crear sucursal")

        return {
            "mensaje": "Sucursal creada correctamente",
            "data": nueva_sucursal.data
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/")
def listar_sucursales(usuario_actual=Depends(get_current_user)):
    id_empresa = usuario_actual["id_raiz"]

    response = (
        supabase.table("sucursales")
        .select("*")
        .eq("id_empresa", id_empresa)
        .order("fecha_creacion", desc=True)
        .execute()
    )

    return response.data
