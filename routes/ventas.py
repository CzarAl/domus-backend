from datetime import datetime
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import supabase
from dependencies import get_current_user

router = APIRouter(prefix="/ventas", tags=["Ventas"])


class ItemVentaNueva(BaseModel):
    id_producto: str | None = None
    id_servicio: str | None = None
    cantidad: int = Field(gt=0)
    precio_unitario: float | None = Field(default=None, gt=0)


class VentaNueva(BaseModel):
    id_sucursal: str
    id_cliente: str | None = None
    metodo_pago: str
    subtotal: float | None = None
    iva: float = 0
    flete: float = 0
    total: float | None = None
    comentarios: str | None = None
    detalles: list[ItemVentaNueva]
    confirmar_transferencia: bool = False
    generar_pdf: bool = False
    origen_venta: str = "fisica"


def _id_empresa(usuario: dict) -> str:
    id_empresa = usuario.get("id_raiz")
    if not id_empresa:
        raise HTTPException(status_code=400, detail="Usuario sin empresa")
    return id_empresa


def _extraer_id_venta(data: Any) -> str | None:
    if isinstance(data, str):
        return data

    if isinstance(data, dict):
        return data.get("id_venta") or data.get("id")

    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get("id_venta") or first.get("id")

    return None


def _venta_reciente(id_empresa: str, id_sucursal: str, total: float | None) -> str | None:
    try:
        q = (
            supabase.table("ventas")
            .select("id")
            .eq("id_empresa", id_empresa)
            .eq("id_sucursal", id_sucursal)
            .order("fecha", desc=True)
            .limit(1)
        )
        if total is not None:
            q = q.eq("total", total)
        resp = q.execute()
        if resp.data:
            return resp.data[0].get("id")
    except Exception:
        return None
    return None


def _map_productos(id_empresa: str, ids_producto: list[str]) -> dict[str, dict]:
    if not ids_producto:
        return {}

    productos_resp = (
        supabase.table("productos")
        .select("*")
        .eq("id_empresa", id_empresa)
        .in_("id", list(set(ids_producto)))
        .execute()
    )

    return {p["id"]: p for p in (productos_resp.data or []) if p.get("id")}


def _precio_producto(p: dict) -> float:
    return float(p.get("precio") if p.get("precio") is not None else p.get("precio_venta") or 0)


def _normalizar_detalle(det: dict, productos_map: dict[str, dict]) -> dict:
    prod = productos_map.get(det.get("id_producto")) if det.get("id_producto") else None
    return {
        "id": det.get("id"),
        "id_venta": det.get("id_venta"),
        "id_producto": det.get("id_producto"),
        "id_servicio": det.get("id_servicio"),
        "cantidad": det.get("cantidad") or 0,
        "precio_unitario": det.get("precio_unitario") or 0,
        "subtotal": det.get("subtotal") or 0,
        "nombre_producto": det.get("nombre_producto") or (prod.get("nombre") if prod else None),
        "descripcion_producto": det.get("descripcion_producto") or (prod.get("descripcion") if prod else None),
        "codigo_producto": det.get("codigo_producto") or (prod.get("codigo_producto") if prod else None),
        "foto_url": det.get("foto_url") or (prod.get("foto_url") if prod else prod.get("imagen_url") if prod else None),
    }


def _listar_detalles_enriquecidos(id_empresa: str, ids_venta: list[str]) -> dict[str, list[dict]]:
    if not ids_venta:
        return {}

    detalles = (
        supabase.table("detalle_ventas")
        .select("*")
        .in_("id_venta", ids_venta)
        .execute()
    ).data or []

    ids_producto = [d.get("id_producto") for d in detalles if d.get("id_producto")]
    productos_map = _map_productos(id_empresa, ids_producto)

    agrupado: dict[str, list[dict]] = {}
    for det in detalles:
        id_venta = det.get("id_venta")
        if not id_venta:
            continue
        agrupado.setdefault(id_venta, []).append(_normalizar_detalle(det, productos_map))
    return agrupado


