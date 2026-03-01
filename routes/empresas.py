from fastapi import APIRouter, Depends, HTTPException
from dependencies import get_current_user
from database import supabase
from datetime import datetime

router = APIRouter(prefix="/empresas", tags=["Empresas"])


@router.post("/solicitar-cancelacion")
def solicitar_cancelacion(usuario=Depends(get_current_user)):

    id_empresa = usuario.get("id_empresa")

    if not id_empresa:
        raise HTTPException(status_code=400, detail="Empresa no seleccionada")

    supabase.table("empresas") \
        .update({
            "cancelacion_pendiente": True,
            "fecha_cancelacion_solicitada": datetime.utcnow(),
            "estado": "cancelacion_pendiente"
        }) \
        .eq("id", id_empresa) \
        .execute()

    return {"mensaje": "Solicitud enviada al administrador"}