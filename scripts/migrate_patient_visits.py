"""Create real patient visits and attach existing encounters without losing history."""

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
        await connection.execute(text("""
            INSERT INTO patient_visits (id, hospital_id, patient_id, created_by, visit_number, status, created_at)
            SELECT e.id, e.hospital_id, e.patient_id, e.doctor_id,
                   COALESCE((SELECT MAX(v.visit_number) FROM patient_visits v WHERE v.patient_id = e.patient_id), 0)
                   + ROW_NUMBER() OVER (PARTITION BY e.patient_id ORDER BY e.created_at, e.id),
                   e.status, e.created_at
            FROM encounters e
            WHERE e.visit_id IS NULL
            ON CONFLICT DO NOTHING
        """))
        await connection.execute(text("UPDATE encounters SET visit_id = id WHERE visit_id IS NULL"))
        await connection.execute(text("ALTER TABLE encounters ALTER COLUMN visit_id SET NOT NULL"))
        await connection.execute(text("ALTER TABLE voice_intake_jobs ADD COLUMN IF NOT EXISTS visit_id UUID REFERENCES patient_visits(id)"))
        await connection.execute(text("""
            UPDATE voice_intake_jobs j
            SET visit_id = e.visit_id
            FROM encounters e
            WHERE j.encounter_id = e.id AND j.visit_id IS NULL
        """))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
