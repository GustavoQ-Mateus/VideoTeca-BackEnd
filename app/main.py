"""Videoteca Unifor — API FastAPI + PostgreSQL."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db import postgres
from app.routers import auth, catalog, events, professor, staff, student


@asynccontextmanager
async def lifespan(app: FastAPI):
    await postgres.init_pool()
    yield
    await postgres.close_pool()


app = FastAPI(title="Videoteca Unifor API", version="0.1.0", lifespan=lifespan)

_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()] or [
    "http://localhost:3000"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    pool = postgres.get_pool()
    await pool.fetchval("SELECT 1")
    return {"status": "ok"}


app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(catalog.router, prefix="/api/v1/catalog", tags=["catalog"])
app.include_router(events.router, prefix="/api/v1/events", tags=["events"])
app.include_router(student.router, prefix="/api/v1/student", tags=["student"])
app.include_router(professor.router, prefix="/api/v1/professor", tags=["professor"])
app.include_router(staff.router, prefix="/api/v1/staff", tags=["staff"])

from app.routers import (
    availability,
    bookings,
    equipment,
    equipment_returns,
    equipment_incidents,
    film_management,
    sessions,
    publication,
)
from fastapi.responses import JSONResponse
import asyncpg


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


@app.exception_handler(asyncpg.QueryCanceledError)
async def query_timeout(request, exc):
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Tempo de processamento excedido; consulte suas reservas antes de tentar novamente"
        },
    )


for module in (
    availability,
    bookings,
    equipment,
    equipment_returns,
    equipment_incidents,
    film_management,
    sessions,
    publication,
):
    app.include_router(module.router, prefix="/api/v1")

from app.session_errors import register_session_errors

register_session_errors(app)
