import re
from datetime import date, datetime
from typing import Optional

import bcrypt
import requests
from fastapi import APIRouter, Depends, HTTPException

from database import SUPABASE_KEY, SUPABASE_URL, supabase
from dependencies import get_current_user

router = APIRouter(prefix="/mr", tags=["MR Abogados"])

JUZGADOS_MR = [
    "1o Civil Tradicional",
    "1o Mercantil",
    "2o Mercantil",
    "3o Mercantil",
    "1o Mercantil por Audiencias",
    "4o Civil por Audiencias",
    "5o Civil por Audiencias",
    "6o Civil por Audiencias",
    "4o Mercantil y Esp. Ext. Dom.",
    "9o Civil por Audiencias",
    "10o Civil por Audiencias",
    "11o Civil por Audiencias",
    "12o Civil por Audiencias",
    "13o Civil por Audiencias",
    "1o Familiar por Audiencias",
    "2o Familiar por Audiencias",
    "3o Familiar por Audiencias",
    "4o Familiar por Audiencias",
    "5o Familiar por Audiencias",
    "6o Familiar por Audiencias",
    "7o Familiar por Audiencias",
    "8o Familiar por Audiencias",
    "9o Familiar por Audiencias",
    "10o Familiar por Audiencias",
    "Juzgado Familiar Tradicional",
    "Centro Auxiliar, Juzgado Familiar",
    "Juzgado Familiar Coadyuvante",
]
JUZGADOS_MR_MAP = {}
for item in JUZGADOS_MR:
    key = re.sub(r"\s+", " ", item.strip())
    key = re.sub(r"(\d+)\s*[oº°]", r"\1o", key, flags=re.IGNORECASE)
    key = key.replace(" ,", ",").lower()
    JUZGADOS_MR_MAP[key] = item

PENDIENTE_ESTADOS = {"pendiente", "completado", "reprogramado", "archivado"}
ACTIVIDAD_TIPOS = {"general", "emplazar", "diligencia", "audiencia", "otro"}
ACTIVIDAD_TIPOS_CON_FECHA = {"emplazar", "diligencia", "audiencia"}
EXPEDIENTE_EDITABLES_LIBRES = {"estado", "seguimiento"}
EXPEDIENTE_EDITABLES_SENSIBLES = {"expediente", "juzgado", "actor_demandado", "actividad", "fecha_vencimiento"}
SUPABASE_WRITE_TIMEOUT_SECONDS = 12
MR_PENDIENTES_URL = f"{SUPABASE_URL.rstrip('/')}/rest/v1/mr_pendientes"
MR_ACTIVIDADES_URL = f"{SUPABASE_URL.rstrip('/')}/rest/v1/mr_actividades"
MR_JUZGADOS_URL = f"{SUPABASE_URL.rstrip('/')}/rest/v1/mr_juzgados_catalogo"
MR_REST_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def _mr_rest_error(response: requests.Response) -> HTTPException:
    try:
        payload = response.json()
    except Exception:
        payload = None

    detail = None
    if isinstance(payload, dict):
        detail = payload.get("message") or payload.get("details") or payload.get("hint") or payload.get("error")

    if not detail:
        detail = response.text or "Error en Supabase"

    return HTTPException(response.status_code, detail)


def _mr_rest_write(table_url: str, method: str, payload: Optional[dict] = None, row_id: str | None = None):
    params = {"select": "*"}
    if row_id:
        params["id"] = f"eq.{row_id}"

    try:
        response = requests.request(
            method,
            table_url,
            headers=MR_REST_HEADERS,
            json=payload,
            params=params,
            timeout=SUPABASE_WRITE_TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        raise HTTPException(504, "La operacion tardo demasiado al guardar en base de datos")
    except requests.RequestException as exc:
        raise HTTPException(502, f"No se pudo conectar con Supabase: {exc}")

    if response.status_code >= 400:
        raise _mr_rest_error(response)

    try:
        return response.json()
    except Exception as exc:
        raise HTTPException(502, f"Respuesta invalida de Supabase: {exc}")


def _clean_optional_fields(payload: dict, fields: list[str]):
    for field in fields:
        if field in payload and payload[field] == "":
            payload[field] = None
    return payload


def _to_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "si", "yes", "on"}


