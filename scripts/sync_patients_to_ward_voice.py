"""Idempotently expose every existing hospital patient on its Ward Voice board."""
import argparse
import asyncio

from sqlalchemy import func, select

from app.db.database import AsyncSessionLocal, engine
from app.db.models import Encounter, Hospital, Patient, User
from app.db.ward_voice_models import Bed, Ward


async def sync(hospital_name: str, ward_code: str, ward_name: str) -> None:
    async with AsyncSessionLocal() as db:
        hospitals = (
            await db.scalars(
                select(Hospital).where(
                    func.lower(Hospital.name).contains(hospital_name.lower()),
                    Hospital.is_active.is_(True),
                )
            )
        ).all()
        if not hospitals:
            raise RuntimeError(f"No active hospital matched {hospital_name!r}")
        if len(hospitals) > 1:
            matches = ", ".join(f"{item.name} ({item.code})" for item in hospitals)
            raise RuntimeError(f"Hospital name is ambiguous: {matches}")
        hospital = hospitals[0]
        ward = await db.scalar(
            select(Ward).where(
                Ward.hospital_id == hospital.id,
                Ward.code == ward_code,
            )
        )
        if ward is None:
            ward = Ward(
                hospital_id=hospital.id,
                name=ward_name,
                code=ward_code,
                is_active=True,
            )
            db.add(ward)
            await db.flush()

        nurse = await db.scalar(
            select(User)
            .where(
                User.hospital_id == hospital.id,
                User.is_active.is_(True),
            )
            .order_by(User.created_at)
        )
        patients = (
            await db.scalars(
                select(Patient)
                .where(Patient.hospital_id == hospital.id)
                .order_by(Patient.created_at, Patient.id)
            )
        ).all()
        created = 0
        updated = 0
        for position, patient in enumerate(patients, start=1):
            existing = await db.scalar(
                select(Bed).where(
                    Bed.hospital_id == hospital.id,
                    Bed.patient_id == patient.id,
                    Bed.is_active.is_(True),
                )
            )
            encounter = await db.scalar(
                select(Encounter)
                .where(
                    Encounter.hospital_id == hospital.id,
                    Encounter.patient_id == patient.id,
                )
                .order_by(
                    (Encounter.status == "open").desc(),
                    Encounter.created_at.desc(),
                )
            )
            if existing:
                existing.ward_id = ward.id
                existing.encounter_id = encounter.id if encounter else existing.encounter_id
                existing.assigned_nurse_id = existing.assigned_nurse_id or (nurse.id if nurse else None)
                updated += 1
                continue
            preferred = encounter.bed_number.strip() if encounter and encounter.bed_number else ""
            bed_number = preferred or f"T-{position:03d}"
            collision = await db.scalar(
                select(Bed).where(Bed.ward_id == ward.id, Bed.bed_number == bed_number)
            )
            if collision:
                bed_number = f"T-{position:03d}"
            db.add(
                Bed(
                    hospital_id=hospital.id,
                    ward_id=ward.id,
                    bed_number=bed_number,
                    patient_id=patient.id,
                    encounter_id=encounter.id if encounter else None,
                    assigned_nurse_id=nurse.id if nurse else None,
                    protocol=encounter.department if encounter else "Triage observation",
                    is_active=True,
                )
            )
            created += 1
        await db.commit()
        print(
            f"Hospital: {hospital.name} ({hospital.code})\n"
            f"Ward: {ward.name} ({ward.code})\n"
            f"Patients found: {len(patients)}\n"
            f"Beds created: {created}\n"
            f"Beds updated: {updated}"
        )
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("hospital_name")
    parser.add_argument("--ward-code", default="TRIAGE")
    parser.add_argument("--ward-name", default="Triage Ward")
    args = parser.parse_args()
    asyncio.run(sync(args.hospital_name, args.ward_code, args.ward_name))


if __name__ == "__main__":
    main()
