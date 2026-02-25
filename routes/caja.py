from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from database import supabase
from dependencies import obtener_usuario_actual

router = APIRouter(prefix="/caja", tags=["Caja"])


# ==============================
# ABRIR CAJA
# ==============================

@router.post("/abrir")
def abrir_caja(monto_inicial: float, usuario=Depends(obtener_usuario_actual)):

    # Verificar si ya hay una caja abierta
    sesion_existente = supabase.table("sesiones_caja") \
        .select("*") \
        .eq("id_raiz", usuario["id_raiz"]) \
        .eq("abierta", True) \
        .execute()

    if sesion_existente.data:
        raise HTTPException(status_code=400, detail="Ya existe una caja abierta")

    respuesta = supabase.table("sesiones_caja").insert({
        "id_raiz": usuario["id_raiz"],
        "id_sucursal": None,
        "id_usuario": usuario["id_usuario"],
        "monto_inicial": monto_inicial,
        "fecha_apertura": datetime.utcnow().isoformat(),
        "abierta": True
    }).execute()

    return {
        "mensaje": "Caja abierta correctamente",
        "data": respuesta.data
    }


# ==============================
# CERRAR CAJA
# ==============================

@router.post("/cerrar/{id_sesion}")
def cerrar_caja(id_sesion: str, usuario=Depends(obtener_usuario_actual)):

    # Verificar que exista y esté abierta
    sesion = supabase.table("sesiones_caja") \
        .select("*") \
        .eq("id", id_sesion) \
        .eq("abierta", True) \
        .execute()

    if not sesion.data:
        raise HTTPException(status_code=400, detail="Sesión no encontrada o ya cerrada")

    # Calcular total movimientos
    movimientos = supabase.table("movimientos_caja") \
        .select("monto") \
        .eq("id_sesion", id_sesion) \
        .execute()

    total_movimientos = sum(m["monto"] for m in movimientos.data) if movimientos.data else 0

    supabase.table("sesiones_caja").update({
        "monto_cierre": total_movimientos,
        "fecha_cierre": datetime.utcnow().isoformat(),
        "abierta": False
    }).eq("id", id_sesion).execute()

    return {
        "mensaje": "Caja cerrada correctamente",
        "total_movimientos": total_movimientos
    }


# ==============================
# VER SESIÓN ABIERTA
# ==============================

@router.get("/abierta")
def obtener_caja_abierta(usuario=Depends(obtener_usuario_actual)):

    sesion = supabase.table("sesiones_caja") \
        .select("*") \
        .eq("id_raiz", usuario["id_raiz"]) \
        .eq("abierta", True) \
        .execute()

    return sesion.data