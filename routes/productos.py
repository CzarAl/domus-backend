from datetime import datetime
import re
import unicodedata
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import supabase
from dependencies import get_current_user

router = APIRouter(prefix="/productos", tags=["Productos"])


class ProductoCreate(BaseModel):
    nombre: str = Field(min_length=2, max_length=140)
    descripcion: str | None = Field(default=None, max_length=500)
    costo_adquisicion: float = Field(ge=0)
    precio: float = Field(gt=0)
    ubicacion: str | None = Field(default=None, max_length=180)
    foto_url: str | None = Field(default=None, max_length=1000)
    categoria: str | None = Field(default=None, max_length=80)
    slug: str | None = Field(default=None, max_length=160)
    visible_publico: bool = True
    destacado: bool = False
    origen_catalogo: str | None = Field(default="manual", max_length=40)
    imagenes_extra: list[str] | None = None
    id_sucursal_inicial: str | None = None
    stock_inicial: int | None = Field(default=None, ge=0)


class ProductoUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=2, max_length=140)
    descripcion: str | None = Field(default=None, max_length=500)
    costo_adquisicion: float | None = Field(default=None, ge=0)
    precio: float | None = Field(default=None, gt=0)
    ubicacion: str | None = Field(default=None, max_length=180)
    foto_url: str | None = Field(default=None, max_length=1000)
    categoria: str | None = Field(default=None, max_length=80)
    slug: str | None = Field(default=None, max_length=160)
    visible_publico: bool | None = None
    destacado: bool | None = None
    origen_catalogo: str | None = Field(default=None, max_length=40)
    imagenes_extra: list[str] | None = None
    activo: bool | None = None
    id_sucursal_inicial: str | None = None
    stock_inicial: int | None = Field(default=None, ge=0)


def _id_empresa(usuario: dict) -> str:
    id_empresa = usuario.get("id_raiz")
    if not id_empresa:
        raise HTTPException(status_code=400, detail="Usuario sin empresa")
    return id_empresa


def _slug_text(texto: str) -> str:
    value = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return value[:160] or f"producto-{uuid.uuid4().hex[:8]}"


