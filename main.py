from fastapi import FastAPI, HTTPException, Depends, Request
from pydantic import BaseModel
import bcrypt
import jwt
import uuid
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials


from database import supabase
from auth import crear_access_token, crear_refresh_token, verificar_token
from dependencies import get_current_user
from dependencies import require_role


from routes.usuarios import router as usuarios_router
from routes.clientes import router as clientes_router
from routes.inventario import router as inventario_router
from routes.productos import router as productos_router
from routes.ventas import router as ventas_router
from routes.caja import router as caja_router
from routes.admin import router as admin_router
from routes.dashboard import router as dashboard_router
from routes.vendedores import router as vendedores_router
from routes.sucursales import router as sucursales_router
from routes.wallet import router as wallet_router
from routes.uploads import router as uploads_router
from routes import admin_saas
from routes import empresas
from routes import pagos
from routes import admin_cargos
from routes import empresa_finanzas
from routes import mr


import os
from dotenv import load_dotenv


security = HTTPBearer()

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")

app = FastAPI()

# Routers
app.include_router(mr.router)

# =================================
# MIDDLEWARE AUDITORÍA BLOQUEO
# =================================

@app.middleware("http")
async def auditoria_bloqueo_middleware(request: Request, call_next):

    try:
        response = await call_next(request)
        return response

    except Exception as e:

        mensaje = str(e)

        if "Empresa suspendida" in mensaje or "Cuenta vencida" in mensaje:

            try:
                token = request.headers.get("authorization")

                user_id = None
                empresa_id = None

                if token:
                    token = token.replace("Bearer ", "")
                    payload = verificar_token(token)
                    user_id = payload.get("id_usuario")
                    empresa_id = payload.get("id_raiz")

                supabase.table("auditoria_bloqueos").insert({
                    "id_empresa": empresa_id,
                    "endpoint": request.url.path,
                    "usuario_id": user_id,
                    "mensaje": mensaje
                }).execute()

            except Exception:
                pass

            return JSONResponse(
                status_code=403,
                content={"detail": mensaje}
            )

        return JSONResponse(
            status_code=500,
            content={"detail": mensaje}
        )


cors_origins = [
    origin.strip()
    for origin in (os.getenv("CORS_ORIGINS") or "").split(",")
    if origin.strip()
]
cors_origin_regex = (os.getenv("CORS_ORIGIN_REGEX") or "").strip() or None

if cors_origins or cors_origin_regex:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_origin_regex=cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
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
app.include_router(productos_router, dependencies=[Depends(get_current_user)])
app.include_router(ventas_router, dependencies=[Depends(get_current_user)])
app.include_router(caja_router, dependencies=[Depends(get_current_user)])
app.include_router(admin_router, dependencies=[Depends(get_current_user)])
app.include_router(dashboard_router, dependencies=[Depends(get_current_user)])
app.include_router(pagos.router)
app.include_router(admin_cargos.router)
app.include_router(empresa_finanzas.router)
app.include_router(vendedores_router)
app.include_router(sucursales_router)
app.include_router(wallet_router)
app.include_router(uploads_router)


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


def _claims_from_contexto(contexto_usuario: dict, permisos: dict | None = None):
    permisos = permisos or {}
    rol = contexto_usuario["nivel"]
    id_empresa = contexto_usuario["id_raiz"]

    return {
        "sub": contexto_usuario["id_usuario"],
        "id_usuario": contexto_usuario["id_usuario"],
        "id_empresa": id_empresa,
        "rol": rol,
        "id_sucursal": contexto_usuario["id_sucursal"],
        "id_vendedor": contexto_usuario["id_vendedor"],
        "permisos": permisos,
    }


def _obtener_contexto_por_usuario(id_usuario: str):
    contexto = supabase.rpc(
        "obtener_contexto_usuario",
        {"p_id_usuario": id_usuario},
    ).execute()

    if not contexto.data:
        raise HTTPException(
            status_code=500,
            detail="No se pudo obtener contexto del usuario"
        )

    return contexto.data[0]


def _obtener_permisos_vendedor(contexto_usuario: dict):
    if contexto_usuario["nivel"] != "vendedor" or not contexto_usuario["id_vendedor"]:
        return {}

    vendedor = (
        supabase.table("vendedores")
        .select("permisos")
        .eq("id", contexto_usuario["id_vendedor"])
        .single()
        .execute()
    )

    if not vendedor.data:
        return {}

    return vendedor.data.get("permisos", {}) or {}


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


@app.get("/test-vendedor")
def test_vendedor(usuario=Depends(require_role("vendedor"))):
    return {
        "mensaje": "Acceso permitido",
        "usuario": usuario
    }
        
# =================================
# DASHBOARD TIENDA AVANZADO
# =================================

