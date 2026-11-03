"""Videoteca Unifor — API FastAPI + PostgreSQL."""

from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core import hardening, observability, openapi
from app.core.config import settings
from app.db import postgres
from app.routers import (
    auth,
    availability,
    bookings,
    catalog,
    equipment,
    equipment_incidents,
    equipment_returns,
    events,
    film_management,
    film_rights,
    privacy,
    professor,
    publication,
    sessions,
    staff,
    student,
)
from app.session_errors import register_session_errors


@asynccontextmanager
async def lifespan(app: FastAPI):
    await postgres.init_pool()
    yield
    await postgres.close_pool()


_docs = settings.docs_enabled
app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)
openapi.configure(app)

_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()] or [
    "http://localhost:3000"
]

# Last added = outermost: observability wraps everything (including rejections and CORS).
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID", "Content-Disposition", "Retry-After"],
)
hardening.install(app)
observability.install(app)


@app.get("/health", tags=["health"])
async def health():
    try:
        await postgres.get_pool().fetchval("SELECT 1")
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok"}


app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(catalog.router, prefix="/api/v1/catalog", tags=["catalog"])
app.include_router(events.router, prefix="/api/v1/events", tags=["events"])
app.include_router(student.router, prefix="/api/v1/student", tags=["student"])
app.include_router(professor.router, prefix="/api/v1/professor", tags=["professor"])
app.include_router(staff.router, prefix="/api/v1/staff", tags=["staff"])

for module, tag in (
    (availability, "rooms"),
    (bookings, "rooms"),
    (equipment, "equipment"),
    (equipment_returns, "equipment"),
    (equipment_incidents, "equipment"),
    (film_management, "films"),
    (film_rights, "rights"),
    (sessions, "sessions"),
    (publication, "sessions"),
    (privacy, "privacy"),
):
    app.include_router(module.router, prefix="/api/v1", tags=[tag])


@app.exception_handler(asyncpg.ExclusionViolationError)
async def conflict_error(request, exc):
    return JSONResponse(
        status_code=409, content={"detail": "A sala já está ocupada nesse período"}
    )


@app.exception_handler(asyncpg.UniqueViolationError)
async def duplicate_error(request, exc):
    return JSONResponse(status_code=409, content={"detail": "Este registro já existe"})


@app.exception_handler(asyncpg.ForeignKeyViolationError)
async def missing_reference(request, exc):
    return JSONResponse(
        status_code=422, content={"detail": "Um dos registros informados não existe"}
    )


@app.exception_handler(TimeoutError)
@app.exception_handler(asyncpg.QueryCanceledError)
async def query_timeout(request, exc):
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Tempo de processamento excedido; consulte suas reservas antes de tentar novamente"
        },
    )


register_session_errors(app)
