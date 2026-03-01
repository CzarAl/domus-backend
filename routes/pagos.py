from fastapi import APIRouter, Depends, HTTPException
from dependencies import get_current_user
from database import supabase

router = APIRouter(prefix="/pagos", tags=["Panel Pago"])


@router.get("/deuda")
def ver_deuda(usuario: dict = Depends(get_current_user)):

    id_empresa = usuario.get("id_empresa")

    if not id_empresa:
        raise HTTPException(
            status_code=403,
            detail="Usuario no tiene empresa asignada"
        )

    deuda = supabase.table("cuentas_matriz") \
        .select("*") \
        .eq("id_empresa_matriz", id_empresa) \
        .eq("estado", "vencida") \
        .execute()

    return {"deudas": deuda.data}