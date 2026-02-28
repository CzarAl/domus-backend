import os
import jwt
from datetime import datetime, timedelta
from fastapi import HTTPException


# ======================================
# CONFIGURACIÓN
# ======================================

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"

ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("TOKEN_EXPIRE_HOURS", 8))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", 7))

if not SECRET_KEY:
    raise Exception("SECRET_KEY no está configurada en variables de entorno")


# ======================================
# CREAR ACCESS TOKEN
# ======================================

def crear_access_token(datos: dict):

    payload = datos.copy()

    expiracion = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)

    payload.update({
        "exp": expiracion,
        "iat": datetime.utcnow(),
        "type": "access"
    })

    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


# ======================================
# CREAR REFRESH TOKEN
# ======================================

def crear_refresh_token(datos: dict):

    payload = {
        "id_usuario": datos.get("id_usuario"),
        "type": "refresh",
        "exp": datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        "iat": datetime.utcnow()
    }

    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


# ======================================
# VERIFICAR TOKEN
# ======================================

def verificar_token(token: str):

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")

    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")