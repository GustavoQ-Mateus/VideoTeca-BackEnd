from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import current_user, require_role
from app.db.postgres import get_pool

router = APIRouter()
_student = require_role("aluno")


@router.get("/profile")
async def get_profile(user=Depends(_student)):
    return user


@router.get("/loans")
async def get_loans(user=Depends(_student)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT l.id, l.status, l.borrowed_at, l.due_at, l.returned_at, "
        "f.title as film, f.director, f.cover, c.code as copy "
        "FROM loans l "
        "JOIN copies c ON c.id = l.copy_id "
        "JOIN films f ON f.id = c.film_id "
        "WHERE l.user_id = $1 ORDER BY l.borrowed_at DESC",
        user["id"],
    )
    return [dict(r) for r in rows]


@router.get("/reservations")
async def get_reservations(user=Depends(_student)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT r.id, r.status, r.requested_at, r.pickup_until, "
        "f.title as film, f.director, f.cover, f.media "
        "FROM reservations r "
        "JOIN films f ON f.id = r.film_id "
        "WHERE r.user_id = $1 ORDER BY r.requested_at DESC",
        user["id"],
    )
    return [dict(r) for r in rows]


class ReservationRequest(BaseModel):
    film_id: int


@router.post("/reservations")
async def create_reservation(body: ReservationRequest, user=Depends(_student)):
    pool = get_pool()
    # Verifica se já tem reserva ativa para esse filme
    existing = await pool.fetchval(
        "SELECT id FROM reservations WHERE user_id = $1 AND film_id = $2 AND status = 'waiting'",
        user["id"], body.film_id,
    )
    if existing:
        raise HTTPException(status_code=400, detail="Reserva já existente para este filme")
    row = await pool.fetchrow(
        "INSERT INTO reservations (user_id, film_id) VALUES ($1, $2) "
        "RETURNING id, status, requested_at",
        user["id"], body.film_id,
    )
    return dict(row)


@router.delete("/reservations/{reservation_id}")
async def cancel_reservation(reservation_id: int, user=Depends(_student)):
    pool = get_pool()
    updated = await pool.fetchval(
        "UPDATE reservations SET status = 'cancelled' "
        "WHERE id = $1 AND user_id = $2 AND status = 'waiting' RETURNING id",
        reservation_id, user["id"],
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Reserva não encontrada")
    return {"ok": True}


@router.get("/events")
async def get_events(user=Depends(_student)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT e.id, e.title, e.event_type, e.location, e.starts_at, e.slots, e.enrolled, "
        "e.description, e.certificate, e.status, "
        "CASE WHEN er.user_id IS NOT NULL THEN true ELSE false END as enrolled_by_me "
        "FROM events e "
        "LEFT JOIN event_registrations er ON er.event_id = e.id AND er.user_id = $1 "
        "WHERE e.status = 'published' ORDER BY e.starts_at ASC",
        user["id"],
    )
    return [dict(r) for r in rows]


@router.post("/events/{event_id}/enroll")
async def enroll_event(event_id: int, user=Depends(_student)):
    pool = get_pool()
    event = await pool.fetchrow(
        "SELECT id, slots, enrolled FROM events WHERE id = $1 AND status = 'published'",
        event_id,
    )
    if not event:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    if event["enrolled"] >= event["slots"]:
        raise HTTPException(status_code=400, detail="Sem vagas disponíveis")

    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                await conn.execute(
                    "INSERT INTO event_registrations (event_id, user_id) VALUES ($1, $2)",
                    event_id, user["id"],
                )
                await conn.execute(
                    "UPDATE events SET enrolled = enrolled + 1 WHERE id = $1", event_id
                )
            except Exception:
                raise HTTPException(status_code=400, detail="Já inscrito neste evento")
    return {"ok": True}


@router.delete("/events/{event_id}/enroll")
async def unenroll_event(event_id: int, user=Depends(_student)):
    pool = get_pool()
    deleted = await pool.fetchval(
        "DELETE FROM event_registrations WHERE event_id = $1 AND user_id = $2 RETURNING id",
        event_id, user["id"],
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Inscrição não encontrada")
    await pool.execute("UPDATE events SET enrolled = GREATEST(enrolled - 1, 0) WHERE id = $1", event_id)
    return {"ok": True}


@router.get("/favorites")
async def get_favorites(user=Depends(_student)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT f.id, f.title, f.director, f.year, f.genre, f.cover, f.status, f.media "
        "FROM films f "
        "JOIN reservations r ON r.film_id = f.id "
        "WHERE r.user_id = $1 AND r.status = 'favorite' ORDER BY f.title",
        user["id"],
    )
    return [dict(r) for r in rows]


@router.get("/notifications")
async def get_notifications(user=Depends(_student)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id, title, body, read, created_at FROM notifications "
        "WHERE user_id = $1 ORDER BY created_at DESC LIMIT 50",
        user["id"],
    )
    return [dict(r) for r in rows]


@router.put("/notifications/{notif_id}/read")
async def mark_read(notif_id: int, user=Depends(_student)):
    pool = get_pool()
    await pool.execute(
        "UPDATE notifications SET read = true WHERE id = $1 AND user_id = $2",
        notif_id, user["id"],
    )
    return {"ok": True}


@router.get("/stats")
async def get_stats(user=Depends(_student)):
    pool = get_pool()
    active_loans = await pool.fetchval(
        "SELECT COUNT(*) FROM loans WHERE user_id = $1 AND status = 'borrowed'", user["id"]
    )
    reservations = await pool.fetchval(
        "SELECT COUNT(*) FROM reservations WHERE user_id = $1 AND status = 'waiting'", user["id"]
    )
    notifications = await pool.fetchval(
        "SELECT COUNT(*) FROM notifications WHERE user_id = $1 AND read = false", user["id"]
    )
    enrolled_events = await pool.fetchval(
        "SELECT COUNT(*) FROM event_registrations WHERE user_id = $1", user["id"]
    )
    return {
        "active_loans": active_loans,
        "reservations": reservations,
        "notifications": notifications,
        "enrolled_events": enrolled_events,
    }
