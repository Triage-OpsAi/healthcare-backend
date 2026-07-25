import uuid
from datetime import datetime, timezone
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_invitation_token,
    hash_refresh_token,
    hash_password,
    verify_password,
)
from app.db.models import (
    Hospital,
    AdminOrganization,
    InvitationClientAccess,
    Permission,
    RefreshToken,
    Role,
    User,
    UserClientAccess,
    UserInvitation,
)


class AuthError(Exception):
    """Raised for any login/refresh failure. Caught in the route layer -> 401."""


ADMIN_PERMISSION_DESCRIPTIONS = {
    "clients:read": "View explicitly assigned clients",
    "clients:manage": "Create and update manageable clients",
    "clients:view_all": "View every client in the administration organization",
    "users:manage": "Invite users and manage roles and client access",
    "services:read": "View the administration service catalog",
    "services:manage": "Create and update administration services",
    "api_keys:manage": "Create service-scoped API keys",
}


async def ensure_admin_roles(db: AsyncSession) -> dict[str, Role]:
    permissions: dict[str, Permission] = {}
    for code, description in ADMIN_PERMISSION_DESCRIPTIONS.items():
        permission = await db.scalar(select(Permission).where(Permission.code == code))
        if permission is None:
            permission = Permission(code=code, description=description)
            db.add(permission)
            await db.flush()
        permissions[code] = permission

    definitions = {
        "company_owner": list(ADMIN_PERMISSION_DESCRIPTIONS),
        "company_admin": list(ADMIN_PERMISSION_DESCRIPTIONS),
        "company_member": ["clients:read", "services:read"],
    }
    roles: dict[str, Role] = {}
    for name, codes in definitions.items():
        role = await db.scalar(
            select(Role)
            .options(selectinload(Role.permissions))
            .where(Role.hospital_id.is_(None), Role.name == name)
        )
        required = [permissions[code] for code in codes]
        if role is None:
            role = Role(hospital_id=None, name=name, permissions=required)
            db.add(role)
            await db.flush()
        else:
            existing = {permission.code for permission in role.permissions}
            role.permissions.extend(
                permission for permission in required if permission.code not in existing
            )
        roles[name] = role
    return roles


async def authenticate_admin_user(
    db: AsyncSession, *, email: str, password: str
) -> User:
    result = await db.execute(
        select(User)
        .join(
            AdminOrganization,
            AdminOrganization.id == User.admin_organization_id,
        )
        .where(
            func.lower(User.email) == email.lower(),
            User.admin_organization_id.is_not(None),
            User.is_active.is_(True),
            AdminOrganization.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.hashed_password):
        raise AuthError("Invalid credentials")
    return user


async def authenticate_clinical_user(
    db: AsyncSession, *, email: str, password: str, hospital_code: str
) -> User:
    hospital_result = await db.execute(
        select(Hospital).where(
            func.upper(Hospital.code) == hospital_code.upper(),
            Hospital.is_active.is_(True),
        )
    )
    hospital = hospital_result.scalar_one_or_none()
    if hospital is None:
        raise AuthError("Unknown or inactive hospital")

    user_result = await db.execute(
        select(User).where(
            func.lower(User.email) == email.lower(),
            User.hospital_id == hospital.id,
            User.is_active.is_(True),
        )
    )
    user = user_result.scalar_one_or_none()

    # Deliberately identical error whether the user doesn't exist or the
    # password is wrong -- avoids leaking which emails are registered.
    if user is None or not verify_password(password, user.hashed_password):
        raise AuthError("Invalid credentials")

    return user


async def issue_tokens(db: AsyncSession, user: User) -> tuple[str, str]:
    permission_codes = [p.code for p in user.role.permissions]
    access_token = create_access_token(
        user_id=str(user.id),
        hospital_id=str(user.hospital_id) if user.hospital_id else None,
        admin_organization_id=(
            str(user.admin_organization_id) if user.admin_organization_id else None
        ),
        role=user.role.name,
        permissions=permission_codes,
        user_type="admin" if user.admin_organization_id else "clinical",
    )

    raw_refresh, refresh_hash, expires_at = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_hash,
            expires_at=expires_at,
        )
    )
    await db.commit()

    return access_token, raw_refresh


