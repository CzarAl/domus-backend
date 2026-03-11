from datetime import datetime
import base64
import io
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
import jwt

from database import supabase
from dependencies import get_current_user

router = APIRouter(prefix="/drive-sync", tags=["Drive Sync"])

GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_VISION_SCOPE = "https://www.googleapis.com/auth/cloud-vision"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
GOOGLE_VISION_FILES_ANNOTATE_URL = "https://vision.googleapis.com/v1/files:annotate"
GOOGLE_DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveSyncRequest(BaseModel):
    folder_id: str | None = None
    nombre_fuente: str | None = None
    proveedor: str | None = None


class DriveReviewResolveRequest(BaseModel):
    accion: str = Field(min_length=3, max_length=20)
    nombre: str | None = None
    codigo_producto: str | None = None
    categoria: str | None = None
    descripcion: str | None = None
    precio_publico: float | None = Field(default=None, ge=0)
    visible_publico: bool | None = None
    destacado: bool | None = None


class CostoProveedorIn(BaseModel):
    codigo_producto: str = Field(min_length=2, max_length=80)
    costo_adquisicion: float = Field(ge=0)
    proveedor: str | None = None
    notas: str | None = None


def _utcnow() -> str:
    return datetime.utcnow().isoformat()


def _id_empresa(usuario: dict) -> str:
    id_empresa = (os.getenv("STOREFRONT_EMPRESA_ID") or usuario.get("id_raiz") or "").strip()
    if not id_empresa:
        raise HTTPException(status_code=400, detail="Usuario sin empresa")
    return id_empresa


def _slug_text(texto: str) -> str:
    value = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return value[:160] or f"producto-{uuid.uuid4().hex[:8]}"


def _drive_private_key() -> str:
    return (os.getenv("GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY") or "").replace("\\n", "\n").strip()


