import copy
import math
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import CurrentUser, get_current_user
from app.api import routes_clinical_documents as routes
from app.db.database import get_db
from app.db.clinical_documents import UserDepartment
from app.main import app
from app.schemas.clinical_documents import ApprovalRequest, ConsentForm, Signature
from app.schemas.doctor import InviteClinicalUserRequest
from app.schemas.patient_chart import PatientChart, PatientSectionReviewSummary
from app.services import clinical_documents as documents


SIGNATURE = {"strokes": [[[0.1, 0.2], [0.2, 0.4], [0.3, 0.3], [0.4, 0.6], [0.5, 0.2]]]}


def user(permissions=None, role="nurse"):
    return CurrentUser(str(uuid.uuid4()), str(uuid.uuid4()), None, "clinical", role, set(permissions or []))


def chart():
    return PatientChart(records=[], reports=[], medications=[], section_reviews=[PatientSectionReviewSummary(
        section_key="clinical", content_override=None, item_overrides={}, deleted_items=[], is_deleted=False,
        is_approved=False, approved_by=None, approved_at=None, updated_by="Staff", updated_at=datetime.now(timezone.utc))])


def consent_data():
    payload = {key: {"en": "Reviewed clinical information", "hi": "समीक्षित चिकित्सीय जानकारी"}
               for key in ("procedure", "purpose", "benefits", "risks", "alternatives", "refusal")}
    payload.update(department_id=str(uuid.uuid4()), language="hi", signer_name="Test Patient",
                   patient_signature=SIGNATURE, clinician_signature=SIGNATURE,
                   explained_and_questions_answered=True, bilingual_content_reviewed=True)
    return payload


class SignatureSchemaTests(unittest.TestCase):
    def test_accepts_real_strokes(self):
        self.assertEqual(Signature(**SIGNATURE).model_dump()["strokes"][0][0], (0.1, 0.2))

    def test_rejects_blank_dot_and_nonfinite_signatures(self):
        for strokes in ([], [[[0.2, 0.2]] * 5], [[[math.nan, 0.2]] * 5], [[[2, 0.2]] * 5]):
            with self.subTest(strokes=strokes), self.assertRaises(ValidationError):
                Signature(strokes=strokes)

    def test_consent_needs_both_languages_and_confirmation(self):
        payload = consent_data()
        self.assertEqual(ConsentForm(**payload).language, "hi")
        payload["risks"]["hi"] = " "
        with self.assertRaises(ValidationError):
            ConsentForm(**payload)
        payload = consent_data()
        payload["bilingual_content_reviewed"] = False
        with self.assertRaises(ValidationError):
            ConsentForm(**payload)

    def test_representative_requires_reason_and_signed_witness(self):
        payload = consent_data()
        payload.update(signer_type="representative", relationship="Parent", representative_reason="Minor", witness_name="Witness")
        with self.assertRaises(ValidationError):
            ConsentForm(**payload)
        payload["witness_signature"] = SIGNATURE
        self.assertEqual(ConsentForm(**payload).signer_type, "representative")

    def test_new_invitation_requires_department_for_every_role(self):
        payload = dict(full_name="Test User", email="test@example.com", role_id=uuid.uuid4())
        with self.assertRaises(ValidationError):
            InviteClinicalUserRequest(**payload)
        payload["department_id"] = uuid.uuid4()
        self.assertEqual(InviteClinicalUserRequest(**payload).department_id, payload["department_id"])


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_invalidates_signature_without_changing_history(self):
        data = chart()
        patient_id = uuid.uuid4()
        snapshot = documents.section_snapshot(data, "clinical", patient_id)
        signed = SimpleNamespace(section_key="clinical", visit_id=None, revision=documents.revision(snapshot),
                                 signer_name="Doctor", signed_at=datetime.now(timezone.utc), snapshot=copy.deepcopy(snapshot))
        with patch.object(documents, "attestations", AsyncMock(return_value=[signed])):
            await documents.validate_chart_approvals(AsyncMock(), data, patient_id, uuid.uuid4(), None)
            self.assertTrue(data.section_reviews[0].is_approved)
            data.section_reviews[0].item_overrides = {"assessment": "Changed assessment"}
            await documents.validate_chart_approvals(AsyncMock(), data, patient_id, uuid.uuid4(), None)
            self.assertFalse(data.section_reviews[0].is_approved)
            self.assertEqual(signed.snapshot, snapshot)

    async def test_visit_signature_cannot_approve_another_visit(self):
        data = chart()
        signed = SimpleNamespace(section_key="clinical", visit_id=uuid.uuid4(), revision="unused")
        with patch.object(documents, "attestations", AsyncMock(return_value=[signed])):
            await documents.validate_chart_approvals(AsyncMock(), data, uuid.uuid4(), uuid.uuid4(), None)
        self.assertFalse(data.section_reviews[0].is_approved)

    async def test_stale_preview_rejected_before_signing(self):
        actor = user(["emr:review"])
        db = AsyncMock()
        with patch("app.services.patient_chart_service._patient", AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), hospital_id=uuid.UUID(actor.hospital_id)))), patch("app.services.patient_chart_service.get_chart", AsyncMock(return_value=chart())):
            with self.assertRaises(HTTPException) as caught:
                await documents.signed_approve(db, uuid.uuid4(), "clinical", ApprovalRequest(signature=SIGNATURE, revision="0" * 64, confirmed=True), actor)
        self.assertEqual(caught.exception.status_code, 409)
        db.commit.assert_not_awaited()


class TenantTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_hospital_department_rejected(self):
        db = AsyncMock()
        db.get.return_value = SimpleNamespace(hospital_id=uuid.uuid4(), is_active=True)
        with self.assertRaises(HTTPException) as caught:
            await documents.department(db, uuid.uuid4(), uuid.uuid4())
        self.assertEqual(caught.exception.status_code, 404)

    async def test_assign_department_does_not_change_role(self):
        from app.schemas.clinical_documents import DepartmentAssignment
        for role in ("doctor", "nurse", "admin", "technician"):
            actor = user(["users:manage"])
            member = SimpleNamespace(id=uuid.uuid4(), role=role, role_id=uuid.uuid4())
            before = member.role_id
            db = AsyncMock()
            db.add = Mock()
            db.scalar.return_value = member
            db.get.return_value = None
            department_id = uuid.uuid4()
            with patch.object(documents, "department", AsyncMock()):
                await routes.assign_department(member.id, DepartmentAssignment(department_id=department_id), db, actor)
            assignment = next(call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], UserDepartment))
            self.assertEqual(assignment.department_id, department_id)
            self.assertEqual(member.role_id, before)


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.actor = user(["emr:read"])
        app.dependency_overrides[get_current_user] = lambda: self.actor
        async def fake_db():
            yield AsyncMock()
        app.dependency_overrides[get_db] = fake_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_read_only_user_cannot_assign_departments_or_sign(self):
        self.assertEqual(self.client.post("/api/v1/doctor/departments", json={"name": "Cardiology"}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/patients/{uuid.uuid4()}/sections/clinical/approve", json={}).status_code, 403)

    def test_approval_requires_a_signature_body(self):
        self.actor.permissions.add("emr:review")
        self.assertEqual(self.client.post(f"/api/v1/patients/{uuid.uuid4()}/sections/clinical/approve", json={}).status_code, 422)


class RolloutTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_rollout_never_reassigns_users(self):
        from scripts import migrate_clinical_consent as migration
        db = AsyncMock()
        db.get.return_value = SimpleNamespace(key=migration.ROLLOUT)
        with patch.object(migration, "seed", AsyncMock()) as seed:
            await migration.migrate(db)
            seed.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_initial_rollout_seeds_all_hospitals_and_marks_completion(self):
        from scripts import migrate_clinical_consent as migration
        db = AsyncMock()
        db.add = Mock()
        db.get.return_value = None
        hospitals = [uuid.uuid4(), uuid.uuid4()]
        db.scalars.return_value = SimpleNamespace(all=lambda: hospitals)
        with patch.object(migration, "seed", AsyncMock()) as seed:
            await migration.migrate(db)
            self.assertEqual(seed.await_count, 2)
        self.assertEqual(db.add.call_args.args[0].key, migration.ROLLOUT)
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
