from fastapi import APIRouter, Depends, HTTPException
from database import supabase
from dependencies import get_current_user
from datetime import datetime
from dateutil import parser

router = APIRouter(prefix="/ajustes", tags=["Ajustes SaaS"])


def validar_empresa_activa(id_empresa):

    empresa = supabase.table("empresas") \
        .select("estado") \
        .eq("id", id_empresa) \
        .execute().data

    if not empresa or empresa[0]["estado"] != "activa":
        raise HTTPException(403, "Empresa suspendida")

    vencidas = supabase.table("cuentas_matriz") \
        .select("id") \
        .eq("id_empresa_matriz", id_empresa) \
        .eq("estado", "vencida") \
        .execute().data

    if vencidas:
        raise HTTPException(403, "Empresa con deuda")


@router.post("/crear")
def crear_ajuste(
    recurso: str,
    cantidad: int,
    costo_unitario: float,
    usuario=Depends(get_current_user)
):

    id_empresa = usuario.get("id_empresa")
    validar_empresa_activa(id_empresa)

    # Obtener periodo activo
    cuenta = supabase.table("cuentas_matriz") \
        .select("*") \
        .eq("id_empresa_matriz", id_empresa) \
        .eq("estado", "activa") \
        .order("fecha_vencimiento", desc=True) \
        .limit(1) \
        .execute().data

    if not cuenta:
        raise HTTPException(400, "No hay periodo activo")

    cuenta = cuenta[0]

    hoy = datetime.utcnow().date()
    fin = cuenta["fecha_vencimiento"]

    dias_restantes = (parser.parse(fin).date() - hoy).days
    dias_total = (parser.parse(cuenta["periodo_fin"]).date() -
                  parser.parse(cuenta["periodo_inicio"]).date()).days

    if dias_restantes <= 0:
        raise HTTPException(400, "Periodo por vencer")

    proporcional = (costo_unitario * cantidad) * (dias_restantes / dias_total)

    supabase.table("cuentas_matriz").insert({
        "id_empresa_matriz": id_empresa,
        "periodo_inicio": hoy,
        "periodo_fin": fin,
        "total_empresas": None,
        "monto_total": proporcional,
        "estado": "activa",
        "tipo": "ajuste",
        "recurso": recurso,
        "cantidad": cantidad,
        "costo_unitario": costo_unitario,
        "fecha_vencimiento": fin
    }).execute()

    return {"mensaje": "Ajuste creado correctamente", "monto_proporcional": proporcional}