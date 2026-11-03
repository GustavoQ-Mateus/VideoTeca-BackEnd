"""Comprovantes de autorização de exibição (T21.3)."""

import hashlib
import re
import unicodedata
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Response
from app.db.postgres import get_pool
from app.core.security import require_role
from app.rights import publication_error

router = APIRouter(prefix="/staff/films/{film_id}/rights-proofs")
staff = require_role("funcionario", "admin")
MAX_PROOF_BYTES = 10 * 1024 * 1024
SIGNATURES = (
    (b"%PDF-", "application/pdf", ".pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
)
EXTENSIONS = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
COLUMNS = """p.id,p.filename,p.media_type,octet_length(p.content) AS size,p.sha256,p.uploaded_at,
 COALESCE(u.full_name,'') AS uploaded_by_name"""


def detect_media_type(data: bytes):
    """Identifies the file by magic bytes only; the client-declared content-type is ignored."""
    for magic, media_type, _ in SIGNATURES:
        if data.startswith(magic):
            return media_type
    return None


def sanitize_filename(name, media_type):
    name = unicodedata.normalize("NFKC", name or "")
    name = re.split(r"[/\\]", name)[-1]
    name = re.sub(r"[\x00-\x1f\x7f\"';<>|*?:]", "", name).strip(" .")
    extension = next(ext for _, mt, ext in SIGNATURES if mt == media_type)
    stem, dot, ext = name.rpartition(".")
    if not dot or EXTENSIONS.get("." + ext.lower()) != media_type:
        stem = name
    else:
        extension = "." + ext.lower()
    stem = stem.strip(" .")[: 120 - len(extension)] or "comprovante"
    return stem + extension


def content_disposition(filename):
    ascii_name = filename.encode("ascii", "ignore").decode() or "comprovante"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


async def active_film(conn, film_id, lock=False):
    if not await conn.fetchval(
        "SELECT id FROM films WHERE id=$1 AND status<>'inactive'" + (" FOR UPDATE" if lock else ""),
        film_id,
    ):
        raise HTTPException(404, "Filme não encontrado")


@router.get("")
async def listing(film_id: int, user=Depends(staff)):
    async with get_pool().acquire() as conn:
        await active_film(conn, film_id)
        rows = await conn.fetch(
            f"SELECT {COLUMNS} FROM film_rights_proofs p LEFT JOIN profiles u ON u.id=p.uploaded_by "
            "WHERE p.film_id=$1 ORDER BY p.uploaded_at DESC,p.id DESC",
            film_id,
        )
    return [dict(r) for r in rows]


@router.post("", status_code=201)
async def upload(film_id: int, file: UploadFile = File(...), user=Depends(staff)):
    data = await file.read(MAX_PROOF_BYTES + 1)
    await file.close()
    if len(data) > MAX_PROOF_BYTES:
        raise HTTPException(413, "Comprovante deve ter no máximo 10 MB")
    media_type = detect_media_type(data)
    if not media_type:
        raise HTTPException(422, "Envie o comprovante em PDF, PNG ou JPEG")
    filename = sanitize_filename(file.filename, media_type)
    async with get_pool().acquire() as conn, conn.transaction():
        await active_film(conn, film_id)
        proof_id = await conn.fetchval(
            """INSERT INTO film_rights_proofs(film_id,filename,media_type,content,sha256,uploaded_by)
            VALUES($1,$2,$3,$4,$5,$6) RETURNING id""",
            film_id,
            filename,
            media_type,
            data,
            hashlib.sha256(data).hexdigest(),
            user["id"],
        )
        row = await conn.fetchrow(
            f"SELECT {COLUMNS} FROM film_rights_proofs p LEFT JOIN profiles u ON u.id=p.uploaded_by WHERE p.id=$1",
            proof_id,
        )
    return dict(row)


@router.get("/{proof_id}")
async def download(film_id: int, proof_id: int, user=Depends(staff)):
    row = await get_pool().fetchrow(
        """SELECT p.filename,p.media_type,p.content FROM film_rights_proofs p JOIN films f ON f.id=p.film_id
        WHERE p.id=$1 AND p.film_id=$2 AND f.status<>'inactive'""",
        proof_id,
        film_id,
    )
    if not row:
        raise HTTPException(404, "Comprovante não encontrado")
    return Response(
        bytes(row["content"]),
        media_type=row["media_type"],
        headers={
            "Content-Disposition": content_disposition(row["filename"]),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/{proof_id}")
async def remove(film_id: int, proof_id: int, user=Depends(staff)):
    async with get_pool().acquire() as conn, conn.transaction():
        await active_film(conn, film_id, lock=True)
        if not await conn.fetchval(
            "DELETE FROM film_rights_proofs WHERE id=$1 AND film_id=$2 RETURNING id", proof_id, film_id
        ):
            raise HTTPException(404, "Comprovante não encontrado")
        film = await conn.fetchrow(
            """SELECT f.rights_status,f.rights_valid_until,
            EXISTS(SELECT 1 FROM film_rights_proofs p WHERE p.film_id=f.id) AS has_proof,
            (SELECT MAX((COALESCE(e.ends_at,e.starts_at) AT TIME ZONE 'America/Fortaleza')::date) FROM events e
             WHERE e.film_id=f.id AND e.status='published' AND COALESCE(e.ends_at,e.starts_at)>NOW()) AS ends_on
            FROM films f WHERE f.id=$1""",
            film_id,
        )
        if film["ends_on"] and publication_error(
            film["rights_status"], film["rights_valid_until"], film["ends_on"], film["has_proof"]
        ):
            raise HTTPException(
                409, "Despublique as sessões futuras antes de remover o último comprovante"
            )
    return {"ok": True}
