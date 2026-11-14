from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

PDF = b"%PDF-1.7\n%test\n"
NOT_AUTHORIZED = "Filme sem autorização de exibição válida para esta sessão"


def session_body(film_id=1, days=4):
    start = datetime.now(timezone.utc) + timedelta(days=days)
    return {
        "title": "Cineclube",
        "film_id": film_id,
        "room_id": 1,
        "slots": 10,
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=2)).isoformat(),
    }, start


async def make_session(client, h):
    body, start = session_body()
    res = await client.post("/api/v1/staff/sessions", json=body, headers=h)
    assert res.status_code == 201, res.text
    return res.json()["id"], start


async def test_publication_requires_valid_rights_and_proof(api):
    client, pool, headers = api
    h = headers("funcionario")
    sid, start = await make_session(client, h)
    path = f"/api/v1/staff/sessions/{sid}/publication"

    async def publish(flag=True):
        return await client.put(path, json={"published": flag}, headers=h)

    blocked = await publish()
    assert blocked.status_code == 422 and blocked.json()["detail"] == NOT_AUTHORIZED
    await pool.execute("UPDATE films SET rights_status='denied' WHERE id=1")
    assert (await publish()).status_code == 422
    await pool.execute(
        "UPDATE films SET rights_status='authorized',rights_holder='Distribuidora',rights_valid_until=$1 WHERE id=1",
        (start + timedelta(days=30)).date(),
    )
    missing = await publish()
    assert missing.status_code == 422
    assert missing.json()["detail"] == "Anexe o comprovante de autorização antes de publicar"
    upload = await client.post(
        "/api/v1/staff/films/1/rights-proofs",
        files={"file": ("contrato.pdf", PDF, "application/pdf")},
        headers=h,
    )
    assert upload.status_code == 201
    # Authorization expiring before the session ends blocks publication.
    await pool.execute(
        "UPDATE films SET rights_valid_until=$1 WHERE id=1", (start - timedelta(days=1)).date()
    )
    assert (await publish()).json()["detail"] == NOT_AUTHORIZED
    await pool.execute(
        "UPDATE films SET rights_valid_until=$1 WHERE id=1", (start + timedelta(days=1)).date()
    )
    ok = await publish()
    assert ok.status_code == 200 and ok.json()["status"] == "published"
    assert (await client.get(f"/api/v1/events/{sid}")).status_code == 200
    # Unpublishing is always allowed, even after the authorization is gone.
    await pool.execute("UPDATE films SET rights_status='denied' WHERE id=1")
    assert (await publish(False)).status_code == 200
    assert (await client.get(f"/api/v1/events/{sid}")).status_code == 404


async def test_public_domain_publishes_without_proof(api):
    client, pool, headers = api
    h = headers("funcionario")
    await pool.execute("UPDATE films SET rights_status='public_domain' WHERE id=1")
    sid, _ = await make_session(client, h)
    res = await client.put(
        f"/api/v1/staff/sessions/{sid}/publication", json={"published": True}, headers=h
    )
    assert res.status_code == 200


async def test_database_blocks_other_publication_paths(api):
    client, pool, headers = api
    h = headers("funcionario")
    # Legacy events (no room) cannot be published for a film without rights (trigger in migration 010).
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO events(title,film_id,starts_at,status) VALUES('Mostra',1,NOW()+interval '1 day','published')"
        )
    sid, _ = await make_session(client, h)
    other = await pool.fetchval("INSERT INTO films(title) VALUES('Outro') RETURNING id")
    await pool.execute("UPDATE films SET rights_status='public_domain' WHERE id=1")
    assert (
        await client.put(
            f"/api/v1/staff/sessions/{sid}/publication", json={"published": True}, headers=h
        )
    ).status_code == 200
    # Swapping a published session to an unauthorized film is rejected by the database.
    body, _ = session_body(film_id=other)
    assert (await client.put(f"/api/v1/staff/sessions/{sid}", json=body, headers=h)).status_code == 422
    assert await pool.fetchval("SELECT film_id FROM events WHERE id=$1", sid) == 1
