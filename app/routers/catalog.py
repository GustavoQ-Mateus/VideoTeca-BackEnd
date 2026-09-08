from fastapi import APIRouter, HTTPException

from app.db.postgres import get_pool

router = APIRouter()


@router.get("/films")
async def list_films():
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT * FROM films WHERE status != 'inactive' ORDER BY title ASC"
    )
    return [dict(r) for r in rows]


@router.get("/films/{film_id}")
async def get_film(film_id: int):
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM films WHERE id = $1", film_id)
    if not row:
        raise HTTPException(status_code=404, detail="Filme não encontrado")
    return dict(row)
