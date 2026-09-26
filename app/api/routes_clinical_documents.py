import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.db.clinical_documents import Department, UserDepartment, PatientConsent
from app.db.models import EMRRecord, EMRRecordCode, MedicalCode, Encounter, PatientVisit, Role, User
from app.schemas.clinical_documents import ConsentForm, ConsentWithdrawal, DepartmentAssignment, DepartmentCreate, SpeechRequest
from app.schemas.patient_chart import PatientSectionKey
from app.services import clinical_documents as documents, patient_chart_service as charts
from app.services.authorization import require_permission

router = APIRouter(tags=["Clinical Documents"])


@router.get("/doctor/departments")
async def list_departments(db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    hospital_id = charts._hospital_id(user)
    rows = (await db.scalars(select(Department).where(Department.hospital_id == hospital_id).order_by(Department.name))).all()
    return [{"id": row.id, "name": row.name, "is_active": row.is_active} for row in rows]


@router.post("/doctor/departments", status_code=201)
async def create_department(payload: DepartmentCreate, db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(require_permission("users:manage"))):
    hospital_id = charts._hospital_id(user)
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"departments:{hospital_id}"))))
    if await db.scalar(select(Department.id).where(Department.hospital_id == hospital_id, func.lower(Department.name) == payload.name.lower())):
        raise HTTPException(409, "This department already exists")
    row = Department(hospital_id=hospital_id, name=payload.name)
    db.add(row)
    await db.flush()
    documents.audit(db, user, "department.created", "department", row.id)
    await db.commit()
    return {"id": row.id, "name": row.name, "is_active": row.is_active}


@router.get("/doctor/department-members")
async def department_members(db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(require_permission("users:manage"))):
    hospital_id = charts._hospital_id(user)
    rows = (await db.execute(select(User, Role.name, UserDepartment.department_id).join(Role, Role.id == User.role_id)
        .outerjoin(UserDepartment, UserDepartment.user_id == User.id)
        .where(User.hospital_id == hospital_id).order_by(User.full_name))).all()
    return [{"id": row.id, "full_name": row.full_name, "role": role, "department_id": department_id}
            for row, role, department_id in rows]


