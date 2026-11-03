"""Fluxos ponta a ponta da API por perfil, com login real (bcrypt) — T23.2."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.hardening import login_limiter
from app.core.security import hash_password

PASSWORD = "Senha-Forte-2026"
FUTURE = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=7)


@pytest.fixture(autouse=True)
def fresh_limiter():
    login_limiter.clear()


async def login(client, pool, role, registration):
    uid = uuid4()
    email = f"{registration.lower()}@flow.invalid"
    await pool.execute(
        "INSERT INTO users(id,email,password_hash) VALUES($1,$2,$3)", uid, email, hash_password(PASSWORD)
    )
    await pool.execute(
        "INSERT INTO profiles(id,role,full_name,registration,course) VALUES($1,$2,$3,$4,'Cinema')",
        uid,
        role,
        f"{role.title()} Fluxo",
        registration,
    )
    res = await client.post("/api/v1/auth/login", json={"login": registration, "password": PASSWORD, "role": role})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["user"] == body["user"] | {"id": str(uid), "email": email, "role": role}
    return {"Authorization": f"Bearer {body['token']}"}


async def test_login_and_profile(api):
    client, pool, _ = api
    h = await login(client, pool, "aluno", "A2026")
    me = await client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200 and me.json()["registration"] == "A2026"
    # By e-mail too; wrong password and wrong portal are refused.
    by_email = {"login": "a2026@flow.invalid", "password": PASSWORD, "role": "aluno"}
    assert (await client.post("/api/v1/auth/login", json=by_email)).status_code == 200
    assert (await client.post("/api/v1/auth/login", json=by_email | {"password": "x"})).status_code == 401
    wrong_role = await client.post("/api/v1/auth/login", json=by_email | {"role": "funcionario"})
    assert wrong_role.status_code == 403
    assert (await client.get("/api/v1/student/profile", headers=h)).json()["full_name"] == "Aluno Fluxo"


async def test_staff_student_and_professor_journey(api):
    client, pool, _ = api
    staff = await login(client, pool, "funcionario", "F2026")
    student = await login(client, pool, "aluno", "A2026")
    professor = await login(client, pool, "professor", "P2026")

    # Funcionário cadastra filme com autorização, anexa comprovante, agenda e publica sessão.
    film = await client.post(
        "/api/v1/staff/films",
        json={
            "title": "Aquarius",
            "year": 2016,
            "director": "Kleber Mendonça Filho",
            "rights_status": "authorized",
            "rights_holder": "Vitrine Filmes",
            "license_type": "exibicao_publica",
            "rights_valid_until": (FUTURE + timedelta(days=60)).date().isoformat(),
        },
        headers=staff,
    )
    assert film.status_code == 201, film.text
    film_id = film.json()["id"]
    proof = await client.post(
        f"/api/v1/staff/films/{film_id}/rights-proofs",
        files={"file": ("licenca.pdf", b"%PDF-1.7\n%%EOF", "application/pdf")},
        headers=staff,
    )
    assert proof.status_code == 201
    session = await client.post(
        "/api/v1/staff/sessions",
        json={
            "title": "Cineclube: Aquarius",
            "film_id": film_id,
            "room_id": 1,
            "slots": 2,
            "starts_at": FUTURE.isoformat(),
            "ends_at": (FUTURE + timedelta(hours=3)).isoformat(),
        },
        headers=staff,
    )
    assert session.status_code == 201
    sid = session.json()["id"]
    published = await client.put(f"/api/v1/staff/sessions/{sid}/publication", json={"published": True}, headers=staff)
    assert published.status_code == 200
    assert any(s["id"] == sid for s in (await client.get("/api/v1/staff/sessions", headers=staff)).json())

    # Catálogo e eventos são públicos.
    catalog = (await client.get("/api/v1/catalog/films", params={"q": "aquarius"})).json()
    assert [f["id"] for f in catalog] == [film_id] and "rights_holder" not in catalog[0]
    assert (await client.get(f"/api/v1/catalog/films/{film_id}")).json()["title"] == "Aquarius"
    assert (await client.get("/api/v1/catalog/films/99999")).status_code == 404
    assert [e["id"] for e in (await client.get("/api/v1/events")).json()] == [sid]

    # Aluno: inscrição na sessão, reserva de filme e painel.
    assert (await client.post(f"/api/v1/student/events/{sid}/enroll", headers=student)).status_code == 200
    assert (await client.post(f"/api/v1/student/events/{sid}/enroll", headers=student)).status_code == 400
    events = (await client.get("/api/v1/student/events", headers=student)).json()
    assert events[0]["enrolled_by_me"] is True and events[0]["enrolled"] == 1
    reservation = await client.post("/api/v1/student/reservations", json={"film_id": film_id}, headers=student)
    assert reservation.status_code == 200
    assert (await client.post("/api/v1/student/reservations", json={"film_id": film_id}, headers=student)).status_code == 400
    stats = (await client.get("/api/v1/student/stats", headers=student)).json()
    assert stats["reservations"] == 1 and stats["enrolled_events"] == 1
    assert len((await client.get("/api/v1/staff/reservations", headers=staff)).json()) == 1
    rid = reservation.json()["id"]
    assert (await client.delete(f"/api/v1/student/reservations/{rid}", headers=student)).status_code == 200
    assert (await client.delete(f"/api/v1/student/events/{sid}/enroll", headers=student)).status_code == 200

    # Professor: disponibilidade (sala 1 ocupada pela sessão), reserva de sala e solicitações.
    window = {"starts_at": FUTURE.isoformat(), "ends_at": (FUTURE + timedelta(hours=1)).isoformat()}
    rooms = {
        r["id"]: r
        for r in (await client.get("/api/v1/salas/disponibilidade", params=window | {"capacity": 5}, headers=professor)).json()
    }
    assert rooms[1]["available"] is False and rooms[2]["available"] is True
    booking = await client.post(
        "/api/v1/reservas",
        json=window | {"title": "Aula prática", "room_id": 2, "student_count": 5, "course": "Cinema", "registration": "P2026"},
        headers=professor,
    )
    assert booking.status_code == 201
    clash = await client.post(
        "/api/v1/reservas",
        json=window | {"title": "Outra aula", "room_id": 1, "student_count": 5, "course": "Cinema", "registration": "P2026"},
        headers=professor,
    )
    assert clash.status_code == 409
    assert len((await client.get("/api/v1/reservas/me", headers=professor)).json()) == 1
    request = await client.post(
        "/api/v1/professor/requests/film",
        json={"film_id": film_id, "date": FUTURE.date().isoformat(), "notes": "Turma A"},
        headers=professor,
    )
    assert request.status_code == 200
    mine = (await client.get("/api/v1/professor/requests", headers=professor)).json()
    assert {r["request_type"] for r in mine} == {"film", "room"}
    assert (await client.get("/api/v1/professor/stats", headers=professor)).json()["total_requests"] == 2
    staff_requests = (await client.get("/api/v1/staff/requests", headers=staff)).json()
    assert len(staff_requests) == 2

    # Funcionário: painel e equipamentos (retirada e devolução).
    stats = (await client.get("/api/v1/staff/stats", headers=staff)).json()
    assert stats["upcoming_events"] == 1 and stats["pending_requests"] == 2
    item = await client.post(
        "/api/v1/staff/equipment", json={"name": "Projetor 4K", "category": "Projeção", "quantity": 2}, headers=staff
    )
    assert item.status_code == 201
    loan = await client.post(
        "/api/v1/staff/equipment/loans",
        json={
            "equipment_id": item.json()["id"],
            "registration": "P2026",
            "quantity": 1,
            "due_at": (FUTURE + timedelta(days=1)).isoformat(),
        },
        headers=staff,
    )
    assert loan.status_code == 201, loan.text
    returned = await client.post(
        f"/api/v1/staff/equipment/loans/{loan.json()['id']}/return", json={"condition": "good"}, headers=staff
    )
    assert returned.status_code == 200
    available = (await client.get("/api/v1/staff/equipment", headers=staff)).json()[0]["available"]
    assert available == 2

    # Despublicar libera a sala e remove a sessão da vitrine.
    assert (await client.put(f"/api/v1/staff/sessions/{sid}/publication", json={"published": False}, headers=staff)).status_code == 200
    assert (await client.get("/api/v1/events")).json() == []


DENIED = [
    ("aluno", "GET", "/api/v1/staff/stats"),
    ("aluno", "GET", "/api/v1/staff/films"),
    ("aluno", "POST", "/api/v1/staff/sessions"),
    ("aluno", "GET", "/api/v1/staff/films/1/rights-proofs"),
    ("aluno", "GET", "/api/v1/professor/requests"),
    ("aluno", "GET", "/api/v1/salas/disponibilidade"),
    ("aluno", "POST", "/api/v1/auth/register"),
    ("professor", "GET", "/api/v1/student/loans"),
    ("professor", "GET", "/api/v1/staff/equipment"),
    ("professor", "PUT", "/api/v1/staff/sessions/1/publication"),
    ("funcionario", "POST", "/api/v1/reservas"),
    ("funcionario", "GET", "/api/v1/student/stats"),
]


async def test_denied_routes_by_role(api):
    client, pool, headers = api
    for role, method, path in DENIED:
        assert (await client.request(method, path, headers=headers(role))).status_code == 403, (role, path)
        assert (await client.request(method, path)).status_code == 401, path
