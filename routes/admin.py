from fastapi import APIRouter, Depends, HTTPException
from dependencies import get_current_user
from database import supabase

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/tiendas")
def listar_tiendas(usuario = Depends(get_current_user)):

    if usuario.get("nivel") != "admin_master":
        raise HTTPException(status_code=403, detail="No autorizado")

    usuarios = supabase.table("usuarios") \
        .select("id, nombre, correo, nivel, id_raiz") \
        .eq("nivel", "usuario") \
        .execute()

    resultado = []

    for u in usuarios.data:

        # sucursales
        sucursales = supabase.table("sucursales") \
            .select("id") \
            .eq("id_raiz", u["id"]) \
            .execute()

        # vendedores
        vendedores = supabase.table("usuarios") \
            .select("id") \
            .eq("id_raiz", u["id"]) \
            .eq("nivel", "vendedor") \
            .execute()

        # suscripción
        suscripcion = supabase.table("suscripciones") \
            .select("estado, fecha_vencimiento") \
            .eq("id_usuario", u["id"]) \
            .execute()

        resultado.append({
            "id_usuario": u["id"],
            "nombre": u["nombre"],
            "correo": u["correo"],
            "sucursales": len(sucursales.data),
            "vendedores": len(vendedores.data),
            "suscripcion": suscripcion.data[0] if suscripcion.data else None
        })

    return resultado