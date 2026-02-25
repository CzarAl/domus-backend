from fastapi import APIRouter, Depends, HTTPException
from supabase import create_client
from dependencies import obtener_usuario_actual
from pydantic import BaseModel
import os
from datetime import datetime
import uuid

from database import supabase

router = APIRouter(prefix="/clientes", tags=["Clientes"])


# ==============================
# MODELO
# ==============================

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

from datetime import datetime
import uuid

@router.post("/")
def crear_cliente(datos: ClienteCrear, usuario=Depends(obtener_usuario_actual)):

    try:
        print("TOKEN USUARIO:", usuario)

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

        print("RESPUESTA SUPABASE:", respuesta)

        return {
            "mensaje": "Cliente creado",
            "data": respuesta.data
        }

    except Exception as e:
        print("ERROR REAL:", e)
        raise HTTPException(status_code=500, detail=str(e))


# ==============================
# LISTAR CLIENTES
# ==============================

@router.get("/")
def listar_clientes(usuario=Depends(obtener_usuario_actual)):

    id_raiz = usuario.get("id_raiz")

    respuesta = supabase.table("clientes") \
        .select("*") \
        .eq("id_raiz", id_raiz) \
        .execute()

    return respuesta.data


# ==============================
# VER CLIENTE POR ID
# ==============================

@router.get("/{cliente_id}")
def obtener_cliente(cliente_id: str, usuario=Depends(obtener_usuario_actual)):

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
# ACTUALIZAR CLIENTE
# ==============================

@router.put("/{cliente_id}")
def actualizar_cliente(
    cliente_id: str,
    datos: ClienteCrear,
    usuario=Depends(obtener_usuario_actual)
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
# ELIMINAR CLIENTE
# ==============================

@router.delete("/{cliente_id}")
def eliminar_cliente(cliente_id: str, usuario=Depends(obtener_usuario_actual)):

    id_raiz = usuario.get("id_raiz")

    supabase.table("clientes") \
        .delete() \
        .eq("id", cliente_id) \
        .eq("id_raiz", id_raiz) \
        .execute()

    return {"mensaje": "Cliente eliminado"}