def _normalize_gallery(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _normalizar_producto(p: dict) -> dict:
    slug = p.get("slug") or _slug_text(p.get("nombre") or "producto")
    return {
        "id": p.get("id"),
        "id_empresa": p.get("id_empresa"),
        "nombre": p.get("nombre") or "",
        "descripcion": p.get("descripcion") or "",
        "costo_adquisicion": p.get("costo_adquisicion") if p.get("costo_adquisicion") is not None else p.get("costo") or 0,
        "precio": p.get("precio") if p.get("precio") is not None else p.get("precio_venta") or 0,
        "ubicacion": p.get("ubicacion") if p.get("ubicacion") is not None else p.get("ubicacion_producto"),
        "foto_url": p.get("foto_url") if p.get("foto_url") is not None else p.get("imagen_url"),
        "categoria": p.get("categoria") or "Sin categoria",
        "slug": slug,
        "visible_publico": p.get("visible_publico", True),
        "destacado": p.get("destacado", False),
        "origen_catalogo": p.get("origen_catalogo") or "manual",
        "imagenes_extra": _normalize_gallery(p.get("imagenes_extra")),
        "activo": p.get("activo", True),
        "fecha_creacion": p.get("fecha_creacion"),
    }


def _try_insert_producto(payloads: list[dict]):
    last_error = None
    for payload in payloads:
        try:
            resp = supabase.table("productos").insert(payload).execute()
            if resp.data:
                return resp.data[0]
        except Exception as e:
            last_error = str(e)
    raise HTTPException(status_code=400, detail=last_error or "No se pudo crear producto")


def _try_update_producto(id_producto: str, id_empresa: str, payloads: list[dict]):
    last_error = None
    for payload in payloads:
        if not payload:
            continue
        try:
            resp = (
                supabase.table("productos")
                .update(payload)
                .eq("id", id_producto)
                .eq("id_empresa", id_empresa)
                .execute()
            )
            if resp.data:
                return resp.data[0]
        except Exception as e:
            last_error = str(e)
    raise HTTPException(status_code=400, detail=last_error or "No se pudo actualizar producto")


@router.get("/")
def listar_productos(usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)

    resp = (
        supabase.table("productos")
        .select("*")
        .eq("id_empresa", id_empresa)
        .order("fecha_creacion", desc=True)
        .execute()
    )

    return [_normalizar_producto(p) for p in (resp.data or [])]


@router.get("/{id_producto}")
def obtener_producto(id_producto: str, usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)

    resp = (
        supabase.table("productos")
        .select("*")
        .eq("id", id_producto)
        .eq("id_empresa", id_empresa)
        .limit(1)
        .execute()
    )

    if not resp.data:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    return _normalizar_producto(resp.data[0])


@router.post("/")
def crear_producto(datos: ProductoCreate, usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)
    now = datetime.utcnow().isoformat()
    nombre = datos.nombre.strip()
    descripcion = (datos.descripcion or "").strip() or None
    ubicacion = (datos.ubicacion or "").strip() or None
    foto_url = (datos.foto_url or "").strip() or None
    categoria = (datos.categoria or "").strip() or None
    slug = _slug_text(datos.slug.strip()) if datos.slug else _slug_text(nombre)
    imagenes_extra = _normalize_gallery(datos.imagenes_extra)
    origen_catalogo = (datos.origen_catalogo or "manual").strip() or "manual"

    payload_full = {
        "id": str(uuid.uuid4()),
        "id_empresa": id_empresa,
        "nombre": nombre,
        "descripcion": descripcion,
        "costo_adquisicion": datos.costo_adquisicion,
        "precio": datos.precio,
        "ubicacion": ubicacion,
        "foto_url": foto_url,
        "categoria": categoria,
        "slug": slug,
        "visible_publico": datos.visible_publico,
        "destacado": datos.destacado,
        "origen_catalogo": origen_catalogo,
        "imagenes_extra": imagenes_extra,
        "activo": True,
        "fecha_creacion": now,
    }

    payload_alt = {
        "id": payload_full["id"],
        "id_empresa": id_empresa,
        "nombre": nombre,
        "descripcion": descripcion,
        "costo": datos.costo_adquisicion,
        "precio_venta": datos.precio,
        "ubicacion_producto": ubicacion,
        "imagen_url": foto_url,
        "activo": True,
        "fecha_creacion": now,
    }

    payload_min = {
        "id": payload_full["id"],
        "id_empresa": id_empresa,
        "nombre": nombre,
        "precio": datos.precio,
        "fecha_creacion": now,
    }

    creado = _try_insert_producto([payload_full, payload_alt, payload_min])

    if datos.id_sucursal_inicial and datos.stock_inicial is not None:
        try:
            supabase.table("inventario").insert({
                "id": str(uuid.uuid4()),
                "id_empresa": id_empresa,
                "id_sucursal": datos.id_sucursal_inicial,
                "id_producto": payload_full["id"],
                "stock": int(datos.stock_inicial),
                "stock_minimo": 0,
                "fecha_actualizacion": now,
                "stock_reservado": 0,
            }).execute()
        except Exception:
            pass

    return {"mensaje": "Producto creado", "data": _normalizar_producto(creado)}


@router.put("/{id_producto}")
def actualizar_producto(id_producto: str, datos: ProductoUpdate, usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)

    actual = (
        supabase.table("productos")
        .select("id,nombre")
        .eq("id", id_producto)
        .eq("id_empresa", id_empresa)
        .limit(1)
        .execute()
    )
    if not actual.data:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    base = {}
    nombre_actual = actual.data[0].get("nombre") or "producto"
    if datos.nombre is not None:
        base["nombre"] = datos.nombre.strip()
        nombre_actual = base["nombre"]
    if datos.descripcion is not None:
        base["descripcion"] = datos.descripcion.strip() or None
    if datos.costo_adquisicion is not None:
        base["costo_adquisicion"] = datos.costo_adquisicion
    if datos.precio is not None:
        base["precio"] = datos.precio
    if datos.ubicacion is not None:
        base["ubicacion"] = datos.ubicacion.strip() or None
    if datos.foto_url is not None:
        base["foto_url"] = datos.foto_url.strip() or None
    if datos.categoria is not None:
        base["categoria"] = datos.categoria.strip() or None
    if datos.slug is not None:
        base["slug"] = _slug_text(datos.slug.strip()) if datos.slug.strip() else _slug_text(nombre_actual)
    if datos.visible_publico is not None:
        base["visible_publico"] = datos.visible_publico
    if datos.destacado is not None:
        base["destacado"] = datos.destacado
    if datos.origen_catalogo is not None:
        base["origen_catalogo"] = datos.origen_catalogo.strip() or "manual"
    if datos.imagenes_extra is not None:
        base["imagenes_extra"] = _normalize_gallery(datos.imagenes_extra)
    if datos.activo is not None:
        base["activo"] = datos.activo

    inv_extra = None
    if datos.id_sucursal_inicial is not None and datos.stock_inicial is not None:
        inv_extra = {"id_sucursal": datos.id_sucursal_inicial, "stock": int(datos.stock_inicial)}

    if not base and not inv_extra:
        raise HTTPException(status_code=400, detail="Sin cambios")

    alt = {}
    if "costo_adquisicion" in base:
        alt["costo"] = base["costo_adquisicion"]
    if "precio" in base:
        alt["precio_venta"] = base["precio"]
    if "ubicacion" in base:
        alt["ubicacion_producto"] = base["ubicacion"]
    if "foto_url" in base:
        alt["imagen_url"] = base["foto_url"]
    if "nombre" in base:
        alt["nombre"] = base["nombre"]
    if "descripcion" in base:
        alt["descripcion"] = base["descripcion"]
    if "activo" in base:
        alt["activo"] = base["activo"]

    actualizado = _try_update_producto(id_producto, id_empresa, [base, alt])

    if inv_extra:
        try:
            supabase.table("inventario").upsert({
                "id": str(uuid.uuid4()),
                "id_empresa": id_empresa,
                "id_sucursal": inv_extra["id_sucursal"],
                "id_producto": id_producto,
                "stock": int(inv_extra["stock"]),
                "stock_minimo": 0,
                "fecha_actualizacion": datetime.utcnow().isoformat(),
                "stock_reservado": 0,
            }, on_conflict="id_producto,id_sucursal").execute()
        except Exception:
            pass

    return {"mensaje": "Producto actualizado", "data": _normalizar_producto(actualizado)}


@router.delete("/{id_producto}")
def eliminar_producto(id_producto: str, usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)

    resp = (
        supabase.table("productos")
        .delete()
        .eq("id", id_producto)
        .eq("id_empresa", id_empresa)
        .execute()
    )

    if not resp.data:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    return {"mensaje": "Producto eliminado"}
