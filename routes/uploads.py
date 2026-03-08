from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
import os
import uuid
from database import supabase
from dependencies import get_current_user

router = APIRouter(prefix="/uploads", tags=["Uploads"])

BUCKET_PRODUCTOS = os.getenv("SUPABASE_BUCKET_PRODUCTOS", "public")
BUCKET_LOGOS = os.getenv("SUPABASE_BUCKET_LOGOS", "public")


def _upload(file: UploadFile, bucket: str, prefix: str) -> str:
    contents = file.file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Archivo vacío")

    ext = os.path.splitext(file.filename or "")[1] or ".bin"
    key = f"{prefix}/{uuid.uuid4()}{ext}"

    try:
        supabase.storage.from_(bucket).upload(key, contents, {"content-type": file.content_type or "application/octet-stream"})
        url = supabase.storage.from_(bucket).get_public_url(key)
        return url
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo subir archivo: {e}")


@router.post("/producto-imagen")
def subir_imagen_producto(file: UploadFile = File(...), usuario=Depends(get_current_user)):
    url = _upload(file, BUCKET_PRODUCTOS, "productos")
    return {"url": url}


@router.post("/logo-tienda")
def subir_logo_tienda(file: UploadFile = File(...), usuario=Depends(get_current_user)):
    url = _upload(file, BUCKET_LOGOS, "logos")
    return {"url": url}
