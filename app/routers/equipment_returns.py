from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from typing import Literal
from app.db.postgres import get_pool
from app.routers.equipment import staff

router = APIRouter(prefix="/staff/equipment")


class ReturnEquipment(BaseModel):
    condition: Literal["good", "damaged"]
    damaged_quantity: int = Field(default=0, ge=0)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def check_damage(self):
        if self.condition == "good" and self.damaged_quantity:
            raise ValueError("Item em bom estado não pode ter quantidade avariada")
        if self.condition == "damaged" and (
            not self.damaged_quantity or not self.notes.strip()
        ):
            raise ValueError("Informe a quantidade avariada e descreva o dano")
        return self


@router.post("/loans/{loan_id}/return")
async def return_equipment(loan_id: int, body: ReturnEquipment, user=Depends(staff)):
    async with get_pool().acquire() as conn, conn.transaction():
        # Same lock order as checkout prevents stock races and deadlocks.
        equipment_id = await conn.fetchval(
            "SELECT equipment_id FROM equipment_loans WHERE id=$1", loan_id
        )
        if equipment_id is None:
            raise HTTPException(404, "Empréstimo não encontrado")
        await conn.fetchrow(
            "SELECT id FROM equipment WHERE id=$1 FOR UPDATE", equipment_id
        )
        loan = await conn.fetchrow(
            "SELECT * FROM equipment_loans WHERE id=$1 FOR UPDATE", loan_id
        )
        if loan["returned_at"]:
            raise HTTPException(409, "Devolução já registrada")
        if body.damaged_quantity > loan["quantity"]:
            raise HTTPException(
                422, "Quantidade avariada excede a quantidade emprestada"
            )
        row = await conn.fetchrow(
            """UPDATE equipment_loans SET returned_at=NOW(),checked_in_by=$2,
             condition=$3,damaged_quantity=$4,notes=$5 WHERE id=$1 RETURNING *""",
            loan_id,
            user["id"],
            body.condition,
            body.damaged_quantity,
            body.notes,
        )
        await conn.execute(
            "UPDATE equipment SET damaged_quantity=damaged_quantity+$2 WHERE id=$1",
            equipment_id,
            body.damaged_quantity,
        )
    return dict(row)
