from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, AwareDatetime, Field, ConfigDict
from datetime import datetime, timezone
from app.core.security import require_role
from app.db.postgres import get_pool

router = APIRouter(prefix="/staff/equipment")
staff = require_role("funcionario", "admin")


class EquipmentCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=200)
    category: str = Field(min_length=2, max_length=80)
    quantity: int = Field(ge=1, le=10000)


class Checkout(BaseModel):
    equipment_id: int = Field(gt=0)
    registration: str = Field(min_length=1, max_length=40)
    quantity: int = Field(ge=1, le=10000)
    due_at: AwareDatetime


@router.get("")
async def list_equipment(user=Depends(staff)):
    return [
        dict(r)
        for r in await get_pool().fetch("""SELECT e.*, e.quantity-e.damaged_quantity-
        COALESCE((SELECT SUM(l.quantity) FROM equipment_loans l WHERE l.equipment_id=e.id AND l.returned_at IS NULL),0) AS available
        FROM equipment e ORDER BY name""")
    ]


@router.post("", status_code=201)
async def create_equipment(body: EquipmentCreate, user=Depends(staff)):
    return dict(
        await get_pool().fetchrow(
            "INSERT INTO equipment(name,category,quantity) VALUES($1,$2,$3) RETURNING *",
            body.name,
            body.category,
            body.quantity,
        )
    )


@router.get("/loans")
async def list_loans(user=Depends(staff)):
    return [
        dict(r)
        for r in await get_pool().fetch("""SELECT l.*,e.name,p.full_name,p.registration,
       (COALESCE(l.returned_at,NOW()) > l.due_at) AS late
       FROM equipment_loans l JOIN equipment e ON e.id=l.equipment_id JOIN profiles p ON p.id=l.user_id
       ORDER BY l.borrowed_at DESC""")
    ]


@router.post("/loans", status_code=201)
async def checkout(body: Checkout, user=Depends(staff)):
    if body.due_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "O prazo deve ser futuro")
    async with get_pool().acquire() as conn, conn.transaction():
        item = await conn.fetchrow(
            "SELECT * FROM equipment WHERE id=$1 FOR UPDATE", body.equipment_id
        )
        if not item:
            raise HTTPException(404, "Equipamento não encontrado")
        borrower = await conn.fetchrow(
            "SELECT id FROM profiles WHERE registration=$1", body.registration
        )
        if not borrower:
            raise HTTPException(404, "Matrícula não encontrada")
        used = await conn.fetchval(
            "SELECT COALESCE(SUM(quantity),0) FROM equipment_loans WHERE equipment_id=$1 AND returned_at IS NULL",
            body.equipment_id,
        )
        if body.quantity > item["quantity"] - item["damaged_quantity"] - used:
            raise HTTPException(409, "Quantidade indisponível")
        row = await conn.fetchrow(
            """INSERT INTO equipment_loans(equipment_id,user_id,quantity,checked_out_by,due_at)
              VALUES($1,$2,$3,$4,$5) RETURNING *""",
            body.equipment_id,
            borrower["id"],
            body.quantity,
            user["id"],
            body.due_at,
        )
    return dict(row)
