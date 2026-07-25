import hashlib
import math
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import cast, delete, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser
from app.core.config import settings
from app.core.security import generate_invitation_token
from app.db.models import (
    City,
    AdminOrganization,
    ClientService,
    ClientProfile,
    District,
    Hospital,
    InvitationClientAccess,
    RefreshToken,
    Role,
    Service,
    State,
    User,
    UserClientAccess,
    UserInvitation,
)
from app.schemas.admin import (
    ClientAccessGrant,
    ClientDetail,
    ClientListResponse,
    ClientPayload,
    ClientSummary,
    CurrentUserResponse,
    InviteUserRequest,
    RoleSummary,
    UpdateUserAccessRequest,
    UserClientAccessSummary,
    UserSummary,
)


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(str(value))


async def validate_location_hierarchy(
    db: AsyncSession, *, state_id: uuid.UUID, district_id: uuid.UUID, city_id: uuid.UUID
) -> tuple[State, District, City]:
    state_row = await db.scalar(
        select(State).where(State.id == state_id, State.is_active.is_(True))
    )
    district_row = await db.scalar(
        select(District).where(
            District.id == district_id,
            District.state_id == state_id,
            District.is_active.is_(True),
        )
    )
    city_row = await db.scalar(
        select(City).where(
            City.id == city_id,
            City.district_id == district_id,
            City.is_active.is_(True),
        )
    )
    if state_row is None or district_row is None or city_row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="State, district, and city must form a valid active hierarchy",
        )
    return state_row, district_row, city_row


def _api_key_values(api_key: str | None) -> tuple[str | None, str | None]:
    if not api_key:
        return None, None
    digest = hashlib.sha256(api_key.encode()).hexdigest()
    if len(api_key) <= 8:
        hint = f"••••{api_key[-4:]}"
    else:
        hint = f"{api_key[:4]}••••{api_key[-4:]}"
    return digest, hint


async def _resolve_client_services(
    db: AsyncSession,
    payload: ClientPayload,
    current_user: CurrentUser,
) -> list[Service]:
    service_ids = set(payload.service_ids)
    if len(service_ids) != len(payload.service_ids):
        raise HTTPException(status_code=422, detail="Services contain duplicates")

    query = select(Service).where(
        Service.admin_organization_id
        == _uuid(current_user.admin_organization_id),
        Service.is_active.is_(True),
    )
    if service_ids:
        query = query.where(Service.id.in_(service_ids))
    elif payload.requested_services:
        requested = {item.lower() for item in payload.requested_services}
        query = query.where(
            or_(
                func.lower(Service.name).in_(requested),
                func.lower(Service.code).in_(requested),
            )
        )
    else:
        return []

    services = (await db.execute(query.order_by(Service.name))).scalars().all()
    expected_count = len(service_ids or set(payload.requested_services or []))
    if len(services) != expected_count:
        raise HTTPException(
            status_code=422,
            detail="Every selected service must exist and be active in this workspace",
        )
    return services


async def _replace_client_services(
    db: AsyncSession,
    *,
    client_id: uuid.UUID,
    services: list[Service],
) -> None:
    await db.execute(
        delete(ClientService).where(ClientService.hospital_id == client_id)
    )
    for service in services:
        db.add(ClientService(hospital_id=client_id, service_id=service.id))


async def _unique_hospital_code(db: AsyncSession, client_name: str) -> str:
    base = re.sub(r"[^A-Z0-9]+", "-", client_name.upper()).strip("-")[:40] or "CLIENT"
    candidate = base
    suffix = 1
    while await db.scalar(select(Hospital.id).where(Hospital.code == candidate)):
        suffix += 1
        candidate = f"{base[:44]}-{suffix}"
    return candidate


def _profile_to_summary(
    profile: ClientProfile, *, can_manage: bool
) -> ClientSummary:
    return ClientSummary(
        id=profile.hospital_id,
        client_name=profile.hospital.name,
        code=profile.hospital.code,
        email=profile.email,
        hq_location=profile.hq_location,
        service_start_date=profile.service_start_date,
        requested_services=profile.requested_services or [],
        state=profile.state.name,
        city=profile.city.name,
        is_active=profile.hospital.is_active,
        can_manage=can_manage,
    )


