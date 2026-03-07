from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
import bcrypt
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
# CREAR VENDEDOR
# ==========================

@router.post("/crear")
def crear_vendedor(
    datos: CrearVendedorRequest,
    usuario_actual=Depends(get_current_user),
):
    try:
        # 🔒 Solo dueño de tienda o admin_master
        if usuario_actual.get("rol") not in ["usuario", "admin_master"]:
            raise HTTPException(status_code=403, detail="No autorizado")

        id_empresa = usuario_actual["id_raiz"]

        # Valida cupo y registra costo extra si aplica (autorizado por admin_master)
        try:
            supabase.rpc(
                "agregar_recurso_extra",
                {"p_id_empresa": id_empresa, "p_tipo_recurso": "vendedor"}
            ).execute()
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        # 1️⃣ Hashear contraseña
        password_hash = bcrypt.hashpw(
            datos.password.encode("utf-8"),
            bcrypt.gensalt()
        ).decode("utf-8")

        # 2️⃣ Crear usuario vendedor
        nuevo_usuario = supabase.table("usuarios").insert({
            "id_raiz": id_empresa,
            "email": datos.email,
            "password_hash": password_hash,
            "nivel_global": "vendedor",
            "activo": True
        }).execute()

        if not nuevo_usuario.data:
            raise HTTPException(status_code=400, detail="No se pudo crear usuario")

        id_usuario_creado = nuevo_usuario.data[0]["id"]

        # 3️⃣ Crear registro vendedor
        nuevo_vendedor = supabase.table("vendedores").insert({
            "id_empresa": id_empresa,
            "id_sucursal": datos.id_sucursal,
            "id_usuario": id_usuario_creado,
            "nombre": datos.nombre,
            "activo": True
        }).execute()

        if not nuevo_vendedor.data:
            raise HTTPException(status_code=400, detail="No se pudo crear vendedor")

        return {
            "mensaje": "Vendedor creado correctamente"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
