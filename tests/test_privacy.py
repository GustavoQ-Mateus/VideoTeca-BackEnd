import json
from datetime import datetime, timedelta, timezone

from app.routers.privacy import POLICY_VERSION


async def test_consent_flow(api):
    client, pool, headers = api
    h = headers("aluno")
    state = (await client.get("/api/v1/me/privacy", headers=h)).json()
    assert state == {"policy_version": POLICY_VERSION, "accepted_at": None}
    assert (await client.post("/api/v1/me/privacy/consent", json={"policy_version": "2020-01"}, headers=h)).status_code == 422
    first = await client.post("/api/v1/me/privacy/consent", json={"policy_version": POLICY_VERSION}, headers=h)
    assert first.status_code == 200 and first.json()["accepted_at"]
    again = await client.post("/api/v1/me/privacy/consent", json={"policy_version": POLICY_VERSION}, headers=h)
    assert again.json()["accepted_at"] == first.json()["accepted_at"]
    assert (await client.get("/api/v1/me/privacy", headers=h)).json()["accepted_at"] == first.json()["accepted_at"]
    assert (await client.get("/api/v1/me/privacy")).status_code == 401


async def test_export_contains_only_own_data(api):
    client, pool, headers = api
    student = await pool.fetchval("SELECT id FROM profiles WHERE role='aluno'")
    professor = await pool.fetchval("SELECT id FROM profiles WHERE role='professor'")
    await pool.execute("INSERT INTO reservations(user_id,film_id,status) VALUES($1,1,'fulfilled'),($2,1,'fulfilled')", student, professor)
    await pool.execute("INSERT INTO notifications(user_id,title,body) VALUES($1,'Aviso','Sua reserva')", student)
    await client.post("/api/v1/me/privacy/consent", json={"policy_version": POLICY_VERSION}, headers=headers("aluno"))
    res = await client.get("/api/v1/me/data", headers=headers("aluno"))
    assert res.status_code == 200
    assert res.headers["content-disposition"] == "attachment; filename=meus-dados.json"
    assert res.headers["cache-control"] == "private, no-store"
    data = json.loads(res.content)
    assert data["perfil"]["email"] == "aluno@test.invalid" and data["perfil"]["id"] == str(student)
    assert len(data["reservas_filmes"]) == 1 and data["reservas_filmes"][0]["filme"] == "Filme teste"
    assert data["notificacoes"][0]["title"] == "Aviso"
    assert data["consentimentos"][0]["policy_version"] == POLICY_VERSION
    assert "password_hash" not in res.text
    assert await pool.fetchval(
        "SELECT COUNT(*) FROM data_requests WHERE user_id=$1 AND kind='export' AND status='done'", student
    ) == 1


async def test_deletion_blocked_by_pending_obligations_then_anonymizes(api):
    client, pool, headers = api
    h = headers("professor")
    professor = await pool.fetchval("SELECT id FROM profiles WHERE role='professor'")
    start = datetime.now(timezone.utc) + timedelta(days=2)
    booking = await client.post(
        "/api/v1/reservas",
        json={
            "title": "Aula de cinema",
            "room_id": 1,
            "student_count": 5,
            "course": "Cinema",
            "registration": "professor",
            "notes": "Turma A",
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=1)).isoformat(),
        },
        headers=h,
    )
    assert booking.status_code == 201
    blocked = await client.post("/api/v1/me/deletion", headers=h)
    assert blocked.status_code == 409 and "reserva de sala futura" in blocked.json()["detail"]
    assert await pool.fetchval("SELECT full_name FROM profiles WHERE id=$1", professor) == "professor"
    # Past booking: history is kept but no longer blocks erasure.
    await pool.execute(
        "UPDATE room_bookings SET starts_at=NOW()-interval '2 days',ends_at=NOW()-interval '1 day'"
    )
    done = await client.post("/api/v1/me/deletion", headers=h)
    assert done.status_code == 200 and done.json() == {"ok": True}
    profile = await pool.fetchrow(
        "SELECT p.full_name,p.registration,p.course,p.phone,u.email,u.password_hash FROM profiles p JOIN users u ON u.id=p.id WHERE p.id=$1",
        professor,
    )
    assert profile["full_name"] == "Usuário removido"
    assert profile["email"] == f"removido-{professor}@anon.invalid"
    assert (profile["registration"], profile["course"], profile["phone"]) == (None, None, None)
    assert profile["password_hash"] != "unused"
    kept = await pool.fetchrow("SELECT registration,notes,professor_id FROM room_bookings")
    assert kept["professor_id"] == professor and kept["registration"] is None and kept["notes"] is None
    payload = await pool.fetchval("SELECT payload FROM professor_requests WHERE professor_id=$1", professor)
    assert "registration" not in json.loads(payload)
    assert await pool.fetchval(
        "SELECT COUNT(*) FROM data_requests WHERE user_id=$1 AND kind='deletion' AND status='done'", professor
    ) == 1
    login = await client.post(
        "/api/v1/auth/login", json={"login": "professor@test.invalid", "password": "x", "role": "professor"}
    )
    assert login.status_code == 401
    # Token emitido antes da exclusão deixa de valer.
    assert (await client.get("/api/v1/me/privacy", headers=h)).status_code == 401


async def test_deletion_blocked_by_active_loan(api):
    client, pool, headers = api
    student = await pool.fetchval("SELECT id FROM profiles WHERE role='aluno'")
    await pool.execute("INSERT INTO copies(id,film_id,code) VALUES(1,1,'CP-1')")
    await pool.execute("INSERT INTO loans(user_id,copy_id,due_at) VALUES($1,1,NOW()+interval '3 days')", student)
    res = await client.post("/api/v1/me/deletion", headers=headers("aluno"))
    assert res.status_code == 409 and "empréstimo de filme em aberto" in res.json()["detail"]
