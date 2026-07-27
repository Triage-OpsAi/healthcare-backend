import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.celery_app import celery_app
from app.core.config import settings
from app.db.models import (
    EMRRecord,
    Encounter,
    Patient,
    PatientMedication,
    PatientReport,
    PatientSectionReview,
    User,
    VoiceIntakeJob,
)
from app.schemas.patient_chart import (
    AdditionalRecordCreateRequest,
    AudioAccess,
    MedicationCreateRequest,
    PatientDetailsResponse,
    PatientDetailsUpdateRequest,
    PatientChart,
    PatientMedicationSummary,
    PatientRecordSummary,
    PatientReportSummary,
    PatientSectionItemUpdateRequest,
    PatientSectionReviewSummary,
    PatientSectionUpdateRequest,
    ReportCreateRequest,
    ReportUploadResponse,
)
from app.services import (
    audit_events,
    audit_service,
    clinical_file_storage_service,
    discharge_pipeline_service,
    handover_pipeline_service,
)

SECTION_KEYS = (
    "summary",
    "timeline",
    "clinical",
    "medications",
    "diagnoses",
    "reports",
    "documents",
    "handover",
)


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


def _report_summary(report: PatientReport) -> PatientReportSummary:
    return PatientReportSummary(
        id=report.id,
        title=report.title,
        content_type=report.content_type,
        capture_source=report.capture_source,
        status=report.status,
        document_type=report.document_type,
        summary=report.summary,
        key_findings=report.key_findings or [],
        extracted_details=report.extracted_details,
        quality_message=report.quality_message,
        created_at=report.created_at,
    )


def _encounter_summary(note: dict | None) -> str:
    if not note:
        return "Clinical encounter captured; structured documentation is being prepared."
    complaint = str(note.get("chief_complaint") or "").strip()
    assessment = str(note.get("assessment") or "").strip()
    plan = str(note.get("plan") or "").strip()
    parts = [value for value in (complaint, assessment, plan) if value]
    if not parts:
        return "Clinical encounter captured with no summary documented."
    return " · ".join(parts[:3])


