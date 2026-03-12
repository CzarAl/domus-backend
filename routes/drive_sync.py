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


def _dedupe_keep_order(values: list[str]) -> list[str]:
    ordered = []
    seen = set()
    for value in values:
        clean = (value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        ordered.append(clean)
    return ordered


def _clean_compact_code(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _extract_price_from_text(text: str) -> float | None:
    if not text:
        return None
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    candidates = []

    for index, line in enumerate(lines[:80]):
        for raw in re.findall(r"\$\s*([0-9][0-9,.]{0,12})", line, flags=re.IGNORECASE):
            try:
                value = float(raw.replace(",", ""))
            except Exception:
                continue
            if not (1 <= value <= 1000000):
                continue
            score = 5
            upper_line = line.upper()
            if "PUBLIC" in upper_line:
                score += 8
            if "PRECIO" in upper_line or "VENTA" in upper_line:
                score += 5
            if "P/PZA" in upper_line or "PZA." in upper_line:
                score += 3
            if "DISTRIBUIDOR" in upper_line or "COSTO" in upper_line:
                score -= 7
            if index < 20:
                score += 2
            candidates.append((score, value))

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return round(candidates[0][1], 2)

    for raw in re.findall(r"\$\s*([0-9][0-9,.]{0,12})", text, flags=re.IGNORECASE):
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
    ranked: list[tuple[int, str]] = []
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line]

    labeled_patterns = [
        r"(?:CODIGO|CLAVE|MODELO|SKU|REF(?:ERENCIA)?)[:#\s-]{0,6}([A-Z0-9][A-Z0-9\-\s]{2,40})",
    ]
    for index, line in enumerate(lines[:80]):
        for pattern in labeled_patterns:
            for raw in re.findall(pattern, line.upper()):
                candidate = _normalize_code_candidate(raw)
                if candidate:
                    ranked.append((12 + _code_score(candidate) - min(index, 8), candidate))

        for candidate in _extract_code_candidates(line):
            bonus = 0
            if re.search(r"CODIGO|CLAVE|MODELO|SKU|REF", line, flags=re.IGNORECASE):
                bonus += 5
            if index < 12:
                bonus += 2
            ranked.append((_code_score(candidate) + bonus, candidate))

    for candidate in _extract_code_candidates((filename or "").upper()):
        ranked.append((_code_score(candidate) + 1, candidate))

    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], len(item[1]), item[1]), reverse=True)
    return ranked[0][1]


def _normalized_name_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", normalized).strip().upper()


def _filename_name_tokens(filename: str) -> set[str]:
    base = os.path.splitext(filename or "")[0]
    normalized = re.sub(r"[^A-Z0-9]+", " ", _normalized_name_text(base)).strip()
    return {token for token in normalized.split() if len(token) >= 4}


GENERIC_NAME_PATTERNS = [
    r"^\d+\s+SECCIONES?$",
    r"^SECCIONES?$",
    r"^PROXIMAMENTE$",
    r"^NOVEDAD(?:ES)?$",
    r"^DISPONIBLE(?:S)?$",
    r"^AGOTADO(?:S)?$",
    r"^CAT(?:ALOGO)?$",
    r"^CAT\.?.*$",
]


def _catalog_line_key(line: str) -> str:
    upper = _normalized_name_text(line)
    return re.sub(r"[^A-Z0-9]+", " ", upper).strip()


def _is_spec_like_name(line: str) -> bool:
    upper = _normalized_name_text(line)
    if not upper:
        return True
    if re.search(r"\b(FRECUENCIA|POTENCIA|VOLTAJE|VOLTAGE|AMPERAJE|AMPERAJE|LUMEN(?:ES)?|TEMPERATURA|MEDIDA|MEDIDAS|DIMENSION(?:ES)?|CONTROL REMOTO|BLUETOOTH|WIFI|CRI|COLOR|MATERIAL)\b", upper):
        return True
    if ":" in (line or "") and re.search(r"\b\d+(?:\.\d+)?\s*(W|V|HZ|H|K|LM|G)\b", upper):
        return True
    if re.fullmatch(r"[A-Z]{1,4}\s+\d+(?:\.\d+)?[A-Z]+", upper):
        return True
    return False


def _is_category_like_name(line: str) -> bool:
    clean = re.sub(r"\s+", " ", (line or "")).strip()
    upper = _normalized_name_text(clean)
    if not upper:
        return True
    if any(symbol in clean for symbol in ["&", ","]) and len(clean.split()) >= 3 and not re.search(r"\d", clean):
        return True
    return False


def _is_generic_catalog_name(line: str, filename: str) -> bool:
    clean = re.sub(r"\s+", " ", (line or "")).strip()
    upper = _normalized_name_text(clean)
    if not upper:
        return True
    if clean.startswith(("-", "*", "•")) and len(clean.split()) <= 4:
        return True
    if clean.endswith(":") and len(clean.split()) <= 4:
        return True
    if _is_spec_like_name(clean) or _is_category_like_name(clean):
        return True
    if any(token in upper for token in ["TEPEYAC", "CATALOGO", "COMPRIMIDO", "PDF"]):
        return True
    if re.search(r"CODIGO|CLAVE|MODELO|SKU|PIEZAS|MEDIDAS|COLOR|PRECIO|PUBLICO|VENTA", upper):
        return True
    if re.fullmatch(r"[A-Z0-9-]{4,}", upper):
        return True
    if any(re.fullmatch(pattern, upper) for pattern in GENERIC_NAME_PATTERNS):
        return True
    if re.search(r"\b\d+\s+SECCIONES?\b", upper):
        return True
    filename_tokens = _filename_name_tokens(filename)
    line_tokens = {token for token in re.sub(r"[^A-Z0-9]+", " ", upper).split() if len(token) >= 4}
    if filename_tokens and line_tokens and len(line_tokens & filename_tokens) >= max(2, len(line_tokens) - 1):
        return True
    return False


