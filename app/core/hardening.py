"""Proteções transversais da API (T22.1): headers, limites de entrada, erros genéricos e rate limit."""

import logging
import re
import time
from collections import deque
from threading import Lock
from urllib.parse import unquote_to_bytes

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger("videoteca.security")
MAX_BODY_BYTES = 11 * 1024 * 1024
CONTROL_CHARS = re.compile(rb"[\x00-\x1f\x7f]")
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
]
HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


def has_control_chars(raw: bytes) -> bool:
    """True when the (percent-decoded) query string carries NUL or other control characters."""
    return bool(CONTROL_CHARS.search(unquote_to_bytes(raw.replace(b"+", b" "))))


def oversized(content_length) -> bool:
    if content_length is None:
        return False
    value = content_length.decode("latin-1").strip()
    return not value.isdigit() or int(value) > MAX_BODY_BYTES


class SecurityMiddleware:
    """Pure ASGI middleware (no BaseHTTPMiddleware) so streaming and error handlers keep working."""

    def __init__(self, app, production: bool | None = None):
        self.app = app
        self.production = settings.is_production if production is None else production

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        if oversized(headers.get(b"content-length")):
            return await self._reject(scope, send, 413, "Requisição excede o tamanho máximo permitido")
        if has_control_chars(scope.get("query_string", b"")) or CONTROL_CHARS.search(
            scope["path"].encode("utf-8", "surrogateescape")
        ):
            return await self._reject(scope, send, 400, "Parâmetros contêm caracteres inválidos")
        extra = SECURITY_HEADERS + ([HSTS] if self.production else [])

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    h for h in extra if h[0] not in present
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)

    async def _reject(self, scope, send, status, detail):
        response = JSONResponse({"detail": detail}, status_code=status)
        response.raw_headers.extend(SECURITY_HEADERS)
        await response(scope, None, send)


class LoginRateLimiter:
    """In-memory sliding window (per process). Keys: IP+login and IP alone (spraying)."""

    def __init__(self, max_attempts=10, window=300, max_keys=50000):
        self.max_attempts, self.window, self.max_keys = max_attempts, window, max_keys
        self._hits: dict[str, deque] = {}
        self._lock = Lock()

    def hit(self, key: str, limit: int | None = None) -> int | None:
        """Registers an attempt; returns Retry-After seconds when the limit is exceeded."""
        limit = limit or self.max_attempts
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > self.max_keys:
                self._purge(now)
            bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] <= now - self.window:
                bucket.popleft()
            if len(bucket) >= limit:
                return max(1, int(bucket[0] + self.window - now) + 1)
            bucket.append(now)
            return None

    def reset(self, key: str):
        with self._lock:
            self._hits.pop(key, None)

    def clear(self):
        with self._lock:
            self._hits.clear()

    def _purge(self, now):
        for key in [k for k, b in self._hits.items() if not b or b[-1] <= now - self.window]:
            del self._hits[key]


login_limiter = LoginRateLimiter(settings.login_max_attempts, settings.login_window_seconds)


def check_login_rate(request: Request, login: str) -> str:
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}|{login.strip().lower()}"
    retry = login_limiter.hit("ip|" + ip, login_limiter.max_attempts * 5) or login_limiter.hit(key)
    if retry:
        raise HTTPException(
            429,
            "Muitas tentativas de login. Aguarde alguns minutos e tente novamente.",
            headers={"Retry-After": str(retry)},
        )
    return key


async def unhandled_error(request: Request, exc: Exception):
    # Never echo exception text: asyncpg messages may contain user data.
    logger.error("unhandled_error type=%s", type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Erro interno. Tente novamente mais tarde."})


def install(app: FastAPI):
    app.add_exception_handler(Exception, unhandled_error)
    app.add_middleware(SecurityMiddleware)