def _actualizar_snapshot_detalles(id_venta: str, productos_map: dict[str, dict]):
    if not productos_map:
        return

    detalles = (
        supabase.table("detalle_ventas")
        .select("id,id_producto")
        .eq("id_venta", id_venta)
        .execute()
    ).data or []

    for det in detalles:
        id_producto = det.get("id_producto")
        if not id_producto or id_producto not in productos_map:
            continue

        prod = productos_map[id_producto]
        payload_full = {
            "nombre_producto": prod.get("nombre"),
            "descripcion_producto": prod.get("descripcion"),
            "codigo_producto": prod.get("codigo_producto"),
            "foto_url": prod.get("foto_url") or prod.get("imagen_url"),
        }
        payload_min = {
            "nombre_producto": prod.get("nombre"),
        }

        for payload in (payload_full, payload_min):
            try:
                supabase.table("detalle_ventas").update(payload).eq("id", det.get("id")).execute()
                break
            except Exception:
                continue


def _validar_stock_suficiente(id_empresa: str, id_sucursal: str, detalles: list[dict]):
    por_producto: dict[str, int] = {}
    for d in detalles:
        if d.get("id_producto"):
            por_producto[d["id_producto"]] = por_producto.get(d["id_producto"], 0) + int(d.get("cantidad") or 0)

    if not por_producto:
        return

    inventario = (
        supabase.table("inventario")
        .select("id,id_producto,stock")
        .eq("id_empresa", id_empresa)
        .eq("id_sucursal", id_sucursal)
        .in_("id_producto", list(por_producto.keys()))
        .execute()
    ).data or []

    inv_map = {i.get("id_producto"): i for i in inventario if i.get("id_producto")}

    faltantes = []
    for id_producto, qty in por_producto.items():
        reg = inv_map.get(id_producto)
        stock = int(reg.get("stock") or 0) if reg else 0
        if stock < qty:
            faltantes.append({"id_producto": id_producto, "stock": stock, "requerido": qty})

    if faltantes:
        raise HTTPException(status_code=400, detail={"mensaje": "Stock insuficiente", "faltantes": faltantes})


def _ajustar_stock_si_no_lo_hizo_rpc(id_empresa: str, id_sucursal: str, detalles: list[dict], stock_antes: dict[str, int]):
    por_producto: dict[str, int] = {}
    for d in detalles:
        if d.get("id_producto"):
            por_producto[d["id_producto"]] = por_producto.get(d["id_producto"], 0) + int(d.get("cantidad") or 0)

    if not por_producto:
        return

    inventario_despues = (
        supabase.table("inventario")
        .select("id,id_producto,stock")
        .eq("id_empresa", id_empresa)
        .eq("id_sucursal", id_sucursal)
        .in_("id_producto", list(por_producto.keys()))
        .execute()
    ).data or []

    for inv in inventario_despues:
        id_producto = inv.get("id_producto")
        if id_producto not in por_producto:
            continue

        before = stock_antes.get(id_producto, int(inv.get("stock") or 0))
        after = int(inv.get("stock") or 0)
        esperado = por_producto[id_producto]
        aplicado = max(before - after, 0)
        faltante = esperado - aplicado

        if faltante > 0:
            nuevo_stock = max(after - faltante, 0)
            supabase.table("inventario").update(
                {
                    "stock": nuevo_stock,
                    "fecha_actualizacion": datetime.utcnow().isoformat(),
                }
            ).eq("id", inv.get("id")).execute()


