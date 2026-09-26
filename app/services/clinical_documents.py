import hashlib
import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.db.clinical_documents import ClinicalAttestation, Department, PatientConsent, UserDepartment
from app.db.models import AuditLog, PatientVisit, User


def revision(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def section_snapshot(chart, section, patient_id, visit_id=None):
    data = chart.model_dump(mode="json")
    fields = {
        "summary": ["records"], "clinical": ["records"], "diagnoses": ["records"],
        "timeline": ["visits", "records"], "medications": ["medications"],
        "reports": ["reports"], "documents": ["discharge_summaries"], "handover": ["handovers"],
    }
    if section not in fields:
        raise HTTPException(422, "Unknown clinical section")
    review = next((r for r in data["section_reviews"] if r["section_key"] == section), {})
    return {
        "patient_id": str(patient_id), "visit_id": str(visit_id) if visit_id else None,
        "section": section, "content": {key: data[key] for key in fields[section]},
        "review": {key: review.get(key, default) for key, default in (
            ("content_override", None), ("item_overrides", {}), ("deleted_items", []), ("is_deleted", False)
        )},
    }


def audit(db, user, action, resource_type, resource_id, patient_id=None, metadata=None):
    db.add(AuditLog(
        hospital_id=uuid.UUID(user.hospital_id), user_id=uuid.UUID(user.user_id),
        actor_role=user.role, action=action, resource_type=resource_type,
        resource_id=resource_id, patient_id=patient_id, event_metadata=metadata or {},
    ))


async def department(db, department_id, hospital_id):
    row = await db.get(Department, department_id)
    if row is None or row.hospital_id != hospital_id or not row.is_active:
        raise HTTPException(404, "Department not found in this workspace")
    return row


async def signer(db, user):
    row = await db.get(User, uuid.UUID(user.user_id))
    if row is None or not row.is_active or str(row.hospital_id) != user.hospital_id:
        raise HTTPException(403, "Active workspace membership required")
    return row


async def signed_approve(db, patient_id, section, payload, user):
    from app.services import patient_chart_service as charts
    patient = await charts._patient(db, patient_id, user)
    # Serialize approvals for the same patient and section.
    from sqlalchemy import func
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"signature:{patient_id}:{section}"))))
    chart = await charts.get_chart(db, patient_id=patient_id, visit_id=payload.visit_id, current_user=user)
    snapshot = section_snapshot(chart, section, patient_id, payload.visit_id)
    if snapshot["review"]["is_deleted"]:
        raise HTTPException(409, "Restore the section before signing")
    if payload.revision != revision(snapshot):
        raise HTTPException(409, "The record changed. Review the latest version and sign again.")
    actor = await signer(db, user)
    attestation = ClinicalAttestation(
        hospital_id=patient.hospital_id, patient_id=patient.id, visit_id=payload.visit_id,
        section_key=section, signer_id=actor.id, signer_name=actor.full_name, signer_role=user.role,
        signature=payload.signature.model_dump(), snapshot=snapshot, revision=payload.revision,
    )
    db.add(attestation)
    review = await charts._section_review(db, patient=patient, section_key=section, current_user=user)
    review.is_approved = True
    review.approved_by = actor.id
    review.approved_at = datetime.now(timezone.utc)
    review.updated_by = actor.id
    await db.flush()
    audit(db, user, "patient_section.signed", "clinical_attestation", attestation.id, patient.id,
          {"revision": payload.revision, "section": section, "visit_id": str(payload.visit_id) if payload.visit_id else None})
    await db.commit()
    await db.refresh(review)
    return await charts._section_summary(db, review)


def attestation_json(row):
    return {"id": str(row.id), "section_key": row.section_key, "visit_id": str(row.visit_id) if row.visit_id else None,
            "signer_name": row.signer_name, "signer_role": row.signer_role, "signature": row.signature,
            "signed_at": row.signed_at.isoformat(), "revision": row.revision, "snapshot": row.snapshot}


def consent_json(row):
    return {"id": str(row.id), "department_name": row.department_name,
            "clinician_name": row.clinician_name, "form": row.form, "revision": row.revision,
            "signed_at": row.signed_at.isoformat(), "withdrawn_at": row.withdrawn_at.isoformat() if row.withdrawn_at else None,
            "withdrawal_reason": row.withdrawal_reason}


async def attestations(db, patient_id, hospital_id):
    return (await db.scalars(select(ClinicalAttestation).where(
        ClinicalAttestation.patient_id == patient_id, ClinicalAttestation.hospital_id == hospital_id
    ).order_by(ClinicalAttestation.signed_at.desc(), ClinicalAttestation.id))).all()


async def validate_chart_approvals(db, chart, patient_id, hospital_id, visit_id):
    signed = await attestations(db, patient_id, hospital_id)
    for review in chart.section_reviews:
        digest = revision(section_snapshot(chart, review.section_key, patient_id, visit_id))
        latest = next((row for row in signed if row.section_key == review.section_key and row.visit_id == visit_id), None)
        review.is_approved = bool(latest and latest.revision == digest and not review.is_deleted)
        review.approved_by = latest.signer_name if review.is_approved else None
        review.approved_at = latest.signed_at if review.is_approved else None
    chart.approval_percentage = round(100 * sum(r.is_approved for r in chart.section_reviews) / 8)
    return chart
