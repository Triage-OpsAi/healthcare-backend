"""Two-stage multilingual transcription and structured handover pipeline."""

import asyncio
import uuid

from celery import Task
from sqlalchemy import select

from app.celery_app import celery_app
from app.db.database import AsyncSessionLocal
from app.db.models import EMRRecord, Encounter, HandoverJob, Patient, PatientMedication, PatientReport
from app.services import audit_events, audit_service, clinical_file_storage_service, handover_reasoning_service, sarvam_service

_loop: asyncio.AbstractEventLoop | None = None


def _run(coroutine):
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop.run_until_complete(coroutine)


async def _mark_failed(job_id: str, message: str) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(HandoverJob, uuid.UUID(job_id))
        if job and job.status != "ready":
            job.status = "failed"
            job.error_message = message[:2000]
            await db.commit()


class HandoverTask(Task):
    autoretry_for = (Exception,)
    retry_backoff = True
    retry_backoff_max = 60
    max_retries = 3
    acks_late = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        if args:
            _run(_mark_failed(str(args[0]), str(exc)))
        super().on_failure(exc, task_id, args, kwargs, einfo)


async def _transcribe(job_id: str) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(HandoverJob, uuid.UUID(job_id))
        if job is None or job.status == "ready":
            return
        if not job.translated_instructions:
            job.status = "transcribing"
            job.error_message = None
            await db.commit()
            await audit_service.safe_log_event(
                hospital_id=job.hospital_id, user_id=job.created_by,
                action="transcription.started", resource_type="handover",
                resource_id=job.id, patient_id=job.patient_id, outcome="queued",
            )
            audio = await clinical_file_storage_service.read(object_key=job.audio_object_key)
            output = await sarvam_service.transcribe_and_translate(
                audio_bytes=audio,
                filename=job.audio_object_key.rsplit("/", 1)[-1],
                content_type=job.audio_content_type,
                language_code=job.source_language,
            )
            job.raw_transcript = output["raw_transcript"]
            job.translated_instructions = output["translated_text"]
            await audit_service.safe_log_event(
                hospital_id=job.hospital_id, user_id=job.created_by,
                action=audit_events.TRANSCRIPT_GENERATED, resource_type="handover",
                resource_id=job.id, patient_id=job.patient_id,
            )
            if job.source_language == "unknown":
                await audit_service.safe_log_event(
                    hospital_id=job.hospital_id, user_id=job.created_by,
                    action=audit_events.LANGUAGE_DETECTED, resource_type="handover",
                    resource_id=job.id, patient_id=job.patient_id,
                    event_metadata={"language_code": "auto", "detection_mode": "automatic"},
                )
            await audit_service.safe_log_event(
                hospital_id=job.hospital_id, user_id=job.created_by,
                action="translation.generated", resource_type="handover",
                resource_id=job.id, patient_id=job.patient_id,
            )
        job.status = "generating_summary"
        await db.commit()
    generate_handover.apply_async(args=[job_id], queue="patient_emr")


async def _chart_context(db, job: HandoverJob) -> dict:
    patient = await db.get(Patient, job.patient_id)
    if patient is None:
        raise RuntimeError("Patient is unavailable")
    records = (
        await db.execute(
            select(EMRRecord, Encounter)
            .join(Encounter, Encounter.id == EMRRecord.encounter_id)
            .where(Encounter.patient_id == patient.id)
            .order_by(EMRRecord.created_at)
        )
    ).all()
    reports = (
        await db.execute(
            select(PatientReport)
            .where(PatientReport.patient_id == patient.id, PatientReport.status == "approved")
            .order_by(PatientReport.created_at)
        )
    ).scalars().all()
    medications = (
        await db.execute(
            select(PatientMedication)
            .where(PatientMedication.patient_id == patient.id)
            .order_by(PatientMedication.created_at)
        )
    ).scalars().all()
    return {
        "patient": {
            "name": patient.full_name,
            "reference": patient.abha_id or str(patient.id)[:8].upper(),
            "gender": patient.gender,
            "date_of_birth": patient.date_of_birth,
        },
        "encounters": [
            {
                "captured_at": record.created_at,
                "department": encounter.department,
                "note": record.structured_note,
            }
            for record, encounter in records
        ],
        "approved_reports": [
            {
                "title": report.title,
                "summary": report.summary,
                "key_findings": report.key_findings or [],
            }
            for report in reports
        ],
        "medications": [
            {
                "name": item.name,
                "dosage": item.dosage,
                "frequency": item.frequency,
                "route": item.route,
                "duration": item.duration,
                "instructions": item.instructions,
                "active": item.is_active,
            }
            for item in medications
        ],
    }


async def _generate(job_id: str) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(HandoverJob, uuid.UUID(job_id))
        if job is None or job.status == "ready":
            return
        if not job.translated_instructions:
            raise RuntimeError("Translated handover instructions are unavailable")
        job.status = "generating_summary"
        job.error_message = None
        await db.commit()
        job.summary_data = await handover_reasoning_service.generate_summary(
            chart_context=await _chart_context(db, job),
            translated_instructions=job.translated_instructions,
        )
        job.status = "ready"
        await db.commit()
        await audit_service.safe_log_event(
            hospital_id=job.hospital_id, user_id=job.created_by,
            action=audit_events.CLINICAL_EXTRACTION_CREATED, resource_type="handover",
            resource_id=job.id, patient_id=job.patient_id,
            event_metadata={"extracted_values": job.summary_data},
        )
        await audit_service.safe_log_event(
            hospital_id=job.hospital_id, user_id=job.created_by,
            action=audit_events.NOTE_DRAFT_CREATED, resource_type="handover",
            resource_id=job.id, patient_id=job.patient_id,
        )


@celery_app.task(bind=True, base=HandoverTask, name="handover.transcribe")
def transcribe_handover(self, job_id: str) -> None:
    _run(_transcribe(job_id))


@celery_app.task(bind=True, base=HandoverTask, name="handover.generate")
def generate_handover(self, job_id: str) -> None:
    _run(_generate(job_id))