def _registrar_movimiento_caja_venta(id_empresa: str, id_sucursal: str, id_usuario: str | None, monto: float, metodo_pago: str, id_venta: str, origen_venta: str):
    sesion = (
        supabase.table("sesiones_caja")
        .select("id")
        .eq("id_empresa", id_empresa)
        .eq("id_sucursal", id_sucursal)
        .eq("abierta", True)
        .order("fecha_apertura", desc=True)
        .limit(1)
        .execute()
    ).data or []

    if not sesion:
        return

    id_sesion = sesion[0].get("id")
    if not id_sesion:
        return

    payload = {
        "id": str(uuid.uuid4()),
        "id_sesion": id_sesion,
        "id_empresa": id_empresa,
        "id_sucursal": id_sucursal,
        "id_usuario": id_usuario,
        "tipo_movimiento": "entrada",
        "concepto": "venta_online" if origen_venta == "online" else "venta_fisica",
        "metodo_pago": metodo_pago,
        "monto": float(monto or 0),
        "referencia": id_venta,
        "fecha_creacion": datetime.utcnow().isoformat(),
    }

    payload_min = {
        "id": payload["id"],
        "id_sesion": id_sesion,
        "id_empresa": id_empresa,
        "tipo_movimiento": "entrada",
        "concepto": "venta_online" if origen_venta == "online" else "venta_fisica",
        "monto": float(monto or 0),
        "fecha_creacion": datetime.utcnow().isoformat(),
    }

    try:
        supabase.table("movimientos_caja").insert(payload).execute()
    except Exception:
        try:
            supabase.table("movimientos_caja").insert(payload_min).execute()
        except Exception:
            pass


@router.get("/")
def listar_ventas(usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)

    ventas = (
        supabase.table("ventas")
        .select("*")
        .eq("id_empresa", id_empresa)
        .order("fecha", desc=True)
        .execute()
    ).data or []

    ids_venta = [v.get("id") for v in ventas if v.get("id")]
    detalles_map = _listar_detalles_enriquecidos(id_empresa, ids_venta)

    ids_sucursal = [v.get("id_sucursal") for v in ventas if v.get("id_sucursal")]
    ids_cliente = [v.get("id_cliente") for v in ventas if v.get("id_cliente")]

    suc_map = {}
    cli_map = {}

    if ids_sucursal:
        suc_resp = (
            supabase.table("sucursales")
            .select("id,nombre")
            .in_("id", list(set(ids_sucursal)))
            .execute()
        ).data or []
        suc_map = {s.get("id"): s.get("nombre") for s in suc_resp if s.get("id")}

    if ids_cliente:
        cli_resp = (
            supabase.table("clientes")
            .select("id,nombre")
            .in_("id", list(set(ids_cliente)))
            .execute()
        ).data or []
        cli_map = {c.get("id"): c.get("nombre") for c in cli_resp if c.get("id")}

    salida = []
    for venta in ventas:
        vid = venta.get("id")
        salida.append(
            {
                **venta,
                "origen_venta": venta.get("origen_venta") or "fisica",
                "sucursal_nombre": suc_map.get(venta.get("id_sucursal")),
                "cliente_nombre": cli_map.get(venta.get("id_cliente")),
                "detalles": detalles_map.get(vid, []),
            }
        )

    return salida


