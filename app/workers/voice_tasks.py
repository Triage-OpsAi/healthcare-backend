"""Two-queue voice pipeline: transcription, then patient registration/EMR."""

import asyncio
import uuid

from celery import Task
from sqlalchemy import select

from app.api.deps import CurrentUser
from app.celery_app import celery_app
from app.db.database import AsyncSessionLocal
from app.db.models import EMRRecord, Encounter, Patient, VoiceIntakeJob
from app.services import (
    b2_storage_service,
    emr_service,
    patient_builder_service,
    sarvam_service,
)

_worker_loop: asyncio.AbstractEventLoop | None = None


def _run(coroutine):
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
    return _worker_loop.run_until_complete(coroutine)


async def _mark_failed(job_id: str, message: str) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(VoiceIntakeJob, uuid.UUID(job_id))
        if job and job.status != "ready":
            job.status = "failed"
            job.error_message = message[:2000]
            await db.commit()


class VoicePipelineTask(Task):
    autoretry_for = (Exception,)
    retry_backoff = True
    retry_backoff_max = 60
    retry_jitter = True
    max_retries = 4
    acks_late = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        if args:
            _run(_mark_failed(str(args[0]), str(exc)))
        super().on_failure(exc, task_id, args, kwargs, einfo)


async def _transcribe(job_id: str) -> None:
    job_uuid = uuid.UUID(job_id)
    async with AsyncSessionLocal() as db:
        job = await db.get(VoiceIntakeJob, job_uuid)
        if job is None or job.status == "ready":
            return
        if job.translated_text:
            job.status = "registering_patient"
            await db.commit()
        else:
            job.status = "transcribing"
            job.error_message = None
            await db.commit()
            audio = await b2_storage_service.download_audio(object_key=job.object_key)
            output = await sarvam_service.transcribe_and_translate(
                audio_bytes=audio,
                filename=job.object_key.rsplit("/", 1)[-1],
                content_type=job.content_type,
                language_code=job.language_code,
            )
            job.raw_transcript = output["raw_transcript"]
            job.translated_text = output["translated_text"]
            job.status = "registering_patient"
            await db.commit()

    build_patient_emr.apply_async(args=[job_id], queue="patient_emr")


@celery_app.task(
    bind=True,
    base=VoicePipelineTask,
    name="voice.transcribe",
)
def transcribe_audio(self, job_id: str) -> None:
    _run(_transcribe(job_id))


async def _build_patient_emr(job_id: str) -> None:
    job_uuid = uuid.UUID(job_id)
    async with AsyncSessionLocal() as db:
        job = await db.get(VoiceIntakeJob, job_uuid)
        if job is None or job.status == "ready":
            return
        if not job.translated_text:
            raise RuntimeError("Transcription is not available for patient registration")

        intake = job.extracted_intake
        if intake is None:
            job.status = "registering_patient"
            await db.commit()
            if job.patient_id is not None:
                intake = {
                    "clinical_note": await emr_service.structure_note(
                        job.translated_text
                    )
                }
            else:
                intake = await patient_builder_service.extract_voice_intake(
                    job.translated_text
                )
            job.extracted_intake = intake

        current_user = CurrentUser(
            user_id=str(job.created_by),
            hospital_id=str(job.hospital_id),
            admin_organization_id=None,
            user_type="clinical",
            role="worker",
            permissions={"emr:create"},
        )

        if job.patient_id is not None and job.encounter_id is None:
            patient = await db.get(Patient, job.patient_id)
            if patient is None or patient.hospital_id != job.hospital_id:
                raise RuntimeError("Selected patient is unavailable")
            encounter = Encounter(
                hospital_id=job.hospital_id,
                patient_id=patient.id,
                doctor_id=job.created_by,
                department=job.department,
                status="open",
            )
            db.add(encounter)
            await db.flush()
            job.encounter_id = encounter.id
            job.status = "generating_emr"
            await db.commit()
        elif job.patient_id is None or job.encounter_id is None:
            patient, encounter, _created = (
                await patient_builder_service.build_patient_and_encounter(
                    db,
                    intake=intake,
                    current_user=current_user,
                    department_override=job.department,
                )
            )
            job.patient_id = patient.id
            job.encounter_id = encounter.id
            job.status = "generating_emr"
            await db.commit()
        else:
            patient_id = job.patient_id
            encounter_id = job.encounter_id

        if job.emr_record_id is None:
            record = EMRRecord(
                hospital_id=job.hospital_id,
                encounter_id=job.encounter_id,
                source_language=job.language_code,
                audio_storage_url=f"b2://{job.bucket_name}/{job.object_key}",
                raw_transcript=job.raw_transcript,
                translated_text=job.translated_text,
                structured_note=intake["clinical_note"],
                created_by=job.created_by,
                status="pending_review",
            )
            db.add(record)
            await db.flush()
            await emr_service.add_code_suggestions(
                db,
                record=record,
                structured_note=intake["clinical_note"],
            )
            job.emr_record_id = record.id
        job.status = "ready"
        job.error_message = None
        await db.commit()


@celery_app.task(
    bind=True,
    base=VoicePipelineTask,
    name="voice.build_patient_emr",
)
def build_patient_emr(self, job_id: str) -> None:
    _run(_build_patient_emr(job_id))
