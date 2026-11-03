import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any

from app.core.security import require_role
from app.db.postgres import get_pool

router = APIRouter()
_prof = require_role("professor")


@router.get("/profile")
async def get_profile(user=Depends(_prof)):
    return user


@router.get("/requests")
async def get_requests(user=Depends(_prof)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id, request_type, payload, status, staff_notes, created_at "
        "FROM professor_requests WHERE professor_id = $1 ORDER BY created_at DESC",
        user["id"],
    )
    return [
        dict(r)
        | {
            "payload": (
                json.loads(r["payload"])
                if isinstance(r["payload"], str)
                else r["payload"]
            )
        }
        for r in rows
    ]


class FilmRequest(BaseModel):
    film_id: int
    date: str
    notes: str | None = None


from app.routers.bookings import BookingCreate as RoomRequest, create_booking


@router.post("/requests/film")
async def request_film(body: FilmRequest, user=Depends(_prof)):
    pool = get_pool()
    film = await pool.fetchrow(
        "SELECT id, title FROM films WHERE id = $1", body.film_id
    )
    if not film:
        raise HTTPException(status_code=404, detail="Filme não encontrado")
    row = await pool.fetchrow(
        "INSERT INTO professor_requests (professor_id, request_type, payload) "
        "VALUES ($1, 'film', $2) RETURNING id, status, created_at",
        user["id"],
        json.dumps(
            {
                "film_id": body.film_id,
                "film_title": film["title"],
                "date": body.date,
                "notes": body.notes,
            }
        ),
    )
    return dict(row)


@router.post("/requests/room")
async def request_room(body: RoomRequest, user=Depends(_prof)):
    return await create_booking(body, user)


@router.get("/stats")
async def get_stats(user=Depends(_prof)):
    pool = get_pool()
    active = await pool.fetchval(
        "SELECT COUNT(*) FROM professor_requests WHERE professor_id = $1 AND status = 'analysis'",
        user["id"],
    )
    approved = await pool.fetchval(
        "SELECT COUNT(*) FROM professor_requests WHERE professor_id = $1 AND status = 'approved'",
        user["id"],
    )
    total = await pool.fetchval(
        "SELECT COUNT(*) FROM professor_requests WHERE professor_id = $1", user["id"]
    )
    return {
        "active_requests": active,
        "approved_requests": approved,
        "total_requests": total,
    }
