"""Regra de publicação condicionada à autorização de exibição (T21.2)."""

from fastapi import HTTPException

PUBLISHABLE = ("authorized", "public_domain")
NOT_AUTHORIZED = "Filme sem autorização de exibição válida para esta sessão"
PROOF_REQUIRED = "Anexe o comprovante de autorização antes de publicar"


def publication_error(rights_status, valid_until, ends_on, has_proof):
    """Pure rule: returns the blocking message or None. `ends_on` is the session end date (local)."""
    if rights_status not in PUBLISHABLE or (valid_until and valid_until < ends_on):
        return NOT_AUTHORIZED
    if rights_status == "authorized" and not has_proof:
        return PROOF_REQUIRED
    return None


async def ensure_publishable(conn, film_id, session_ends_at):
    # Locks the film like the occupancy trigger does, so rights cannot change mid-publication.
    film = await conn.fetchrow(
        """SELECT f.rights_status,f.rights_valid_until,($2::timestamptz AT TIME ZONE 'America/Fortaleza')::date AS ends_on,
        EXISTS(SELECT 1 FROM film_rights_proofs p WHERE p.film_id=f.id) AS has_proof
        FROM films f WHERE f.id=$1 AND f.status<>'inactive' FOR UPDATE OF f""",
        film_id,
        session_ends_at,
    )
    if not film:
        raise HTTPException(404, "Filme não encontrado")
    error = publication_error(
        film["rights_status"], film["rights_valid_until"], film["ends_on"], film["has_proof"]
    )
    if error:
        raise HTTPException(422, error)
