from datetime import date, datetime
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import supabase
from dependencies import get_current_user

router = APIRouter(prefix="/wallet", tags=["Wallet"])


class WalletCuentaCreate(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    tipo: Literal["efectivo", "credito", "debito"]
    saldo_inicial: float = 0
    limite_credito: float | None = None
    fecha_pago: date | None = None


class WalletCuentaUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=2, max_length=120)
    limite_credito: float | None = None
    fecha_pago: date | None = None
    activo: bool | None = None


class WalletMovimientoCreate(BaseModel):
    id_cuenta: str
    tipo_movimiento: Literal["cargo", "abono"]
    monto: float = Field(gt=0)
    nombre: str = Field(min_length=2, max_length=150)
    es_msi: bool = False
    meses_msi: int | None = None
    fecha_movimiento: date | None = None


class WalletBackupCreate(BaseModel):
    nombre_backup: str = Field(min_length=3, max_length=80)


class WalletRestoreBackupRequest(BaseModel):
    id_empresa_destino: str | None = None


def _slug_text(texto: str) -> str:
    limpio = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in texto)
    limpio = limpio.strip("_")
    return limpio or "backup"


def _parse_date(value):
    if value is None:
        return None
    if isinstance(value, date):
        return value
    s = str(value)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _wallet_recurso_habilitado(id_empresa: str) -> bool:
    hoy = date.today()

    autorizaciones = (
        supabase.table("autorizaciones_admin_empresa")
        .select("id,fecha_fin")
        .eq("id_empresa", id_empresa)
        .eq("tipo_recurso", "wallet")
        .eq("activo", True)
        .execute()
    ).data or []

    for a in autorizaciones:
        fecha_fin = _parse_date(a.get("fecha_fin"))
        if fecha_fin is None or fecha_fin >= hoy:
            return True

    recursos = (
        supabase.table("recursos_activos_empresa")
        .select("id,fecha_fin")
        .eq("id_empresa", id_empresa)
        .eq("tipo_recurso", "wallet")
        .execute()
    ).data or []

    for r in recursos:
        fecha_fin = _parse_date(r.get("fecha_fin"))
        if fecha_fin is None or fecha_fin >= hoy:
            return True

    return False


def _validar_acceso_wallet(usuario: dict) -> str:
    rol = usuario.get("rol")
    id_empresa = usuario.get("id_raiz")

    if rol not in ("admin_master", "usuario", "vendedor"):
        raise HTTPException(status_code=403, detail="Rol sin acceso a wallet")

    if not id_empresa:
        raise HTTPException(status_code=400, detail="Usuario sin empresa")

    if rol == "admin_master":
        return id_empresa

    if not _wallet_recurso_habilitado(id_empresa):
        raise HTTPException(status_code=403, detail="Wallet no habilitado para esta empresa")

    return id_empresa


def _crear_backup_wallet(id_empresa: str, nombre_backup: str, id_usuario: str | None):
    cuentas = (
        supabase.table("wallet_cuentas")
        .select("*")
        .eq("id_empresa", id_empresa)
        .execute()
    ).data or []

    ids_cuentas = [c.get("id") for c in cuentas if c.get("id")]

    movimientos = []
    if ids_cuentas:
        movimientos = (
            supabase.table("wallet_movimientos")
            .select("*")
            .in_("id_cuenta", ids_cuentas)
            .execute()
        ).data or []

    payload = {
        "tipo_respaldo": "wallet",
        "nombre_respaldo": nombre_backup,
        "id_empresa": id_empresa,
        "generado_por": id_usuario,
        "fecha": datetime.utcnow().isoformat(),
        "cuentas": cuentas,
        "movimientos": movimientos,
    }

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    nombre_archivo = f"wallet_{_slug_text(nombre_backup)}_{stamp}.json"

    respaldo = (
        supabase.table("empresas_backup")
        .insert(
            {
                "id_empresa_original": id_empresa,
                "nombre_empresa": f"WALLET::{nombre_backup}",
                "nombre_archivo": nombre_archivo,
                "datos": payload,
            }
        )
        .execute()
    )

    if not respaldo.data:
        raise HTTPException(status_code=500, detail="No se pudo guardar backup wallet")

    return respaldo.data[0]


@router.get("/disponible")
def wallet_disponible(usuario=Depends(get_current_user)):
    rol = usuario.get("rol")
    id_empresa = usuario.get("id_raiz")

    if rol == "admin_master":
        return {
            "enabled": True,
            "rol": rol,
            "id_empresa": id_empresa,
        }

    if rol not in ("usuario", "vendedor"):
        return {
            "enabled": False,
            "rol": rol,
            "id_empresa": id_empresa,
            "reason": "Rol sin acceso",
        }

    enabled = bool(id_empresa and _wallet_recurso_habilitado(id_empresa))

    return {
        "enabled": enabled,
        "rol": rol,
        "id_empresa": id_empresa,
        "reason": None if enabled else "Recurso wallet no habilitado",
    }