def _score_name_candidate(line: str, filename: str, repeated_count: int = 1) -> int:
    clean = re.sub(r"\s+", " ", (line or "").strip())
    upper = _normalized_name_text(clean)
    if len(clean) < 4:
        return -100
    if clean.startswith(("-", "*", "•")):
        return -90
    if clean.endswith(":") and len(clean.split()) <= 5:
        return -90
    if "$" in clean:
        return -100
    if re.search(r"\b\d{2,}[.,]?\d*\b", clean):
        return -40
    if repeated_count >= 4 and len(clean.split()) <= 4:
        return -85
    if _is_generic_catalog_name(clean, filename):
        return -80

    score = 0
    words = [word for word in clean.split() if word]
    if len(words) >= 2:
        score += 5
    if 8 <= len(clean) <= 60:
        score += 4
    if re.search(r"[A-Z]", upper) and not re.search(r"\d", clean):
        score += 3
    if re.search(r"INTERIOR|LAMBRIN|MURO|PANEL|DECK|REVESTIMIENTO|PARED|FACHADA", upper):
        score += 4
    score += min(len(words), 5)
    if repeated_count >= 3:
        score -= min(8, repeated_count * 2)
    return score


def _extract_name_from_text(text: str, filename: str, *, allow_filename_fallback: bool = True) -> str:

    best_line = None
    best_score = -100
    if text:
        for raw in text.splitlines()[:50]:
            line = re.sub(r"\s+", " ", raw).strip()
            score = _score_name_candidate(line, filename)
            if score > best_score:
                best_score = score
                best_line = line
    if best_line and best_score >= 0:
        return best_line.title()[:140]
    if not allow_filename_fallback:
        return "Producto sin nombre"
    base = os.path.splitext(filename or "")[0]
    return re.sub(r"[_-]+", " ", base).strip()[:140] or "Producto sin nombre"


def _variant_label_from_code(codigo_producto: str | None) -> str | None:
    upper = _normalized_name_text(codigo_producto or "")
    match = re.search(r"(?:^|-)0*(\d+)SEC(?:$|-)", upper)
    if match:
        cantidad = int(match.group(1))
        return f"{cantidad} Secciones"
    return None


def _extract_catalog_variant_near_index(lines: list[str], index: int, codigo_producto: str | None) -> str | None:
    start = max(0, index - 4)
    end = min(len(lines), index + 2)
    for pos in range(start, end):
        line = re.sub(r"\s+", " ", (lines[pos] or "")).strip()
        upper = _normalized_name_text(line)
        if not upper:
            continue
        match = re.search(r"\b(\d+)\s+SECCIONES?\b", upper)
        if match:
            return f"{int(match.group(1))} Secciones"
    return _variant_label_from_code(codigo_producto)


def _compose_catalog_name(base_name: str, variant_label: str | None) -> str:
    name = (base_name or "").strip() or "Producto sin nombre"
    if not variant_label:
        return name[:140]
    normalized_name = _normalized_name_text(name)
    normalized_variant = _normalized_name_text(variant_label)
    if normalized_variant and normalized_variant in normalized_name:
        return name[:140]
    if name == "Producto sin nombre":
        return variant_label[:140]
    return f"{name} {variant_label}"[:140]


def _extract_catalog_name_near_index(
    lines: list[str],
    index: int,
    filename: str,
    codigo_producto: str | None = None,
    line_counts: dict[str, int] | None = None,
) -> str:
    start = max(0, index - 8)
    end = min(len(lines), index + 3)
    ranked = []
    for pos in range(start, end):
        line = re.sub(r"\s+", " ", (lines[pos] or "")).strip()
        if not line:
            continue
        repeated_count = (line_counts or {}).get(_catalog_line_key(line), 1)
        score = _score_name_candidate(line, filename, repeated_count=repeated_count)
        if score < 0:
            continue
        distance = abs(index - pos)
        if pos <= index:
            score += 3
        if distance <= 2:
            score += 2
        if distance >= 6:
            score -= 2
        ranked.append((score, -distance, -pos, line))
    if ranked:
        ranked.sort(reverse=True)
        base_name = ranked[0][3].title()[:140]
    else:
        window = "\n".join(lines[start:end])
        base_name = _extract_name_from_text(window, filename, allow_filename_fallback=False)
    variant_label = _extract_catalog_variant_near_index(lines, index, codigo_producto)
    return _compose_catalog_name(base_name, variant_label)


def _page_marker(page_number: int) -> str:
    return f"[[PAGE:{page_number}]]"


