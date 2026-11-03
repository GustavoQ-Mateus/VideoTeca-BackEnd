import hashlib
from datetime import datetime, timedelta, timezone

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
BASE = "/api/v1/staff/films/1/rights-proofs"


async def test_upload_list_download_delete(api):
    client, pool, headers = api
    h = headers("funcionario")
    assert (await client.get(BASE, headers=h)).json() == []
    res = await client.post(
        BASE, files={"file": ("..\\..\\etc\\Contrato<b>|* Ação.PDF", PDF, "text/plain")}, headers=h
    )
    assert res.status_code == 201, res.text
    proof = res.json()
    assert proof["media_type"] == "application/pdf"  # magic bytes win over declared type
    assert proof["filename"] == "Contratob Ação.pdf"
    assert proof["size"] == len(PDF) and proof["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert proof["uploaded_by_name"] == "funcionario"
    for name, data, media in (("foto.png", PNG, "image/png"), ("scan", JPEG, "image/jpeg")):
        item = (await client.post(BASE, files={"file": (name, data, "application/octet-stream")}, headers=h)).json()
        assert item["media_type"] == media
    assert item["filename"] == "scan.jpg"
    listed = (await client.get(BASE, headers=h)).json()
    assert len(listed) == 3 and "content" not in listed[0]

    download = await client.get(f"{BASE}/{proof['id']}", headers=h)
    assert download.status_code == 200 and download.content == PDF
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert "filename*=UTF-8''Contratob%20A%C3%A7%C3%A3o.pdf" in download.headers["content-disposition"]
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["cache-control"] == "private, no-store"
    # Proof id must belong to the film in the path.
    other = await pool.fetchval("INSERT INTO films(title) VALUES('Outro') RETURNING id")
    assert (await client.get(f"/api/v1/staff/films/{other}/rights-proofs/{proof['id']}", headers=h)).status_code == 404
    assert (await client.delete(f"/api/v1/staff/films/{other}/rights-proofs/{proof['id']}", headers=h)).status_code == 404
    assert (await client.delete(f"{BASE}/{proof['id']}", headers=h)).json() == {"ok": True}
    assert (await client.get(f"{BASE}/{proof['id']}", headers=h)).status_code == 404
    assert len((await client.get(BASE, headers=h)).json()) == 2


async def test_upload_validation_and_access(api):
    client, pool, headers = api
    h = headers("funcionario")
    fake = await client.post(BASE, files={"file": ("x.pdf", b"<html><script>", "application/pdf")}, headers=h)
    assert fake.status_code == 422
    assert (await client.post(BASE, files={"file": ("x.pdf", b"", "application/pdf")}, headers=h)).status_code == 422
    big = await client.post(
        BASE, files={"file": ("x.pdf", PDF + b"0" * (10 * 1024 * 1024), "application/pdf")}, headers=h
    )
    assert big.status_code == 413
    assert (await client.post("/api/v1/staff/films/999/rights-proofs", files={"file": ("x.pdf", PDF)}, headers=h)).status_code == 404
    for role in ("aluno", "professor"):
        assert (await client.get(BASE, headers=headers(role))).status_code == 403
        assert (await client.post(BASE, files={"file": ("x.pdf", PDF)}, headers=headers(role))).status_code == 403
    assert (await client.get(BASE)).status_code == 401
    assert await pool.fetchval("SELECT COUNT(*) FROM film_rights_proofs") == 0


async def test_last_proof_cannot_be_removed_while_session_published(api):
    client, pool, headers = api
    h = headers("funcionario")
    await pool.execute("UPDATE films SET rights_status='authorized',rights_holder='Distribuidora' WHERE id=1")
    proof = (await client.post(BASE, files={"file": ("c.pdf", PDF)}, headers=h)).json()
    start = datetime.now(timezone.utc) + timedelta(days=2)
    sid = (
        await client.post(
            "/api/v1/staff/sessions",
            json={
                "title": "Sessão",
                "film_id": 1,
                "room_id": 1,
                "slots": 5,
                "starts_at": start.isoformat(),
                "ends_at": (start + timedelta(hours=2)).isoformat(),
            },
            headers=h,
        )
    ).json()["id"]
    path = f"/api/v1/staff/sessions/{sid}/publication"
    assert (await client.put(path, json={"published": True}, headers=h)).status_code == 200
    assert (await client.delete(f"{BASE}/{proof['id']}", headers=h)).status_code == 409
    assert await pool.fetchval("SELECT COUNT(*) FROM film_rights_proofs") == 1
    assert (await client.put(path, json={"published": False}, headers=h)).status_code == 200
    assert (await client.delete(f"{BASE}/{proof['id']}", headers=h)).status_code == 200
