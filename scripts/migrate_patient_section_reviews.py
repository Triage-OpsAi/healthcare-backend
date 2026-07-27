import asyncio

from sqlalchemy import text

from app.db.database import engine

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS patient_section_reviews (
        id UUID PRIMARY KEY,
        hospital_id UUID NOT NULL REFERENCES hospitals(id),
        patient_id UUID NOT NULL REFERENCES patients(id),
        section_key VARCHAR(40) NOT NULL,
        content_override TEXT,
        is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
        is_approved BOOLEAN NOT NULL DEFAULT FALSE,
        approved_by UUID REFERENCES users(id),
        approved_at TIMESTAMPTZ,
        updated_by UUID NOT NULL REFERENCES users(id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_patient_section_review UNIQUE (patient_id, section_key)
    )
    """,
    "ALTER TABLE patient_section_reviews ADD COLUMN IF NOT EXISTS item_overrides JSON NOT NULL DEFAULT '{}'::json",
    "ALTER TABLE patient_section_reviews ADD COLUMN IF NOT EXISTS deleted_items JSON NOT NULL DEFAULT '[]'::json",
    """
    CREATE INDEX IF NOT EXISTS ix_patient_section_reviews_hospital_patient
    ON patient_section_reviews(hospital_id, patient_id)
    """,
]


async def main() -> None:
    async with engine.begin() as connection:
        for statement in STATEMENTS:
            await connection.execute(text(statement))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
