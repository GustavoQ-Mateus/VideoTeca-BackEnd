"""Logs estruturados e métricas CloudWatch EMF por requisição (T31.1).

Uma linha JSON por requisição em stdout (coletada pelo awslogs do ECS). Nunca registra corpo,
query string, headers, tokens, e-mail ou senha: o caminho é o template da rota (`/films/{film_id}`).
"""

import json
import logging
import os
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

from starlette.routing import Match

from app.core.config import settings

NAMESPACE = "Videoteca/API"
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
logger = logging.getLogger("videoteca.access")


def emf_enabled() -> bool:
    return settings.environment.lower() != "development" or os.environ.get("EMF_ENABLED") == "1"


def route_template(app, scope) -> str:
    route = scope.get("route")
    if route is not None and getattr(route, "path", None):
        return route.path
    for candidate in getattr(app, "routes", []):
        match, _ = candidate.matches(scope)
        if match == Match.FULL:
            return getattr(candidate, "path", "unmatched")
    return "unmatched"  # never the raw path: it may carry identifiers (high cardinality/PII)


def build_record(request_id, method, path, status, duration_ms, emf, now=None):
    now = now or datetime.now(timezone.utc)
    record = {
        "ts": now.isoformat(timespec="milliseconds"),
        "level": "error" if status >= 500 else ("warning" if status >= 400 else "info"),
        "request_id": request_id,
        "method": method,
        "path": path,
        "status": status,
        "duration_ms": round(duration_ms, 2),
    }
    if emf:
        record |= {
            "_aws": {
                "Timestamp": int(now.timestamp() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": NAMESPACE,
                        "Dimensions": [["Route"]],
                        "Metrics": [
                            {"Name": "Latency", "Unit": "Milliseconds"},
                            {"Name": "Errors5xx", "Unit": "Count"},
                        ],
                    }
                ],
            },
            "Route": f"{method} {path}",
            "Latency": record["duration_ms"],
            "Errors5xx": 1 if status >= 500 else 0,
        }
    return record


class ObservabilityMiddleware:
    def __init__(self, app, root=None):
        self.app = app
        self.root = root

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status = 500

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            record = build_record(
                request_id,
                scope["method"],
                route_template(self.root, scope),
                status,
                (time.perf_counter() - started) * 1000,
                emf_enabled(),
            )
            logger.info(json.dumps(record, ensure_ascii=False))
            request_id_var.reset(token)


def _configure_logger():
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False


def install(app):
    _configure_logger()
    app.add_middleware(ObservabilityMiddleware, root=app)
