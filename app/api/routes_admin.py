import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.config import settings
from app.db.database import get_db
from app.db.models import City, District, Hospital, State
from app.schemas.admin import (
    ClientCreateResponse,
    ClientInvitationDelivery,
    ClientDetail,
    ClientListResponse,
    ClientPayload,
    CurrentUserResponse,
    GeneratedServiceCode,
    InvitationResponse,
    InviteUserRequest,
    LocationItem,
    ApiKeyCreated,
    ApiKeyCreateRequest,
    ApiKeySummary,
    RoleSummary,
    ServicePayload,
    ServiceResponse,
    UpdateUserAccessRequest,
    UserSummary,
)
from app.services import (
    admin_service,
    audit_service,
    catalog_service,
    doctor_service,
    invitation_email,
)
from app.services.authorization import require_admin_permission

client_router = APIRouter(prefix="/clients", tags=["Clients"])
user_router = APIRouter(prefix="/users", tags=["Users"])
location_router = APIRouter(prefix="/locations", tags=["Locations"])
service_router = APIRouter(prefix="/services", tags=["Services"])
api_key_router = APIRouter(prefix="/api-keys", tags=["API Keys"])


@service_router.get(
    "",
    response_model=list[ServiceResponse],
    operation_id="list_services",
)
async def list_services(
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(
        require_admin_permission("services:read")
    ),
):
    return await catalog_service.list_services(
        db, current_user, active_only=active_only
    )


@service_router.get(
    "/code/generate",
    response_model=GeneratedServiceCode,
    operation_id="generate_service_code",
)
async def generate_service_code(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(
        require_admin_permission("services:manage")
    ),
):
    return GeneratedServiceCode(
        code=await catalog_service.generate_service_code(db, current_user)
    )


@service_router.post(
    "",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_service",
)
async def create_service(
    payload: ServicePayload,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(
        require_admin_permission("services:manage")
    ),
):
    return await catalog_service.create_service(db, current_user, payload)


@service_router.put(
    "/{service_id}",
    response_model=ServiceResponse,
    operation_id="update_service",
)
async def update_service(
    service_id: uuid.UUID,
    payload: ServicePayload,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(
        require_admin_permission("services:manage")
    ),
):
    return await catalog_service.update_service(
        db, current_user, service_id, payload
    )


@api_key_router.get(
    "",
    response_model=list[ApiKeySummary],
    operation_id="list_service_api_keys",
)
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(
        require_admin_permission("api_keys:manage")
    ),
):
    return await catalog_service.list_api_keys(db, current_user)


@api_key_router.post(
    "",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_service_api_key",
)
async def create_api_key(
    payload: ApiKeyCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(
        require_admin_permission("api_keys:manage")
    ),
):
    return await catalog_service.create_api_key(db, current_user, payload)


@client_router.get("", response_model=ClientListResponse, operation_id="list_clients")
async def list_clients(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=255),
    state_id: uuid.UUID | None = None,
    service: str | None = Query(None, max_length=100),
    is_active: bool | None = None,
    sort_by: Literal["name", "email", "service_start_date", "created_at"] = "name",
    sort_order: Literal["asc", "desc"] = "asc",
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_admin_permission("clients:read")),
):
    return await admin_service.list_clients(
        db,
        current_user=current_user,
        page=page,
        page_size=page_size,
        search=search,
        state_id=state_id,
        service=service,
        is_active=is_active,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@client_router.post(
    "",
    response_model=ClientCreateResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_client",
)
async def create_client(
    payload: ClientPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_admin_permission("clients:manage")),
):
    client = await admin_service.create_client(
        db, payload=payload, current_user=current_user
    )
    hospital = await db.get(Hospital, client.id)
    owner_invitations = await doctor_service.create_owner_invitations(
        db,
        hospital=hospital,
        owners=[
            (f"{client.client_name} Owner", client.email),
            (client.contact_name, client.contact_email),
        ],
        invited_by=uuid.UUID(current_user.user_id),
    )
    deliveries = await asyncio.gather(
        *(
            invitation_email.deliver_invitation(
                recipient=invitation.email,
                full_name=invitation.full_name,
                raw_token=raw_token,
                frontend_url=settings.DOCTOR_FRONTEND_URL,
                workspace_name=client.client_name,
            )
            for invitation, raw_token in owner_invitations
        ),
        return_exceptions=True,
    )
    invitation_results: list[ClientInvitationDelivery] = []
    for (invitation, _), delivery_result in zip(
        owner_invitations, deliveries, strict=True
    ):
        if isinstance(delivery_result, BaseException):
            invitation_results.append(
                ClientInvitationDelivery(
                    email=invitation.email,
                    delivery="failed",
                    error="SMTP delivery failed. Check the server mail logs.",
                )
            )
        else:
            delivery, magic_link = delivery_result
            invitation_results.append(
                ClientInvitationDelivery(
                    email=invitation.email,
                    delivery=delivery,
                    magic_link=magic_link,
                )
            )
    await audit_service.log_event(
        db,
        hospital_id=None,
        admin_organization_id=current_user.admin_organization_id,
        user_id=current_user.user_id,
        action="client.create",
        resource_type="client",
        resource_id=client.id,
        ip_address=request.client.host if request.client else None,
    )
    return ClientCreateResponse(
        client=client,
        invitations=invitation_results,
    )


