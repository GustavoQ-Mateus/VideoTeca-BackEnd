import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from starlette.requests import Request

from app.core import hardening
from app.core.config import DEFAULT_JWT_SECRET, Settings
from app.core.hardening import LoginRateLimiter, SecurityMiddleware, has_control_chars, oversized


def test_control_chars_and_body_size():
    assert has_control_chars(b"q=%00")
    assert has_control_chars(b"q=a%0Ab")
    assert has_control_chars(b"q=%7F")
    assert has_control_chars(b"q=\x01")
    assert not has_control_chars(b"q=Cidade+de+Deus&limit=10")
    assert not has_control_chars("q=Ação".encode())
    assert not oversized(None)
    assert not oversized(str(11 * 1024 * 1024).encode())
    assert oversized(str(11 * 1024 * 1024 + 1).encode())
    assert oversized(b"-1") and oversized(b"abc")


def test_rate_limiter_window(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(hardening.time, "monotonic", lambda: clock[0])
    limiter = LoginRateLimiter(max_attempts=3, window=60)
    assert [limiter.hit("k") for _ in range(3)] == [None, None, None]
    retry = limiter.hit("k")
    assert retry and 1 <= retry <= 61
    assert limiter.hit("other") is None
    clock[0] += 61
    assert limiter.hit("k") is None
    limiter.reset("k")
    assert limiter.hit("k", limit=1) is None and limiter.hit("k", limit=1)


def test_rate_limiter_purges_stale_keys(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(hardening.time, "monotonic", lambda: clock[0])
    limiter = LoginRateLimiter(max_attempts=1, window=10, max_keys=2)
    for key in "abc":
        limiter.hit(key)
    clock[0] = 20
    limiter.hit("d")
    assert set(limiter._hits) == {"d"}


def test_check_login_rate_uses_ip_and_login(monkeypatch):
    limiter = LoginRateLimiter(max_attempts=2, window=60)
    monkeypatch.setattr(hardening, "login_limiter", limiter)
    request = Request({"type": "http", "client": ("10.0.0.1", 1), "headers": []})
    assert hardening.check_login_rate(request, " Ana@X.invalid ") == "10.0.0.1|ana@x.invalid"
    hardening.check_login_rate(request, "ana@x.invalid")
    with pytest.raises(HTTPException) as exc:
        hardening.check_login_rate(request, "ANA@x.invalid")
    assert exc.value.status_code == 429 and exc.value.headers["Retry-After"]


def mini_app(production):
    app = FastAPI()

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("token=abc senha=123")

    app.add_exception_handler(Exception, hardening.unhandled_error)
    app.add_middleware(SecurityMiddleware, production=production)
    return app


@pytest.mark.parametrize("production", [False, True])
async def test_middleware_headers(production):
    transport = ASGITransport(app=mini_app(production), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        res = await client.get("/ok")
        assert res.headers["x-frame-options"] == "DENY"
        assert ("strict-transport-security" in res.headers) is production
        rejected = await client.get("/ok?x=%00")
        assert rejected.status_code == 400 and rejected.headers["x-content-type-options"] == "nosniff"
        assert (await client.post("/ok", headers={"content-length": "999999999"})).status_code == 413
        boom = await client.get("/boom")
        assert boom.status_code == 500 and "abc" not in boom.text and "123" not in boom.text


def test_production_settings_guards():
    strong = "s" * 40
    assert Settings(environment="production", jwt_secret=strong, cors_origins="https://videoteca.unifor.br")
    for bad in (
        {"jwt_secret": DEFAULT_JWT_SECRET},
        {"jwt_secret": "short-secret"},
        {"jwt_secret": strong, "cors_origins": "*"},
    ):
        with pytest.raises(ValidationError):
            Settings(**({"environment": "production", "cors_origins": "https://a.b"} | bad))
    dev = Settings(environment="development")
    assert dev.docs_enabled and not dev.is_production
    prod = Settings(environment="Production", jwt_secret=strong)
    assert prod.is_production and not prod.docs_enabled
    assert Settings(environment="production", jwt_secret=strong, expose_docs=True).docs_enabled