def _profile_to_detail(profile: ClientProfile, *, can_manage: bool) -> ClientDetail:
    summary = _profile_to_summary(profile, can_manage=can_manage).model_dump()
    return ClientDetail(
        **summary,
        pan_number=profile.pan_number,
        gst_number=profile.gst_number,
        complete_address=profile.complete_address,
        state_id=profile.state_id,
        district_id=profile.district_id,
        city_id=profile.city_id,
        district=profile.district.name,
        pincode=profile.pincode,
        contact_name=profile.contact_name,
        contact_email=profile.contact_email,
        contact_mobile=profile.contact_mobile,
        emergency_contact_name=profile.emergency_contact_name,
        emergency_contact_mobile=profile.emergency_contact_mobile,
        api_key_hint=profile.api_key_hint,
        created_at=profile.hospital.created_at,
        updated_at=profile.updated_at,
    )


async def _client_access(
    db: AsyncSession, current_user: CurrentUser, client_id: uuid.UUID
) -> tuple[bool, bool]:
    organization_id = _uuid(current_user.admin_organization_id)
    belongs_to_organization = await db.scalar(
        select(Hospital.id).where(
            Hospital.id == client_id,
            Hospital.admin_organization_id == organization_id,
        )
    )
    if not belongs_to_organization:
        return False, False
    if "clients:view_all" in current_user.permissions:
        return True, "clients:manage" in current_user.permissions
    grant = await db.scalar(
        select(UserClientAccess)
        .join(Hospital, Hospital.id == UserClientAccess.hospital_id)
        .where(
            UserClientAccess.user_id == _uuid(current_user.user_id),
            UserClientAccess.hospital_id == client_id,
            Hospital.admin_organization_id == organization_id,
        )
    )
    return grant is not None, bool(grant and grant.can_manage)


async def create_client(
    db: AsyncSession, *, payload: ClientPayload, current_user: CurrentUser
) -> ClientDetail:
    await validate_location_hierarchy(
        db,
        state_id=payload.state_id,
        district_id=payload.district_id,
        city_id=payload.city_id,
    )
    services = await _resolve_client_services(db, payload, current_user)
    duplicate = await db.scalar(
        select(ClientProfile.hospital_id).where(
            or_(
                func.lower(ClientProfile.email) == payload.email.lower(),
                ClientProfile.pan_number == payload.pan_number,
                ClientProfile.gst_number == payload.gst_number,
            )
        )
    )
    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A client with this email, PAN, or GST number already exists",
        )

    hospital = Hospital(
        admin_organization_id=_uuid(current_user.admin_organization_id),
        name=payload.client_name,
        code=await _unique_hospital_code(db, payload.client_name),
        is_active=True,
    )
    db.add(hospital)
    await db.flush()
    api_key_hash, api_key_hint = _api_key_values(payload.api_key)
    data = payload.model_dump(
        exclude={"client_name", "api_key", "service_ids", "requested_services"},
        mode="python",
    )
    data["email"] = str(payload.email)
    data["contact_email"] = str(payload.contact_email)
    profile = ClientProfile(
        hospital_id=hospital.id,
        requested_services=[service.name for service in services],
        api_key_hash=api_key_hash,
        api_key_hint=api_key_hint,
        **data,
    )
    db.add(profile)
    await _replace_client_services(
        db, client_id=hospital.id, services=services
    )
    db.add(
        UserClientAccess(
            user_id=_uuid(current_user.user_id),
            hospital_id=hospital.id,
            can_manage=True,
            granted_by=_uuid(current_user.user_id),
        )
    )
    await db.commit()
    result = await db.execute(
        select(ClientProfile).where(ClientProfile.hospital_id == hospital.id)
    )
    return _profile_to_detail(result.scalar_one(), can_manage=True)


