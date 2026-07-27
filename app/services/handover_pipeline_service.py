"""API-facing lifecycle for recorded and structured clinical handovers."""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.celery_app import celery_app
from app.core.config import settings
from app.db.models import HandoverJob, Patient, User
from app.schemas.patient_chart import (
    AudioAccess,
    HandoverCreateRequest,
    HandoverSummaryItem,
    HandoverUploadResponse,
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


def _summary(job: HandoverJob, creator: User, recipient: User | None) -> HandoverSummaryItem:
    return HandoverSummaryItem(
        id=job.id,
        status=job.status,
        source_language=job.source_language,
        translated_instructions=job.translated_instructions,
        summary_data=job.summary_data,
        captured_by=creator.full_name,
        handed_over_to=recipient.full_name if recipient else None,
        recorded_at=job.created_at,
        audio_available=bool(job.audio_object_key),
        error_message=job.error_message,
    )


async def _with_users(db: AsyncSession, job: HandoverJob) -> HandoverSummaryItem:
    creator = await db.get(User, job.created_by)
    recipient = await db.get(User, job.handed_over_to) if job.handed_over_to else None
    if creator is None:
        raise RuntimeError("Capturing clinician is unavailable")
    return _summary(job, creator, recipient)


async def list_for_patient(
    db: AsyncSession, *, patient_id: uuid.UUID
) -> list[HandoverSummaryItem]:
    jobs = (
        await db.execute(
            select(HandoverJob)
            .where(HandoverJob.patient_id == patient_id)
            .order_by(HandoverJob.created_at.desc())
        )
    ).scalars().all()
    return [await _with_users(db, job) for job in jobs]


async def create(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    payload: HandoverCreateRequest,
    current_user: CurrentUser,
) -> HandoverUploadResponse:
    patient = await _patient(db, patient_id, current_user)
    recipient = (
        await db.get(User, payload.handed_over_to)
        if payload.handed_over_to
        else None
    )
    if recipient is not None and (
        recipient.hospital_id != patient.hospital_id or not recipient.is_active
    ):
        raise HTTPException(status_code=422, detail="Select an active clinician in this hospital")
    content_type = clinical_file_storage_service.normalize_content_type(payload.content_type)
    if content_type not in clinical_file_storage_service.AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported handover audio type")
    job_id = uuid.uuid4()
    object_key = clinical_file_storage_service.handover_audio_key(
        hospital_id=patient.hospital_id,
        patient_id=patient.id,
        job_id=job_id,
        content_type=content_type,
    )
    job = HandoverJob(
        id=job_id,
        hospital_id=patient.hospital_id,
        patient_id=patient.id,
        created_by=uuid.UUID(current_user.user_id),
        handed_over_to=recipient.id if recipient else None,
        audio_bucket_name=clinical_file_storage_service.bucket_name(),
        audio_object_key=object_key,
        audio_content_type=content_type,
        audio_file_size=payload.file_size,
        source_language=payload.language_code,
        status="awaiting_upload",
    )
    db.add(job)
    await db.commit()
    return HandoverUploadResponse(
        job_id=job.id,
        upload_url=await clinical_file_storage_service.direct_upload(
            object_key=object_key, content_type=content_type
        ),
        content_type=content_type,
        expires_in=settings.B2_PRESIGN_EXPIRE_SECONDS,
    )


async def complete(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    job_id: uuid.UUID,
    etag: str | None,
    current_user: CurrentUser,
) -> HandoverSummaryItem:
    await _patient(db, patient_id, current_user)
    job = await db.get(HandoverJob, job_id)
    if job is None or job.patient_id != patient_id:
        raise HTTPException(status_code=404, detail="Handover not found")
    if job.status != "awaiting_upload":
        return await _with_users(db, job)
    metadata = await clinical_file_storage_service.verify(
        object_key=job.audio_object_key, expected_size=job.audio_file_size
    )
    job.audio_etag = (etag or metadata.get("ETag") or "").strip('"') or None
    job.status = "queued"
    await db.commit()
    try:
        celery_app.send_task(
            "handover.transcribe", args=[str(job.id)], queue="voice_transcription"
        )
    except Exception as exc:
        job.status = "failed"
        job.error_message = "Handover processing queue is unavailable."
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Handover processing queue unavailable",
        ) from exc
    return await _with_users(db, job)


async def assign_recipient(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    job_id: uuid.UUID,
    handed_over_to: uuid.UUID,
    current_user: CurrentUser,
) -> HandoverSummaryItem:
    patient = await _patient(db, patient_id, current_user)
    job = await db.get(HandoverJob, job_id)
    if job is None or job.patient_id != patient.id:
        raise HTTPException(status_code=404, detail="Handover not found")
    recipient = await db.get(User, handed_over_to)
    if (
        recipient is None
        or recipient.hospital_id != patient.hospital_id
        or not recipient.is_active
    ):
        raise HTTPException(status_code=422, detail="Select an active clinician in this hospital")
    job.handed_over_to = recipient.id
    await db.commit()
    return await _with_users(db, job)


async def audio_access(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    job_id: uuid.UUID,
    current_user: CurrentUser,
) -> AudioAccess:
    await _patient(db, patient_id, current_user)
    job = await db.get(HandoverJob, job_id)
    if job is None or job.patient_id != patient_id:
        raise HTTPException(status_code=404, detail="Handover not found")
    return AudioAccess(
        url=await clinical_file_storage_service.signed_download(
            object_key=job.audio_object_key
        ),
        content_type=job.audio_content_type,
        expires_in=settings.B2_PRESIGN_EXPIRE_SECONDS,
    )
