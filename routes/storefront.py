import os
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from database import supabase
from routes.productos import _normalizar_producto

router = APIRouter(prefix="/storefront", tags=["Storefront"])


class StorefrontClienteRegistro(BaseModel):
    nombre: str = Field(min_length=2, max_length=180)
    tipo_persona: str = Field(default="fisica", max_length=20)
    razon_social: str | None = Field(default=None, max_length=180)
    telefono: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=180)
    direccion: str | None = Field(default=None, max_length=300)
    rfc: str | None = Field(default=None, max_length=20)
    codigo_postal: str | None = Field(default=None, max_length=10)
    requiere_factura: bool = False
    tipo_entrega_preferida: str = Field(default="recoge", max_length=20)
    ciudad_envio: str | None = Field(default=None, max_length=120)
    costo_envio_estimado: float = 0
    requiere_logistica: bool = False


def _storefront_empresa_id() -> str:
    empresa_id = (os.getenv("STOREFRONT_EMPRESA_ID") or os.getenv("PUBLIC_STOREFRONT_EMPRESA_ID") or "").strip()
    if empresa_id:
        return empresa_id

    resp = (
        supabase.table("empresas")
        .select("id")
        .eq("estado", "activa")
        .order("fecha_creacion")
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]["id"]

    raise HTTPException(status_code=500, detail="No se encontro empresa para storefront")


def _storefront_config():
    empresa_id = _storefront_empresa_id()
    resp = (
        supabase.table("empresas")
        .select("id,nombre,logo_url,color_primario,color_secundario,usar_marca_domus")
        .eq("id", empresa_id)
        .limit(1)
        .execute()
    )
    empresa = resp.data[0] if resp.data else {}
    return {
        "empresa_id": empresa_id,
        "brand": {
            "nombre": empresa.get("nombre") or "Domus Interiorismo",
            "logo_url": empresa.get("logo_url"),
            "color_primario": empresa.get("color_primario") or "#12c6c3",
            "color_secundario": empresa.get("color_secundario") or "#1e293b",
            "usar_marca_domus": empresa.get("usar_marca_domus", True),
        },
    }


@router.get("/config")
def storefront_config():
    return _storefront_config()


@router.get("/productos")
def storefront_productos():
    empresa_id = _storefront_empresa_id()
    resp = (
        supabase.table("productos")
        .select("*")
        .eq("id_empresa", empresa_id)
        .eq("activo", True)
        .order("destacado", desc=True)
        .order("fecha_creacion", desc=True)
        .execute()
    )

    productos = [_normalizar_producto(item) for item in (resp.data or [])]
    productos_publicos = [item for item in productos if item.get("visible_publico", True)]
    categorias = sorted({item.get("categoria") or "Sin categoria" for item in productos_publicos})

    return {
        "config": _storefront_config(),
        "categorias": categorias,
        "productos": productos_publicos,
    }


@router.get("/productos/{slug_or_id}")
def storefront_producto_detalle(slug_or_id: str):
    empresa_id = _storefront_empresa_id()
    resp = (
        supabase.table("productos")
        .select("*")
        .eq("id_empresa", empresa_id)
        .eq("activo", True)
        .execute()
    )

    productos = [_normalizar_producto(item) for item in (resp.data or [])]
    for producto in productos:
        if not producto.get("visible_publico", True):
            continue
        if producto.get("id") == slug_or_id or producto.get("slug") == slug_or_id:
            return producto

    raise HTTPException(status_code=404, detail="Producto no encontrado")


@router.post("/clientes/registro")
def storefront_registrar_cliente(datos: StorefrontClienteRegistro):
    empresa_id = _storefront_empresa_id()
    payload = {
        "id": str(uuid.uuid4()),
        "id_empresa": empresa_id,
        "nombre": datos.nombre.strip(),
        "telefono": (datos.telefono or "").strip() or None,
        "email": (datos.email or "").strip() or None,
        "direccion": (datos.direccion or "").strip() or None,
        "rfc": (datos.rfc or "").strip() or None,
        "codigo_postal": (datos.codigo_postal or "").strip() or None,
        "tipo_persona": (datos.tipo_persona or "fisica").strip().lower(),
        "razon_social": (datos.razon_social or "").strip() or None,
        "requiere_factura": bool(datos.requiere_factura),
        "tipo_entrega_preferida": (datos.tipo_entrega_preferida or "recoge").strip().lower(),
        "ciudad_envio": (datos.ciudad_envio or "").strip() or None,
        "costo_envio_estimado": float(datos.costo_envio_estimado or 0),
        "requiere_logistica": bool(datos.requiere_logistica),
        "fecha_creacion": datetime.utcnow().isoformat(),
    }

    try:
        resp = supabase.table("clientes").insert(payload).execute()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"mensaje": "Cliente registrado", "data": resp.data}
