from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from auth import verificar_token
from database import supabase

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


def validar_empresa_activa(usuario: dict):
    """
    Valida que la empresa esté activa.
    Ejecuta actualizaciones automáticas de suscripciones.
    """

    # 🔹 Admin master nunca se bloquea
    if usuario.get("nivel_global") == "admin_master":
        return usuario

    id_empresa = usuario.get("id_empresa")

    if not id_empresa:
        raise HTTPException(
            status_code=403,
            detail="Empresa no seleccionada"
        )

    # 🔹 Ejecutar funciones automáticas
    supabase.rpc("actualizar_suscripciones_vencidas").execute()
    supabase.rpc("detectar_suscripciones_por_vencer").execute()

    # 🔹 Verificar estado de la empresa
    resp = supabase.table("empresas") \
        .select("estado") \
        .eq("id", id_empresa) \
        .execute()

    if not resp.data:
        raise HTTPException(
            status_code=403,
            detail="Empresa no encontrada"
        )

    estado = resp.data[0]["estado"]

    if estado != "activa":
        raise HTTPException(
            status_code=403,
            detail={
                "tipo": "empresa_suspendida",
                "mensaje": "Tu suscripción está vencida. Renueva para continuar."
            }
        )

    return usuario


def get_current_user(token: str = Depends(oauth2_scheme)):
    """
    Valida token JWT y luego valida estado de empresa.
    """

    payload = verificar_token(token)

    if not payload.get("id_usuario"):
        raise HTTPException(
            status_code=403,
            detail="Token inválido"
        )

    # 🔹 Validar empresa activa (si aplica)
    usuario_validado = validar_empresa_activa(payload)

    return usuario_validado