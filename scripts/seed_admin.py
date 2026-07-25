"""Ensure internal administration roles exist without promoting clinical users."""

import asyncio

from app.db.database import AsyncSessionLocal, engine
from app.services.auth_service import ensure_admin_roles


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        roles = await ensure_admin_roles(session)
        await session.commit()
    await engine.dispose()
    print("Administration roles ready: " + ", ".join(sorted(roles)))
    print("Create the first company owner through POST /api/v1/auth/signup.")


if __name__ == "__main__":
    asyncio.run(seed())
