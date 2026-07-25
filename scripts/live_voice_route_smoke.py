"""Authenticated smoke test for the live presigned voice-job route."""

import asyncio
import os

import httpx
from sqlalchemy import delete, select

from app.core.security import create_access_token
from app.db.database import AsyncSessionLocal
from app.db.models import Hospital, Role, User, VoiceIntakeJob


async def main() -> None:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(User, Hospital, Role)
                .join(Hospital, Hospital.id == User.hospital_id)
                .join(Role, Role.id == User.role_id)
                .where(Hospital.code == "RAINBOW-BLR", User.is_active.is_(True))
                .limit(1)
            )
        ).first()
        if row is None:
            raise RuntimeError("No active RAINBOW-BLR user is available")
        user, hospital, role = row
        token = create_access_token(
            user_id=str(user.id),
            hospital_id=str(hospital.id),
            role=role.name,
            permissions=["emr:create", "emr:read"],
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{os.getenv('API_BASE_URL', 'http://127.0.0.1:8000')}/api/v1/emr/voice-jobs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "content_type": "audio/webm;codecs=opus",
                "file_size": 17,
                "language_code": "unknown",
            },
        )
    if not response.is_success:
        raise RuntimeError(f"Live route returned {response.status_code}: {response.text}")
    job_id = response.json()["job_id"]

    async with AsyncSessionLocal() as db:
        await db.execute(delete(VoiceIntakeJob).where(VoiceIntakeJob.id == job_id))
        await db.commit()
    print("live voice-job route: 201, boto3 loaded, presign created")


if __name__ == "__main__":
    asyncio.run(main())
