from fastapi import APIRouter, Depends
from dependencies import obtener_usuario_actual

router = APIRouter()


@router.get("/perfil")
def perfil(usuario = Depends(obtener_usuario_actual)):
    return {
        "mensaje": "Ruta protegida funcionando",
        "usuario_token": usuario
    }