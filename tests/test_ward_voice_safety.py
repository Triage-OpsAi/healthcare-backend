import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.services import ward_voice_service


class WardVoiceSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_complete_returns_existing_capture(self):
        hospital_id = uuid.uuid4()
        capture = SimpleNamespace(
            id=uuid.uuid4(), hospital_id=hospital_id, status="pending_confirmation",
            raw_transcript="original", translated_text="English",
            extraction_payload={"observations": []}, error_message=None,
        )
        db = SimpleNamespace(get=AsyncMock(return_value=capture))
        user = SimpleNamespace(hospital_id=str(hospital_id), user_id=str(uuid.uuid4()))
        with patch.object(ward_voice_service.s3_storage_service, "verify_upload") as verify:
            result = await ward_voice_service.complete_capture(db, capture.id, None, user)
        verify.assert_not_called()
        self.assertEqual(result["status"], "pending_confirmation")

    async def test_closed_chart_rejects_amendment(self):
        db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())))
        with self.assertRaises(HTTPException) as raised:
            await ward_voice_service._assert_chart_open(db, uuid.uuid4(), __import__("datetime").date.today())
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
