import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.db.database import get_db
from app.db.models import EMRRecord, EMRRecordCode, Encounter, MedicalCode
from app.schemas.common import ErrorResponse
from app.schemas.emr import (
    CodeSuggestion,
    EMRIngestResponse,
    EMRRecordDetail,
    EMRReviewRequest,
    EncounterSummary,
    VoiceJobCompleteRequest,
    VoiceJobCreateRequest,
    VoiceJobSummary,
    VoiceJobUploadResponse,
    VoiceIntakeResponse,
    VoicePatientDetails,
)
from app.services import audit_service, emr_service, patient_builder_service, sarvam_service
from app.services.authorization import assert_same_hospital, require_permission
from app.services.sarvam_service import SUPPORTED_LANGUAGES

router = APIRouter(prefix="/emr", tags=["EMR Records"])
MAX_AUDIO_BYTES = 25 * 1024 * 1024


@router.get(
    "/encounters",
    response_model=list[EncounterSummary],
    summary="List the doctor's encounters",
    description=(
        "Returns open and closed encounters assigned to the authenticated doctor. "
        "Use an `id` from this response as `encounter_id` in the audio upload API."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid bearer token."},
        403: {"model": ErrorResponse, "description": "Missing `emr:read` permission."},
    },
    operation_id="list_emr_encounters",
)
async def list_encounters(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:read")),
):
    result = await db.execute(
        select(Encounter)
        .where(
            Encounter.hospital_id == uuid.UUID(current_user.hospital_id),
            Encounter.doctor_id == uuid.UUID(current_user.user_id),
        )
        .order_by(Encounter.created_at.desc())
    )
    return result.scalars().all()


