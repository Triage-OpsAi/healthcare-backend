import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _strip(value: str) -> str:
    return value.strip()


class LocationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str | None = None


class ClientPayload(BaseModel):
    client_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    hq_location: str = Field(min_length=2, max_length=255)
    service_start_date: date
    requested_services: list[str] | None = None
    service_ids: list[uuid.UUID] = Field(default_factory=list)
    api_key: str | None = Field(default=None, min_length=8, max_length=500)
    pan_number: str = Field(pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]$")
    gst_number: str = Field(pattern=r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
    complete_address: str = Field(min_length=5, max_length=2000)
    state_id: uuid.UUID
    district_id: uuid.UUID
    city_id: uuid.UUID
    pincode: str = Field(pattern=r"^[1-9][0-9]{5}$")
    contact_name: str = Field(min_length=2, max_length=255)
    contact_email: EmailStr
    contact_mobile: str = Field(pattern=r"^\+?[0-9]{10,15}$")
    emergency_contact_name: str = Field(min_length=2, max_length=255)
    emergency_contact_mobile: str = Field(pattern=r"^\+?[0-9]{10,15}$")

    @field_validator(
        "client_name",
        "hq_location",
        "complete_address",
        "contact_name",
        "emergency_contact_name",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _strip(value)

    @field_validator("pan_number", "gst_number", mode="before")
    @classmethod
    def normalize_compliance_id(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("requested_services", mode="before")
    @classmethod
    def normalize_services(cls, value):
        if value is None:
            return None
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized or None


class ClientSummary(BaseModel):
    id: uuid.UUID
    client_name: str
    code: str
    email: EmailStr
    hq_location: str
    service_start_date: date
    requested_services: list[str] = Field(default_factory=list)
    state: str
    city: str
    is_active: bool
    can_manage: bool


class ClientDetail(ClientSummary):
    pan_number: str
    gst_number: str
    complete_address: str
    state_id: uuid.UUID
    district_id: uuid.UUID
    city_id: uuid.UUID
    district: str
    pincode: str
    contact_name: str
    contact_email: EmailStr
    contact_mobile: str
    emergency_contact_name: str
    emergency_contact_mobile: str
    api_key_hint: str | None
    created_at: datetime
    updated_at: datetime


class ClientInvitationDelivery(BaseModel):
    email: EmailStr
    delivery: Literal["email", "development_outbox", "failed"]
    magic_link: str | None = None
    error: str | None = None


class ClientCreateResponse(BaseModel):
    client: ClientDetail
    invitations: list[ClientInvitationDelivery]


class ClientListResponse(BaseModel):
    items: list[ClientSummary]
    page: int
    page_size: int
    total: int
    total_pages: int


class ClientAccessGrant(BaseModel):
    client_id: uuid.UUID
    can_manage: bool = False


class InviteUserRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    role_id: uuid.UUID
    client_access: list[ClientAccessGrant] = Field(default_factory=list)

    @field_validator("full_name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()


class InvitationResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    expires_at: datetime
    delivery: Literal["email", "development_outbox"]
    magic_link: str | None = None


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=32)
    password: str = Field(min_length=8, max_length=128)


class AcceptInvitationResponse(BaseModel):
    message: str = "Account setup complete. You can now sign in."
    organization_slug: str | None = None
    workspace_path: str | None = None
    hospital_code: str | None = None
    email: EmailStr


class RoleSummary(BaseModel):
    id: uuid.UUID
    name: str
    permissions: list[str]


class UserClientAccessSummary(BaseModel):
    client_id: uuid.UUID
    client_name: str
    can_manage: bool


class UserSummary(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    role_id: uuid.UUID
    role: str
    permissions: list[str]
    is_active: bool
    client_access: list[UserClientAccessSummary]


class UpdateUserAccessRequest(BaseModel):
    role_id: uuid.UUID
    is_active: bool = True
    client_access: list[ClientAccessGrant] = Field(default_factory=list)


class CurrentUserResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    admin_organization_id: uuid.UUID
    organization_name: str
    organization_slug: str
    role: str
    permissions: list[str]


class ServicePayload(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    code: str = Field(pattern=r"^TA[A-Z0-9]{1,4}$", min_length=3, max_length=6)
    about: str = Field(min_length=5, max_length=5000)
    features: list[str] = Field(default_factory=list, max_length=50)
    service_charge: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    gst_included: bool = False
    gst_percentage: Decimal = Field(
        default=Decimal("18"), ge=0, le=100, max_digits=5, decimal_places=2
    )
    is_active: bool = True

    @field_validator("name", "about", mode="before")
    @classmethod
    def normalize_service_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("code", mode="before")
    @classmethod
    def normalize_service_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("features", mode="before")
    @classmethod
    def normalize_features(cls, value):
        if value is None:
            return []
        return [str(item).strip() for item in value if str(item).strip()]


class ServiceResponse(ServicePayload):
    id: uuid.UUID
    gst_amount: Decimal
    total_charge: Decimal
    created_at: datetime
    updated_at: datetime


class GeneratedServiceCode(BaseModel):
    code: str


class ApiKeyCreateRequest(BaseModel):
    service_id: uuid.UUID
    label: str | None = Field(default=None, max_length=100)

    @field_validator("label", mode="before")
    @classmethod
    def normalize_label(cls, value):
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class ApiKeySummary(BaseModel):
    id: uuid.UUID
    service_id: uuid.UUID
    service_name: str
    service_code: str
    label: str | None
    key_hint: str
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


class ApiKeyCreated(ApiKeySummary):
    api_key: str = Field(description="Shown once. Store it securely.")
