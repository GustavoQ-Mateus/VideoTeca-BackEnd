"""Aplica as migrations (e, em homologação, o seed) usando a DATABASE_URL da task.

    python -m app.migrate            # db/migrations/*.sql em ordem, cada uma numa transação
    python -m app.migrate --seed     # + db/seed/seed_*.sql em ordem (catálogo TMDB, dev, programação)
Com --seed, SEED_PASSWORD_HASHES (JSON {email: hash_bcrypt}) troca as senhas
conhecidas do seed por senhas geradas fora da AWS: só o hash trafega.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

from app.core.config import settings

ROOT = Path(__file__).resolve().parents[1]


async def main(seed: bool) -> None:
    conn = await asyncpg.connect(settings.database_url)
    try:
        for migration in sorted((ROOT / "db/migrations").glob("*.sql")):
            async with conn.transaction():
                await conn.execute(migration.read_text(encoding="utf-8"))
            print("migration ok:", migration.name, flush=True)
        if not seed:
            return
        async with conn.transaction():
            for seed_file in sorted((ROOT / "db/seed").glob("seed_*.sql")):
                await conn.execute(seed_file.read_text(encoding="utf-8"))
                print("seed ok:", seed_file.name, flush=True)
            hashes = json.loads(os.environ.get("SEED_PASSWORD_HASHES") or "{}")
            for email, password_hash in hashes.items():
                updated = await conn.execute(
                    "UPDATE users SET password_hash=$2 WHERE email=$1", email, password_hash
                )
                print("senha redefinida:", email, updated, flush=True)
        print("seed ok", flush=True)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main("--seed" in sys.argv))