@router.get("/doctor/my-department")
async def my_department(db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    hospital_id = charts._hospital_id(user)
    row = await db.get(UserDepartment, uuid.UUID(user.user_id))
    if row is None or row.hospital_id != hospital_id:
        return {"department_id": None}
    return {"department_id": row.department_id}


@router.put("/doctor/department-members/{user_id}")
async def assign_department(user_id: uuid.UUID, payload: DepartmentAssignment, db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(require_permission("users:manage"))):
    hospital_id = charts._hospital_id(user)
    member = await db.scalar(select(User).where(User.id == user_id, User.hospital_id == hospital_id).with_for_update())
    if member is None:
        raise HTTPException(404, "Workspace member not found")
    if payload.department_id:
        await documents.department(db, payload.department_id, hospital_id)
    assignment = await db.get(UserDepartment, user_id)
    if payload.department_id is None:
        if assignment:
            await db.delete(assignment)
    elif assignment:
        assignment.department_id = payload.department_id
        assignment.assigned_by = uuid.UUID(user.user_id)
    else:
        db.add(UserDepartment(user_id=user_id, hospital_id=hospital_id, department_id=payload.department_id, assigned_by=uuid.UUID(user.user_id)))
    documents.audit(db, user, "department.assigned", "user", user_id, metadata={"department_id": str(payload.department_id) if payload.department_id else None})
    await db.commit()
    return {"department_id": payload.department_id}


@router.get("/patients/{patient_id}/sections/{section_key}/signing-preview")
async def signing_preview(patient_id: uuid.UUID, section_key: PatientSectionKey, visit_id: uuid.UUID | None = None,
                          db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(require_permission("emr:review"))):
    chart = await charts.get_chart(db, patient_id=patient_id, visit_id=visit_id, current_user=user)
    snapshot = documents.section_snapshot(chart, section_key, patient_id, visit_id)
    return {"snapshot": snapshot, "revision": documents.revision(snapshot)}


@router.get("/patients/{patient_id}/consents")
async def list_consents(patient_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(require_permission("emr:read"))):
    patient = await charts._patient(db, patient_id, user)
    rows = (await db.scalars(select(PatientConsent).where(PatientConsent.patient_id == patient.id,
        PatientConsent.hospital_id == patient.hospital_id).order_by(PatientConsent.signed_at.desc()))).all()
    return [documents.consent_json(row) for row in rows]


@router.post("/patients/{patient_id}/consents", status_code=201)
async def create_consent(patient_id: uuid.UUID, payload: ConsentForm, db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(require_permission("emr:create"))):
    patient = await charts._patient(db, patient_id, user)
    department = await documents.department(db, payload.department_id, patient.hospital_id)
    actor = await documents.signer(db, user)
    if payload.visit_id:
        visit = await db.get(PatientVisit, payload.visit_id)
        if visit is None or visit.patient_id != patient_id or visit.hospital_id != patient.hospital_id:
            raise HTTPException(404, "Visit not found for this patient")
    form = payload.model_dump(mode="json")
    form["version"] = "1"
    form["patient_name"] = patient.full_name
    form["declaration"] = {
        "en": "You may ask questions, refuse, or withdraw consent. Signing does not guarantee a particular result. Please tell the care team if anything is unclear.",
        "hi": "आप प्रश्न पूछ सकते हैं, मना कर सकते हैं या सहमति वापस ले सकते हैं। हस्ताक्षर किसी विशेष परिणाम की गारंटी नहीं हैं। यदि कुछ स्पष्ट नहीं है, तो कृपया देखभाल टीम को बताएं।",
    }
    row = PatientConsent(hospital_id=patient.hospital_id, patient_id=patient_id, visit_id=payload.visit_id,
        department_id=department.id, department_name=department.name, recorded_by=actor.id,
        clinician_name=actor.full_name, form=form, revision=documents.revision(form))
    db.add(row)
    await db.flush()
    documents.audit(db, user, "patient_consent.signed", "patient_consent", row.id, patient_id,
                    {"revision": row.revision, "decision": payload.decision, "language": payload.language})
    await db.commit()
    await db.refresh(row)
    return documents.consent_json(row)


@router.post("/patients/{patient_id}/consents/{consent_id}/withdraw")
async def withdraw_consent(patient_id: uuid.UUID, consent_id: uuid.UUID, payload: ConsentWithdrawal,
                           db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(require_permission("emr:create"))):
    patient = await charts._patient(db, patient_id, user)
    row = await db.scalar(select(PatientConsent).where(PatientConsent.id == consent_id,
        PatientConsent.patient_id == patient_id, PatientConsent.hospital_id == patient.hospital_id).with_for_update())
    if row is None:
        raise HTTPException(404, "Consent not found")
    if row.withdrawn_at or row.form["decision"] != "accepted":
        raise HTTPException(409, "This consent is not active")
    row.withdrawn_at = datetime.now(timezone.utc)
    row.withdrawn_by = uuid.UUID(user.user_id)
    row.withdrawal_reason = payload.reason
    documents.audit(db, user, "patient_consent.withdrawn", "patient_consent", row.id, patient_id)
    await db.commit()
    return documents.consent_json(row)


@router.post("/patients/{patient_id}/consent-audio")
async def consent_audio(patient_id: uuid.UUID, payload: SpeechRequest, response: Response,
                        db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(require_permission("emr:create"))):
    await charts._patient(db, patient_id, user)
    response.headers["Cache-Control"] = "no-store"
    if not settings.SARVAM_API_KEY:
        raise HTTPException(503, "Consent audio is not configured. Please read the form aloud.")
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            result = await client.post(f"{settings.SARVAM_BASE_URL.rstrip('/')}/text-to-speech",
                headers={"api-subscription-key": settings.SARVAM_API_KEY},
                json={"text": payload.text, "language_code": f"{payload.language}-IN", "model": "bulbul:v3", "speaker": "shubh", "output_audio_codec": "wav"})
        result.raise_for_status()
        audios = result.json()["audios"]
        if not audios or not all(isinstance(audio, str) for audio in audios):
            raise ValueError("Missing audio")
        return {"audios": audios}
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(502, "Audio could not be generated. Retry or read the selected form aloud.") from None


@router.get("/patients/{patient_id}/complete-record")
async def complete_record(patient_id: uuid.UUID, response: Response, db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(require_permission("emr:read"))):
    response.headers["Cache-Control"] = "no-store"
    patient = await charts._patient(db, patient_id, user)
    chart = await charts.get_chart(db, patient_id=patient_id, current_user=user)
    signed = await documents.attestations(db, patient_id, patient.hospital_id)
    consents = await list_consents(patient_id, db, user)
    # All encounters, including those without a formal visit, are retained.
    records = (await db.scalars(select(EMRRecord).join(Encounter, Encounter.id == EMRRecord.encounter_id).where(
        Encounter.patient_id == patient_id, EMRRecord.hospital_id == patient.hospital_id).order_by(EMRRecord.created_at))).all()
    coded = (await db.execute(select(EMRRecordCode, MedicalCode).join(MedicalCode, MedicalCode.id == EMRRecordCode.medical_code_id)
        .join(EMRRecord, EMRRecord.id == EMRRecordCode.emr_record_id)
        .join(Encounter, Encounter.id == EMRRecord.encounter_id)
        .where(Encounter.patient_id == patient_id, EMRRecord.hospital_id == patient.hospital_id))).all()
    codes_by_record = {}
    for link, code in coded:
        codes_by_record.setdefault(link.emr_record_id, []).append({"system": code.system, "code": code.code,
            "description": code.display_term, "field": link.field_type, "confidence": link.confidence_score,
            "confirmed_by_doctor": link.confirmed_by_doctor})
    originals = [{"id": str(row.id), "raw_transcript": row.raw_transcript, "translated_text": row.translated_text,
                  "created_at": row.created_at, "codes": codes_by_record.get(row.id, [])} for row in records]
    from app.db import ward_voice_models as models
    ward_entries = {}
    # Patient-linked ward observations and intake/output entries are part of the record.
    for name in ("ExtractedObservation", "FluidEntry", "IVInfusion", "ChartClosure", "WardTask", "VoiceCapture"):
        model = getattr(models, name, None)
        if model is not None and hasattr(model, "patient_id"):
            rows = (await db.scalars(select(model).where(model.patient_id == patient_id, model.hospital_id == patient.hospital_id))).all()
            ward_entries[name] = [{column.name: getattr(row, column.name) for column in model.__table__.columns
                                  if column.name not in {"hospital_id", "bucket_name", "object_key"}} for row in rows]
    sections = []
    for key in charts.SECTION_KEYS:
        snapshot = documents.section_snapshot(chart, key, patient_id)
        digest = documents.revision(snapshot)
        latest = next((row for row in signed if row.section_key == key and row.visit_id is None), None)
        signature = documents.attestation_json(latest) if latest and latest.revision == digest else None
        sections.append({"key": key, "snapshot": snapshot, "attestation": signature})
    documents.audit(db, user, "patient_record.viewed", "patient", patient_id, patient_id)
    await db.commit()
    return jsonable_encoder({"patient": {"id": patient.id, "name": patient.full_name, "date_of_birth": patient.date_of_birth,
        "gender": patient.gender, "phone": patient.phone}, "chart": chart, "original_entries": originals,
        "ward_entries": ward_entries, "consents": consents, "sections": sections, "attestations": [documents.attestation_json(row) for row in signed],
        "generated_at": datetime.now(timezone.utc)})
