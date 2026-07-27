import asyncio

from sqlalchemy import text

from app.db.database import engine

STATEMENTS = [
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS client_event_id VARCHAR(64)",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS event_category VARCHAR(50) NOT NULL DEFAULT 'clinical'",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS actor_role VARCHAR(50)",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS patient_id UUID REFERENCES patients(id)",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS encounter_id UUID REFERENCES encounters(id)",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS outcome VARCHAR(20) NOT NULL DEFAULT 'success'",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS source VARCHAR(30) NOT NULL DEFAULT 'web'",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_id VARCHAR(100)",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS changes JSON",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS event_metadata JSON",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_logs_client_event_id ON audit_logs(client_event_id) WHERE client_event_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_audit_logs_patient_time ON audit_logs(patient_id, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS ix_audit_logs_action_time ON audit_logs(action, timestamp DESC)",
]


async def main() -> None:
    async with engine.begin() as connection:
        for statement in STATEMENTS:
            await connection.execute(text(statement))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
