from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from dependencies import get_current_user
from database import supabase
from datetime import datetime
from typing import Literal
import bcrypt
import uuid

router = APIRouter(prefix="/admin", tags=["Admin SaaS"])

ESTADO_VENCIDA = "vencida"
ESTADO_PAGADA = "pagada"
ESTADOS_VENCIDA_ALIAS = [ESTADO_VENCIDA, "vencido"]
ESTADOS_PAGADA_ALIAS = [ESTADO_PAGADA, "pagado"]


def validar_admin(usuario):
    if usuario.get("nivel_global") != "admin_master":
        raise HTTPException(status_code=403, detail="Acceso solo para admin_master")
    return usuario


def _slug_text(texto: str) -> str:
    limpio = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in texto)
    limpio = limpio.strip("_")
    return limpio or "backup"


PORTAL_PERMISSION_DEFAULTS = {
    "domus": {
        "enabled": True,
        "features": {
            "dashboard": True,
            "ventas": True,
            "caja": True,
            "sucursales": True,
            "vendedores": True,
            "productos": True,
            "clientes": True,
            "wallet": True,
        },
    },
    "mr": {
        "enabled": True,
        "features": {
            "expedientes": True,
            "pendientes": True,
            "actividades": True,
            "alertas": True,
            "pagos": True,
        },
    },
}


def _bool_value(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "si", "yes", "on"}


def _portal_permissions_default_for_role(nivel_global: str):
    permisos = {
        modulo: {
            "enabled": config["enabled"],
            "features": dict(config["features"]),
        }
        for modulo, config in PORTAL_PERMISSION_DEFAULTS.items()
    }

    if nivel_global == "admin_master":
        return permisos

    return permisos


def _normalizar_permisos_portal(permisos_portal, nivel_global: str):
    base = _portal_permissions_default_for_role(nivel_global)

    if not isinstance(permisos_portal, dict):
        return base

    for modulo, config in base.items():
        incoming = permisos_portal.get(modulo)
        if not isinstance(incoming, dict):
            continue

        enabled = _bool_value(incoming.get("enabled"), config["enabled"])
        config["enabled"] = enabled

        incoming_features = incoming.get("features") if isinstance(incoming.get("features"), dict) else {}
        for feature, default_value in config["features"].items():
            config["features"][feature] = enabled and _bool_value(incoming_features.get(feature), default_value)

        if not enabled:
            config["features"] = {feature: False for feature in config["features"]}

    return base


class UsuarioCrearAdmin(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=6)
    email: EmailStr | None = None
    nivel_global: Literal["admin_master", "usuario", "vendedor"] = "usuario"
    id_empresa: str | None = None
    rol_empresa: str | None = None
    id_sucursal: str | None = None
    nombre_vendedor: str | None = None
    permisos_portal: dict | None = None


