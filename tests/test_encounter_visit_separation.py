import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.api.deps import CurrentUser
from app.db.models import Encounter
from app.services.patient_chart_service import _encounter_visit


class EncounterVisitSeparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_encounter_without_selected_visit_does_not_create_or_reuse_visit(self):
        db = AsyncMock()
        patient = SimpleNamespace(id=uuid.uuid4(), hospital_id=uuid.uuid4())
        user = CurrentUser(
            user_id=str(uuid.uuid4()),
            hospital_id=str(patient.hospital_id),
            admin_organization_id=None,
            user_type="clinical",
            role="doctor",
            permissions={"emr:create"},
        )

        visit = await _encounter_visit(
            db,
            patient=patient,
            visit_id=None,
            current_user=user,
        )

        self.assertIsNone(visit)
        db.get.assert_not_awaited()
        db.execute.assert_not_awaited()

    def test_encounter_visit_foreign_key_is_optional(self):
        self.assertTrue(Encounter.__table__.c.visit_id.nullable)


if __name__ == "__main__":
    unittest.main()