def _normalize_juzgado_key(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip())
    cleaned = re.sub(r"(\d+)\s*[oº°]", r"\1o", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace(" ,", ",")
    return cleaned.lower()


def _listar_juzgados_personalizados() -> list[dict]:
    try:
        data = supabase.table("mr_juzgados_catalogo").select("*").order("nombre_juzgado").execute().data
    except Exception:
        data = []
    return data


def _buscar_juzgado_personalizado(value: str) -> Optional[dict]:
    normalizado = _normalize_juzgado_key(value)
    try:
        data = (
            supabase.table("mr_juzgados_catalogo")
            .select("*")
            .eq("normalizado", normalizado)
            .limit(1)
            .execute()
            .data
        )
    except Exception:
        data = []
    return data[0] if data else None


def _build_custom_juzgado_label(ciudad: str, distrito_judicial: str, nombre_juzgado: str) -> str:
    ciudad_clean = re.sub(r"\s+", " ", ciudad.strip())
    distrito_clean = re.sub(r"\s+", " ", distrito_judicial.strip())
    nombre_clean = re.sub(r"\s+", " ", nombre_juzgado.strip())
    return f"{nombre_clean} - Distrito Judicial {distrito_clean} - {ciudad_clean}"


def _normalizar_juzgado(value: Optional[str], required: bool = False) -> Optional[str]:
    if value is None:
        if required:
            raise HTTPException(400, "Falta juzgado")
        return None

    cleaned = re.sub(r"\s+", " ", str(value).strip())
    if not cleaned:
        if required:
            raise HTTPException(400, "Falta juzgado")
        return None

    normalized = JUZGADOS_MR_MAP.get(_normalize_juzgado_key(cleaned))
    if normalized:
        return normalized

    juzgado_personalizado = _buscar_juzgado_personalizado(cleaned)
    if juzgado_personalizado:
        return juzgado_personalizado.get("label") or cleaned

    raise HTTPException(400, "Juzgado invalido")


def _normalizar_registros_juzgado(rows: list[dict]) -> list[dict]:
    personalizados = {
        item.get("normalizado"): item.get("label")
        for item in _listar_juzgados_personalizados()
        if item.get("normalizado") and item.get("label")
    }

    for row in rows:
        if not isinstance(row, dict) or not row.get("juzgado"):
            continue

        cleaned = re.sub(r"\s+", " ", str(row.get("juzgado")).strip())
        key = _normalize_juzgado_key(cleaned)
        row["juzgado"] = JUZGADOS_MR_MAP.get(key) or personalizados.get(key) or cleaned

    return rows


def _verificar_password_confirmacion(usuario: dict, password_confirmacion: Optional[str]):
    if not password_confirmacion:
        raise HTTPException(400, "Falta contraseña de confirmacion")

    usuario_id = usuario.get("id_usuario") or usuario.get("id") or usuario.get("sub")
    if not usuario_id:
        raise HTTPException(403, "Usuario invalido")

    respuesta = supabase.table("usuarios").select("password_hash").eq("id", usuario_id).limit(1).execute()
    if not respuesta.data:
        raise HTTPException(404, "Usuario no encontrado")

    password_hash = respuesta.data[0].get("password_hash")
    if not password_hash or not bcrypt.checkpw(password_confirmacion.encode("utf-8"), password_hash.encode("utf-8")):
        raise HTTPException(401, "Contraseña de confirmacion incorrecta")


def _normalizar_pendiente_payload(payload: dict, parcial: bool = False):
    data = dict(payload or {})
    data = _clean_optional_fields(
        data,
        [
            "juzgado",
            "expediente",
            "fecha_pendiente",
            "actividad_relacionada",
            "fecha_reprogramada",
            "resultado",
            "responsable",
        ],
    )

    if not parcial and not data.get("pendiente"):
        raise HTTPException(400, "Falta pendiente")

    if not parcial and not data.get("expediente"):
        raise HTTPException(400, "Falta expediente")

    if "juzgado" in data or not parcial:
        data["juzgado"] = _normalizar_juzgado(data.get("juzgado"), required=not parcial)

    estado = data.get("estado")
    if estado is None and not parcial:
        estado = "pendiente"

    if estado is not None:
        estado_normalizado = str(estado).strip().lower()
        if estado_normalizado not in PENDIENTE_ESTADOS:
            raise HTTPException(400, "Estado de pendiente invalido")
        data["estado"] = estado_normalizado
        data["realizado"] = estado_normalizado == "completado"
        data["resuelto_en"] = datetime.utcnow().isoformat() if estado_normalizado == "completado" else None

        if estado_normalizado != "reprogramado":
            data["fecha_reprogramada"] = None

    if not parcial and not data.get("fecha_creacion"):
        data["fecha_creacion"] = datetime.utcnow().isoformat()

    return data


def _normalizar_actividad_payload(payload: dict, parcial: bool = False):
    data = dict(payload or {})
    data = _clean_optional_fields(
        data,
        [
            "expediente_id",
            "expediente",
            "juzgado",
            "descripcion",
            "fecha_actividad",
            "observaciones",
            "resultado",
        ],
    )

    if not parcial and not data.get("expediente"):
        raise HTTPException(400, "Falta expediente")

    if "juzgado" in data or not parcial:
        data["juzgado"] = _normalizar_juzgado(data.get("juzgado"), required=not parcial)

    if not parcial and not data.get("descripcion"):
        raise HTTPException(400, "Falta descripcion")

    tipo = data.get("tipo")
    if tipo is None and not parcial:
        tipo = "general"

    if tipo is not None:
        tipo_normalizado = str(tipo).strip().lower()
        if tipo_normalizado not in ACTIVIDAD_TIPOS:
            raise HTTPException(400, "Tipo de actividad invalido")
        data["tipo"] = tipo_normalizado

        if tipo_normalizado in ACTIVIDAD_TIPOS_CON_FECHA and not data.get("fecha_actividad") and not parcial:
            raise HTTPException(400, "Falta fecha de actividad")

    if "cumplido" in data or not parcial:
        cumplido = _to_bool(data.get("cumplido"), default=False)
        data["cumplido"] = cumplido
        data["fecha_cumplimiento"] = datetime.utcnow().isoformat() if cumplido else None

    now = datetime.utcnow().isoformat()
    if not parcial and not data.get("created_at"):
        data["created_at"] = now
    data["updated_at"] = now

    return data


def _normalizar_expediente_payload(payload: dict, parcial: bool = False):
    data = dict(payload or {})
    data = _clean_optional_fields(
        data,
        ["expediente", "juzgado", "actor_demandado", "actividad", "fecha_vencimiento", "seguimiento", "estado"],
    )

    if not parcial and not data.get("expediente"):
        raise HTTPException(400, "Falta expediente")

    if "juzgado" in data or not parcial:
        data["juzgado"] = _normalizar_juzgado(data.get("juzgado"), required=not parcial)

    if data.get("fecha_vencimiento"):
        try:
            fv = date.fromisoformat(data["fecha_vencimiento"])
            dias = (fv - date.today()).days
            data["dias_disponibles"] = dias
            if not data.get("estado"):
                if dias < 0:
                    data["estado"] = "Vencido"
                elif dias <= 3:
                    data["estado"] = "Proximo"
                else:
                    data["estado"] = "Vigente"
        except Exception:
            pass

    return data


@router.get("/juzgados")
def listar_juzgados(usuario: dict = Depends(get_current_user)):
    personalizados = _listar_juzgados_personalizados()
    labels = list(JUZGADOS_MR)
    for row in personalizados:
        label = row.get("label")
        if label and label not in labels:
            labels.append(label)
    return {"juzgados": labels, "personalizados": personalizados}


@router.post("/juzgados")
def crear_juzgado(payload: dict, usuario: dict = Depends(get_current_user)):
    ciudad = re.sub(r"\s+", " ", str((payload or {}).get("ciudad") or "").strip())
    distrito_judicial = re.sub(r"\s+", " ", str((payload or {}).get("distrito_judicial") or "").strip())
    nombre_juzgado = re.sub(r"\s+", " ", str((payload or {}).get("nombre_juzgado") or "").strip())

    if not ciudad:
        raise HTTPException(400, "Falta ciudad")
    if not distrito_judicial:
        raise HTTPException(400, "Falta distrito judicial")
    if not nombre_juzgado:
        raise HTTPException(400, "Falta nombre del juzgado")

    label = _build_custom_juzgado_label(ciudad, distrito_judicial, nombre_juzgado)
    normalizado = _normalize_juzgado_key(label)
    existente = _buscar_juzgado_personalizado(label)
    if existente:
        return {"juzgado": existente}

    data = _mr_rest_write(
        MR_JUZGADOS_URL,
        "POST",
        {
            "ciudad": ciudad,
            "distrito_judicial": distrito_judicial,
            "nombre_juzgado": nombre_juzgado,
            "label": label,
            "normalizado": normalizado,
        },
    )
    return {"juzgado": data[0] if data else None}


@router.get("/expedientes")
def listar_expedientes(
    q: Optional[str] = None,
    actor: Optional[str] = None,
    actividad: Optional[str] = None,
    estado: Optional[str] = None,
    usuario: dict = Depends(get_current_user),
):
    query = supabase.table("mr_expedientes").select("*")
    if q:
        query = query.ilike("expediente", f"%{q}%")
    if actor:
        query = query.ilike("actor_demandado", f"%{actor}%")
    if actividad:
        query = query.ilike("actividad", f"%{actividad}%")
    if estado:
        query = query.ilike("estado", f"%{estado}%")
    data = query.order("fecha_vencimiento", desc=False).execute().data
    return {"expedientes": _normalizar_registros_juzgado(data)}


@router.post("/expedientes")
def crear_expediente(payload: dict, usuario: dict = Depends(get_current_user)):
    payload = _normalizar_expediente_payload(payload, parcial=False)
    res = supabase.table("mr_expedientes").insert(payload).execute()
    data = _normalizar_registros_juzgado(res.data or [])
    return {"expediente": data[0] if data else None}


@router.patch("/expedientes/{expediente_id}")
def actualizar_expediente(expediente_id: str, payload: dict, usuario: dict = Depends(get_current_user)):
    data = dict(payload or {})
    password_confirmacion = data.pop("password_confirmacion", None)
    confirmacion_cambios = _to_bool(data.pop("confirmacion_cambios", False), default=False)
    data = {k: v for k, v in data.items() if k in (EXPEDIENTE_EDITABLES_LIBRES | EXPEDIENTE_EDITABLES_SENSIBLES)}
    data = _normalizar_expediente_payload(data, parcial=True)

    if not data:
        raise HTTPException(400, "Nada por actualizar")

    requiere_password = any(campo in data for campo in EXPEDIENTE_EDITABLES_SENSIBLES)
    if requiere_password:
        if not confirmacion_cambios:
            raise HTTPException(400, "Confirma los cambios sensibles antes de guardar")
        _verificar_password_confirmacion(usuario, password_confirmacion)

    res = supabase.table("mr_expedientes").update(data).eq("id", expediente_id).execute()
    registros = _normalizar_registros_juzgado(res.data or [])
    return {"expediente": registros[0] if registros else None}


@router.get("/alertas")
def alertas(usuario: dict = Depends(get_current_user)):
    try:
        data = supabase.table("mr_alertas_proximas").select("*").order("fecha_vencimiento").execute().data
    except Exception:
        data = (
            supabase.table("mr_expedientes")
            .select("*")
            .in_("estado", ["Vencido", "Proximo"])
            .order("fecha_vencimiento")
            .execute()
            .data
        )
    return {"alertas": _normalizar_registros_juzgado(data)}


@router.get("/pendientes")
def listar_pendientes(expediente: Optional[str] = None, usuario: dict = Depends(get_current_user)):
    query = supabase.table("mr_pendientes").select("*")
    if expediente:
        query = query.ilike("expediente", f"%{expediente}%")
    data = query.order("fecha_creacion", desc=True).execute().data
    return {"pendientes": _normalizar_registros_juzgado(data)}


@router.post("/pendientes")
def crear_pendiente(payload: dict, usuario: dict = Depends(get_current_user)):
    payload = _normalizar_pendiente_payload(payload, parcial=False)
    data = _mr_rest_write(MR_PENDIENTES_URL, "POST", payload)
    return {"pendiente": data[0] if data else None}


@router.patch("/pendientes/{pendiente_id}")
def actualizar_pendiente(pendiente_id: str, payload: dict, usuario: dict = Depends(get_current_user)):
    payload = _normalizar_pendiente_payload(payload, parcial=True)
    if not payload:
        raise HTTPException(400, "Nada por actualizar")
    data = _mr_rest_write(MR_PENDIENTES_URL, "PATCH", payload, pendiente_id)
    return {"pendiente": data[0] if data else None}


@router.delete("/pendientes/{pendiente_id}")
def eliminar_pendiente(pendiente_id: str, usuario: dict = Depends(get_current_user)):
    data = _mr_rest_write(MR_PENDIENTES_URL, "DELETE", None, pendiente_id)
    return {"pendiente": data[0] if data else None}


@router.get("/actividades")
def listar_actividades(
    expediente: Optional[str] = None,
    juzgado: Optional[str] = None,
    tipo: Optional[str] = None,
    usuario: dict = Depends(get_current_user),
):
    try:
        query = supabase.table("mr_actividades").select("*")
        if expediente:
            query = query.ilike("expediente", f"%{expediente}%")
        if juzgado:
            query = query.ilike("juzgado", f"%{juzgado}%")
        if tipo:
            query = query.ilike("tipo", f"%{tipo}%")
        data = query.order("fecha_actividad", desc=False).order("created_at", desc=True).execute().data
    except Exception:
        data = []
    return {"actividades": _normalizar_registros_juzgado(data)}


@router.post("/actividades")
def crear_actividad(payload: dict, usuario: dict = Depends(get_current_user)):
    payload = _normalizar_actividad_payload(payload, parcial=False)
    data = _mr_rest_write(MR_ACTIVIDADES_URL, "POST", payload)
    return {"actividad": data[0] if data else None}


@router.patch("/actividades/{actividad_id}")
def actualizar_actividad(actividad_id: str, payload: dict, usuario: dict = Depends(get_current_user)):
    payload = _normalizar_actividad_payload(payload, parcial=True)
    if not payload:
        raise HTTPException(400, "Nada por actualizar")
    data = _mr_rest_write(MR_ACTIVIDADES_URL, "PATCH", payload, actividad_id)
    return {"actividad": data[0] if data else None}


@router.delete("/actividades/{actividad_id}")
def eliminar_actividad(actividad_id: str, usuario: dict = Depends(get_current_user)):
    data = _mr_rest_write(MR_ACTIVIDADES_URL, "DELETE", None, actividad_id)
    return {"actividad": data[0] if data else None}


@router.get("/pagos")
def listar_pagos(expediente: Optional[str] = None, juzgado: Optional[str] = None, usuario: dict = Depends(get_current_user)):
    query = supabase.table("pagos_expedientes").select("*")
    if expediente:
        query = query.ilike("expediente", f"%{expediente}%")
    if juzgado:
        query = query.ilike("juzgado", f"%{juzgado}%")
    data = query.order("fecha_oficio", desc=True).execute().data
    return {"pagos": _normalizar_registros_juzgado(data)}


@router.post("/pagos")
def crear_pago(payload: dict, usuario: dict = Depends(get_current_user)):
    for campo in ["expediente", "juzgado", "monto"]:
        if not payload.get(campo):
            raise HTTPException(400, f"Falta {campo}")
    payload["juzgado"] = _normalizar_juzgado(payload.get("juzgado"), required=True)
    res = supabase.table("pagos_expedientes").insert(payload).execute()
    data = _normalizar_registros_juzgado(res.data or [])
    return {"pago": data[0] if data else None}
