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

_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()] or ["http://localhost:3000"]

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


app.include_router(auth.router,      prefix="/api/v1/auth",      tags=["auth"])
app.include_router(catalog.router,   prefix="/api/v1/catalog",   tags=["catalog"])
app.include_router(events.router,    prefix="/api/v1/events",    tags=["events"])
app.include_router(student.router,   prefix="/api/v1/student",   tags=["student"])
app.include_router(professor.router, prefix="/api/v1/professor", tags=["professor"])
app.include_router(staff.router,     prefix="/api/v1/staff",     tags=["staff"])