def _google_service_config() -> dict:
    client_email = (os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL") or "").strip()
    private_key = _drive_private_key()
    if not client_email or not private_key:
        raise HTTPException(status_code=400, detail="Faltan GOOGLE_SERVICE_ACCOUNT_EMAIL o GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY")
    return {"client_email": client_email, "private_key": private_key}


def _drive_config(folder_id_override: str | None = None) -> dict:
    folder_id = (folder_id_override or os.getenv("GOOGLE_DRIVE_FOLDER_ID") or "").strip()
    if not folder_id:
        raise HTTPException(status_code=400, detail="Falta GOOGLE_DRIVE_FOLDER_ID o captura el ID de la carpeta")
    return {**_google_service_config(), "folder_id": folder_id}


def _google_access_token(config: dict, scopes: list[str], service_name: str) -> str:
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": config["client_email"],
            "scope": " ".join(scopes),
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
        raise HTTPException(status_code=400, detail=f"No se pudo autenticar con {service_name}: {detail or exc.reason}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"No se pudo autenticar con {service_name}: {exc}")
    access_token = payload.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail=f"{service_name} no devolvio access_token")
    return access_token


def _drive_access_token(config: dict) -> str:
    return _google_access_token(config, [GOOGLE_DRIVE_SCOPE], "Google Drive")


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


def _drive_download_file(file_id: str, access_token: str) -> bytes:
    request = urllib.request.Request(
        f"{GOOGLE_DRIVE_FILES_URL}/{file_id}?alt=media&supportsAllDrives=true",
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _extract_price_from_text(text: str) -> float | None:
    if not text:
        return None
    patterns = [
        r"(?:precio|venta|publico|p/pza|pza\.)[^0-9$]{0,8}\$?\s*([0-9][0-9,.]{0,12})",
        r"\$\s*([0-9][0-9,.]{0,12})",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        for raw in matches:
            try:
                value = float(raw.replace(",", ""))
            except Exception:
                continue
            if 1 <= value <= 1000000:
                return round(value, 2)
    return None


def _extract_pieces_from_text(text: str) -> int | None:
    match = re.search(r"(\d+)\s+PIEZAS", text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _extract_code_from_text(text: str, filename: str) -> str | None:
    match = re.search(r"\b[A-Z]{1,4}-\d{2,4}-[A-Z0-9]{2,10}\b", text or "")
    if match:
        return match.group(0).upper()
    match = re.search(r"\b[A-Z]{1,4}-\d{2,4}-[A-Z0-9]{2,10}\b", filename or "")
    if match:
        return match.group(0).upper()
    return None


def _extract_name_from_text(text: str, filename: str) -> str:
    if text:
        for raw in text.splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if len(line) < 4:
                continue
            if "TEPEYAC" in line.upper():
                continue
            if re.fullmatch(r"[A-Z0-9-]{4,}", line):
                continue
            return line.title()[:140]
    base = os.path.splitext(filename or "")[0]
    return re.sub(r"[_-]+", " ", base).strip()[:140] or "Producto sin nombre"


def _extract_pdf_text(file_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"pypdf no esta disponible en el backend: {exc}")

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception:
        return ""

    chunks = []
    for page in reader.pages[:12]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(chunks)


def _pdf_page_count(file_bytes: bytes) -> int:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        return len(reader.pages)
    except Exception:
        return 0


def _vision_parent() -> str | None:
    project_id = (os.getenv("GOOGLE_VISION_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT_ID") or "").strip()
    location = (os.getenv("GOOGLE_VISION_LOCATION") or "us").strip()
    if not project_id:
        return None
    return f"projects/{project_id}/locations/{location}"


def _vision_endpoint(parent: str | None) -> str:
    if not parent:
        return GOOGLE_VISION_FILES_ANNOTATE_URL
    location = (os.getenv("GOOGLE_VISION_LOCATION") or "us").strip()
    return f"https://{location}-vision.googleapis.com/v1/{parent}/files:annotate"


def _vision_enabled() -> bool:
    value = (os.getenv("GOOGLE_VISION_OCR_ENABLED") or "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _vision_ocr_pdf(file_bytes: bytes, filename: str, config: dict) -> str:
    if not _vision_enabled():
        return ""

    total_pages = _pdf_page_count(file_bytes)
    if total_pages <= 0:
        return ""

    access_token = _google_access_token(config, [GOOGLE_VISION_SCOPE], "Google Vision")
    full_chunks = []
    encoded_content = base64.b64encode(file_bytes).decode("utf-8")
    parent = _vision_parent()

    for start in range(1, total_pages + 1, 5):
        pages = list(range(start, min(start + 5, total_pages + 1)))
        payload = {
            "requests": [
                {
                    "inputConfig": {
                        "mimeType": "application/pdf",
                        "content": encoded_content,
                    },
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                    "pages": pages,
                }
            ]
        }
        endpoint_url = _vision_endpoint(parent)

        request = urllib.request.Request(
            endpoint_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise HTTPException(status_code=400, detail=f"No se pudo ejecutar OCR con Google Vision para {filename}: {detail or exc.reason}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"No se pudo ejecutar OCR con Google Vision para {filename}: {exc}")

        file_responses = response_payload.get("responses") or []
        annotate_file = file_responses[0] if file_responses else {}
        image_responses = annotate_file.get("responses") or []
        for image_response in image_responses:
            if image_response.get("error", {}).get("message"):
                continue
            text_value = ((image_response.get("fullTextAnnotation") or {}).get("text") or "").strip()
            if text_value:
                full_chunks.append(text_value)

    return "\n".join(full_chunks)


def _extract_pdf_info_from_text(text: str, filename: str) -> dict:
    clean = re.sub(r"\s+", " ", text).strip()
    return {
        "codigo_producto": _extract_code_from_text(clean, filename),
        "nombre": _extract_name_from_text(clean, filename),
        "precio_publico": _extract_price_from_text(clean),
        "piezas_por_caja": _extract_pieces_from_text(clean),
        "descripcion": clean[:1200] or None,
    }


def _extract_pdf_info(file_bytes: bytes, filename: str) -> dict:
    return _extract_pdf_info_from_text(_extract_pdf_text(file_bytes), filename)


def _scan_drive(folder_id: str, access_token: str) -> tuple[list[dict], list[dict], list[dict]]:
    root_entries = _drive_list_children(folder_id, access_token)
    categorias = []
    archivos_raiz = []
    archivos = []
    for item in root_entries:
        if item.get("mimeType") == GOOGLE_DRIVE_FOLDER_MIME:
            categorias.append(item)
            sub_entries = _drive_list_children(item["id"], access_token)
            for child in sub_entries:
                if child.get("mimeType") == GOOGLE_DRIVE_FOLDER_MIME:
                    continue
                archivos.append({**child, "categoria": item.get("name") or "Sin categoria"})
        else:
            archivos_raiz.append(item)
            archivos.append({**item, "categoria": "Sin categoria"})
    return categorias, archivos_raiz, archivos


def _signature(item: dict) -> str:
    return "|".join([
        item.get("id") or "",
        item.get("name") or "",
        item.get("mimeType") or "",
        str(item.get("size") or ""),
        item.get("modifiedTime") or "",
    ])


def _ensure_fuente(id_empresa: str, folder_id: str, nombre_fuente: str | None, proveedor: str | None):
    resp = supabase.table("catalogo_drive_fuentes").select("*").eq("id_empresa", id_empresa).eq("folder_id", folder_id).limit(1).execute()
    if resp.data:
        fuente = resp.data[0]
        supabase.table("catalogo_drive_fuentes").update({
            "nombre": (nombre_fuente or fuente.get("nombre") or "Catalogo Drive").strip(),
            "proveedor": (proveedor or fuente.get("proveedor") or "Proveedor Domus").strip(),
            "fecha_actualizacion": _utcnow(),
        }).eq("id", fuente["id"]).execute()
        return fuente
    payload = {
        "id": str(uuid.uuid4()),
        "id_empresa": id_empresa,
        "nombre": (nombre_fuente or "Catalogo Drive proveedor").strip(),
        "folder_id": folder_id,
        "proveedor": (proveedor or "Proveedor Domus").strip(),
        "activa": True,
        "fecha_creacion": _utcnow(),
        "fecha_actualizacion": _utcnow(),
    }
    created = supabase.table("catalogo_drive_fuentes").insert(payload).execute()
    return created.data[0]

def _pending_revision_for_item(drive_item_id: str):
    return (
        supabase.table("catalogo_drive_revisiones")
        .delete()
        .eq("drive_item_id", drive_item_id)
        .eq("estado_revision", "pendiente")
        .execute()
    )


def _upsert_revision(id_empresa: str, fuente_id: str, item_record: dict, tipo_cambio: str, titulo: str, detalle: str, anteriores: dict, propuestos: dict):
    _pending_revision_for_item(item_record["id"])
    payload = {
        "id": str(uuid.uuid4()),
        "id_empresa": id_empresa,
        "id_fuente": fuente_id,
        "drive_item_id": item_record["id"],
        "producto_id": item_record.get("producto_id"),
        "tipo_cambio": tipo_cambio,
        "titulo": titulo,
        "detalle": detalle,
        "datos_anteriores": anteriores or {},
        "datos_propuestos": propuestos or {},
        "estado_revision": "pendiente",
        "fecha_detectada": _utcnow(),
    }
    supabase.table("catalogo_drive_revisiones").insert(payload).execute()


@router.get("/preview")
def preview_drive(folder_id: str | None = Query(default=None), _usuario=Depends(get_current_user)):
    config = _drive_config(folder_id)
    access_token = _drive_access_token(config)
    categorias, archivos_raiz, archivos = _scan_drive(config["folder_id"], access_token)
    return {
        "folder_id": config["folder_id"],
        "service_account_email": config["client_email"],
        "total_elementos": len(archivos) + len(categorias),
        "categorias": [{"id": item["id"], "nombre": item["name"]} for item in categorias],
        "archivos_raiz": archivos_raiz,
        "mensaje": "Comparte la carpeta con este correo de servicio para que el backend pueda leerla.",
    }


@router.post("/sync")
def sync_drive(datos: DriveSyncRequest, usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)
    config = _drive_config(datos.folder_id)
    access_token = _drive_access_token(config)
    fuente = _ensure_fuente(id_empresa, config["folder_id"], datos.nombre_fuente, datos.proveedor)
    google_config = _google_service_config()
    _, _, archivos = _scan_drive(config["folder_id"], access_token)

    existing_resp = supabase.table("catalogo_drive_items").select("*").eq("id_empresa", id_empresa).eq("id_fuente", fuente["id"]).execute()
    existing_map = {item["drive_file_id"]: item for item in (existing_resp.data or [])}
    seen = set()
    resumen = {"nuevos": 0, "actualizados": 0, "precios_modificados": 0, "removidos": 0, "sin_cambios": 0}

    for item in archivos:
        seen.add(item["id"])
        existing = existing_map.get(item["id"])
        info = {}
        if item.get("mimeType") == "application/pdf":
            try:
                pdf_bytes = _drive_download_file(item["id"], access_token)
                info = _extract_pdf_info(pdf_bytes, item.get("name") or "")
                if not info.get("codigo_producto") or info.get("precio_publico") in (None, ""):
                    try:
                        ocr_text = _vision_ocr_pdf(pdf_bytes, item.get("name") or "catalogo.pdf", google_config)
                        if ocr_text.strip():
                            info = _extract_pdf_info_from_text(ocr_text, item.get("name") or "")
                    except Exception:
                        pass
            except Exception:
                info = _extract_pdf_info(b"", item.get("name") or "")
        else:
            info = {
                "codigo_producto": _extract_code_from_text("", item.get("name") or ""),
                "nombre": _extract_name_from_text("", item.get("name") or ""),
                "precio_publico": None,
                "piezas_por_caja": None,
                "descripcion": None,
            }

        proposed = {
            "codigo_producto": info.get("codigo_producto"),
            "nombre": info.get("nombre"),
            "categoria": item.get("categoria") or "Sin categoria",
            "precio_publico": info.get("precio_publico"),
            "piezas_por_caja": info.get("piezas_por_caja"),
            "descripcion": info.get("descripcion"),
            "mime_type": item.get("mimeType"),
            "web_view_link": item.get("webViewLink"),
        }
        sign = _signature(item)

        if not existing:
            payload = {
                "id": str(uuid.uuid4()),
                "id_empresa": id_empresa,
                "id_fuente": fuente["id"],
                "drive_file_id": item["id"],
                "drive_parent_id": None,
                "drive_parent_name": item.get("categoria") or "Sin categoria",
                "categoria": item.get("categoria") or "Sin categoria",
                "nombre_archivo": item.get("name"),
                "mime_type": item.get("mimeType"),
                "web_view_link": item.get("webViewLink"),
                "modified_time": item.get("modifiedTime"),
                "size_bytes": int(item.get("size") or 0),
                "signature": sign,
                "extracted_data": proposed,
                "estado_sync": "vigente",
                "last_seen_at": _utcnow(),
                "synced_at": _utcnow(),
                "fecha_creacion": _utcnow(),
            }
            created = supabase.table("catalogo_drive_items").insert(payload).execute()
            item_record = created.data[0]
            _upsert_revision(id_empresa, fuente["id"], item_record, "nuevo", f"Nuevo producto detectado: {payload['nombre_archivo']}", "Se detecto un archivo nuevo en Drive pendiente de revision.", {}, proposed)
            resumen["nuevos"] += 1
            continue

        update_payload = {
            "categoria": item.get("categoria") or "Sin categoria",
            "nombre_archivo": item.get("name"),
            "mime_type": item.get("mimeType"),
            "web_view_link": item.get("webViewLink"),
            "modified_time": item.get("modifiedTime"),
            "size_bytes": int(item.get("size") or 0),
            "last_seen_at": _utcnow(),
            "synced_at": _utcnow(),
        }
        if existing.get("signature") == sign:
            supabase.table("catalogo_drive_items").update(update_payload).eq("id", existing["id"]).execute()
            resumen["sin_cambios"] += 1
            continue

        tipo = "actualizado"
        anterior = existing.get("extracted_data") or {}
        if anterior.get("precio_publico") not in (None, "") and proposed.get("precio_publico") not in (None, "") and float(anterior.get("precio_publico")) != float(proposed.get("precio_publico")):
            tipo = "precio_modificado"
            resumen["precios_modificados"] += 1
        else:
            resumen["actualizados"] += 1

        update_payload.update({"signature": sign, "extracted_data": proposed, "estado_sync": "vigente"})
        updated = supabase.table("catalogo_drive_items").update(update_payload).eq("id", existing["id"]).execute()
        item_record = updated.data[0] if updated.data else {**existing, **update_payload}
        _upsert_revision(id_empresa, fuente["id"], item_record, tipo, f"Cambio detectado en {item.get('name')}", "Se detecto un cambio en el catalogo del proveedor.", anterior, proposed)

    for drive_file_id, item in existing_map.items():
        if drive_file_id in seen:
            continue
        if item.get("estado_sync") == "removido":
            continue
        updated = supabase.table("catalogo_drive_items").update({"estado_sync": "removido", "synced_at": _utcnow()}).eq("id", item["id"]).execute()
        item_record = updated.data[0] if updated.data else {**item, "estado_sync": "removido"}
        _upsert_revision(id_empresa, fuente["id"], item_record, "no_encontrado_en_drive", f"Archivo removido: {item.get('nombre_archivo')}", "El proveedor ya no tiene este archivo en su carpeta.", item.get("extracted_data") or {}, {})
        resumen["removidos"] += 1

    supabase.table("catalogo_drive_fuentes").update({"ultima_sincronizacion": _utcnow(), "ultimo_resumen": resumen, "fecha_actualizacion": _utcnow()}).eq("id", fuente["id"]).execute()
    return {"mensaje": "Sincronizacion completada", "resumen": resumen, "fuente": {"id": fuente["id"], "folder_id": config["folder_id"]}}


@router.get("/revisiones")
def listar_revisiones(usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)
    revisiones_resp = supabase.table("catalogo_drive_revisiones").select("*").eq("id_empresa", id_empresa).eq("estado_revision", "pendiente").order("fecha_detectada", desc=True).execute()
    revisiones = revisiones_resp.data or []
    item_ids = [item.get("drive_item_id") for item in revisiones if item.get("drive_item_id")]
    items_map = {}
    if item_ids:
        items_resp = supabase.table("catalogo_drive_items").select("id,producto_id,drive_file_id,nombre_archivo,categoria,mime_type,web_view_link,modified_time").in_("id", item_ids).execute()
        items_map = {item["id"]: item for item in (items_resp.data or [])}

    codigos = []
    for revision in revisiones:
        before = revision.get("datos_anteriores") or {}
        after = revision.get("datos_propuestos") or {}
        codigos.extend([before.get("codigo_producto"), after.get("codigo_producto")])
    costos_map = _buscar_costos(id_empresa, codigos)

    salida = []
    for revision in revisiones:
        proposed = revision.get("datos_propuestos") or {}
        previous = revision.get("datos_anteriores") or {}
        codigo = (proposed.get("codigo_producto") or previous.get("codigo_producto") or "").strip().upper() or None
        salida.append({
            **revision,
            "drive_item": items_map.get(revision.get("drive_item_id"), {}),
            "costo_registrado": costos_map.get(codigo) if codigo else None,
        })
    return {"pendientes": salida}

def _buscar_costo(id_empresa: str, codigo_producto: str | None):
    if not codigo_producto:
        return None
    resp = supabase.table("catalogo_costos_proveedor").select("*").eq("id_empresa", id_empresa).eq("codigo_producto", codigo_producto).limit(1).execute()
    return resp.data[0] if resp.data else None


def _buscar_costos(id_empresa: str, codigos: list[str]) -> dict[str, dict]:
    clean = [codigo.strip().upper() for codigo in codigos if codigo and codigo.strip()]
    if not clean:
        return {}
    resp = supabase.table("catalogo_costos_proveedor").select("*").eq("id_empresa", id_empresa).in_("codigo_producto", list(set(clean))).execute()
    return {item["codigo_producto"]: item for item in (resp.data or []) if item.get("codigo_producto")}


def _guardar_costo(id_empresa: str, codigo_producto: str, costo_adquisicion: float, proveedor: str | None, notas: str | None = None):
    codigo = codigo_producto.strip().upper()
    existing = supabase.table("catalogo_costos_proveedor").select("id").eq("id_empresa", id_empresa).eq("codigo_producto", codigo).limit(1).execute()
    payload = {
        "codigo_producto": codigo,
        "costo_adquisicion": float(costo_adquisicion),
        "proveedor": (proveedor or "Proveedor Domus").strip(),
        "notas": (notas or "").strip() or None,
        "fecha_actualizacion": _utcnow(),
    }
    if existing.data:
        saved = supabase.table("catalogo_costos_proveedor").update(payload).eq("id", existing.data[0]["id"]).execute()
        return saved.data[0] if saved.data else {**payload, "id": existing.data[0]["id"], "id_empresa": id_empresa}
    payload.update({"id": str(uuid.uuid4()), "id_empresa": id_empresa, "fecha_creacion": _utcnow()})
    saved = supabase.table("catalogo_costos_proveedor").insert(payload).execute()
    return saved.data[0] if saved.data else payload


def _registrar_importacion_costos(id_empresa: str, nombre_archivo: str, proveedor: str, resumen: dict):
    try:
        supabase.table("catalogo_costos_importaciones").insert({
            "id": str(uuid.uuid4()),
            "id_empresa": id_empresa,
            "nombre_archivo": nombre_archivo,
            "proveedor": proveedor,
            "resumen": resumen,
            "fecha_creacion": _utcnow(),
        }).execute()
    except Exception:
        pass


def _extract_money_values(text: str) -> list[float]:
    values = []
    patterns = [
        r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d{2})?)",
        r"(?<!\d)([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d{2}))(?!\d)",
    ]
    for pattern in patterns:
        for raw in re.findall(pattern, text or "", flags=re.IGNORECASE):
            try:
                value = float(raw.replace(",", ""))
            except Exception:
                continue
            if 1 <= value <= 1000000:
                values.append(round(value, 2))
    deduped = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _normalize_code_candidate(raw: str) -> str | None:
    cleaned = (raw or "").upper()
    cleaned = re.sub(r"[^A-Z0-9]+", "-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    parts = [part for part in cleaned.split("-") if part]
    if len(parts) < 3:
        return None
    if re.fullmatch(r"[A-Z]{1,5}", parts[0]) and re.fullmatch(r"\d{2,5}", parts[1]) and re.fullmatch(r"[A-Z0-9]{2,12}", parts[2]):
        return "-".join(parts[:3])
    if 3 <= len(parts) <= 4 and re.fullmatch(r"\d{2,5}", parts[0]) and all(re.fullmatch(r"[A-Z0-9]{1,5}", part) for part in parts[1:]):
        return "-".join(parts[:4])
    return None


def _extract_code_candidates(text: str) -> list[str]:
    if not text:
        return []
    patterns = [
        r"\b[A-Z]{1,5}\s*[- ]\s*\d{2,5}\s*[- ]\s*[A-Z0-9]{2,12}\b",
        r"\b[A-Z]{1,5}-\d{2,5}[A-Z0-9]{2,12}\b",
        r"\b[A-Z]{1,5}\d{2,5}\s*[- ]\s*[A-Z0-9]{2,12}\b",
        r"\b\d{2,5}\s*[- ]\s*[A-Z0-9]{1,5}\s*[- ]\s*[A-Z0-9]{1,5}\s*[- ]\s*[A-Z0-9]{1,5}\b",
        r"\b\d{2,5}\s+[A-Z0-9]{1,5}\s+\d{2,5}\s+\d{2,5}\b",
    ]
    found = []
    seen = set()
    for pattern in patterns:
        for raw in re.findall(pattern, (text or "").upper()):
            normalized = _normalize_code_candidate(raw)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            found.append(normalized)
    return found


def _pick_cost_from_context(lines: list[str], index: int) -> float | None:
    window = " ".join(lines[max(0, index - 1): min(len(lines), index + 3)])
    tagged = re.findall(r"(?:costo|distribuidor|precio|p\.\s*distribuidor)[^0-9$]{0,8}\$?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d{2})?)", window, flags=re.IGNORECASE)
    if tagged:
        try:
            return round(float(tagged[0].replace(",", "")), 2)
        except Exception:
            pass
    values = _extract_money_values(window)
    return values[0] if values else None


def _extract_cost_rows_from_text(raw_text: str, filename: str) -> list[dict]:
    if not raw_text.strip():
        return []
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw_text.splitlines()]
    lines = [line for line in lines if line]
    results = {}
    for index, line in enumerate(lines):
        codes = _extract_code_candidates(line)
        if not codes and index + 1 < len(lines):
            codes = _extract_code_candidates(f"{line} {lines[index + 1]}")
        if not codes:
            continue
        cost = _pick_cost_from_context(lines, index)
        if cost is None:
            continue
        name = _extract_name_from_text("\n".join(lines[max(0, index - 1): min(len(lines), index + 2)]), filename)
        for code in codes:
            results[code] = {
                "codigo_producto": code.upper(),
                "costo_adquisicion": cost,
                "nombre_detectado": name,
            }
    return list(results.values())


def _extract_cost_rows_from_pdf(file_bytes: bytes, filename: str) -> list[dict]:
    raw_text = _extract_pdf_text(file_bytes)
    if not raw_text.strip():
        return []
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw_text.splitlines()]
    lines = [line for line in lines if line]
    results = {}
    for index, line in enumerate(lines):
        codes = re.findall(r"\b[A-Z]{1,5}-\d{2,5}-[A-Z0-9]{2,12}\b", line.upper())
        if not codes:
            continue
        cost = _pick_cost_from_context(lines, index)
        if cost is None:
            continue
        name = _extract_name_from_text("\n".join(lines[max(0, index - 1): min(len(lines), index + 2)]), filename)
        for code in codes:
            results[code] = {
                "codigo_producto": code.upper(),
                "costo_adquisicion": cost,
                "nombre_detectado": name,
            }
    return list(results.values())


def _buscar_producto_por_codigo(id_empresa: str, codigo_producto: str | None):
    if not codigo_producto:
        return None
    resp = supabase.table("productos").select("id").eq("id_empresa", id_empresa).eq("codigo_producto", codigo_producto).limit(1).execute()
    return resp.data[0] if resp.data else None


def _guardar_producto_desde_revision(id_empresa: str, revision: dict, drive_item: dict, proposed: dict):
    codigo_producto = (proposed.get("codigo_producto") or "").strip().upper() or None
    nombre = (proposed.get("nombre") or "").strip()
    categoria = (proposed.get("categoria") or "Sin categoria").strip()
    precio_publico = proposed.get("precio_publico")
    if precio_publico in (None, ""):
        raise HTTPException(status_code=400, detail="No se detecto precio publico. Editalo antes de publicar.")
    costo = _buscar_costo(id_empresa, codigo_producto)
    costo_adquisicion = float(costo.get("costo_adquisicion") or 0) if costo else 0
    producto_payload = {
        "nombre": nombre,
        "codigo_producto": codigo_producto,
        "categoria": categoria,
        "descripcion": proposed.get("descripcion"),
        "precio": float(precio_publico),
        "precio_publico": float(precio_publico),
        "costo_adquisicion": costo_adquisicion,
        "slug": _slug_text(f"{codigo_producto or nombre}"),
        "visible_publico": True if proposed.get("visible_publico") is None else bool(proposed.get("visible_publico")),
        "destacado": bool(proposed.get("destacado") or False),
        "origen_catalogo": "drive",
        "piezas_por_caja": proposed.get("piezas_por_caja"),
        "proveedor_catalogo": "Proveedor Domus",
        "origen_drive_file_id": drive_item.get("drive_file_id"),
        "activo": True,
    }
    producto_id = revision.get("producto_id")
    if not producto_id:
        existing_producto = _buscar_producto_por_codigo(id_empresa, codigo_producto)
        producto_id = existing_producto.get("id") if existing_producto else None
    if producto_id:
        updated = supabase.table("productos").update(producto_payload).eq("id", producto_id).eq("id_empresa", id_empresa).execute()
        if updated.data:
            return updated.data[0]
    producto_payload.update({"id": str(uuid.uuid4()), "id_empresa": id_empresa, "fecha_creacion": _utcnow()})
    created = supabase.table("productos").insert(producto_payload).execute()
    if not created.data:
        raise HTTPException(status_code=400, detail="No se pudo crear producto desde revision")
    return created.data[0]


@router.post("/revisiones/{id_revision}/resolver")
def resolver_revision(id_revision: str, datos: DriveReviewResolveRequest, usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)
    resp = supabase.table("catalogo_drive_revisiones").select("*").eq("id", id_revision).eq("id_empresa", id_empresa).limit(1).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Revision no encontrada")
    revision = resp.data[0]
    accion = datos.accion.strip().lower()
    if accion not in {"publicar", "actualizar", "ocultar", "ignorar"}:
        raise HTTPException(status_code=400, detail="Accion invalida")

    drive_item_resp = supabase.table("catalogo_drive_items").select("*").eq("id", revision["drive_item_id"]).limit(1).execute()
    drive_item = drive_item_resp.data[0] if drive_item_resp.data else {}

    if accion == "ignorar":
        supabase.table("catalogo_drive_revisiones").update({"estado_revision": "ignorado", "fecha_resuelta": _utcnow()}).eq("id", id_revision).execute()
        return {"mensaje": "Revision ignorada"}
    if accion == "ocultar":
        producto_id = revision.get("producto_id") or drive_item.get("producto_id")
        if producto_id:
            supabase.table("productos").update({"visible_publico": False, "activo": False}).eq("id", producto_id).eq("id_empresa", id_empresa).execute()
        supabase.table("catalogo_drive_revisiones").update({"estado_revision": "oculto", "fecha_resuelta": _utcnow()}).eq("id", id_revision).execute()
        return {"mensaje": "Producto ocultado"}

    proposed = dict(revision.get("datos_propuestos") or {})
    if datos.nombre is not None:
        proposed["nombre"] = datos.nombre.strip()
    if datos.codigo_producto is not None:
        proposed["codigo_producto"] = datos.codigo_producto.strip().upper()
    if datos.categoria is not None:
        proposed["categoria"] = datos.categoria.strip()
    if datos.descripcion is not None:
        proposed["descripcion"] = datos.descripcion.strip()
    if datos.precio_publico is not None:
        proposed["precio_publico"] = datos.precio_publico
    if datos.visible_publico is not None:
        proposed["visible_publico"] = datos.visible_publico
    if datos.destacado is not None:
        proposed["destacado"] = datos.destacado

    producto = _guardar_producto_desde_revision(id_empresa, revision, drive_item, proposed)
    supabase.table("catalogo_drive_items").update({"producto_id": producto["id"], "estado_sync": "vigente", "synced_at": _utcnow()}).eq("id", revision["drive_item_id"]).execute()
    supabase.table("catalogo_drive_revisiones").update({"estado_revision": "aplicado", "fecha_resuelta": _utcnow(), "producto_id": producto["id"]}).eq("id", id_revision).execute()
    return {"mensaje": "Revision aplicada", "producto": producto}


@router.get("/costos")
def listar_costos(usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)
    resp = supabase.table("catalogo_costos_proveedor").select("*").eq("id_empresa", id_empresa).order("fecha_actualizacion", desc=True).execute()
    return resp.data or []


@router.post("/costos")
def guardar_costo(datos: CostoProveedorIn, usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)
    codigo = datos.codigo_producto.strip().upper()
    row = _guardar_costo(id_empresa, codigo, datos.costo_adquisicion, datos.proveedor, datos.notas)
    supabase.table("productos").update({"costo_adquisicion": float(datos.costo_adquisicion)}).eq("id_empresa", id_empresa).eq("codigo_producto", codigo).execute()
    return {"mensaje": "Costo guardado", "data": row}


@router.post("/costos/importar-pdfs")
def importar_costos_pdf(
    files: list[UploadFile] = File(...),
    proveedor: str = Form(default="Proveedor Domus"),
    usuario=Depends(get_current_user),
):
    id_empresa = _id_empresa(usuario)
    proveedor_value = (proveedor or "Proveedor Domus").strip()
    archivos = [file for file in files if file and (file.filename or "").lower().endswith(".pdf")]
    if not archivos:
        raise HTTPException(status_code=400, detail="Adjunta al menos un PDF valido")

    resumen_global = {
        "archivos_procesados": 0,
        "costos_detectados": 0,
        "costos_guardados": 0,
        "archivos": [],
    }

    google_config = _google_service_config()

    for archivo in archivos:
        contenido = archivo.file.read()
        texto_pdf = _extract_pdf_text(contenido)
        ocr_usado = False
        ocr_error = None

        if not texto_pdf.strip():
            try:
                texto_pdf = _vision_ocr_pdf(contenido, archivo.filename or "catalogo.pdf", google_config)
                ocr_usado = bool(texto_pdf.strip())
            except HTTPException as exc:
                ocr_error = str(exc.detail)
                texto_pdf = ""

        requiere_ocr = not bool(texto_pdf.strip())
        rows = _extract_cost_rows_from_text(texto_pdf, archivo.filename or "catalogo.pdf") if texto_pdf.strip() else []
        guardados = 0
        for row in rows:
            _guardar_costo(id_empresa, row["codigo_producto"], row["costo_adquisicion"], proveedor_value, f"Importado desde PDF: {archivo.filename}")
            supabase.table("productos").update({"costo_adquisicion": float(row["costo_adquisicion"])}).eq("id_empresa", id_empresa).eq("codigo_producto", row["codigo_producto"]).execute()
            guardados += 1

        ocr_preview = None
        if texto_pdf.strip() and not rows:
            compact_preview = re.sub(r"\s+", " ", texto_pdf).strip()
            ocr_preview = compact_preview[:800] if compact_preview else None

        resumen_archivo = {
            "nombre_archivo": archivo.filename,
            "costos_detectados": len(rows),
            "costos_guardados": guardados,
            "requiere_ocr": requiere_ocr,
            "ocr_usado": ocr_usado,
            "ocr_error": ocr_error,
            "ocr_preview": ocr_preview,
            "ejemplos": rows[:5],
        }
        _registrar_importacion_costos(id_empresa, archivo.filename or "catalogo.pdf", proveedor_value, resumen_archivo)
        resumen_global["archivos_procesados"] += 1
        resumen_global["costos_detectados"] += len(rows)
        resumen_global["costos_guardados"] += guardados
        resumen_global["archivos"].append(resumen_archivo)

    return {"mensaje": "Importacion de costos completada", "resumen": resumen_global}


@router.get("/rentabilidad")
def rentabilidad(usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)
    productos = supabase.table("productos").select("id,nombre,codigo_producto,precio,costo_adquisicion").eq("id_empresa", id_empresa).execute().data or []
    ventas = supabase.table("ventas").select("id").eq("id_empresa", id_empresa).execute().data or []
    ids_venta = [item.get("id") for item in ventas if item.get("id")]
    detalles = []
    if ids_venta:
        detalles = supabase.table("detalle_ventas").select("id_producto,cantidad,precio_unitario").in_("id_venta", ids_venta).execute().data or []
    productos_map = {p["id"]: p for p in productos if p.get("id")}
    por_producto = {}
    utilidad_total = 0.0
    for det in detalles:
        id_producto = det.get("id_producto")
        if id_producto not in productos_map:
            continue
        producto = productos_map[id_producto]
        cantidad = int(det.get("cantidad") or 0)
        precio = float(det.get("precio_unitario") or producto.get("precio") or 0)
        costo = float(producto.get("costo_adquisicion") or 0)
        utilidad = (precio - costo) * cantidad
        utilidad_total += utilidad
        item = por_producto.setdefault(id_producto, {
            "id_producto": id_producto,
            "nombre": producto.get("nombre"),
            "codigo_producto": producto.get("codigo_producto"),
            "unidades_vendidas": 0,
            "venta_total": 0.0,
            "utilidad_total": 0.0,
            "precio_publico": float(producto.get("precio") or 0),
            "costo_adquisicion": costo,
        })
        item["unidades_vendidas"] += cantidad
        item["venta_total"] += precio * cantidad
        item["utilidad_total"] += utilidad
    ranking = sorted(por_producto.values(), key=lambda item: item["unidades_vendidas"], reverse=True)
    rentables = sorted(por_producto.values(), key=lambda item: item["utilidad_total"], reverse=True)
    return {
        "utilidad_total_estimada": round(utilidad_total, 2),
        "producto_mas_vendido": ranking[0] if ranking else None,
        "producto_mas_rentable": rentables[0] if rentables else None,
        "productos": ranking,
    }
