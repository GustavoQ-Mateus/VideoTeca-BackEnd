import asyncio
from datetime import datetime, timedelta, timezone


async def test_session_publication_conflicts_and_visibility(api):
    client, pool, headers = api
    h = headers("funcionario")
    start = datetime.now(timezone.utc) + timedelta(days=3)
    body = {
        "title": "Sessão",
        "film_id": 1,
        "room_id": 1,
        "slots": 20,
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=2)).isoformat(),
    }
    session = await client.post("/api/v1/staff/sessions", json=body, headers=h)
    assert session.status_code == 201, session.text
    sid = session.json()["id"]
    path = f"/api/v1/staff/sessions/{sid}/publication"
    assert (await client.get(f"/api/v1/events/{sid}")).status_code == 404
    reserve = {
        "title": "Aula",
        "room_id": 1,
        "student_count": 20,
        "course": "Cinema",
        "registration": "professor",
        "starts_at": body["starts_at"],
        "ends_at": body["ends_at"],
    }
    results = await asyncio.gather(
        client.put(path, json={"published": True}, headers=h),
        client.post("/api/v1/reservas", json=reserve, headers=headers()),
    )
    assert sorted(r.status_code for r in results) in ([200, 409], [201, 409])
    if results[0].status_code == 409:
        await pool.execute("UPDATE room_bookings SET status='cancelled'")
        assert (
            await client.put(path, json={"published": True}, headers=h)
        ).status_code == 200
    assert (await client.get(f"/api/v1/events/{sid}")).status_code == 200
    assert len((await client.get("/api/v1/events")).json()) == 1
    assert (await client.delete("/api/v1/staff/films/1", headers=h)).status_code == 409
    assert (
        await client.post("/api/v1/reservas", json=reserve, headers=headers())
    ).status_code == 409
    assert (
        await client.put(path, json={"published": False}, headers=h)
    ).status_code == 200
    assert (await client.get(f"/api/v1/events/{sid}")).status_code == 404
    assert (await client.get("/api/v1/events")).json() == []
    assert (
        await client.post("/api/v1/reservas", json=reserve, headers=headers())
    ).status_code == 201


async def test_session_edit_conflict_and_capacity(api):
    client, pool, headers = api
    h = headers("funcionario")
    start = datetime.now(timezone.utc) + timedelta(days=3)
    body = {
        "title": "Sessão",
        "film_id": 1,
        "room_id": 1,
        "slots": 20,
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=1)).isoformat(),
    }
    assert (
        await client.post(
            "/api/v1/staff/sessions", json=body | {"slots": 31}, headers=h
        )
    ).status_code == 422
    ids = []
    for offset in [0, 2]:
        data = body | {
            "starts_at": (start + timedelta(hours=offset)).isoformat(),
            "ends_at": (start + timedelta(hours=offset + 1)).isoformat(),
        }
        result = await client.post("/api/v1/staff/sessions", json=data, headers=h)
        sid = result.json()["id"]
        ids.append(sid)
        assert (
            await client.put(
                f"/api/v1/staff/sessions/{sid}/publication",
                json={"published": True},
                headers=h,
            )
        ).status_code == 200
    assert (
        await client.put(f"/api/v1/staff/sessions/{ids[1]}", json=body, headers=h)
    ).status_code == 409
    assert (
        await pool.fetchval(
            "SELECT COUNT(*) FROM room_bookings WHERE status='confirmed'"
        )
        == 2
    )
