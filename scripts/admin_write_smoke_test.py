"""Rollback-isolated smoke test for company signup and administration flows."""

import asyncio
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.db.database import engine, get_db
from app.db.models import City, District, State, User
from app.main import app


def assert_status(response: httpx.Response, expected: int):
    assert response.status_code == expected, f"{response.request.url}: {response.text}"
    return response.json() if response.content else None


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
            state = await session.scalar(select(State).order_by(State.name))
            district = await session.scalar(
                select(District).where(District.state_id == state.id).order_by(District.name)
            )
            city = await session.scalar(
                select(City).where(City.district_id == district.id).order_by(City.name)
            )
            clinical_user = await session.scalar(
                select(User).where(
                    User.hospital_id.is_not(None),
                    User.admin_organization_id.is_(None),
                    User.is_active.is_(True),
                )
            )
            if not all((state, district, city, clinical_user)):
                raise RuntimeError("Location data and one clinical user must exist first")

            suffix = uuid.uuid4().hex[:8]
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                signup = assert_status(
                    await client.post(
                        "/api/v1/auth/signup",
                        json={
                            "organization_name": f"Smoke Administration {suffix}",
                            "full_name": "Smoke Owner",
                            "email": f"owner-{suffix}@example.com",
                            "password": "SmokePass@123",
                        },
                    ),
                    201,
                )
                headers = {"Authorization": f"Bearer {signup['access_token']}"}
                profile = assert_status(
                    await client.get("/api/v1/users/me", headers=headers), 200
                )
                assert profile["role"] == "company_owner"
                assert profile["organization_name"].startswith("Smoke Administration")
                assert "hospital_id" not in profile

                clinical_token = create_access_token(
                    user_id=str(clinical_user.id),
                    hospital_id=str(clinical_user.hospital_id),
                    role=clinical_user.role.name,
                    permissions=["clients:read", "clients:manage", "users:manage"],
                    user_type="clinical",
                )
                assert_status(
                    await client.get(
                        "/api/v1/clients",
                        headers={"Authorization": f"Bearer {clinical_token}"},
                    ),
                    403,
                )

                roles = assert_status(
                    await client.get("/api/v1/users/roles", headers=headers), 200
                )
                member_role = next(role for role in roles if role["name"] == "company_member")

                generated_code = assert_status(
                    await client.get(
                        "/api/v1/services/code/generate", headers=headers
                    ),
                    200,
                )["code"]
                assert generated_code.startswith("TA")
                assert len(generated_code) == 6
                service = assert_status(
                    await client.post(
                        "/api/v1/services",
                        headers=headers,
                        json={
                            "name": f"Smoke Service {suffix}",
                            "code": generated_code,
                            "about": "Rollback-isolated administration service",
                            "features": ["Feature one", "Feature two"],
                            "service_charge": 1000,
                            "gst_included": True,
                            "gst_percentage": 18,
                            "is_active": True,
                        },
                    ),
                    201,
                )
                assert service["gst_amount"] == "180.00"
                assert service["total_charge"] == "1180.00"

                api_key = assert_status(
                    await client.post(
                        "/api/v1/api-keys",
                        headers=headers,
                        json={
                            "service_id": service["id"],
                            "label": "Smoke production key",
                        },
                    ),
                    201,
                )
                assert api_key["api_key"].startswith("ta_live_")
                key_list = assert_status(
                    await client.get("/api/v1/api-keys", headers=headers), 200
                )
                assert key_list[0]["service_id"] == service["id"]
                assert "api_key" not in key_list[0]

                created = assert_status(
                    await client.post(
                        "/api/v1/clients",
                        headers=headers,
                        json={
                            "client_name": f"Smoke Client {suffix}",
                            "email": f"client-{suffix}@example.com",
                            "hq_location": city.name,
                            "service_start_date": "2026-08-01",
                            "requested_services": None,
                            "service_ids": [service["id"]],
                            "api_key": None,
                            "pan_number": "ABCDE1234F",
                            "gst_number": "29ABCDE1234F1Z5",
                            "complete_address": "1 Smoke Test Road",
                            "state_id": str(state.id),
                            "district_id": str(district.id),
                            "city_id": str(city.id),
                            "pincode": "560001",
                            "contact_name": "Test Contact",
                            "contact_email": f"contact-{suffix}@example.com",
                            "contact_mobile": "+919876543210",
                            "emergency_contact_name": "Emergency Contact",
                            "emergency_contact_mobile": "+919876543211",
                        },
                    ),
                    201,
                )
                assert created["requested_services"] == [service["name"]]
                listing = assert_status(
                    await client.get(
                        f"/api/v1/clients?search={suffix}&page=1&page_size=10",
                        headers=headers,
                    ),
                    200,
                )
                assert listing["total"] == 1

                invitation = assert_status(
                    await client.post(
                        "/api/v1/users/invitations",
                        headers=headers,
                        json={
                            "full_name": "Invited Smoke User",
                            "email": f"invite-{suffix}@example.com",
                            "role_id": member_role["id"],
                            "client_access": [
                                {"client_id": created["id"], "can_manage": False}
                            ],
                        },
                    ),
                    201,
                )
                raw_token = parse_qs(urlparse(invitation["magic_link"]).query)["token"][0]
                accepted = assert_status(
                    await client.post(
                        "/api/v1/auth/invitations/accept",
                        json={"token": raw_token, "password": "SmokePass@123"},
                    ),
                    200,
                )
                assert accepted["email"].startswith("invite-")
                assert accepted["organization_slug"] == profile["organization_slug"]

                invited_login = assert_status(
                    await client.post(
                        "/api/v1/auth/login",
                        json={
                            "email": accepted["email"],
                            "password": "SmokePass@123",
                        },
                    ),
                    200,
                )
                assert invited_login["access_token"]
        finally:
            app.dependency_overrides.clear()
            await session.close()
            if outer_transaction.is_active:
                await outer_transaction.rollback()
    await engine.dispose()
    print(
        "Rollback-isolated administration smoke passed: owner signup, service/GST "
        "creation, scoped API key, client service selection, clinical-user rejection, "
        "team invitation, acceptance, and internal login"
    )


if __name__ == "__main__":
    asyncio.run(main())
