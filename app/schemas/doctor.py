import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class DoctorIdentity(BaseModel):
    id: uuid.UUID
    full_name: str
    email: EmailStr
    role: str
    permissions: list[str]


class OrganizationInfo(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    email: EmailStr
    gst_number: str | None = None
    contact_name: str
    contact_email: EmailStr
    contact_mobile: str | None = None
    hq_location: str | None = None
    is_network_hospital: bool = False
    parent_name: str | None = None


class DoctorWorkspace(BaseModel):
    organization: OrganizationInfo
    current_user: DoctorIdentity
    workspace_slug: str
    encrypted_client_id: str
    workspace_path: str


class DoctorRecordSummary(BaseModel):
    id: uuid.UUID
    encounter_id: uuid.UUID
    patient_name: str
    patient_id: uuid.UUID
    patient_reference: str
    serial_number: int
    age: int | None
    subject: str
    doctor_name: str
    nurses: list[str] = Field(default_factory=list)
    status: str
    created_at: datetime


class PatientDashboardSummary(BaseModel):
    id: uuid.UUID
    latest_record_id: uuid.UUID | None
    encounter_id: uuid.UUID | None
    encounter_number: str | None
    ward_number: str | None
    bed_number: str | None
    patient_name: str
    patient_reference: str
    serial_number: int
    age: int | None
    gender: str | None
    phone: str | None
    subject: str
    doctor_name: str | None
    nurses: list[str] = Field(default_factory=list)
    status: str
    created_at: datetime
    last_visit_at: datetime | None


class ClinicalRoleSummary(BaseModel):
    id: uuid.UUID
    name: str
    permissions: list[str]


class ClinicalUserSummary(BaseModel):
    id: uuid.UUID
    full_name: str
    email: EmailStr
    role_id: uuid.UUID
    role: str
    permissions: list[str]
    is_active: bool
    is_current_user: bool = False


class ClinicalInvitationSummary(BaseModel):
    id: uuid.UUID
    full_name: str
    email: EmailStr
    role: str
    expires_at: datetime
    created_at: datetime


class InviteClinicalUserRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    role_id: uuid.UUID

    @field_validator("full_name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()


class UpdateClinicalUserRequest(BaseModel):
    role_id: uuid.UUID
    is_active: bool = True


class ClinicalInvitationResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    expires_at: datetime
    delivery: Literal["email", "development_outbox"]
    magic_link: str | None = None


class NetworkHospitalCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    place: str = Field(min_length=2, max_length=255)
    email: EmailStr
    contact_name: str = Field(min_length=2, max_length=255)
    contact_email: EmailStr

    @field_validator("name", "place", "contact_name", mode="before")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()


class NetworkHospitalSummary(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    place: str
    email: EmailStr
    contact_name: str
    contact_email: EmailStr
    created_at: datetime
    is_active: bool
    workspace_path: str


class CreateEncounterRequest(BaseModel):
    patient_name: str = Field(min_length=2, max_length=255)
    patient_reference: str | None = Field(default=None, max_length=50)
    date_of_birth: date | None = None
    age: int | None = Field(default=None, ge=0, le=130)
    gender: str | None = Field(default=None, max_length=20)
    phone: str | None = Field(default=None, max_length=20)
    department: str | None = Field(default=None, max_length=100)

    @field_validator("patient_name", mode="before")
    @classmethod
    def normalize_patient_name(cls, value: str) -> str:
        return value.strip()


class CreatedEncounter(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    department: str | None
    status: str
    created_at: datetime
