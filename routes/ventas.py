from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from database import supabase
from datetime import datetime
import uuid
from dependencies import get_current_user

router = APIRouter(prefix="/ventas", tags=["Ventas"])


# ==============================
# MODELOS
# ==============================

class ItemVenta(BaseModel):
    id_producto: str
    cantidad: int


class VentaCrear(BaseModel):
    id_cliente: str
    metodo_pago: str
    items: list[ItemVenta]
    id_sucursal: str | None = None


# ==============================
# LISTAR VENTAS
# ==============================

@router.get("/")
def listar_ventas(usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")
    nivel = usuario.get("nivel")
    id_sucursal = usuario.get("id_sucursal")

    query = supabase.table("ventas") \
        .select("*") \
        .eq("id_raiz", id_raiz)

    if nivel not in ["admin_master", "usuario"]:
        query = query.eq("id_sucursal", id_sucursal)

    return query.execute().data


# ==============================
# CREAR VENTA COMPLETA
# ==============================

@router.post("/")
def crear_venta(datos: VentaCrear, usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")
    id_usuario = usuario.get("id_usuario")
    nivel = usuario.get("nivel")
    id_sucursal_usuario = usuario.get("id_sucursal")

    if not id_raiz:
        raise HTTPException(status_code=400, detail="Usuario sin id_raiz")

    # Determinar sucursal real
    if nivel in ["admin_master", "usuario"]:
        if not datos.id_sucursal:
            raise HTTPException(status_code=400, detail="Debe especificar sucursal")
        id_sucursal = datos.id_sucursal
    else:
        if not id_sucursal_usuario:
            raise HTTPException(status_code=403, detail="Vendedor sin sucursal asignada")
        id_sucursal = id_sucursal_usuario

    # Validar sesión de caja abierta
    sesion = supabase.table("sesiones_caja") \
        .select("*") \
        .eq("id_raiz", id_raiz) \
        .eq("id_sucursal", id_sucursal) \
        .eq("abierta", True) \
        .execute()

    if not sesion.data:
        raise HTTPException(status_code=400, detail="No hay caja abierta")

    id_sesion = sesion.data[0]["id"]

    # Validar cliente
    cliente = supabase.table("clientes") \
        .select("*") \
        .eq("id", datos.id_cliente) \
        .eq("id_raiz", id_raiz) \
        .execute()

    if not cliente.data:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    total = 0
    detalles = []

    # Validar productos
    for item in datos.items:

        producto = supabase.table("inventario") \
            .select("*") \
            .eq("id", item.id_producto) \
            .eq("id_raiz", id_raiz) \
            .eq("id_sucursal", id_sucursal) \
            .execute()

        if not producto.data:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        producto = producto.data[0]

        if producto.get("stock", 0) < item.cantidad:
            raise HTTPException(
                status_code=400,
                detail=f"Stock insuficiente para {producto['nombre']}"
            )

        precio = float(producto["precio_venta"])
        subtotal = precio * item.cantidad
        total += subtotal

        detalles.append({
            "id_producto": item.id_producto,
            "cantidad": item.cantidad,
            "precio_unitario": precio,
            "subtotal": subtotal
        })

        # Descontar stock
        supabase.rpc("descontar_stock", {
            "producto_id": item.id_producto,
            "cantidad": item.cantidad
        }).execute()

    # Crear venta
    folio = str(uuid.uuid4())[:8]

    venta = supabase.table("ventas").insert({
        "folio": folio,
        "id_cliente": datos.id_cliente,
        "id_usuario": id_usuario,
        "id_raiz": id_raiz,
        "id_sucursal": id_sucursal,
        "total": total,
        "metodo_pago": datos.metodo_pago,
        "fecha": datetime.utcnow().isoformat()
    }).execute()

    id_venta = venta.data[0]["id"]

    # Insertar detalles
    for d in detalles:
        supabase.table("detalles_venta").insert({
            "id_venta": id_venta,
            "id_producto": d["id_producto"],
            "cantidad": d["cantidad"],
            "precio_unitario": d["precio_unitario"],
            "subtotal": d["subtotal"],
            "id_raiz": id_raiz,
            "id_sucursal": id_sucursal
        }).execute()

    # Movimiento de caja
    supabase.table("movimientos_caja").insert({
        "id_sesion": id_sesion,
        "tipo": "ingreso",
        "concepto": f"Venta folio {folio}",
        "monto": total,
        "id_usuario": id_usuario,
        "id_raiz": id_raiz,
        "id_sucursal": id_sucursal,
        "fecha": datetime.utcnow().isoformat()
    }).execute()

    return {
        "mensaje": "Venta creada correctamente",
        "folio": folio,
        "total": total
    }