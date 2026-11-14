"""Funções do funcionário: fila de reservas, empréstimos, cópias, parâmetros, usuários, salas,
solicitações, relatórios e contador de inscritos (migration 015)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.hardening import login_limiter
from app.core.security import create_token

S = "/api/v1/staff"


@pytest.fixture(autouse=True)
def fresh_limiter():
    login_limiter.clear()


async def ids(pool):
    rows = await pool.fetch("SELECT id, role FROM profiles")
    return {r["role"]: r["id"] for r in rows}


async def add_copy(pool, code, film_id=1, status="available"):
    return await pool.fetchval(
        "INSERT INTO copies(film_id,code,condition,status) VALUES($1,$2,'good',$3) RETURNING id", film_id, code, status
    )


async def notes(pool, user_id):
    return [r["title"] for r in await pool.fetch("SELECT title FROM notifications WHERE user_id=$1 ORDER BY id", user_id)]


async def admin_headers(pool):
    uid = uuid4()
    await pool.execute("INSERT INTO users(id,email,password_hash) VALUES($1,'admin@test.invalid','x')", uid)
    await pool.execute("INSERT INTO profiles(id,role,full_name,registration) VALUES($1,'admin','Admin','ADM')", uid)
    return {"Authorization": "Bearer " + create_token(str(uid), "admin")}


async def test_enrolled_trigger_keeps_real_count(api):
    client, pool, headers = api
    who = await ids(pool)
    start = datetime.now(timezone.utc) + timedelta(days=2)
    ev = await pool.fetchval(
        "INSERT INTO events(title,starts_at,slots,enrolled,status) VALUES('E',$1,10,50,'published') RETURNING id", start
    )
    assert await pool.fetchval("SELECT enrolled FROM events WHERE id=$1", ev) == 0
    await pool.execute("INSERT INTO event_registrations(event_id,user_id) VALUES($1,$2),($1,$3)", ev, who["aluno"], who["professor"])
    assert await pool.fetchval("SELECT enrolled FROM events WHERE id=$1", ev) == 2
    await pool.execute("UPDATE events SET enrolled=enrolled+1 WHERE id=$1", ev)  # escrita manual é ignorada
    assert await pool.fetchval("SELECT enrolled FROM events WHERE id=$1", ev) == 2
    await pool.execute("DELETE FROM event_registrations WHERE event_id=$1 AND user_id=$2", ev, who["aluno"])
    assert await pool.fetchval("SELECT enrolled FROM events WHERE id=$1", ev) == 1


async def test_settings(api):
    client, pool, headers = api
    h = headers("funcionario")
    assert (await client.get(f"{S}/settings", headers=h)).json() == {
        "loan_days": 7, "max_items_per_user": 3, "max_reservations": 2, "reservation_pickup_days": 3
    }
    new = {"loan_days": 14, "max_items_per_user": 5, "max_reservations": 0, "reservation_pickup_days": 2}
    res = await client.put(f"{S}/settings", json=new, headers=h)
    assert res.status_code == 200 and res.json() == new
    assert (await client.get(f"{S}/settings", headers=h)).json() == new
    assert (await client.put(f"{S}/settings", json=new | {"loan_days": 61}, headers=h)).status_code == 422
    assert (await client.put(f"{S}/settings", json=new, headers=headers("aluno"))).status_code == 403


async def test_copies_crud(api):
    client, pool, headers = api
    h = headers("funcionario")
    await pool.execute("UPDATE films SET internal_code='VT-9001' WHERE id=1")
    created = await client.post(f"{S}/films/1/copies", json={"condition": "good"}, headers=h)
    assert created.status_code == 201
    assert created.json() == {"id": created.json()["id"], "code": "VT-9001-1", "patrimonio": None,
                              "condition": "good", "status": "available", "open_loan": None}
    second = await client.post(f"{S}/films/1/copies", json={"code": "X-2", "patrimonio": "PAT-2", "condition": "fair"}, headers=h)
    assert second.status_code == 201
    assert (await client.post(f"{S}/films/1/copies", json={"code": "X-2"}, headers=h)).status_code == 409
    assert (await client.post(f"{S}/films/999/copies", json={}, headers=h)).status_code == 404

    loan = await client.post(f"{S}/loans", json={"user_registration": "aluno", "copy_code": "X-2"}, headers=h)
    assert loan.status_code == 201, loan.text
    copies = {c["code"]: c for c in (await client.get(f"{S}/films/1/copies", headers=h)).json()}
    assert copies["X-2"]["status"] == "borrowed" and copies["X-2"]["open_loan"]["user_name"] == "aluno"
    cid2 = second.json()["id"]
    assert (await client.put(f"{S}/copies/{cid2}", json={"status": "lost"}, headers=h)).status_code == 409
    assert (await client.delete(f"{S}/copies/{cid2}", headers=h)).status_code == 409
    await client.post(f"{S}/loans/{loan.json()['id']}/return", json={"condition": "good"}, headers=h)
    # Com histórico: não exclui, mas pode ser marcado como extraviado.
    assert (await client.delete(f"{S}/copies/{cid2}", headers=h)).status_code == 409
    lost = await client.put(f"{S}/copies/{cid2}", json={"status": "lost", "patrimonio": "PAT-9"}, headers=h)
    assert lost.status_code == 200 and lost.json()["status"] == "lost" and lost.json()["patrimonio"] == "PAT-9"
    assert (await client.put(f"{S}/copies/{cid2}", json={"status": "borrowed"}, headers=h)).status_code == 422
    assert (await client.put(f"{S}/copies/{created.json()['id']}", json={"code": "X-2"}, headers=h)).status_code == 409
    assert (await client.delete(f"{S}/copies/{created.json()['id']}", headers=h)).status_code == 200
    assert [c["code"] for c in (await client.get(f"{S}/films/1/copies", headers=h)).json()] == ["X-2"]


async def test_loans_limits_late_search_and_return(api):
    client, pool, headers = api
    h = headers("funcionario")
    who = await ids(pool)
    for code in ("C-1", "C-2", "C-3"):
        await add_copy(pool, code)
    await client.put(f"{S}/settings", json={"loan_days": 10, "max_items_per_user": 2, "max_reservations": 2,
                                          "reservation_pickup_days": 3}, headers=h)
    first = await client.post(f"{S}/loans", json={"user_registration": "aluno", "copy_code": "C-1"}, headers=h)
    assert first.status_code == 201
    body = first.json()
    days = (datetime.fromisoformat(body["due_at"]) - datetime.fromisoformat(body["borrowed_at"])).days
    assert days == 10 and body["status"] == "borrowed" and body["copy"] == "C-1" and body["film"] == "Filme teste"
    assert (await client.post(f"{S}/loans", json={"user_registration": "aluno", "copy_code": "C-1"}, headers=h)).status_code == 409
    assert (await client.post(f"{S}/loans", json={"user_registration": "nao-existe", "copy_code": "C-2"}, headers=h)).status_code == 404
    assert (await client.post(f"{S}/loans", json={"user_registration": "aluno", "copy_code": "ZZ"}, headers=h)).status_code == 404
    assert (await client.post(f"{S}/loans", json={"user_registration": "aluno", "copy_code": "C-2"}, headers=h)).status_code == 201
    over = await client.post(f"{S}/loans", json={"user_registration": "aluno", "copy_code": "C-3"}, headers=h)
    assert over.status_code == 409 and "Limite de 2" in over.json()["detail"]
    await pool.execute("UPDATE profiles SET active=FALSE WHERE id=$1", who["professor"])
    off = await client.post(f"{S}/loans", json={"user_registration": "professor", "copy_code": "C-3"}, headers=h)
    assert off.status_code == 409 and off.json()["detail"] == "Conta desativada"

    # Atraso derivado de due_at e busca no servidor.
    await pool.execute("UPDATE loans SET borrowed_at=NOW()-INTERVAL '20 days', due_at=NOW()-INTERVAL '1 day' WHERE id=$1", body["id"])
    open_loans = (await client.get(f"{S}/loans", params={"open": "true"}, headers=h)).json()
    assert {l["copy"]: l["status"] for l in open_loans} == {"C-1": "late", "C-2": "borrowed"}
    assert [l["copy"] for l in (await client.get(f"{S}/loans", params={"q": "c-1"}, headers=h)).json()] == ["C-1"]
    assert len((await client.get(f"{S}/loans", params={"q": "Filme TESTE", "limit": 1}, headers=h)).json()) == 1
    assert len((await client.get(f"{S}/loans", params={"q": "aluno", "offset": 1}, headers=h)).json()) == 1
    assert (await client.get(f"{S}/loans", params={"q": "%"}, headers=h)).json() == []
    stats = (await client.get(f"{S}/stats", headers=h)).json()
    assert stats["late_items"] == 1 and stats["active_loans"] == 2

    damaged = await client.post(f"{S}/loans/{body['id']}/return", json={"condition": "damaged", "notes": "Disco riscado"}, headers=h)
    assert damaged.status_code == 200 and damaged.json()["copy_status"] == "maintenance"
    row = await pool.fetchrow("SELECT status, return_condition, return_notes FROM loans WHERE id=$1", body["id"])
    assert dict(row) == {"status": "returned", "return_condition": "damaged", "return_notes": "Disco riscado"}
    assert await pool.fetchval("SELECT status FROM copies WHERE code='C-1'") == "maintenance"
    assert (await client.post(f"{S}/loans/{body['id']}/return", json={}, headers=h)).status_code == 404
    assert (await client.post(f"{S}/loans/{body['id']}/return", json={"condition": "x"}, headers=h)).status_code == 422
    closed = (await client.get(f"{S}/loans", params={"open": "false"}, headers=h)).json()
    assert closed[0]["status"] == "returned" and closed[0]["return_notes"] == "Disco riscado"


async def test_reservation_queue(api):
    client, pool, headers = api
    h = headers("funcionario")
    who = await ids(pool)
    await add_copy(pool, "Q-1")
    loan = (await client.post(f"{S}/loans", json={"user_registration": "aluno", "copy_code": "Q-1"}, headers=h)).json()

    # Sem exemplar livre: reserva fica na fila e não pode ser confirmada.
    r1 = await client.post(f"{S}/reservations", json={"registration": "professor", "film_id": 1}, headers=h)
    assert r1.status_code == 201 and r1.json()["status"] == "waiting" and r1.json()["queue_position"] == 1
    rid = r1.json()["id"]
    assert (await client.post(f"{S}/reservations", json={"registration": "professor", "film_id": 1}, headers=h)).status_code == 409
    assert (await client.post(f"{S}/reservations", json={"registration": "aluno", "film_id": 1}, headers=h)).status_code == 409  # já emprestado
    assert (await client.put(f"{S}/reservations/{rid}/confirm", headers=h)).status_code == 409

    # Devolução promove a reserva mais antiga e avisa.
    ret = await client.post(f"{S}/loans/{loan['id']}/return", json={"condition": "good"}, headers=h)
    assert ret.json()["promoted_reservations"] == [rid]
    res = await pool.fetchrow("SELECT status, pickup_until FROM reservations WHERE id=$1", rid)
    assert res["status"] == "ready" and res["pickup_until"] > datetime.now(timezone.utc) + timedelta(days=2)
    assert await notes(pool, who["professor"]) == ["Reserva pronta para retirada"]
    # O exemplar está separado: não vai para outra pessoa.
    taken = await client.post(f"{S}/loans", json={"user_registration": "aluno", "copy_code": "Q-1"}, headers=h)
    assert taken.status_code == 409
    # Empréstimo ao dono da reserva dá baixa (fulfilled).
    lent = await client.post(f"{S}/loans", json={"user_registration": "professor", "copy_code": "Q-1"}, headers=h)
    assert lent.status_code == 201 and lent.json()["reservation_fulfilled"] == rid
    assert await pool.fetchval("SELECT status FROM reservations WHERE id=$1", rid) == "fulfilled"
    await client.post(f"{S}/loans/{lent.json()['id']}/return", json={}, headers=h)

    # Expiração: ready vencida vira expired nas listagens e passa a vez ao próximo.
    a = (await client.post(f"{S}/reservations", json={"registration": "aluno", "film_id": 1}, headers=h)).json()["id"]
    assert (await client.put(f"{S}/reservations/{a}/confirm", headers=h)).json()["status"] == "ready"
    p = (await client.post(f"{S}/reservations", json={"registration": "professor", "film_id": 1}, headers=h)).json()["id"]
    await pool.execute("UPDATE reservations SET pickup_until=NOW()-INTERVAL '1 minute' WHERE id=$1", a)
    listing = {r["id"]: r["status"] for r in (await client.get(f"{S}/reservations", headers=h)).json()}
    assert listing[a] == "expired" and listing[p] == "ready"
    assert "Reserva expirada" in await notes(pool, who["aluno"])
    active = (await client.get(f"{S}/reservations", params={"status": "active"}, headers=h)).json()
    assert [r["id"] for r in active] == [p]

    # Cancelamento pelo funcionário avisa; checkout converte em empréstimo.
    assert (await client.delete(f"{S}/reservations/{p}", headers=h)).status_code == 200
    assert await notes(pool, who["professor"]) == ["Reserva pronta para retirada", "Reserva pronta para retirada", "Reserva cancelada"]
    assert (await client.delete(f"{S}/reservations/{p}", headers=h)).status_code == 409
    w = (await client.post(f"{S}/reservations", json={"registration": "professor", "film_id": 1}, headers=h)).json()["id"]
    out = await client.post(f"{S}/reservations/{w}/checkout", json={}, headers=h)
    assert out.status_code == 201 and out.json()["copy"] == "Q-1" and out.json()["reservation_fulfilled"] == w
    assert await pool.fetchval("SELECT status FROM reservations WHERE id=$1", w) == "fulfilled"
    w2 = (await client.post(f"{S}/reservations", json={"registration": "aluno", "film_id": 1}, headers=h)).json()["id"]
    assert (await client.post(f"{S}/reservations/{w2}/checkout", json={"copy_code": "Q-1"}, headers=h)).status_code == 409
    assert (await client.post(f"{S}/reservations/{w2}/checkout", json={"copy_code": "NOPE"}, headers=h)).status_code == 404
    assert (await client.get(f"{S}/stats", headers=h)).json()["pending_reservations"] == 1


async def test_reservation_limit(api):
    client, pool, headers = api
    h = headers("funcionario")
    await pool.execute("INSERT INTO films(id,title) VALUES(2,'Outro'),(3,'Terceiro')")
    await client.put(f"{S}/settings", json={"loan_days": 7, "max_items_per_user": 3, "max_reservations": 1,
                                          "reservation_pickup_days": 3}, headers=h)
    assert (await client.post(f"{S}/reservations", json={"registration": "aluno", "film_id": 1}, headers=h)).status_code == 201
    over = await client.post(f"{S}/reservations", json={"registration": "aluno", "film_id": 2}, headers=h)
    assert over.status_code == 409 and "Limite de 1" in over.json()["detail"]
    assert (await client.post(f"{S}/reservations", json={"registration": "aluno", "film_id": 99}, headers=h)).status_code == 404


async def test_users_management_and_inactive_login(api):
    client, pool, headers = api
    h = headers("funcionario")
    created = await client.post(f"{S}/users", json={"full_name": "Nova Aluna", "email": "Nova@Test.invalid",
                                                    "role": "aluno", "registration": "2026999"}, headers=h)
    assert created.status_code == 201
    data = created.json()
    pw = data["temporary_password"]
    assert len(pw) == 12 and data["user"]["email"] == "nova@test.invalid" and data["user"]["active"] is True
    uid = data["user"]["id"]
    assert await pool.fetchval("SELECT password_hash FROM users WHERE id=$1", uid) != pw
    login = {"login": "2026999", "password": pw, "role": "aluno"}
    assert (await client.post("/api/v1/auth/login", json=login)).status_code == 200

    assert (await client.post(f"{S}/users", json={"full_name": "X", "email": "nova@test.invalid", "role": "aluno"}, headers=h)).status_code == 409
    assert (await client.post(f"{S}/users", json={"full_name": "X", "email": "y@test.invalid", "role": "aluno", "registration": "2026999"}, headers=h)).status_code == 409
    assert (await client.post(f"{S}/users", json={"full_name": "F", "email": "f@test.invalid", "role": "funcionario"}, headers=h)).status_code == 403
    admin = await admin_headers(pool)
    assert (await client.post(f"{S}/users", json={"full_name": "F", "email": "f@test.invalid", "role": "funcionario"}, headers=admin)).status_code == 201

    edited = await client.put(f"{S}/users/{uid}", json={"full_name": "Nova A. Silva", "phone": "85999990000"}, headers=h)
    assert edited.status_code == 200 and edited.json()["full_name"] == "Nova A. Silva" and edited.json()["registration"] == "2026999"
    me = await pool.fetchval("SELECT id FROM profiles WHERE role='funcionario' AND registration='funcionario'")
    assert (await client.patch(f"{S}/users/{me}/status", json={"active": False}, headers=h)).status_code == 409
    off = await client.patch(f"{S}/users/{uid}/status", json={"active": False}, headers=h)
    assert off.status_code == 200 and off.json()["active"] is False
    refused = await client.post("/api/v1/auth/login", json=login)
    assert refused.status_code == 401 and refused.json()["detail"] == "Conta desativada"
    assert (await client.post("/api/v1/auth/login", json=login | {"password": "errada"})).json()["detail"] == "Credenciais inválidas"
    assert [u["id"] for u in (await client.get(f"{S}/users", params={"active": "false"}, headers=h)).json()] == [uid]
    assert [u["id"] for u in (await client.get(f"{S}/users", params={"q": "silva", "role": "aluno"}, headers=h)).json()] == [uid]
    assert (await client.patch(f"{S}/users/{uid}/status", json={"active": True}, headers=h)).json()["active"] is True
    assert (await client.post("/api/v1/auth/login", json=login)).status_code == 200
    assert (await client.patch(f"{S}/users/{uuid4()}/status", json={"active": False}, headers=h)).status_code == 404
    assert (await client.post(f"{S}/users", json={"full_name": "A", "email": "a@a.invalid", "role": "aluno"}, headers=headers("aluno"))).status_code == 403


async def test_rooms(api):
    client, pool, headers = api
    h = headers("funcionario")
    await pool.execute("SELECT setval(pg_get_serial_sequence('rooms','id'), 2)")  # o conftest insere ids fixos
    room = await client.post(f"{S}/rooms", json={"name": "Sala C", "capacity": 20, "equipment": ["Projetor", " ", "Projetor"]}, headers=h)
    assert room.status_code == 201
    rid = room.json()["id"]
    assert room.json() | {"id": 0} == {"id": 0, "name": "Sala C", "capacity": 20, "equipment": ["Projetor"],
                                       "status": "available", "occupied_now": False}
    now = datetime.now(timezone.utc)
    await pool.execute(
        "INSERT INTO room_bookings(room_id,starts_at,ends_at,title,status) VALUES($1,$2,$3,'Aula','confirmed')",
        rid, now - timedelta(minutes=30), now + timedelta(minutes=30),
    )
    rooms = {r["id"]: r for r in (await client.get(f"{S}/rooms", headers=h)).json()}
    assert rooms[rid]["occupied_now"] is True and rooms[1]["occupied_now"] is False
    upd = await client.put(f"{S}/rooms/{rid}", json={"capacity": 25, "status": "maintenance"}, headers=h)
    assert upd.status_code == 200 and upd.json()["capacity"] == 25 and upd.json()["status"] == "maintenance"
    assert (await client.put(f"{S}/rooms/999", json={"name": "X"}, headers=h)).status_code == 404
    assert (await client.post(f"{S}/rooms", json={"name": "", "capacity": 0}, headers=h)).status_code == 422


async def test_request_decision_notifies_professor(api):
    client, pool, headers = api
    who = await ids(pool)
    rid = await pool.fetchval(
        "INSERT INTO professor_requests(professor_id,request_type,payload) VALUES($1,'film','{}') RETURNING id", who["professor"]
    )
    res = await client.put(f"{S}/requests/{rid}", json={"status": "refused", "staff_notes": "Sem cópia"}, headers=headers("funcionario"))
    assert res.status_code == 200
    row = await pool.fetchrow("SELECT title, body FROM notifications WHERE user_id=$1", who["professor"])
    assert row["title"] == "Solicitação recusada" and "Sem cópia" in row["body"]
    assert (await client.put(f"{S}/requests/{rid}", json={"status": "x"}, headers=headers("funcionario"))).status_code == 422
    listed = (await client.get(f"{S}/requests", params={"status": "refused"}, headers=headers("funcionario"))).json()
    assert [r["id"] for r in listed] == [rid]


async def test_reports(api):
    client, pool, headers = api
    h = headers("funcionario")
    await pool.execute("UPDATE films SET genre='Drama' WHERE id=1")
    for code in ("R-1", "R-2"):
        await add_copy(pool, code)
    for code in ("R-1", "R-2"):
        await client.post(f"{S}/loans", json={"user_registration": "aluno", "copy_code": code}, headers=h)
    loan = await pool.fetchval("SELECT id FROM loans ORDER BY id LIMIT 1")
    await client.post(f"{S}/loans/{loan}/return", json={}, headers=h)
    rep = await client.get(f"{S}/reports", headers=h)
    assert rep.status_code == 200
    data = rep.json()
    assert [(f["name"], f["value"]) for f in data["top_films"]] == [("Filme teste", 2)]
    assert data["by_genre"] == [{"name": "Drama", "value": 2}]
    assert len(data["loans_by_day"]) == 30 and data["loans_by_day"][-1]["value"] == 2
    assert data["totals"] == {"loans": 2, "returns": 1, "late": 0, "reservations": 0, "sessions": 0,
                              "room_requests": 0, "equipment_requests": 0, "audience": 0}
    assert data["previous"]["loans"] == 0
    # Mapa de calor: 6 horários × dias úteis × salas ativas, sem ocupação no período.
    assert {h["slot"] for h in data["heat"]} == {"07:30", "09:20", "11:20", "13:30", "15:30", "18:10"}
    assert all(h["pct"] == 0 for h in data["heat"])
    assert data["room_requests"] == [] and data["equipment_usage"] == [] and data["projects"] == []
    assert {r["name"] for r in data["room_usage"]} == {"Sala A", "Sala B"}
    # Sala sem reserva no período: 0 ocupações e 0 hora (não o período inteiro).
    assert all(r["value"] == 0 and r["hours"] == 0 for r in data["room_usage"])
    assert (await client.get(f"{S}/reports", params={"from": "2026-02-01", "to": "2026-01-01"}, headers=h)).status_code == 422
    old = (await client.get(f"{S}/reports", params={"from": "2020-01-01", "to": "2020-01-03"}, headers=h)).json()
    assert old["totals"]["loans"] == 0 and len(old["loans_by_day"]) == 3


async def test_legacy_write_routes_removed(api):
    client, pool, headers = api
    h = headers("funcionario")
    assert (await client.post(f"{S}/inventory", json={"title": "x"}, headers=h)).status_code == 405
    assert (await client.post(f"{S}/events", json={"title": "x"}, headers=h)).status_code == 405
    assert (await client.get(f"{S}/events", params={"limit": 1}, headers=h)).status_code == 200
