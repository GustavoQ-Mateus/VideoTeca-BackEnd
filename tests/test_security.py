from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.hardening import login_limiter
from app.core.security import hash_password
from app.main import app
from app.routers import catalog


@pytest.fixture(autouse=True)
def fresh_limiter():
    login_limiter.clear()
    yield
    login_limiter.clear()


async def make_user(pool, email, password, role="aluno", registration=None):
    uid = uuid4()
    await pool.execute(
        "INSERT INTO users(id,email,password_hash) VALUES($1,$2,$3)", uid, email, hash_password(password)
    )
    await pool.execute(
        "INSERT INTO profiles(id,role,full_name,registration) VALUES($1,$2,$3,$4)",
        uid,
        role,
        "Pessoa Teste",
        registration,
    )
    return uid


async def test_security_headers_and_input_limits(api):
    client, pool, headers = api
    res = await client.get("/api/v1/catalog/films")
    assert res.status_code == 200
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in res.headers["permissions-policy"]
    assert "strict-transport-security" not in res.headers  # only in production
    assert len(res.headers["x-request-id"]) >= 8
    assert (await client.get("/api/v1/catalog/films?q=a%00b")).status_code == 400
    assert (await client.get("/api/v1/catalog/films?q=a%1Fb")).status_code == 400
    assert (await client.get("/api/v1/catalog/films?q=Cidade%20de%20Deus")).status_code == 200
    big = await client.post(
        "/api/v1/auth/login", content=b"{}", headers={"Content-Length": str(11 * 1024 * 1024 + 1)}
    )
    assert big.status_code == 413
    # Unauthenticated and wrong-role access to protected routes.
    assert (await client.get("/api/v1/staff/films")).status_code == 401
    bad = {"Authorization": "Bearer not-a-jwt"}
    assert (await client.get("/api/v1/auth/me", headers=bad)).status_code == 401
    assert (await client.get("/api/v1/staff/stats", headers=headers("aluno"))).status_code == 403
    assert (await client.get("/api/v1/student/loans", headers=headers("professor"))).status_code == 403


async def test_login_hardening_and_rate_limit(api):
    client, pool, headers = api
    await make_user(pool, "ana@test.invalid", "senha-correta-123", registration="2026001")
    url = "/api/v1/auth/login"
    ok = await client.post(url, json={"login": "2026001", "password": "senha-correta-123", "role": "aluno"})
    assert ok.status_code == 200 and ok.json()["user"]["email"] == "ana@test.invalid"
    unknown = await client.post(url, json={"login": "ninguem@x.invalid", "password": "x", "role": "aluno"})
    assert unknown.status_code == 401 and unknown.json()["detail"] == "Credenciais inválidas"
    # Over bcrypt's 72-byte limit or unknown role: validation error, never 500.
    assert (await client.post(url, json={"login": "a", "password": "é" * 100, "role": "aluno"})).status_code == 401
    assert (await client.post(url, json={"login": "a", "password": "x", "role": "root"})).status_code == 422
    assert (await client.post(url, json={"login": "a", "password": "x" * 129, "role": "aluno"})).status_code == 422
    login_limiter.clear()
    wrong = {"login": "ana@test.invalid", "password": "errada", "role": "aluno"}
    codes = [(await client.post(url, json=wrong)).status_code for _ in range(11)]
    assert codes == [401] * 10 + [429]
    blocked = await client.post(url, json=wrong | {"password": "senha-correta-123"})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    # Other logins from the same IP are still allowed (per IP+login window).
    assert (await client.post(url, json={"login": "outro", "password": "x", "role": "aluno"})).status_code == 401


async def test_register_requires_staff_and_admin_for_staff_roles(api):
    client, pool, headers = api
    url = "/api/v1/auth/register"
    body = {"email": "Novo@Test.invalid", "password": "segura-123", "full_name": "Novo", "role": "aluno"}
    assert (await client.post(url, json=body)).status_code == 401
    assert (await client.post(url, json=body, headers=headers("aluno"))).status_code == 403
    created = await client.post(url, json=body, headers=headers("funcionario"))
    assert created.status_code == 201
    assert await pool.fetchval("SELECT email FROM users WHERE id=$1", created.json()["id"]) == "novo@test.invalid"
    assert (await client.post(url, json=body, headers=headers("funcionario"))).status_code == 409
    staff = body | {"email": "func@test.invalid", "role": "admin"}
    assert (await client.post(url, json=staff, headers=headers("funcionario"))).status_code == 403
    assert (await client.post(url, json=body | {"password": "curta"}, headers=headers("funcionario"))).status_code == 422
    assert (await client.post(url, json=body | {"email": "sem-arroba"}, headers=headers("funcionario"))).status_code == 422


async def test_unhandled_errors_do_not_leak_details(monkeypatch):
    class Broken:
        async def fetch(self, *args):
            raise RuntimeError("SELECT secret FROM users -- ana@test.invalid")

    monkeypatch.setattr(catalog, "get_pool", Broken)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as raw:
        res = await raw.get("/api/v1/catalog/films")
    assert res.status_code == 500
    assert res.json() == {"detail": "Erro interno. Tente novamente mais tarde."}
    assert "secret" not in res.text and "Traceback" not in res.text


async def test_health_checks_database(api):
    client, pool, headers = api
    assert (await client.get("/health")).json() == {"status": "ok"}
