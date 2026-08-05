"""Create support for explicit patient visits without converting encounters."""

import asyncio

from sqlalchemy import text

from app.db.database import engine


async def main() -> None:
    async with engine.begin() as connection:
        await connection.execute(text("""
            CREATE TABLE IF NOT EXISTS patient_visits (
                id UUID PRIMARY KEY,
                hospital_id UUID NOT NULL REFERENCES hospitals(id),
                patient_id UUID NOT NULL REFERENCES patients(id),
                created_by UUID NOT NULL REFERENCES users(id),
                visit_number INTEGER NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'open',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                closed_at TIMESTAMPTZ NULL,
                CONSTRAINT uq_patient_visit_number UNIQUE (patient_id, visit_number)
            )
        """))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_patient_visits_hospital_patient ON patient_visits (hospital_id, patient_id)"))
        await connection.execute(text("ALTER TABLE encounters ADD COLUMN IF NOT EXISTS visit_id UUID REFERENCES patient_visits(id)"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_encounters_visit_id ON encounters (visit_id)"))
        await connection.execute(text("ALTER TABLE encounters ALTER COLUMN visit_id DROP NOT NULL"))
        await connection.execute(text("ALTER TABLE voice_intake_jobs ADD COLUMN IF NOT EXISTS visit_id UUID REFERENCES patient_visits(id)"))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
