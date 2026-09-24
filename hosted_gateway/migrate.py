"""Apply checked-in PostgreSQL migrations before deploying gateway replicas."""

from __future__ import annotations

import asyncio
import os

from .store import PostgresStore


async def _migrate() -> None:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("Set DATABASE_URL to the approved PostgreSQL service.")
    store = await PostgresStore.connect(database_url)
    try:
        await store.migrate()
    finally:
        await store.close()


def main() -> None:
    asyncio.run(_migrate())


if __name__ == "__main__":
    main()
