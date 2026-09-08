from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.core.security import require_role
from app.db.postgres import get_pool

router = APIRouter()
_staff = require_role("funcionario", "admin")


# ── Dashboard stats ──────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(user=Depends(_staff)):
    pool = get_pool()
    total_films = await pool.fetchval("SELECT COUNT(*) FROM films WHERE status != 'inactive'")
    active_loans = await pool.fetchval("SELECT COUNT(*) FROM loans WHERE status = 'borrowed'")
    pending_reservations = await pool.fetchval("SELECT COUNT(*) FROM reservations WHERE status = 'waiting'")
    late_items = await pool.fetchval(
        "SELECT COUNT(*) FROM loans WHERE status = 'borrowed' AND due_at < NOW()"
    )
    pending_requests = await pool.fetchval(
        "SELECT COUNT(*) FROM professor_requests WHERE status = 'analysis'"
    )
    upcoming_events = await pool.fetchval(
        "SELECT COUNT(*) FROM events WHERE starts_at > NOW() AND status = 'published'"
    )
    return {
        "total_films": total_films,
        "active_loans": active_loans,
        "pending_reservations": pending_reservations,
        "late_items": late_items,
        "pending_requests": pending_requests,
        "upcoming_events": upcoming_events,
    }


# ── Inventory ────────────────────────────────────────────────────────────────

@router.get("/inventory")
async def list_inventory(user=Depends(_staff)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT f.*, "
        "(SELECT COUNT(*) FROM copies c WHERE c.film_id = f.id AND c.status = 'available') as available_copies, "
        "(SELECT COUNT(*) FROM copies c WHERE c.film_id = f.id) as total_copies "
        "FROM films f WHERE f.status != 'inactive' ORDER BY f.title ASC"
    )
    return [dict(r) for r in rows]


class FilmCreate(BaseModel):
    title: str
    original_title: Optional[str] = None
    year: Optional[int] = None
    director: Optional[str] = None
    genre: Optional[str] = None
    duration: Optional[str] = None
    language: Optional[str] = None
    rating: Optional[str] = None
    media: Optional[str] = None
    cover: Optional[str] = None
    synopsis: Optional[str] = None
    location: Optional[str] = None
    internal_code: Optional[str] = None


@router.post("/inventory")
async def create_film(body: FilmCreate, user=Depends(_staff)):
    pool = get_pool()
    row = await pool.fetchrow(
        "INSERT INTO films (title, original_title, year, director, genre, duration, language, "
        "rating, media, cover, synopsis, location, internal_code) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) RETURNING *",
        body.title, body.original_title, body.year, body.director, body.genre,
        body.duration, body.language, body.rating, body.media, body.cover,
        body.synopsis, body.location, body.internal_code,
    )
    return dict(row)