@router.get("/resumen")
def wallet_resumen(usuario=Depends(get_current_user)):
    id_empresa = _validar_acceso_wallet(usuario)

    cuentas = (
        supabase.table("wallet_cuentas")
        .select("*")
        .eq("id_empresa", id_empresa)
        .eq("activo", True)
        .order("fecha_creacion", desc=True)
        .execute()
    ).data or []

    ids_cuentas = [c.get("id") for c in cuentas if c.get("id")]

    movimientos = []
    if ids_cuentas:
        movimientos = (
            supabase.table("wallet_movimientos")
            .select("*")
            .in_("id_cuenta", ids_cuentas)
            .order("fecha_creacion", desc=True)
            .limit(100)
            .execute()
        ).data or []

    saldo_efectivo = sum((c.get("saldo_actual") or 0) for c in cuentas if c.get("tipo") == "efectivo")
    saldo_debito = sum((c.get("saldo_actual") or 0) for c in cuentas if c.get("tipo") == "debito")
    credito_usado = sum((c.get("saldo_actual") or 0) for c in cuentas if c.get("tipo") == "credito")
    credito_limite = sum((c.get("limite_credito") or 0) for c in cuentas if c.get("tipo") == "credito")

    return {
        "cuentas": cuentas,
        "movimientos": movimientos,
        "totales": {
            "efectivo": saldo_efectivo,
            "debito": saldo_debito,
            "credito_usado": credito_usado,
            "credito_limite": credito_limite,
            "credito_disponible": credito_limite - credito_usado,
            "patrimonio_estimado": saldo_efectivo + saldo_debito - credito_usado,
        },
    }


@router.post("/cuentas")
def crear_cuenta_wallet(datos: WalletCuentaCreate, usuario=Depends(get_current_user)):
    id_empresa = _validar_acceso_wallet(usuario)

    if datos.saldo_inicial < 0:
        raise HTTPException(status_code=400, detail="Saldo inicial no puede ser negativo")

    if datos.tipo == "credito":
        if datos.limite_credito is None or datos.limite_credito <= 0:
            raise HTTPException(status_code=400, detail="La tarjeta de crédito requiere límite")
        if datos.saldo_inicial > datos.limite_credito:
            raise HTTPException(status_code=400, detail="El saldo inicial excede el límite")
    else:
        if datos.limite_credito not in (None, 0):
            raise HTTPException(status_code=400, detail="Solo crédito maneja límite")

    payload = {
        "id": str(uuid.uuid4()),
        "id_empresa": id_empresa,
        "id_usuario_creador": usuario.get("id_usuario"),
        "nombre": datos.nombre.strip(),
        "tipo": datos.tipo,
        "saldo_actual": datos.saldo_inicial,
        "limite_credito": datos.limite_credito if datos.tipo == "credito" else None,
        "fecha_pago": datos.fecha_pago.isoformat() if datos.tipo == "credito" and datos.fecha_pago else None,
        "activo": True,
        "fecha_creacion": datetime.utcnow().isoformat(),
        "fecha_actualizacion": datetime.utcnow().isoformat(),
    }

    created = supabase.table("wallet_cuentas").insert(payload).execute()
    if not created.data:
        raise HTTPException(status_code=400, detail="No se pudo crear cuenta wallet")

    return {"mensaje": "Cuenta wallet creada", "data": created.data[0]}


@router.patch("/cuentas/{id_cuenta}")
def actualizar_cuenta_wallet(id_cuenta: str, datos: WalletCuentaUpdate, usuario=Depends(get_current_user)):
    id_empresa = _validar_acceso_wallet(usuario)

    cuenta_resp = (
        supabase.table("wallet_cuentas")
        .select("*")
        .eq("id", id_cuenta)
        .eq("id_empresa", id_empresa)
        .limit(1)
        .execute()
    )

    if not cuenta_resp.data:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")

    cuenta = cuenta_resp.data[0]
    update_data = {"fecha_actualizacion": datetime.utcnow().isoformat()}

    if datos.nombre is not None:
        update_data["nombre"] = datos.nombre.strip()

    if datos.activo is not None:
        update_data["activo"] = datos.activo

    if cuenta.get("tipo") == "credito":
        if datos.limite_credito is not None:
            if datos.limite_credito < (cuenta.get("saldo_actual") or 0):
                raise HTTPException(status_code=400, detail="Límite menor al saldo actual")
            update_data["limite_credito"] = datos.limite_credito

        if datos.fecha_pago is not None:
            update_data["fecha_pago"] = datos.fecha_pago.isoformat()
    else:
        if datos.limite_credito not in (None, 0):
            raise HTTPException(status_code=400, detail="Solo crédito permite límite")
        if datos.fecha_pago is not None:
            raise HTTPException(status_code=400, detail="Solo crédito permite fecha de pago")

    updated = (
        supabase.table("wallet_cuentas")
        .update(update_data)
        .eq("id", id_cuenta)
        .eq("id_empresa", id_empresa)
        .execute()
    )

    return {"mensaje": "Cuenta actualizada", "data": updated.data}


