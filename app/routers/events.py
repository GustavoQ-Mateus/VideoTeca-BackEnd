from fastapi import APIRouter, HTTPException

from app.db.postgres import get_pool

router = APIRouter()

# Sessão "futura" = ainda não terminou (ou não começou, quando não há horário de término).
UPCOMING = "COALESCE(e.ends_at, e.starts_at) >= NOW()"


@router.get("")
async def list_events():
    rows = await get_pool().fetch(
        "SELECT e.id, e.title, e.event_type, e.location, e.starts_at, e.ends_at, e.slots, e.enrolled, "
        "e.description, e.certificate, e.status, e.film_id, f.title as film_title, f.cover as film_cover, "
        "f.backdrop as film_backdrop, f.year as film_year, f.director as film_director, f.duration as film_duration "
        f"FROM events e LEFT JOIN films f ON f.id = e.film_id "
        f"WHERE e.status = 'published' AND {UPCOMING} ORDER BY e.starts_at ASC"
    )
    return [dict(r) for r in rows]


@router.get("/{event_id}")
async def get_event(event_id: int):
    row = await get_pool().fetchrow(
        "SELECT e.*, f.title as film_title, f.cover as film_cover FROM events e "
        "LEFT JOIN films f ON f.id = e.film_id WHERE e.id = $1 AND e.status = 'published'",
        event_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    return dict(row)
