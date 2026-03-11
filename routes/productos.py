from datetime import datetime
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
import jwt

from database import supabase
from dependencies import get_current_user

router = APIRouter(prefix="/productos", tags=["Productos"])

GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
GOOGLE_DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"


class ProductoCreate(BaseModel):
    nombre: str = Field(min_length=2, max_length=140)
    descripcion: str | None = Field(default=None, max_length=500)
    costo_adquisicion: float = Field(ge=0)
    precio: float = Field(gt=0)
    ubicacion: str | None = Field(default=None, max_length=180)
    foto_url: str | None = Field(default=None, max_length=1000)
    categoria: str | None = Field(default=None, max_length=80)
    codigo_producto: str | None = Field(default=None, max_length=80)
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
    codigo_producto: str | None = Field(default=None, max_length=80)
    slug: str | None = Field(default=None, max_length=160)
    visible_publico: bool | None = None
    destacado: bool | None = None
    origen_catalogo: str | None = Field(default=None, max_length=40)
    imagenes_extra: list[str] | None = None
    activo: bool | None = None
    id_sucursal_inicial: str | None = None
    stock_inicial: int | None = Field(default=None, ge=0)


def _storefront_empresa_id() -> str:
    return (os.getenv("STOREFRONT_EMPRESA_ID") or os.getenv("PUBLIC_STOREFRONT_EMPRESA_ID") or "").strip()


def _id_empresa(usuario: dict) -> str:
    storefront_empresa = _storefront_empresa_id()
    if storefront_empresa:
        return storefront_empresa

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
        "codigo_producto": p.get("codigo_producto"),
        "precio_publico": p.get("precio_publico") if p.get("precio_publico") is not None else p.get("precio") if p.get("precio") is not None else p.get("precio_venta") or 0,
        "piezas_por_caja": p.get("piezas_por_caja"),
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


def _drive_private_key() -> str:
    return (os.getenv("GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY") or "").replace("\\n", "\n").strip()


def _drive_config(folder_id_override: str | None = None) -> dict:
    folder_id = (folder_id_override or os.getenv("GOOGLE_DRIVE_FOLDER_ID") or "").strip()
    client_email = (os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL") or "").strip()
    private_key = _drive_private_key()

    if not client_email or not private_key:
        raise HTTPException(
            status_code=400,
            detail="Faltan GOOGLE_SERVICE_ACCOUNT_EMAIL o GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY en el backend",
        )

    if not folder_id:
        raise HTTPException(
            status_code=400,
            detail="Falta GOOGLE_DRIVE_FOLDER_ID o captura manualmente el ID de la carpeta de Drive",
        )

    return {
        "folder_id": folder_id,
        "client_email": client_email,
        "private_key": private_key,
    }


def _drive_access_token(config: dict) -> str:
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": config["client_email"],
            "scope": GOOGLE_DRIVE_SCOPE,
            "aud": GOOGLE_OAUTH_TOKEN_URL,
            "exp": now + 3600,
            "iat": now,
        },
        config["private_key"],
        algorithm="RS256",
    )

    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }).encode("utf-8")

    request = urllib.request.Request(
        GOOGLE_OAUTH_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=400, detail=f"No se pudo autenticar con Google Drive: {detail or exc.reason}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"No se pudo autenticar con Google Drive: {exc}")

    access_token = payload.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Google no devolvio access_token para Drive")
    return access_token


def _drive_list_children(folder_id: str, access_token: str) -> list[dict]:
    params = urllib.parse.urlencode({
        "q": f"'{folder_id}' in parents and trashed = false",
        "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink)",
        "orderBy": "folder,name_natural",
        "pageSize": "200",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    })

    request = urllib.request.Request(
        f"{GOOGLE_DRIVE_FILES_URL}?{params}",
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=400, detail=f"No se pudo listar la carpeta de Drive: {detail or exc.reason}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"No se pudo listar la carpeta de Drive: {exc}")

    return payload.get("files") or []


def _format_drive_file(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "name": item.get("name") or "Sin nombre",
        "mimeType": item.get("mimeType") or "application/octet-stream",
        "modifiedTime": item.get("modifiedTime"),
        "size": item.get("size"),
        "webViewLink": item.get("webViewLink"),
    }


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


@router.get("/drive/preview")
def vista_previa_drive(folder_id: str | None = Query(default=None), _usuario=Depends(get_current_user)):
    config = _drive_config(folder_id)
    access_token = _drive_access_token(config)
    archivos = [_format_drive_file(item) for item in _drive_list_children(config["folder_id"], access_token)]

    categorias = [
        {"id": item["id"], "nombre": item["name"]}
        for item in archivos
        if item["mimeType"] == GOOGLE_DRIVE_FOLDER_MIME
    ]
    archivos_raiz = [item for item in archivos if item["mimeType"] != GOOGLE_DRIVE_FOLDER_MIME]

    return {
        "folder_id": config["folder_id"],
        "service_account_email": config["client_email"],
        "total_elementos": len(archivos),
        "categorias": categorias,
        "archivos_raiz": archivos_raiz,
        "mensaje": "Comparte la carpeta de Drive con este correo de servicio para que el backend pueda leerla.",
    }


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
    codigo_producto = (datos.codigo_producto or "").strip().upper() or None
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
        "codigo_producto": codigo_producto,
        "precio_publico": datos.precio,
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
    if datos.codigo_producto is not None:
        base["codigo_producto"] = datos.codigo_producto.strip().upper() or None
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

    existing = (
        supabase.table("productos")
        .select("id")
        .eq("id", id_producto)
        .eq("id_empresa", id_empresa)
        .limit(1)
        .execute()
    )

    if not existing.data:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    supabase.table("productos").delete().eq("id", id_producto).eq("id_empresa", id_empresa).execute()
    return {"mensaje": "Producto eliminado", "id": id_producto}
