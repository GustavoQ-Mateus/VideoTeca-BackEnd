import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import require_role
from app.db.postgres import get_pool
from app.notify import get_settings
from app.routers.catalog import AVAILABILITY_COLUMNS, AVAILABILITY_JOINS
from app.routers.events import UPCOMING
from app.routers.me import mark_notification_read

router = APIRouter()
_student = require_role("aluno")
FILM_CARD = f"f.id, f.title, f.year, f.director, f.genre, f.cover, {AVAILABILITY_COLUMNS}"


@router.get("/profile")
async def get_profile(user=Depends(_student)):
    return user


@router.get("/loans")
async def get_loans(user=Depends(_student)):
    rows = await get_pool().fetch(
        "SELECT l.id, CASE WHEN l.returned_at IS NULL AND l.due_at < NOW() THEN 'late' ELSE l.status END AS status, "
        "l.borrowed_at, l.due_at, l.returned_at, l.renewals, f.id as film_id, "
        "f.title as film, f.director, f.cover, c.code as copy "
        "FROM loans l JOIN copies c ON c.id = l.copy_id JOIN films f ON f.id = c.film_id "
        "WHERE l.user_id = $1 ORDER BY l.borrowed_at DESC",
        user["id"],
    )
    return [dict(r) for r in rows]


@router.post("/loans/{loan_id}/renew")
async def renew_loan(loan_id: int, user=Depends(_student)):
    async with get_pool().acquire() as conn, conn.transaction():
        loan = await conn.fetchrow(
            "SELECT l.id, l.user_id, l.returned_at, l.due_at, l.renewals, c.film_id, "
            "l.due_at < NOW() AS late FROM loans l JOIN copies c ON c.id = l.copy_id WHERE l.id = $1 FOR UPDATE OF l",
            loan_id,
        )
        if not loan or loan["user_id"] != user["id"]:
            raise HTTPException(404, "Empréstimo não encontrado")
        if loan["returned_at"] is not None:
            raise HTTPException(409, "Este empréstimo já foi devolvido")
        if loan["late"]:
            raise HTTPException(409, "Empréstimo em atraso não pode ser renovado; procure o balcão da Videoteca")
        rules = await conn.fetchrow("SELECT loan_days, max_renewals FROM films WHERE id = $1", loan["film_id"])
        max_renewals = rules["max_renewals"] if rules and rules["max_renewals"] is not None else 1
        if loan["renewals"] >= max_renewals:
            raise HTTPException(
                409,
                "Este filme não pode ser renovado" if max_renewals == 0
                else f"Este empréstimo já foi renovado {max_renewals} {'vez' if max_renewals == 1 else 'vezes'}",
            )
        waiting = await conn.fetchval(
            "SELECT 1 FROM reservations WHERE film_id = $1 AND status = 'waiting' AND user_id <> $2 LIMIT 1",
            loan["film_id"], user["id"],
        )
        if waiting:
            raise HTTPException(409, "Há outra pessoa aguardando este filme; não é possível renovar")
        days = (rules["loan_days"] if rules else None) or (await get_settings(conn))["loan_days"]
        row = await conn.fetchrow(
            "UPDATE loans SET due_at = COALESCE(due_at, NOW()) + make_interval(days => $2), renewals = renewals + 1 "
            "WHERE id = $1 RETURNING id, due_at, renewals",
            loan_id, days,
        )
    return dict(row)


@router.get("/reservations")
async def get_reservations(user=Depends(_student)):
    rows = await get_pool().fetch(
        "SELECT r.id, r.status, r.requested_at, r.pickup_until, f.id as film_id, "
        "f.title as film, f.director, f.cover, f.media "
        "FROM reservations r JOIN films f ON f.id = r.film_id "
        "WHERE r.user_id = $1 ORDER BY r.requested_at DESC",
        user["id"],
    )
    return [dict(r) for r in rows]


class ReservationRequest(BaseModel):
    film_id: int


