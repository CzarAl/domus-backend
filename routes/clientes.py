from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime
import uuid
from dependencies import get_current_user
from database import supabase

router = APIRouter(prefix="/clientes", tags=["Clientes"])


class ClienteCrear(BaseModel):
    nombre: str
    telefono: str | None = None
    email: str | None = None
    direccion: str | None = None
    rfc: str | None = None
    codigo_postal: str | None = None


@router.post("/")
def crear_cliente(datos: ClienteCrear, usuario=Depends(get_current_user)):
    id_empresa = usuario.get("id_raiz")
    if not id_empresa:
        raise HTTPException(status_code=400, detail="Usuario sin empresa")

    nuevo_id = str(uuid.uuid4())

    payload = {
        "id": nuevo_id,
        "id_empresa": id_empresa,
        "nombre": datos.nombre,
        "telefono": datos.telefono,
        "email": datos.email,
        "direccion": datos.direccion,
        "rfc": datos.rfc,
        "codigo_postal": datos.codigo_postal,
        "fecha_creacion": datetime.utcnow().isoformat(),
    }

    respuesta = supabase.table("clientes").insert(payload).execute()

    return {"mensaje": "Cliente creado", "data": respuesta.data}


@router.get("/")
def listar_clientes(usuario=Depends(get_current_user)):
    id_empresa = usuario.get("id_raiz")
    resp = (
        supabase.table("clientes")
        .select("*")
        .eq("id_empresa", id_empresa)
        .execute()
    )
    return resp.data


@router.get("/{cliente_id}")
def obtener_cliente(cliente_id: str, usuario=Depends(get_current_user)):
    id_empresa = usuario.get("id_raiz")
    resp = (
        supabase.table("clientes")
        .select("*")
        .eq("id", cliente_id)
        .eq("id_empresa", id_empresa)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return resp.data[0]


@router.put("/{cliente_id}")
def actualizar_cliente(cliente_id: str, datos: ClienteCrear, usuario=Depends(get_current_user)):
    id_empresa = usuario.get("id_raiz")
    payload = {
        "nombre": datos.nombre,
        "telefono": datos.telefono,
        "email": datos.email,
        "direccion": datos.direccion,
        "rfc": datos.rfc,
        "codigo_postal": datos.codigo_postal,
    }
    resp = (
        supabase.table("clientes")
        .update(payload)
        .eq("id", cliente_id)
        .eq("id_empresa", id_empresa)
        .execute()
    )
    return {"mensaje": "Cliente actualizado", "data": resp.data}


@router.delete("/{cliente_id}")
def eliminar_cliente(cliente_id: str, usuario=Depends(get_current_user)):
    id_empresa = usuario.get("id_raiz")
    supabase.table("clientes").delete().eq("id", cliente_id).eq("id_empresa", id_empresa).execute()
    return {"mensaje": "Cliente eliminado"}
