from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime
import uuid

from database import supabase
from main import get_current_user  # IMPORTANTE

router = APIRouter(prefix="/clientes", tags=["Clientes"])


class ClienteCrear(BaseModel):
    nombre: str
    telefono: str | None = None
    email: str | None = None
    direccion: str | None = None
    codigo_postal: str | None = None
    rfc: str | None = None


# ==============================
# CREAR CLIENTE
# ==============================

@router.post("/")
def crear_cliente(datos: ClienteCrear, usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")
    id_usuario = usuario.get("id_usuario")

    if not id_raiz:
        raise HTTPException(status_code=400, detail="Usuario sin id_raiz")

    nuevo_id = str(uuid.uuid4())

    respuesta = supabase.table("clientes").insert({
        "id": nuevo_id,
        "numero_cliente": nuevo_id[:8],
        "nombre": datos.nombre,
        "telefono": datos.telefono,
        "email": datos.email,
        "direccion": datos.direccion,
        "codigo_postal": datos.codigo_postal,
        "rfc": datos.rfc,
        "id_raiz": id_raiz,
        "id_usuario_creador": id_usuario,
        "fecha_registro": datetime.utcnow().isoformat()
    }).execute()

    return {
        "mensaje": "Cliente creado",
        "data": respuesta.data
    }


# ==============================
# LISTAR CLIENTES
# ==============================

@router.get("/")
def listar_clientes(usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")

    respuesta = supabase.table("clientes") \
        .select("*") \
        .eq("id_raiz", id_raiz) \
        .execute()

    return respuesta.data


# ==============================
# VER CLIENTE
# ==============================

@router.get("/{cliente_id}")
def obtener_cliente(cliente_id: str, usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")

    respuesta = supabase.table("clientes") \
        .select("*") \
        .eq("id", cliente_id) \
        .eq("id_raiz", id_raiz) \
        .execute()

    if not respuesta.data:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    return respuesta.data[0]


# ==============================
# ACTUALIZAR
# ==============================

@router.put("/{cliente_id}")
def actualizar_cliente(
    cliente_id: str,
    datos: ClienteCrear,
    usuario=Depends(get_current_user)
):

    id_raiz = usuario.get("id_raiz")

    respuesta = supabase.table("clientes") \
        .update({
            "nombre": datos.nombre,
            "telefono": datos.telefono,
            "email": datos.email,
            "direccion": datos.direccion,
            "codigo_postal": datos.codigo_postal,
            "rfc": datos.rfc
        }) \
        .eq("id", cliente_id) \
        .eq("id_raiz", id_raiz) \
        .execute()

    return {"mensaje": "Cliente actualizado", "data": respuesta.data}


# ==============================
# ELIMINAR
# ==============================

@router.delete("/{cliente_id}")
def eliminar_cliente(cliente_id: str, usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")

    supabase.table("clientes") \
        .delete() \
        .eq("id", cliente_id) \
        .eq("id_raiz", id_raiz) \
        .execute()

    return {"mensaje": "Cliente eliminado"}