import os
from pathlib import Path
from uuid import uuid4
import asyncpg
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import postgres
from app.core.security import create_token


@pytest_asyncio.fixture
async def api():
    dsn = os.environ.get(
        "TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:55435/videoteca_test"
    )
    if not dsn.rsplit("/", 1)[-1].endswith("_test"):
        raise RuntimeError("Use um banco exclusivo com nome terminado em _test")
    admin = await asyncpg.connect(dsn)
    schema = "test_" + uuid4().hex
    await admin.execute(
        'CREATE EXTENSION IF NOT EXISTS btree_gist; CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'
    )
    await admin.execute(f"CREATE SCHEMA {schema}")
    pool = await asyncpg.create_pool(
        dsn, min_size=1, max_size=8, server_settings={"search_path": schema + ",public"}
    )
    previous = postgres._pool
    postgres._pool = pool
    try:
        async with pool.acquire() as conn:
            for migration in sorted(
                (Path(__file__).parents[1] / "db/migrations").glob("*.sql")
            ):
                await conn.execute(migration.read_text(encoding="utf-8"))
            ids = {}
            for role in ["professor", "funcionario", "aluno"]:
                uid = uuid4()
                ids[role] = uid
                await conn.execute(
                    "INSERT INTO users(id,email,password_hash) VALUES($1,$2,$3)",
                    uid,
                    role + "@test.invalid",
                    "unused",
                )
                await conn.execute(
                    "INSERT INTO profiles(id,role,full_name,registration,course) VALUES($1,$2,$2,$2,$3)",
                    uid,
                    role,
                    "Cinema",
                )
            await conn.execute(
                "INSERT INTO rooms(id,name,capacity,equipment) VALUES(1,'Sala A',30,ARRAY['Projetor']), (2,'Sala B',10,ARRAY['Som'])"
            )
            await conn.execute("INSERT INTO films(id,title) VALUES(1,'Filme teste')")
            await conn.execute("SELECT setval(pg_get_serial_sequence('films','id'),1)")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:

            def headers(role="professor"):
                return {"Authorization": "Bearer " + create_token(str(ids[role]), role)}

            yield client, pool, headers
    finally:
        postgres._pool = previous
        await pool.close()
        await admin.execute(f"DROP SCHEMA {schema} CASCADE")
        await admin.close()