@router.post(
    "/records/upload",
    response_model=EMRIngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and process clinical dictation",
    description=(
        "Uploads one audio clip for an existing encounter, transcribes and translates it, "
        "creates a structured SOAP note, and returns the record in `pending_review`. "
        "Requires the `emr:create` permission."
    ),
    response_description="Processed EMR record awaiting doctor review.",
    responses={
        400: {"model": ErrorResponse, "description": "Unsupported language or empty audio file."},
        401: {"model": ErrorResponse, "description": "Missing or invalid bearer token."},
        403: {"model": ErrorResponse, "description": "Permission or tenant check failed."},
        404: {"model": ErrorResponse, "description": "Encounter not found."},
        502: {"model": ErrorResponse, "description": "External processing pipeline failed."},
    },
    operation_id="upload_emr_audio",
)
async def upload_audio(
    request: Request,
    encounter_id: uuid.UUID = Form(..., description="Existing encounter UUID."),
    language_code: str = Form(
        "unknown",
        description="BCP-47 language code. Use `unknown` for automatic detection.",
        examples=["hi-IN"],
    ),
    audio_file: UploadFile = File(..., description="Clinical dictation audio clip."),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    if language_code not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported language_code. Allowed: {', '.join(SUPPORTED_LANGUAGES)}",
        )

    audio_bytes = await audio_file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Audio file is empty")

    encounter_result = await db.execute(select(Encounter).where(Encounter.id == encounter_id))
    encounter = encounter_result.scalar_one_or_none()
    if encounter is None:
        raise HTTPException(status_code=404, detail="Encounter not found")
    assert_same_hospital(current_user, str(encounter.hospital_id))

    record = EMRRecord(
        hospital_id=encounter.hospital_id,
        encounter_id=encounter.id,
        source_language=language_code,
        created_by=current_user.user_id,
        status="draft",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    await audit_service.log_event(
        db,
        hospital_id=current_user.hospital_id,
        user_id=current_user.user_id,
        action="emr_record.upload",
        resource_type="emr_record",
        resource_id=record.id,
        ip_address=request.client.host if request.client else None,
    )

    try:
        await emr_service.process_uploaded_audio(
            db,
            emr_record_id=record.id,
            audio_bytes=audio_bytes,
            filename=audio_file.filename or "audio.wav",
            content_type=audio_file.content_type or "application/octet-stream",
            language_code=language_code,
        )
    except Exception as exc:  # noqa: BLE001 -- surfaced to caller, record stays in 'draft'
        raise HTTPException(status_code=502, detail=f"Processing pipeline failed: {exc}") from exc

    return EMRIngestResponse(emr_record_id=record.id, status="pending_review")


@router.post(
    "/records/voice-intake",
    response_model=VoiceIntakeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a patient and EMR record from one voice intake",
    description=(
        "Transcribes a nurse or doctor's recording, extracts patient demographics and "
        "the clinical note, safely creates or matches the patient, and stores the encounter "
        "and EMR as one tenant-scoped transaction."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Unsupported language or invalid audio."},
        401: {"model": ErrorResponse, "description": "Missing or invalid bearer token."},
        403: {"model": ErrorResponse, "description": "Missing `emr:create` permission."},
        413: {"model": ErrorResponse, "description": "Audio file exceeds the upload limit."},
        422: {"model": ErrorResponse, "description": "Required patient details were not spoken."},
        502: {"model": ErrorResponse, "description": "Speech or AI processing failed."},
    },
    operation_id="create_voice_patient_record",
)
async def create_voice_intake(
    request: Request,
    language_code: str = Form("unknown"),
    department: str | None = Form(default=None),
    audio_file: UploadFile = File(..., description="Patient intake and clinical dictation."),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    if language_code not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported language_code. Allowed: {', '.join(SUPPORTED_LANGUAGES)}",
        )

    audio_bytes = await audio_file.read(MAX_AUDIO_BYTES + 1)
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Audio file is empty")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Audio file exceeds the 25 MB limit.",
        )

    try:
        transcription = await sarvam_service.transcribe_and_translate(
            audio_bytes=audio_bytes,
            filename=audio_file.filename or "voice-intake.webm",
            content_type=audio_file.content_type or "application/octet-stream",
            language_code=language_code,
        )
        intake = await patient_builder_service.extract_voice_intake(
            transcription["translated_text"]
        )
    except patient_builder_service.PatientDetailsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001 -- external pipeline errors become a stable API error
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Voice processing failed: {exc}",
        ) from exc

    try:
        patient, encounter, patient_created = (
            await patient_builder_service.build_patient_and_encounter(
                db,
                intake=intake,
                current_user=current_user,
                department_override=department,
            )
        )
        record = EMRRecord(
            hospital_id=encounter.hospital_id,
            encounter_id=encounter.id,
            source_language=language_code,
            raw_transcript=transcription["raw_transcript"],
            translated_text=transcription["translated_text"],
            structured_note=intake["clinical_note"],
            created_by=uuid.UUID(current_user.user_id),
            status="pending_review",
        )
        db.add(record)
        await db.flush()
        await emr_service.add_code_suggestions(
            db, record=record, structured_note=intake["clinical_note"]
        )
        await db.commit()
        await db.refresh(patient)
        await db.refresh(record)
    except patient_builder_service.PatientDetailsError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except Exception:
        await db.rollback()
        raise

    await audit_service.log_event(
        db,
        hospital_id=current_user.hospital_id,
        user_id=current_user.user_id,
        action="voice_intake.create",
        resource_type="emr_record",
        resource_id=record.id,
        ip_address=request.client.host if request.client else None,
    )

    return VoiceIntakeResponse(
        emr_record_id=record.id,
        encounter_id=encounter.id,
        patient=VoicePatientDetails(
            id=patient.id,
            full_name=patient.full_name,
            patient_reference=patient.abha_id or str(patient.id)[:8].upper(),
            age=patient_builder_service.patient_age(patient),
            gender=patient.gender,
            phone=patient.phone,
            created=patient_created,
        ),
        raw_transcript=transcription["raw_transcript"],
        translated_text=transcription["translated_text"],
        status="pending_review",
    )


