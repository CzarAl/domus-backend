from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from database import supabase
from datetime import date
from main import get_current_user

router = APIRouter(prefix="/inventario", tags=["Inventario"])


# ==============================
# MODELO
# ==============================

class ProductoCrear(BaseModel):
    nombre: str
    descripcion: str | None = None
    foto_url: str | None = None
    costo_compra: float
    precio_venta: float
    stock: int | None = 0
    numero_serie: str | None = None
    fecha_adquisicion: date
    id_sucursal: str | None = None


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
# CREAR PRODUCTO
# ==============================

@router.post("/")
def crear_producto(datos: ProductoCrear, usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")
    id_sucursal = resolver_sucursal(usuario, datos.id_sucursal)

    respuesta = supabase.table("inventario").insert({
        "nombre": datos.nombre,
        "descripcion": datos.descripcion,
        "foto_url": datos.foto_url,
        "costo_compra": datos.costo_compra,
        "precio_venta": datos.precio_venta,
        "stock": datos.stock,
        "numero_serie": datos.numero_serie,
        "fecha_adquisicion": datos.fecha_adquisicion.isoformat(),
        "id_raiz": id_raiz,
        "id_sucursal": id_sucursal
    }).execute()

    return {"mensaje": "Producto creado correctamente", "data": respuesta.data}


# ==============================
# LISTAR PRODUCTOS
# ==============================

@router.get("/")
def listar_productos(usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")
    nivel = usuario.get("nivel")
    id_sucursal = usuario.get("id_sucursal")

    query = supabase.table("inventario") \
        .select("*") \
        .eq("id_raiz", id_raiz)

    if nivel not in ["admin_master", "usuario"]:
        if not id_sucursal:
            raise HTTPException(status_code=403, detail="Vendedor sin sucursal asignada")
        query = query.eq("id_sucursal", id_sucursal)

    return query.execute().data


# ==============================
# VER PRODUCTO
# ==============================

@router.get("/{producto_id}")
def obtener_producto(producto_id: str, usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")
    nivel = usuario.get("nivel")
    id_sucursal = usuario.get("id_sucursal")

    query = supabase.table("inventario") \
        .select("*") \
        .eq("id", producto_id) \
        .eq("id_raiz", id_raiz)

    if nivel not in ["admin_master", "usuario"]:
        query = query.eq("id_sucursal", id_sucursal)

    respuesta = query.execute()

    if not respuesta.data:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    return respuesta.data[0]


# ==============================
# ACTUALIZAR PRODUCTO
# ==============================

@router.put("/{producto_id}")
def actualizar_producto(producto_id: str, datos: ProductoCrear, usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")
    id_sucursal = resolver_sucursal(usuario, datos.id_sucursal)

    respuesta = supabase.table("inventario") \
        .update({
            "nombre": datos.nombre,
            "descripcion": datos.descripcion,
            "foto_url": datos.foto_url,
            "costo_compra": datos.costo_compra,
            "precio_venta": datos.precio_venta,
            "stock": datos.stock,
            "numero_serie": datos.numero_serie,
            "fecha_adquisicion": datos.fecha_adquisicion.isoformat(),
            "id_sucursal": id_sucursal
        }) \
        .eq("id", producto_id) \
        .eq("id_raiz", id_raiz) \
        .execute()

    return {"mensaje": "Producto actualizado", "data": respuesta.data}


# ==============================
# ELIMINAR PRODUCTO
# ==============================

@router.delete("/{producto_id}")
def eliminar_producto(producto_id: str, usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")
    nivel = usuario.get("nivel")
    id_sucursal = usuario.get("id_sucursal")

    query = supabase.table("inventario") \
        .delete() \
        .eq("id", producto_id) \
        .eq("id_raiz", id_raiz)

    if nivel not in ["admin_master", "usuario"]:
        query = query.eq("id_sucursal", id_sucursal)

    query.execute()

    return {"mensaje": "Producto eliminado"}


# ==============================
# BAJO STOCK
# ==============================

@router.get("/bajo-stock")
def bajo_stock(limite: int = 5, usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")
    nivel = usuario.get("nivel")
    id_sucursal = usuario.get("id_sucursal")

    query = supabase.table("inventario") \
        .select("*") \
        .eq("id_raiz", id_raiz) \
        .lte("stock", limite)

    if nivel not in ["admin_master", "usuario"]:
        query = query.eq("id_sucursal", id_sucursal)

    return query.execute().data


# ==============================
# VALOR TOTAL INVENTARIO
# ==============================

@router.get("/valor-total")
def valor_total(usuario=Depends(get_current_user)):

    productos = listar_productos(usuario)

    total = 0
    for p in productos:
        total += (p.get("stock") or 0) * (p.get("costo_compra") or 0)

    return {"valor_total_inventario": total}


# ==============================
# TOP PRODUCTOS VENDIDOS
# ==============================

@router.get("/top-vendidos")
def top_vendidos(usuario=Depends(get_current_user)):

    id_raiz = usuario.get("id_raiz")
    nivel = usuario.get("nivel")
    id_sucursal = usuario.get("id_sucursal")

    query = supabase.table("detalles_venta") \
        .select("id_producto, cantidad") \
        .eq("id_raiz", id_raiz)

    if nivel not in ["admin_master", "usuario"]:
        query = query.eq("id_sucursal", id_sucursal)

    detalles = query.execute().data

    if not detalles:
        return []

    resumen = {}

    for d in detalles:
        producto_id = d["id_producto"]
        resumen[producto_id] = resumen.get(producto_id, 0) + d["cantidad"]

    productos = supabase.table("inventario") \
        .select("id, nombre") \
        .in_("id", list(resumen.keys())) \
        .execute().data

    nombres = {p["id"]: p["nombre"] for p in productos}

    resultado = [
        {
            "id_producto": pid,
            "nombre": nombres.get(pid, "Desconocido"),
            "total_vendido": total
        }
        for pid, total in resumen.items()
    ]

    return sorted(resultado, key=lambda x: x["total_vendido"], reverse=True)


# ==============================
# KPI INVENTARIO
# ==============================

@router.get("/kpi")
def kpi_inventario(usuario=Depends(get_current_user)):

    productos = listar_productos(usuario)

    total_productos = len(productos)
    unidades_totales = 0
    valor_costo = 0
    valor_venta = 0

    for p in productos:
        stock = p.get("stock") or 0
        costo = p.get("costo_compra") or 0
        venta = p.get("precio_venta") or 0

        unidades_totales += stock
        valor_costo += stock * costo
        valor_venta += stock * venta

    return {
        "total_productos": total_productos,
        "unidades_totales": unidades_totales,
        "valor_inventario_costo": valor_costo,
        "valor_inventario_venta": valor_venta,
        "ganancia_potencial": valor_venta - valor_costo
    }