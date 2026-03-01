from fastapi import APIRouter, Depends, HTTPException
from dependencies import get_user_solo_token
from database import supabase

router = APIRouter(prefix="/pagos", tags=["Panel Pago"])


@router.get("/deuda")
def ver_deuda(usuario=Depends(get_user_solo_token)):

    id_empresa = usuario.get("id_empresa")

    deuda = supabase.table("cuentas_matriz") \
        .select("*") \
        .eq("id_empresa_matriz", id_empresa) \
        .eq("estado", "vencida") \
        .execute()

    return {"deudas": deuda.data}