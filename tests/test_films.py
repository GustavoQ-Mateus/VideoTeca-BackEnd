import io
from PIL import Image


async def test_crud_and_poster_validation(api):
    client, pool, headers = api
    h = headers("funcionario")
    res = await client.post(
        "/api/v1/staff/films", json={"title": "Novo filme", "year": 2026}, headers=h
    )
    assert res.status_code == 201, res.text
    film = res.json()
    path = f"/api/v1/staff/films/{film['id']}"
    assert (
        await client.put(path, json={"title": "Filme editado", "year": 2025}, headers=h)
    ).status_code == 200
    assert (await client.put(path, json={"title": "   "}, headers=h)).status_code == 422
    image = io.BytesIO()
    Image.new("RGB", (10, 10)).save(image, format="PNG")
    result = await client.post(
        path + "/poster",
        files={"file": ("x.png", image.getvalue(), "image/png")},
        headers=h,
    )
    assert result.status_code == 200, result.text
    poster = await client.get(result.json()["cover"])
    assert poster.status_code == 200 and poster.headers["content-type"] == "image/jpeg"
    assert (
        await client.post(
            path + "/poster",
            files={"file": ("x.png", b"<script>alert(1)</script>", "image/png")},
            headers=h,
        )
    ).status_code == 422
    assert (
        await client.post(
            path + "/poster",
            files={"file": ("x.png", b"x" * 5242881, "image/png")},
            headers=h,
        )
    ).status_code == 413
    assert (await client.delete(path, headers=h)).status_code == 200
    assert (await client.get(result.json()["cover"])).status_code == 404
    assert (
        await client.post(
            "/api/v1/staff/films", json={"title": "No"}, headers=headers("aluno")
        )
    ).status_code == 403
