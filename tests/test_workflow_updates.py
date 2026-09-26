import asyncio
import json
import time
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError
from app.schemas.ward_voice import VitalsCreate
from app.schemas.clinical_documents import ApprovalRequest
from app.services import clinical_documents as documents, patient_list_cache as cache, ward_voice_service as ward
from app.services.ward_voice_reasoning_service import OBSERVATION_SCHEMA
from app.db.clinical_documents import ClinicalAttestation

INK={"strokes":[[[.1,.2],[.2,.3],[.3,.5],[.4,.2],[.5,.4]]]}
def actor(role="doctor"):
    return SimpleNamespace(user_id=str(uuid.uuid4()),hospital_id=str(uuid.uuid4()),role=role,user_type="clinical",permissions={"emr:read","emr:review"})
def vitals():
    return dict(bed_id=uuid.uuid4(),patient_id=uuid.uuid4(),observed_at=datetime.now(timezone.utc),
                readings=[dict(observation_type="blood_glucose",value_numeric=100,unit="mg/dL")])

class VitalTests(unittest.IsolatedAsyncioTestCase):
    def test_units_and_pair_validation(self):
        self.assertEqual(VitalsCreate(**vitals()).readings[0].unit,"mg/dL")
        for readings in ([dict(observation_type="systolic_bp",value_numeric=120,unit="mmHg")],
                         [dict(observation_type="blood_glucose",value_numeric=100,unit="%")],
                         [dict(observation_type="spo2",value_numeric=101,unit="%")],
                         [dict(observation_type="pulse",value_numeric=float("nan"),unit="/min")]):
            data=vitals();data["readings"]=readings
            with self.assertRaises(ValidationError):VitalsCreate(**data)
        types=OBSERVATION_SCHEMA["properties"]["observations"]["items"]["properties"]["observation_type"]["enum"]
        self.assertIn("blood_glucose",types);self.assertIn("respiratory_rate",types)

    async def test_bed_reassignment_cannot_write_to_wrong_patient(self):
        data=VitalsCreate(**vitals());db=AsyncMock();db.add=Mock()
        with patch.object(ward,"_bed",AsyncMock(return_value=SimpleNamespace(patient_id=uuid.uuid4()))):
            with self.assertRaises(HTTPException) as raised:await ward.record_vitals(db,data,actor())
        self.assertEqual(raised.exception.status_code,409);db.add.assert_not_called();db.commit.assert_not_awaited()

class BulkTests(unittest.IsolatedAsyncioTestCase):
    async def test_nurse_cannot_bulk_sign(self):
        with self.assertRaises(HTTPException) as raised:await documents.bulk_preview(AsyncMock(),uuid.uuid4(),None,actor("nurse"))
        self.assertEqual(raised.exception.status_code,403)

    async def test_stale_bulk_revision_never_commits(self):
        user=actor();db=AsyncMock();db.add=Mock()
        with patch('app.services.patient_chart_service._patient',AsyncMock()),patch.object(documents,"bulk_preview",AsyncMock(return_value={"revision":"1"*64,"snapshot":[]})):
            with self.assertRaises(HTTPException) as raised:await documents.signed_approve_all(db,uuid.uuid4(),ApprovalRequest(signature=INK,revision="0"*64,confirmed=True),user)
        self.assertEqual(raised.exception.status_code,409);db.add.assert_not_called();db.commit.assert_not_awaited()

    async def test_bulk_uses_one_commit_and_keeps_each_section_snapshot(self):
        user=actor();patient=SimpleNamespace(id=uuid.uuid4(),hospital_id=uuid.UUID(user.hospital_id));db=AsyncMock();db.add=Mock()
        snapshots=[{"section":key,"content":{"text":key}} for key in ("clinical","timeline")]
        preview={"snapshot":snapshots,"sections":["clinical","timeline"],"revision":documents.revision(snapshots)}
        with patch('app.services.patient_chart_service._patient',AsyncMock(return_value=patient)),patch('app.services.patient_chart_service._section_review',AsyncMock(return_value=SimpleNamespace())),patch.object(documents,"bulk_preview",AsyncMock(return_value=preview)),patch.object(documents,"signer",AsyncMock(return_value=SimpleNamespace(id=uuid.UUID(user.user_id),full_name="Doctor"))):
            result=await documents.signed_approve_all(db,patient.id,ApprovalRequest(signature=INK,revision=preview["revision"],confirmed=True),user)
        rows=[call.args[0] for call in db.add.call_args_list if isinstance(call.args[0],ClinicalAttestation)]
        self.assertEqual(len(rows),2);self.assertEqual([row.snapshot for row in rows],snapshots)
        self.assertTrue(all(row.revision==documents.revision(row.snapshot) for row in rows))
        self.assertEqual(result["approved_sections"],preview["sections"]);db.commit.assert_awaited_once()

class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_key_changes_for_tenant_user_and_database_version(self):
        user=actor();db=AsyncMock();db.execute.return_value=SimpleNamespace(one=lambda:(1,"version-one"))
        first=await cache.cache_key(db,user)
        user.hospital_id=str(uuid.uuid4());self.assertNotEqual(first,await cache.cache_key(db,user))
        second=await cache.cache_key(db,user)
        db.execute.return_value=SimpleNamespace(one=lambda:(2,"version-two"))
        self.assertNotEqual(second,await cache.cache_key(db,user))

    async def test_cache_hit_avoids_full_patient_query(self):
        loader=AsyncMock()
        with patch.object(cache,"cache_key",AsyncMock(return_value="scoped")),patch.object(cache,"redis_call",AsyncMock(return_value="[]")):
            self.assertEqual(await cache.patient_list(AsyncMock(),actor(),loader),[])
        loader.assert_not_awaited()

    async def test_redis_outage_is_bounded_and_database_still_used(self):
        client=SimpleNamespace(get=AsyncMock(side_effect=lambda *_:None))
        async def hang(*args):await asyncio.sleep(60)
        client.get=hang
        with patch.object(cache,"_client",client),patch.object(cache,"_retry_at",0):
            started=time.monotonic();self.assertIsNone(await cache.redis_call("get","key"));self.assertLess(time.monotonic()-started,1)
        loader=AsyncMock(return_value=[])
        with patch.object(cache,"cache_key",AsyncMock(return_value="scoped")),patch.object(cache,"redis_call",AsyncMock(return_value=None)):
            self.assertEqual(await cache.patient_list(AsyncMock(),actor(),loader),[])
        loader.assert_awaited_once()
