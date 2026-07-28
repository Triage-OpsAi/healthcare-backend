"""API-facing lifecycle for recorded discharge instructions and generated PDFs."""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.celery_app import celery_app
from app.core.config import settings
from app.db.models import DischargeSummaryJob, Patient
from app.schemas.patient_chart import (
    AudioAccess,
    DischargeCreateRequest,
    DischargeSummaryItem,
    DischargeUploadResponse,
)
from app.services import clinical_file_storage_service


def _hospital_id(current_user: CurrentUser) -> uuid.UUID:
    if not current_user.hospital_id:
        raise HTTPException(status_code=403, detail="Clinical workspace required")
    return uuid.UUID(current_user.hospital_id)


async def _patient(
    db: AsyncSession, patient_id: uuid.UUID, current_user: CurrentUser
) -> Patient:
    patient = await db.get(Patient, patient_id)
    if patient is None or patient.hospital_id != _hospital_id(current_user):
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


def summarize(job: DischargeSummaryJob) -> DischargeSummaryItem:
    return DischargeSummaryItem(
        id=job.id,
        status=job.status,
        source_language=job.source_language,
        translated_instructions=job.translated_instructions,
        summary_data=job.summary_data,
        audio_available=bool(job.audio_object_key),
        pdf_available=bool(job.pdf_object_key and job.status == "ready"),
        error_message=job.error_message,
        created_at=job.created_at,
    )


async def list_for_patient(
    db: AsyncSession, *, patient_id: uuid.UUID
) -> list[DischargeSummaryItem]:
    jobs = (
        await db.execute(
            select(DischargeSummaryJob)
            .where(DischargeSummaryJob.patient_id == patient_id)
            .order_by(DischargeSummaryJob.created_at.desc())
        )
    ).scalars().all()
    return [summarize(job) for job in jobs]


async def create(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    payload: DischargeCreateRequest,
    current_user: CurrentUser,
) -> DischargeUploadResponse:
    patient = await _patient(db, patient_id, current_user)
    content_type = clinical_file_storage_service.normalize_content_type(
        payload.content_type
    )
    if content_type not in clinical_file_storage_service.AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported discharge audio type")
    job_id = uuid.uuid4()
    object_key = clinical_file_storage_service.discharge_audio_key(
        hospital_id=patient.hospital_id,
        patient_id=patient.id,
        job_id=job_id,
        content_type=content_type,
    )
    job = DischargeSummaryJob(
        id=job_id,
        hospital_id=patient.hospital_id,
        patient_id=patient.id,
        created_by=uuid.UUID(current_user.user_id),
        audio_bucket_name=clinical_file_storage_service.bucket_name(),
        audio_object_key=object_key,
        audio_content_type=content_type,
        audio_file_size=payload.file_size,
        source_language=payload.language_code,
        status="awaiting_upload",
    )
    upload_url = await clinical_file_storage_service.direct_upload(
        object_key=object_key, content_type=content_type
    )
    db.add(job)
    await db.commit()
    return DischargeUploadResponse(
        job_id=job.id,
        upload_url=upload_url,
        content_type=content_type,
        expires_in=settings.AWS_S3_PRESIGN_EXPIRE_SECONDS,
    )


async def complete(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    job_id: uuid.UUID,
    etag: str | None,
    current_user: CurrentUser,
) -> DischargeSummaryItem:
    await _patient(db, patient_id, current_user)
    job = await db.get(DischargeSummaryJob, job_id)
    if job is None or job.patient_id != patient_id:
        raise HTTPException(status_code=404, detail="Discharge summary job not found")
    if job.status != "awaiting_upload":
        return summarize(job)
    metadata = await clinical_file_storage_service.verify(
        object_key=job.audio_object_key, expected_size=job.audio_file_size
    )
    job.audio_etag = (etag or metadata.get("ETag") or "").strip('"') or None
    job.status = "queued"
    await db.commit()
    try:
        celery_app.send_task(
            "discharge.transcribe", args=[str(job.id)], queue="voice_transcription"
        )
    except Exception as exc:
        job.status = "failed"
        job.error_message = "Discharge processing queue is unavailable."
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Discharge processing queue unavailable",
        ) from exc
    return summarize(job)


async def _job(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    job_id: uuid.UUID,
    current_user: CurrentUser,
) -> DischargeSummaryJob:
    await _patient(db, patient_id, current_user)
    job = await db.get(DischargeSummaryJob, job_id)
    if job is None or job.patient_id != patient_id:
        raise HTTPException(status_code=404, detail="Discharge summary job not found")
    return job


async def audio_access(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    job_id: uuid.UUID,
    current_user: CurrentUser,
) -> AudioAccess:
    job = await _job(
        db, patient_id=patient_id, job_id=job_id, current_user=current_user
    )
    return AudioAccess(
        url=await clinical_file_storage_service.signed_download(
            object_key=job.audio_object_key
        ),
        content_type=job.audio_content_type,
        expires_in=settings.AWS_S3_PRESIGN_EXPIRE_SECONDS,
    )


async def pdf_access(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    job_id: uuid.UUID,
    current_user: CurrentUser,
) -> AudioAccess:
    job = await _job(
        db, patient_id=patient_id, job_id=job_id, current_user=current_user
    )
    if job.status != "ready" or not job.pdf_object_key:
        raise HTTPException(status_code=409, detail="Discharge PDF is not ready")
    return AudioAccess(
        url=await clinical_file_storage_service.signed_download(
            object_key=job.pdf_object_key
        ),
        content_type="application/pdf",
        expires_in=settings.AWS_S3_PRESIGN_EXPIRE_SECONDS,
    )
