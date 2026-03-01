from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from database import supabase
from auth import verificar_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = verificar_token(token)

    user_id = payload.get("id_usuario")

    if not user_id:
        raise HTTPException(status_code=403, detail="Token inválido")

    respuesta = supabase.table("usuarios") \
        .select("*") \
        .eq("id", user_id) \
        .execute()

    if not respuesta.data:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    return respuesta.data[0]