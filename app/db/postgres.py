import asyncpg
from app.core.config import settings

_pool: asyncpg.Pool | None = None


async def init_pool():
    global _pool
    dsn = settings.database_url
    is_supabase = "supabase.co" in dsn or "supabase.com" in dsn
    # PgBouncer (Supabase pooler) does not support asyncpg prepared statements.
    # Client and server timeouts stay below the 60 s requirement (T22.3); the ALB idle timeout is 60 s.
    kwargs: dict = {"min_size": 2, "max_size": 10, "command_timeout": 55}
    if is_supabase:
        kwargs["ssl"] = "require"
        kwargs["statement_cache_size"] = 0
        # Supavisor/PgBouncer may reject startup parameters; keep only the client-side timeout there.
    else:
        kwargs["server_settings"] = {
            "statement_timeout": "55000",
            "application_name": "videoteca-api",
        }
    _pool = await asyncpg.create_pool(dsn, **kwargs)


async def close_pool():
    if _pool:
        await _pool.close()


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    return _pool