@router.post("/reservations")
async def create_reservation(body: ReservationRequest, user=Depends(_student)):
    async with get_pool().acquire() as conn, conn.transaction():
        # Trava o perfil: contagem e inserção não correm em paralelo para o mesmo aluno.
        await conn.execute("SELECT 1 FROM profiles WHERE id = $1 FOR UPDATE", user["id"])
        film = await conn.fetchrow(
            "SELECT local_only, queue_limit FROM films WHERE id = $1 AND status <> 'inactive'", body.film_id
        )
        if not film:
            raise HTTPException(404, "Filme não encontrado")
        if film["local_only"]:
            raise HTTPException(409, "Este filme é só para assistir na Videoteca; procure a equipe para agendar")
        if film["queue_limit"]:
            waiting = await conn.fetchval(
                "SELECT COUNT(*) FROM reservations WHERE film_id = $1 AND status = 'waiting'", body.film_id
            )
            if waiting >= film["queue_limit"]:
                raise HTTPException(409, "A fila de reservas deste filme está cheia no momento")
        active = await conn.fetch(
            "SELECT film_id FROM reservations WHERE user_id = $1 AND status IN ('waiting', 'ready')", user["id"]
        )
        if any(r["film_id"] == body.film_id for r in active):
            raise HTTPException(400, "Reserva já existente para este filme")
        limit = (await get_settings(conn))["max_reservations"]
        if len(active) >= limit:
            raise HTTPException(409, f"Limite de {limit} reservas ativas atingido")
        row = await conn.fetchrow(
            "INSERT INTO reservations (user_id, film_id) VALUES ($1, $2) RETURNING id, status, requested_at",
            user["id"], body.film_id,
        )
    return dict(row)


@router.delete("/reservations/{reservation_id}")
async def cancel_reservation(reservation_id: int, user=Depends(_student)):
    updated = await get_pool().fetchval(
        "UPDATE reservations SET status = 'cancelled' "
        "WHERE id = $1 AND user_id = $2 AND status IN ('waiting', 'ready') RETURNING id",
        reservation_id, user["id"],
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Reserva não encontrada")
    return {"ok": True}


EVENT_COLUMNS = (
    "e.id, e.title, e.event_type, e.location, e.starts_at, e.ends_at, e.slots, e.enrolled, "
    "e.description, e.certificate, e.status, e.film_id, f.title as film_title, f.cover as film_cover, "
    "(er.user_id IS NOT NULL) AS enrolled_by_me, COALESCE(er.present, false) AS present "
    "FROM events e LEFT JOIN films f ON f.id = e.film_id "
    "LEFT JOIN event_registrations er ON er.event_id = e.id AND er.user_id = $1 "
)


@router.get("/events")
async def get_events(user=Depends(_student)):
    rows = await get_pool().fetch(
        f"SELECT {EVENT_COLUMNS} WHERE e.status = 'published' AND {UPCOMING} ORDER BY e.starts_at ASC", user["id"]
    )
    return [dict(r) for r in rows]


async def _my_event(event_id: int, user):
    # Eventos já encerrados continuam visíveis para quem se inscreveu.
    row = await get_pool().fetchrow(
        f"SELECT {EVENT_COLUMNS} WHERE e.id = $2 AND (e.status = 'published' OR (e.status = 'finished' AND er.user_id IS NOT NULL))",
        user["id"], event_id,
    )
    if not row:
        raise HTTPException(404, "Evento não encontrado")
    return row


@router.get("/events/{event_id}")
async def get_event(event_id: int, user=Depends(_student)):
    return dict(await _my_event(event_id, user))


@router.post("/events/{event_id}/enroll")
async def enroll_event(event_id: int, user=Depends(_student)):
    try:
        async with get_pool().acquire() as conn, conn.transaction():
            if await conn.fetchval(
                "SELECT 1 FROM event_registrations WHERE event_id = $1 AND user_id = $2", event_id, user["id"]
            ):
                raise HTTPException(409, "Já inscrito neste evento")
            # Reserva a vaga e a inscrição na mesma transação: não passa do limite sob concorrência.
            taken = await conn.fetchval(
                "UPDATE events SET enrolled = enrolled + 1 WHERE id = $1 AND status = 'published' "
                "AND enrolled < slots AND starts_at > NOW() RETURNING id",
                event_id,
            )
            if not taken:
                event = await conn.fetchrow(
                    "SELECT starts_at > NOW() AS upcoming FROM events WHERE id = $1 AND status = 'published'", event_id
                )
                if not event:
                    raise HTTPException(404, "Evento não encontrado")
                if not event["upcoming"]:
                    raise HTTPException(422, "Inscrições encerradas: o evento já começou")
                raise HTTPException(409, "Sessão lotada")
            await conn.execute(
                "INSERT INTO event_registrations (event_id, user_id) VALUES ($1, $2)", event_id, user["id"]
            )
            enrolled = await _sync_enrolled(conn, event_id)
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "Já inscrito neste evento")
    return {"ok": True, "enrolled": enrolled}