async def list_clients(
    db: AsyncSession,
    *,
    current_user: CurrentUser,
    page: int,
    page_size: int,
    search: str | None,
    state_id: uuid.UUID | None,
    service: str | None,
    is_active: bool | None,
    sort_by: str,
    sort_order: str,
) -> ClientListResponse:
    organization_id = _uuid(current_user.admin_organization_id)
    query = select(ClientProfile)
    count_query = select(func.count()).select_from(ClientProfile)
    if "clients:view_all" not in current_user.permissions:
        access_filter = UserClientAccess.user_id == _uuid(current_user.user_id)
        query = query.join(
            UserClientAccess,
            UserClientAccess.hospital_id == ClientProfile.hospital_id,
        ).where(access_filter)
        count_query = count_query.join(
            UserClientAccess,
            UserClientAccess.hospital_id == ClientProfile.hospital_id,
        ).where(access_filter)

    filters = []
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(
                Hospital.name.ilike(pattern),
                ClientProfile.email.ilike(pattern),
                ClientProfile.hq_location.ilike(pattern),
                ClientProfile.pan_number.ilike(pattern),
                ClientProfile.gst_number.ilike(pattern),
            )
        )
    if state_id:
        filters.append(ClientProfile.state_id == state_id)
    if service:
        filters.append(cast(ClientProfile.requested_services, JSONB).contains([service]))
    if is_active is not None:
        filters.append(Hospital.is_active.is_(is_active))

    query = query.join(Hospital, Hospital.id == ClientProfile.hospital_id)
    count_query = count_query.join(Hospital, Hospital.id == ClientProfile.hospital_id)
    filters.append(Hospital.admin_organization_id == organization_id)
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)

    sort_columns = {
        "name": Hospital.name,
        "email": ClientProfile.email,
        "service_start_date": ClientProfile.service_start_date,
        "created_at": Hospital.created_at,
    }
    sort_column = sort_columns[sort_by]
    query = query.order_by(
        sort_column.desc() if sort_order == "desc" else sort_column.asc(),
        Hospital.id.asc(),
    ).offset((page - 1) * page_size).limit(page_size)

    total = int(await db.scalar(count_query) or 0)
    profiles = (await db.execute(query)).scalars().unique().all()
    grants: dict[uuid.UUID, bool] = {}
    if "clients:view_all" in current_user.permissions:
        grants = {
            profile.hospital_id: "clients:manage" in current_user.permissions
            for profile in profiles
        }
    else:
        access_rows = (
            await db.execute(
                select(UserClientAccess).where(
                    UserClientAccess.user_id == _uuid(current_user.user_id),
                    UserClientAccess.hospital_id.in_([p.hospital_id for p in profiles]),
                )
            )
        ).scalars().all()
        grants = {row.hospital_id: row.can_manage for row in access_rows}

    return ClientListResponse(
        items=[
            _profile_to_summary(profile, can_manage=grants.get(profile.hospital_id, False))
            for profile in profiles
        ],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=math.ceil(total / page_size) if total else 0,
    )


