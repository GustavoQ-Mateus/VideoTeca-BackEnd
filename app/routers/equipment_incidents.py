from fastapi import APIRouter, Depends
from app.db.postgres import get_pool
from app.routers.equipment import staff

router = APIRouter(prefix="/staff/equipment")


@router.get("/incidents")
async def incidents(user=Depends(staff)):
    return [
        dict(r)
        for r in await get_pool().fetch(
            """SELECT l.id,e.name,p.full_name,l.quantity,l.due_at,
      l.returned_at,l.condition,l.damaged_quantity,l.notes,
      COALESCE(l.returned_at,NOW())>l.due_at AS late
      FROM equipment_loans l JOIN equipment e ON e.id=l.equipment_id JOIN profiles p ON p.id=l.user_id
      WHERE COALESCE(l.returned_at,NOW())>l.due_at OR l.damaged_quantity>0 ORDER BY l.due_at"""
        )
    ]
