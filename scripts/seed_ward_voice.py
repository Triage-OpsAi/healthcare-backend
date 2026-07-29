"""Idempotently load the screenshot Ward 3B scenario into the database for local QA."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.database import AsyncSessionLocal, engine
from app.db.models import Encounter, Hospital, Patient, User
from app.db.ward_voice_models import Bed, FluidEntry, IVInfusion, Ward, WardTask

HOSPITAL_CODE = "RAINBOW-BLR"
PATIENTS = [
    ("11", "Ishaan M", 2, "gastro · I/O 2-hourly", "Intake / output log", -18, "completed"),
    ("12", "Diya R", 7, "dengue · I/O hourly", "Fluid chart entry (hourly) + vitals", 28, "due"),
    ("14", "Aarav K", 4, "febrile · vitals 4-hourly", "Vitals (4-hourly) + paracetamol 250 mg", 42, "due"),
    ("15", "Vihaan S", 5, "post-op · drain + dressing", "Dressing check + drainage volume", -32, "due"),
    ("16", "Anaya T", 6, "asthma · neb 6-hourly", "Nebulisation given + notes", -47, "completed"),
    ("18", "Zara K", 9, "bronchiolitis · SpO₂ 2-hourly", "SpO₂ and respiratory check", 55, "due"),
]


async def seed() -> None:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        hospital = await db.scalar(select(Hospital).where(Hospital.code == HOSPITAL_CODE))
        if not hospital:
            raise RuntimeError(f"Hospital {HOSPITAL_CODE} does not exist; run seed_demo first")
        user = await db.scalar(select(User).where(User.hospital_id == hospital.id, User.is_active.is_(True)).order_by(User.created_at))
        if not user:
            raise RuntimeError("The hospital has no active clinical user")
        ward = await db.scalar(select(Ward).where(Ward.hospital_id == hospital.id, Ward.code == "3B"))
        if not ward:
            ward = Ward(hospital_id=hospital.id, name="Ward 3B — Paediatrics", code="3B")
            db.add(ward); await db.flush()
        created_beds: dict[str, Bed] = {}
        for bed_number, name, age, protocol, title, due_minutes, task_status in PATIENTS:
            patient = await db.scalar(select(Patient).where(Patient.hospital_id == hospital.id, Patient.full_name == name))
            if not patient:
                patient = Patient(
                    hospital_id=hospital.id, full_name=name, gender="unknown",
                    date_of_birth=datetime(now.year - age, 1, 1, tzinfo=timezone.utc),
                )
                db.add(patient); await db.flush()
            encounter = await db.scalar(select(Encounter).where(
                Encounter.hospital_id == hospital.id, Encounter.patient_id == patient.id, Encounter.status == "open"
            ))
            if not encounter:
                encounter = Encounter(
                    hospital_id=hospital.id, patient_id=patient.id, doctor_id=user.id,
                    ward_number="3B", bed_number=bed_number, department="Paediatrics", status="open",
                )
                db.add(encounter); await db.flush()
            bed = await db.scalar(select(Bed).where(Bed.ward_id == ward.id, Bed.bed_number == bed_number))
            if not bed:
                bed = Bed(
                    hospital_id=hospital.id, ward_id=ward.id, bed_number=bed_number,
                    patient_id=patient.id, encounter_id=encounter.id, assigned_nurse_id=user.id, protocol=protocol,
                )
                db.add(bed); await db.flush()
            else:
                bed.patient_id, bed.encounter_id, bed.assigned_nurse_id, bed.protocol = patient.id, encounter.id, user.id, protocol
            created_beds[bed_number] = bed
            task = await db.scalar(select(WardTask).where(WardTask.bed_id == bed.id, WardTask.title == title))
            if not task:
                task = WardTask(
                    hospital_id=hospital.id, ward_id=ward.id, bed_id=bed.id, patient_id=patient.id,
                    assigned_to=user.id, title=title, task_type="observation", status=task_status,
                    due_at=now + timedelta(minutes=due_minutes),
                    completed_at=now + timedelta(minutes=due_minutes - 4) if task_status == "completed" else None,
                    completed_by=user.id if task_status == "completed" else None,
                )
                db.add(task)
        await db.flush()
        diya_bed = created_beds["12"]
        existing_entry = await db.scalar(select(FluidEntry).where(FluidEntry.bed_id == diya_bed.id))
        if not existing_entry:
            for hours_ago, direction, category, amount, note in [
                (6, "intake", "oral", 60, "Water"), (5, "output", "urine", 70, None),
                (4, "intake", "oral", 50, "ORS"), (3, "output", "stool", 20, "Loose stool"),
                (2, "intake", "oral", 100, "Food — mashed banana"), (0, "output", "urine", 80, None),
            ]:
                db.add(FluidEntry(
                    hospital_id=hospital.id, ward_id=ward.id, bed_id=diya_bed.id,
                    patient_id=diya_bed.patient_id, direction=direction, category=category,
                    amount_ml=amount, source="manual", notes=note, occurred_at=now - timedelta(hours=hours_ago),
                    recorded_by=user.id,
                ))
            db.add(IVInfusion(
                hospital_id=hospital.id, bed_id=diya_bed.id, patient_id=diya_bed.patient_id,
                fluid_name="IV DNS", rate_ml_per_hour=25, started_at=now - timedelta(hours=5),
                recorded_by=user.id,
            ))
        await db.commit()
    await engine.dispose()
    print("Ward 3B operational records are ready.")


if __name__ == "__main__":
    asyncio.run(seed())
