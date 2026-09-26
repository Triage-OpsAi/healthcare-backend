import copy
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError
from app.schemas.clinical_documents import DemoConsentForm
from app.services.demo_consent import TEMPLATE, LEGACY_TEMPLATE, build_demo_form, consent_prefill
from app.services.clinical_documents import revision
from app.api import routes_clinical_documents as routes
from app.api.deps import CurrentUser

INK = {"strokes": [[[0.1, 0.2], [0.2, 0.4], [0.3, 0.3], [0.4, 0.6], [0.5, 0.2]]]}


def payload():
    return dict(template_version="demo-consent-v1", department_id=str(uuid.uuid4()), language="hi",
                fields={key: {"ink": copy.deepcopy(INK)} for key in
                        ("patient_name", "representative_name", "clinician_name", "procedure", "purpose")},
                data_decision="declined", procedure_decision="accepted",
                patient_signature=INK, clinician_signature=INK, confirmed=True)


class DemoConsentTests(unittest.TestCase):
    def test_independent_decisions_and_handwriting_survive_snapshot(self):
        model = DemoConsentForm(**payload())
        form = build_demo_form(model, SimpleNamespace(id=uuid.uuid4(), full_name="Test Patient"), "Test Hospital", "Test Clinician", datetime.now(timezone.utc))
        self.assertEqual(form["demo"]["data_decision"], "declined")
        self.assertEqual(form["demo"]["procedure_decision"], "accepted")
        self.assertEqual(form["demo"]["fields"]["patient_name"]["ink"], INK)
        self.assertEqual(form["demo"]["template"], LEGACY_TEMPLATE)
        before = revision(form)
        form["demo"]["data_decision"] = "accepted"
        self.assertNotEqual(before, revision(form))
        self.assertEqual(len(TEMPLATE["en"]["sections"]), 6)
        self.assertEqual(set(TEMPLATE["en"]), set(TEMPLATE["hi"]))

    def test_requires_each_choice_and_each_signature(self):
        for key in ("data_decision", "procedure_decision", "patient_signature", "clinician_signature", "confirmed"):
            data = payload()
            del data[key]
            with self.subTest(key=key), self.assertRaises(ValidationError):
                DemoConsentForm(**data)

    def test_rejects_blank_name_unknown_fields_and_untrusted_template(self):
        for mutate in (lambda data: data["fields"].update(patient_name={"text": " "}),
                       lambda data: data["fields"].update(arbitrary={"text": "unknown"}),
                       lambda data: data.update(template={"en": "changed wording"}),
                       lambda data: data.update(template_version="unknown"),
                       lambda data: data["fields"].update(patient_name={"text": "typed", "ink": INK})):
            data = payload()
            mutate(data)
            with self.assertRaises(ValidationError):
                DemoConsentForm(**data)

    def test_typed_input_and_optional_witness(self):
        data = payload()
        data["fields"]["patient_name"] = {"text": " Test Patient "}
        self.assertEqual(DemoConsentForm(**data).fields["patient_name"].text, "Test Patient")
        data["fields"]["witness_name"] = {"ink": INK}
        with self.assertRaises(ValidationError):
            DemoConsentForm(**data)
        data["witness_signature"] = INK
        self.assertIsNotNone(DemoConsentForm(**data).witness_signature)


class DemoRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_patient_visit_is_rejected_without_save(self):
        actor = CurrentUser(str(uuid.uuid4()), str(uuid.uuid4()), None, "clinical", "doctor", {"emr:create"})
        patient = SimpleNamespace(id=uuid.uuid4(), hospital_id=uuid.UUID(actor.hospital_id))
        data = payload(); data["visit_id"] = str(uuid.uuid4())
        db = AsyncMock(); db.add = Mock()
        db.get.return_value = SimpleNamespace(patient_id=uuid.uuid4(), hospital_id=patient.hospital_id)
        with patch.object(routes.charts, "_patient", AsyncMock(return_value=patient)), patch.object(routes.documents, "department", AsyncMock()), patch.object(routes.documents, "signer", AsyncMock()):
            with self.assertRaises(HTTPException) as caught:
                await routes.create_demo_consent(patient.id, DemoConsentForm(**data), db, actor)
        self.assertEqual(caught.exception.status_code, 404)
        db.add.assert_not_called(); db.commit.assert_not_awaited()

    async def test_saved_row_keeps_demo_wording_and_both_choices(self):
        actor = CurrentUser(str(uuid.uuid4()), str(uuid.uuid4()), None, "clinical", "doctor", {"emr:create"})
        patient = SimpleNamespace(id=uuid.uuid4(), hospital_id=uuid.UUID(actor.hospital_id), full_name="Test Patient")
        db = AsyncMock(); db.add = Mock(); db.get.return_value = SimpleNamespace(name="Test Hospital")
        with patch.object(routes.charts, "_patient", AsyncMock(return_value=patient)), patch.object(routes.documents, "department", AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), name="Radiology"))), patch.object(routes.documents, "signer", AsyncMock(return_value=SimpleNamespace(id=uuid.UUID(actor.user_id), full_name="Test Doctor"))), patch.object(routes.documents, "audit"), patch.object(routes.documents, "consent_json", side_effect=lambda row: row.form):
            result = await routes.create_demo_consent(patient.id, DemoConsentForm(**payload()), db, actor)
        self.assertEqual(result["demo"]["data_decision"], "declined")
        self.assertEqual(result["demo"]["procedure_decision"], "accepted")
        self.assertIn("NOT FOR ACTUAL PATIENT CARE", result["demo"]["template"]["en"]["demo_title"])
        db.commit.assert_awaited_once()


class HospitalConsentTests(unittest.TestCase):
    def test_current_form_has_no_demo_notice_and_preserves_legacy_version(self):
        data = payload(); data["template_version"] = "hospital-consent-v2"
        patient = SimpleNamespace(id=uuid.uuid4(), full_name="Patient", abha_id="PT-123")
        form = build_demo_form(DemoConsentForm(**data), patient, "Hospital", "Doctor", datetime.now(timezone.utc))
        self.assertEqual(form["version"], "hospital-consent-v2")
        self.assertEqual(form["demo"]["patient_reference"], "PT-123")
        for lang in ("en", "hi"):
            self.assertEqual(form["demo"]["template"][lang]["demo_notice"], "")
            self.assertTrue(form["demo"]["template"][lang]["policy_notice"])
        self.assertIn("NOT FOR ACTUAL PATIENT CARE", LEGACY_TEMPLATE["en"]["demo_title"])

    def test_prefill_uses_record_and_correct_age_before_birthday(self):
        from datetime import date
        patient = SimpleNamespace(full_name="Patient One", date_of_birth=datetime(1991, 9, 27, tzinfo=timezone.utc), gender="Female", phone="9999999999")
        fields = consent_prefill(patient, "Doctor One", date(2026,9,26))
        self.assertEqual(fields["age"]["text"], "34")
        self.assertEqual(fields["patient_name"]["text"], "Patient One")
        self.assertEqual(fields["representative_name"]["text"], "Patient One")
        self.assertEqual(fields["clinician_name"]["text"], "Doctor One")
        self.assertEqual(fields["mobile"]["text"], "9999999999")
        self.assertNotIn("address", fields)
        self.assertNotIn("registration", fields)
        self.assertNotIn("patient_signature", fields)
        self.assertEqual(consent_prefill(patient,"Doctor",date(2026,9,27))["age"]["text"],"35")

    def test_prefill_leaves_missing_fields_empty_and_does_not_reuse_other_patient(self):
        first = consent_prefill(SimpleNamespace(full_name="Patient One", phone="1111111111"), "Doctor")
        second = consent_prefill(SimpleNamespace(full_name="Patient Two"), "Doctor")
        self.assertEqual(second["patient_name"]["text"], "Patient Two")
        self.assertNotIn("mobile", second)
        self.assertNotIn("age", second)
        self.assertEqual(first["mobile"]["text"], "1111111111")
