"""Read-only verification of the administration/clinical identity boundary."""

import asyncio

from sqlalchemy import text

from app.db.database import AsyncSessionLocal, engine
from app.services.auth_service import AuthError, authenticate_admin_user


async def main() -> None:
    async with engine.connect() as connection:
        admin_organizations = await connection.scalar(
            text("SELECT count(*) FROM admin_organizations")
        )
        clinical_users = await connection.scalar(
            text(
                "SELECT count(*) FROM users "
                "WHERE hospital_id IS NOT NULL AND admin_organization_id IS NULL"
            )
        )
        mixed_identity_users = await connection.scalar(
            text(
                "SELECT count(*) FROM users "
                "WHERE hospital_id IS NOT NULL AND admin_organization_id IS NOT NULL"
            )
        )
        organizations = (
            await connection.execute(
                text(
                    "SELECT name, slug FROM admin_organizations "
                    "ORDER BY created_at"
                )
            )
        ).all()
    await engine.dispose()
    print(
        f"admin_organizations={admin_organizations}; "
        f"clinical_users={clinical_users}; "
        f"mixed_identity_users={mixed_identity_users}"
    )
    for name, slug in organizations:
        print(f"admin_organization={name} ({slug})")
    if mixed_identity_users:
        raise RuntimeError("A user cannot be both clinical and an internal administrator")

    async with AsyncSessionLocal() as session:
        try:
            await authenticate_admin_user(
                session,
                email="doctor@example.com",
                password="Doctor@123",
            )
        except AuthError:
            print("doctor_admin_login=rejected")
        else:
            raise RuntimeError("Clinical doctor credentials entered the administration app")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
