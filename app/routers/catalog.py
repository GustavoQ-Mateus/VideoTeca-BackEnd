from fastapi import APIRouter, HTTPException, Query

from app.db.postgres import get_pool

router = APIRouter()
# Internal rights paperwork is not part of the public catalog.
PRIVATE_FIELDS = {"rights_holder", "license_type", "rights_notes", "rights_valid_until"}


def public_film(row):
    return {k: v for k, v in dict(row).items() if k not in PRIVATE_FIELDS}


def like_pattern(term: str) -> str:
    """Escapes LIKE wildcards so user input is matched literally."""
    escaped = term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


@router.get("/films")
async def list_films(
    q: str | None = Query(None, max_length=100, description="Busca por título, título original, diretor ou gênero"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
):
    term = (q or "").strip()
    rows = await get_pool().fetch(
        """SELECT * FROM films WHERE status <> 'inactive'
        AND ($1::text IS NULL OR title ILIKE $1 OR original_title ILIKE $1 OR director ILIKE $1 OR genre ILIKE $1)
        ORDER BY title ASC, id ASC LIMIT $2 OFFSET $3""",
        like_pattern(term) if term else None,
        limit,
        offset,
    )
    return [public_film(r) for r in rows]


@router.get("/films/{film_id}")
async def get_film(film_id: int):
    row = await get_pool().fetchrow(
        "SELECT * FROM films WHERE id = $1 AND status <> 'inactive'", film_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Filme não encontrado")
    return public_film(row)
