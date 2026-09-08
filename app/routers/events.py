from fastapi import APIRouter
from app.db.postgres import get_pool

router = APIRouter()


@router.get("")
async def list_events():
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT e.id, e.title, e.event_type, e.location, e.starts_at, e.slots, e.enrolled, "
        "e.description, e.certificate, e.status, f.title as film_title, f.cover as film_cover "
        "FROM events e LEFT JOIN films f ON f.id = e.film_id "
        "WHERE e.status = 'published' ORDER BY e.starts_at ASC"
    )
    return [dict(r) for r in rows]


@router.get("/{event_id}")
async def get_event(event_id: int):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT e.*, f.title as film_title FROM events e "
        "LEFT JOIN films f ON f.id = e.film_id WHERE e.id = $1",
        event_id,
    )
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    return dict(row)
