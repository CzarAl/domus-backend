from fastapi import APIRouter, Depends
from dependencies import get_current_user

router = APIRouter()

@router.get("/perfil")
def perfil(usuario = Depends(get_current_user)):
    return {
        "mensaje": "Ruta protegida funcionando",
        "usuario_token": usuario
    }