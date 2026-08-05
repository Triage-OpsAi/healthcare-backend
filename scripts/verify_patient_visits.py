"""Read-only integrity check for optional encounter-to-visit assignments."""

import asyncio

from sqlalchemy import select, text

from app.api.deps import CurrentUser
from app.db.database import AsyncSessionLocal, engine
from app.db.models import User
from app.services import doctor_service, patient_chart_service


async def main() -> None:
    async with engine.connect() as connection:
        result = (
            await connection.execute(text("""
                SELECT
                    (SELECT COUNT(*) FROM patient_visits) AS visits,
                    (SELECT COUNT(*) FROM patient_visits v WHERE EXISTS (
                        SELECT 1 FROM audit_logs a
                        WHERE a.action = 'patient_visit.created'
                          AND a.resource_type = 'patient_visit'
                          AND a.resource_id = v.id
                    )) AS explicit_visits,
                    (SELECT COUNT(*) FROM encounters) AS encounters,
                    (SELECT COUNT(*) FROM encounters WHERE visit_id IS NULL) AS standalone_encounters,
                    (SELECT COALESCE(MAX(encounter_count), 0) FROM (
                        SELECT COUNT(*) AS encounter_count
                        FROM encounters
                        WHERE visit_id IS NOT NULL
                        GROUP BY visit_id
                    ) grouped) AS max_encounters_in_one_visit
            """))
        ).one()
    print(
        f"visits={result.visits} encounters={result.encounters} "
        f"explicit_visits={result.explicit_visits} "
        f"standalone_encounters={result.standalone_encounters} "
        f"max_encounters_in_one_visit={result.max_encounters_in_one_visit}"
    )
    async with AsyncSessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.hospital_id.is_not(None)).limit(1)
        )
        if user is not None:
            current_user = CurrentUser(
                user_id=str(user.id),
                hospital_id=str(user.hospital_id),
                admin_organization_id=None,
                user_type="clinical",
                role="verification",
                permissions={"emr:read"},
            )
            patients = await doctor_service.list_patients(session, current_user)
            if patients and patients[0].visits:
                visit = patients[0].visits[0]
                chart = await patient_chart_service.get_chart(
                    session,
                    patient_id=patients[0].id,
                    visit_id=visit.id,
                    current_user=current_user,
                )
                if chart.selected_visit is None or chart.selected_visit.id != visit.id:
                    raise RuntimeError("Visit-scoped chart did not select the requested visit")
                print(
                    f"api_check=ok patient={patients[0].id} "
                    f"visit={visit.visit_number} encounters={visit.encounter_count}"
                )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
