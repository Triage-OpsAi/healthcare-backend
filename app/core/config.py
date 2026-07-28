import json
from pathlib import Path

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# The platform administrator owns the SMTP configuration. Load those values as
# a fallback so the API can deliver both platform and clinical invitations
# without duplicating credentials across repositories.
PLATFORM_ENV = Path(__file__).resolve().parents[3] / "healthai-platform-2" / ".env"
if PLATFORM_ENV.exists():
    load_dotenv(PLATFORM_ENV, override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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


settings = Settings()
