from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
import bcrypt
import jwt
from fastapi.middleware.cors import CORSMiddleware

from database import supabase
from auth import crear_access_token, crear_refresh_token, verificar_token
from dependencies import get_current_user

from routes.usuarios import router as usuarios_router
from routes.clientes import router as clientes_router
from routes.inventario import router as inventario_router
from routes.ventas import router as ventas_router
from routes.caja import router as caja_router
from routes.admin import router as admin_router
from routes.dashboard import router as dashboard_router
from routes import admin_saas
from routes import empresas
from routes import pagos
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import os
from dotenv import load_dotenv






security = HTTPBearer()

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")

app = FastAPI()


# =================================
# CORS
# =================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "https://rovimr.com",
        "https://app.rovimr.com",
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =================================
# ROUTERS
# =================================

app.include_router(usuarios_router)

app.include_router(clientes_router, dependencies=[Depends(get_current_user)])
app.include_router(inventario_router, dependencies=[Depends(get_current_user)])
app.include_router(ventas_router, dependencies=[Depends(get_current_user)])
app.include_router(caja_router, dependencies=[Depends(get_current_user)])
app.include_router(admin_router, dependencies=[Depends(get_current_user)])
app.include_router(dashboard_router, dependencies=[Depends(get_current_user)])
app.include_router(pagos.router)


# Admin SaaS protegido (solo admin_master pasa por dependency)
app.include_router(admin_saas.router, dependencies=[Depends(get_current_user)])

# Empresas protegido
app.include_router(empresas.router, dependencies=[Depends(get_current_user)])


# =================================
# MODELOS
# =================================

class LoginData(BaseModel):
    correo: str
    contrasena: str


class SeleccionarEmpresa(BaseModel):
    id_empresa: str


class RefreshData(BaseModel):
    refresh_token: str


# =================================
# HOME
# =================================

@app.get("/")
def home():
    return {"mensaje": "Domus SaaS API activa"}


# =================================
# VALIDAR TOKEN
# =================================

@app.get("/me")
def validar_usuario(usuario=Depends(get_current_user)):
    return {"usuario": usuario}


# =================================
# LOGIN
# =================================

@app.post("/login")
def login(datos: LoginData):

    try:
        respuesta = supabase.table("usuarios") \
            .select("*") \
            .eq("email", datos.correo) \
            .execute()

        if not respuesta.data:
            raise HTTPException(status_code=401, detail="Credenciales incorrectas")

        usuario = respuesta.data[0]

        if not usuario.get("activo"):
            raise HTTPException(status_code=403, detail="Usuario inactivo")

        if not bcrypt.checkpw(
            datos.contrasena.encode("utf-8"),
            usuario["password_hash"].encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="Credenciales incorrectas")

        # 🔥 Ejecutar motor financiero (no debe romper login si falla)
        try:
            supabase.rpc("motor_financiero_saas", {}).execute()
        except Exception:
            pass

        # 🔥 Crear tokens
        access_token = crear_access_token({
            "id_usuario": usuario["id"],
            "nivel_global": usuario.get("nivel_global")
        })

        refresh_token = crear_refresh_token({
            "id_usuario": usuario["id"]
        })

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    # =========================
    # ADMIN MASTER
    # =========================

    if usuario.get("nivel_global") == "admin_master":

        access_token = crear_access_token({
            "id_usuario": usuario["id"],
            "nivel_global": usuario["nivel_global"]
        })

        refresh_token = crear_refresh_token({
            "id_usuario": usuario["id"]
        })

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }

    # =========================
    # USUARIO NORMAL
    # =========================

    empresas_resp = supabase.table("usuarios_empresas") \
        .select("id_empresa, rol") \
        .eq("id_usuario", usuario["id"]) \
        .eq("activo", True) \
        .execute()

    empresas_usuario = empresas_resp.data

    if not empresas_usuario:
        return {"status": "crear_empresa"}

    if len(empresas_usuario) == 1:

        empresa = empresas_usuario[0]

        access_token = crear_access_token({
            "id_usuario": usuario["id"],
            "id_empresa": empresa["id_empresa"],
            "rol": empresa["rol"],
            "nivel_global": usuario["nivel_global"]
        })

        refresh_token = crear_refresh_token({
            "id_usuario": usuario["id"]
        })

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }

    return {
        "status": "seleccionar_empresa",
        "empresas": empresas_usuario
    }

# =================================
# SELECCIONAR EMPRESA
# =================================

@app.post("/seleccionar-empresa")
def seleccionar_empresa(
    datos: SeleccionarEmpresa,
    usuario: dict = Depends(get_current_user)
):

    # Verificar que el usuario tenga acceso a esa empresa
    resp = supabase.table("usuarios_empresas") \
        .select("rol") \
        .eq("id_usuario", usuario["id"]) \
        .eq("id_empresa", datos.id_empresa) \
        .eq("activo", True) \
        .execute()

    if not resp.data:
        raise HTTPException(
            status_code=403,
            detail="No tienes acceso a esta empresa"
        )

    rol = resp.data[0]["rol"]

    access_token = crear_access_token({
        "id_usuario": usuario["id"],
        "id_empresa": datos.id_empresa,
        "rol": rol,
        "nivel_global": usuario.get("nivel_global")
    })

    refresh_token = crear_refresh_token({
        "id_usuario": usuario["id"]
    })

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

# =================================
# REFRESH
# =================================

@app.post("/refresh")
def refresh_token(data: RefreshData):

    payload = verificar_token(data.refresh_token)

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Token inválido")

    nuevo_access = crear_access_token({
        "id_usuario": payload["id_usuario"]
    })

    return {
        "access_token": nuevo_access,
        "token_type": "bearer"
    }
    

# =================================
# CAMBIAR PASSWORD
# =================================
class CambiarPasswordData(BaseModel):
    password_actual: str
    password_nueva: str


@app.post("/cambiar-password")
def cambiar_password(
    datos: CambiarPasswordData,
    usuario_actual: dict = Depends(get_current_user)
):
    # Verificar contraseña actual
    if not bcrypt.checkpw(
        datos.password_actual.encode("utf-8"),
        usuario_actual["password_hash"].encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail="Contraseña actual incorrecta"
        )

    # Hashear nueva contraseña
    nuevo_hash = bcrypt.hashpw(
        datos.password_nueva.encode("utf-8"),
        bcrypt.gensalt()
    ).decode("utf-8")

    # Actualizar en base de datos
    supabase.table("usuarios") \
        .update({"password_hash": nuevo_hash}) \
        .eq("id", usuario_actual["id"]) \
        .execute()

    return {"mensaje": "Contraseña actualizada correctamente"}