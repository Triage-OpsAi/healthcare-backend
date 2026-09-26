"""Check database connectivity and optionally create missing application tables."""

import argparse
import asyncio

from sqlalchemy import inspect, text

from app.db import documentation, models, ward_voice_models  # noqa: F401 - register all clinical models
from app.db.database import Base, engine


async def main(create: bool) -> None:
    async with engine.begin() as connection:
        result = await connection.scalar(text("SELECT 1"))
        if create:
            await connection.run_sync(Base.metadata.create_all)
        tables = await connection.run_sync(lambda sync_connection: inspect(sync_connection).get_table_names())

    await engine.dispose()
    action = "created/verified" if create else "found"
    print(f"Database connection OK ({result}); {action} {len(tables)} tables")
    print(", ".join(sorted(tables)) or "No application tables found")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--create", action="store_true", help="Create any missing application tables")
    arguments = parser.parse_args()
    asyncio.run(main(create=arguments.create))
