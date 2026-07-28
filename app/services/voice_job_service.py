"""Create, verify, enqueue, and report direct-upload voice jobs."""

import re
import uuid
from datetime import date
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.celery_app import celery_app
from app.core.config import settings
from app.db.models import Patient, VoiceIntakeJob
from app.schemas.emr import (
    VoiceJobCreateRequest,
    VoiceJobSummary,
    VoiceJobUploadResponse,
)
from app.services import patient_builder_service, s3_storage_service
from app.services.sarvam_service import SUPPORTED_LANGUAGES, normalize_audio_content_type

ALLOWED_AUDIO_TYPES = {
    "audio/webm", "video/webm", "audio/mp4", "audio/x-m4a", "audio/mpeg",
    "audio/mp3", "audio/wav", "audio/x-wav", "audio/aac", "audio/ogg",
    "audio/opus", "audio/flac", "application/octet-stream",
}


def _hospital_id(current_user: CurrentUser) -> uuid.UUID:
    if not current_user.hospital_id:
        raise HTTPException(status_code=403, detail="Clinical workspace required")
    return uuid.UUID(current_user.hospital_id)


def _extension(content_type: str) -> str:
    return {
        "audio/webm": ".webm", "video/webm": ".webm", "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a", "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
        "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/aac": ".aac",
        "audio/ogg": ".ogg", "audio/opus": ".opus", "audio/flac": ".flac",
    }.get(content_type, ".bin")


async def create_job(
    db: AsyncSession,
    *,
    payload: VoiceJobCreateRequest,
    current_user: CurrentUser,
) -> VoiceJobUploadResponse:
    if payload.language_code not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="Unsupported language code")
    content_type = normalize_audio_content_type(payload.content_type)
    if content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported audio type")
    if payload.file_size > settings.VOICE_UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Recording exceeds the 100 MB limit")

    job_id = uuid.uuid4()
    hospital_id = _hospital_id(current_user)
    if payload.patient_id:
        patient = await db.get(Patient, payload.patient_id)
        if patient is None or patient.hospital_id != hospital_id:
            raise HTTPException(status_code=404, detail="Patient not found")
    object_key = (
        f"voice-intakes/{hospital_id}/{date.today().isoformat()}/"
        f"{job_id}{_extension(content_type)}"
    )
    job = VoiceIntakeJob(
        id=job_id,
        hospital_id=hospital_id,
        created_by=uuid.UUID(current_user.user_id),
        bucket_name=settings.AWS_S3_BUCKET,
        object_key=object_key,
        content_type=content_type,
        file_size=payload.file_size,
        language_code=payload.language_code,
        department=payload.department.strip() if payload.department else None,
        patient_id=payload.patient_id,
        status="awaiting_upload",
    )
    try:
        upload_url = await s3_storage_service.create_upload_url(
            object_key=object_key, content_type=content_type
        )
    except s3_storage_service.S3ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Voice storage is not configured.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to prepare the voice upload.",
        ) from exc
    db.add(job)
    await db.commit()
    return VoiceJobUploadResponse(
        job_id=job.id,
        upload_url=upload_url,
        object_key=object_key,
        content_type=content_type,
        expires_in=settings.AWS_S3_PRESIGN_EXPIRE_SECONDS,
    )


async def complete_job(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    etag: str | None,
    current_user: CurrentUser,
) -> VoiceIntakeJob:
    job = await db.get(VoiceIntakeJob, job_id)
    if job is None or job.hospital_id != _hospital_id(current_user):
        raise HTTPException(status_code=404, detail="Voice job not found")
    if job.status != "awaiting_upload":
        return job
    try:
        metadata = await s3_storage_service.verify_upload(
            object_key=job.object_key, expected_size=job.file_size
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to verify the uploaded recording.",
        ) from exc
    job.etag = (etag or metadata.get("ETag") or "").strip('"') or None
    job.status = "queued"
    await db.commit()
    try:
        celery_app.send_task(
            "voice.transcribe", args=[str(job.id)], queue="voice_transcription"
        )
    except Exception as exc:
        job.status = "failed"
        job.error_message = "Processing queue is unavailable"
        await db.commit()
        raise HTTPException(status_code=503, detail="Processing queue unavailable") from exc
    return job


def _age(patient: Patient | None) -> int | None:
    return patient_builder_service.patient_age(patient) if patient else None


async def summarize_job(
    db: AsyncSession, *, job: VoiceIntakeJob
) -> VoiceJobSummary:
    patient = await db.get(Patient, job.patient_id) if job.patient_id else None
    return VoiceJobSummary(
        id=job.id,
        status=job.status,
        patient_id=job.patient_id,
        patient_name=patient.full_name if patient else None,
        patient_reference=(
            (patient.abha_id or str(patient.id)[:8].upper()) if patient else None
        ),
        patient_age=_age(patient),
        emr_record_id=job.emr_record_id,
        error_message=job.error_message,
        created_at=job.created_at,
    )


async def list_jobs(
    db: AsyncSession, *, current_user: CurrentUser
) -> list[VoiceJobSummary]:
    rows = (
        await db.execute(
            select(VoiceIntakeJob, Patient)
            .outerjoin(Patient, Patient.id == VoiceIntakeJob.patient_id)
            .where(
                VoiceIntakeJob.hospital_id == _hospital_id(current_user),
                VoiceIntakeJob.status.notin_(["ready", "awaiting_upload"]),
            )
            .order_by(VoiceIntakeJob.created_at.desc())
            .limit(50)
        )
    ).all()
    return [
        VoiceJobSummary(
            id=job.id,
            status=job.status,
            patient_id=job.patient_id,
            patient_name=patient.full_name if patient else None,
            patient_reference=(
                patient.abha_id or str(patient.id)[:8].upper() if patient else None
            ),
            patient_age=_age(patient),
            emr_record_id=job.emr_record_id,
            error_message=job.error_message,
            created_at=job.created_at,
        )
        for job, patient in rows
    ]
