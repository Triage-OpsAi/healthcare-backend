"""Create or reuse an open encounter for a hospital user."""

import argparse
import asyncio

from sqlalchemy import select

from app.db.database import AsyncSessionLocal, engine
from app.db.models import Encounter, Hospital, Patient, User


async def create_encounter(
    *, hospital_code: str, doctor_email: str, patient_name: str, department: str
) -> None:
    async with AsyncSessionLocal() as session:
        hospital = await session.scalar(
            select(Hospital).where(Hospital.code == hospital_code, Hospital.is_active.is_(True))
        )
        if hospital is None:
            raise RuntimeError(f"Active hospital not found: {hospital_code}")

        doctor = await session.scalar(
            select(User).where(
                User.hospital_id == hospital.id,
                User.email == doctor_email,
                User.is_active.is_(True),
            )
        )
        if doctor is None:
            raise RuntimeError(f"Active doctor not found: {doctor_email}")

        patient = await session.scalar(
            select(Patient).where(
                Patient.hospital_id == hospital.id,
                Patient.full_name == patient_name,
            )
        )
        if patient is None:
            patient = Patient(
                hospital_id=hospital.id,
                full_name=patient_name,
                gender="unknown",
            )
            session.add(patient)
            await session.flush()

        encounter = await session.scalar(
            select(Encounter).where(
                Encounter.hospital_id == hospital.id,
                Encounter.patient_id == patient.id,
                Encounter.doctor_id == doctor.id,
                Encounter.status == "open",
            )
        )
        action = "reused"
        if encounter is None:
            action = "created"
            encounter = Encounter(
                hospital_id=hospital.id,
                patient_id=patient.id,
                doctor_id=doctor.id,
                department=department,
                status="open",
            )
            session.add(encounter)

        await session.commit()
        await session.refresh(encounter)

    await engine.dispose()
    print(f"Encounter {action}: {encounter.id}")
    print(f"Doctor: {doctor_email}")
    print(f"Patient: {patient_name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hospital-code", required=True)
    parser.add_argument("--doctor-email", required=True)
    parser.add_argument("--patient-name", required=True)
    parser.add_argument("--department", default="General Medicine")
    arguments = parser.parse_args()
    asyncio.run(
        create_encounter(
            hospital_code=arguments.hospital_code,
            doctor_email=arguments.doctor_email,
            patient_name=arguments.patient_name,
            department=arguments.department,
        )
    )
