import time

from app.db import postgres


async def test_catalog_pagination_search_and_latency(api):
    client, pool, headers = api
    await pool.execute(
        """INSERT INTO films(title,director,genre,status)
        SELECT 'Filme ' || lpad(g::text,5,'0'), 'Diretor ' || (g % 50), 'Drama',
               CASE WHEN g % 10 = 0 THEN 'inactive' ELSE 'available' END
        FROM generate_series(2,20001) g"""
    )
    await pool.execute("INSERT INTO films(title) VALUES('100% Cinema'),('Cinema_Novo')")
    await pool.execute("ANALYZE films")
    url = "/api/v1/catalog/films"
    started = time.perf_counter()
    first = await client.get(url)
    assert time.perf_counter() - started < 5  # requirement is < 60 s; this guards regressions
    assert first.status_code == 200 and len(first.json()) == 50
    titles = [f["title"] for f in first.json()]
    assert titles == sorted(titles)
    page = (await client.get(url, params={"limit": 100, "offset": 50})).json()
    assert len(page) == 100 and page[0]["title"] > titles[-1]
    assert all(f["status"] != "inactive" for f in page)
    assert "rights_notes" not in page[0] and "rights_holder" not in page[0]
    found = (await client.get(url, params={"q": "diretor 7"})).json()
    assert found and all(f["director"].startswith("Diretor 7") for f in found)
    # LIKE wildcards in user input are literal.
    assert [f["title"] for f in (await client.get(url, params={"q": "100%"})).json()] == ["100% Cinema"]
    assert [f["title"] for f in (await client.get(url, params={"q": "a_n"})).json()] == ["Cinema_Novo"]
    assert (await client.get(url, params={"q": "' OR 1=1 --"})).json() == []
    for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "x" * 101}):
        assert (await client.get(url, params=bad)).status_code == 422
    plan = "\n".join(
        r[0]
        for r in await pool.fetch(
            "EXPLAIN SELECT * FROM films WHERE status<>'inactive' ORDER BY title,id LIMIT 50"
        )
    )
    assert "idx_films_title_active" in plan


async def test_hot_query_indexes_exist(api):
    client, pool, headers = api
    names = {
        r["indexname"]
        for r in await pool.fetch("SELECT indexname FROM pg_indexes WHERE schemaname=current_schema()")
    }
    assert {
        "idx_films_title_active",
        "idx_events_status_starts",
        "idx_loans_user_status",
        "idx_reservations_user_status",
        "idx_room_bookings_room_period",
        "idx_equipment_loans_user_open",
    } <= names


async def test_pool_timeouts_stay_below_60_seconds(monkeypatch):
    calls = []

    async def fake_pool(dsn, **kwargs):
        calls.append((dsn, kwargs))
        return object()

    previous = postgres._pool
    monkeypatch.setattr(postgres.asyncpg, "create_pool", fake_pool)
    try:
        monkeypatch.setattr(postgres.settings, "database_url", "postgresql://u@db:5432/videoteca")
        await postgres.init_pool()
        monkeypatch.setattr(postgres.settings, "database_url", "postgresql://u@db.x.supabase.co:6543/postgres")
        await postgres.init_pool()
    finally:
        postgres._pool = previous
    local, supabase = calls[0][1], calls[1][1]
    assert local["command_timeout"] == 55 and supabase["command_timeout"] == 55
    assert local["server_settings"] == {"statement_timeout": "55000", "application_name": "videoteca-api"}
    assert supabase["statement_cache_size"] == 0 and "server_settings" not in supabase