@router.post("/nueva")
def crear_venta_nueva(datos: VentaNueva, usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)
    id_vendedor = usuario.get("id_vendedor")

    if not datos.detalles:
        raise HTTPException(status_code=400, detail="Debes enviar al menos un detalle")

    ids_producto = [d.id_producto for d in datos.detalles if d.id_producto]
    productos_map = _map_productos(id_empresa, [p for p in ids_producto if p])

    for d in datos.detalles:
        if d.id_producto and d.id_producto not in productos_map:
            raise HTTPException(status_code=404, detail=f"Producto no encontrado: {d.id_producto}")

    detalles_rpc = []
    subtotal_calc = 0.0

    for d in datos.detalles:
        precio = d.precio_unitario
        if precio is None and d.id_producto:
            precio = _precio_producto(productos_map[d.id_producto])
        if precio is None:
            raise HTTPException(status_code=400, detail="Cada detalle requiere precio_unitario o un producto con precio")

        subtotal_calc += float(precio) * int(d.cantidad)

        detalle = {
            "cantidad": int(d.cantidad),
            "precio_unitario": float(precio),
        }
        if d.id_producto:
            detalle["id_producto"] = d.id_producto
        if d.id_servicio:
            detalle["id_servicio"] = d.id_servicio

        detalles_rpc.append(detalle)

    _validar_stock_suficiente(id_empresa, datos.id_sucursal, detalles_rpc)

    inventario_antes = (
        supabase.table("inventario")
        .select("id_producto,stock")
        .eq("id_empresa", id_empresa)
        .eq("id_sucursal", datos.id_sucursal)
        .in_("id_producto", ids_producto if ids_producto else ["00000000-0000-0000-0000-000000000000"])
        .execute()
    ).data or []
    stock_antes = {i.get("id_producto"): int(i.get("stock") or 0) for i in inventario_antes if i.get("id_producto")}

    subtotal = float(datos.subtotal if datos.subtotal is not None else subtotal_calc)
    total = float(datos.total if datos.total is not None else subtotal + float(datos.iva or 0) + float(datos.flete or 0))

    response = supabase.rpc(
        "crear_venta_completa",
        {
            "p_id_empresa": id_empresa,
            "p_id_sucursal": datos.id_sucursal,
            "p_id_vendedor": id_vendedor,
            "p_id_cliente": datos.id_cliente,
            "p_metodo_pago": datos.metodo_pago,
            "p_subtotal": subtotal,
            "p_iva": float(datos.iva or 0),
            "p_flete": float(datos.flete or 0),
            "p_total": total,
            "p_comentarios": datos.comentarios,
            "p_detalles": detalles_rpc,
            "p_confirmar_transferencia": datos.confirmar_transferencia,
        },
    ).execute()

    id_venta = _extraer_id_venta(response.data)
    if not id_venta:
        id_venta = _venta_reciente(id_empresa, datos.id_sucursal, total) or _venta_reciente(id_empresa, datos.id_sucursal, None)

    if not id_venta:
        return {
            "mensaje": "Venta registrada",
            "data": response.data,
            "generar_pdf": datos.generar_pdf,
        }

    _actualizar_snapshot_detalles(id_venta, productos_map)
    _ajustar_stock_si_no_lo_hizo_rpc(id_empresa, datos.id_sucursal, detalles_rpc, stock_antes)
    try:
        supabase.table("ventas").update({"origen_venta": datos.origen_venta or "fisica"}).eq("id", id_venta).execute()
    except Exception:
        pass

    _registrar_movimiento_caja_venta(id_empresa, datos.id_sucursal, usuario.get("id_usuario"), total, datos.metodo_pago, id_venta, datos.origen_venta or "fisica")

    venta = (
        supabase.table("ventas")
        .select("*")
        .eq("id", id_venta)
        .limit(1)
        .execute()
    ).data
    venta_row = venta[0] if venta else {}

    detalles_enriquecidos = _listar_detalles_enriquecidos(id_empresa, [id_venta]).get(id_venta, [])

    empresa = (
        supabase.table("empresas")
        .select("id,nombre")
        .eq("id", id_empresa)
        .limit(1)
        .execute()
    ).data or []

    cliente = []
    if datos.id_cliente:
        cliente = (
            supabase.table("clientes")
            .select("id,nombre,telefono,email,direccion")
            .eq("id", datos.id_cliente)
            .limit(1)
            .execute()
        ).data or []

    sucursal = (
        supabase.table("sucursales")
        .select("id,nombre")
        .eq("id", datos.id_sucursal)
        .limit(1)
        .execute()
    ).data or []

    comprobante = {
        "venta_id": id_venta,
        "fecha": venta_row.get("fecha") or datetime.utcnow().isoformat(),
        "empresa": empresa[0] if empresa else {"id": id_empresa},
        "sucursal": sucursal[0] if sucursal else {"id": datos.id_sucursal},
        "cliente": cliente[0] if cliente else None,
        "metodo_pago": datos.metodo_pago,
        "origen_venta": datos.origen_venta or "fisica",
        "subtotal": subtotal,
        "iva": float(datos.iva or 0),
        "flete": float(datos.flete or 0),
        "total": total,
        "detalles": detalles_enriquecidos,
        "comentarios": datos.comentarios,
    }

    if datos.generar_pdf:
        try:
            supabase.table("ventas").update(
                {
                    "solicito_pdf": True,
                    "comprobante_data": comprobante,
                }
            ).eq("id", id_venta).execute()
        except Exception:
            pass

    return {
        "mensaje": "Venta registrada",
        "id_venta": id_venta,
        "venta": venta_row,
        "detalles": detalles_enriquecidos,
        "generar_pdf": datos.generar_pdf,
        "comprobante": comprobante,
    }

