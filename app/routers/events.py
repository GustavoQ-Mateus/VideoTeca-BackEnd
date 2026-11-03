from fastapi import APIRouter
from app.db.postgres import get_pool

router = APIRouter()


@router.get("")
async def list_events():
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT e.id, e.title, e.event_type, e.location, e.starts_at, e.slots, e.enrolled, "
        "e.description, e.certificate, e.status, e.ends_at, e.film_id, f.title as film_title, f.cover as film_cover, "
        "f.backdrop as film_backdrop, f.year as film_year, f.director as film_director, f.duration as film_duration "
        "FROM events e LEFT JOIN films f ON f.id = e.film_id "
        "WHERE e.status = 'published' ORDER BY e.starts_at ASC"
    )
    return [dict(r) for r in rows]


@router.get("/{event_id}")
async def get_event(event_id: int):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT e.*, f.title as film_title FROM events e "
        "LEFT JOIN films f ON f.id = e.film_id WHERE e.id = $1 AND e.status = 'published'",
        event_id,
    )
    if not row:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Evento não encontrado")
    return dict(row)