@client_router.get(
    "/{client_id}", response_model=ClientDetail, operation_id="get_client"
)
async def get_client(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_admin_permission("clients:read")),
):
    return await admin_service.get_client(
        db, client_id=client_id, current_user=current_user
    )


@client_router.put(
    "/{client_id}", response_model=ClientDetail, operation_id="update_client"
)
async def update_client(
    client_id: uuid.UUID,
    payload: ClientPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_admin_permission("clients:manage")),
):
    client = await admin_service.update_client(
        db,
        client_id=client_id,
        payload=payload,
        current_user=current_user,
    )
    await audit_service.log_event(
        db,
        hospital_id=None,
        admin_organization_id=current_user.admin_organization_id,
        user_id=current_user.user_id,
        action="client.update",
        resource_type="client",
        resource_id=client.id,
        ip_address=request.client.host if request.client else None,
    )
    return client


@location_router.get(
    "/states", response_model=list[LocationItem], operation_id="list_states"
)
async def list_states(
    db: AsyncSession = Depends(get_db),
    _current_user: CurrentUser = Depends(require_admin_permission("clients:read")),
):
    rows = (
        await db.execute(
            select(State)
            .where(State.is_active.is_(True))
            .order_by(State.name)
        )
    ).scalars().all()
    return [LocationItem(id=row.id, name=row.name, code=row.code) for row in rows]


@location_router.get(
    "/states/{state_id}/districts",
    response_model=list[LocationItem],
    operation_id="list_districts",
)
async def list_districts(
    state_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: CurrentUser = Depends(require_admin_permission("clients:read")),
):
    rows = (
        await db.execute(
            select(District)
            .where(District.state_id == state_id, District.is_active.is_(True))
            .order_by(District.name)
        )
    ).scalars().all()
    return [LocationItem(id=row.id, name=row.name) for row in rows]


@location_router.get(
    "/districts/{district_id}/cities",
    response_model=list[LocationItem],
    operation_id="list_cities",
)
async def list_cities(
    district_id: uuid.UUID,
    search: str | None = Query(None, max_length=100),
    limit: int = Query(500, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _current_user: CurrentUser = Depends(require_admin_permission("clients:read")),
):
    query = select(City).where(
        City.district_id == district_id, City.is_active.is_(True)
    )
    if search:
        query = query.where(City.name.ilike(f"%{search.strip()}%"))
    rows = (
        await db.execute(query.order_by(City.name).limit(limit))
    ).scalars().all()
    return [LocationItem(id=row.id, name=row.name) for row in rows]


@user_router.get(
    "/me", response_model=CurrentUserResponse, operation_id="get_current_user_profile"
)
async def me(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_admin_permission("clients:read")),
):
    return await admin_service.current_user_profile(db, current_user)


@user_router.get("", response_model=list[UserSummary], operation_id="list_users")
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_admin_permission("users:manage")),
):
    return await admin_service.list_users(db, current_user)


@user_router.get(
    "/roles", response_model=list[RoleSummary], operation_id="list_roles"
)
async def list_roles(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_admin_permission("users:manage")),
):
    return await admin_service.list_roles(db, current_user)


@user_router.post(
    "/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="invite_user",
)
async def invite_user(
    payload: InviteUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_admin_permission("users:manage")),
):
    invitation, raw_token = await admin_service.create_invitation(
        db, payload=payload, current_user=current_user
    )
    delivery, magic_link = await invitation_email.deliver_invitation(
        recipient=invitation.email,
        full_name=invitation.full_name,
        raw_token=raw_token,
    )
    return InvitationResponse(
        id=invitation.id,
        email=invitation.email,
        expires_at=invitation.expires_at,
        delivery=delivery,
        magic_link=magic_link,
    )


@user_router.put(
    "/{user_id}/access",
    response_model=UserSummary,
    operation_id="update_user_access",
)
async def update_user_access(
    user_id: uuid.UUID,
    payload: UpdateUserAccessRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_admin_permission("users:manage")),
):
    return await admin_service.update_user_access(
        db,
        user_id=user_id,
        payload=payload,
        current_user=current_user,
    )
