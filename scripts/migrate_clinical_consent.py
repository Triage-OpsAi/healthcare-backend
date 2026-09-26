"""One-time, idempotent department test assignment requested for existing users.

Run database_setup --create first. No credentials or patient details are logged.
"""
import asyncio
from sqlalchemy import func, select
from app.db.database import AsyncSessionLocal, engine
from app.db.models import Hospital
from app.db.clinical_documents import ClinicalFeatureRollout
from scripts.seed_departments import seed

ROLLOUT = "clinical-consent-departments-v1"


async def migrate(db):
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(ROLLOUT))))
    completed = await db.get(ClinicalFeatureRollout, ROLLOUT)
    if completed:
        print("Clinical department test assignments already applied; preserving all assignments.")
        return
    hospital_ids = (await db.scalars(select(Hospital.id))).all()
    for hospital_id in hospital_ids:
        await seed(hospital_id, True)
    db.add(ClinicalFeatureRollout(key=ROLLOUT))
    await db.commit()
    print(f"Clinical department rollout complete for {len(hospital_ids)} hospitals.")


async def main():
    async with AsyncSessionLocal() as db:
        await migrate(db)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
