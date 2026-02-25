from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from dependencies import obtener_usuario_actual
from database import supabase
from datetime import date

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
# CREAR PRODUCTO
# ==============================

@router.post("/")
def crear_producto(datos: ProductoCrear, usuario=Depends(obtener_usuario_actual)):

    id_raiz = usuario.get("id_raiz")

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
        "id_sucursal": datos.id_sucursal
    }).execute()

    return {
        "mensaje": "Producto creado correctamente",
        "data": respuesta.data
    }

# ==============================
# LISTAR PRODUCTOS
# ==============================

@router.get("/")
def listar_productos(usuario=Depends(obtener_usuario_actual)):

    id_raiz = usuario.get("id_raiz")

    respuesta = supabase.table("inventario") \
        .select("*") \
        .eq("id_raiz", id_raiz) \
        .execute()

    return respuesta.data




# ==============================
# ACTUALIZAR PRODUCTO
# ==============================

@router.put("/{producto_id}")
def actualizar_producto(producto_id: str, datos: ProductoCrear, usuario=Depends(obtener_usuario_actual)):

    id_raiz = usuario.get("id_raiz")

    respuesta = supabase.table("inventario") \
        .update({
            "nombre": datos.nombre,
            "descripcion": datos.descripcion,
            "foto_url": datos.foto_url,
            "costo_compra": datos.costo_compra,
            "precio_venta": datos.precio_venta,
            "material": datos.material,
            "numero_serie": datos.numero_serie,
            "fecha_adquisicion": str(datos.fecha_adquisicion),
            "id_sucursal": datos.id_sucursal
        }) \
        .eq("id", producto_id) \
        .eq("id_raiz", id_raiz) \
        .execute()

    return {"mensaje": "Producto actualizado", "data": respuesta.data}


# ==============================
# ELIMINAR PRODUCTO
# ==============================

@router.delete("/{producto_id}")
def eliminar_producto(producto_id: str, usuario=Depends(obtener_usuario_actual)):

    id_raiz = usuario.get("id_raiz")

    supabase.table("inventario") \
        .delete() \
        .eq("id", producto_id) \
        .eq("id_raiz", id_raiz) \
        .execute()

    return {"mensaje": "Producto eliminado"}

# bajo stock
@router.get("/bajo-stock")
def bajo_stock(limite: int = 5, usuario=Depends(obtener_usuario_actual)):

    productos = supabase.table("inventario") \
        .select("*") \
        .eq("id_raiz", usuario["id_raiz"]) \
        .lte("stock", limite) \
        .execute()

    return productos.data

# valor total
@router.get("/valor-total")
def valor_total(usuario=Depends(obtener_usuario_actual)):

    productos = supabase.table("inventario") \
        .select("stock, costo_compra") \
        .eq("id_raiz", usuario["id_raiz"]) \
        .execute()

    total = 0

    for p in productos.data:
        total += (p["stock"] or 0) * (p["costo_compra"] or 0)

    return {"valor_total_inventario": total}

# top productos vendidos

@router.get("/top-vendidos")
def top_vendidos(usuario=Depends(obtener_usuario_actual)):

    # Obtener detalles de venta
    detalles = supabase.table("detalles_venta") \
        .select("id_producto, cantidad") \
        .eq("id_raiz", usuario["id_raiz"]) \
        .execute()

    if not detalles.data:
        return []

    resumen = {}

    # Sumar cantidades por producto
    for d in detalles.data:
        producto_id = d.get("id_producto")
        cantidad = d.get("cantidad", 0)

        if producto_id not in resumen:
            resumen[producto_id] = 0

        resumen[producto_id] += cantidad

    # Obtener nombres de productos
    productos_ids = list(resumen.keys())

    productos = supabase.table("inventario") \
        .select("id, nombre") \
        .in_("id", productos_ids) \
        .execute()

    nombres = {p["id"]: p["nombre"] for p in productos.data}

    resultado = []

    for producto_id, total in resumen.items():
        resultado.append({
            "id_producto": producto_id,
            "nombre": nombres.get(producto_id, "Desconocido"),
            "total_vendido": total
        })

    # Ordenar de mayor a menor
    resultado.sort(key=lambda x: x["total_vendido"], reverse=True)

    return resultado



# kpi

@router.get("/kpi")
def kpi_inventario(usuario=Depends(obtener_usuario_actual)):

    productos = supabase.table("inventario") \
        .select("stock, costo_compra, precio_venta") \
        .eq("id_raiz", usuario["id_raiz"]) \
        .execute()

    total_productos = len(productos.data)
    unidades_totales = 0
    valor_costo = 0
    valor_venta = 0

    for p in productos.data:
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
    
# productos mas rentables

@router.get("/kpi")
def kpi_inventario(usuario=Depends(obtener_usuario_actual)):

    productos = supabase.table("inventario") \
        .select("stock, costo_compra, precio_venta") \
        .eq("id_raiz", usuario["id_raiz"]) \
        .execute()

    total_productos = len(productos.data)
    unidades_totales = 0
    valor_costo = 0
    valor_venta = 0

    for p in productos.data:
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

# dashboard

@router.get("/dashboard")
def dashboard_financiero(usuario=Depends(obtener_usuario_actual)):

    ventas = supabase.table("ventas") \
        .select("total, fecha") \
        .eq("id_raiz", usuario["id_raiz"]) \
        .execute()

    if not ventas.data:
        return {
            "ventas_totales": 0,
            "total_ingresos": 0
        }

    ventas_totales = len(ventas.data)
    total_ingresos = sum(v.get("total") or 0 for v in ventas.data)

    return {
        "ventas_totales": ventas_totales,
        "total_ingresos": total_ingresos
    }
    
# ==============================
# VER PRODUCTO
# ==============================

@router.get("/{producto_id}")
def obtener_producto(producto_id: str, usuario=Depends(obtener_usuario_actual)):

    id_raiz = usuario.get("id_raiz")

    respuesta = supabase.table("inventario") \
        .select("*") \
        .eq("id", producto_id) \
        .eq("id_raiz", id_raiz) \
        .execute()

    if not respuesta.data:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    return respuesta.data[0]