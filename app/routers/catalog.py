from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.db.postgres import get_pool

router = APIRouter()
# Internal rights paperwork is not part of the public catalog.
PRIVATE_FIELDS = {"rights_holder", "license_type", "rights_notes", "rights_valid_until", "loans_count"}

# Disponibilidade real por cópias: cópia 'available' sem empréstimo aberto, menos reservas 'ready' do filme.
# Use com "FROM films f" e selecione AVAILABILITY_COLUMNS.
AVAILABILITY_JOINS = """
LEFT JOIN LATERAL (
  SELECT COUNT(*)::int AS total_copies,
         COUNT(*) FILTER (WHERE c.status = 'available' AND NOT EXISTS (
           SELECT 1 FROM loans l WHERE l.copy_id = c.id AND l.returned_at IS NULL))::int AS free_copies
  FROM copies c WHERE c.film_id = f.id) cp ON TRUE
LEFT JOIN LATERAL (
  SELECT COUNT(*)::int AS ready FROM reservations r WHERE r.film_id = f.id AND r.status = 'ready') rr ON TRUE
"""
AVAILABLE_EXPR = "GREATEST(cp.free_copies - rr.ready, 0)"
AVAILABILITY_COLUMNS = f"""cp.total_copies, {AVAILABLE_EXPR} AS available_copies,
CASE WHEN f.status = 'maintenance' THEN 'maintenance' WHEN {AVAILABLE_EXPR} > 0 THEN 'available' ELSE 'borrowed' END
  AS availability"""
LOANS_COUNT_JOIN = """
LEFT JOIN LATERAL (
  SELECT COUNT(*)::int AS loans_count FROM loans l JOIN copies c ON c.id = l.copy_id WHERE c.film_id = f.id) lc ON TRUE
"""
SORTS = {
    "title": "f.title ASC, f.id ASC",
    "recent": "f.year DESC NULLS LAST, f.title ASC, f.id ASC",
    "popular": "lc.loans_count DESC, f.title ASC, f.id ASC",
}


def public_film(row):
    return {k: v for k, v in dict(row).items() if k not in PRIVATE_FIELDS}


def like_pattern(term: str) -> str:
    """Escapes LIKE wildcards so user input is matched literally."""
    escaped = term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


@router.get("/films")
async def list_films(
    q: str | None = Query(None, max_length=100, description="Busca por título, título original, diretor ou gênero"),
    sort: Literal["title", "recent", "popular"] = Query("title", description="title | recent (ano) | popular (empréstimos)"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
):
    term = (q or "").strip()
    # A ordenação vem de um dicionário fixo (nunca da entrada do usuário).
    rows = await get_pool().fetch(
        f"""SELECT f.*, {AVAILABILITY_COLUMNS} FROM films f {LOANS_COUNT_JOIN} {AVAILABILITY_JOINS}
        WHERE f.status <> 'inactive'
        AND ($1::text IS NULL OR f.title ILIKE $1 OR f.original_title ILIKE $1 OR f.director ILIKE $1 OR f.genre ILIKE $1)
        ORDER BY {SORTS[sort]} LIMIT $2 OFFSET $3""",
        like_pattern(term) if term else None,
        limit,
        offset,
    )
    return [public_film(r) for r in rows]


@router.get("/films/{film_id}")
async def get_film(film_id: int):
    row = await get_pool().fetchrow(
        f"SELECT f.*, {AVAILABILITY_COLUMNS} FROM films f {AVAILABILITY_JOINS} WHERE f.id = $1 AND f.status <> 'inactive'",
        film_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Filme não encontrado")
    return public_film(row)
