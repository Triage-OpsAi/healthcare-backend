"""Create or update a hospital-scoped user from safe command-line inputs."""

import argparse
import asyncio
import os

from sqlalchemy import select

from app.core.security import hash_password
from app.db.database import AsyncSessionLocal, engine
from app.db.models import Hospital, Role, User


async def create_or_update_user(
    *, email: str, hospital_code: str, full_name: str, role_name: str, password: str
) -> None:
    async with AsyncSessionLocal() as session:
        hospital = await session.scalar(
            select(Hospital).where(Hospital.code == hospital_code, Hospital.is_active.is_(True))
        )
        if hospital is None:
            raise RuntimeError(f"Active hospital not found: {hospital_code}")

        role = await session.scalar(
            select(Role).where(Role.hospital_id == hospital.id, Role.name == role_name)
        )
        if role is None:
            raise RuntimeError(f"Role '{role_name}' not found for hospital {hospital_code}")

        user = await session.scalar(
            select(User).where(User.hospital_id == hospital.id, User.email == email)
        )
        action = "updated"
        if user is None:
            action = "created"
            user = User(
                hospital_id=hospital.id,
                email=email,
                full_name=full_name,
                role_id=role.id,
                hashed_password=hash_password(password),
                is_active=True,
            )
            session.add(user)
        else:
            user.full_name = full_name
            user.role_id = role.id
            user.hashed_password = hash_password(password)
            user.is_active = True

        await session.commit()
        await session.refresh(user)

    await engine.dispose()
    print(f"User {action}: {email} ({role_name}) in {hospital_code}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--hospital-code", required=True)
    parser.add_argument("--full-name", required=True)
    parser.add_argument("--role", default="doctor")
    arguments = parser.parse_args()

    user_password = os.environ.get("CREATE_USER_PASSWORD")
    if not user_password:
        raise RuntimeError("Set CREATE_USER_PASSWORD for this one command")

    asyncio.run(
        create_or_update_user(
            email=arguments.email,
            hospital_code=arguments.hospital_code,
            full_name=arguments.full_name,
            role_name=arguments.role,
            password=user_password,
        )
    )
