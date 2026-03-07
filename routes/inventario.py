from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from database import supabase
from datetime import datetime
from dependencies import get_current_user
import uuid

router = APIRouter(prefix="/inventario", tags=["Inventario"])


# ==============================
# MODELO
# ==============================

class InventarioCrear(BaseModel):
    id_producto: str
    id_sucursal: str
    stock: int = 0
    stock_minimo: int = 0


# ==============================
# CREAR REGISTRO INVENTARIO
# ==============================

@router.post("/")
def crear_inventario(datos: InventarioCrear, usuario=Depends(get_current_user)):

    id_empresa = usuario["id_raiz"]

    respuesta = supabase.table("inventario").insert({
        "id": str(uuid.uuid4()),
        "id_empresa": id_empresa,
        "id_sucursal": datos.id_sucursal,
        "id_producto": datos.id_producto,
        "stock": datos.stock,
        "stock_minimo": datos.stock_minimo,
        "fecha_actualizacion": datetime.utcnow().isoformat(),
        "stock_reservado": 0
    }).execute()

    return {"mensaje": "Inventario creado", "data": respuesta.data}


# ==============================
# LISTAR INVENTARIO
# ==============================

@router.get("/")
def listar_inventario(usuario=Depends(get_current_user)):

    id_empresa = usuario["id_raiz"]

    return (
        supabase.table("inventario")
        .select("*")
        .eq("id_empresa", id_empresa)
        .execute()
        .data
    )


# ==============================
# ACTUALIZAR STOCK
# ==============================

class ActualizarStock(BaseModel):
    stock: int | None = None
    stock_minimo: int | None = None


@router.put("/{inventario_id}")
def actualizar_inventario(inventario_id: str, datos: ActualizarStock, usuario=Depends(get_current_user)):

    id_empresa = usuario["id_raiz"]

    update_data = {}

    if datos.stock is not None:
        update_data["stock"] = datos.stock

    if datos.stock_minimo is not None:
        update_data["stock_minimo"] = datos.stock_minimo

    update_data["fecha_actualizacion"] = datetime.utcnow().isoformat()

    respuesta = supabase.table("inventario") \
        .update(update_data) \
        .eq("id", inventario_id) \
        .eq("id_empresa", id_empresa) \
        .execute()

    return {"mensaje": "Inventario actualizado", "data": respuesta.data}


# ==============================
# ELIMINAR INVENTARIO
# ==============================

@router.delete("/{inventario_id}")
def eliminar_inventario(inventario_id: str, usuario=Depends(get_current_user)):

    id_empresa = usuario["id_raiz"]

    supabase.table("inventario") \
        .delete() \
        .eq("id", inventario_id) \
        .eq("id_empresa", id_empresa) \
        .execute()

    return {"mensaje": "Inventario eliminado"}