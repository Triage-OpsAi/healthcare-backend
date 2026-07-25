"""Two-stage discharge pipeline: multilingual transcription, then summary/PDF."""

import asyncio
import uuid
from datetime import datetime, timezone

from celery import Task
from sqlalchemy import select

from app.celery_app import celery_app
from app.db.database import AsyncSessionLocal
from app.db.models import (
    DischargeSummaryJob,
    EMRRecord,
    Encounter,
    Hospital,
    Patient,
    PatientMedication,
    PatientReport,
)
from app.services import (
    clinical_file_storage_service,
    discharge_summary_service,
    sarvam_service,
)

_loop: asyncio.AbstractEventLoop | None = None


def _run(coroutine):
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop.run_until_complete(coroutine)


async def _mark_failed(job_id: str, message: str) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(DischargeSummaryJob, uuid.UUID(job_id))
        if job and job.status != "ready":
            job.status = "failed"
            job.error_message = message[:2000]
            await db.commit()


class DischargeTask(Task):
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
        job = await db.get(DischargeSummaryJob, uuid.UUID(job_id))
        if job is None or job.status == "ready":
            return
        if not job.translated_instructions:
            job.status = "transcribing"
            job.error_message = None
            await db.commit()
            audio = await clinical_file_storage_service.read(
                object_key=job.audio_object_key
            )
            output = await sarvam_service.transcribe_and_translate(
                audio_bytes=audio,
                filename=job.audio_object_key.rsplit("/", 1)[-1],
                content_type=job.audio_content_type,
                language_code=job.source_language,
            )
            job.raw_transcript = output["raw_transcript"]
            job.translated_instructions = output["translated_text"]
        job.status = "generating_summary"
        await db.commit()
    generate_discharge.apply_async(args=[job_id], queue="patient_emr")


async def _chart_context(db, job: DischargeSummaryJob) -> tuple[dict, Patient, Hospital]:
    patient = await db.get(Patient, job.patient_id)
    hospital = await db.get(Hospital, job.hospital_id)
    if patient is None or hospital is None:
        raise RuntimeError("Patient or hospital is unavailable")
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
            .where(
                PatientReport.patient_id == patient.id,
                PatientReport.status == "approved",
            )
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
    context = {
        "patient": {
            "name": patient.full_name,
            "reference": patient.abha_id or str(patient.id)[:8].upper(),
            "gender": patient.gender,
            "date_of_birth": patient.date_of_birth,
        },
        "emr_records": [
            {
                "date": record.created_at,
                "department": encounter.department,
                "status": record.status,
                "note": record.structured_note,
            }
            for record, encounter in records
        ],
        "reports": [
            {
                "title": report.title,
                "document_type": report.document_type,
                "summary": report.summary,
                "key_findings": report.key_findings or [],
            }
            for report in reports
        ],
        "medications": [
            {
                "name": medication.name,
                "dosage": medication.dosage,
                "frequency": medication.frequency,
                "route": medication.route,
                "duration": medication.duration,
                "instructions": medication.instructions,
                "active": medication.is_active,
            }
            for medication in medications
        ],
    }
    return context, patient, hospital


async def _generate(job_id: str) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(DischargeSummaryJob, uuid.UUID(job_id))
        if job is None or job.status == "ready":
            return
        if not job.translated_instructions:
            raise RuntimeError("Translated discharge instructions are unavailable")
        job.status = "generating_summary"
        job.error_message = None
        await db.commit()
        context, patient, hospital = await _chart_context(db, job)
        summary = await discharge_summary_service.generate_summary(
            chart_context=context,
            translated_instructions=job.translated_instructions,
        )
        job.summary_data = summary
        job.status = "generating_pdf"
        await db.commit()
        pdf = discharge_summary_service.render_pdf(
            hospital_name=hospital.name,
            patient_name=patient.full_name,
            patient_reference=patient.abha_id or str(patient.id)[:8].upper(),
            generated_at=datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
            summary=summary,
        )
        pdf_key = clinical_file_storage_service.discharge_pdf_key(
            hospital_id=job.hospital_id,
            patient_id=job.patient_id,
            job_id=job.id,
        )
        await clinical_file_storage_service.save_generated(
            object_key=pdf_key,
            content_type="application/pdf",
            data=pdf,
        )
        job.pdf_bucket_name = clinical_file_storage_service.bucket_name()
        job.pdf_object_key = pdf_key
        job.status = "ready"
        await db.commit()


@celery_app.task(bind=True, base=DischargeTask, name="discharge.transcribe")
def transcribe_discharge(self, job_id: str) -> None:
    _run(_transcribe(job_id))


@celery_app.task(bind=True, base=DischargeTask, name="discharge.generate")
def generate_discharge(self, job_id: str) -> None:
    _run(_generate(job_id))