async def _sync_enrolled(conn, event_id: int) -> int:
    # Contador = inscrições reais (compatível com o trigger da migration 015, se existir).
    return await conn.fetchval(
        "UPDATE events SET enrolled = (SELECT COUNT(*) FROM event_registrations WHERE event_id = $1) "
        "WHERE id = $1 RETURNING enrolled",
        event_id,
    )


@router.delete("/events/{event_id}/enroll")
async def unenroll_event(event_id: int, user=Depends(_student)):
    async with get_pool().acquire() as conn, conn.transaction():
        reg = await conn.fetchrow(
            "SELECT er.id, er.present, e.starts_at <= NOW() AS started FROM event_registrations er "
            "JOIN events e ON e.id = er.event_id WHERE er.event_id = $1 AND er.user_id = $2 FOR UPDATE OF e, er",
            event_id, user["id"],
        )
        if not reg:
            raise HTTPException(404, "Inscrição não encontrada")
        if reg["present"]:
            raise HTTPException(409, "Presença já registrada; a inscrição não pode ser cancelada")
        if reg["started"]:
            raise HTTPException(409, "O evento já começou; a inscrição não pode ser cancelada")
        await conn.execute("DELETE FROM event_registrations WHERE id = $1", reg["id"])
        enrolled = await _sync_enrolled(conn, event_id)
    return {"ok": True, "enrolled": enrolled}


@router.get("/favorites")
async def get_favorites(user=Depends(_student)):
    rows = await get_pool().fetch(
        f"SELECT {FILM_CARD} FROM favorites fv JOIN films f ON f.id = fv.film_id {AVAILABILITY_JOINS} "
        "WHERE fv.user_id = $1 AND f.status <> 'inactive' ORDER BY fv.created_at DESC, f.title",
        user["id"],
    )
    return [dict(r) for r in rows]


@router.post("/favorites/{film_id}", status_code=201)
async def add_favorite(film_id: int, user=Depends(_student)):
    pool = get_pool()
    if not await pool.fetchval("SELECT 1 FROM films WHERE id = $1 AND status <> 'inactive'", film_id):
        raise HTTPException(404, "Filme não encontrado")
    await pool.execute(
        "INSERT INTO favorites (user_id, film_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", user["id"], film_id
    )
    return {"ok": True, "film_id": film_id}


@router.delete("/favorites/{film_id}")
async def remove_favorite(film_id: int, user=Depends(_student)):
    await get_pool().execute("DELETE FROM favorites WHERE user_id = $1 AND film_id = $2", user["id"], film_id)
    return {"ok": True, "film_id": film_id}


@router.get("/notifications")
async def get_notifications(user=Depends(_student)):
    rows = await get_pool().fetch(
        "SELECT id, title, body, read, created_at FROM notifications "
        "WHERE user_id = $1 ORDER BY created_at DESC LIMIT 50",
        user["id"],
    )
    return [dict(r) for r in rows]


@router.put("/notifications/{notif_id}/read")
async def mark_read(notif_id: int, user=Depends(_student)):
    return await mark_notification_read(notif_id, user)


@router.get("/stats")
async def get_stats(user=Depends(_student)):
    row = await get_pool().fetchrow(
        """SELECT
          (SELECT COUNT(*) FROM loans WHERE user_id = $1 AND returned_at IS NULL) AS active_loans,
          (SELECT COUNT(*) FROM reservations WHERE user_id = $1 AND status IN ('waiting', 'ready')) AS reservations,
          (SELECT COUNT(*) FROM notifications WHERE user_id = $1 AND read = false) AS notifications,
          (SELECT COUNT(*) FROM event_registrations WHERE user_id = $1) AS enrolled_events""",
        user["id"],
    )
    return dict(row)