@router.post("/movimientos")
def crear_movimiento_wallet(datos: WalletMovimientoCreate, usuario=Depends(get_current_user)):
    id_empresa = _validar_acceso_wallet(usuario)

    cuenta_resp = (
        supabase.table("wallet_cuentas")
        .select("*")
        .eq("id", datos.id_cuenta)
        .eq("id_empresa", id_empresa)
        .eq("activo", True)
        .limit(1)
        .execute()
    )

    if not cuenta_resp.data:
        raise HTTPException(status_code=404, detail="Cuenta wallet no encontrada")

    cuenta = cuenta_resp.data[0]
    saldo_actual = float(cuenta.get("saldo_actual") or 0)
    monto = float(datos.monto)
    tipo_cuenta = cuenta.get("tipo")

    if tipo_cuenta != "credito" and datos.es_msi:
        raise HTTPException(status_code=400, detail="MSI solo aplica para tarjeta de crédito")

    if datos.es_msi and (datos.meses_msi is None or datos.meses_msi < 2):
        raise HTTPException(status_code=400, detail="MSI requiere meses >= 2")

    if tipo_cuenta in ("efectivo", "debito"):
        if datos.tipo_movimiento == "cargo":
            nuevo_saldo = saldo_actual - monto
            if nuevo_saldo < 0:
                raise HTTPException(status_code=400, detail="Saldo insuficiente")
        else:
            nuevo_saldo = saldo_actual + monto
    elif tipo_cuenta == "credito":
        limite = float(cuenta.get("limite_credito") or 0)
        if limite <= 0:
            raise HTTPException(status_code=400, detail="Tarjeta sin límite configurado")

        if datos.tipo_movimiento == "cargo":
            nuevo_saldo = saldo_actual + monto
            if nuevo_saldo > limite:
                raise HTTPException(status_code=400, detail="El cargo excede el límite de crédito")
        else:
            nuevo_saldo = max(saldo_actual - monto, 0)
    else:
        raise HTTPException(status_code=400, detail="Tipo de cuenta inválido")

    mov_payload = {
        "id": str(uuid.uuid4()),
        "id_cuenta": cuenta["id"],
        "id_empresa": id_empresa,
        "id_usuario": usuario.get("id_usuario"),
        "tipo_movimiento": datos.tipo_movimiento,
        "monto": monto,
        "nombre": datos.nombre.strip(),
        "es_msi": datos.es_msi if tipo_cuenta == "credito" else False,
        "meses_msi": datos.meses_msi if tipo_cuenta == "credito" else None,
        "fecha_movimiento": (datos.fecha_movimiento or date.today()).isoformat(),
        "fecha_creacion": datetime.utcnow().isoformat(),
    }

    created = supabase.table("wallet_movimientos").insert(mov_payload).execute()
    if not created.data:
        raise HTTPException(status_code=400, detail="No se pudo registrar movimiento")

    supabase.table("wallet_cuentas").update(
        {
            "saldo_actual": nuevo_saldo,
            "fecha_actualizacion": datetime.utcnow().isoformat(),
        }
    ).eq("id", cuenta["id"]).eq("id_empresa", id_empresa).execute()

    return {
        "mensaje": "Movimiento registrado",
        "saldo_anterior": saldo_actual,
        "saldo_nuevo": nuevo_saldo,
        "data": created.data[0],
    }


@router.get("/cuentas/{id_cuenta}/movimientos")
def listar_movimientos_wallet(id_cuenta: str, usuario=Depends(get_current_user)):
    id_empresa = _validar_acceso_wallet(usuario)

    cuenta = (
        supabase.table("wallet_cuentas")
        .select("id")
        .eq("id", id_cuenta)
        .eq("id_empresa", id_empresa)
        .limit(1)
        .execute()
    )

    if not cuenta.data:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")

    movimientos = (
        supabase.table("wallet_movimientos")
        .select("*")
        .eq("id_cuenta", id_cuenta)
        .order("fecha_creacion", desc=True)
        .execute()
    )

    return movimientos.data or []


