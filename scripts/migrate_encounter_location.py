import asyncio

from sqlalchemy import text

from app.db.database import engine


async def main() -> None:
    async with engine.begin() as connection:
        await connection.execute(text("ALTER TABLE encounters ADD COLUMN IF NOT EXISTS encounter_number VARCHAR(50)"))
        await connection.execute(text("ALTER TABLE encounters ADD COLUMN IF NOT EXISTS ward_number VARCHAR(50)"))
        await connection.execute(text("ALTER TABLE encounters ADD COLUMN IF NOT EXISTS bed_number VARCHAR(50)"))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
