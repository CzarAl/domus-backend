from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from datetime import datetime
import bcrypt
import uuid

from database import supabase
from dependencies import get_current_user

router = APIRouter(prefix="/vendedores", tags=["Vendedores"])


# ==========================
# SCHEMA CREAR VENDEDOR
# ==========================

class CrearVendedorRequest(BaseModel):
    nombre: str
    email: EmailStr
    password: str
    id_sucursal: str


# ==========================
# LISTAR VENDEDORES
# ==========================

@router.get("/")
def listar_vendedores(usuario_actual=Depends(get_current_user)):
    id_empresa = usuario_actual["id_raiz"]

    vendedores_resp = (
        supabase.table("vendedores")
        .select("id,id_usuario,id_sucursal,nombre,activo,fecha_creacion,permisos")
        .eq("id_empresa", id_empresa)
        .order("fecha_creacion", desc=True)
        .execute()
    )

    vendedores = vendedores_resp.data or []
    ids_usuario = [v.get("id_usuario") for v in vendedores if v.get("id_usuario")]

    emails_por_usuario: dict[str, str] = {}
    if ids_usuario:
        usuarios_resp = (
            supabase.table("usuarios")
            .select("id,email")
            .in_("id", ids_usuario)
            .execute()
        )

        for usuario in (usuarios_resp.data or []):
            user_id = usuario.get("id")
            if user_id:
                emails_por_usuario[user_id] = usuario.get("email")

    return [
        {
            **v,
            "email": emails_por_usuario.get(v.get("id_usuario")),
        }
        for v in vendedores
    ]


# ==========================
# CREAR VENDEDOR
# ==========================

@router.post("/crear")
def crear_vendedor(
    datos: CrearVendedorRequest,
    usuario_actual=Depends(get_current_user),
):
    try:
        # Solo dueño de tienda o admin_master
        if usuario_actual.get("rol") not in ["usuario", "admin_master"]:
            raise HTTPException(status_code=403, detail="No autorizado")

        id_empresa = usuario_actual["id_raiz"]

        # Validar que no exista otro usuario con ese correo
        existente = (
            supabase.table("usuarios")
            .select("id")
            .eq("email", datos.email)
            .limit(1)
            .execute()
        )
        if existente.data:
            raise HTTPException(status_code=400, detail="Ya existe un usuario con ese correo")

        # Valida cupo y registra costo extra si aplica (autorizado por admin_master)
        try:
            supabase.rpc(
                "agregar_recurso_extra",
                {"p_id_empresa": id_empresa, "p_tipo_recurso": "vendedor"}
            ).execute()
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        id_usuario_creado = str(uuid.uuid4())
        id_vendedor = str(uuid.uuid4())

        # 1) Hashear contraseña
        password_hash = bcrypt.hashpw(
            datos.password.encode("utf-8"),
            bcrypt.gensalt()
        ).decode("utf-8")

        # 2) Crear usuario vendedor
        nuevo_usuario = supabase.table("usuarios").insert({
            "id": id_usuario_creado,
            "id_raiz": id_empresa,
            "email": datos.email,
            "password_hash": password_hash,
            "nivel_global": "vendedor",
            "activo": True,
            "fecha_creacion": datetime.utcnow().isoformat(),
        }).execute()

        if not nuevo_usuario.data:
            raise HTTPException(status_code=400, detail="No se pudo crear usuario")

        # 3) Relacionar usuario con empresa
        relacion = supabase.table("usuarios_empresas").insert({
            "id": str(uuid.uuid4()),
            "id_usuario": id_usuario_creado,
            "id_empresa": id_empresa,
            "rol": "vendedor",
            "permisos": {},
            "activo": True,
            "fecha_asignacion": datetime.utcnow().isoformat(),
        }).execute()

        if not relacion.data:
            raise HTTPException(status_code=400, detail="No se pudo asignar vendedor a la empresa")

        # 4) Crear registro vendedor
        nuevo_vendedor = supabase.table("vendedores").insert({
            "id": id_vendedor,
            "id_empresa": id_empresa,
            "id_sucursal": datos.id_sucursal,
            "id_usuario": id_usuario_creado,
            "nombre": datos.nombre,
            "activo": True,
            "permisos": {},
            "fecha_creacion": datetime.utcnow().isoformat(),
        }).execute()

        if not nuevo_vendedor.data:
            raise HTTPException(status_code=400, detail="No se pudo crear vendedor")

        return {
            "mensaje": "Vendedor creado correctamente",
            "id_vendedor": id_vendedor,
            "id_usuario": id_usuario_creado,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
