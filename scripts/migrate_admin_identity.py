"""Idempotently split internal company identities from clinical tenants."""

import asyncio

from sqlalchemy import text

from app.db import models  # noqa: F401
from app.db.database import Base, engine


ALTERATIONS = (
    "ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS admin_organization_id UUID",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS admin_organization_id UUID",
    "ALTER TABLE user_invitations ADD COLUMN IF NOT EXISTS admin_organization_id UUID",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS admin_organization_id UUID",
    "ALTER TABLE users ALTER COLUMN hospital_id DROP NOT NULL",
    "ALTER TABLE user_invitations ALTER COLUMN hospital_id DROP NOT NULL",
    "ALTER TABLE audit_logs ALTER COLUMN hospital_id DROP NOT NULL",
    """
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_hospitals_admin_organization') THEN
        ALTER TABLE hospitals
          ADD CONSTRAINT fk_hospitals_admin_organization
          FOREIGN KEY (admin_organization_id) REFERENCES admin_organizations(id);
      END IF;
    END $$;
    """,
    """
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_users_admin_organization') THEN
        ALTER TABLE users
          ADD CONSTRAINT fk_users_admin_organization
          FOREIGN KEY (admin_organization_id) REFERENCES admin_organizations(id);
      END IF;
    END $$;
    """,
    """
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_invitations_admin_organization') THEN
        ALTER TABLE user_invitations
          ADD CONSTRAINT fk_invitations_admin_organization
          FOREIGN KEY (admin_organization_id) REFERENCES admin_organizations(id);
      END IF;
    END $$;
    """,
    """
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_audit_logs_admin_organization') THEN
        ALTER TABLE audit_logs
          ADD CONSTRAINT fk_audit_logs_admin_organization
          FOREIGN KEY (admin_organization_id) REFERENCES admin_organizations(id);
      END IF;
    END $$;
    """,
    """
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_admin_user_email_per_organization'
      ) THEN
        ALTER TABLE users
          ADD CONSTRAINT uq_admin_user_email_per_organization
          UNIQUE (admin_organization_id, email);
      END IF;
    END $$;
    """,
    "CREATE INDEX IF NOT EXISTS ix_hospitals_admin_organization_id ON hospitals (admin_organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_users_admin_organization_id ON users (admin_organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_user_invitations_admin_organization_id ON user_invitations (admin_organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_audit_logs_admin_organization_id ON audit_logs (admin_organization_id)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_admin_users_email_global
    ON users (lower(email))
    WHERE admin_organization_id IS NOT NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_global_role_name
    ON roles (name)
    WHERE hospital_id IS NULL
    """,
)


async def main() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        for statement in ALTERATIONS:
            await connection.execute(text(statement))
    await engine.dispose()
    print("Administration identity migration complete")


if __name__ == "__main__":
    asyncio.run(main())
