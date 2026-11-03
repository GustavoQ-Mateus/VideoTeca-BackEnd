from datetime import datetime, timedelta, timezone


async def test_room_bookings_and_presence(api):
    client, pool, headers = api
    h = headers("funcionario")
    start = datetime.now(timezone.utc) + timedelta(days=1)
    await pool.execute(
        "INSERT INTO room_bookings(room_id,starts_at,ends_at,title,status) VALUES(1,$1,$2,'Aula','confirmed')",
        start,
        start + timedelta(hours=2),
    )
    window = {"from": (start - timedelta(hours=1)).isoformat(), "to": (start + timedelta(hours=3)).isoformat()}
    res = await client.get("/api/v1/staff/room-bookings", params=window, headers=h)
    assert res.status_code == 200 and [b["title"] for b in res.json()] == ["Aula"]
    assert res.json()[0]["kind"] == "booking"
    bad = {"from": window["to"], "to": window["from"]}
    assert (await client.get("/api/v1/staff/room-bookings", params=bad, headers=h)).status_code == 422
    assert (await client.get("/api/v1/staff/room-bookings", params=window, headers=headers("aluno"))).status_code == 403

    event = await pool.fetchval(
        "INSERT INTO events(title,event_type,location,starts_at,slots,status) VALUES('Debate','debate','Sala',$1,10,'published') RETURNING id",
        start,
    )
    student = await pool.fetchval("SELECT id FROM profiles WHERE role='aluno'")
    await pool.execute("INSERT INTO event_registrations(event_id,user_id) VALUES($1,$2)", event, student)
    rows = (await client.get(f"/api/v1/staff/events/{event}/registrations", headers=h)).json()
    assert rows == [{"user_id": str(student), "name": "aluno", "matricula": "aluno", "course": "Cinema", "present": False}]
    marked = await client.put(f"/api/v1/staff/events/{event}/registrations/{student}", json={"present": True}, headers=h)
    assert marked.status_code == 200 and marked.json()["present"] is True
    assert await pool.fetchval("SELECT present FROM event_registrations WHERE event_id=$1", event) is True
    stranger = "00000000-0000-0000-0000-000000000000"
    missing = await client.put(f"/api/v1/staff/events/{event}/registrations/{stranger}", json={"present": True}, headers=h)
    assert missing.status_code == 404
    assert (await client.get("/api/v1/staff/events/999999/registrations", headers=h)).status_code == 404
