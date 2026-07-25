"""Add doctor-portal tenancy structures to an existing database."""

import asyncio

from sqlalchemy import text

from app.db.database import engine


STATEMENTS = (
    """
    ALTER TABLE hospitals
    ADD COLUMN IF NOT EXISTS parent_hospital_id UUID REFERENCES hospitals(id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_hospitals_parent_hospital_id
    ON hospitals(parent_hospital_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS network_hospital_profiles (
        hospital_id UUID PRIMARY KEY
            REFERENCES hospitals(id) ON DELETE CASCADE,
        parent_hospital_id UUID NOT NULL
            REFERENCES hospitals(id) ON DELETE CASCADE,
        place VARCHAR(255) NOT NULL,
        email VARCHAR(255) NOT NULL,
        contact_name VARCHAR(255) NOT NULL,
        contact_email VARCHAR(255) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_network_hospital_profiles_parent
    ON network_hospital_profiles(parent_hospital_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_network_hospital_profiles_email
    ON network_hospital_profiles(email)
    """,
)


async def main() -> None:
    async with engine.begin() as connection:
        for statement in STATEMENTS:
            await connection.execute(text(statement))
    await engine.dispose()
    print("Doctor portal schema migration complete.")


if __name__ == "__main__":
    asyncio.run(main())
