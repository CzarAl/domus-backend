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

    # =============================
    # DETERMINAR SUCURSAL REAL
    # =============================

    if nivel in ["admin_master", "usuario"]:
        if not datos.id_sucursal:
            raise HTTPException(status_code=400, detail="Debe especificar sucursal")
        id_sucursal = datos.id_sucursal
    else:
        if not id_sucursal_usuario:
            raise HTTPException(status_code=403, detail="Vendedor sin sucursal asignada")
        id_sucursal = id_sucursal_usuario

    # =============================
    # OBTENER SESIÓN ABIERTA
    # =============================

    sesion = supabase.table("sesiones_caja") \
        .select("*") \
        .eq("id_raiz", id_raiz) \
        .eq("id_sucursal", id_sucursal) \
        .eq("abierta", True) \
        .execute()

    if not sesion.data:
        raise HTTPException(status_code=400, detail="No hay caja abierta")

    id_sesion = sesion.data[0]["id"]

    # =============================
    # VALIDAR CLIENTE
    # =============================

    cliente = supabase.table("clientes") \
        .select("*") \
        .eq("id", datos.id_cliente) \
        .eq("id_raiz", id_raiz) \
        .execute()

    if not cliente.data:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    total = 0
    detalles = []

    # =============================
    # VALIDAR PRODUCTOS Y CALCULAR
    # =============================

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

        stock_actual = producto.get("stock", 0)

        if stock_actual < item.cantidad:
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

        # Descontar stock vía RPC
        resultado_stock = supabase.rpc("descontar_stock", {
            "producto_id": item.id_producto,
            "cantidad": item.cantidad
        }).execute()

        if not resultado_stock.data:
            raise HTTPException(
                status_code=400,
                detail=f"No se pudo descontar stock para {producto['nombre']}"
            )

    # =============================
    # CREAR VENTA
    # =============================

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

    if not venta.data:
        raise HTTPException(status_code=500, detail="Error creando venta")

    id_venta = venta.data[0]["id"]

    # =============================
    # INSERTAR DETALLES
    # =============================

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

    # =============================
    # MOVIMIENTO DE CAJA
    # =============================

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

    # =============================
    # AUDITORÍA
    # =============================

    supabase.table("auditoria_tienda").insert({
        "id_raiz": id_raiz,
        "id_sucursal": id_sucursal,
        "id_usuario": id_usuario,
        "accion": "CREAR_VENTA",
        "descripcion": f"Venta creada folio {folio} por {total}",
        "fecha_hora": datetime.utcnow().isoformat()
    }).execute()

    return {
        "mensaje": "Venta creada correctamente",
        "folio": folio,
        "total": total
    }