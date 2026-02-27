from fastapi import APIRouter, Depends, HTTPException
from dependencies import get_current_user
from database import supabase

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/tiendas")
def listar_tiendas(usuario = Depends(get_current_user)):

    if usuario.get("nivel") != "admin_master":
        raise HTTPException(status_code=403, detail="No autorizado")

    return {"mensaje": "admin endpoint funcionando"}