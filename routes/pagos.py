from fastapi import APIRouter, Depends, HTTPException
from dependencies import get_current_user
from database import supabase

router = APIRouter(prefix="/pagos", tags=["Panel Pago"])

ESTADO_VENCIDA = "vencida"
ESTADOS_VENCIDA_ALIAS = [ESTADO_VENCIDA, "vencido"]


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
        .in_("estado", ESTADOS_VENCIDA_ALIAS) \
        .execute()

    return {"deudas": deuda.data}
