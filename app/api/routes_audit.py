import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.db.database import get_db
from app.schemas.audit import AuditEventCreate, AuditEventList
from app.services import audit_service
from app.services.authorization import require_permission

router = APIRouter(prefix="/audit", tags=["Audit Trail"])


@router.post(
    "/events",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_audit_event(
    payload: AuditEventCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:read")),
):
    event = await audit_service.record_event(
        db,
        current_user=current_user,
        payload=payload,
        ip_address=request.client.host if request.client else None,
    )
    return {"accepted": True, "id": str(event.id)}


@router.get("/events", response_model=AuditEventList)
async def audit_events(
    search: str | None = Query(default=None, max_length=100),
    action: str | None = Query(default=None, max_length=100),
    outcome: str | None = Query(default=None, max_length=20),
    patient_id: uuid.UUID | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:read")),
):
    return await audit_service.list_events(
        db,
        current_user=current_user,
        search=search,
        action=action,
        outcome=outcome,
        patient_id=patient_id,
        limit=limit,
    )
