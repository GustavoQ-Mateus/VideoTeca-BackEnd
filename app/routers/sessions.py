from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, ConfigDict
from app.scheduling import Period, future_period, lock_room
from app.db.postgres import get_pool
from app.core.security import require_role

router = APIRouter(prefix="/staff/sessions")
staff = require_role("funcionario", "admin")


class SessionInput(Period):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    film_id: int = Field(gt=0)
    room_id: int = Field(gt=0)
    slots: int = Field(ge=1, le=10000)
    description: str = Field(default="", max_length=4000)


async def validate_session(conn, body):
    future_period(body)
    room = await lock_room(conn, body.room_id, body.slots)
    if not await conn.fetchval(
        "SELECT id FROM films WHERE id=$1 AND status<>'inactive' FOR UPDATE",
        body.film_id,
    ):
        raise HTTPException(404, "Filme não encontrado")
    return room


@router.get("")
async def listing(user=Depends(staff)):
    return [
        dict(r)
        for r in await get_pool().fetch(
            """SELECT e.*,f.title AS film_title,r.name AS room_name
      FROM events e JOIN films f ON f.id=e.film_id JOIN rooms r ON r.id=e.room_id ORDER BY starts_at DESC"""
        )
    ]


@router.post("", status_code=201)
async def create(body: SessionInput, user=Depends(staff)):
    async with get_pool().acquire() as conn, conn.transaction():
        room = await validate_session(conn, body)
        row = await conn.fetchrow(
            """INSERT INTO events(title,event_type,film_id,room_id,location,starts_at,ends_at,slots,description)
          VALUES($1,'session',$2,$3,$4,$5,$6,$7,$8) RETURNING *""",
            body.title,
            body.film_id,
            body.room_id,
            room["name"],
            body.starts_at,
            body.ends_at,
            body.slots,
            body.description,
        )
    return dict(row)


@router.put("/{session_id}")
async def update(session_id: int, body: SessionInput, user=Depends(staff)):
    async with get_pool().acquire() as conn, conn.transaction():
        event = await conn.fetchrow(
            "SELECT * FROM events WHERE id=$1 AND room_id IS NOT NULL FOR UPDATE",
            session_id,
        )
        if not event:
            raise HTTPException(404, "Sessão não encontrada")
        if body.slots < event["enrolled"]:
            raise HTTPException(422, "Capacidade inferior ao número de inscritos")
        room = await validate_session(conn, body)
        row = await conn.fetchrow(
            """UPDATE events SET title=$2,film_id=$3,room_id=$4,location=$5,starts_at=$6,ends_at=$7,slots=$8,description=$9
           WHERE id=$1 RETURNING *""",
            session_id,
            body.title,
            body.film_id,
            body.room_id,
            room["name"],
            body.starts_at,
            body.ends_at,
            body.slots,
            body.description,
        )
    return dict(row)
