"""Failure-isolated EMR sync state transition with canonical audit events."""

import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.db.models import EMRRecord, Encounter
from app.services import audit_events, audit_service


async def sync_record(
    db: AsyncSession,
    *,
    record_id: uuid.UUID,
    current_user: CurrentUser,
) -> EMRRecord:
    record = await db.get(EMRRecord, record_id)
    encounter = await db.get(Encounter, record.encounter_id) if record else None
    context = {
        "hospital_id": current_user.hospital_id,
        "user_id": current_user.user_id,
        "actor_role": current_user.role,
        "resource_type": "emr_record",
        "resource_id": record_id,
        "patient_id": encounter.patient_id if encounter else None,
        "encounter_id": record.encounter_id if record else None,
    }
    await audit_service.safe_log_event(
        action=audit_events.EMR_SYNC_ATTEMPTED,
        outcome="queued",
        event_metadata={"target": "hospital_emr"},
        **context,
    )

    if record is None:
        await audit_service.safe_log_event(
            action=audit_events.EMR_SYNC_FAILURE,
            outcome="failure",
            event_metadata={"reason": "record_not_found", "target": "hospital_emr"},
            **context,
        )
        raise HTTPException(status_code=404, detail="Record not found")
    if str(record.hospital_id) != str(current_user.hospital_id):
        await audit_service.safe_log_event(
            action=audit_events.EMR_SYNC_FAILURE,
            outcome="denied",
            event_metadata={"reason": "cross_tenant_access", "target": "hospital_emr"},
            **context,
        )
        raise HTTPException(status_code=403, detail="Resource does not belong to your hospital")
    if record.status == "synced_to_emr":
        await audit_service.safe_log_event(
            action=audit_events.EMR_SYNC_SUCCESS,
            event_metadata={"target": "hospital_emr", "already_synced": True},
            **context,
        )
        return record
    if record.status != "approved":
        await audit_service.safe_log_event(
            action=audit_events.EMR_SYNC_FAILURE,
            outcome="failure",
            event_metadata={
                "reason": "record_not_approved",
                "current_status": record.status,
                "target": "hospital_emr",
            },
            **context,
        )
        raise HTTPException(status_code=409, detail="Only approved records can be synced")

    try:
        record.status = "synced_to_emr"
        await db.commit()
        await db.refresh(record)
    except Exception as exc:
        await db.rollback()
        await audit_service.safe_log_event(
            action=audit_events.EMR_SYNC_FAILURE,
            outcome="failure",
            event_metadata={"reason": type(exc).__name__, "target": "hospital_emr"},
            **context,
        )
        raise

    await audit_service.safe_log_event(
        action=audit_events.EMR_SYNC_SUCCESS,
        changes={"status": {"before": "approved", "after": "synced_to_emr"}},
        event_metadata={"target": "hospital_emr"},
        **context,
    )
    return record
