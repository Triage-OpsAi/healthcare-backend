"""API contracts for Ward Voice."""
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class TaskSummary(BaseModel):
    id: uuid.UUID
    bed_id: uuid.UUID
    bed_number: str
    patient_id: uuid.UUID
    patient_name: str
    patient_age: int | None
    protocol: str | None
    doctor_name: str | None
    title: str
    task_type: str
    status: str
    due_at: datetime
    completed_at: datetime | None


class BedSummary(BaseModel):
    id: uuid.UUID
    bed_number: str
    patient_id: uuid.UUID | None
    patient_name: str | None
    patient_age: int | None
    protocol: str | None
    nurse_name: str | None
    last_entry_at: datetime | None
    next_due_at: datetime | None
    fluid_balance_ml: int
    completed_tasks: int
    total_tasks: int
    status: str


class KPI(BaseModel):
    due_next_hour: int
    overdue: int
    done_this_shift: int
    on_time_percentage: int


class WardCard(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    patient_count: int


class ComplianceSummary(BaseModel):
    on_time_percentage: int
    closed_by_08_percentage: int
    iv_checks_percentage: int
    arithmetic_errors: int


class HandoverLine(BaseModel):
    bed_id: uuid.UUID
    bed_number: str
    patient_name: str
    text: str
    priority: Literal["routine", "attention", "urgent"]


class AuditSummary(BaseModel):
    id: uuid.UUID
    action: str
    resource_type: str
    patient_id: uuid.UUID | None
    user_name: str
    details: dict | None
    created_at: datetime


class WardVoiceOverview(BaseModel):
    ward_id: uuid.UUID | None
    ward_name: str | None
    ward_code: str | None
    kpis: KPI
    tasks: list[TaskSummary]
    beds: list[BedSummary]
    handover: list[HandoverLine]
    compliance: ComplianceSummary
    audit: list[AuditSummary]
    pending_countersigns: int


class VoiceCaptureCreate(BaseModel):
    bed_id: uuid.UUID
    task_id: uuid.UUID | None = None
    content_type: str = Field(min_length=3, max_length=100)
    file_size: int = Field(gt=0)
    language_code: str = Field(default="unknown", max_length=10)


class VoiceCaptureUpload(BaseModel):
    capture_id: uuid.UUID
    upload_url: str
    object_key: str
    content_type: str
    expires_in: int


class VoiceCaptureComplete(BaseModel):
    etag: str | None = None


class ProvisionalObservation(BaseModel):
    observation_type: str
    value_numeric: float | None = None
    value_text: str | None = None
    unit: str | None = None
    confidence: float | None = None
    requires_countersign: bool = False


class VoiceCaptureResult(BaseModel):
    capture_id: uuid.UUID
    status: str
    raw_transcript: str | None
    translated_text: str | None
    observations: list[ProvisionalObservation]
    error_message: str | None = None


class ConfirmObservation(BaseModel):
    observation_type: str
    value_numeric: float | None = None
    value_text: str | None = None
    unit: str | None = None
    requires_countersign: bool = False


class ConfirmCaptureRequest(BaseModel):
    observations: list[ConfirmObservation]


class FluidEntryCreate(BaseModel):
    bed_id: uuid.UUID
    direction: Literal["intake", "output"]
    category: Literal["oral", "iv", "vomit", "drainage", "urine", "stool", "other"]
    amount_ml: int = Field(gt=0, le=10000)
    occurred_at: datetime
    notes: str | None = Field(default=None, max_length=255)


class IVInfusionCreate(BaseModel):
    bed_id: uuid.UUID
    fluid_name: str = Field(min_length=1, max_length=120)
    rate_ml_per_hour: int = Field(gt=0, le=5000)
    started_at: datetime


class FluidEntrySummary(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    direction: str
    category: str
    amount_ml: int
    source: str
    notes: str | None
    recorded_by: str


class IVInfusionSummary(BaseModel):
    id: uuid.UUID
    fluid_name: str
    rate_ml_per_hour: int
    started_at: datetime
    stopped_at: datetime | None
    calculated_ml: int


class FluidChartResponse(BaseModel):
    bed_id: uuid.UUID
    bed_number: str
    patient_id: uuid.UUID
    patient_name: str
    patient_age: int | None
    protocol: str | None
    chart_date: date
    intake_ml: int
    output_ml: int
    iv_running_ml: int
    balance_ml: int
    entries: list[FluidEntrySummary]
    infusions: list[IVInfusionSummary]
    is_closed: bool
    closed_at: datetime | None


class CloseChartRequest(BaseModel):
    chart_date: date


class TaskUpdate(BaseModel):
    status: Literal["due", "in_progress", "completed", "cancelled"]


class CountersignSummary(BaseModel):
    id: uuid.UUID
    capture_id: uuid.UUID
    bed_number: str
    patient_name: str
    observation_type: str
    value_numeric: float | None
    value_text: str | None
    unit: str | None
    confirmed_by: str
    confirmed_at: datetime


class ConsumableSummary(BaseModel):
    id: uuid.UUID
    capture_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    bed_number: str
    item_name: str
    quantity_numeric: float | None
    quantity_text: str | None
    unit: str | None
    recorded_by: str
    recorded_at: datetime
    approval_status: Literal["pending", "approved"]
    approved_by: str | None
    approved_at: datetime | None
