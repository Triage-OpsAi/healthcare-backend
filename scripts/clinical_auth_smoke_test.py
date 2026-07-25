"""Rollback-isolated doctor login and EMR authorization smoke test."""

import asyncio

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.database import engine, get_db
from app.main import app


async def main() -> None:
    async with engine.connect() as connection:
        outer_transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        async def override_database():
            yield session

        app.dependency_overrides[get_db] = override_database
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/auth/login",
                    json={
                        "email": "doctor@example.com",
                        "password": "Doctor@123",
                        "hospital_code": "RAINBOW-BLR",
                    },
                )
                assert response.status_code == 200, response.text
                tokens = response.json()
                claims = decode_token(tokens["access_token"])
                assert claims["user_type"] == "clinical"
                assert claims["hospital_id"]
                assert claims.get("admin_organization_id") is None
                assert "emr:read" in claims["permissions"]

                emr_response = await client.get(
                    "/api/v1/emr/encounters",
                    headers={
                        "Authorization": f"Bearer {tokens['access_token']}"
                    },
                )
                assert emr_response.status_code == 200, emr_response.text

                dedicated_response = await client.post(
                    "/api/v1/auth/clinical/login",
                    json={
                        "email": "doctor@example.com",
                        "password": "Doctor@123",
                        "hospital_code": "RAINBOW-BLR",
                    },
                )
                assert dedicated_response.status_code == 200, dedicated_response.text
        finally:
            app.dependency_overrides.clear()
            await session.close()
            if outer_transaction.is_active:
                await outer_transaction.rollback()
    await engine.dispose()
    print(
        "Clinical auth smoke passed: legacy /auth/login contract, dedicated "
        "/auth/clinical/login, hospital-scoped JWT, and authenticated EMR access"
    )


if __name__ == "__main__":
    asyncio.run(main())
