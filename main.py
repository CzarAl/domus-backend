from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import bcrypt
import datetime
from datetime import timedelta
from routes.usuarios import router as usuarios_router
from auth import crear_token
from routes.clientes import router as clientes_router
from database import supabase
from routes.inventario import router as inventario_router
from routes.ventas import router as ventas_router
from routes.caja import router as caja_router
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(usuarios_router)
app.include_router(clientes_router)
app.include_router(inventario_router)
app.include_router(ventas_router)
app.include_router(caja_router)

# =================================
# CONFIGURACIÓN
# =================================


SECRET_KEY = "c4d4352f2f4bd29f6e892ac00572984cb3cb69b12e243cef776c71abdc430cf4f541f18b6c986d7181153cae4c4f4d4b02890d3a827e1324b34e330696aadeee"
ALGORITHM = "HS256"


# =================================
# MODELO LOGIN
# =================================

class LoginData(BaseModel):
    correo: str
    contrasena: str


# =================================
# HOME
# =================================

@app.get("/")
def home():
    return {"mensaje": "Domus SaaS API activa"}

# =================================
# LOGIN
# =================================

@app.post("/login")
def login(datos: LoginData):

    try:
        # ==============================
        # BUSCAR USUARIO
        # ==============================
        respuesta = supabase.table("usuarios") \
            .select("*") \
            .eq("correo", datos.correo) \
            .execute()

        if not respuesta.data:
            raise HTTPException(status_code=401, detail="Credenciales incorrectas")

        usuario = respuesta.data[0]

        password_guardado = usuario.get("contrasena")
        if not password_guardado:
            raise HTTPException(status_code=500, detail="Usuario sin contraseña registrada")

        # ==============================
        # VERIFICAR BLOQUEO
        # ==============================
        bloqueado_hasta = usuario.get("bloqueado_hasta")
        esta_bloqueado = False

        if bloqueado_hasta:
            bloqueado_dt = datetime.datetime.fromisoformat(bloqueado_hasta)
            if datetime.datetime.utcnow() < bloqueado_dt:
                esta_bloqueado = True

        # ==============================
        # VALIDAR CONTRASEÑA
        # ==============================
        password_correcta = bcrypt.checkpw(
            datos.contrasena.encode("utf-8"),
            password_guardado.encode("utf-8")
        )

        # ❌ Contraseña incorrecta
        if not password_correcta:

            # Si está bloqueado → mantener bloqueo
            if esta_bloqueado:
                raise HTTPException(status_code=403, detail="Cuenta bloqueada temporalmente")

            intentos = (usuario.get("intentos_fallidos") or 0) + 1

            update_data = {
                "intentos_fallidos": intentos
            }

            if intentos >= 5:
                bloqueo = datetime.datetime.utcnow() + datetime.timedelta(minutes=15)
                update_data["bloqueado_hasta"] = bloqueo.isoformat()

            supabase.table("usuarios") \
                .update(update_data) \
                .eq("id", usuario["id"]) \
                .execute()

            raise HTTPException(status_code=401, detail="Credenciales incorrectas")

        # ✅ Contraseña correcta

        # Si estaba bloqueado → desbloquear
        if esta_bloqueado:
            supabase.table("usuarios") \
                .update({
                    "intentos_fallidos": 0,
                    "bloqueado_hasta": None
                }) \
                .eq("id", usuario["id"]) \
                .execute()
        else:
            # Reset normal
            supabase.table("usuarios") \
                .update({
                    "intentos_fallidos": 0
                }) \
                .eq("id", usuario["id"]) \
                .execute()

        # ==============================
        # ADMIN MASTER
        # ==============================
        if usuario.get("nivel") == "admin_master":
            token = crear_token({
                "id_usuario": usuario.get("id"),
                "id_raiz": usuario.get("id"),
                "nivel": usuario.get("nivel")
            })
            return {"access_token": token, "token_type": "bearer"}

        # ==============================
        # VALIDAR SUSCRIPCIÓN
        # ==============================
        if usuario.get("nivel") == "usuario":

            suscripcion = supabase.table("suscripciones") \
                .select("*") \
                .eq("id_usuario", usuario.get("id")) \
                .execute()

            if not suscripcion.data:
                raise HTTPException(status_code=403, detail="Suscripción pendiente de pago")

            sub = suscripcion.data[0]

            if sub.get("estado") != "activa":
                raise HTTPException(status_code=403, detail="Suscripción no activa")

            fecha_vencimiento = sub.get("fecha_vencimiento")
            if fecha_vencimiento:
                fecha_vencimiento = datetime.datetime.fromisoformat(fecha_vencimiento)
                if datetime.datetime.utcnow() > fecha_vencimiento:
                    raise HTTPException(status_code=403, detail="Suscripción vencida")

        # ==============================
        # TOKEN FINAL
        # ==============================
        token = crear_token({
            "id_usuario": usuario.get("id"),
            "id_raiz": usuario.get("id_raiz"),
            "nivel": usuario.get("nivel")
        })

        return {"access_token": token, "token_type": "bearer"}

    except HTTPException:
        raise
    except Exception as e:
        print("ERROR REAL:", e)
        raise HTTPException(status_code=500, detail="Error interno del servidor")
