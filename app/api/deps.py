from dataclasses import dataclass
import hmac

from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_token
from app.core.config import settings


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
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> CurrentUser:
    cookie_authenticated = credentials is None
    encoded_token = (
        request.cookies.get(settings.ACCESS_COOKIE_NAME)
        if cookie_authenticated
        else credentials.credentials
    )
    if not encoded_token or (
        credentials is not None and credentials.scheme.lower() != "bearer"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(encoded_token)
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

    if cookie_authenticated and request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        csrf_header = request.headers.get("X-CSRF-Token", "")
        csrf_cookie = request.cookies.get(settings.CSRF_COOKIE_NAME, "")
        if not csrf_header or not csrf_cookie or not hmac.compare_digest(csrf_header, csrf_cookie):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF validation failed",
            )

    return CurrentUser(
        user_id=payload["sub"],
        hospital_id=payload.get("hospital_id"),
        admin_organization_id=payload.get("admin_organization_id"),
        user_type=payload.get("user_type", "clinical"),
        role=payload["role"],
        permissions=set(payload.get("permissions", [])),
    )
