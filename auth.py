import os
import jwt
import hashlib
from datetime import datetime, timedelta
from fastapi import HTTPException
from dotenv import load_dotenv
load_dotenv()


# ======================================
# CONFIGURACION
# ======================================

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"

ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("TOKEN_EXPIRE_HOURS", 8))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", 7))
RECOVERY_TOKEN_EXPIRE_DAYS = int(os.getenv("RECOVERY_TOKEN_EXPIRE_DAYS", 3650))

if not SECRET_KEY:
    raise Exception("SECRET_KEY no esta configurada en variables de entorno")


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
# RECOVERY TOKEN
# ======================================

def recovery_fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()


def crear_recovery_token(id_usuario: str, email: str, password_hash: str):
    payload = {
        "sub": id_usuario,
        "id_usuario": id_usuario,
        "email": email,
        "pwdv": recovery_fingerprint(password_hash),
        "type": "recovery",
        "exp": datetime.utcnow() + timedelta(days=RECOVERY_TOKEN_EXPIRE_DAYS),
        "iat": datetime.utcnow(),
    }

    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verificar_recovery_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Codigo de recuperacion expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Codigo de recuperacion invalido")

    if payload.get("type") != "recovery":
        raise HTTPException(status_code=401, detail="Codigo de recuperacion invalido")

    return payload


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
        raise HTTPException(status_code=401, detail="Token invalido")
