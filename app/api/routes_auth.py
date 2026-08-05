import hmac
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.core.config import settings
from app.schemas.auth import (
    AcceptInvitationRequest,
    AcceptInvitationResponse,
    ClinicalLoginRequest,
    ClinicalHospitalCodeRequest,
    ClinicalHospitalCodeResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    SignupRequest,
    TokenResponse,
)
from app.schemas.common import ErrorResponse
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _set_session_cookies(
    response: Response, access_token: str, refresh_token: str
) -> str:
    csrf_token = secrets.token_urlsafe(32)
    common = {
        "secure": settings.SESSION_COOKIE_SECURE,
        "httponly": True,
        "samesite": settings.SESSION_COOKIE_SAMESITE,
    }
    response.set_cookie(
        settings.ACCESS_COOKIE_NAME,
        access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/api/v1",
        **common,
    )
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path="/api/v1/auth",
        **common,
    )
    response.set_cookie(
        settings.CSRF_COOKIE_NAME,
        csrf_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path="/api/v1",
        **common,
    )
    return csrf_token


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(settings.ACCESS_COOKIE_NAME, path="/api/v1")
    response.delete_cookie(settings.REFRESH_COOKIE_NAME, path="/api/v1/auth")
    response.delete_cookie(settings.CSRF_COOKIE_NAME, path="/api/v1")


def _cookie_refresh_token(request: Request, payload_token: str | None) -> str:
    if payload_token:
        return payload_token
    refresh_token = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    csrf_header = request.headers.get("X-CSRF-Token", "")
    csrf_cookie = request.cookies.get(settings.CSRF_COOKIE_NAME, "")
    if (
        not refresh_token
        or not csrf_header
        or not csrf_cookie
        or not hmac.compare_digest(csrf_header, csrf_cookie)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
        )
    return refresh_token


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in to administration or a clinical tenant",
    description=(
        "For administration login, send email and password. For doctor/clinical "
        "testing, also send hospital_code; the returned JWT is scoped to that hospital."
    ),
    response_description="New bearer access token and refresh token.",
    responses={401: {"model": ErrorResponse, "description": "Invalid credentials or hospital code."}},
    operation_id="login",
)
async def login(payload: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    try:
        if payload.hospital_code:
            user = await auth_service.authenticate_clinical_user(
                db,
                email=payload.email,
                password=payload.password,
                hospital_code=payload.hospital_code.strip().upper(),
            )
        else:
            user = await auth_service.authenticate_admin_user(
                db, email=payload.email, password=payload.password
            )
        access_token, refresh_token = await auth_service.issue_tokens(db, user)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    csrf_token = _set_session_cookies(response, access_token, refresh_token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, csrf_token=csrf_token)


@router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create the administration company and owner account",
    operation_id="signup_admin_owner",
)
async def signup(payload: SignupRequest, response: Response, db: AsyncSession = Depends(get_db)):
    try:
        user = await auth_service.signup_admin_owner(
            db,
            organization_name=payload.organization_name,
            full_name=payload.full_name,
            email=payload.email,
            password=payload.password,
        )
        access_token, refresh_token = await auth_service.issue_tokens(db, user)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    csrf_token = _set_session_cookies(response, access_token, refresh_token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, csrf_token=csrf_token)


@router.post(
    "/clinical/hospital-code",
    response_model=ClinicalHospitalCodeResponse,
    summary="Find a clinical user's hospital code",
    description=(
        "Looks up the single active hospital workspace associated with a clinical "
        "user's work email."
    ),
    responses={
        404: {
            "model": ErrorResponse,
            "description": "No unique active hospital workspace was found.",
        }
    },
    operation_id="clinical_hospital_code",
)
async def clinical_hospital_code(
    payload: ClinicalHospitalCodeRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        hospital_code = await auth_service.find_clinical_hospital_code(
            db, email=payload.email
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ClinicalHospitalCodeResponse(hospital_code=hospital_code)


@router.post(
    "/clinical/login",
    response_model=TokenResponse,
    summary="Log in to a clinical tenant for EMR testing",
    description=(
        "Explicit clinical-login endpoint. Accepts doctor email, password, and "
        "hospital code, then returns a hospital-scoped JWT for EMR endpoints."
    ),
    operation_id="clinical_login",
)
async def clinical_login(
    payload: ClinicalLoginRequest, response: Response, db: AsyncSession = Depends(get_db)
):
    try:
        user = await auth_service.authenticate_clinical_user(
            db,
            email=payload.email,
            password=payload.password,
            hospital_code=payload.hospital_code,
        )
        access_token, refresh_token = await auth_service.issue_tokens(db, user)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    csrf_token = _set_session_cookies(response, access_token, refresh_token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, csrf_token=csrf_token)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate a refresh token",
    description=(
        "Consumes a valid refresh token and returns a new access/refresh pair. "
        "The submitted refresh token cannot be reused."
    ),
    responses={401: {"model": ErrorResponse, "description": "Invalid or expired refresh token."}},
    operation_id="refresh_tokens",
)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    submitted_token = _cookie_refresh_token(request, payload.refresh_token)
    try:
        access_token, refresh_token = await auth_service.rotate_refresh_token(
            db, submitted_token
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    csrf_token = _set_session_cookies(response, access_token, refresh_token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, csrf_token=csrf_token)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out",
    description="Revokes the supplied refresh token. This operation is idempotent.",
    response_description="Refresh token revoked or already absent.",
    operation_id="logout",
)
async def logout(
    payload: LogoutRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    try:
        submitted_token = _cookie_refresh_token(request, payload.refresh_token)
        await auth_service.revoke_refresh_token(db, submitted_token)
    finally:
        _clear_session_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT


@router.post(
    "/invitations/accept",
    response_model=AcceptInvitationResponse,
    summary="Complete an invited user's account setup",
    operation_id="accept_invitation",
)
async def accept_invitation(
    payload: AcceptInvitationRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        user, organization_slug, workspace_path, hospital_code = (
            await auth_service.accept_invitation(
                db,
                raw_token=payload.token,
                password=payload.password,
            )
        )
    except auth_service.AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return AcceptInvitationResponse(
        organization_slug=organization_slug,
        workspace_path=workspace_path,
        hospital_code=hospital_code,
        email=user.email,
    )
