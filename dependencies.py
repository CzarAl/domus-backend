from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from auth import verificar_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = verificar_token(token)

    if not payload.get("id_raiz"):
        raise HTTPException(status_code=403, detail="Token inválido")

    return payload