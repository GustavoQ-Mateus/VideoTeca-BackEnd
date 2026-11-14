import io
import warnings
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Response
from pydantic import BaseModel, Field, ConfigDict, model_validator
from PIL import Image, UnidentifiedImageError
from app.db.postgres import get_pool
from app.core.security import require_role
from app.rights import publication_error

router = APIRouter()
staff = require_role("funcionario", "admin")
LOCAL_TZ = timezone(timedelta(hours=-3))  # America/Fortaleza (sem horário de verão)
RightsStatus = Literal["pending", "authorized", "denied", "public_domain"]


def local_today():
    return datetime.now(LOCAL_TZ).date()


def rights_error(status, holder, valid_until, today):
    if status == "authorized" and not holder:
        return "Informe o titular dos direitos para marcar o filme como autorizado"
    if status == "authorized" and valid_until and valid_until < today:
        return "A validade da autorização já expirou"
    return None


class FilmInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    year: int | None = Field(default=None, ge=1888, le=2100)
    director: str = Field(default="", max_length=200)
    genre: str = Field(default="", max_length=80)
    duration: str = Field(default="", max_length=40)
    rating: str = Field(default="Livre", max_length=20)
    synopsis: str = Field(default="", max_length=10000)
    # Rights are a group: omitted rights_status keeps the stored authorization untouched.
    rights_status: RightsStatus | None = None
    rights_holder: str = Field(default="", max_length=200)
    license_type: str = Field(default="", max_length=80)
    rights_valid_until: date | None = None
    rights_notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def valid_rights(self):
        error = rights_error(
            self.rights_status, self.rights_holder, self.rights_valid_until, local_today()
        )
        if error:
            raise ValueError(error)
        return self

    def rights(self):
        return (
            self.rights_status or "pending",
            self.rights_holder,
            self.license_type,
            self.rights_valid_until,
            self.rights_notes,
        )


@router.get("/staff/films")
async def films(user=Depends(staff)):
    return [
        dict(r)
        for r in await get_pool().fetch(
            "SELECT * FROM films WHERE status<>'inactive' ORDER BY title"
        )
    ]


@router.post("/staff/films", status_code=201)
async def create(body: FilmInput, user=Depends(staff)):
    return dict(
        await get_pool().fetchrow(
            """INSERT INTO films(title,year,director,genre,duration,rating,synopsis,
        rights_status,rights_holder,license_type,rights_valid_until,rights_notes)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING *""",
            body.title,
            body.year,
            body.director,
            body.genre,
            body.duration,
            body.rating,
            body.synopsis,
            *body.rights(),
        )
    )


@router.put("/staff/films/{film_id}")
async def update(film_id: int, body: FilmInput, user=Depends(staff)):
    keep = body.rights_status is None
    async with get_pool().acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """UPDATE films SET title=$2,year=$3,director=$4,genre=$5,duration=$6,rating=$7,synopsis=$8,
            rights_status=CASE WHEN $9 THEN rights_status ELSE $10 END,
            rights_holder=CASE WHEN $9 THEN rights_holder ELSE $11 END,
            license_type=CASE WHEN $9 THEN license_type ELSE $12 END,
            rights_valid_until=CASE WHEN $9 THEN rights_valid_until ELSE $13 END,
            rights_notes=CASE WHEN $9 THEN rights_notes ELSE $14 END
            WHERE id=$1 AND status<>'inactive' RETURNING *""",
            film_id,
            body.title,
            body.year,
            body.director,
            body.genre,
            body.duration,
            body.rating,
            body.synopsis,
            keep,
            *body.rights(),
        )
        if not row:
            raise HTTPException(404, "Filme não encontrado")
        if not keep:
            await ensure_future_sessions_covered(conn, row)
    return dict(row)


async def ensure_future_sessions_covered(conn, film):
    """Revoking/shortening an authorization must not leave future published sessions uncovered."""
    pending = await conn.fetchrow(
        """SELECT MAX((COALESCE(ends_at,starts_at) AT TIME ZONE 'America/Fortaleza')::date) AS ends_on,
        EXISTS(SELECT 1 FROM film_rights_proofs p WHERE p.film_id=$1) AS has_proof
        FROM events WHERE film_id=$1 AND status='published' AND COALESCE(ends_at,starts_at)>NOW()""",
        film["id"],
    )
    if pending["ends_on"] and publication_error(
        film["rights_status"], film["rights_valid_until"], pending["ends_on"], pending["has_proof"]
    ):
        raise HTTPException(
            409, "Despublique as sessões futuras antes de alterar a autorização do filme"
        )


@router.delete("/staff/films/{film_id}")
async def remove(film_id: int, user=Depends(staff)):
    async with get_pool().acquire() as conn, conn.transaction():
        film = await conn.fetchrow(
            "SELECT id FROM films WHERE id=$1 FOR UPDATE", film_id
        )
        if not film:
            raise HTTPException(404, "Filme não encontrado")
        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM events WHERE film_id=$1 AND status='published' AND starts_at>NOW())",
            film_id,
        ):
            raise HTTPException(
                409, "Despublique as sessões futuras antes de excluir o filme"
            )
        await conn.execute("UPDATE films SET status='inactive' WHERE id=$1", film_id)
    return {"ok": True}


def clean_poster(data):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if (
                    image.format not in ("JPEG", "PNG", "WEBP")
                    or image.width * image.height > 20000000
                ):
                    raise ValueError()
                image.load()
                image.thumbnail((1600, 2400))
                output = io.BytesIO()
                image.convert("RGB").save(output, format="JPEG", quality=85)
                return output.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise HTTPException(
            422, "Envie uma imagem PNG, JPEG ou WebP válida, com até 20 megapixels"
        )


@router.post("/staff/films/{film_id}/poster")
async def upload_poster(
    film_id: int, file: UploadFile = File(...), user=Depends(staff)
):
    data = await file.read(5242881)
    await file.close()
    if len(data) > 5242880:
        raise HTTPException(413, "Cartaz deve ter no máximo 5 MB")
    data = clean_poster(data)
    cover = f"/api/v1/posters/{film_id}"
    async with get_pool().acquire() as conn, conn.transaction():
        if not await conn.fetchval(
            "UPDATE films SET cover=$2 WHERE id=$1 AND status<>'inactive' RETURNING id",
            film_id,
            cover,
        ):
            raise HTTPException(404, "Filme não encontrado")
        await conn.execute(
            """INSERT INTO film_posters(film_id,content,media_type) VALUES($1,$2,'image/jpeg')
           ON CONFLICT(film_id) DO UPDATE SET content=EXCLUDED.content,media_type=EXCLUDED.media_type""",
            film_id,
            data,
        )
    return {"cover": cover}


@router.get("/posters/{film_id}")
async def poster(film_id: int):
    row = await get_pool().fetchrow(
        "SELECT p.* FROM film_posters p JOIN films f ON f.id=p.film_id WHERE p.film_id=$1 AND f.status<>'inactive'",
        film_id,
    )
    if not row:
        raise HTTPException(404, "Cartaz não encontrado")
    return Response(
        bytes(row["content"]),
        media_type=row["media_type"],
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=300",
        },
    )
