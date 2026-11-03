from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.db.postgres import get_pool
from app.routers.sessions import staff

router = APIRouter(prefix="/staff/sessions")


class Publication(BaseModel):
    published: bool


@router.put("/{session_id}/publication")
async def publication(session_id: int, body: Publication, user=Depends(staff)):
    async with get_pool().acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT *,starts_at>NOW() AS future FROM events WHERE id=$1 AND room_id IS NOT NULL FOR UPDATE",
            session_id,
        )
        if not row:
            raise HTTPException(404, "Sessão não encontrada")
        if body.published and not row["future"]:
            raise HTTPException(422, "Não é possível publicar uma sessão passada")
        result = await conn.fetchrow(
            "UPDATE events SET status=$2 WHERE id=$1 RETURNING *",
            session_id,
            "published" if body.published else "draft",
        )
    return dict(result)
