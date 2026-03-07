from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from dependencies import get_current_user
from database import supabase

router = APIRouter(prefix="/admin/cargos", tags=["Admin Cargos"])


# 🔹 Modelo creación
class CargoCreate(BaseModel):
    id_empresa: str
    concepto: str
    monto: float
    es_recurrente: Optional[bool] = True


# 🔹 Crear cargo
@router.post("")
def crear_cargo(data: CargoCreate, usuario=Depends(get_current_user)):

    if usuario["nivel_global"] != "admin_master":
        raise HTTPException(status_code=403, detail="No autorizado")

    response = supabase.table("cargos_empresa").insert({
        "id_empresa": data.id_empresa,
        "concepto": data.concepto,
        "monto": data.monto,
        "es_recurrente": data.es_recurrente,
        "activo": True
    }).execute()
    # 🔥 Ejecutar motor financiero después de crear cargo
    try:
        supabase.rpc("motor_financiero_saas", {}).execute()
    except Exception as e:
        print("ERROR EJECUTANDO MOTOR FINANCIERO:", e)
    return {"ok": True, "data": response.data}


# 🔹 Listar cargos por empresa
@router.get("/{id_empresa}")
def listar_cargos(id_empresa: str, usuario=Depends(get_current_user)):

    if usuario["nivel_global"] != "admin_master":
        raise HTTPException(status_code=403, detail="No autorizado")

    response = supabase.table("cargos_empresa") \
        .select("*") \
        .eq("id_empresa", id_empresa) \
        .order("fecha_creacion", desc=True) \
        .execute()

    return response.data


# 🔹 Activar / Desactivar cargo
class CargoUpdate(BaseModel):
    activo: bool


@router.patch("/{id_cargo}")
def actualizar_cargo(id_cargo: str, data: CargoUpdate, usuario=Depends(get_current_user)):

    if usuario["nivel_global"] != "admin_master":
        raise HTTPException(status_code=403, detail="No autorizado")

    response = supabase.table("cargos_empresa") \
        .update({"activo": data.activo}) \
        .eq("id", id_cargo) \
        .execute()

    return {"ok": True}
