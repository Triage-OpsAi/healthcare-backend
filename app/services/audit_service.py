"""Append-only, failure-isolated clinical audit service."""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.deps import CurrentUser
from app.db.database import AsyncSessionLocal
from app.db.models import AuditLog, Patient, User
from app.schemas.audit import AuditEventCreate, AuditEventList, AuditEventSummary

logger = logging.getLogger(__name__)


def _uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


async def record_event(
    db: AsyncSession,
    *,
    current_user: CurrentUser,
    payload: AuditEventCreate,
    ip_address: str | None = None,
) -> AuditLog:
    existing = (
        await db.execute(
            select(AuditLog).where(AuditLog.client_event_id == payload.client_event_id)
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    event = AuditLog(
        hospital_id=_uuid(current_user.hospital_id),
        admin_organization_id=_uuid(current_user.admin_organization_id),
        user_id=_uuid(current_user.user_id),
        client_event_id=payload.client_event_id,
        action=payload.action,
        event_category=payload.event_category,
        actor_role=current_user.role,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        patient_id=payload.patient_id,
        encounter_id=payload.encounter_id,
        outcome=payload.outcome,
        source=payload.source,
        request_id=payload.request_id,
        changes=payload.changes,
        event_metadata=payload.event_metadata,
        ip_address=ip_address,
        occurred_at=payload.occurred_at,
    )
    db.add(event)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        duplicate = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.client_event_id == payload.client_event_id
                )
            )
        ).scalar_one()
        return duplicate
    await db.refresh(event)
    return event


async def safe_log_event(
    *,
    hospital_id: str | uuid.UUID | None,
    user_id: str | uuid.UUID | None,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None = None,
    patient_id: uuid.UUID | None = None,
    encounter_id: uuid.UUID | None = None,
    actor_role: str | None = "service",
    outcome: str = "success",
    source: str = "service",
    changes: dict | None = None,
    event_metadata: dict | None = None,
    occurred_at: datetime | None = None,
) -> None:
    """Write through an isolated session and never fail the clinical workflow."""
    try:
        async with AsyncSessionLocal() as audit_db:
            audit_db.add(
                AuditLog(
                    hospital_id=_uuid(hospital_id),
                    user_id=_uuid(user_id),
                    action=action,
                    event_category="clinical",
                    actor_role=actor_role,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    patient_id=patient_id,
                    encounter_id=encounter_id,
                    outcome=outcome,
                    source=source,
                    changes=changes,
                    event_metadata=event_metadata,
                    occurred_at=occurred_at or datetime.now(timezone.utc),
                )
            )
            await audit_db.commit()
    except Exception:
        logger.exception("Audit event could not be persisted: %s", action)


async def list_events(
    db: AsyncSession,
    *,
    current_user: CurrentUser,
    search: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    patient_id: uuid.UUID | None = None,
    limit: int = 200,
) -> AuditEventList:
    hospital_id = _uuid(current_user.hospital_id)
    actor = aliased(User)
    query = (
        select(AuditLog, actor, Patient)
        .outerjoin(actor, actor.id == AuditLog.user_id)
        .outerjoin(Patient, Patient.id == AuditLog.patient_id)
        .where(AuditLog.hospital_id == hospital_id)
    )
    count_query = select(func.count(AuditLog.id)).where(
        AuditLog.hospital_id == hospital_id
    )
    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if outcome:
        query = query.where(AuditLog.outcome == outcome)
        count_query = count_query.where(AuditLog.outcome == outcome)
    if patient_id:
        query = query.where(AuditLog.patient_id == patient_id)
        count_query = count_query.where(AuditLog.patient_id == patient_id)
    if search:
        pattern = f"%{search.strip()}%"
        condition = or_(
            AuditLog.action.ilike(pattern),
            AuditLog.resource_type.ilike(pattern),
            actor.full_name.ilike(pattern),
            Patient.full_name.ilike(pattern),
            Patient.abha_id.ilike(pattern),
        )
        query = query.where(condition)
        count_query = count_query.outerjoin(
            actor, actor.id == AuditLog.user_id
        ).outerjoin(Patient, Patient.id == AuditLog.patient_id).where(condition)
    rows = (
        await db.execute(query.order_by(AuditLog.timestamp.desc()).limit(limit))
    ).all()
    total = int((await db.execute(count_query)).scalar_one())
    return AuditEventList(
        total=total,
        events=[
            AuditEventSummary(
                id=event.id,
                client_event_id=event.client_event_id,
                action=event.action,
                event_category=event.event_category,
                actor_name=user.full_name if user else "System service",
                actor_role=event.actor_role,
                patient_id=event.patient_id,
                patient_name=patient.full_name if patient else None,
                patient_reference=(
                    patient.abha_id or str(patient.id)[:8].upper()
                    if patient
                    else None
                ),
                encounter_id=event.encounter_id,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                outcome=event.outcome,
                source=event.source,
                changes=event.changes,
                event_metadata=event.event_metadata,
                occurred_at=event.occurred_at or event.timestamp,
                recorded_at=event.timestamp,
            )
            for event, user, patient in rows
        ],
    )


async def log_event(
    db: AsyncSession,
    *,
    hospital_id: str | None,
    admin_organization_id: str | None = None,
    user_id: str | None,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None = None,
    ip_address: str | None = None,
) -> None:
    """Backward-compatible append helper for existing callers."""
    db.add(
        AuditLog(
            hospital_id=_uuid(hospital_id),
            admin_organization_id=_uuid(admin_organization_id),
            user_id=_uuid(user_id),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            occurred_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()
