from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from database import supabase
from dependencies import get_current_user

router = APIRouter(prefix="/ventas", tags=["Ventas"])


# ==============================
# LISTAR VENTAS
# ==============================

@router.get("/")
def listar_ventas(usuario=Depends(get_current_user)):

    id_empresa = usuario["id_raiz"]

    return (
        supabase.table("ventas")
        .select("*")
        .eq("id_empresa", id_empresa)
        .execute()
        .data
    )


# ==============================
# CREAR VENTA TRANSACCIONAL
# ==============================

class ItemVentaNueva(BaseModel):
    id_producto: str | None = None
    id_servicio: str | None = None
    cantidad: int
    precio_unitario: float


class VentaNueva(BaseModel):
    id_sucursal: str
    id_cliente: str | None = None
    metodo_pago: str
    subtotal: float
    iva: float
    flete: float = 0
    total: float
    comentarios: str | None = None
    detalles: list[ItemVentaNueva]
    confirmar_transferencia: bool = False


@router.post("/nueva")
def crear_venta_nueva(datos: VentaNueva, usuario=Depends(get_current_user)):

    id_empresa = usuario["id_raiz"]
    id_vendedor = usuario.get("id_vendedor")

    response = supabase.rpc(
        "crear_venta_completa",
        {
            "p_id_empresa": id_empresa,
            "p_id_sucursal": datos.id_sucursal,
            "p_id_vendedor": id_vendedor,
            "p_id_cliente": datos.id_cliente,
            "p_metodo_pago": datos.metodo_pago,
            "p_subtotal": datos.subtotal,
            "p_iva": datos.iva,
            "p_flete": datos.flete,
            "p_total": datos.total,
            "p_comentarios": datos.comentarios,
            "p_detalles": [d.dict() for d in datos.detalles],
            "p_confirmar_transferencia": datos.confirmar_transferencia
        }
    ).execute()

    return response.data