async def get_chart(
    db: AsyncSession, *, patient_id: uuid.UUID, current_user: CurrentUser
) -> PatientChart:
    patient = await _patient(db, patient_id, current_user)
    record_rows = (
        await db.execute(
            select(EMRRecord, Encounter, User)
            .join(Encounter, Encounter.id == EMRRecord.encounter_id)
            .join(User, User.id == EMRRecord.created_by)
            .where(
                Encounter.patient_id == patient.id,
                EMRRecord.hospital_id == patient.hospital_id,
            )
            .order_by(EMRRecord.created_at.desc())
        )
    ).all()
    reports = (
        await db.execute(
            select(PatientReport)
            .where(PatientReport.patient_id == patient.id)
            .order_by(PatientReport.created_at.desc())
        )
    ).scalars().all()
    medications = (
        await db.execute(
            select(PatientMedication)
            .where(PatientMedication.patient_id == patient.id)
            .order_by(PatientMedication.created_at.desc())
        )
    ).scalars().all()
    section_reviews = (
        await db.execute(
            select(PatientSectionReview)
            .where(PatientSectionReview.patient_id == patient.id)
            .order_by(PatientSectionReview.section_key)
        )
    ).scalars().all()
    user_ids = {
        value
        for review in section_reviews
        for value in (review.approved_by, review.updated_by)
        if value is not None
    }
    users = {
        user.id: user.full_name
        for user in (
            (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
            if user_ids
            else []
        )
    }
    review_summaries = [
        PatientSectionReviewSummary(
            section_key=review.section_key,
            content_override=review.content_override,
            item_overrides=review.item_overrides or {},
            deleted_items=review.deleted_items or [],
            is_deleted=review.is_deleted,
            is_approved=review.is_approved,
            approved_by=users.get(review.approved_by) if review.approved_by else None,
            approved_at=review.approved_at,
            updated_by=users.get(review.updated_by, "Clinical user"),
            updated_at=review.updated_at,
        )
        for review in section_reviews
    ]
    return PatientChart(
        records=[
            PatientRecordSummary(
                id=record.id,
                encounter_id=encounter.id,
                department=encounter.department,
                status=record.status,
                source_language=record.source_language,
                structured_note=record.structured_note,
                encounter_summary=_encounter_summary(record.structured_note),
                captured_by=creator.full_name,
                audio_available=bool(record.audio_storage_url),
                created_at=record.created_at,
            )
            for record, encounter, creator in record_rows
        ],
        reports=[_report_summary(report) for report in reports],
        medications=[
            PatientMedicationSummary.model_validate(
                medication, from_attributes=True
            )
            for medication in medications
        ],
        discharge_summaries=await discharge_pipeline_service.list_for_patient(
            db, patient_id=patient.id
        ),
        handovers=await handover_pipeline_service.list_for_patient(
            db, patient_id=patient.id
        ),
        section_reviews=review_summaries,
        approval_percentage=round(
            100
            * sum(1 for review in section_reviews if review.is_approved)
            / len(SECTION_KEYS)
        ),
    )


async def _section_review(
    db: AsyncSession,
    *,
    patient: Patient,
    section_key: str,
    current_user: CurrentUser,
) -> PatientSectionReview:
    review = (
        await db.execute(
            select(PatientSectionReview).where(
                PatientSectionReview.patient_id == patient.id,
                PatientSectionReview.section_key == section_key,
            )
        )
    ).scalar_one_or_none()
    if review is None:
        review = PatientSectionReview(
            hospital_id=patient.hospital_id,
            patient_id=patient.id,
            section_key=section_key,
            updated_by=uuid.UUID(current_user.user_id),
        )
        db.add(review)
        await db.flush()
    return review


async def _section_summary(
    db: AsyncSession, review: PatientSectionReview
) -> PatientSectionReviewSummary:
    editor = await db.get(User, review.updated_by)
    approver = await db.get(User, review.approved_by) if review.approved_by else None
    return PatientSectionReviewSummary(
        section_key=review.section_key,
        content_override=review.content_override,
        item_overrides=review.item_overrides or {},
        deleted_items=review.deleted_items or [],
        is_deleted=review.is_deleted,
        is_approved=review.is_approved,
        approved_by=approver.full_name if approver else None,
        approved_at=review.approved_at,
        updated_by=editor.full_name if editor else "Clinical user",
        updated_at=review.updated_at,
    )


async def edit_section(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    section_key: str,
    payload: PatientSectionUpdateRequest,
    current_user: CurrentUser,
) -> PatientSectionReviewSummary:
    patient = await _patient(db, patient_id, current_user)
    review = await _section_review(
        db, patient=patient, section_key=section_key, current_user=current_user
    )
    before = {
        "content_override": review.content_override,
        "is_deleted": review.is_deleted,
    }
    review.content_override = payload.content_override.strip() or None
    review.is_deleted = False
    review.updated_by = uuid.UUID(current_user.user_id)
    await db.commit()
    await db.refresh(review)
    await audit_service.safe_log_event(
        hospital_id=patient.hospital_id,
        user_id=current_user.user_id,
        actor_role=current_user.role,
        action="patient_section.edit_made",
        resource_type="patient_section",
        resource_id=review.id,
        patient_id=patient.id,
        changes={
            "before": before,
            "after": {
                "content_override": review.content_override,
                "is_deleted": False,
            },
        },
        event_metadata={"section_key": section_key},
    )
    return await _section_summary(db, review)


def _validate_item_key(item_key: str) -> str:
    normalized = item_key.strip().lower()
    if (
        not normalized
        or len(normalized) > 80
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in normalized)
    ):
        raise HTTPException(status_code=422, detail="Invalid section item")
    return normalized


async def edit_section_item(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    section_key: str,
    item_key: str,
    payload: PatientSectionItemUpdateRequest,
    current_user: CurrentUser,
) -> PatientSectionReviewSummary:
    patient = await _patient(db, patient_id, current_user)
    review = await _section_review(
        db, patient=patient, section_key=section_key, current_user=current_user
    )
    key = _validate_item_key(item_key)
    overrides = dict(review.item_overrides or {})
    before = overrides.get(key)
    overrides[key] = payload.content_override.strip()
    review.item_overrides = overrides
    review.deleted_items = [
        value for value in (review.deleted_items or []) if value != key
    ]
    review.is_deleted = False
    review.updated_by = uuid.UUID(current_user.user_id)
    await db.commit()
    await db.refresh(review)
    await audit_service.safe_log_event(
        hospital_id=patient.hospital_id,
        user_id=current_user.user_id,
        actor_role=current_user.role,
        action="patient_section_item.edit_made",
        resource_type="patient_section",
        resource_id=review.id,
        patient_id=patient.id,
        changes={
            "content": {
                "before": before,
                "after": overrides[key],
            }
        },
        event_metadata={"section_key": section_key, "item_key": key},
    )
    return await _section_summary(db, review)


