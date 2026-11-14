"""Migration 017: turmas, aula só para a turma e contato editável."""

from datetime import datetime, timedelta, timezone

NOW = datetime.now(timezone.utc)


async def test_turmas_and_class_visibility(api):
    client, pool, headers = api
    staff, prof, aluno = headers("funcionario"), headers("professor"), headers("aluno")

    made = await client.post("/api/v1/staff/turmas", headers=staff, json={"code": "T02", "discipline": "Teorias da Imagem", "professor_registration": "professor"})
    assert made.status_code == 201, made.text
    tid = made.json()["id"]
    other = await client.post("/api/v1/staff/turmas", headers=staff, json={"code": "T09", "discipline": "Outra", "professor_registration": "professor"})
    oid = other.json()["id"]
    assert (await client.post("/api/v1/staff/turmas", headers=staff, json={"code": "T02", "discipline": "Teorias da Imagem", "professor_registration": "professor"})).status_code == 409
    assert (await client.post("/api/v1/staff/turmas", headers=staff, json={"code": "X", "discipline": "Sem prof", "professor_registration": "aluno"})).status_code == 404

    add = await client.post(f"/api/v1/staff/turmas/{tid}/alunos", headers=staff, json={"registration": "aluno"})
    assert add.status_code == 201, add.text
    listed = await client.get(f"/api/v1/staff/turmas/{tid}/alunos", headers=staff)
    assert [s["registration"] for s in listed.json()] == ["aluno"]

    mine = await client.get("/api/v1/turmas/me", headers=prof)
    assert {t["label"] for t in mine.json()} == {"Teorias da Imagem (T02)", "Outra (T09)"}
    assert [t["id"] for t in (await client.get("/api/v1/turmas/me", headers=aluno)).json()] == [tid]

    start = (NOW + timedelta(days=2)).replace(microsecond=0)
    body = {"room_id": 1, "title": "Exibição e debate", "course": "Teorias da Imagem", "registration": "professor",
            "student_count": 20, "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=2)).isoformat(), "turma_ids": [tid]}
    room = await client.post("/api/v1/reservas", headers=prof, json=body)
    assert room.status_code == 201, room.text
    film_id = await pool.fetchval("INSERT INTO films(title, status) VALUES('Aula film','available') RETURNING id")
    film = await client.post("/api/v1/professor/requests/film", headers=prof,
                             json={"film_id": film_id, "date": (NOW + timedelta(days=4)).date().isoformat(), "turma_ids": [oid]})
    assert film.status_code == 200, film.text

    # Turma de outro professor / aluno informando turma: recusado.
    assert (await client.post("/api/v1/reservas", headers=aluno, json=body | {"registration": "aluno"})).status_code == 422
    assert (await client.post("/api/v1/professor/requests/film", headers=prof, json={"film_id": film_id, "date": "2030-01-01", "turma_ids": [999999]})).status_code == 422

    # Em análise: o aluno ainda não vê.
    assert (await client.get("/api/v1/aulas/me", headers=aluno)).json() == []
    await pool.execute("UPDATE professor_requests SET status = 'approved'")
    aulas = (await client.get("/api/v1/aulas/me", headers=aluno)).json()
    assert len(aulas) == 1 and aulas[0]["kind"] == "room" and aulas[0]["turmas"] == ["Teorias da Imagem (T02)"]
    payload = await pool.fetchval("SELECT payload->'turmas' FROM professor_requests WHERE request_type='room'")
    assert "Teorias da Imagem (T02)" in payload
    assert (await client.get("/api/v1/aulas/me", headers=prof)).status_code == 403

    assert (await client.delete(f"/api/v1/staff/turmas/{tid}/alunos/{add.json()['user_id']}", headers=staff)).status_code == 204
    assert (await client.get("/api/v1/aulas/me", headers=aluno)).json() == []


async def test_contact_info_editable(api):
    client, pool, headers = api
    staff, aluno = headers("funcionario"), headers("aluno")
    info = await client.get("/api/v1/contact/info", headers=aluno)
    assert info.status_code == 200 and info.json()["contact_email"] == "videoteca@unifor.br"
    body = info.json() | {"contact_hours": "Seg. a sex., 8h às 20h", "notice": "Fechado no feriado de 15/11."}
    assert (await client.put("/api/v1/staff/contact/info", headers=aluno, json=body)).status_code == 403
    bad = await client.put("/api/v1/staff/contact/info", headers=staff, json=body | {"contact_email": "sem-arroba"})
    assert bad.status_code == 422
    ok = await client.put("/api/v1/staff/contact/info", headers=staff, json=body)
    assert ok.status_code == 200 and ok.json()["notice"].startswith("Fechado")
    assert (await client.get("/api/v1/contact/info", headers=aluno)).json()["contact_hours"] == "Seg. a sex., 8h às 20h"
