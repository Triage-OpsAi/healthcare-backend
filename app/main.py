import logging

import asyncpg
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routes_admin import (
    api_key_router,
    client_router,
    location_router,
    service_router,
    user_router,
)
from app.api.routes_auth import router as auth_router
from app.api.routes_audit import router as audit_router
from app.api.routes_doctor import router as doctor_router
from app.api.routes_emr import router as emr_router
from app.api.routes_patient_chart import router as patient_chart_router
from app.api.routes_documentation import router as documentation_router
from app.api.routes_ward_voice import router as ward_voice_router
from app.api.routes_clinical_documents import router as clinical_documents_router
from app.core.config import settings
from app.schemas.common import HealthResponse
from app.middleware.security import RateLimitMiddleware, SecurityHeadersMiddleware

logger = logging.getLogger(__name__)

OPENAPI_TAGS = [
    {
        "name": "Authentication",
        "description": "Login, rotate refresh tokens, and revoke sessions.",
    },
    {
        "name": "EMR Records",
        "description": (
            "Upload clinical dictation, retrieve structured records, and complete the "
            "doctor-review approval gate. Protected operations require a bearer access token."
        ),
    },
    {
        "name": "Clients",
        "description": "Client onboarding, visibility-scoped listing, and administration.",
    },
    {
        "name": "Users",
        "description": "Internal-user invitations, roles, and client-access assignments.",
    },
    {
        "name": "Locations",
        "description": "Cascading Indian state, district, and city master data.",
    },
    {
        "name": "Doctor Portal",
        "description": (
            "Clinical workspace profile, records dashboard, users, invitations, "
            "network hospitals, and encounter creation."
        ),
    },
    {
        "name": "Services",
        "description": "Workspace service catalog, pricing, features, and GST configuration.",
    },
    {
        "name": "API Keys",
        "description": "One-time generation and masked history for service-scoped API keys.",
    },
    {"name": "Operations", "description": "Service readiness and health endpoints."},
]

app = FastAPI(
    title="Meridian Multi-Tenant Administration API",
    description=(
        "Internal company administration and client-management API, with separate "
        "clinical tenant services.\n\n"
        "### Authentication\n"
        "1. Create the company owner with **POST /api/v1/auth/signup**, or call "
        "**POST /api/v1/auth/login** for an existing internal user.\n"
        "2. Copy `access_token` from the response.\n"
        "3. Click **Authorize** and paste the token."
    ),
    version="0.1.0",
    openapi_tags=OPENAPI_TAGS,
    docs_url=None if settings.ENVIRONMENT.lower() == "production" else "/docs",
    redoc_url=None if settings.ENVIRONMENT.lower() == "production" else "/redoc",
    openapi_url=None if settings.ENVIRONMENT.lower() == "production" else "/openapi.json",
    contact={"name": "Meridian Platform Team"},
    license_info={"name": "Proprietary"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.TRUSTED_HOSTS)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(audit_router, prefix="/api/v1")
app.include_router(emr_router, prefix="/api/v1")
app.include_router(patient_chart_router, prefix="/api/v1")
app.include_router(doctor_router, prefix="/api/v1")
app.include_router(client_router, prefix="/api/v1")
app.include_router(user_router, prefix="/api/v1")
app.include_router(location_router, prefix="/api/v1")
app.include_router(service_router, prefix="/api/v1")
app.include_router(api_key_router, prefix="/api/v1")
app.include_router(ward_voice_router, prefix="/api/v1")
app.include_router(documentation_router, prefix="/api/v1")
app.include_router(clinical_documents_router, prefix="/api/v1")


@app.exception_handler(SQLAlchemyError)
@app.exception_handler(asyncpg.PostgresError)
async def database_exception_handler(
    _request: Request, exc: SQLAlchemyError | asyncpg.PostgresError
) -> JSONResponse:
    """Return a stable API error when the configured database is unavailable."""
    logger.error("Database request failed: %s", type(exc).__name__)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": "Database unavailable. Check DATABASE_URL and database credentials.",
            "code": "database_unavailable",
        },
    )


@app.get(
    "/healthz",
    tags=["Operations"],
    summary="Check service health",
    description="Returns HTTP 200 when the API process is running.",
    response_model=HealthResponse,
    operation_id="health_check",
)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")