async def delete_section_item(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    section_key: str,
    item_key: str,
    current_user: CurrentUser,
) -> PatientSectionReviewSummary:
    patient = await _patient(db, patient_id, current_user)
    review = await _section_review(
        db, patient=patient, section_key=section_key, current_user=current_user
    )
    if review.is_approved:
        raise HTTPException(status_code=409, detail="Approved items cannot be deleted")
    key = _validate_item_key(item_key)
    deleted_items = list(review.deleted_items or [])
    if key not in deleted_items:
        deleted_items.append(key)
    review.deleted_items = deleted_items
    review.updated_by = uuid.UUID(current_user.user_id)
    await db.commit()
    await db.refresh(review)
    await audit_service.safe_log_event(
        hospital_id=patient.hospital_id,
        user_id=current_user.user_id,
        actor_role=current_user.role,
        action="patient_section_item.deleted",
        resource_type="patient_section",
        resource_id=review.id,
        patient_id=patient.id,
        changes={"is_deleted": {"before": False, "after": True}},
        event_metadata={"section_key": section_key, "item_key": key},
    )
    return await _section_summary(db, review)


async def approve_section(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    section_key: str,
    current_user: CurrentUser,
) -> PatientSectionReviewSummary:
    patient = await _patient(db, patient_id, current_user)
    review = await _section_review(
        db, patient=patient, section_key=section_key, current_user=current_user
    )
    if review.is_deleted:
        raise HTTPException(status_code=409, detail="Restore the section before approval")
    was_approved = review.is_approved
    review.is_approved = True
    review.approved_by = uuid.UUID(current_user.user_id)
    review.approved_at = datetime.now(timezone.utc)
    review.updated_by = uuid.UUID(current_user.user_id)
    await db.commit()
    await db.refresh(review)
    await audit_service.safe_log_event(
        hospital_id=patient.hospital_id,
        user_id=current_user.user_id,
        actor_role=current_user.role,
        action="patient_section.approved",
        resource_type="patient_section",
        resource_id=review.id,
        patient_id=patient.id,
        changes={"is_approved": {"before": was_approved, "after": True}},
        event_metadata={"section_key": section_key},
    )
    return await _section_summary(db, review)


async def delete_section(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    section_key: str,
    current_user: CurrentUser,
) -> PatientSectionReviewSummary:
    patient = await _patient(db, patient_id, current_user)
    review = await _section_review(
        db, patient=patient, section_key=section_key, current_user=current_user
    )
    if review.is_approved:
        raise HTTPException(status_code=409, detail="Approved sections cannot be deleted")
    was_deleted = review.is_deleted
    review.is_deleted = True
    review.updated_by = uuid.UUID(current_user.user_id)
    await db.commit()
    await db.refresh(review)
    await audit_service.safe_log_event(
        hospital_id=patient.hospital_id,
        user_id=current_user.user_id,
        actor_role=current_user.role,
        action="patient_section.deleted",
        resource_type="patient_section",
        resource_id=review.id,
        patient_id=patient.id,
        changes={"is_deleted": {"before": was_deleted, "after": True}},
        event_metadata={"section_key": section_key},
    )
    return await _section_summary(db, review)


