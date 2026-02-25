import jwt
import datetime
from datetime import timedelta
from fastapi import HTTPException

SECRET_KEY = "DOMUS_SUPER_SECRETO_2026"
ALGORITHM = "HS256"


def crear_token(datos: dict):
    datos_a_codificar = datos.copy()
    expiracion = datetime.datetime.utcnow() + timedelta(hours=8)
    datos_a_codificar.update({"exp": expiracion})
    return jwt.encode(datos_a_codificar, SECRET_KEY, algorithm=ALGORITHM)


def verificar_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")