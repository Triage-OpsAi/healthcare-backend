import asyncio

from app.db.database import engine
from app.db.models import HandoverJob
from sqlalchemy import text


async def main() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: HandoverJob.__table__.create(
                sync_connection, checkfirst=True
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE handover_jobs "
                "ALTER COLUMN handed_over_to DROP NOT NULL"
            )
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