async def audio_access(
    db: AsyncSession, *, record_id: uuid.UUID, current_user: CurrentUser
) -> AudioAccess:
    record = await db.get(EMRRecord, record_id)
    if record is None or record.hospital_id != _hospital_id(current_user):
        raise HTTPException(status_code=404, detail="EMR record not found")
    if not record.audio_storage_url or not record.audio_storage_url.startswith("b2://"):
        raise HTTPException(status_code=404, detail="No recording is attached to this EMR")
    _scheme, _separator, location = record.audio_storage_url.partition("://")
    _bucket, _slash, object_key = location.partition("/")
    if not object_key:
        raise HTTPException(status_code=404, detail="Recording object is unavailable")
    url = await clinical_file_storage_service.signed_download(object_key=object_key)
    content_type = (
        await db.execute(
            select(VoiceIntakeJob.content_type)
            .where(VoiceIntakeJob.emr_record_id == record.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    return AudioAccess(
        url=url,
        content_type=content_type or "audio/webm",
        expires_in=settings.B2_PRESIGN_EXPIRE_SECONDS,
    )


async def update_patient_details(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    payload: PatientDetailsUpdateRequest,
    current_user: CurrentUser,
) -> PatientDetailsResponse:
    patient = await _patient(db, patient_id, current_user)
    before = {
        "full_name": patient.full_name,
        "phone": patient.phone,
        "gender": patient.gender,
    }
    patient.full_name = payload.full_name.strip()
    patient.phone = payload.phone.strip() if payload.phone else None
    patient.gender = payload.gender.strip() if payload.gender else None
    if "date_of_birth" in payload.model_fields_set:
        patient.date_of_birth = payload.date_of_birth
    elif "age" in payload.model_fields_set:
        patient.date_of_birth = (
            datetime(date.today().year - payload.age, 1, 1, tzinfo=timezone.utc)
            if payload.age is not None
            else None
        )
    encounter = (
        await db.execute(
            select(Encounter)
            .where(
                Encounter.patient_id == patient.id,
                Encounter.hospital_id == patient.hospital_id,
            )
            .order_by(Encounter.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if encounter is None:
        encounter = Encounter(
            hospital_id=patient.hospital_id,
            patient_id=patient.id,
            doctor_id=uuid.UUID(current_user.user_id),
            status="open",
        )
        db.add(encounter)
    encounter.encounter_number = payload.encounter_number.strip() if payload.encounter_number else None
    encounter.ward_number = payload.ward_number.strip() if payload.ward_number else None
    encounter.bed_number = payload.bed_number.strip() if payload.bed_number else None
    await db.commit()
    await audit_service.safe_log_event(
        hospital_id=patient.hospital_id,
        user_id=current_user.user_id,
        actor_role=current_user.role,
        action="edit.made",
        resource_type="patient",
        resource_id=patient.id,
        patient_id=patient.id,
        encounter_id=encounter.id,
        changes={
            "before": before,
            "after": {
                "full_name": patient.full_name,
                "phone": patient.phone,
                "gender": patient.gender,
                "encounter_number": encounter.encounter_number,
                "ward_number": encounter.ward_number,
                "bed_number": encounter.bed_number,
            },
        },
    )
    return PatientDetailsResponse(
        id=patient.id,
        full_name=patient.full_name,
        age=(
            date.today().year
            - patient.date_of_birth.date().year
            - (
                (date.today().month, date.today().day)
                < (patient.date_of_birth.date().month, patient.date_of_birth.date().day)
            )
            if patient.date_of_birth
            else None
        ),
        phone=patient.phone,
        gender=patient.gender,
        date_of_birth=patient.date_of_birth,
        encounter_number=encounter.encounter_number,
        ward_number=encounter.ward_number,
        bed_number=encounter.bed_number,
    )


async def report_access(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
    current_user: CurrentUser,
) -> AudioAccess:
    patient = await _patient(db, patient_id, current_user)
    report = await db.get(PatientReport, report_id)
    if (
        report is None
        or report.patient_id != patient.id
        or report.hospital_id != patient.hospital_id
    ):
        raise HTTPException(status_code=404, detail="Report not found")
    return AudioAccess(
        url=await clinical_file_storage_service.signed_download(object_key=report.object_key),
        content_type=report.content_type,
        expires_in=settings.B2_PRESIGN_EXPIRE_SECONDS,
    )


async def create_report(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    payload: ReportCreateRequest,
    current_user: CurrentUser,
) -> ReportUploadResponse:
    patient = await _patient(db, patient_id, current_user)
    content_type = clinical_file_storage_service.normalize_content_type(payload.content_type)
    extension = clinical_file_storage_service.report_extension(content_type)
    if extension is None:
        raise HTTPException(
            status_code=400,
            detail="Upload a PDF, Word document, JPEG, PNG, or WEBP clinical report.",
        )
    report_id = uuid.uuid4()
    object_key = clinical_file_storage_service.report_key(
        hospital_id=patient.hospital_id,
        patient_id=patient.id,
        file_id=report_id,
        extension=extension,
    )
    report = PatientReport(
        id=report_id,
        hospital_id=patient.hospital_id,
        patient_id=patient.id,
        uploaded_by=uuid.UUID(current_user.user_id),
        title=payload.title.strip(),
        bucket_name=clinical_file_storage_service.bucket_name(),
        object_key=object_key,
        content_type=content_type,
        file_size=payload.file_size,
        capture_source=payload.capture_source,
        status="awaiting_upload",
    )
    upload_url = await clinical_file_storage_service.direct_upload(
        object_key=object_key, content_type=content_type
    )
    db.add(report)
    await db.commit()
    return ReportUploadResponse(
        report_id=report.id,
        upload_url=upload_url,
        content_type=content_type,
        expires_in=settings.B2_PRESIGN_EXPIRE_SECONDS,
    )


async def complete_report(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
    etag: str | None,
    current_user: CurrentUser,
) -> PatientReportSummary:
    await _patient(db, patient_id, current_user)
    report = await db.get(PatientReport, report_id)
    if report is None or report.patient_id != patient_id:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status != "awaiting_upload":
        return _report_summary(report)
    try:
        metadata = await clinical_file_storage_service.verify(
            object_key=report.object_key, expected_size=report.file_size
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    report.etag = (etag or metadata.get("ETag") or "").strip('"') or None
    report.status = "queued"
    await db.commit()
    try:
        celery_app.send_task(
            "report.summarize", args=[str(report.id)], queue="patient_emr"
        )
    except Exception as exc:
        report.status = "failed"
        report.quality_message = "Report processing queue is unavailable."
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report processing queue unavailable",
        ) from exc
    return _report_summary(report)


async def approve_report(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
    current_user: CurrentUser,
) -> PatientReportSummary:
    patient = await _patient(db, patient_id, current_user)
    report = await db.get(PatientReport, report_id)
    if (
        report is None
        or report.patient_id != patient.id
        or report.hospital_id != patient.hospital_id
    ):
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status == "approved":
        return _report_summary(report)
    if report.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only ready report summaries can be approved; current status is {report.status}",
        )
    report.status = "approved"
    await db.commit()
    await db.refresh(report)
    return _report_summary(report)


async def add_medication(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    payload: MedicationCreateRequest,
    current_user: CurrentUser,
) -> PatientMedicationSummary:
    patient = await _patient(db, patient_id, current_user)
    medication = PatientMedication(
        hospital_id=patient.hospital_id,
        patient_id=patient.id,
        prescribed_by=uuid.UUID(current_user.user_id),
        **payload.model_dump(),
    )
    db.add(medication)
    await db.commit()
    await db.refresh(medication)
    return PatientMedicationSummary.model_validate(medication, from_attributes=True)


async def add_record(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    payload: AdditionalRecordCreateRequest,
    current_user: CurrentUser,
) -> PatientRecordSummary:
    patient = await _patient(db, patient_id, current_user)
    encounter = Encounter(
        hospital_id=patient.hospital_id,
        patient_id=patient.id,
        doctor_id=uuid.UUID(current_user.user_id),
        department=payload.department,
        status="open",
    )
    db.add(encounter)
    await db.flush()
    record = EMRRecord(
        hospital_id=patient.hospital_id,
        encounter_id=encounter.id,
        source_language="en-IN",
        structured_note={
            "chief_complaint": payload.title,
            "subjective": payload.subjective,
            "objective": payload.objective,
            "assessment": payload.assessment,
            "plan": payload.plan,
            "diagnoses": [],
            "symptoms": [],
            "medications": [],
        },
        status="pending_review",
        created_by=uuid.UUID(current_user.user_id),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    creator = await db.get(User, uuid.UUID(current_user.user_id))
    await audit_service.safe_log_event(
        hospital_id=patient.hospital_id,
        user_id=current_user.user_id,
        actor_role=current_user.role,
        action=audit_events.NOTE_DRAFT_CREATED,
        resource_type="emr_record",
        resource_id=record.id,
        patient_id=patient.id,
        encounter_id=encounter.id,
    )
    return PatientRecordSummary(
        id=record.id,
        encounter_id=encounter.id,
        department=encounter.department,
        status=record.status,
        source_language=record.source_language,
        structured_note=record.structured_note,
        encounter_summary=_encounter_summary(record.structured_note),
        captured_by=creator.full_name if creator else "Clinical user",
        audio_available=False,
        created_at=record.created_at,
    )
