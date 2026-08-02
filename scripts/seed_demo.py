"""Seed an idempotent Swagger/demo tenant in an empty development database."""

import asyncio

from sqlalchemy import select

from app.core.security import hash_password
from app.db.database import AsyncSessionLocal, engine
from app.db.models import Encounter, Hospital, Patient, PatientVisit, Permission, Role, User

HOSPITAL_CODE = "RAINBOW-BLR"
USER_EMAIL = "doctor@example.com"
USER_PASSWORD = "Doctor@123"
PERMISSION_CODES = ("emr:create", "emr:read", "emr:review")


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        hospital = await session.scalar(select(Hospital).where(Hospital.code == HOSPITAL_CODE))
        if hospital is None:
            hospital = Hospital(name="Rainbow Demo Hospital", code=HOSPITAL_CODE, is_active=True)
            session.add(hospital)
            await session.flush()

        permissions = []
        for code in PERMISSION_CODES:
            permission = await session.scalar(select(Permission).where(Permission.code == code))
            if permission is None:
                permission = Permission(code=code, description=f"Allows {code} operations")
                session.add(permission)
                await session.flush()
            permissions.append(permission)

        role = await session.scalar(
            select(Role).where(Role.hospital_id == hospital.id, Role.name == "doctor")
        )
        if role is None:
            role = Role(hospital_id=hospital.id, name="doctor", permissions=permissions)
            session.add(role)
            await session.flush()
        else:
            existing_codes = {permission.code for permission in role.permissions}
            role.permissions.extend(
                permission for permission in permissions if permission.code not in existing_codes
            )

        user = await session.scalar(
            select(User).where(User.hospital_id == hospital.id, User.email == USER_EMAIL)
        )
        if user is None:
            user = User(
                hospital_id=hospital.id,
                email=USER_EMAIL,
                hashed_password=hash_password(USER_PASSWORD),
                full_name="Swagger Demo Doctor",
                role_id=role.id,
                is_active=True,
            )
            session.add(user)
            await session.flush()

        patient = await session.scalar(
            select(Patient).where(
                Patient.hospital_id == hospital.id,
                Patient.full_name == "Swagger Demo Patient",
            )
        )
        if patient is None:
            patient = Patient(
                hospital_id=hospital.id,
                full_name="Swagger Demo Patient",
                gender="unknown",
            )
            session.add(patient)
            await session.flush()

        encounter = await session.scalar(
            select(Encounter).where(
                Encounter.hospital_id == hospital.id,
                Encounter.patient_id == patient.id,
                Encounter.doctor_id == user.id,
                Encounter.status == "open",
            )
        )
        if encounter is None:
            visit = await session.scalar(select(PatientVisit).where(PatientVisit.patient_id == patient.id).order_by(PatientVisit.created_at.desc()).limit(1))
            if visit is None:
                visit = PatientVisit(hospital_id=hospital.id, patient_id=patient.id, created_by=user.id, visit_number=1, status="open")
                session.add(visit)
                await session.flush()
            encounter = Encounter(
                hospital_id=hospital.id,
                patient_id=patient.id,
                visit_id=visit.id,
                doctor_id=user.id,
                department="General Medicine",
                status="open",
            )
            session.add(encounter)

        await session.commit()
        await session.refresh(encounter)

    await engine.dispose()
    print(f"Hospital code: {HOSPITAL_CODE}")
    print(f"Doctor email: {USER_EMAIL}")
    print(f"Doctor password: {USER_PASSWORD}")
    print(f"Encounter ID: {encounter.id}")


if __name__ == "__main__":
    asyncio.run(seed())