@app.get("/tienda/dashboard")
def dashboard_tienda(usuario=Depends(get_current_user)):

    id_empresa = usuario["id_raiz"]

    # 🔹 Obtener datos empresa
    empresa_db = (
        supabase.table("empresas")
        .select("nombre, logo_url, color_primario, color_secundario, usar_marca_domus")
        .eq("id", id_empresa)
        .single()
        .execute()
    )

    empresa = empresa_db.data if empresa_db.data else {}

    # 🔹 Ventas totales
    ventas_resp = (
        supabase.table("ventas")
        .select("id,total,id_sucursal,id_vendedor,fecha", count="exact")
        .eq("id_empresa", id_empresa)
        .execute()
    )

    ventas = ventas_resp.data or []
    total_ventas = sum(v["total"] for v in ventas)
    cantidad_ventas = ventas_resp.count or 0

    # 🔹 Ventas del mes y del día
    from datetime import datetime

    ahora = datetime.now()
    inicio_mes = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    inicio_dia = ahora.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    ventas_mes = [v for v in ventas if (v.get("fecha") or "") >= inicio_mes]
    total_mes = sum(v["total"] for v in ventas_mes)

    ventas_hoy = [v for v in ventas if (v.get("fecha") or "") >= inicio_dia]
    total_hoy = sum(v["total"] for v in ventas_hoy)

    # 🔹 Ventas por sucursal
    ventas_por_sucursal = {}
    for v in ventas:
        suc = v.get("id_sucursal") or "sin_sucursal"
        ventas_por_sucursal[suc] = ventas_por_sucursal.get(suc, 0) + v["total"]

    # 🔹 Ventas por vendedor
    ventas_por_vendedor = {}
    for v in ventas:
        vend = v.get("id_vendedor") or "sin_vendedor"
        ventas_por_vendedor[vend] = ventas_por_vendedor.get(vend, 0) + v["total"]

    # 🔹 Producto más vendido
    ids_ventas = [v["id"] for v in ventas]
    producto_mas_vendido = None

    if ids_ventas:
        detalles = (
            supabase.table("detalle_ventas")
            .select("id_producto,cantidad,id_venta")
            .in_("id_venta", ids_ventas)
            .execute()
        ).data or []

        conteo = {}
        for d in detalles:
            prod = d["id_producto"]
            conteo[prod] = conteo.get(prod, 0) + d["cantidad"]

        if conteo:
            producto_top_id = max(conteo, key=conteo.get)

            producto_db = (
                supabase.table("productos")
                .select("nombre")
                .eq("id", producto_top_id)
                .single()
                .execute()
            )

            if producto_db.data:
                producto_mas_vendido = producto_db.data["nombre"]

    return {
        "empresa": empresa,
        "total_ventas": total_ventas,
        "cantidad_ventas": cantidad_ventas,
        "ventas_mes_actual": total_mes,
        "ventas_hoy": total_hoy,
        "transacciones_hoy": len(ventas_hoy),
        "ventas_por_sucursal": ventas_por_sucursal,
        "ventas_por_vendedor": ventas_por_vendedor,
        "producto_mas_vendido": producto_mas_vendido,
    }


# =================================
# LOGIN
# =================================