@router.post("/voice-jobs", response_model=VoiceJobUploadResponse, status_code=201)
async def create_voice_job(
    payload: VoiceJobCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    from app.services import voice_job_service

    return await voice_job_service.create_job(
        db, payload=payload, current_user=current_user
    )


@router.post("/voice-jobs/{job_id}/complete", response_model=VoiceJobSummary)
async def complete_voice_job(
    job_id: uuid.UUID,
    payload: VoiceJobCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    from app.services import voice_job_service

    job = await voice_job_service.complete_job(
        db, job_id=job_id, etag=payload.etag, current_user=current_user
    )
    return await voice_job_service.summarize_job(db, job=job)


@router.get("/voice-jobs", response_model=list[VoiceJobSummary])
async def voice_jobs(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:read")),
):
    from app.services import voice_job_service

    return await voice_job_service.list_jobs(db, current_user=current_user)


@router.get(
    "/records/{record_id}",
    response_model=EMRRecordDetail,
    summary="Get an EMR record",
    description=(
        "Returns transcripts, the structured SOAP note, and coding suggestions for a record. "
        "Requires the `emr:read` permission and same-hospital tenancy."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid bearer token."},
        403: {"model": ErrorResponse, "description": "Permission or tenant check failed."},
        404: {"model": ErrorResponse, "description": "Record not found."},
    },
    operation_id="get_emr_record",
)
async def get_record(
    record_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:read")),
):
    result = await db.execute(select(EMRRecord).where(EMRRecord.id == record_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    assert_same_hospital(current_user, str(record.hospital_id))

    codes_result = await db.execute(
        select(EMRRecordCode, MedicalCode)
        .join(MedicalCode, EMRRecordCode.medical_code_id == MedicalCode.id)
        .where(EMRRecordCode.emr_record_id == record.id)
    )
    suggestions = [
        CodeSuggestion(
            system=code.system,
            code=code.code,
            display_term=code.display_term,
            confidence_score=float(link.confidence_score),
        )
        for link, code in codes_result.all()
    ]

    await audit_service.log_event(
        db,
        hospital_id=current_user.hospital_id,
        user_id=current_user.user_id,
        action="emr_record.read",
        resource_type="emr_record",
        resource_id=record.id,
        ip_address=request.client.host if request.client else None,
    )

    return EMRRecordDetail(
        id=record.id,
        encounter_id=record.encounter_id,
        source_language=record.source_language,
        raw_transcript=record.raw_transcript,
        translated_text=record.translated_text,
        structured_note=record.structured_note,
        suggested_codes=suggestions,
        status=record.status,
    )


@router.post(
    "/records/{record_id}/review",
    response_model=EMRRecordDetail,
    summary="Review and approve an EMR record",
    description=(
        "Lets a doctor correct the SOAP note and confirm suggested codes, then marks the record "
        "approved. Requires the `emr:review` permission and same-hospital tenancy."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid bearer token."},
        403: {"model": ErrorResponse, "description": "Permission or tenant check failed."},
        404: {"model": ErrorResponse, "description": "Record not found."},
        409: {"model": ErrorResponse, "description": "Record is not pending review."},
        422: {"model": ErrorResponse, "description": "Invalid request or code-selection ID."},
    },
    operation_id="review_emr_record",
)
async def review_record(
    record_id: uuid.UUID,
    payload: EMRReviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:review")),
):
    """Doctor confirms/edits the note and confirms which codes to keep.
    This is the human-in-the-loop gate before anything is considered final."""
    result = await db.execute(select(EMRRecord).where(EMRRecord.id == record_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    assert_same_hospital(current_user, str(record.hospital_id))

    if record.status != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only pending_review records can be approved; current status is {record.status}",
        )

    if payload.edited_structured_note is not None:
        record.structured_note = payload.edited_structured_note.model_dump()

    if payload.confirmed_code_ids:
        codes_result = await db.execute(
            select(EMRRecordCode).where(
                EMRRecordCode.emr_record_id == record.id,
                EMRRecordCode.id.in_(payload.confirmed_code_ids),
            )
        )
        links = codes_result.scalars().all()
        found_ids = {link.id for link in links}
        missing_ids = set(payload.confirmed_code_ids) - found_ids
        if missing_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="One or more confirmed_code_ids do not belong to this record",
            )
        for link in links:
            link.confirmed_by_doctor = True

    record.status = "approved"
    record.reviewed_by = current_user.user_id
    from datetime import datetime, timezone

    record.reviewed_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(record)

    await audit_service.log_event(
        db,
        hospital_id=current_user.hospital_id,
        user_id=current_user.user_id,
        action="emr_record.approve",
        resource_type="emr_record",
        resource_id=record.id,
        ip_address=request.client.host if request.client else None,
    )

    return await get_record(record_id, request, db, current_user)
