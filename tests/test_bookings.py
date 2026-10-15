import asyncio
from datetime import datetime, timedelta, timezone


def booking():
    start = datetime.now(timezone.utc) + timedelta(days=2)
    return dict(
        room_id=1,
        title="Aula de cinema",
        course="Cinema",
        registration="professor",
        student_count=20,
        starts_at=start.isoformat(),
        ends_at=(start + timedelta(hours=2)).isoformat(),
    )


async def test_booking_concurrency_and_adjacent_periods(api):
    client, pool, headers = api
    body = booking()
    results = await asyncio.gather(
        *[
            client.post("/api/v1/reservas", json=body, headers=headers())
            for _ in range(2)
        ]
    )
    assert sorted(r.status_code for r in results) == [201, 409]
    assert await pool.fetchval("SELECT COUNT(*) FROM room_bookings") == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM professor_requests") == 1
    params = {
        "starts_at": body["starts_at"],
        "ends_at": body["ends_at"],
        "capacity": 20,
        "resources": "Projetor",
    }
    response = await client.get(
        "/api/v1/salas/disponibilidade", params=params, headers=headers()
    )
    assert response.status_code == 200, response.text
    assert response.json()[0]["free_slots"] == []
    body["starts_at"] = body["ends_at"]
    body["ends_at"] = (
        datetime.fromisoformat(body["ends_at"]) + timedelta(hours=1)
    ).isoformat()
    assert (
        await client.post("/api/v1/reservas", json=body, headers=headers())
    ).status_code == 201


async def test_booking_validation_and_access(api):
    client, pool, headers = api
    body = booking()
    assert (await client.post("/api/v1/reservas", json=body)).status_code == 401
    assert (
        await client.post("/api/v1/reservas", json=body, headers=headers("aluno"))
    ).status_code == 403
    for changes in [
        {"student_count": 31},
        {"registration": "someone"},
        {"ends_at": body["starts_at"]},
        {"starts_at": "2020-01-01T00:00:00Z"},
        {"starts_at": "2027-01-01T12:00:00"},
    ]:
        assert (
            await client.post(
                "/api/v1/reservas", json=body | changes, headers=headers()
            )
        ).status_code == 422
    assert await pool.fetchval("SELECT COUNT(*) FROM room_bookings") == 0


async def test_legacy_submission_and_rejection_release_room(api):
    client, pool, headers = api
    body = booking()
    assert (
        await client.post(
            "/api/v1/professor/requests/room", json=body, headers=headers()
        )
    ).status_code == 200
    req = await pool.fetchval("SELECT id FROM professor_requests")
    res = await client.put(
        f"/api/v1/staff/requests/{req}",
        json={"status": "refused"},
        headers=headers("funcionario"),
    )
    assert res.status_code == 200
    assert await pool.fetchval("SELECT status FROM room_bookings") == "cancelled"
    assert (
        await client.post("/api/v1/reservas", json=body, headers=headers())
    ).status_code == 201
