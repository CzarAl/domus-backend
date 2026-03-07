from datetime import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import supabase
from dependencies import get_current_user

router = APIRouter(prefix="/caja", tags=["Caja"])


class AperturaCaja(BaseModel):
    monto_inicial: float = Field(ge=0)
    id_sucursal: str


class MovimientoCaja(BaseModel):
    id_sesion: str | None = None
    id_sucursal: str | None = None
    tipo_movimiento: str  # entrada | salida
    monto: float = Field(gt=0)
    concepto: str = Field(min_length=2, max_length=200)
    metodo_pago: str = "efectivo"  # efectivo | cheque | tarjeta_credito | tarjeta_debito | transferencia


class CierreCaja(BaseModel):
    monto_final: float = Field(ge=0)
    arqueo_real: float | None = Field(default=None, ge=0)


def _id_empresa(usuario: dict) -> str:
    id_empresa = usuario.get("id_raiz")
    if not id_empresa:
        raise HTTPException(status_code=400, detail="Usuario sin empresa")
    return id_empresa


def _sesion_abierta(id_empresa: str, id_sucursal: str):
    resp = (
        supabase.table("sesiones_caja")
        .select("*")
        .eq("id_empresa", id_empresa)
        .eq("id_sucursal", id_sucursal)
        .eq("abierta", True)
        .order("fecha_apertura", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def _totales_movimientos(id_sesion: str):
    movimientos = (
        supabase.table("movimientos_caja")
        .select("*")
        .eq("id_sesion", id_sesion)
        .order("fecha_creacion", desc=False)
        .execute()
    ).data or []

    total_entradas = sum(float(m.get("monto") or 0) for m in movimientos if (m.get("tipo_movimiento") or "") == "entrada")
    total_salidas = sum(float(m.get("monto") or 0) for m in movimientos if (m.get("tipo_movimiento") or "") == "salida")

    por_metodo: dict[str, float] = {}
    for m in movimientos:
        metodo = m.get("metodo_pago") or "sin_metodo"
        signo = 1 if (m.get("tipo_movimiento") or "") == "entrada" else -1
        por_metodo[metodo] = por_metodo.get(metodo, 0) + signo * float(m.get("monto") or 0)

    return {
        "movimientos": movimientos,
        "total_entradas": total_entradas,
        "total_salidas": total_salidas,
        "balance": total_entradas - total_salidas,
        "por_metodo": por_metodo,
    }


@router.get("/estado/{id_sucursal}")
def estado_caja(id_sucursal: str, usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)

    sesion = _sesion_abierta(id_empresa, id_sucursal)
    if not sesion:
        return {"abierta": False, "id_sucursal": id_sucursal}

    totales = _totales_movimientos(sesion["id"])
    monto_inicial = float(sesion.get("monto_inicial") or 0)
    arqueo_esperado = monto_inicial + totales["balance"]

    return {
        "abierta": True,
        "sesion": sesion,
        "totales": {
            **totales,
            "monto_inicial": monto_inicial,
            "arqueo_esperado": arqueo_esperado,
        },
    }


@router.get("/sesiones")
def listar_sesiones(id_sucursal: str | None = None, usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)

    q = (
        supabase.table("sesiones_caja")
        .select("*")
        .eq("id_empresa", id_empresa)
        .order("fecha_apertura", desc=True)
        .limit(100)
    )

    if id_sucursal:
        q = q.eq("id_sucursal", id_sucursal)

    return q.execute().data or []


@router.post("/abrir")
def abrir_caja(datos: AperturaCaja, usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)
    id_usuario = usuario.get("id_usuario")

    existente = _sesion_abierta(id_empresa, datos.id_sucursal)
    if existente:
        raise HTTPException(status_code=400, detail="Ya existe caja abierta en esta sucursal")

    payload = {
        "id": str(uuid.uuid4()),
        "id_empresa": id_empresa,
        "id_sucursal": datos.id_sucursal,
        "id_usuario_apertura": id_usuario,
        "monto_inicial": float(datos.monto_inicial),
        "fecha_apertura": datetime.utcnow().isoformat(),
        "abierta": True,
    }

    creada = supabase.table("sesiones_caja").insert(payload).execute()
    return {"mensaje": "Caja abierta", "data": creada.data[0] if creada.data else payload}


@router.post("/movimiento")
def registrar_movimiento(datos: MovimientoCaja, usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)
    id_usuario = usuario.get("id_usuario")

    tipo = (datos.tipo_movimiento or "").strip().lower()
    if tipo not in ("entrada", "salida"):
        raise HTTPException(status_code=400, detail="tipo_movimiento debe ser entrada o salida")

    metodo = (datos.metodo_pago or "efectivo").strip().lower()
    permitidos = {"efectivo", "cheque", "tarjeta_credito", "tarjeta_debito", "transferencia"}
    if metodo not in permitidos:
        raise HTTPException(status_code=400, detail=f"metodo_pago inválido. Usa: {', '.join(sorted(permitidos))}")

    id_sesion = datos.id_sesion
    id_sucursal = datos.id_sucursal

    if not id_sesion:
        if not id_sucursal:
            raise HTTPException(status_code=400, detail="Envía id_sesion o id_sucursal")
        sesion = _sesion_abierta(id_empresa, id_sucursal)
        if not sesion:
            raise HTTPException(status_code=400, detail="No hay caja abierta en esa sucursal")
        id_sesion = sesion["id"]
        id_sucursal = sesion.get("id_sucursal")
    else:
        sesion_resp = (
            supabase.table("sesiones_caja")
            .select("*")
            .eq("id", id_sesion)
            .eq("id_empresa", id_empresa)
            .limit(1)
            .execute()
        )
        if not sesion_resp.data:
            raise HTTPException(status_code=404, detail="Sesión de caja no encontrada")
        sesion = sesion_resp.data[0]
        if not sesion.get("abierta"):
            raise HTTPException(status_code=400, detail="La caja está cerrada")
        id_sucursal = sesion.get("id_sucursal")

    payload_full = {
        "id": str(uuid.uuid4()),
        "id_sesion": id_sesion,
        "id_empresa": id_empresa,
        "id_sucursal": id_sucursal,
        "id_usuario": id_usuario,
        "tipo_movimiento": tipo,
        "monto": float(datos.monto),
        "concepto": datos.concepto.strip(),
        "metodo_pago": metodo,
        "fecha_creacion": datetime.utcnow().isoformat(),
    }

    payload_min = {
        "id": payload_full["id"],
        "id_sesion": id_sesion,
        "id_empresa": id_empresa,
        "tipo_movimiento": tipo,
        "monto": float(datos.monto),
        "concepto": datos.concepto.strip(),
        "fecha_creacion": datetime.utcnow().isoformat(),
    }

    try:
        mov = supabase.table("movimientos_caja").insert(payload_full).execute()
    except Exception:
        mov = supabase.table("movimientos_caja").insert(payload_min).execute()

    return {"mensaje": "Movimiento registrado", "data": mov.data[0] if mov.data else payload_full}


@router.get("/movimientos/{id_sesion}")
def listar_movimientos(id_sesion: str, usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)

    sesion = (
        supabase.table("sesiones_caja")
        .select("id")
        .eq("id", id_sesion)
        .eq("id_empresa", id_empresa)
        .limit(1)
        .execute()
    )
    if not sesion.data:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    return _totales_movimientos(id_sesion)


@router.post("/cerrar/{id_sesion}")
def cerrar_caja(id_sesion: str, datos: CierreCaja, usuario=Depends(get_current_user)):
    id_empresa = _id_empresa(usuario)
    id_usuario = usuario.get("id_usuario")

    sesion_resp = (
        supabase.table("sesiones_caja")
        .select("*")
        .eq("id", id_sesion)
        .eq("id_empresa", id_empresa)
        .limit(1)
        .execute()
    )

    if not sesion_resp.data:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    sesion = sesion_resp.data[0]
    if not sesion.get("abierta"):
        raise HTTPException(status_code=400, detail="La caja ya está cerrada")

    totales = _totales_movimientos(id_sesion)
    monto_inicial = float(sesion.get("monto_inicial") or 0)
    arqueo_esperado = monto_inicial + totales["balance"]
    arqueo_real = float(datos.arqueo_real if datos.arqueo_real is not None else datos.monto_final)
    diferencia = arqueo_real - arqueo_esperado

    payload = {
        "monto_final": float(datos.monto_final),
        "fecha_cierre": datetime.utcnow().isoformat(),
        "abierta": False,
        "id_usuario_cierre": id_usuario,
        "total_entradas": float(totales["total_entradas"]),
        "total_salidas": float(totales["total_salidas"]),
        "arqueo_esperado": float(arqueo_esperado),
        "arqueo_real": float(arqueo_real),
        "diferencia_arqueo": float(diferencia),
    }

    payload_min = {
        "monto_final": float(datos.monto_final),
        "fecha_cierre": datetime.utcnow().isoformat(),
        "abierta": False,
    }

    try:
        resp = (
            supabase.table("sesiones_caja")
            .update(payload)
            .eq("id", id_sesion)
            .eq("id_empresa", id_empresa)
            .execute()
        )
    except Exception:
        resp = (
            supabase.table("sesiones_caja")
            .update(payload_min)
            .eq("id", id_sesion)
            .eq("id_empresa", id_empresa)
            .execute()
        )

    return {
        "mensaje": "Caja cerrada",
        "data": resp.data,
        "resumen": {
            "monto_inicial": monto_inicial,
            "total_entradas": totales["total_entradas"],
            "total_salidas": totales["total_salidas"],
            "arqueo_esperado": arqueo_esperado,
            "arqueo_real": arqueo_real,
            "diferencia_arqueo": diferencia,
            "por_metodo": totales["por_metodo"],
        },
    }
