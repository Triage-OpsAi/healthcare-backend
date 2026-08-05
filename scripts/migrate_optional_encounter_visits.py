"""Allow encounters to exist independently of explicitly created visits."""

import asyncio

from sqlalchemy import text

from app.db.database import engine


async def main() -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("ALTER TABLE encounters ALTER COLUMN visit_id DROP NOT NULL")
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