@app.post("/login")
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends()
):
    try:
        print("LOGIN NUEVO EJECUTÁNDOSE")
        
        # 🔥 Detectar si viene JSON (frontend) o form-data (Swagger)
        try:
            body = await request.json()
            correo = body.get("correo") or body.get("username")
            contrasena = body.get("contrasena") or body.get("password")
        except:
            correo = form_data.username
            contrasena = form_data.password
                    
        if not correo or not contrasena:
            raise HTTPException(status_code=400, detail="Datos incompletos")

        
        # 1️⃣ Buscar usuario por correo
        print("ANTES DEL SELECT")
        respuesta = (
            supabase.table("usuarios")
            .select("*")
            .eq("email", correo)
            .execute()
        )
        print("DESPUÉS DEL SELECT")

        if not respuesta.data:
            raise HTTPException(status_code=401, detail="Credenciales incorrectas")

        usuario = respuesta.data[0]

        # 2️⃣ Verificar activo
        if not usuario.get("activo"):
            raise HTTPException(status_code=403, detail="Usuario inactivo")

        # 3️⃣ Verificar contraseña
        if not bcrypt.checkpw(
            contrasena.encode("utf-8"),
            usuario["password_hash"].encode("utf-8"),
        ):
            raise HTTPException(status_code=401, detail="Credenciales incorrectas")

        # 4️⃣ Ejecutar motor financiero (no bloqueante)
        try:
            supabase.rpc("motor_financiero_saas", {}).execute()
        except Exception:
            pass
        # 5️⃣ Obtener contexto real multiempresa
        contexto_usuario = _obtener_contexto_por_usuario(usuario["id"])
        print("CONTEXTO:", contexto_usuario)

        if not contexto_usuario.get("id_raiz"):
            raise HTTPException(
                status_code=403,
                detail="Usuario sin empresa asignada"
            )

        # 🔥 NUEVO: obtener permisos si es vendedor
        permisos = _obtener_permisos_vendedor(contexto_usuario)

        # 6️⃣ Crear tokens con contexto REAL + permisos
        access_token = crear_access_token(_claims_from_contexto(contexto_usuario, permisos))

        refresh_token = crear_refresh_token({
            "id_usuario": contexto_usuario["id_usuario"]
        })

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }

    except HTTPException:
        raise
    except Exception as e:
        print("ERROR LOGIN:", e)
        raise
  

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
        .eq("id_usuario", usuario["id_usuario"]) \
        .eq("id_empresa", datos.id_empresa) \
        .eq("activo", True) \
        .execute()

    if not resp.data:
        raise HTTPException(
            status_code=403,
            detail="No tienes acceso a esta empresa"
        )

    rol = resp.data[0]["rol"]

    id_vendedor = None
    id_sucursal = None
    permisos = {}

    if rol == "vendedor":
        vendedor = (
            supabase.table("vendedores")
            .select("id,id_sucursal,permisos")
            .eq("id_empresa", datos.id_empresa)
            .eq("id_usuario", usuario["id_usuario"])
            .eq("activo", True)
            .limit(1)
            .execute()
        )
        if vendedor.data:
            id_vendedor = vendedor.data[0]["id"]
            id_sucursal = vendedor.data[0]["id_sucursal"]
            permisos = vendedor.data[0].get("permisos", {}) or {}

    access_token = crear_access_token({
        "sub": usuario["id_usuario"],
        "id_usuario": usuario["id_usuario"],
        "id_empresa": datos.id_empresa,
        "rol": rol,
        "id_sucursal": id_sucursal,
        "id_vendedor": id_vendedor,
        "permisos": permisos
    })

    refresh_token = crear_refresh_token({
        "id_usuario": usuario["id_usuario"]
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

    id_usuario = payload.get("id_usuario") or payload.get("sub")
    if not id_usuario:
        raise HTTPException(status_code=401, detail="Token inválido")

    contexto_usuario = _obtener_contexto_por_usuario(id_usuario)
    permisos = _obtener_permisos_vendedor(contexto_usuario)
    nuevo_access = crear_access_token(_claims_from_contexto(contexto_usuario, permisos))

    return {
        "access_token": nuevo_access,
        "token_type": "bearer"
    }
   
   
   
   
   
   
   
    

@app.post("/admin/autorizar-recurso")
def autorizar_recurso(
    id_empresa: str,
    tipo_recurso: str,
    cantidad: int,
    costo_mensual: float,
    usuario=Depends(require_role("admin_master"))
):
    # Validar tipo permitido
    if tipo_recurso not in ["vendedor", "sucursal", "web_publica", "wallet"]:
        raise HTTPException(status_code=400, detail="Tipo de recurso inválido")

    data = {
        "id": str(uuid.uuid4()),
        "id_empresa": id_empresa,
        "tipo_recurso": tipo_recurso,
        "cantidad_autorizada": cantidad,
        "costo_mensual": costo_mensual,
        "activo": True
    }

    response = supabase.table("autorizaciones_admin_empresa").insert(data).execute()

    return {
        "mensaje": "Recurso autorizado correctamente",
        "data": response.data
    }
    
@app.post("/admin/cancelar-recurso")
def cancelar_recurso(
    id_autorizacion: str,
    usuario=Depends(require_role("admin_master"))
):

    response = (
        supabase.table("autorizaciones_admin_empresa")
        .update({
            "activo": False,
            "fecha_fin": "now()"
        })
        .eq("id", id_autorizacion)
        .execute()
    )

    return {
        "mensaje": "Recurso cancelado correctamente",
        "data": response.data
    }

@app.get("/admin/recursos-empresa")
def listar_recursos_empresa(
    id_empresa: str,
    usuario=Depends(require_role("admin_master"))
):

    response = (
        supabase.table("autorizaciones_admin_empresa")
        .select("*")
        .eq("id_empresa", id_empresa)
        .order("fecha_autorizacion", desc=True)
        .execute()
    )

    return {
        "empresa": id_empresa,
        "recursos": response.data
    }
    

@app.post("/admin/cancelar-empresa")
def cancelar_empresa(
    id_empresa: str,
    _usuario=Depends(require_role("admin_master"))
):
    try:
        # Fuente de verdad: función SQL de respaldo + eliminación definitiva.
        supabase.rpc("cancelar_empresa_definitivamente", {"p_id_empresa": id_empresa}).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo cancelar empresa desde función SQL: {exc}"
        )

    return {
        "mensaje": "Empresa cancelada definitivamente y respaldada correctamente"
    }


@app.get("/admin/empresas")
def listar_empresas(
    usuario=Depends(require_role("admin_master"))
):

    print("USUARIO TOKEN:", usuario)

    response = (
        supabase.table("empresas")
        .select("id,nombre,estado,id_plan,fecha_creacion")
        .eq("es_empresa_master", False)
        .order("fecha_creacion", desc=True)
        .execute()
    )

    return {
        "empresas": response.data
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