@router.put("/inventory/{film_id}")
async def update_film(film_id: int, body: FilmCreate, user=Depends(_staff)):
    pool = get_pool()
    row = await pool.fetchrow(
        "UPDATE films SET title=$1, original_title=$2, year=$3, director=$4, genre=$5, "
        "duration=$6, language=$7, rating=$8, media=$9, cover=$10, synopsis=$11, "
        "location=$12, internal_code=$13 WHERE id=$14 RETURNING *",
        body.title, body.original_title, body.year, body.director, body.genre,
        body.duration, body.language, body.rating, body.media, body.cover,
        body.synopsis, body.location, body.internal_code, film_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Filme não encontrado")
    return dict(row)


@router.delete("/inventory/{film_id}")
async def delete_film(film_id: int, user=Depends(_staff)):
    pool = get_pool()
    await pool.execute("UPDATE films SET status = 'inactive' WHERE id = $1", film_id)
    return {"ok": True}


# ── Loans ────────────────────────────────────────────────────────────────────

@router.get("/loans")
async def list_loans(user=Depends(_staff)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT l.id, l.status, l.borrowed_at, l.due_at, l.returned_at, "
        "f.title as film, f.cover, c.code as copy, "
        "p.full_name as user_name, p.registration "
        "FROM loans l "
        "JOIN copies c ON c.id = l.copy_id "
        "JOIN films f ON f.id = c.film_id "
        "JOIN profiles p ON p.id = l.user_id "
        "ORDER BY l.borrowed_at DESC LIMIT 100"
    )
    return [dict(r) for r in rows]


class LoanCreate(BaseModel):
    user_registration: str
    copy_code: str
    days: int = 7


@router.post("/loans")
async def create_loan(body: LoanCreate, user=Depends(_staff)):
    pool = get_pool()
    profile = await pool.fetchrow(
        "SELECT id FROM profiles WHERE registration = $1", body.user_registration
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    copy = await pool.fetchrow(
        "SELECT id, status FROM copies WHERE code = $1", body.copy_code
    )
    if not copy:
        raise HTTPException(status_code=404, detail="Exemplar não encontrado")
    if copy["status"] != "available":
        raise HTTPException(status_code=400, detail="Exemplar não disponível")

    async with pool.acquire() as conn:
        async with conn.transaction():
            loan = await conn.fetchrow(
                "INSERT INTO loans (user_id, copy_id, due_at) "
                "VALUES ($1, $2, NOW() + ($3 || ' days')::interval) RETURNING id, status, borrowed_at, due_at",
                profile["id"], copy["id"], str(body.days),
            )
            await conn.execute(
                "UPDATE copies SET status = 'borrowed' WHERE id = $1", copy["id"]
            )
    return dict(loan)


@router.post("/loans/{loan_id}/return")
async def return_loan(loan_id: int, user=Depends(_staff)):
    pool = get_pool()
    loan = await pool.fetchrow(
        "SELECT l.id, l.copy_id FROM loans l WHERE l.id = $1 AND l.status = 'borrowed'", loan_id
    )
    if not loan:
        raise HTTPException(status_code=404, detail="Empréstimo não encontrado")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE loans SET status = 'returned', returned_at = NOW() WHERE id = $1", loan_id
            )
            await conn.execute(
                "UPDATE copies SET status = 'available' WHERE id = $1", loan["copy_id"]
            )
    return {"ok": True}


# ── Reservations ─────────────────────────────────────────────────────────────

@router.get("/reservations")
async def list_reservations(user=Depends(_staff)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT r.id, r.status, r.requested_at, r.pickup_until, "
        "f.title as film, f.media, f.cover, "
        "p.full_name as user_name, p.registration "
        "FROM reservations r "
        "JOIN films f ON f.id = r.film_id "
        "JOIN profiles p ON p.id = r.user_id "
        "WHERE r.status != 'cancelled' "
        "ORDER BY r.requested_at DESC LIMIT 100"
    )
    return [dict(r) for r in rows]


@router.put("/reservations/{reservation_id}/confirm")
async def confirm_reservation(reservation_id: int, user=Depends(_staff)):
    pool = get_pool()
    row = await pool.fetchrow(
        "UPDATE reservations SET status = 'confirmed', pickup_until = NOW() + INTERVAL '3 days' "
        "WHERE id = $1 AND status = 'waiting' RETURNING id",
        reservation_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Reserva não encontrada")
    return {"ok": True}


# ── Users ────────────────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(user=Depends(_staff)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT p.id, p.full_name, p.role, p.registration, p.course, p.phone, u.email, p.created_at, "
        "(SELECT COUNT(*) FROM loans l WHERE l.user_id = p.id AND l.status = 'borrowed') as active_loans "
        "FROM profiles p JOIN users u ON u.id = p.id ORDER BY p.full_name ASC"
    )
    return [dict(r) for r in rows]


@router.get("/users/by-registration/{registration}")
async def get_user_by_registration(registration: str, user=Depends(_staff)):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT p.id, p.full_name, p.role, p.registration, p.course, p.phone, u.email "
        "FROM profiles p JOIN users u ON u.id = p.id WHERE p.registration = $1",
        registration,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return dict(row)


# ── Rooms ────────────────────────────────────────────────────────────────────

@router.get("/rooms")
async def list_rooms(user=Depends(_staff)):
    pool = get_pool()
    rows = await pool.fetch("SELECT * FROM rooms ORDER BY name ASC")
    return [dict(r) for r in rows]


# ── Events ───────────────────────────────────────────────────────────────────

@router.get("/events")
async def list_events(user=Depends(_staff)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT e.*, f.title as film_title FROM events e "
        "LEFT JOIN films f ON f.id = e.film_id ORDER BY e.starts_at DESC"
    )
    return [dict(r) for r in rows]


class EventCreate(BaseModel):
    title: str
    event_type: Optional[str] = None
    film_id: Optional[int] = None
    location: Optional[str] = None
    starts_at: Optional[str] = None
    slots: int = 0
    description: Optional[str] = None
    certificate: bool = False
    status: str = "draft"


@router.post("/events")
async def create_event(body: EventCreate, user=Depends(_staff)):
    pool = get_pool()
    row = await pool.fetchrow(
        "INSERT INTO events (title, event_type, film_id, location, starts_at, slots, "
        "description, certificate, status) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *",
        body.title, body.event_type, body.film_id, body.location,
        body.starts_at, body.slots, body.description, body.certificate, body.status,
    )
    return dict(row)


@router.put("/events/{event_id}")
async def update_event(event_id: int, body: EventCreate, user=Depends(_staff)):
    pool = get_pool()
    row = await pool.fetchrow(
        "UPDATE events SET title=$1, event_type=$2, film_id=$3, location=$4, starts_at=$5, "
        "slots=$6, description=$7, certificate=$8, status=$9 WHERE id=$10 RETURNING *",
        body.title, body.event_type, body.film_id, body.location,
        body.starts_at, body.slots, body.description, body.certificate, body.status, event_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    return dict(row)


# ── Professor requests ────────────────────────────────────────────────────────

@router.get("/requests")
async def list_requests(user=Depends(_staff)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT pr.id, pr.request_type, pr.payload, pr.status, pr.staff_notes, pr.created_at, "
        "p.full_name as professor_name, p.registration "
        "FROM professor_requests pr "
        "JOIN profiles p ON p.id = pr.professor_id "
        "ORDER BY pr.created_at DESC LIMIT 100"
    )
    return [dict(r) for r in rows]


class RequestUpdate(BaseModel):
    status: str
    staff_notes: Optional[str] = None


@router.put("/requests/{request_id}")
async def update_request(request_id: int, body: RequestUpdate, user=Depends(_staff)):
    pool = get_pool()
    row = await pool.fetchrow(
        "UPDATE professor_requests SET status=$1, staff_notes=$2 WHERE id=$3 RETURNING id",
        body.status, body.staff_notes, request_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada")
    return {"ok": True}