async def get_client(
    db: AsyncSession, *, client_id: uuid.UUID, current_user: CurrentUser
) -> ClientDetail:
    visible, can_manage = await _client_access(db, current_user, client_id)
    if not visible:
        raise HTTPException(status_code=404, detail="Client not found")
    profile = await db.scalar(
        select(ClientProfile).where(ClientProfile.hospital_id == client_id)
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return _profile_to_detail(profile, can_manage=can_manage)


async def update_client(
    db: AsyncSession,
    *,
    client_id: uuid.UUID,
    payload: ClientPayload,
    current_user: CurrentUser,
) -> ClientDetail:
    visible, can_manage = await _client_access(db, current_user, client_id)
    if not visible or not can_manage:
        raise HTTPException(status_code=403, detail="You cannot manage this client")
    profile = await db.scalar(
        select(ClientProfile).where(ClientProfile.hospital_id == client_id)
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Client not found")
    await validate_location_hierarchy(
        db,
        state_id=payload.state_id,
        district_id=payload.district_id,
        city_id=payload.city_id,
    )
    services = await _resolve_client_services(db, payload, current_user)
    duplicate = await db.scalar(
        select(ClientProfile.hospital_id).where(
            ClientProfile.hospital_id != client_id,
            or_(
                func.lower(ClientProfile.email) == payload.email.lower(),
                ClientProfile.pan_number == payload.pan_number,
                ClientProfile.gst_number == payload.gst_number,
            ),
        )
    )
    if duplicate:
        raise HTTPException(
            status_code=409, detail="A client with this email, PAN, or GST number already exists"
        )
    profile.hospital.name = payload.client_name
    for field, value in payload.model_dump(
        exclude={
            "client_name",
            "api_key",
            "service_ids",
            "requested_services",
        },
        mode="python",
    ).items():
        setattr(profile, field, str(value) if field in {"email", "contact_email"} else value)
    profile.requested_services = [service.name for service in services]
    await _replace_client_services(
        db, client_id=client_id, services=services
    )
    if payload.api_key:
        profile.api_key_hash, profile.api_key_hint = _api_key_values(payload.api_key)
    await db.commit()
    await db.refresh(profile)
    return _profile_to_detail(profile, can_manage=True)


async def current_user_profile(
    db: AsyncSession, current_user: CurrentUser
) -> CurrentUserResponse:
    user = await db.scalar(
        select(User)
        .options(selectinload(User.role).selectinload(Role.permissions))
        .where(
            User.id == _uuid(current_user.user_id),
            User.admin_organization_id == _uuid(current_user.admin_organization_id),
            User.is_active.is_(True),
        )
    )
    if user is None:
        raise HTTPException(status_code=401, detail="User is no longer active")
    organization = await db.get(AdminOrganization, user.admin_organization_id)
    if organization is None or not organization.is_active:
        raise HTTPException(status_code=401, detail="Administration organization is inactive")
    return CurrentUserResponse(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        admin_organization_id=user.admin_organization_id,
        organization_name=organization.name,
        organization_slug=organization.slug,
        role=user.role.name,
        permissions=sorted(permission.code for permission in user.role.permissions),
    )


async def list_roles(db: AsyncSession, current_user: CurrentUser) -> list[RoleSummary]:
    roles = (
        await db.execute(
            select(Role)
            .options(selectinload(Role.permissions))
            .where(
                Role.hospital_id.is_(None),
                Role.name.in_(("company_owner", "company_admin", "company_member")),
            )
            .order_by(Role.name)
        )
    ).scalars().unique().all()
    return [
        RoleSummary(
            id=role.id,
            name=role.name,
            permissions=sorted(permission.code for permission in role.permissions),
        )
        for role in roles
    ]


async def _validate_role(db: AsyncSession, role_id: uuid.UUID, current_user: CurrentUser) -> Role:
    role = await db.scalar(
        select(Role)
        .options(selectinload(Role.permissions))
        .where(
            Role.id == role_id,
            Role.hospital_id.is_(None),
            Role.name.in_(("company_owner", "company_admin", "company_member")),
        )
    )
    if role is None:
        raise HTTPException(status_code=422, detail="Role is not assignable")
    if "clients:read" not in {permission.code for permission in role.permissions}:
        raise HTTPException(status_code=422, detail="Role is not an administration role")
    return role


async def _validate_access_grants(
    db: AsyncSession,
    grants: list[ClientAccessGrant],
    current_user: CurrentUser,
) -> None:
    ids = {grant.client_id for grant in grants}
    if len(ids) != len(grants):
        raise HTTPException(status_code=422, detail="Client access contains duplicates")
    if not ids:
        return
    if "clients:view_all" in current_user.permissions:
        existing = set(
            (
                await db.execute(
                    select(ClientProfile.hospital_id)
                    .join(Hospital, Hospital.id == ClientProfile.hospital_id)
                    .where(
                        ClientProfile.hospital_id.in_(ids),
                        Hospital.admin_organization_id
                        == _uuid(current_user.admin_organization_id),
                    )
                )
            ).scalars().all()
        )
    else:
        existing = set(
            (
                await db.execute(
                    select(UserClientAccess.hospital_id).where(
                        UserClientAccess.user_id == _uuid(current_user.user_id),
                        UserClientAccess.hospital_id.in_(ids),
                        UserClientAccess.can_manage.is_(True),
                    )
                )
            ).scalars().all()
        )
    if existing != ids:
        raise HTTPException(
            status_code=403,
            detail="You may only assign clients you are authorized to manage",
        )


async def create_invitation(
    db: AsyncSession,
    *,
    payload: InviteUserRequest,
    current_user: CurrentUser,
) -> tuple[UserInvitation, str]:
    await _validate_role(db, payload.role_id, current_user)
    await _validate_access_grants(db, payload.client_access, current_user)
    existing = await db.scalar(
        select(User.id).where(
            User.admin_organization_id.is_not(None),
            func.lower(User.email) == payload.email.lower(),
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="This user already has an account")
    await db.execute(
        delete(UserInvitation).where(
            UserInvitation.admin_organization_id
            == _uuid(current_user.admin_organization_id),
            func.lower(UserInvitation.email) == payload.email.lower(),
            UserInvitation.accepted_at.is_(None),
        )
    )
    raw_token, token_hash = generate_invitation_token()
    invitation = UserInvitation(
        email=str(payload.email).lower(),
        full_name=payload.full_name,
        hospital_id=None,
        admin_organization_id=_uuid(current_user.admin_organization_id),
        role_id=payload.role_id,
        token_hash=token_hash,
        invited_by=_uuid(current_user.user_id),
        expires_at=datetime.now(timezone.utc)
        + timedelta(hours=settings.INVITATION_EXPIRE_HOURS),
    )
    db.add(invitation)
    await db.flush()
    for grant in payload.client_access:
        db.add(
            InvitationClientAccess(
                invitation_id=invitation.id,
                hospital_id=grant.client_id,
                can_manage=grant.can_manage,
            )
        )
    await db.commit()
    await db.refresh(invitation)
    return invitation, raw_token


async def _user_summary(db: AsyncSession, user: User) -> UserSummary:
    access_rows = (
        await db.execute(
            select(UserClientAccess)
            .where(UserClientAccess.user_id == user.id)
            .order_by(UserClientAccess.created_at)
        )
    ).scalars().all()
    return UserSummary(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        role_id=user.role_id,
        role=user.role.name,
        permissions=sorted(permission.code for permission in user.role.permissions),
        is_active=user.is_active,
        client_access=[
            UserClientAccessSummary(
                client_id=row.hospital_id,
                client_name=row.hospital.name,
                can_manage=row.can_manage,
            )
            for row in access_rows
        ],
    )


async def list_users(db: AsyncSession, current_user: CurrentUser) -> list[UserSummary]:
    users = (
        await db.execute(
            select(User)
            .options(selectinload(User.role).selectinload(Role.permissions))
            .where(
                User.admin_organization_id
                == _uuid(current_user.admin_organization_id)
            )
            .order_by(User.full_name)
        )
    ).scalars().unique().all()
    return [await _user_summary(db, user) for user in users]


async def update_user_access(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    payload: UpdateUserAccessRequest,
    current_user: CurrentUser,
) -> UserSummary:
    if user_id == _uuid(current_user.user_id):
        current_role_id = await db.scalar(
            select(User.role_id).where(User.id == _uuid(current_user.user_id))
        )
        if not payload.is_active:
            raise HTTPException(status_code=422, detail="You cannot deactivate your own account")
        if payload.role_id != current_role_id:
            raise HTTPException(status_code=422, detail="You cannot change your own role")
    await _validate_role(db, payload.role_id, current_user)
    await _validate_access_grants(db, payload.client_access, current_user)
    user = await db.scalar(
        select(User)
        .options(selectinload(User.role).selectinload(Role.permissions))
        .where(
            User.id == user_id,
            User.admin_organization_id == _uuid(current_user.admin_organization_id),
        )
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.role_id = payload.role_id
    user.is_active = payload.is_active
    await db.execute(delete(UserClientAccess).where(UserClientAccess.user_id == user.id))
    for grant in payload.client_access:
        db.add(
            UserClientAccess(
                user_id=user.id,
                hospital_id=grant.client_id,
                can_manage=grant.can_manage,
                granted_by=_uuid(current_user.user_id),
            )
        )
    await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
    await db.commit()
    refreshed = await db.scalar(
        select(User)
        .options(selectinload(User.role).selectinload(Role.permissions))
        .where(User.id == user.id)
    )
    return await _user_summary(db, refreshed)
