"""Seed department names and optionally distribute existing users for a test hospital.

python -m scripts.seed_departments --hospital-id UUID
python -m scripts.seed_departments --hospital-id UUID --assign-unassigned
Assignments never overwrite an existing membership. Every role is eligible.
"""
import argparse
import asyncio
import uuid
import random
from sqlalchemy import select, func
from app.db.database import AsyncSessionLocal, engine
from app.db.models import Hospital, User
from app.db.clinical_documents import Department, UserDepartment

NAMES = ("Radiology", "Cardiology", "General Medicine", "Surgery", "Anaesthesiology", "Obstetrics and Gynaecology", "Paediatrics", "Emergency", "Nursing", "Administration")


async def seed(hospital_id, assign):
    async with AsyncSessionLocal() as db:
        if await db.get(Hospital, hospital_id) is None:
            raise SystemExit("Hospital not found")
        await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"departments:{hospital_id}"))))
        existing = (await db.scalars(select(Department).where(Department.hospital_id == hospital_id))).all()
        by_name = {row.name.lower(): row for row in existing}
        for name in NAMES:
            if name.lower() not in by_name:
                row = Department(hospital_id=hospital_id, name=name)
                db.add(row)
                by_name[name.lower()] = row
        await db.flush()
        count = 0
        if assign:
            users = (await db.scalars(select(User).where(User.hospital_id == hospital_id).order_by(User.id))).all()
            departments = list(by_name.values())
            for user in users:
                if await db.get(UserDepartment, user.id) is None:
                    assigned = random.choice(departments)
                    db.add(UserDepartment(user_id=user.id, hospital_id=hospital_id,
                        department_id=assigned.id, assigned_by=user.id))
                    from app.db.models import AuditLog
                    db.add(AuditLog(hospital_id=hospital_id, user_id=user.id,
                        action="department.test_assignment", resource_type="user", resource_id=user.id,
                        source="migration", event_metadata={"department_id": str(assigned.id), "random_test_assignment": True}))
                    count += 1
        await db.commit()
        print(f"Departments ready; {count} previously unassigned users assigned for testing.")
async def main(hospital_id, all_hospitals, assign):
    if all_hospitals:
        async with AsyncSessionLocal() as db:
            hospital_ids = (await db.scalars(select(Hospital.id))).all()
    else:
        hospital_ids = [hospital_id]
    for item in hospital_ids:
        await seed(item, assign)
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--hospital-id", type=uuid.UUID)
    scope.add_argument("--all-hospitals", action="store_true")
    parser.add_argument("--assign-unassigned", action="store_true", help="Distribute unassigned users in this test hospital")
    args = parser.parse_args()
    asyncio.run(main(args.hospital_id, args.all_hospitals, args.assign_unassigned))
