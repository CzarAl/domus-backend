from fastapi import APIRouter, Depends, HTTPException
from dependencies import get_current_user
from database import supabase

router = APIRouter(prefix="/empresa", tags=["Empresa Finanzas"])


@router.get("/estado-financiero")
def estado_financiero(usuario=Depends(get_current_user)):

    id_empresa = usuario.get("id_raiz")

    if not id_empresa:
        raise HTTPException(status_code=403, detail="Usuario sin empresa asignada")

    # 🔹 Obtener periodo activo
    cuenta = supabase.table("cuentas_matriz") \
        .select("*") \
        .eq("id_empresa_matriz", id_empresa) \
        .order("periodo_fin", desc=True) \
        .limit(1) \
        .execute()

    if not cuenta.data:
        raise HTTPException(status_code=404, detail="No hay cuenta activa")

    cuenta_actual = cuenta.data[0]

    # 🔹 Obtener cargos activos
    cargos = supabase.table("cargos_empresa") \
        .select("concepto, monto") \
        .eq("id_empresa", id_empresa) \
        .eq("activo", True) \
        .execute()

    total = sum(c["monto"] for c in cargos.data) if cargos.data else 0

    return {
        "periodo_inicio": cuenta_actual["periodo_inicio"],
        "periodo_fin": cuenta_actual["periodo_fin"],
        "estado": cuenta_actual["estado"],
        "cargos": cargos.data,
        "total": total
    }