@router.post("/backup")
def crear_backup_wallet(datos: WalletBackupCreate, usuario=Depends(get_current_user)):
    id_empresa = _validar_acceso_wallet(usuario)

    respaldo = _crear_backup_wallet(id_empresa, datos.nombre_backup, usuario.get("id_usuario"))

    return {
        "mensaje": "Backup wallet generado",
        "backup_id": respaldo.get("id"),
        "nombre_archivo": respaldo.get("nombre_archivo"),
    }


@router.post("/backup/{backup_id}/restaurar")
def restaurar_backup_wallet(
    backup_id: str,
    datos: WalletRestoreBackupRequest,
    usuario=Depends(get_current_user),
):
    rol = usuario.get("rol")
    id_empresa_usuario = usuario.get("id_raiz")

    if rol not in ("admin_master", "usuario"):
        raise HTTPException(status_code=403, detail="No autorizado para restaurar wallet")

    backup_resp = (
        supabase.table("empresas_backup")
        .select("*")
        .eq("id", backup_id)
        .limit(1)
        .execute()
    )

    if not backup_resp.data:
        raise HTTPException(status_code=404, detail="Backup no encontrado")

    backup = backup_resp.data[0]
    payload = backup.get("datos") or {}

    if payload.get("tipo_respaldo") != "wallet":
        raise HTTPException(status_code=400, detail="El backup no corresponde a wallet")

    empresa_backup = payload.get("id_empresa")

    if rol == "admin_master":
        id_empresa_destino = datos.id_empresa_destino or empresa_backup or id_empresa_usuario
    else:
        id_empresa_destino = id_empresa_usuario

    if not id_empresa_destino:
        raise HTTPException(status_code=400, detail="No se pudo determinar empresa destino")

    cuentas = payload.get("cuentas") or []
    movimientos = payload.get("movimientos") or []

    for cuenta in cuentas:
        cuenta_payload = dict(cuenta)
        cuenta_payload["id_empresa"] = id_empresa_destino

        if not cuenta_payload.get("id"):
            cuenta_payload["id"] = str(uuid.uuid4())

        existe = (
            supabase.table("wallet_cuentas")
            .select("id")
            .eq("id", cuenta_payload["id"])
            .limit(1)
            .execute()
        )

        if existe.data:
            supabase.table("wallet_cuentas").update(cuenta_payload).eq("id", cuenta_payload["id"]).execute()
        else:
            supabase.table("wallet_cuentas").insert(cuenta_payload).execute()

    for mov in movimientos:
        mov_payload = dict(mov)
        mov_payload["id_empresa"] = id_empresa_destino

        if not mov_payload.get("id"):
            mov_payload["id"] = str(uuid.uuid4())

        existe_mov = (
            supabase.table("wallet_movimientos")
            .select("id")
            .eq("id", mov_payload["id"])
            .limit(1)
            .execute()
        )

        if existe_mov.data:
            supabase.table("wallet_movimientos").update(mov_payload).eq("id", mov_payload["id"]).execute()
        else:
            supabase.table("wallet_movimientos").insert(mov_payload).execute()

    return {
        "mensaje": "Wallet restaurada desde backup",
        "empresa_destino": id_empresa_destino,
    }

@router.get("/backups")
def listar_backups_wallet(usuario=Depends(get_current_user)):
    rol = usuario.get("rol")
    id_empresa = usuario.get("id_raiz")

    if rol not in ("admin_master", "usuario"):
        raise HTTPException(status_code=403, detail="No autorizado")

    if rol == "admin_master":
        backups_raw = (
            supabase.table("empresas_backup")
            .select("id,id_empresa_original,nombre_archivo,nombre_empresa,fecha_eliminacion,datos")
            .order("fecha_eliminacion", desc=True)
            .execute()
        ).data or []
    else:
        backups_raw = (
            supabase.table("empresas_backup")
            .select("id,id_empresa_original,nombre_archivo,nombre_empresa,fecha_eliminacion,datos")
            .eq("id_empresa_original", id_empresa)
            .order("fecha_eliminacion", desc=True)
            .execute()
        ).data or []

    salida = []
    for b in backups_raw:
        datos = b.get("datos") or {}
        if datos.get("tipo_respaldo") != "wallet":
            continue

        salida.append(
            {
                "id": b.get("id"),
                "id_empresa": b.get("id_empresa_original"),
                "nombre_backup": datos.get("nombre_respaldo") or b.get("nombre_empresa"),
                "nombre_archivo": b.get("nombre_archivo"),
                "fecha": b.get("fecha_eliminacion") or datos.get("fecha"),
                "cuentas": len(datos.get("cuentas") or []),
                "movimientos": len(datos.get("movimientos") or []),
            }
        )

    return salida
