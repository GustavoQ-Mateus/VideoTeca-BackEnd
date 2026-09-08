import asyncpg
from app.core.config import settings

_pool: asyncpg.Pool | None = None


async def init_pool():
    global _pool
    dsn = settings.database_url
    is_supabase = "supabase.co" in dsn or "supabase.com" in dsn
    # PgBouncer (Supabase pooler) does not support asyncpg prepared statements.
    kwargs: dict = {"min_size": 2, "max_size": 10}
    if is_supabase:
        kwargs["ssl"] = "require"
        kwargs["statement_cache_size"] = 0
    _pool = await asyncpg.create_pool(dsn, **kwargs)


async def close_pool():
    if _pool:
        await _pool.close()


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    return _pool
