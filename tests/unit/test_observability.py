import json
import logging
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core import observability
from app.core.observability import build_record

FORBIDDEN = ("ana@test.invalid", "segredo-123", "eyJhbGciOi", "q=")


def test_record_shape_without_emf():
    now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    record = build_record("req-12345678", "GET", "/api/v1/films/{film_id}", 200, 12.345, False, now)
    assert record == {
        "ts": "2026-10-29T12:00:00.000+00:00",
        "level": "info",
        "request_id": "req-12345678",
        "method": "GET",
        "path": "/api/v1/films/{film_id}",
        "status": 200,
        "duration_ms": 12.35,
    }
    assert build_record("r", "GET", "/x", 404, 1, False)["level"] == "warning"


def test_record_with_emf():
    record = build_record("r", "POST", "/api/v1/auth/login", 503, 80, True)
    metrics = record["_aws"]["CloudWatchMetrics"][0]
    assert metrics["Namespace"] == "Videoteca/API" and metrics["Dimensions"] == [["Route"]]
    assert {m["Name"] for m in metrics["Metrics"]} == {"Latency", "Errors5xx"}
    assert (record["Route"], record["Latency"], record["Errors5xx"], record["level"]) == (
        "POST /api/v1/auth/login",
        80,
        1,
        "error",
    )


def test_emf_toggle(monkeypatch):
    monkeypatch.delenv("EMF_ENABLED", raising=False)
    monkeypatch.setattr(observability.settings, "environment", "development")
    assert not observability.emf_enabled()
    monkeypatch.setenv("EMF_ENABLED", "1")
    assert observability.emf_enabled()
    monkeypatch.delenv("EMF_ENABLED")
    monkeypatch.setattr(observability.settings, "environment", "production")
    assert observability.emf_enabled()


@pytest.fixture
def app_and_logs(monkeypatch):
    monkeypatch.setattr(observability.settings, "environment", "development")
    app = FastAPI()

    @app.post("/login/{user_id}")
    async def login(user_id: str, body: dict):
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("fail")

    observability.install(app)
    records = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(json.loads(r.getMessage()))
    observability.logger.addHandler(handler)
    yield app, records
    observability.logger.removeHandler(handler)


async def test_middleware_logs_template_without_pii(app_and_logs):
    app, records = app_and_logs
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        res = await client.post(
            "/login/ana@test.invalid?q=segredo-123",
            json={"email": "ana@test.invalid", "password": "segredo-123"},
            headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.x.y", "X-Request-ID": "abc12345-trace"},
        )
        assert res.headers["x-request-id"] == "abc12345-trace"
        bad_id = await client.get("/missing", headers={"X-Request-ID": "<script>"})
        assert bad_id.status_code == 404 and len(bad_id.headers["x-request-id"]) == 32
    first, second = records
    assert first["path"] == "/login/{user_id}" and first["status"] == 200
    assert first["request_id"] == "abc12345-trace" and first["method"] == "POST"
    assert second["path"] == "unmatched" and second["status"] == 404
    text = json.dumps(records)
    assert not any(value in text for value in FORBIDDEN)
    assert "_aws" not in first


async def test_middleware_logs_unhandled_errors_as_500(app_and_logs):
    app, records = app_and_logs
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        assert (await client.get("/boom")).status_code == 500
    assert records[-1]["status"] == 500 and records[-1]["level"] == "error"
