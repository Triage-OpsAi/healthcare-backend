import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.services import voice_job_service


class CompleteVoiceJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_failure_remains_retryable(self):
        hospital_id = uuid.uuid4()
        job = SimpleNamespace(
            id=uuid.uuid4(),
            hospital_id=hospital_id,
            status="failed",
            error_message="Processing queue is unavailable",
            object_key="voice/test.webm",
            file_size=10,
            etag=None,
        )
        db = SimpleNamespace(
            get=AsyncMock(return_value=job),
            commit=AsyncMock(),
        )
        current_user = SimpleNamespace(hospital_id=str(hospital_id))
        verify_upload = AsyncMock(return_value={"ETag": "etag"})

        with self.assertLogs(voice_job_service.logger, level="ERROR"):
            with (
                patch.object(
                    voice_job_service.s3_storage_service,
                    "verify_upload",
                    verify_upload,
                ),
                patch.object(
                    voice_job_service.celery_app,
                    "send_task",
                    side_effect=ConnectionError("broker down"),
                ),
                self.assertRaises(HTTPException) as raised,
            ):
                await voice_job_service.complete_job(
                    db,
                    job_id=job.id,
                    etag=None,
                    current_user=current_user,
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(job.status, "awaiting_upload")
        self.assertEqual(job.error_message, "Processing queue is unavailable")

        with (
            patch.object(
                voice_job_service.s3_storage_service,
                "verify_upload",
                verify_upload,
            ),
            patch.object(
                voice_job_service.celery_app,
                "send_task",
            ) as send_task,
        ):
            result = await voice_job_service.complete_job(
                db,
                job_id=job.id,
                etag=None,
                current_user=current_user,
            )

        self.assertIs(result, job)
        self.assertEqual(job.status, "queued")
        self.assertIsNone(job.error_message)
        send_task.assert_called_once_with(
            "voice.transcribe",
            args=[str(job.id)],
            queue="voice_transcription",
        )


if __name__ == "__main__":
    unittest.main()
