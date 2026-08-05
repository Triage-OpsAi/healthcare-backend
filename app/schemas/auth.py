from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.admin import AcceptInvitationRequest, AcceptInvitationResponse


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "email": "owner@company.com",
                    "password": "your-password",
                },
                {
                    "email": "doctor@example.com",
                    "password": "Doctor@123",
                    "hospital_code": "RAINBOW-BLR",
                },
            ]
        }
    )

    email: EmailStr = Field(description="Administration or clinical user email.")
    password: str = Field(description="User password.", min_length=1)
    hospital_code: str | None = Field(
        default=None,
        description=(
            "Clinical hospital tenant code. Include this for doctor/clinical login; "
            "omit it for administration login."
        ),
        examples=["RAINBOW-BLR"],
        min_length=1,
    )


class ClinicalHospitalCodeRequest(BaseModel):
    email: EmailStr


class ClinicalHospitalCodeResponse(BaseModel):
    hospital_code: str


class ClinicalLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    hospital_code: str = Field(
        description="Clinical hospital tenant code.", examples=["RAINBOW-BLR"], min_length=1
    )


class SignupRequest(BaseModel):
    organization_name: str = Field(min_length=2, max_length=255)
    full_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str = Field(description="Short-lived JWT for the Swagger Authorize dialog.")
    refresh_token: str = Field(description="Opaque token used only by refresh and logout.")
    token_type: str = Field(default="bearer", examples=["bearer"])
    csrf_token: str = Field(
        description="CSRF token required for state-changing requests made with session cookies."
    )


class RefreshRequest(BaseModel):
    refresh_token: str | None = Field(
        default=None,
        description="Optional for cookie sessions; legacy API clients may submit the refresh token.",
    )


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(
        default=None,
        description="Optional for cookie sessions; legacy API clients may submit the refresh token.",
    )
