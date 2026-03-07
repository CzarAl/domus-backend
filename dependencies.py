from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from auth import verificar_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


# 🔐 Usuario autenticado (desde JWT)
def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = verificar_token(token)

    user_id = payload.get("sub") or payload.get("id_usuario") or payload.get("id")
    id_empresa = payload.get("id_empresa") or payload.get("id_raiz")
    rol = payload.get("rol") or payload.get("nivel") or payload.get("nivel_global")

    if not user_id:
        raise HTTPException(status_code=403, detail="Token inválido")

    normalized = payload.copy()
    normalized["sub"] = user_id
    normalized["id_usuario"] = user_id
    normalized["id"] = user_id
    normalized["id_empresa"] = id_empresa
    normalized["id_raiz"] = id_empresa
    normalized["rol"] = rol
    normalized["nivel"] = rol
    normalized["nivel_global"] = rol

    return normalized


# 🔒 Requiere rol específico
def require_role(role: str):
    def role_checker(user=Depends(get_current_user)):

        if user.get("rol") != role:
            raise HTTPException(
                status_code=403,
                detail=f"Acceso solo para {role}"
            )

        return user

    return role_checker