class UsuarioActualizarAdmin(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    username: str = Field(min_length=3, max_length=40)
    email: EmailStr | None = None
    nivel_global: Literal["admin_master", "usuario", "vendedor"] | None = None
    id_empresa: str | None = None
    rol_empresa: str | None = None
    permisos_portal: dict | None = None


class UsuarioEstadoUpdate(BaseModel):
    activo: bool


class UsuarioEliminarAdmin(BaseModel):
    password_confirmacion: str
    nombre_backup: str = Field(min_length=3, max_length=80)


@router.get("/empresas")
def listar_empresas(usuario=Depends(get_current_user)):
    validar_admin(usuario)

    empresas = (
        supabase.table("empresas")
        .select("*")
        .order("fecha_creacion", desc=True)
        .execute()
    )

    return empresas.data


@router.get("/usuarios")
def listar_usuarios(usuario=Depends(get_current_user)):
    validar_admin(usuario)

    usuarios_resp = (
        supabase.table("usuarios")
        .select("id,nombre,username,email,nivel_global,activo,fecha_creacion,id_raiz,permisos_portal")
        .order("fecha_creacion", desc=True)
        .execute()
    )

    usuarios = usuarios_resp.data or []
    ids_usuarios = [u["id"] for u in usuarios]

    asignaciones = []
    if ids_usuarios:
        asignaciones_resp = (
            supabase.table("usuarios_empresas")
            .select("id,id_usuario,id_empresa,rol,activo")
            .in_("id_usuario", ids_usuarios)
            .execute()
        )
        asignaciones = asignaciones_resp.data or []

    ids_empresas = list({a["id_empresa"] for a in asignaciones if a.get("id_empresa")})
    empresas_map: dict[str, str] = {}

    if ids_empresas:
        empresas_resp = (
            supabase.table("empresas")
            .select("id,nombre")
            .in_("id", ids_empresas)
            .execute()
        )
        for empresa in (empresas_resp.data or []):
            empresas_map[empresa["id"]] = empresa.get("nombre") or "Sin nombre"

    asignaciones_por_usuario: dict[str, list] = {}
    for asig in asignaciones:
        user_id = asig.get("id_usuario")
        if not user_id:
            continue

        asig_data = {
            "id": asig.get("id"),
            "id_empresa": asig.get("id_empresa"),
            "empresa_nombre": empresas_map.get(asig.get("id_empresa"), "-"),
            "rol": asig.get("rol"),
            "activo": asig.get("activo"),
        }

        if user_id not in asignaciones_por_usuario:
            asignaciones_por_usuario[user_id] = []
        asignaciones_por_usuario[user_id].append(asig_data)

    return [
        {
            **u,
            "asignaciones": asignaciones_por_usuario.get(u["id"], []),
        }
        for u in usuarios
    ]


@router.post("/usuarios")
def crear_usuario_admin(datos: UsuarioCrearAdmin, usuario=Depends(get_current_user)):
    validar_admin(usuario)

    if datos.nivel_global != "admin_master" and not datos.id_empresa:
        raise HTTPException(status_code=400, detail="Debes seleccionar empresa para usuario/vendedor")

    if datos.nivel_global == "vendedor" and not datos.id_sucursal:
        raise HTTPException(status_code=400, detail="Debes seleccionar sucursal para vendedor")

    username = _slug_text(datos.username.strip().lower())
    nombre = datos.nombre.strip()
    email = (datos.email or f"{username}@local.domus").strip().lower()

    existe_email = (
        supabase.table("usuarios")
        .select("id")
        .eq("email", email)
        .limit(1)
        .execute()
    )

    if existe_email.data:
        raise HTTPException(status_code=400, detail="Ya existe un usuario con ese correo")

    existe_username = (
        supabase.table("usuarios")
        .select("id")
        .eq("username", username)
        .limit(1)
        .execute()
    )

    if existe_username.data:
        raise HTTPException(status_code=400, detail="Ya existe un usuario con ese nombre de usuario")

    id_usuario = str(uuid.uuid4())
    id_raiz = datos.id_empresa if datos.id_empresa else None
    permisos_portal = _normalizar_permisos_portal(datos.permisos_portal, datos.nivel_global)

    password_hash = bcrypt.hashpw(datos.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    nuevo_usuario = (
        supabase.table("usuarios")
        .insert(
            {
                "id": id_usuario,
                "nombre": nombre,
                "username": username,
                "email": email,
                "password_hash": password_hash,
                "nivel_global": datos.nivel_global,
                "permisos_portal": permisos_portal,
                "activo": True,
                "id_raiz": id_raiz,
                "fecha_creacion": datetime.utcnow().isoformat(),
            }
        )
        .execute()
    )

    if not nuevo_usuario.data:
        raise HTTPException(status_code=400, detail="No se pudo crear usuario")

    if datos.id_empresa:
        rol_empresa = datos.rol_empresa or datos.nivel_global

        relacion = (
            supabase.table("usuarios_empresas")
            .insert(
                {
                    "id": str(uuid.uuid4()),
                    "id_usuario": id_usuario,
                    "id_empresa": datos.id_empresa,
                    "rol": rol_empresa,
                    "permisos": {},
                    "activo": True,
                    "fecha_asignacion": datetime.utcnow().isoformat(),
                }
            )
            .execute()
        )

        if not relacion.data:
            raise HTTPException(status_code=400, detail="No se pudo asignar el usuario a la empresa")

    if datos.nivel_global == "vendedor" and datos.id_empresa and datos.id_sucursal:
        try:
            supabase.rpc(
                "agregar_recurso_extra",
                {"p_id_empresa": datos.id_empresa, "p_tipo_recurso": "vendedor"},
            ).execute()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"No se pudo autorizar vendedor extra: {e}")

        nombre_vendedor = datos.nombre_vendedor or nombre

        vendedor = (
            supabase.table("vendedores")
            .insert(
                {
                    "id": str(uuid.uuid4()),
                    "id_empresa": datos.id_empresa,
                    "id_sucursal": datos.id_sucursal,
                    "id_usuario": id_usuario,
                    "nombre": nombre_vendedor,
                    "activo": True,
                    "permisos": {},
                    "fecha_creacion": datetime.utcnow().isoformat(),
                }
            )
            .execute()
        )

        if not vendedor.data:
            raise HTTPException(status_code=400, detail="Se creó usuario, pero falló crear vendedor")

    return {"mensaje": "Usuario creado correctamente", "id_usuario": id_usuario}


@router.patch("/usuarios/{id_usuario}")
def actualizar_usuario_admin(
    id_usuario: str,
    datos: UsuarioActualizarAdmin,
    usuario=Depends(get_current_user),
):
    validar_admin(usuario)

    actual_resp = (
        supabase.table("usuarios")
        .select("id,nombre,username,email,nivel_global,id_raiz,permisos_portal")
        .eq("id", id_usuario)
        .limit(1)
        .execute()
    )

    if not actual_resp.data:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    actual = actual_resp.data[0]
    username = _slug_text(datos.username.strip().lower())
    email = (datos.email or actual.get("email") or f"{username}@local.domus").strip().lower()
    nivel_global = datos.nivel_global or actual.get("nivel_global") or "usuario"

    existe_username = (
        supabase.table("usuarios")
        .select("id")
        .eq("username", username)
        .limit(1)
        .execute()
    )
    if existe_username.data and existe_username.data[0].get("id") != id_usuario:
        raise HTTPException(status_code=400, detail="Ya existe un usuario con ese nombre de usuario")

    existe_email = (
        supabase.table("usuarios")
        .select("id")
        .eq("email", email)
        .limit(1)
        .execute()
    )
    if existe_email.data and existe_email.data[0].get("id") != id_usuario:
        raise HTTPException(status_code=400, detail="Ya existe un usuario con ese correo")

    permisos_portal = _normalizar_permisos_portal(datos.permisos_portal, nivel_global)
    id_raiz = None if nivel_global == "admin_master" else datos.id_empresa

    usuario_update = (
        supabase.table("usuarios")
        .update(
            {
                "nombre": datos.nombre.strip(),
                "username": username,
                "email": email,
                "nivel_global": nivel_global,
                "id_raiz": id_raiz,
                "permisos_portal": permisos_portal,
            }
        )
        .eq("id", id_usuario)
        .execute()
    )

    if nivel_global == "admin_master":
        supabase.table("usuarios_empresas").delete().eq("id_usuario", id_usuario).execute()
    else:
        rel_resp = (
            supabase.table("usuarios_empresas")
            .select("id")
            .eq("id_usuario", id_usuario)
            .limit(1)
            .execute()
        )
        rol_empresa = datos.rol_empresa or nivel_global
        rel_payload = {
            "id_empresa": datos.id_empresa,
            "rol": rol_empresa,
            "permisos": {},
            "activo": True,
        }

        if rel_resp.data:
            supabase.table("usuarios_empresas").update(rel_payload).eq("id", rel_resp.data[0]["id"]).execute()
        else:
            supabase.table("usuarios_empresas").insert(
                {
                    "id": str(uuid.uuid4()),
                    "id_usuario": id_usuario,
                    "id_empresa": datos.id_empresa,
                    "rol": rol_empresa,
                    "permisos": {},
                    "activo": True,
                    "fecha_asignacion": datetime.utcnow().isoformat(),
                }
            ).execute()

    return {"mensaje": "Usuario actualizado correctamente", "usuario": usuario_update.data[0] if usuario_update.data else None}


@router.patch("/usuarios/{id_usuario}/estado")
def cambiar_estado_usuario(
    id_usuario: str,
    datos: UsuarioEstadoUpdate,
    usuario=Depends(get_current_user),
):
    validar_admin(usuario)

    if id_usuario == usuario.get("id_usuario") and not datos.activo:
        raise HTTPException(status_code=400, detail="No puedes suspender tu propio usuario")

    actualizado = (
        supabase.table("usuarios")
        .update({"activo": datos.activo})
        .eq("id", id_usuario)
        .execute()
    )

    if not actualizado.data:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    supabase.table("usuarios_empresas").update({"activo": datos.activo}).eq("id_usuario", id_usuario).execute()
    supabase.table("vendedores").update({"activo": datos.activo}).eq("id_usuario", id_usuario).execute()

    return {
        "mensaje": "Usuario actualizado",
        "activo": datos.activo,
    }


@router.delete("/usuarios/{id_usuario}")
def eliminar_usuario_admin(
    id_usuario: str,
    datos: UsuarioEliminarAdmin,
    usuario=Depends(get_current_user),
):
    validar_admin(usuario)

    if id_usuario == usuario.get("id_usuario"):
        raise HTTPException(status_code=400, detail="No puedes eliminar tu propio usuario")

    admin_db = (
        supabase.table("usuarios")
        .select("id,password_hash")
        .eq("id", usuario.get("id_usuario"))
        .limit(1)
        .execute()
    )

    if not admin_db.data:
        raise HTTPException(status_code=403, detail="Admin no válido")

    password_hash_admin = admin_db.data[0].get("password_hash")
    if not password_hash_admin or not bcrypt.checkpw(
        datos.password_confirmacion.encode("utf-8"),
        password_hash_admin.encode("utf-8"),
    ):
        raise HTTPException(status_code=401, detail="Contraseña de confirmación incorrecta")

    usuario_resp = (
        supabase.table("usuarios")
        .select("*")
        .eq("id", id_usuario)
        .limit(1)
        .execute()
    )

    if not usuario_resp.data:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    usuario_data = usuario_resp.data[0]

    usuarios_empresas = (
        supabase.table("usuarios_empresas")
        .select("*")
        .eq("id_usuario", id_usuario)
        .execute()
    ).data or []

    vendedores = (
        supabase.table("vendedores")
        .select("*")
        .eq("id_usuario", id_usuario)
        .execute()
    ).data or []

    backup_payload = {
        "tipo_respaldo": "usuario",
        "nombre_respaldo": datos.nombre_backup,
        "generado_por": usuario.get("id_usuario"),
        "fecha": datetime.utcnow().isoformat(),
        "usuario": usuario_data,
        "usuarios_empresas": usuarios_empresas,
        "vendedores": vendedores,
    }

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_name = _slug_text(datos.nombre_backup)

    respaldo = (
        supabase.table("empresas_backup")
        .insert(
            {
                "id_empresa_original": id_usuario,
                "nombre_empresa": f"USUARIO::{datos.nombre_backup}",
                "nombre_archivo": f"{backup_name}_{stamp}.json",
                "datos": backup_payload,
            }
        )
        .execute()
    )

    if not respaldo.data:
        raise HTTPException(status_code=500, detail="No se pudo guardar respaldo del usuario")

    backup_id = respaldo.data[0].get("id")

    # Si el usuario era vendedor, se desvincula de vendedor para no romper ventas históricas.
    supabase.table("vendedores").update({"id_usuario": None, "activo": False}).eq("id_usuario", id_usuario).execute()

    supabase.table("usuarios_empresas").delete().eq("id_usuario", id_usuario).execute()

    eliminado = supabase.table("usuarios").delete().eq("id", id_usuario).execute()
    if not eliminado.data:
        raise HTTPException(status_code=500, detail="Usuario respaldado pero no se pudo eliminar")

    return {
        "mensaje": "Usuario eliminado y respaldado correctamente",
        "backup_id": backup_id,
    }


@router.get("/usuarios/backups")
def listar_backups_usuarios(usuario=Depends(get_current_user)):
    validar_admin(usuario)

    backups_resp = (
        supabase.table("empresas_backup")
        .select("id,id_empresa_original,nombre_empresa,nombre_archivo,fecha_eliminacion,datos")
        .order("fecha_eliminacion", desc=True)
        .execute()
    )

    backups = backups_resp.data or []
    salida = []

    for b in backups:
        datos = b.get("datos") or {}
        if datos.get("tipo_respaldo") != "usuario":
            continue

        usuario_data = datos.get("usuario") or {}

        salida.append(
            {
                "id": b.get("id"),
                "id_usuario_original": b.get("id_empresa_original"),
                "nombre_backup": datos.get("nombre_respaldo") or b.get("nombre_empresa"),
                "nombre_archivo": b.get("nombre_archivo"),
                "fecha": b.get("fecha_eliminacion") or datos.get("fecha"),
                "email": usuario_data.get("email"),
                "nivel_global": usuario_data.get("nivel_global"),
            }
        )

    return salida


@router.post("/usuarios/restaurar/{backup_id}")
def restaurar_usuario_desde_backup(backup_id: str, usuario=Depends(get_current_user)):
    validar_admin(usuario)

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
    datos = backup.get("datos") or {}

    if datos.get("tipo_respaldo") != "usuario":
        raise HTTPException(status_code=400, detail="El backup no corresponde a un usuario")

    usuario_data = datos.get("usuario")
    if not usuario_data or not usuario_data.get("id"):
        raise HTTPException(status_code=400, detail="Backup inválido: no contiene usuario")

    id_usuario = usuario_data["id"]

    existe = (
        supabase.table("usuarios")
        .select("id")
        .eq("id", id_usuario)
        .limit(1)
        .execute()
    )

    if existe.data:
        raise HTTPException(status_code=409, detail="Ya existe un usuario con ese ID")

    crear_usuario = supabase.table("usuarios").insert(usuario_data).execute()
    if not crear_usuario.data:
        raise HTTPException(status_code=500, detail="No se pudo restaurar usuario")

    for rel in (datos.get("usuarios_empresas") or []):
        payload = dict(rel)
        if not payload.get("id"):
            payload["id"] = str(uuid.uuid4())

        ya_rel = (
            supabase.table("usuarios_empresas")
            .select("id")
            .eq("id", payload["id"])
            .limit(1)
            .execute()
        )
        if ya_rel.data:
            payload["id"] = str(uuid.uuid4())

        supabase.table("usuarios_empresas").insert(payload).execute()

    for vend in (datos.get("vendedores") or []):
        payload = dict(vend)
        vend_id = payload.get("id")
        if not vend_id:
            payload["id"] = str(uuid.uuid4())
            supabase.table("vendedores").insert(payload).execute()
            continue

        existe_vend = (
            supabase.table("vendedores")
            .select("id")
            .eq("id", vend_id)
            .limit(1)
            .execute()
        )

        if existe_vend.data:
            supabase.table("vendedores").update(payload).eq("id", vend_id).execute()
        else:
            supabase.table("vendedores").insert(payload).execute()

    return {
        "mensaje": "Usuario restaurado correctamente",
        "id_usuario": id_usuario,
    }


@router.get("/empresa-resumen/{empresa_id}")
def resumen_empresa(empresa_id: str, usuario=Depends(get_current_user)):
    validar_admin(usuario)

    empresa_resp = (
        supabase.table("empresas")
        .select("id,nombre,estado,id_plan,fecha_creacion")
        .eq("id", empresa_id)
        .limit(1)
        .execute()
    )

    if not empresa_resp.data:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    empresa = empresa_resp.data[0]

    vendedores_activos = (
        supabase.table("vendedores")
        .select("id", count="exact")
        .eq("id_empresa", empresa_id)
        .eq("activo", True)
        .execute()
        .count
        or 0
    )

    sucursales_total = (
        supabase.table("sucursales")
        .select("id", count="exact")
        .eq("id_empresa", empresa_id)
        .execute()
        .count
        or 0
    )

    clientes_total = (
        supabase.table("clientes")
        .select("id", count="exact")
        .eq("id_empresa", empresa_id)
        .execute()
        .count
        or 0
    )

    productos_total = (
        supabase.table("productos")
        .select("id", count="exact")
        .eq("id_empresa", empresa_id)
        .execute()
        .count
        or 0
    )

    ahora = datetime.utcnow()
    inicio_dia = ahora.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    inicio_mes = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    ventas_hoy_resp = (
        supabase.table("ventas")
        .select("id,total")
        .eq("id_empresa", empresa_id)
        .gte("fecha", inicio_dia)
        .execute()
    )
    ventas_hoy_data = ventas_hoy_resp.data or []

    ventas_mes_resp = (
        supabase.table("ventas")
        .select("id,total")
        .eq("id_empresa", empresa_id)
        .gte("fecha", inicio_mes)
        .execute()
    )
    ventas_mes_data = ventas_mes_resp.data or []

    recursos_activos_resp = (
        supabase.table("recursos_activos_empresa")
        .select("id,tipo_recurso,costo_mensual,fecha_inicio,fecha_fin")
        .eq("id_empresa", empresa_id)
        .execute()
    )

    recursos_activos = []
    hoy_date = datetime.utcnow().date()
    for recurso in (recursos_activos_resp.data or []):
        fecha_fin = recurso.get("fecha_fin")
        if fecha_fin:
            try:
                fecha_fin_date = datetime.fromisoformat(str(fecha_fin)).date()
                if fecha_fin_date < hoy_date:
                    continue
            except Exception:
                pass
        recursos_activos.append(recurso)

    autorizaciones_resp = (
        supabase.table("autorizaciones_admin_empresa")
        .select("id,tipo_recurso,cantidad_autorizada,costo_mensual,activo,fecha_autorizacion,fecha_fin")
        .eq("id_empresa", empresa_id)
        .execute()
    )

    return {
        "empresa": empresa,
        "kpi": {
            "sucursales": sucursales_total,
            "vendedores_activos": vendedores_activos,
            "clientes": clientes_total,
            "productos": productos_total,
            "ventas_hoy": sum(v.get("total") or 0 for v in ventas_hoy_data),
            "transacciones_hoy": len(ventas_hoy_data),
            "ventas_mes": sum(v.get("total") or 0 for v in ventas_mes_data),
            "transacciones_mes": len(ventas_mes_data),
        },
        "recursos_activos": recursos_activos,
        "autorizaciones": autorizaciones_resp.data or [],
    }


@router.get("/saas-metrics")
def saas_metrics(usuario=Depends(get_current_user)):
    validar_admin(usuario)

    ahora = datetime.utcnow()
    inicio_mes = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_empresas = (
        supabase.table("empresas").select("id", count="exact").execute().count or 0
    )

    empresas_activas = (
        supabase.table("empresas")
        .select("id", count="exact")
        .eq("estado", "activa")
        .execute()
        .count
        or 0
    )

    empresas_suspendidas = (
        supabase.table("empresas")
        .select("id", count="exact")
        .eq("estado", "suspendida")
        .execute()
        .count
        or 0
    )

    empresas_trial = (
        supabase.table("suscripciones")
        .select("id", count="exact")
        .eq("tipo", "trial")
        .eq("estado", "activa")
        .execute()
        .count
        or 0
    )

    nuevas_empresas_mes = (
        supabase.table("empresas")
        .select("id", count="exact")
        .gte("fecha_creacion", inicio_mes.isoformat())
        .execute()
        .count
        or 0
    )

    cuentas_pagadas = (
        supabase.table("cuentas_matriz")
        .select("monto,monto_total")
        .in_("estado", ESTADOS_PAGADA_ALIAS)
        .gte("fecha_pago", inicio_mes.isoformat())
        .execute()
        .data
        or []
    )

    ingresos_mes = sum((c.get("monto_total") or c.get("monto") or 0) for c in cuentas_pagadas)

    cuentas_pendientes = (
        supabase.table("cuentas_matriz")
        .select("monto,monto_total")
        .eq("estado", "pendiente")
        .execute()
        .data
        or []
    )

    ingresos_pendientes = sum((c.get("monto_total") or c.get("monto") or 0) for c in cuentas_pendientes)

    cuentas_vencidas = (
        supabase.table("cuentas_matriz")
        .select("monto,monto_total")
        .in_("estado", ESTADOS_VENCIDA_ALIAS)
        .execute()
        .data
        or []
    )

    ingresos_vencidos = sum((c.get("monto_total") or c.get("monto") or 0) for c in cuentas_vencidas)

    suscripciones_activas = (
        supabase.table("suscripciones")
        .select("precio")
        .eq("estado", "activa")
        .execute()
        .data
        or []
    )

    mrr = sum(s.get("precio") or 0 for s in suscripciones_activas)
    arr = mrr * 12

    total_trials = (
        supabase.table("suscripciones")
        .select("id", count="exact")
        .eq("tipo", "trial")
        .execute()
        .count
        or 0
    )

    suscripciones_pago = (
        supabase.table("suscripciones")
        .select("id", count="exact")
        .neq("tipo", "trial")
        .execute()
        .count
        or 0
    )

    conversion = 0
    if total_trials > 0:
        conversion = (suscripciones_pago / total_trials) * 100

    suscripciones_vencidas = (
        supabase.table("suscripciones")
        .select("id", count="exact")
        .eq("estado", "vencida")
        .execute()
        .count
        or 0
    )

    churn = 0
    if total_empresas > 0:
        churn = (suscripciones_vencidas / total_empresas) * 100

    ltv = 0
    if churn > 0:
        ltv = mrr / (churn / 100)

    return {
        "empresas": {
            "total": total_empresas,
            "activas": empresas_activas,
            "trial": empresas_trial,
            "suspendidas": empresas_suspendidas,
            "nuevas_mes": nuevas_empresas_mes,
        },
        "ingresos": {
            "mes_actual": ingresos_mes,
            "pendientes": ingresos_pendientes,
            "vencidos": ingresos_vencidos,
            "mrr": mrr,
            "arr": arr,
        },
        "metricas": {
            "conversion_trial_porcentaje": round(conversion, 2),
            "churn_porcentaje": round(churn, 2),
            "ltv_estimado": round(ltv, 2),
        },
    }


@router.get("/dashboard")
def dashboard_financiero(usuario=Depends(get_current_user)):
    validar_admin(usuario)

    response = supabase.table("dashboard_admin_financiero").select("*").execute()

    data = response.data[0] if response.data else {}

    total_empresas = supabase.table("empresas").select("id", count="exact").execute().count or 0
    empresas_activas = (
        supabase.table("empresas").select("id", count="exact").eq("estado", "activa").execute().count or 0
    )
    empresas_suspendidas = (
        supabase.table("empresas").select("id", count="exact").eq("estado", "suspendida").execute().count or 0
    )

    total_usuarios = supabase.table("usuarios").select("id", count="exact").execute().count or 0
    usuarios_activos = (
        supabase.table("usuarios").select("id", count="exact").eq("activo", True).execute().count or 0
    )

    cuentas_vencidas = (
        supabase.table("cuentas_matriz")
        .select("id", count="exact")
        .in_("estado", ESTADOS_VENCIDA_ALIAS)
        .execute()
        .count
        or 0
    )
    cuentas_pendientes = (
        supabase.table("cuentas_matriz").select("id", count="exact").eq("estado", "pendiente").execute().count
        or 0
    )

    hoy = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    ventas_hoy = (
        supabase.table("ventas")
        .select("id,total")
        .gte("fecha", hoy)
        .execute()
        .data
        or []
    )

    data.update(
        {
            "empresas_total": total_empresas,
            "empresas_activas": data.get("empresas_activas", empresas_activas),
            "empresas_suspendidas": data.get("empresas_suspendidas", empresas_suspendidas),
            "usuarios_total": total_usuarios,
            "usuarios_activos": usuarios_activos,
            "usuarios_inactivos": max(total_usuarios - usuarios_activos, 0),
            "cuentas_vencidas": cuentas_vencidas,
            "cuentas_pendientes": cuentas_pendientes,
            "ventas_hoy_global": sum(v.get("total") or 0 for v in ventas_hoy),
            "transacciones_hoy_global": len(ventas_hoy),
        }
    )

    return data


@router.get("/crecimiento-mensual")
def crecimiento_mensual(usuario=Depends(get_current_user)):
    validar_admin(usuario)

    response = (
        supabase.table("dashboard_crecimiento_mensual")
        .select("*")
        .order("mes")
        .execute()
    )

    return response.data
