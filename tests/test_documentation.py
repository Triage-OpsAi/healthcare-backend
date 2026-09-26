import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from fastapi import HTTPException
from app.services import documentation_service as service
from app.services.documentation_templates import TEMPLATES, checks, suggest, suggest_template
from app.schemas.documentation import DraftCreate, DraftUpdate, ReviewRequest, FinalizeRequest

INK={"strokes":[[[.1,.2],[.2,.4],[.3,.5],[.4,.2],[.6,.4]]]}
def user(role="doctor"):
    return SimpleNamespace(user_id=str(uuid.uuid4()),hospital_id=str(uuid.uuid4()),role=role)
def row():
    return SimpleNamespace(id=uuid.uuid4(),status="draft",revision=1,template_snapshot=TEMPLATES["medicine_opd"],
        fields={"plan":{"value":"Review","source":"clinician_entered","evidence":""}},transcript="Plan: Review",reviewed_by=None)

class TemplateTests(unittest.TestCase):
    def test_six_specialties_have_unique_fields(self):
        self.assertEqual({t["specialty"] for t in TEMPLATES.values()},{"General Medicine","Surgery","Pediatrics","OB/GYN","Radiology","Oncology"})
        for template in TEMPLATES.values():
            keys=[f["key"] for f in template["fields"]]
            self.assertEqual(len(keys),len(set(keys)),template["id"])
        self.assertNotIn("subjective",[f["key"] for f in TEMPLATES["radiology_report"]["fields"]])
        self.assertEqual(suggest("Obstetrics and Gynecology"),"OB/GYN")
    def test_extraction_rejects_invented_values_and_unknown_fields(self):
        text="Plan: Review tomorrow. No antibiotics prescribed."
        result=service.validate_extraction([{"key":"plan","evidence":"Review tomorrow."},{"key":"allergies","evidence":"No known allergies"},{"key":"invented","evidence":"Review tomorrow."}],text,TEMPLATES["medicine_opd"])
        self.assertEqual(list(result),["plan"])
        self.assertEqual(result["plan"]["value"],"Review tomorrow.")
    def test_missing_critical_communication_flagged(self):
        result=checks(TEMPLATES["radiology_report"],{"critical_finding":{"value":"Appendicitis"}})
        self.assertIn("person_contacted",[item["key"] for item in result])
    def test_finalized_and_stale_edits_rejected(self):
        r=row();r.status="finalized"
        with self.assertRaises(HTTPException):service.editable(r,1)
        r.status="draft"
        with self.assertRaises(HTTPException):service.editable(r,2)

class SuggestionTests(unittest.TestCase):
    def test_pediatric_context_and_explicit_workflow(self):
        self.assertEqual(suggest_template("Pediatrics","",10)["template_id"],"pediatrics_neonatal")
        self.assertEqual(suggest_template("Pediatrics","",900)["template_id"],"pediatrics_acute")
        self.assertEqual(suggest_template("Surgery","Discharge summary")["template_id"],"surgery_discharge")
        self.assertEqual(suggest_template("General","Operative report")["template_id"],"surgery_operative")
    def test_unknown_is_missing_but_explicit_negative_is_documented(self):
        required=lambda value:[c["key"] for c in checks(TEMPLATES["medicine_opd"],{"allergies":{"value":value}})]
        self.assertIn("allergies",required("Allergies were not discussed"))
        self.assertNotIn("allergies",required("No known drug allergies"))

class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_tenant_document_not_returned(self):
        db=AsyncMock();db.scalar.return_value=None
        with self.assertRaises(HTTPException) as error:await service.get_document(db,uuid.uuid4(),user())
        self.assertEqual(error.exception.status_code,404)
        query=str(db.scalar.call_args.args[0]);self.assertIn("hospital_id",query)
    async def test_wrong_patient_encounter_rejected(self):
        with patch.object(service,"context",AsyncMock(return_value={"patient":{},"encounters":[]})):
            with self.assertRaises(HTTPException):await service.create(AsyncMock(),DraftCreate(patient_id=uuid.uuid4(),encounter_id=uuid.uuid4(),template_id="medicine_opd"),user())
    async def test_nurse_cannot_review_or_sign(self):
        for fn,payload in [(service.review,ReviewRequest(revision=1)),(service.finalize,FinalizeRequest(revision=1,signature=INK,confirmed=True))]:
            with self.assertRaises(HTTPException) as error:await fn(AsyncMock(),uuid.uuid4(),payload,user("nurse"))
            self.assertEqual(error.exception.status_code,403)
    async def test_missing_checks_require_reasons(self):
        with patch.object(service,"get_document",AsyncMock(return_value=row())):
            with self.assertRaises(HTTPException) as error:await service.review(AsyncMock(),uuid.uuid4(),ReviewRequest(revision=1),user())
            self.assertEqual(error.exception.status_code,422)
    async def test_edits_invalidate_review(self):
        r=row();r.status="reviewed";r.reviewed_by=uuid.uuid4()
        with patch.object(service,"get_document",AsyncMock(return_value=r)),patch.object(service,"persist",AsyncMock()):
            await service.update(AsyncMock(),r.id,DraftUpdate(revision=1,transcript=r.transcript,fields={"plan":"Reassess"}),user())
        self.assertEqual(r.status,"draft");self.assertIsNone(r.reviewed_by);self.assertEqual(r.revision,2)
        self.assertEqual(r.fields["plan"]["source"],"clinician_entered")
    async def test_sign_requires_own_review(self):
        r=row();r.status="reviewed";r.reviewed_by=uuid.uuid4()
        with patch.object(service,"get_document",AsyncMock(return_value=r)):
            with self.assertRaises(HTTPException):await service.finalize(AsyncMock(),r.id,FinalizeRequest(revision=1,signature=INK,confirmed=True),user())
    async def test_regeneration_keeps_clinician_correction(self):
        r=row();r.fields={"plan":{"value":"Clinician correction","source":"clinician_entered","evidence":""}}
        with patch.object(service,"get_document",AsyncMock(return_value=r)),patch.object(service,"extract",AsyncMock(return_value={"plan":{"value":"Review","source":"dictation"}})),patch.object(service,"persist",AsyncMock()):
            await service.generate(AsyncMock(),r.id,ReviewRequest(revision=1),user())
        self.assertEqual(r.fields["plan"]["value"],"Clinician correction")
    async def test_sign_retry_is_idempotent(self):
        u=user();r=row();r.status="finalized";r.reviewed_by=uuid.UUID(u.user_id);r.signature=INK;r.revision=2
        with patch.object(service,"get_document",AsyncMock(return_value=r)),patch.object(service,"serialize",return_value={"status":"finalized"}),patch.object(service,"persist",AsyncMock()) as persist:
            result=await service.finalize(AsyncMock(),r.id,FinalizeRequest(revision=1,signature=INK,confirmed=True),u)
        self.assertEqual(result["status"],"finalized");persist.assert_not_awaited()
