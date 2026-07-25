import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


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
    """Append-only audit trail. Call this on every read/write of patient
    data -- required for DPDP Act compliance and hospital audit requests.
    Commits independently so an audit-log failure never silently rolls
    back the actual clinical write it's describing."""
    db.add(
        AuditLog(
            hospital_id=hospital_id,
            admin_organization_id=admin_organization_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
        )
    )
    await db.commit()
