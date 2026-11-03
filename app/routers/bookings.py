import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, ConfigDict
from app.core.security import require_role
from app.db.postgres import get_pool
from app.scheduling import Period, future_period, lock_room

router = APIRouter()
professor = require_role("professor")


class BookingCreate(Period):
    model_config = ConfigDict(str_strip_whitespace=True)
    room_id: int = Field(gt=0)
    title: str = Field(min_length=3, max_length=200)
    course: str = Field(min_length=2, max_length=120)
    registration: str = Field(min_length=1, max_length=40)
    student_count: int = Field(ge=1, le=10000)
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/reservas", status_code=201)
async def create_booking(body: BookingCreate, user=Depends(professor)):
    future_period(body)
    if body.registration != user["registration"]:
        raise HTTPException(422, "A matrícula deve corresponder ao usuário autenticado")
    async with get_pool().acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL statement_timeout = '50s'")
        room = await lock_room(conn, body.room_id, body.student_count)
        booking = await conn.fetchrow(
            """
            INSERT INTO room_bookings(room_id,professor_id,starts_at,ends_at,title,status,course,registration,student_count,notes)
            VALUES($1,$2,$3,$4,$5,'pending',$6,$7,$8,$9) RETURNING *
        """,
            body.room_id,
            user["id"],
            body.starts_at,
            body.ends_at,
            body.title,
            body.course,
            body.registration,
            body.student_count,
            body.notes,
        )
        payload = body.model_dump(mode="json") | {"room_name": room["name"]}
        await conn.execute(
            """INSERT INTO professor_requests(professor_id,request_type,payload,booking_id)
                              VALUES($1,'room',$2::jsonb,$3)""",
            user["id"],
            json.dumps(payload),
            booking["id"],
        )
    return dict(booking)


@router.get("/reservas/me")
async def my_bookings(user=Depends(professor)):
    return [
        dict(r)
        for r in await get_pool().fetch(
            "SELECT b.*, r.name AS room_name FROM room_bookings b JOIN rooms r ON r.id=b.room_id WHERE professor_id=$1 ORDER BY starts_at DESC",
            user["id"],
        )
    ]
