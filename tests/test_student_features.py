"""Funcionalidades do aluno, catálogo, eventos e /me (frente BE-A)."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.security import create_token
from app.notify import notify

NOW = datetime.now(timezone.utc)
S = "/api/v1/student"


async def new_user(pool, role="aluno", name="Outro Aluno"):
    uid = uuid4()
    await pool.execute("INSERT INTO users(id,email,password_hash) VALUES($1,$2,'x')", uid, f"{uid.hex}@test.invalid")
    await pool.execute(
        "INSERT INTO profiles(id,role,full_name,registration) VALUES($1,$2,$3,$4)", uid, role, name, uid.hex[:8]
    )
    return uid, {"Authorization": "Bearer " + create_token(str(uid), role)}


async def student_id(pool):
    return await pool.fetchval("SELECT id FROM profiles WHERE role='aluno'")


async def film(pool, title, *, copies=0, year=None, genre=None, status="available"):
    fid = await pool.fetchval(
        "INSERT INTO films(title,year,genre,status) VALUES($1,$2,$3,$4) RETURNING id", title, year, genre, status
    )
    cids = [
        await pool.fetchval("INSERT INTO copies(film_id,code) VALUES($1,$2) RETURNING id", fid, f"C{fid}-{n}")
        for n in range(copies)
    ]
    return fid, cids


async def loan(pool, user, copy_id, *, due=7, returned=False):
    return await pool.fetchval(
        "INSERT INTO loans(user_id,copy_id,borrowed_at,due_at,returned_at,status) "
        "VALUES($1,$2,NOW()-interval '20 days',NOW()+make_interval(days=>$3),$4,$5) RETURNING id",
        user, copy_id, due, NOW if returned else None, "returned" if returned else "borrowed",
    )


async def event(pool, *, start=2, hours=2, slots=10, status="published", film_id=None):
    starts = NOW + timedelta(days=start)
    return await pool.fetchval(
        "INSERT INTO events(title,event_type,location,starts_at,ends_at,slots,status,film_id) "
        "VALUES($1,'Cineclube','Auditório',$2,$3,$4,$5,$6) RETURNING id",
        f"Sessão {start}", starts, starts + timedelta(hours=hours), slots, status, film_id,
    )


async def test_catalog_availability_by_copies_and_sort(api):
    client, pool, headers = api
    aluno = await student_id(pool)
    alpha, (a1, a2) = await film(pool, "Alpha", copies=2, year=1990)
    beta, (b1,) = await film(pool, "Beta", copies=1, year=2020)
    gamma, _ = await film(pool, "Gamma", copies=1, year=2005, status="maintenance")
    await loan(pool, aluno, a2)  # uma cópia de Alpha emprestada
    for _ in range(3):
        await loan(pool, aluno, b1, returned=True)  # Beta é o mais emprestado
    await pool.execute("INSERT INTO reservations(user_id,film_id,status) VALUES($1,$2,'ready')", aluno, beta)

    url = "/api/v1/catalog/films"
    films = {f["title"]: f for f in (await client.get(url)).json()}
    assert [f for f in films] == ["Alpha", "Beta", "Filme teste", "Gamma"]
    assert (films["Alpha"]["total_copies"], films["Alpha"]["available_copies"]) == (2, 1)
    assert films["Alpha"]["availability"] == "available"
    # Única cópia de Beta está separada para a reserva pronta; Filme teste não tem cópias.
    assert (films["Beta"]["available_copies"], films["Beta"]["availability"]) == (0, "borrowed")
    assert (films["Filme teste"]["total_copies"], films["Filme teste"]["availability"]) == (0, "borrowed")
    assert films["Gamma"]["availability"] == "maintenance"
    assert "loans_count" not in films["Beta"]

    recent = [f["title"] for f in (await client.get(url, params={"sort": "recent"})).json()]
    assert recent == ["Beta", "Gamma", "Alpha", "Filme teste"]
    popular = [f["title"] for f in (await client.get(url, params={"sort": "popular"})).json()]
    assert popular[:2] == ["Beta", "Alpha"]
    assert (await client.get(url, params={"sort": "preco"})).status_code == 422
    detail = (await client.get(f"{url}/{alpha}")).json()
    assert (detail["available_copies"], detail["availability"]) == (1, "available")
    # Devolvida a cópia, a disponibilidade sobe.
    await pool.execute("UPDATE loans SET returned_at=NOW(), status='returned' WHERE copy_id=$1", a2)
    assert (await client.get(f"{url}/{alpha}")).json()["available_copies"] == 2


async def test_events_only_upcoming(api):
    client, pool, headers = api
    fid, _ = await film(pool, "Filme da sessão")
    await pool.execute("UPDATE films SET rights_status='public_domain' WHERE id=$1", fid)
    future = await event(pool, start=3, film_id=fid)
    ongoing = await event(pool, start=0, hours=3)  # começou agora, termina daqui a 3 h
    await pool.execute("UPDATE events SET starts_at=NOW()-interval '1 hour' WHERE id=$1", ongoing)
    await event(pool, start=-2)  # passado
    await event(pool, start=4, status="draft")
    public = (await client.get("/api/v1/events")).json()
    assert [e["id"] for e in public] == [ongoing, future]
    assert public[1]["film_title"] == "Filme da sessão" and "film_cover" in public[1] and public[1]["ends_at"]
    mine = (await client.get(f"{S}/events", headers=headers("aluno"))).json()
    assert [e["id"] for e in mine] == [ongoing, future]
    assert mine[1] | {"enrolled_by_me": False, "present": False, "film_title": "Filme da sessão"} == mine[1]


async def test_enroll_is_atomic_and_respects_slots(api):
    client, pool, headers = api
    eid = await event(pool, slots=1)
    _, other = await new_user(pool)
    results = await asyncio.gather(
        client.post(f"{S}/events/{eid}/enroll", headers=headers("aluno")),
        client.post(f"{S}/events/{eid}/enroll", headers=other),
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409]
    assert [r.json()["detail"] for r in results if r.status_code == 409] == ["Sessão lotada"]
    assert await pool.fetchval("SELECT enrolled FROM events WHERE id=$1", eid) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM event_registrations WHERE event_id=$1", eid) == 1
    winner = headers("aluno") if results[0].status_code == 200 else other
    again = await client.post(f"{S}/events/{eid}/enroll", headers=winner)
    assert (again.status_code, again.json()["detail"]) == (409, "Já inscrito neste evento")
    past = await event(pool, start=-1)
    assert (await client.post(f"{S}/events/{past}/enroll", headers=other)).status_code == 422
    assert (await client.post(f"{S}/events/999999/enroll", headers=other)).status_code == 404
    detail = (await client.get(f"{S}/events/{eid}", headers=winner)).json()
    assert (detail["enrolled_by_me"], detail["present"], detail["enrolled"]) == (True, False, 1)


async def test_unenroll_blocked_after_start_or_presence(api):
    client, pool, headers = api
    h, aluno = headers("aluno"), await student_id(pool)
    eid = await event(pool)
    assert (await client.post(f"{S}/events/{eid}/enroll", headers=h)).status_code == 200
    await pool.execute("UPDATE event_registrations SET present=true WHERE event_id=$1", eid)
    blocked = await client.delete(f"{S}/events/{eid}/enroll", headers=h)
    assert blocked.status_code == 409 and "Presença" in blocked.json()["detail"]
    await pool.execute("UPDATE event_registrations SET present=false WHERE event_id=$1", eid)
    ok = await client.delete(f"{S}/events/{eid}/enroll", headers=h)
    assert ok.status_code == 200 and ok.json()["enrolled"] == 0
    assert (await client.delete(f"{S}/events/{eid}/enroll", headers=h)).status_code == 404
    started = await event(pool, start=-1, hours=48)
    await pool.execute("INSERT INTO event_registrations(event_id,user_id) VALUES($1,$2)", started, aluno)
    late = await client.delete(f"{S}/events/{started}/enroll", headers=h)
    assert late.status_code == 409 and "começou" in late.json()["detail"]


async def test_favorites_crud(api):
    client, pool, headers = api
    h = headers("aluno")
    fid, _ = await film(pool, "Favorito", copies=1, year=2001, genre="Drama")
    for _ in range(2):  # idempotente
        assert (await client.post(f"{S}/favorites/{fid}", headers=h)).status_code == 201
    favs = (await client.get(f"{S}/favorites", headers=h)).json()
    assert len(favs) == 1
    assert favs[0] | {"id": fid, "title": "Favorito", "year": 2001, "genre": "Drama", "availability": "available"} == favs[0]
    assert (await client.post(f"{S}/favorites/999999", headers=h)).status_code == 404
    assert (await client.delete(f"{S}/favorites/{fid}", headers=h)).status_code == 200
    assert (await client.get(f"{S}/favorites", headers=h)).json() == []


async def test_loan_renewal_rules(api):
    client, pool, headers = api
    h, aluno = headers("aluno"), await student_id(pool)
    other_id, other = await new_user(pool)
    fid, (c1, c2, c3, c4) = await film(pool, "Renovável", copies=4)
    lid = await loan(pool, aluno, c1, due=2)
    due = await pool.fetchval("SELECT due_at FROM loans WHERE id=$1", lid)
    ok = await client.post(f"{S}/loans/{lid}/renew", headers=h)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["id"] == lid and body["renewals"] == 1
    assert datetime.fromisoformat(body["due_at"]) - due == timedelta(days=7)
    twice = await client.post(f"{S}/loans/{lid}/renew", headers=h)
    assert twice.status_code == 409 and "já foi renovado" in twice.json()["detail"]

    late = await loan(pool, aluno, c2, due=-1)
    blocked = await client.post(f"{S}/loans/{late}/renew", headers=h)
    assert blocked.status_code == 409 and "atraso" in blocked.json()["detail"]
    statuses = {r["id"]: r["status"] for r in (await client.get(f"{S}/loans", headers=h)).json()}
    assert statuses[late] == "late" and statuses[lid] == "borrowed"

    returned = await loan(pool, aluno, c3, returned=True)
    assert (await client.post(f"{S}/loans/{returned}/renew", headers=h)).status_code == 409
    assert (await client.post(f"{S}/loans/{lid}/renew", headers=other)).status_code == 404

    queued = await loan(pool, aluno, c4)
    await pool.execute("INSERT INTO reservations(user_id,film_id,status) VALUES($1,$2,'waiting')", other_id, fid)
    waiting = await client.post(f"{S}/loans/{queued}/renew", headers=h)
    assert waiting.status_code == 409 and "aguardando" in waiting.json()["detail"]
    # Prazo da renovação segue app_settings.loan_days.
    await pool.execute("UPDATE reservations SET status='cancelled'")
    await pool.execute("UPDATE app_settings SET loan_days=3")
    due = await pool.fetchval("SELECT due_at FROM loans WHERE id=$1", queued)
    body = (await client.post(f"{S}/loans/{queued}/renew", headers=h)).json()
    assert datetime.fromisoformat(body["due_at"]) - due == timedelta(days=3)


async def test_reservations_limits_and_stats(api):
    client, pool, headers = api
    h, aluno = headers("aluno"), await student_id(pool)
    f1, _ = await film(pool, "R1")
    f2, _ = await film(pool, "R2")
    f3, _ = await film(pool, "R3")
    await pool.execute("INSERT INTO reservations(user_id,film_id,status) VALUES($1,$2,'ready')", aluno, f1)
    dup = await client.post(f"{S}/reservations", json={"film_id": f1}, headers=h)
    assert dup.status_code == 400  # 'ready' também conta como reserva ativa
    assert (await client.post(f"{S}/reservations", json={"film_id": f2}, headers=h)).status_code == 200
    full = await client.post(f"{S}/reservations", json={"film_id": f3}, headers=h)
    assert full.status_code == 409 and "Limite de 2" in full.json()["detail"]
    assert (await client.post(f"{S}/reservations", json={"film_id": 999999}, headers=h)).status_code == 404
    await pool.execute("INSERT INTO notifications(user_id,title,read) VALUES($1,'a',false),($1,'b',true)", aluno)
    stats = (await client.get(f"{S}/stats", headers=h)).json()
    assert stats["reservations"] == 2 and stats["notifications"] == 1
    ready = await pool.fetchval("SELECT id FROM reservations WHERE film_id=$1", f1)
    assert (await client.delete(f"{S}/reservations/{ready}", headers=h)).status_code == 200


async def test_me_profile_and_prefs(api):
    client, pool, headers = api
    h = headers("professor")
    me = (await client.get("/api/v1/me/profile", headers=h)).json()
    assert set(me) == {"id", "email", "full_name", "role", "registration", "course", "phone"}
    assert me["email"] == "professor@test.invalid" and me["role"] == "professor"
    upd = await client.put("/api/v1/me/profile", json={"full_name": "Prof. Ana", "phone": "(85) 3477-0000"}, headers=h)
    assert upd.status_code == 200 and upd.json() | {"full_name": "Prof. Ana", "course": "Cinema", "phone": "(85) 3477-0000"} == upd.json()
    cleared = (await client.put("/api/v1/me/profile", json={"course": ""}, headers=h)).json()
    assert cleared["course"] is None and cleared["full_name"] == "Prof. Ana"
    for bad in ({"email": "x@y.z"}, {"registration": "123"}, {"full_name": "x" * 121}, {"full_name": None}, {"phone": "abc"}):
        assert (await client.put("/api/v1/me/profile", json=bad, headers=h)).status_code == 422, bad
    assert (await client.get("/api/v1/me/profile")).status_code == 401

    url = "/api/v1/me/notification-prefs"
    assert (await client.get(url, headers=h)).json() == {"reservations": True, "events": True, "reminders": True}
    prefs = {"reservations": False, "events": True, "reminders": True}
    assert (await client.put(url, json=prefs, headers=h)).json() == prefs
    assert (await client.get(url, headers=h)).json() == prefs
    prof = await pool.fetchval("SELECT id FROM profiles WHERE role='professor'")
    async with pool.acquire() as conn:
        assert await notify(conn, prof, "Reserva", category="reservations") is False
        assert await notify(conn, prof, "Sessão", category="events") is True


async def test_notifications_read_and_count(api):
    client, pool, headers = api
    aluno = await student_id(pool)
    prof = await pool.fetchval("SELECT id FROM profiles WHERE role='professor'")
    mine = await pool.fetchval("INSERT INTO notifications(user_id,title) VALUES($1,'Minha') RETURNING id", aluno)
    await pool.execute("INSERT INTO notifications(user_id,title,read) VALUES($1,'Lida',true)", aluno)
    theirs = await pool.fetchval("INSERT INTO notifications(user_id,title) VALUES($1,'Dele') RETURNING id", prof)
    h = headers("aluno")
    assert (await client.get("/api/v1/me/notifications/unread-count", headers=h)).json() == {"count": 1}
    assert [n["title"] for n in (await client.get("/api/v1/me/notifications", params={"unread": "true"}, headers=h)).json()] == ["Minha"]
    assert len((await client.get("/api/v1/me/notifications", headers=h)).json()) == 2
    assert (await client.put(f"{S}/notifications/{theirs}/read", headers=h)).status_code == 404
    assert (await client.put(f"/api/v1/me/notifications/{theirs}/read", headers=h)).status_code == 404
    assert (await client.put(f"{S}/notifications/{mine}/read", headers=h)).status_code == 200
    assert (await client.get("/api/v1/me/notifications/unread-count", headers=h)).json() == {"count": 0}
    assert await pool.fetchval("SELECT read FROM notifications WHERE id=$1", theirs) is False
    assert (await client.get("/api/v1/me/notifications/unread-count", headers=headers("funcionario"))).json() == {"count": 0}
