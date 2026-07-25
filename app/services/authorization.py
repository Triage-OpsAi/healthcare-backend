"""
Authorization service.

Every protected endpoint declares the permission it requires, e.g.:

    @router.post("/emr/records")
    async def create_record(
        current_user: CurrentUser = Depends(require_permission("emr:create")),
    ):
        ...

`require_permission` returns a FastAPI dependency that:
  1. Resolves the caller via the JWT (permissions are already embedded in
     the token -- see core/security.py -- so this is an in-memory check,
     not a DB query, which is what lets this scale to high request volume).
  2. Rejects with 403 if the required permission isn't present.
  3. Returns the CurrentUser so route handlers get it for free.

Suggested baseline permission codes for this domain:
    emr:create              - upload audio/text, create a draft record
    emr:read                - view EMR records within own hospital
    emr:review               - edit/approve a pending record (doctor role)
    emr:code:override        - manually pick a different SNOMED/ICD code
    emr:sync_to_ehr           - push an approved record to the hospital EMR
    users:manage              - create/deactivate users (admin role)
    audit:read                 - view audit logs (compliance officer)
"""
from fastapi import Depends, HTTPException, status

from app.api.deps import CurrentUser, get_current_user


def require_permission(permission_code: str):
    async def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if permission_code not in current_user.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {permission_code}",
            )
        return current_user

    return _check


def require_admin_permission(permission_code: str):
    async def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.user_type != "admin" or not current_user.admin_organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This endpoint is restricted to internal administration users",
            )
        if permission_code not in current_user.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {permission_code}",
            )
        return current_user

    return _check


def require_any_permission(*permission_codes: str):
    """For endpoints reachable by more than one role, e.g. either a doctor
    or a records-admin can view a record."""
    async def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not (set(permission_codes) & current_user.permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(permission_codes)}",
            )
        return current_user

    return _check


def assert_same_hospital(current_user: CurrentUser, resource_hospital_id: str) -> None:
    """Cross-tenant access guard -- call this any time a route loads a
    resource by ID, to stop a valid token from user A's hospital reading
    hospital B's patient data even if the resource ID is guessed correctly."""
    if current_user.hospital_id is None or str(current_user.hospital_id) != str(resource_hospital_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Resource does not belong to your hospital",
        )
