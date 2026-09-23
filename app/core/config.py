import json
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]
# Later dotenv files override earlier files; process environment wins over all
# files. The sibling frontend is a local-development fallback only. Deployed
# containers must receive SMTP settings through their own environment.
ENV_FILES = (
    BACKEND_ROOT.parent / "healthai-platform-2" / ".env",
    BACKEND_ROOT.parent / "EMR-NextApp" / ".env",
    BACKEND_ROOT / ".env",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    # Database
    DATABASE_URL: str

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def use_async_postgres_driver(cls, value: str) -> str:
        """Make ordinary Postgres/Supabase URLs compatible with AsyncEngine."""
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        return value

    # JWT
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ACCESS_COOKIE_NAME: str = "meridian_access"
    REFRESH_COOKIE_NAME: str = "meridian_refresh"
    CSRF_COOKIE_NAME: str = "meridian_csrf"
    SESSION_COOKIE_SECURE: bool = False
    SESSION_COOKIE_SAMESITE: str = "lax"

    # Sarvam AI
    SARVAM_API_KEY: str
    SARVAM_BASE_URL: str = "https://api.sarvam.ai"
    SARVAM_STT_MODEL: str = "saaras:v3"

    # OpenAI LLM used for note structuring + entity extraction (see emr_service.py)
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-5.6-sol"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_REASONING_EFFORT: str = "medium"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    RATE_LIMIT_ENABLED: bool = True
    MAX_REQUEST_BYTES: int = 110 * 1024 * 1024
    RATE_LIMIT_ENABLED: bool = True
    MAX_REQUEST_BYTES: int = 110 * 1024 * 1024

    # AWS S3 private object storage.
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "ap-south-1"
    AWS_S3_BUCKET: str = ""
    AWS_S3_PRESIGN_EXPIRE_SECONDS: int = 900
    VOICE_UPLOAD_MAX_BYTES: int = 100 * 1024 * 1024

    # Administration application
    FRONTEND_URL: str = "http://localhost:3000"
    DOCTOR_FRONTEND_URL: str = "http://localhost:3001"
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:3001"]
    TRUSTED_HOSTS: list[str] = [
        "healthcare-backend.triage-ops.com",
        "localhost",
        "127.0.0.1",
        "testserver",
    ]
    INVITATION_EXPIRE_HOURS: int = 48

    # Optional SMTP delivery. Invitations are written to the application log
    # in development when SMTP_HOST is empty.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "no-reply@meridianhealth.ai"
    SMTP_FROM_NAME: str = "Meridian Health AI"
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    SMTP_TIMEOUT_SECONDS: int = 20
    SMTP_RETRY_ATTEMPTS: int = 3
    SMTP_RETRY_BASE_SECONDS: float = 1

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                return json.loads(value)
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    ENVIRONMENT: str = "development"

    @field_validator("SESSION_COOKIE_SAMESITE")
    @classmethod
    def validate_cookie_samesite(cls, value: str) -> str:
        value = value.lower()
        if value not in {"lax", "strict", "none"}:
            raise ValueError("SESSION_COOKIE_SAMESITE must be lax, strict, or none")
        return value

    @field_validator("TRUSTED_HOSTS", mode="before")
    @classmethod
    def parse_trusted_hosts(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                return json.loads(value)
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def validate_production_security(self):
        if self.ENVIRONMENT.lower() != "production":
            return self
        errors: list[str] = []
        if len(self.JWT_SECRET_KEY) < 32:
            errors.append("JWT_SECRET_KEY must contain at least 32 characters")
        if self.JWT_ALGORITHM != "HS256":
            errors.append("JWT_ALGORITHM must be HS256")
        if not self.SESSION_COOKIE_SECURE:
            errors.append("SESSION_COOKIE_SECURE must be true")
        if not self.SMTP_HOST:
            errors.append("SMTP_HOST is required so invitation tokens are never logged")
        for name, value in (
            ("FRONTEND_URL", self.FRONTEND_URL),
            ("DOCTOR_FRONTEND_URL", self.DOCTOR_FRONTEND_URL),
        ):
            if not value.startswith("https://"):
                errors.append(f"{name} must use HTTPS")
        if not self.CORS_ORIGINS or any(
            origin == "*" or not origin.startswith("https://")
            for origin in self.CORS_ORIGINS
        ):
            errors.append("CORS_ORIGINS must contain only explicit HTTPS origins")
        if not self.TRUSTED_HOSTS or "*" in self.TRUSTED_HOSTS:
            errors.append("TRUSTED_HOSTS must contain explicit production hostnames")
        if errors:
            raise ValueError("Unsafe production configuration: " + "; ".join(errors))
        return self


settings = Settings()
