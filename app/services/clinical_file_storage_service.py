"""Single private-file boundary for direct upload, verification, and signed access."""

import uuid
from datetime import date

from app.core.config import settings
from app.services import b2_storage_service

CLINICAL_DOCUMENT_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}

AUDIO_EXTENSIONS = {
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/flac": ".flac",
}


def normalize_content_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def report_extension(content_type: str) -> str | None:
    return CLINICAL_DOCUMENT_TYPES.get(normalize_content_type(content_type))


def report_key(*, hospital_id: uuid.UUID, patient_id: uuid.UUID, file_id: uuid.UUID, extension: str) -> str:
    return (
        f"patient-reports/{hospital_id}/{patient_id}/{date.today().isoformat()}/"
        f"{file_id}{extension}"
    )


def discharge_audio_key(
    *, hospital_id: uuid.UUID, patient_id: uuid.UUID, job_id: uuid.UUID, content_type: str
) -> str:
    extension = AUDIO_EXTENSIONS.get(normalize_content_type(content_type), ".bin")
    return (
        f"discharge-instructions/{hospital_id}/{patient_id}/{date.today().isoformat()}/"
        f"{job_id}{extension}"
    )


def discharge_pdf_key(*, hospital_id: uuid.UUID, patient_id: uuid.UUID, job_id: uuid.UUID) -> str:
    return (
        f"discharge-summaries/{hospital_id}/{patient_id}/{date.today().isoformat()}/"
        f"{job_id}.pdf"
    )


def handover_audio_key(
    *, hospital_id: uuid.UUID, patient_id: uuid.UUID, job_id: uuid.UUID, content_type: str
) -> str:
    extension = AUDIO_EXTENSIONS.get(normalize_content_type(content_type), ".bin")
    return (
        f"handovers/{hospital_id}/{patient_id}/{date.today().isoformat()}/"
        f"{job_id}{extension}"
    )


async def direct_upload(*, object_key: str, content_type: str) -> str:
    return await b2_storage_service.create_upload_url(
        object_key=object_key, content_type=normalize_content_type(content_type)
    )


async def verify(*, object_key: str, expected_size: int) -> dict:
    return await b2_storage_service.verify_upload(
        object_key=object_key, expected_size=expected_size
    )


async def read(*, object_key: str) -> bytes:
    return await b2_storage_service.download_object(object_key=object_key)


async def save_generated(*, object_key: str, content_type: str, data: bytes) -> None:
    await b2_storage_service.upload_object(
        object_key=object_key, content_type=content_type, data=data
    )


async def signed_download(*, object_key: str) -> str:
    return await b2_storage_service.create_download_url(object_key=object_key)


def bucket_name() -> str:
    return settings.B2_BUCKET
