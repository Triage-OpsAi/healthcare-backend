"""Create 30 stable Rainbow doctor encounters for voice-upload testing."""

import asyncio

from sqlalchemy import select

from app.db.database import AsyncSessionLocal, engine
from app.db.models import Encounter, Hospital, Patient, User
from scripts.seed_demo import HOSPITAL_CODE, USER_EMAIL

ENCOUNTER_COUNT = 30


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        hospital = await session.scalar(
            select(Hospital).where(
                Hospital.code == HOSPITAL_CODE,
                Hospital.is_active.is_(True),
            )
        )
        if hospital is None:
            raise RuntimeError(
                "Rainbow demo hospital is missing; run scripts.seed_demo first"
            )

        doctor = await session.scalar(
            select(User).where(
                User.hospital_id == hospital.id,
                User.email == USER_EMAIL,
                User.is_active.is_(True),
            )
        )
        if doctor is None:
            raise RuntimeError(
                "Swagger demo doctor is missing; run scripts.seed_demo first"
            )

        encounters: list[Encounter] = []
        for number in range(1, ENCOUNTER_COUNT + 1):
            patient_name = f"Voice Upload Test Patient {number:02d}"
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
                )
            )
            if encounter is None:
                encounter = Encounter(
                    hospital_id=hospital.id,
                    patient_id=patient.id,
                    doctor_id=doctor.id,
                    department="General Medicine",
                    status="open",
                )
                session.add(encounter)
                await session.flush()
            encounters.append(encounter)

        await session.commit()

    await engine.dispose()
    print("Voice upload encounter IDs:")
    for index, encounter in enumerate(encounters, start=1):
        print(f"{index:02d}. `{encounter.id}`")


if __name__ == "__main__":
    asyncio.run(seed())
