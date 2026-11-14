"""Migration 016: regras por filme, pedido de equipamento, sugestões, SAC, login sem perfil e sala para aluno."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.security import hash_password

NOW = datetime.now(timezone.utc)


async def film_with_copies(pool, title, copies=2, **rules):
    fid = await pool.fetchval("INSERT INTO films(title, status) VALUES($1,'available') RETURNING id", title)
    for n in range(copies):
        await pool.execute("INSERT INTO copies(film_id, code) VALUES($1,$2)", fid, f"R{fid}-{n}")
    if rules:
        sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(rules))
        await pool.execute(f"UPDATE films SET {sets} WHERE id = $1", fid, *rules.values())
    return fid


async def test_login_without_role_uses_account_role(api):
    client, pool, _ = api
    uid = uuid4()
    await pool.execute("INSERT INTO users(id,email,password_hash) VALUES($1,$2,$3)", uid, "semperfil@test.invalid", hash_password("segredo123"))
    await pool.execute("INSERT INTO profiles(id,role,full_name,registration) VALUES($1,'professor','Prof Sem Perfil','SIAPE77')", uid)
    ok = await client.post("/api/v1/auth/login", json={"login": "SIAPE77", "password": "segredo123"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["user"]["role"] == "professor"
    wrong = await client.post("/api/v1/auth/login", json={"login": "SIAPE77", "password": "segredo123", "role": "aluno"})
    assert wrong.status_code == 403


async def test_film_rules_drive_loans_renewals_and_reservations(api):
    client, pool, headers = api
    staff, aluno = headers("funcionario"), headers("aluno")

    fid = await film_with_copies(pool, "Regra curta")
    put = await client.put(f"/api/v1/staff/films/{fid}/rules", headers=staff,
                           json={"loan_days": 3, "max_renewals": 0, "pickup_days": 2, "queue_limit": 1, "local_only": False})
    assert put.status_code == 200, put.text
    got = await client.get(f"/api/v1/staff/films/{fid}/rules", headers=staff)
    assert got.json()["loan_days"] == 3 and got.json()["defaults"]["loan_days"] == 7

    lent = await client.post("/api/v1/staff/loans", headers=staff, json={"user_registration": "aluno", "copy_code": f"R{fid}-0"})
    assert lent.status_code == 201, lent.text
    due = datetime.fromisoformat(lent.json()["due_at"])
    assert timedelta(days=2, hours=23) < due - NOW < timedelta(days=3, hours=1)
    renew = await client.post(f"/api/v1/student/loans/{lent.json()['id']}/renew", headers=aluno)
    assert renew.status_code == 409 and "não pode ser renovado" in renew.json()["detail"]

    # Ajuste do prazo só neste empréstimo.
    custom = await client.post("/api/v1/staff/loans", headers=staff, json={"user_registration": "professor", "copy_code": f"R{fid}-1", "days": 10})
    assert custom.status_code == 201, custom.text
    assert datetime.fromisoformat(custom.json()["due_at"]) - NOW > timedelta(days=9)

    # Fila cheia: limite 1 pessoa esperando.
    other = uuid4()
    await pool.execute("INSERT INTO users(id,email,password_hash) VALUES($1,$2,'x')", other, f"{other.hex}@test.invalid")
    await pool.execute("INSERT INTO profiles(id,role,full_name,registration) VALUES($1,'aluno','Outra','OUT1')", other)
    await pool.execute("INSERT INTO reservations(user_id, film_id, status) VALUES($1,$2,'waiting')", other, fid)
    full = await client.post("/api/v1/student/reservations", headers=aluno, json={"film_id": fid})
    assert full.status_code == 409 and "fila" in full.json()["detail"]

    local = await film_with_copies(pool, "Só na sala", local_only=True)
    blocked = await client.post("/api/v1/staff/loans", headers=staff, json={"user_registration": "aluno", "copy_code": f"R{local}-0"})
    assert blocked.status_code == 409 and "só para assistir" in blocked.json()["detail"]
    no_res = await client.post("/api/v1/student/reservations", headers=aluno, json={"film_id": local})
    assert no_res.status_code == 409


async def test_equipment_request_flow(api):
    client, pool, headers = api
    staff, aluno = headers("funcionario"), headers("aluno")
    eid = await pool.fetchval("INSERT INTO equipment(name, category, quantity) VALUES('Projetor', 'Projetores', 2) RETURNING id")

    cat = await client.get("/api/v1/equipment/catalog", headers=aluno)
    assert cat.status_code == 200 and cat.json()[0]["available"] == 2

    body = {"items": [{"equipment_id": eid, "quantity": 1}], "pickup_at": (NOW + timedelta(days=1)).isoformat(),
            "return_at": (NOW + timedelta(days=1, hours=4)).isoformat(), "purpose": "Trabalho acadêmico"}
    too_many = await client.post("/api/v1/equipment/requests", headers=aluno, json=body | {"items": [{"equipment_id": eid, "quantity": 5}]})
    assert too_many.status_code == 409
    past = await client.post("/api/v1/equipment/requests", headers=aluno, json=body | {"pickup_at": (NOW - timedelta(hours=1)).isoformat()})
    assert past.status_code == 422
    made = await client.post("/api/v1/equipment/requests", headers=aluno, json=body)
    assert made.status_code == 201, made.text
    rid = made.json()[0]["id"]
    assert (await client.post("/api/v1/equipment/requests", headers=staff, json=body)).status_code == 403

    pending = await client.get("/api/v1/staff/equipment-requests?status=analysis", headers=staff)
    assert [r["id"] for r in pending.json()] == [rid]
    ok = await client.put(f"/api/v1/staff/equipment-requests/{rid}", headers=staff, json={"status": "approved", "staff_notes": "Retire às 13h"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    mine = await client.get("/api/v1/equipment/requests/me", headers=aluno)
    assert mine.json()[0]["staff_notes"] == "Retire às 13h"
    note = await pool.fetchval("SELECT title FROM notifications WHERE user_id = (SELECT id FROM profiles WHERE role='aluno') ORDER BY id DESC LIMIT 1")
    assert "aprovado" in note
    late_cancel = await client.delete(f"/api/v1/equipment/requests/{rid}", headers=aluno)
    assert late_cancel.status_code == 409


async def test_suggestions_flow(api):
    client, pool, headers = api
    staff, aluno, prof = headers("funcionario"), headers("aluno"), headers("professor")
    body = {"title": "Marte Um", "year": 2022, "project": "bibcine", "reason": "Cinema mineiro independente."}
    first = await client.post("/api/v1/suggestions", headers=aluno, json=body)
    assert first.status_code == 201, first.text
    assert (await client.post("/api/v1/suggestions", headers=aluno, json=body)).status_code == 409
    second = await client.post("/api/v1/suggestions", headers=prof, json=body | {"reason": "Para a aula."})
    assert second.json()["same_title"] == 2
    bad = await client.post("/api/v1/suggestions", headers=aluno, json=body | {"project": "cineclube"})
    assert bad.status_code == 422

    listed = await client.get("/api/v1/staff/suggestions?status=new", headers=staff)
    assert len(listed.json()) == 2
    done = await client.put(f"/api/v1/staff/suggestions/{first.json()['id']}", headers=staff, json={"status": "approved", "staff_notes": "Sexta, 04/12"})
    assert done.status_code == 200
    # As duas sugestões do mesmo título são decididas juntas e ambos os autores são avisados.
    statuses = await pool.fetch("SELECT status FROM film_suggestions")
    assert {r["status"] for r in statuses} == {"approved"}
    assert await pool.fetchval("SELECT COUNT(*) FROM notifications WHERE title LIKE 'Sua sugestão foi aprovada%'") == 2


async def test_contact_tickets_flow(api):
    client, pool, headers = api
    staff, aluno, prof = headers("funcionario"), headers("aluno"), headers("professor")
    opened = await client.post("/api/v1/contact/tickets", headers=aluno, json={"topic": "Cópia com defeito", "reference": "CP-1", "message": "O disco trava no fim."})
    assert opened.status_code == 201, opened.text
    t = opened.json()
    assert t["protocol"].startswith("SAC-") and t["status"] == "open" and len(t["messages"]) == 1

    assert (await client.post(f"/api/v1/contact/tickets/{t['id']}/messages", headers=prof, json={"body": "intruso"})).status_code == 404
    reply = await client.post(f"/api/v1/staff/contact/tickets/{t['id']}/reply", headers=staff, json={"body": "A cópia vai para revisão.", "close": False})
    assert reply.status_code == 200 and reply.json()["status"] == "answered"
    assert reply.json()["messages"][-1]["from_staff"] is True
    follow = await client.post(f"/api/v1/contact/tickets/{t['id']}/messages", headers=aluno, json={"body": "Obrigada!"})
    assert follow.json()["status"] == "open"
    closed = await client.post(f"/api/v1/staff/contact/tickets/{t['id']}/reply", headers=staff, json={"body": "Resolvido.", "close": True})
    assert closed.json()["status"] == "closed"
    again = await client.post(f"/api/v1/contact/tickets/{t['id']}/messages", headers=aluno, json={"body": "Mais uma"})
    assert again.status_code == 409
    mine = await client.get("/api/v1/contact/tickets/me", headers=aluno)
    assert len(mine.json()) == 1 and len(mine.json()[0]["messages"]) == 4
    assert (await client.get("/api/v1/staff/contact/tickets", headers=aluno)).status_code == 403


async def test_student_can_request_room(api):
    client, pool, headers = api
    aluno = headers("aluno")
    start = (NOW + timedelta(days=3)).replace(microsecond=0)
    body = {"room_id": 2, "title": "Grupo de estudo", "course": "Cinema (T01)", "registration": "aluno",
            "student_count": 5, "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=2)).isoformat()}
    made = await client.post("/api/v1/reservas", headers=aluno, json=body)
    assert made.status_code == 201, made.text
    assert await pool.fetchval("SELECT request_type FROM professor_requests ORDER BY id DESC LIMIT 1") == "room"
    avail = await client.get("/api/v1/salas/disponibilidade", headers=aluno,
                             params={"starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=2)).isoformat()})
    assert avail.status_code == 200
    mine = await client.get("/api/v1/rooms/requests/me", headers=aluno)
    assert mine.status_code == 200 and mine.json()[0]["payload"]["title"] == "Grupo de estudo"