def _parse_page_marker(line: str) -> int | None:
    match = re.fullmatch(r"\[\[PAGE:(\d+)\]\]", (line or "").strip())
    if not match:
        return None
    return int(match.group(1))


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
    for page_number, page in enumerate(reader.pages[:12], start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            continue
        if page_text.strip():
            chunks.append(_page_marker(page_number))
            chunks.append(page_text)
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
        for response_index, image_response in enumerate(image_responses):
            if image_response.get("error", {}).get("message"):
                continue
            text_value = ((image_response.get("fullTextAnnotation") or {}).get("text") or "").strip()
            if text_value:
                page_number = pages[response_index] if response_index < len(pages) else pages[0]
                full_chunks.append(_page_marker(page_number))
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


def _description_from_lines(lines: list[str]) -> str | None:
    parts = []
    for raw in lines:
        line = re.sub(r"\s+", " ", raw).strip()
        upper = line.upper()
        if len(line) < 5:
            continue
        if "$" in line:
            continue
        if re.search(r"CODIGO|CLAVE|MODELO|SKU|REF|PIEZAS|MEDIDAS|COLOR|PRECIO|PUBLICO|VENTA", upper):
            continue
        if re.fullmatch(r"[A-Z0-9\-]{4,}", upper):
            continue
        parts.append(line)
    if not parts:
        return None
    return " ".join(_dedupe_keep_order(parts))[:1200]


def _candidate_key_for_item(file_id: str, data: dict, index: int) -> str:
    code = _canonical_code(data.get("codigo_producto"))
    if code:
        suffix = code
    else:
        name_slug = _slug_text(data.get("nombre") or f"item-{index + 1}")
        price = data.get("precio_publico")
        price_key = "sin-precio"
        try:
            if price not in (None, ""):
                price_key = str(int(round(float(price) * 100)))
        except Exception:
            price_key = "sin-precio"
        suffix = f"{name_slug}-{price_key}"
    return f"{file_id}::item::{suffix[:120]}"


def _extract_catalog_items_from_text(text: str, filename: str, file_id: str) -> list[dict]:
    raw_lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    lines = []
    line_pages: dict[int, int] = {}
    current_page = 1
    for raw_line in raw_lines:
        if not raw_line:
            continue
        page_number = _parse_page_marker(raw_line)
        if page_number is not None:
            current_page = page_number
            continue
        line_pages[len(lines)] = current_page
        lines.append(raw_line)
    if not lines:
        fallback = _extract_pdf_info_from_text(text, filename)
        fallback["candidate_key"] = _candidate_key_for_item(file_id, fallback, 0)
        fallback["orden_detectado"] = 0
        fallback["page_detectada"] = 1
        return [fallback]

    line_counts = {}
    for line in lines:
        key = _catalog_line_key(line)
        if key:
            line_counts[key] = line_counts.get(key, 0) + 1

    candidates = {}
    order = 0
    for index, line in enumerate(lines):
        joined_prices = re.findall(r"\$\s*([0-9][0-9,.]{0,12})", line, flags=re.IGNORECASE)
        if not joined_prices:
            continue
        block_lines = lines[max(0, index - 3): min(len(lines), index + 4)]
        block_text = "\n".join(block_lines)
        precio_publico = _extract_price_from_text(block_text)
        if precio_publico in (None, ""):
            continue
        codigo_producto = _extract_code_from_text(block_text, "")
        nombre = _extract_catalog_name_near_index(lines, index, filename, codigo_producto, line_counts=line_counts)
        descripcion = _description_from_lines(block_lines)
        piezas_por_caja = _extract_pieces_from_text(block_text)
        item = {
            "codigo_producto": codigo_producto,
            "nombre": nombre,
            "precio_publico": precio_publico,
            "piezas_por_caja": piezas_por_caja,
            "descripcion": descripcion,
            "orden_detectado": order,
            "page_detectada": line_pages.get(index, 1),
        }
        candidate_key = _candidate_key_for_item(file_id, item, order)
        item["candidate_key"] = candidate_key
        item["codigo_normalizado"] = _canonical_code(codigo_producto)
        quality = 0
        if codigo_producto:
            quality += 5 + _code_score(codigo_producto)
        if nombre and nombre != "Producto sin nombre":
            quality += 4
        if descripcion:
            quality += 2
        if piezas_por_caja:
            quality += 1
        if not codigo_producto and (
            nombre == "Producto sin nombre"
            or _is_spec_like_name(nombre)
            or _is_category_like_name(nombre)
            or not descripcion
        ):
            continue
        existing = candidates.get(candidate_key)
        if not existing or quality > existing.get("_quality", -1):
            item["_quality"] = quality
            candidates[candidate_key] = item
        order += 1

    results = list(candidates.values())
    results.sort(key=lambda item: item.get("orden_detectado", 0))
    for item in results:
        item.pop("_quality", None)

    if results:
        return results

    fallback = _extract_pdf_info_from_text(text, filename)
    fallback["candidate_key"] = _candidate_key_for_item(file_id, fallback, 0)
    fallback["codigo_normalizado"] = _canonical_code(fallback.get("codigo_producto"))
    fallback["orden_detectado"] = 0
    fallback["page_detectada"] = 1
    return [fallback]


def _extract_catalog_items(file_bytes: bytes, filename: str, file_id: str) -> list[dict]:
    return _extract_catalog_items_from_text(_extract_pdf_text(file_bytes), filename, file_id)


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


def _item_signature(item: dict, proposed: dict, candidate_key: str) -> str:
    return "|".join([
        _signature(item),
        candidate_key,
        str(proposed.get("codigo_normalizado") or ""),
        str(proposed.get("nombre") or ""),
        str(proposed.get("precio_publico") or ""),
        str(proposed.get("piezas_por_caja") or ""),
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
    existing_items = existing_resp.data or []
    existing_map = {item["drive_file_id"]: item for item in existing_items}
    legacy_map: dict[str, list[dict]] = {}
    for stored_item in existing_items:
        extracted = stored_item.get("extracted_data") or {}
        real_file_id = (extracted.get("drive_file_real_id") or stored_item.get("drive_file_id") or "").split("::item::", 1)[0]
        if real_file_id:
            legacy_map.setdefault(real_file_id, []).append(stored_item)
    seen = set()
    resumen = {"nuevos": 0, "actualizados": 0, "precios_modificados": 0, "removidos": 0, "sin_cambios": 0}

    for item in archivos:
        extraction_source = "filename"
        catalog_items = []
        if item.get("mimeType") == "application/pdf":
            try:
                pdf_bytes = _drive_download_file(item["id"], access_token)
                catalog_items, ocr_usado, extraction_source = _extract_catalog_items_with_optional_ocr(
                    pdf_bytes,
                    item.get("name") or "catalogo.pdf",
                    google_config,
                    item["id"],
                )
            except Exception:
                fallback = _extract_pdf_info_from_text("", item.get("name") or "")
                fallback["candidate_key"] = _candidate_key_for_item(item["id"], fallback, 0)
                fallback["orden_detectado"] = 0
                catalog_items = [fallback]
                extraction_source = "filename"
        else:
            fallback = {
                "codigo_producto": _extract_code_from_text("", item.get("name") or ""),
                "nombre": _extract_name_from_text("", item.get("name") or ""),
                "precio_publico": None,
                "piezas_por_caja": None,
                "descripcion": None,
                "candidate_key": _candidate_key_for_item(item["id"], {"nombre": item.get("name") or ""}, 0),
                "orden_detectado": 0,
            }
            catalog_items = [fallback]

        for index, info in enumerate(catalog_items):
            candidate_key = info.get("candidate_key") or _candidate_key_for_item(item["id"], info, index)
            seen.add(candidate_key)
            existing = existing_map.get(candidate_key)
            if not existing:
                legacy_candidates = legacy_map.get(item["id"], [])
                if len(legacy_candidates) == 1 and len(catalog_items) == 1:
                    existing = legacy_candidates[0]

            proposed = {
                "candidate_key": candidate_key,
                "drive_file_real_id": item["id"],
                "codigo_producto": info.get("codigo_producto"),
                "codigo_normalizado": _canonical_code(info.get("codigo_producto")),
                "nombre": info.get("nombre"),
                "categoria": item.get("categoria") or "Sin categoria",
                "precio_publico": info.get("precio_publico"),
                "piezas_por_caja": info.get("piezas_por_caja"),
                "descripcion": info.get("descripcion"),
                "mime_type": item.get("mimeType"),
                "web_view_link": item.get("webViewLink"),
                "origen_extraccion": extraction_source,
                "orden_detectado": info.get("orden_detectado", index),
            }
            proposed["motivos_revision"] = _build_public_revision_flags(proposed, extraction_source=extraction_source)
            proposed["requiere_revision"] = bool(proposed["motivos_revision"])
            sign = _item_signature(item, proposed, candidate_key)

            if not existing:
                payload = {
                    "id": str(uuid.uuid4()),
                    "id_empresa": id_empresa,
                    "id_fuente": fuente["id"],
                    "drive_file_id": candidate_key,
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
                titulo_producto = proposed.get("nombre") or item.get("name") or "Producto sin nombre"
                _upsert_revision(id_empresa, fuente["id"], item_record, "nuevo", f"Nuevo producto detectado: {titulo_producto}", "Se detecto un producto nuevo dentro del catalogo del proveedor pendiente de revision.", {}, proposed)
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

            update_payload.update({"signature": sign, "extracted_data": proposed, "estado_sync": "vigente", "drive_file_id": candidate_key})
            updated = supabase.table("catalogo_drive_items").update(update_payload).eq("id", existing["id"]).execute()
            item_record = updated.data[0] if updated.data else {**existing, **update_payload}
            titulo_producto = proposed.get("nombre") or item.get("name") or "Producto sin nombre"
            _upsert_revision(id_empresa, fuente["id"], item_record, tipo, f"Cambio detectado en {titulo_producto}", "Se detecto un cambio en el catalogo del proveedor.", anterior, proposed)

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
        costo = costos_map.get(codigo) if codigo else None
        precio_base = proposed.get("precio_publico")
        if precio_base in (None, ""):
            precio_base = previous.get("precio_publico") or previous.get("precio")
        utilidad_estimada = None
        margen_estimado = None
        if costo and precio_base not in (None, ""):
            try:
                precio_num = float(precio_base)
                costo_num = float(costo.get("costo_adquisicion") or 0)
                utilidad_estimada = round(precio_num - costo_num, 2)
                margen_estimado = round((utilidad_estimada / precio_num) * 100, 2) if precio_num else None
            except Exception:
                utilidad_estimada = None
                margen_estimado = None

        motivos = []
        for source in [previous.get("motivos_revision") or [], proposed.get("motivos_revision") or []]:
            for item in source:
                if item:
                    motivos.append(str(item).strip())
        if codigo and not costo:
            motivos.append("No hay costo interno ligado para este codigo.")

        salida.append({
            **revision,
            "drive_item": items_map.get(revision.get("drive_item_id"), {}),
            "costo_registrado": costo,
            "codigo_normalizado": proposed.get("codigo_normalizado") or previous.get("codigo_normalizado") or _canonical_code(codigo),
            "codigo_costo_ligado": costo.get("codigo_producto") if costo else None,
            "motivos_revision": _dedupe_keep_order(motivos),
            "requiere_revision": bool(_dedupe_keep_order(motivos)),
            "origen_extraccion": proposed.get("origen_extraccion") or previous.get("origen_extraccion"),
            "utilidad_estimada": utilidad_estimada,
            "margen_estimado": margen_estimado,
        })
    return {"pendientes": salida}

def _buscar_costo(id_empresa: str, codigo_producto: str | None):
    if not codigo_producto:
        return None
    codigo = codigo_producto.strip().upper()
    resp = supabase.table("catalogo_costos_proveedor").select("*").eq("id_empresa", id_empresa).eq("codigo_producto", codigo).limit(1).execute()
    if resp.data:
        return resp.data[0]
    fallback = supabase.table("catalogo_costos_proveedor").select("*").eq("id_empresa", id_empresa).execute()
    return _pick_best_row_by_code(codigo, fallback.data or [])


def _buscar_costos(id_empresa: str, codigos: list[str]) -> dict[str, dict]:
    clean = [codigo.strip().upper() for codigo in codigos if codigo and codigo.strip()]
    if not clean:
        return {}
    resp = supabase.table("catalogo_costos_proveedor").select("*").eq("id_empresa", id_empresa).execute()
    rows = resp.data or []
    resultados = {}
    for codigo in clean:
        match = _pick_best_row_by_code(codigo, rows)
        if match:
            resultados[codigo] = match
    return resultados


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


def _extract_money_values(text: str, *, allow_plain_numbers: bool = True, blocked_numbers: set[str] | None = None) -> list[float]:
    blocked_numbers = blocked_numbers or set()
    values = []
    patterns = [
        r"\$\s*([0-9]{1,4}(?:,[0-9]{3})*(?:\.\d{2})?)",
    ]
    if allow_plain_numbers:
        patterns.append(r"(?<!\d)([0-9]{1,4}(?:,[0-9]{3})*(?:\.\d{2}))(?!\d)")
    for pattern in patterns:
        for raw in re.findall(pattern, text or "", flags=re.IGNORECASE):
            normalized_raw = raw.replace(",", "")
            integer_part = normalized_raw.split(".", 1)[0]
            if integer_part in blocked_numbers:
                continue
            try:
                value = float(normalized_raw)
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


def _normalize_letters_segment(value: str) -> str:
    return (value or "").upper().translate(str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B"}))


def _normalize_digits_segment(value: str) -> str:
    return (value or "").upper().translate(str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8"}))


def _normalize_alnum_segment(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _normalize_code_candidate(raw: str) -> str | None:
    cleaned = (raw or "").upper()
    cleaned = re.sub(r"[^A-Z0-9]+", "-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    parts = [part for part in cleaned.split("-") if part]

    if len(parts) == 1:
        single = _normalize_alnum_segment(parts[0])
        for index, char in enumerate(single):
            if index < 2:
                continue
            if char.isdigit() or char in {"O", "Q", "D", "I", "L", "S", "B", "Z"}:
                prefix = _normalize_letters_segment(single[:index])
                suffix = _normalize_digits_segment(single[index:])
                if re.fullmatch(r"[A-Z]{2,6}", prefix) and re.fullmatch(r"\d{2,6}", suffix):
                    return prefix + suffix
                break
        if re.fullmatch(r"[A-Z]{2,6}\d{2,6}", _normalize_letters_segment(single[:6]) + _normalize_digits_segment(single[6:])):
            return single
        if re.fullmatch(r"\d{3,6}", _normalize_digits_segment(single)):
            return _normalize_digits_segment(single)

    if len(parts) == 2:
        first = _normalize_alnum_segment(parts[0])
        second = _normalize_alnum_segment(parts[1])
        first_candidate = _normalize_letters_segment(re.sub(r"\d", "", first)) + _normalize_digits_segment(re.sub(r"\D", "", first))
        if re.fullmatch(r"\d{3}", _normalize_digits_segment(first)) and re.fullmatch(r"[A-Z]{4,20}", _normalize_letters_segment(second)):
            return _normalize_digits_segment(first)
        if re.fullmatch(r"[A-Z]{1,4}\d{4,6}", first_candidate) and re.fullmatch(r"[A-Z0-9]{2,12}", second):
            return f"{first_candidate}-{second}"

    if len(parts) >= 3:
        first = _normalize_letters_segment(parts[0])
        second = _normalize_digits_segment(parts[1])
        third = _normalize_alnum_segment(parts[2])
        if re.fullmatch(r"[A-Z]{1,5}", first) and re.fullmatch(r"\d{2,5}", second) and re.fullmatch(r"[A-Z0-9]{2,12}", third):
            return "-".join([first, second, third])

    if 3 <= len(parts) <= 4:
        fixed_parts = [_normalize_digits_segment(parts[0])] + [_normalize_alnum_segment(part) for part in parts[1:4]]
        if re.fullmatch(r"\d{2,5}", fixed_parts[0]) and all(re.fullmatch(r"[A-Z0-9]{1,12}", part) for part in fixed_parts[1:]):
            return "-".join(fixed_parts[: len(parts)])

    return None


def _code_score(code: str) -> int:
    score = 0
    if "-" in code:
        score += code.count("-") * 2
    if re.search(r"[A-Z]", code) and re.search(r"\d", code):
        score += 3
    if len(code) >= 8:
        score += 2
    if re.fullmatch(r"\d{3}", code):
        score -= 3
    elif re.fullmatch(r"\d{4,6}", code):
        score += 1
    return score


def _prune_code_candidates(codes: list[str]) -> list[str]:
    unique = []
    seen = set()
    for code in codes:
        if not code or code in seen:
            continue
        seen.add(code)
        unique.append(code)
    rich_codes = [code for code in unique if _code_score(code) >= 4]
    pruned = []
    for code in unique:
        if any(code != rich and code in rich for rich in rich_codes):
            continue
        if rich_codes and re.fullmatch(r"\d{3,4}", code):
            continue
        pruned.append(code)
    return pruned


def _extract_code_candidates(text: str) -> list[str]:
    if not text:
        return []
    patterns = [
        r"\b[A-Z]{1,5}\s*[- ]\s*\d{2,5}\s*[- ]\s*[A-Z0-9]{2,12}\b",
        r"\b[A-Z]{1,5}-\d{2,5}[A-Z0-9]{2,12}\b",
        r"\b[A-Z]{1,5}\d{2,5}\s*[- ]\s*[A-Z0-9]{2,12}\b",
        r"\b\d{2,5}\s*[- ]\s*[A-Z0-9]{1,12}\s*[- ]\s*[A-Z0-9]{1,12}\s*[- ]\s*[A-Z0-9]{1,12}\b",
        r"\b\d{2,5}\s+[A-Z0-9]{1,12}\s+\d{2,5}\s+\d{2,5}\b",
        r"\b[A-Z]{2,6}\d{2,6}\b",
        r"\b\d{4,6}\b",
        r"\b\d{3}\s+[A-Z]{4,20}\b",
        r"\b[A-Z]{1,4}\d{4,6}-[A-Z0-9]{2,12}\b",
    ]
    found = []
    for pattern in patterns:
        for raw in re.findall(pattern, (text or "").upper()):
            normalized = _normalize_code_candidate(raw)
            if normalized:
                found.append(normalized)
    return _prune_code_candidates(found)


def _pick_cost_from_context(lines: list[str], index: int, codes: list[str]) -> float | None:
    window_lines = lines[max(0, index - 1): min(len(lines), index + 3)]
    window = " ".join(window_lines)
    blocked_numbers = {re.sub(r"\D", "", code) for code in codes if re.fullmatch(r"\d{3,6}", re.sub(r"\D", "", code))}

    tagged = re.findall(r"(?:costo|distribuidor|precio|p\.\s*distribuidor|p/pza|pza\.|p/par|del par)[^0-9$]{0,12}\$?\s*([0-9]{1,4}(?:,[0-9]{3})*(?:\.\d{2})?)", window, flags=re.IGNORECASE)
    for raw in tagged:
        try:
            return round(float(raw.replace(",", "")), 2)
        except Exception:
            continue

    currency_values = _extract_money_values(window, allow_plain_numbers=False, blocked_numbers=blocked_numbers)
    if currency_values:
        return currency_values[0]

    if re.search(r"P/PZA|PZA\.|P/PAR|DEL PAR|PRECIO|COSTO|DISTRIBUIDOR", window, flags=re.IGNORECASE):
        plain_values = _extract_money_values(window, allow_plain_numbers=True, blocked_numbers=blocked_numbers)
        if plain_values:
            return plain_values[0]

    return None


def _extract_cost_rows_from_text(raw_text: str, filename: str) -> list[dict]:
    if not raw_text.strip():
        return []
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw_text.splitlines()]
    lines = [line for line in lines if line]
    results = {}
    for index, line in enumerate(lines):
        window_text = line
        if index + 1 < len(lines):
            window_text = f"{line} {lines[index + 1]}"
        codes = _extract_code_candidates(window_text)
        if not codes:
            continue
        cost = _pick_cost_from_context(lines, index, codes)
        if cost is None:
            continue
        name = _extract_name_from_text("\n".join(lines[max(0, index - 1): min(len(lines), index + 2)]), filename)
        for code in codes:
            quality = _code_score(code) + (2 if "$" in window_text else 0)
            row = {
                "codigo_producto": code.upper(),
                "costo_adquisicion": cost,
                "nombre_detectado": name,
                "calidad": quality,
            }
            existing = results.get(code)
            if not existing or row["calidad"] > existing.get("calidad", 0):
                results[code] = row
    ordered = sorted(results.values(), key=lambda item: (-item.get("calidad", 0), item.get("codigo_producto") or ""))
    for item in ordered:
        item.pop("calidad", None)
    return ordered


def _extract_cost_rows_from_pdf(file_bytes: bytes, filename: str) -> list[dict]:
    return _extract_cost_rows_from_text(_extract_pdf_text(file_bytes), filename)


def _row_requires_review(row: dict) -> bool:
    code = (row.get("codigo_producto") or "").strip().upper()
    if not code:
        return True
    if re.fullmatch(r"\d{3}", code):
        return True
    if len(code) < 5:
        return True
    if code.count("-") >= 3 and len(code) < 8:
        return True
    return False


def _build_import_warnings(rows: list[dict], *, ocr_usado: bool, review_rows: list[dict] | None = None) -> list[str]:
    warnings = []
    review_rows = review_rows or []
    if ocr_usado and len(rows) < 3:
        warnings.append("Cobertura baja: se detectaron muy pocos articulos; revisa el OCR antes de confiar en la importacion completa.")
    short_codes = [row.get("codigo_producto") for row in rows if re.fullmatch(r"\d{3}", row.get("codigo_producto") or "")]
    if short_codes:
        sample = ", ".join(short_codes[:5])
        warnings.append(f"Hay claves cortas que pueden requerir revision manual: {sample}.")
    if review_rows:
        sample = ", ".join([(row.get("codigo_producto") or "").strip() for row in review_rows[:5] if row.get("codigo_producto")])
        warnings.append(f"Se omitieron {len(review_rows)} claves dudosas del guardado automatico para revision manual: {sample}.")
    return warnings


def _merge_catalog_items(base_items: list[dict], extra_items: list[dict], *, file_id: str) -> list[dict]:
    if not base_items:
        return extra_items
    if not extra_items:
        return base_items

    merged = []
    used_extra_keys = set()
    for index, base_item in enumerate(base_items):
        match = _pick_best_row_by_code(base_item.get("codigo_producto"), extra_items) or (extra_items[index] if index < len(extra_items) else None)
        merged_item = dict(base_item)
        if match:
            used_extra_keys.add(match.get("candidate_key") or _candidate_key_for_item(file_id, match, index))
            if match.get("codigo_producto") and (
                not merged_item.get("codigo_producto")
                or _code_score(match.get("codigo_producto") or "") > _code_score(merged_item.get("codigo_producto") or "")
            ):
                merged_item["codigo_producto"] = match.get("codigo_producto")
            if match.get("precio_publico") not in (None, "") and merged_item.get("precio_publico") in (None, ""):
                merged_item["precio_publico"] = match.get("precio_publico")
            if match.get("nombre") and (not merged_item.get("nombre") or merged_item.get("nombre") == "Producto sin nombre"):
                merged_item["nombre"] = match.get("nombre")
            if match.get("piezas_por_caja") and not merged_item.get("piezas_por_caja"):
                merged_item["piezas_por_caja"] = match.get("piezas_por_caja")
            if match.get("descripcion") and (not merged_item.get("descripcion") or len(match.get("descripcion") or "") > len(merged_item.get("descripcion") or "")):
                merged_item["descripcion"] = match.get("descripcion")
        merged.append(merged_item)

    for extra_index, extra_item in enumerate(extra_items):
        extra_key = extra_item.get("candidate_key") or _candidate_key_for_item(file_id, extra_item, extra_index)
        if extra_key in used_extra_keys:
            continue
        merged.append(extra_item)
    return merged


def _extract_catalog_items_with_optional_ocr(file_bytes: bytes, filename: str, google_config: dict, file_id: str) -> tuple[list[dict], bool, str | None]:
    texto_pdf = _extract_pdf_text(file_bytes)
    ocr_usado = False
    extraction_source = "filename"
    items = []

    if texto_pdf.strip():
        items = _extract_catalog_items_from_text(texto_pdf, filename, file_id)
        extraction_source = "pdf_text"

    needs_ocr = not texto_pdf.strip() or not items or any(not row.get("codigo_producto") or row.get("precio_publico") in (None, "") for row in items)
    if needs_ocr:
        try:
            ocr_text = _vision_ocr_pdf(file_bytes, filename, google_config)
            if ocr_text.strip():
                ocr_usado = True
                ocr_items = _extract_catalog_items_from_text(ocr_text, filename, file_id)
                items = _merge_catalog_items(items, ocr_items, file_id=file_id) if items else ocr_items
                extraction_source = "ocr"
        except HTTPException:
            raise
        except Exception:
            pass

    if not items:
        fallback = _extract_pdf_info_from_text("", filename)
        fallback["candidate_key"] = _candidate_key_for_item(file_id, fallback, 0)
        fallback["codigo_normalizado"] = _canonical_code(fallback.get("codigo_producto"))
        fallback["orden_detectado"] = 0
        items = [fallback]

    for index, item in enumerate(items):
        item.setdefault("candidate_key", _candidate_key_for_item(file_id, item, index))
        item["codigo_normalizado"] = _canonical_code(item.get("codigo_producto"))
    return items, ocr_usado, extraction_source


def _build_public_catalog_warnings(items: list[dict], *, ocr_usado: bool) -> list[str]:
    warnings = []
    if ocr_usado and len(items) < 3:
        warnings.append("Cobertura baja: OCR detecto pocos articulos para un catalogo publico; valida manualmente el PDF.")
    missing_price = [item for item in items if item.get("precio_publico") in (None, "")]
    if missing_price:
        warnings.append(f"Hay {len(missing_price)} articulos sin precio_publico detectado.")
    weak_names = [
        item
        for item in items
        if not item.get("nombre")
        or item.get("nombre") == "Producto sin nombre"
        or _is_generic_catalog_name(item.get("nombre") or "", "")
    ]
    if weak_names:
        warnings.append(f"Hay {len(weak_names)} articulos con nombre debil o incompleto.")
    short_codes = [item.get("codigo_producto") for item in items if _row_requires_review({"codigo_producto": item.get("codigo_producto")}) and item.get("codigo_producto")]
    if short_codes:
        sample = ", ".join(short_codes[:5])
        warnings.append(f"Hay codigos ambiguos o cortos que requieren revision: {sample}.")
    return warnings


def _canonical_code(value: str | None) -> str | None:
    raw = (value or "").strip().upper()
    if not raw:
        return None
    normalized = _normalize_code_candidate(raw)
    if normalized:
        return normalized
    compact = _clean_compact_code(raw)
    return compact or None


def _code_lookup_keys(value: str | None) -> list[str]:
    raw = (value or "").strip().upper()
    canonical = _canonical_code(raw)
    compact_raw = _clean_compact_code(raw)
    compact_canonical = _clean_compact_code(canonical)
    return _dedupe_keep_order([raw, canonical or "", compact_raw, compact_canonical])


def _pick_best_row_by_code(code: str | None, rows: list[dict], *, field: str = "codigo_producto") -> dict | None:
    requested_canonical = _canonical_code(code)
    lookup_keys = set(_code_lookup_keys(code))
    if not lookup_keys:
        return None

    best = None
    best_score = -1
    for row in rows:
        row_code = row.get(field)
        row_keys = set(_code_lookup_keys(row_code))
        if not row_keys:
            continue
        overlap = lookup_keys & row_keys
        if not overlap:
            continue
        canonical = _canonical_code(row_code) or ""
        score = len(overlap) * 10
        if requested_canonical and canonical == requested_canonical:
            score += 8
        if canonical:
            score += _code_score(canonical)
        if best is None or score > best_score:
            best = row
            best_score = score
    return best


def _build_public_revision_flags(proposed: dict, *, extraction_source: str) -> list[str]:
    flags = []
    codigo = (proposed.get("codigo_producto") or "").strip().upper()
    precio = proposed.get("precio_publico")
    nombre = (proposed.get("nombre") or "").strip()
    if not codigo:
        flags.append("No se detecto codigo_producto.")
    elif _row_requires_review({"codigo_producto": codigo}):
        flags.append("El codigo detectado parece corto o ambiguo; revisar antes de publicar.")
    if precio in (None, ""):
        flags.append("No se detecto precio_publico.")
    if not nombre or nombre.lower() == "producto sin nombre":
        flags.append("No se detecto un nombre limpio del producto.")
    if extraction_source == "filename":
        flags.append("La extraccion dependio del nombre del archivo; valida codigo, nombre y precio.")
    if extraction_source == "ocr":
        flags.append("Se uso OCR para leer el catalogo; confirma los campos detectados.")
    return flags


def _buscar_producto_por_codigo(id_empresa: str, codigo_producto: str | None):
    if not codigo_producto:
        return None
    codigo = codigo_producto.strip().upper()
    resp = supabase.table("productos").select("id,codigo_producto").eq("id_empresa", id_empresa).eq("codigo_producto", codigo).limit(1).execute()
    if resp.data:
        return resp.data[0]
    fallback = supabase.table("productos").select("id,codigo_producto").eq("id_empresa", id_empresa).execute()
    return _pick_best_row_by_code(codigo, fallback.data or [])


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
        "origen_drive_file_id": proposed.get("drive_file_real_id") or drive_item.get("drive_file_id"),
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


@router.post("/catalogos/importar-pdfs-publicos")
def importar_catalogos_publicos_pdf(
    files: list[UploadFile] = File(...),
    usuario=Depends(get_current_user),
):
    id_empresa = _id_empresa(usuario)
    archivos = [file for file in files if file and (file.filename or "").lower().endswith(".pdf")]
    if not archivos:
        raise HTTPException(status_code=400, detail="Adjunta al menos un PDF valido")

    google_config = _google_service_config()
    resumen_global = {
        "archivos_procesados": 0,
        "productos_detectados": 0,
        "archivos": [],
    }

    for archivo in archivos:
        contenido = archivo.file.read()
        file_id = f"upload:{uuid.uuid4().hex}"
        ocr_error = None
        try:
            items, ocr_usado, extraction_source = _extract_catalog_items_with_optional_ocr(
                contenido,
                archivo.filename or "catalogo.pdf",
                google_config,
                file_id,
            )
        except HTTPException as exc:
            items = []
            ocr_usado = False
            extraction_source = "error"
            ocr_error = str(exc.detail)

        warnings = _build_public_catalog_warnings(items, ocr_usado=ocr_usado)
        ejemplos = []
        items_payload = []
        for index, item in enumerate(items):
            codigo_producto = item.get("codigo_producto")
            costo = _buscar_costo(id_empresa, codigo_producto) if codigo_producto else None
            costo_adquisicion = float(costo.get("costo_adquisicion") or 0) if costo else None
            precio_publico = item.get("precio_publico")
            utilidad_estimada = None
            margen_estimado = None
            if precio_publico not in (None, "") and costo_adquisicion is not None:
                try:
                    utilidad_estimada = round(float(precio_publico) - float(costo_adquisicion), 2)
                    if float(precio_publico) > 0:
                        margen_estimado = round((utilidad_estimada / float(precio_publico)) * 100, 2)
                except Exception:
                    utilidad_estimada = None
                    margen_estimado = None

            payload_item = {
                "id": item.get("candidate_key") or f"{file_id}:{index}",
                "candidate_key": item.get("candidate_key") or f"{file_id}:{index}",
                "nombre": item.get("nombre"),
                "codigo_producto": codigo_producto,
                "precio_publico": precio_publico,
                "piezas_por_caja": item.get("piezas_por_caja"),
                "descripcion": item.get("descripcion"),
                "orden_detectado": item.get("orden_detectado", index),
                "page_detectada": item.get("page_detectada", 1),
                "costo_adquisicion": costo_adquisicion,
                "utilidad_estimada": utilidad_estimada,
                "margen_estimado": margen_estimado,
                "requiere_revision": not codigo_producto or not item.get("nombre") or item.get("nombre") == "Producto sin nombre",
            }
            items_payload.append(payload_item)
            if index < 8:
                ejemplos.append({
                    "nombre": payload_item.get("nombre"),
                    "codigo_producto": payload_item.get("codigo_producto"),
                    "precio_publico": payload_item.get("precio_publico"),
                    "piezas_por_caja": payload_item.get("piezas_por_caja"),
                })

        resumen_archivo = {
            "nombre_archivo": archivo.filename,
            "productos_detectados": len(items),
            "ocr_usado": ocr_usado,
            "ocr_error": ocr_error,
            "origen_extraccion": extraction_source,
            "requiere_revision": bool(warnings),
            "advertencias": warnings,
            "ejemplos": ejemplos,
            "items": items_payload,
        }
        resumen_global["archivos_procesados"] += 1
        resumen_global["productos_detectados"] += len(items)
        resumen_global["archivos"].append(resumen_archivo)

    return {"mensaje": "Analisis de catalogos publicos completado", "resumen": resumen_global}


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
        approved_rows = [row for row in rows if not _row_requires_review(row)]
        review_rows = [row for row in rows if _row_requires_review(row)]
        guardados = 0
        for row in approved_rows:
            _guardar_costo(id_empresa, row["codigo_producto"], row["costo_adquisicion"], proveedor_value, f"Importado desde PDF: {archivo.filename}")
            supabase.table("productos").update({"costo_adquisicion": float(row["costo_adquisicion"])}).eq("id_empresa", id_empresa).eq("codigo_producto", row["codigo_producto"]).execute()
            guardados += 1

        ocr_preview = None
        if texto_pdf.strip() and not rows:
            compact_preview = re.sub(r"\s+", " ", texto_pdf).strip()
            ocr_preview = compact_preview[:800] if compact_preview else None

        advertencias = _build_import_warnings(rows, ocr_usado=ocr_usado, review_rows=review_rows)
        resumen_archivo = {
            "nombre_archivo": archivo.filename,
            "costos_detectados": len(rows),
            "costos_guardados": guardados,
            "costos_revision": len(review_rows),
            "requiere_ocr": requiere_ocr,
            "ocr_usado": ocr_usado,
            "ocr_error": ocr_error,
            "ocr_preview": ocr_preview,
            "requiere_revision": bool(advertencias),
            "advertencias": advertencias,
            "ejemplos": approved_rows[:5],
            "ejemplos_revision": review_rows[:5],
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
