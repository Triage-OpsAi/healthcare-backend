from dataclasses import dataclass

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_token


@dataclass
class CurrentUser:
    """Everything downstream code needs about the caller, decoded once per request
    directly from the JWT -- no DB hit required for this step."""
    user_id: str
    hospital_id: str | None
    admin_organization_id: str | None
    user_type: str
    role: str
    permissions: set[str]


bearer_scheme = HTTPBearer(
    auto_error=False,
    description="JWT access token returned by POST /api/v1/auth/login.",
)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not an access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return CurrentUser(
        user_id=payload["sub"],
        hospital_id=payload.get("hospital_id"),
        admin_organization_id=payload.get("admin_organization_id"),
        user_type=payload.get("user_type", "clinical"),
        role=payload["role"],
        permissions=set(payload.get("permissions", [])),
    )
