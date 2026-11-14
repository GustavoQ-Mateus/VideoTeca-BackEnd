from datetime import date, datetime, timedelta, timezone


async def test_rights_fields_validation_and_persistence(api):
    client, pool, headers = api
    h = headers("funcionario")
    url = "/api/v1/staff/films"
    created = await client.post(url, json={"title": "Sem direitos"}, headers=h)
    assert created.status_code == 201
    assert created.json()["rights_status"] == "pending"
    future = (date.today() + timedelta(days=90)).isoformat()
    for body in (
        {"rights_status": "authorized"},
        {"rights_status": "authorized", "rights_holder": "   "},
        {"rights_status": "authorized", "rights_holder": "Distribuidora", "rights_valid_until": "2020-01-01"},
        {"rights_status": "cancelled"},
        {"rights_status": "authorized", "rights_holder": "x" * 201},
        {"rights_status": "public_domain", "rights_notes": "x" * 2001},
        {"rights_status": "public_domain", "license_type": "x" * 81},
    ):
        res = await client.post(url, json={"title": "Filme"} | body, headers=h)
        assert res.status_code == 422, body
    authorized = {
        "title": "Autorizado",
        "rights_status": "authorized",
        "rights_holder": " Distribuidora XYZ ",
        "license_type": "exibicao_publica",
        "rights_valid_until": future,
        "rights_notes": "Contrato 12/2026",
    }
    film = (await client.post(url, json=authorized, headers=h)).json()
    assert (film["rights_status"], film["rights_holder"], film["license_type"]) == (
        "authorized",
        "Distribuidora XYZ",
        "exibicao_publica",
    )
    assert film["rights_valid_until"] == future
    domain = await client.post(url, json={"title": "Domínio", "rights_status": "public_domain"}, headers=h)
    assert domain.status_code == 201 and domain.json()["rights_holder"] == ""

    # PUT without rights_status keeps the stored authorization (old clients cannot wipe it).
    edited = await client.put(f"{url}/{film['id']}", json={"title": "Novo título"}, headers=h)
    assert edited.status_code == 200
    assert edited.json()["rights_status"] == "authorized"
    assert edited.json()["rights_holder"] == "Distribuidora XYZ"
    denied = await client.put(
        f"{url}/{film['id']}", json={"title": "Novo título", "rights_status": "denied"}, headers=h
    )
    assert denied.json()["rights_status"] == "denied" and denied.json()["rights_holder"] == ""
    listed = {f["id"]: f for f in (await client.get(url, headers=h)).json()}
    assert listed[film["id"]]["rights_status"] == "denied"
    assert (await client.post(url, json=authorized, headers=headers("professor"))).status_code == 403


async def test_cannot_revoke_rights_with_future_published_session(api):
    client, pool, headers = api
    h = headers("funcionario")
    await pool.execute("UPDATE films SET rights_status='public_domain' WHERE id=1")
    start = datetime.now(timezone.utc) + timedelta(days=5)
    session = await client.post(
        "/api/v1/staff/sessions",
        json={
            "title": "Sessão",
            "film_id": 1,
            "room_id": 1,
            "slots": 10,
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=2)).isoformat(),
        },
        headers=h,
    )
    sid = session.json()["id"]
    publish = f"/api/v1/staff/sessions/{sid}/publication"
    assert (await client.put(publish, json={"published": True}, headers=h)).status_code == 200
    revoke = {"title": "Filme teste", "rights_status": "denied"}
    res = await client.put("/api/v1/staff/films/1", json=revoke, headers=h)
    assert res.status_code == 409
    assert await pool.fetchval("SELECT rights_status FROM films WHERE id=1") == "public_domain"
    short = {
        "title": "Filme teste",
        "rights_status": "public_domain",
        "rights_valid_until": (start - timedelta(days=1)).date().isoformat(),
    }
    assert (await client.put("/api/v1/staff/films/1", json=short, headers=h)).status_code == 409
    assert (await client.put(publish, json={"published": False}, headers=h)).status_code == 200
    assert (await client.put("/api/v1/staff/films/1", json=revoke, headers=h)).status_code == 200
