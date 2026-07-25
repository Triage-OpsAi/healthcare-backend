import hashlib
import secrets
import string
import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.db.models import Service, ServiceApiKey
from app.schemas.admin import (
    ApiKeyCreated,
    ApiKeyCreateRequest,
    ApiKeySummary,
    ServicePayload,
    ServiceResponse,
)


def _organization_id(current_user: CurrentUser) -> uuid.UUID:
    return uuid.UUID(str(current_user.admin_organization_id))


def _service_response(service: Service) -> ServiceResponse:
    charge = Decimal(service.service_charge)
    percentage = Decimal(service.gst_percentage)
    gst_amount = (
        (charge * percentage / Decimal("100")).quantize(Decimal("0.01"))
        if service.gst_included
        else Decimal("0.00")
    )
    return ServiceResponse(
        id=service.id,
        name=service.name,
        code=service.code,
        about=service.about,
        features=service.features or [],
        service_charge=charge,
        gst_included=service.gst_included,
        gst_percentage=percentage,
        is_active=service.is_active,
        gst_amount=gst_amount,
        total_charge=charge + gst_amount,
        created_at=service.created_at,
        updated_at=service.updated_at,
    )


async def generate_service_code(
    db: AsyncSession, current_user: CurrentUser
) -> str:
    alphabet = string.ascii_uppercase + string.digits
    organization_id = _organization_id(current_user)
    for _ in range(100):
        code = "TA" + "".join(secrets.choice(alphabet) for _ in range(4))
        exists = await db.scalar(
            select(Service.id).where(
                Service.admin_organization_id == organization_id,
                Service.code == code,
            )
        )
        if not exists:
            return code
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Unable to generate a unique service code",
    )


async def list_services(
    db: AsyncSession,
    current_user: CurrentUser,
    *,
    active_only: bool = False,
) -> list[ServiceResponse]:
    query = select(Service).where(
        Service.admin_organization_id == _organization_id(current_user)
    )
    if active_only:
        query = query.where(Service.is_active.is_(True))
    services = (
        await db.execute(query.order_by(Service.name))
    ).scalars().all()
    return [_service_response(service) for service in services]


async def _validate_unique_service(
    db: AsyncSession,
    current_user: CurrentUser,
    payload: ServicePayload,
    *,
    exclude_id: uuid.UUID | None = None,
) -> None:
    query = select(Service.id).where(
        Service.admin_organization_id == _organization_id(current_user),
        (
            (func.lower(Service.name) == payload.name.lower())
            | (Service.code == payload.code)
        ),
    )
    if exclude_id:
        query = query.where(Service.id != exclude_id)
    if await db.scalar(query):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A service with this name or code already exists",
        )


async def create_service(
    db: AsyncSession,
    current_user: CurrentUser,
    payload: ServicePayload,
) -> ServiceResponse:
    await _validate_unique_service(db, current_user, payload)
    service = Service(
        admin_organization_id=_organization_id(current_user),
        **payload.model_dump(mode="python"),
    )
    db.add(service)
    await db.commit()
    await db.refresh(service)
    return _service_response(service)


async def update_service(
    db: AsyncSession,
    current_user: CurrentUser,
    service_id: uuid.UUID,
    payload: ServicePayload,
) -> ServiceResponse:
    service = await db.scalar(
        select(Service).where(
            Service.id == service_id,
            Service.admin_organization_id == _organization_id(current_user),
        )
    )
    if service is None:
        raise HTTPException(status_code=404, detail="Service not found")
    await _validate_unique_service(
        db, current_user, payload, exclude_id=service_id
    )
    for field, value in payload.model_dump(mode="python").items():
        setattr(service, field, value)
    await db.commit()
    await db.refresh(service)
    return _service_response(service)


def _key_summary(row: ServiceApiKey) -> ApiKeySummary:
    return ApiKeySummary(
        id=row.id,
        service_id=row.service_id,
        service_name=row.service.name,
        service_code=row.service.code,
        label=row.label,
        key_hint=row.key_hint,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
    )


async def list_api_keys(
    db: AsyncSession, current_user: CurrentUser
) -> list[ApiKeySummary]:
    rows = (
        await db.execute(
            select(ServiceApiKey)
            .where(
                ServiceApiKey.admin_organization_id
                == _organization_id(current_user)
            )
            .order_by(ServiceApiKey.created_at.desc())
        )
    ).scalars().all()
    return [_key_summary(row) for row in rows]


async def create_api_key(
    db: AsyncSession,
    current_user: CurrentUser,
    payload: ApiKeyCreateRequest,
) -> ApiKeyCreated:
    service = await db.scalar(
        select(Service).where(
            Service.id == payload.service_id,
            Service.admin_organization_id == _organization_id(current_user),
            Service.is_active.is_(True),
        )
    )
    if service is None:
        raise HTTPException(status_code=422, detail="Select an active service")

    raw_key = f"ta_live_{secrets.token_urlsafe(32)}"
    row = ServiceApiKey(
        admin_organization_id=_organization_id(current_user),
        service_id=service.id,
        label=payload.label,
        key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        key_hint=f"{raw_key[:11]}••••{raw_key[-4:]}",
        created_by=uuid.UUID(str(current_user.user_id)),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    summary = _key_summary(row)
    return ApiKeyCreated(**summary.model_dump(), api_key=raw_key)
