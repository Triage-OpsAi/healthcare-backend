import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.emr import StructuredNote

PatientSectionKey = Literal[
    "summary",
    "timeline",
    "clinical",
    "medications",
    "diagnoses",
    "reports",
    "documents",
    "handover",
]


class AudioAccess(BaseModel):
    url: str
    content_type: str
    expires_in: int


class PatientDetailsUpdateRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    age: int | None = Field(default=None, ge=0, le=130)
    phone: str | None = Field(default=None, max_length=20)
    gender: str | None = Field(default=None, max_length=20)
    date_of_birth: datetime | None = None
    encounter_number: str | None = Field(default=None, max_length=50)
    ward_number: str | None = Field(default=None, max_length=50)
    bed_number: str | None = Field(default=None, max_length=50)


class PatientDetailsResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    age: int | None
    phone: str | None
    gender: str | None
    date_of_birth: datetime | None
    encounter_number: str | None
    ward_number: str | None
    bed_number: str | None


class VisitCreateRequest(BaseModel):
    encounter_number: str | None = Field(default=None, max_length=50)
    department: str | None = Field(default=None, max_length=100)
    ward_number: str | None = Field(default=None, max_length=50)
    bed_number: str | None = Field(default=None, max_length=50)


class PatientVisitSummary(BaseModel):
    id: uuid.UUID
    visit_number: int
    encounter_number: str | None
    department: str | None
    ward_number: str | None
    bed_number: str | None
    status: str
    doctor_name: str
    summary: str
    record_count: int
    created_at: datetime


class ReportCreateRequest(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    content_type: str = Field(min_length=3, max_length=100)
    file_size: int = Field(gt=0, le=25 * 1024 * 1024)
    capture_source: Literal["file", "camera"] = "file"


class ReportUploadResponse(BaseModel):
    report_id: uuid.UUID
    upload_url: str
    content_type: str
    expires_in: int


class ReportCompleteRequest(BaseModel):
    etag: str | None = Field(default=None, max_length=255)


class PatientReportSummary(BaseModel):
    id: uuid.UUID
    title: str
    content_type: str
    capture_source: str
    status: str
    document_type: str | None
    summary: str | None
    key_findings: list[str] = Field(default_factory=list)
    extracted_details: dict | None
    quality_message: str | None
    created_at: datetime


class MedicationCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    dosage: str | None = Field(default=None, max_length=100)
    frequency: str | None = Field(default=None, max_length=100)
    route: str | None = Field(default=None, max_length=100)
    duration: str | None = Field(default=None, max_length=100)
    instructions: str | None = Field(default=None, max_length=2000)


class PatientMedicationSummary(BaseModel):
    id: uuid.UUID
    name: str
    dosage: str | None
    frequency: str | None
    route: str | None
    duration: str | None
    instructions: str | None
    is_active: bool
    created_at: datetime


class AdditionalRecordCreateRequest(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    department: str | None = Field(default=None, max_length=100)
    subjective: str = Field(default="", max_length=8000)
    objective: str = Field(default="", max_length=8000)
    assessment: str = Field(default="", max_length=8000)
    plan: str = Field(default="", max_length=8000)


class PatientRecordSummary(BaseModel):
    id: uuid.UUID
    encounter_id: uuid.UUID
    department: str | None
    status: str
    source_language: str
    structured_note: StructuredNote | None
    encounter_summary: str
    captured_by: str
    audio_available: bool
    created_at: datetime


class DischargeCreateRequest(BaseModel):
    content_type: str = Field(min_length=3, max_length=100)
    file_size: int = Field(gt=0, le=100 * 1024 * 1024)
    language_code: str = Field(default="unknown", max_length=10)


class DischargeUploadResponse(BaseModel):
    job_id: uuid.UUID
    upload_url: str
    content_type: str
    expires_in: int


class DischargeCompleteRequest(BaseModel):
    etag: str | None = Field(default=None, max_length=255)


class DischargeSummaryItem(BaseModel):
    id: uuid.UUID
    status: str
    source_language: str
    translated_instructions: str | None
    summary_data: dict | None
    audio_available: bool
    pdf_available: bool
    error_message: str | None
    created_at: datetime


class HandoverCreateRequest(BaseModel):
    content_type: str = Field(min_length=3, max_length=100)
    file_size: int = Field(gt=0, le=100 * 1024 * 1024)
    language_code: str = Field(default="unknown", max_length=10)
    handed_over_to: uuid.UUID | None = None


class HandoverUploadResponse(BaseModel):
    job_id: uuid.UUID
    upload_url: str
    content_type: str
    expires_in: int


class HandoverCompleteRequest(BaseModel):
    etag: str | None = Field(default=None, max_length=255)


class HandoverRecipientUpdateRequest(BaseModel):
    handed_over_to: uuid.UUID


class HandoverSummaryItem(BaseModel):
    id: uuid.UUID
    status: str
    source_language: str
    translated_instructions: str | None
    summary_data: dict | None
    captured_by: str
    handed_over_to: str | None
    recorded_at: datetime
    audio_available: bool
    error_message: str | None


class PatientSectionUpdateRequest(BaseModel):
    content_override: str = Field(default="", max_length=12000)


class PatientSectionItemUpdateRequest(BaseModel):
    content_override: str = Field(min_length=1, max_length=12000)


class PatientSectionReviewSummary(BaseModel):
    section_key: PatientSectionKey
    content_override: str | None
    item_overrides: dict[str, str] = Field(default_factory=dict)
    deleted_items: list[str] = Field(default_factory=list)
    is_deleted: bool
    is_approved: bool
    approved_by: str | None
    approved_at: datetime | None
    updated_by: str
    updated_at: datetime


class PatientChart(BaseModel):
    records: list[PatientRecordSummary]
    reports: list[PatientReportSummary]
    medications: list[PatientMedicationSummary]
    discharge_summaries: list[DischargeSummaryItem] = Field(default_factory=list)
    handovers: list[HandoverSummaryItem] = Field(default_factory=list)
    section_reviews: list[PatientSectionReviewSummary] = Field(default_factory=list)
    approval_percentage: int = 0
    visits: list[PatientVisitSummary] = Field(default_factory=list)
    selected_visit: PatientVisitSummary | None = None