async def signup_admin_owner(
    db: AsyncSession,
    *,
    organization_name: str,
    full_name: str,
    email: str,
    password: str,
) -> User:
    existing = await db.scalar(
        select(User.id).where(
            User.admin_organization_id.is_not(None),
            func.lower(User.email) == email.lower(),
        )
    )
    if existing:
        raise AuthError("An administration account already exists for this email")

    base_slug = re.sub(r"[^a-z0-9]+", "-", organization_name.lower()).strip("-")[:80]
    base_slug = base_slug or "company"
    slug = base_slug
    suffix = 1
    while await db.scalar(
        select(AdminOrganization.id).where(AdminOrganization.slug == slug)
    ):
        suffix += 1
        slug = f"{base_slug[:90]}-{suffix}"

    roles = await ensure_admin_roles(db)
    organization = AdminOrganization(
        name=organization_name.strip(),
        slug=slug,
        is_active=True,
    )
    db.add(organization)
    await db.flush()
    user = User(
        hospital_id=None,
        admin_organization_id=organization.id,
        email=email.lower(),
        hashed_password=hash_password(password),
        full_name=full_name.strip(),
        role_id=roles["company_owner"].id,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def rotate_refresh_token(db: AsyncSession, raw_refresh_token: str) -> tuple[str, str]:
    """Validates a refresh token, revokes it, and issues a new access+refresh pair.
    Rotation (not reuse) means a stolen refresh token has a one-shot window."""
    token_hash = hash_refresh_token(raw_refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()

    if stored is None or stored.revoked or stored.expires_at < datetime.now(timezone.utc):
        raise AuthError("Refresh token invalid, expired, or already used")

    stored.revoked = True  # one-time use

    user_result = await db.execute(
        select(User)
        .options(selectinload(User.role).selectinload(Role.permissions))
        .where(User.id == stored.user_id)
    )
    user = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthError("User no longer active")
    if user.admin_organization_id:
        organization = await db.get(AdminOrganization, user.admin_organization_id)
        if organization is None or not organization.is_active:
            raise AuthError("Administration organization is inactive")
    elif user.hospital_id:
        hospital = await db.get(Hospital, user.hospital_id)
        if hospital is None or not hospital.is_active:
            raise AuthError("Hospital is inactive")
    else:
        raise AuthError("User is not assigned to an account")

    return await issue_tokens(db, user)


async def revoke_refresh_token(db: AsyncSession, raw_refresh_token: str) -> None:
    token_hash = hash_refresh_token(raw_refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()
    if stored is not None:
        stored.revoked = True
        await db.commit()


async def accept_invitation(
    db: AsyncSession, *, raw_token: str, password: str
) -> tuple[User, str | None, str | None, str | None]:
    token_hash = hash_invitation_token(raw_token)
    invitation = await db.scalar(
        select(UserInvitation).where(UserInvitation.token_hash == token_hash)
    )
    now = datetime.now(timezone.utc)
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.expires_at < now
        or (
            invitation.admin_organization_id is None
            and invitation.hospital_id is None
        )
    ):
        raise AuthError("Invitation is invalid, expired, or already used")

    scope_filter = (
        User.admin_organization_id == invitation.admin_organization_id
        if invitation.admin_organization_id
        else User.hospital_id == invitation.hospital_id
    )
    existing = await db.scalar(
        select(User).where(
            scope_filter,
            func.lower(User.email) == invitation.email.lower(),
        )
    )
    if existing is not None:
        raise AuthError("An account already exists for this invitation")

    organization = None
    hospital = None
    if invitation.admin_organization_id:
        organization = await db.get(
            AdminOrganization, invitation.admin_organization_id
        )
        if organization is None or not organization.is_active:
            raise AuthError("Administration organization is inactive")
    else:
        hospital = await db.get(Hospital, invitation.hospital_id)
        if hospital is None or not hospital.is_active:
            raise AuthError("Hospital workspace is inactive")

    user = User(
        hospital_id=invitation.hospital_id,
        admin_organization_id=invitation.admin_organization_id,
        email=invitation.email,
        hashed_password=hash_password(password),
        full_name=invitation.full_name,
        role_id=invitation.role_id,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    if invitation.admin_organization_id:
        access_rows = (
            await db.execute(
                select(InvitationClientAccess).where(
                    InvitationClientAccess.invitation_id == invitation.id
                )
            )
        ).scalars().all()
        for access in access_rows:
            db.add(
                UserClientAccess(
                    user_id=user.id,
                    hospital_id=access.hospital_id,
                    can_manage=access.can_manage,
                    granted_by=invitation.invited_by,
                )
            )
    invitation.accepted_at = now
    await db.commit()
    await db.refresh(user)
    if hospital:
        from app.services.doctor_service import workspace_path

        return user, None, workspace_path(hospital), hospital.code
    return user, organization.slug, None, None
