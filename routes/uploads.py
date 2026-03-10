from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
import os
import uuid
from database import supabase
from dependencies import get_current_user

router = APIRouter(prefix="/uploads", tags=["Uploads"])

BUCKET_PRODUCTOS = os.getenv("SUPABASE_BUCKET_PRODUCTOS", "productos")
BUCKET_LOGOS = os.getenv("SUPABASE_BUCKET_LOGOS", "logos")
_BUCKET_MIME_TYPES = [
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/svg+xml",
    "application/octet-stream",
]
_BUCKET_FILE_SIZE_LIMIT = 10 * 1024 * 1024


def _ensure_bucket(bucket: str) -> None:
    try:
        supabase.storage.get_bucket(bucket)
        return
    except Exception:
        pass

    try:
        supabase.storage.create_bucket(
            bucket,
            bucket,
            {
                "public": True,
                "allowed_mime_types": _BUCKET_MIME_TYPES,
                "file_size_limit": _BUCKET_FILE_SIZE_LIMIT,
            },
        )
    except Exception as create_error:
        message = str(create_error)
        if "already exists" not in message.lower():
            raise HTTPException(
                status_code=500,
                detail=f"El bucket '{bucket}' no existe y no se pudo crear automaticamente: {create_error}",
            )


def _upload(file: UploadFile, bucket: str, prefix: str) -> str:
    contents = file.file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Archivo vacio")

    _ensure_bucket(bucket)

    ext = os.path.splitext(file.filename or "")[1] or ".bin"
    key = f"{prefix}/{uuid.uuid4()}{ext}"

    try:
        supabase.storage.from_(bucket).upload(key, contents, {"content-type": file.content_type or "application/octet-stream"})
        url = supabase.storage.from_(bucket).get_public_url(key)
        return url
    except Exception as e:
        message = str(e)
        if "Bucket not found" in message:
            raise HTTPException(
                status_code=500,
                detail=f"No se encontro el bucket '{bucket}'. Revisa SUPABASE_BUCKET_PRODUCTOS / SUPABASE_BUCKET_LOGOS en Render.",
            )
        raise HTTPException(status_code=500, detail=f"No se pudo subir archivo: {e}")


@router.post("/producto-imagen")
def subir_imagen_producto(file: UploadFile = File(...), usuario=Depends(get_current_user)):
    url = _upload(file, BUCKET_PRODUCTOS, "productos")
    return {"url": url}


@router.post("/logo-tienda")
def subir_logo_tienda(file: UploadFile = File(...), usuario=Depends(get_current_user)):
    url = _upload(file, BUCKET_LOGOS, "logos")
    return {"url": url}
