import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Medication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Medication name mentioned in the consultation.")
    dosage: str = Field(description="Dosage exactly as documented, or an empty string.")
    frequency: str = Field(description="Frequency exactly as documented, or an empty string.")


class StructuredNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chief_complaint: str
    subjective: str
    objective: str
    assessment: str
    plan: str
    diagnoses: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    medications: list[Medication] = Field(default_factory=list)


class EncounterSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    department: str | None
    status: Literal["open", "closed"]
    created_at: datetime


class EMRIngestResponse(BaseModel):
    """Returned after the MVP's inline audio-processing pipeline completes."""

    emr_record_id: uuid.UUID = Field(description="Created EMR record identifier.")
    status: Literal["pending_review"]
    message: str = "Audio processed and ready for doctor review."


class VoicePatientDetails(BaseModel):
    id: uuid.UUID
    full_name: str
    patient_reference: str
    age: int | None
    gender: str | None
    phone: str | None
    created: bool


class VoiceIntakeResponse(BaseModel):
    emr_record_id: uuid.UUID
    encounter_id: uuid.UUID
    patient: VoicePatientDetails
    raw_transcript: str
    translated_text: str
    status: Literal["pending_review"]
    message: str = "Patient and EMR record created from the voice intake."


class VoiceJobCreateRequest(BaseModel):
    content_type: str = Field(min_length=3, max_length=100)
    file_size: int = Field(gt=0)
    language_code: str = Field(default="unknown", max_length=10)
    department: str | None = Field(default=None, max_length=100)
    patient_id: uuid.UUID | None = None


class VoiceJobUploadResponse(BaseModel):
    job_id: uuid.UUID
    upload_url: str
    object_key: str
    content_type: str
    expires_in: int


class VoiceJobCompleteRequest(BaseModel):
    etag: str | None = Field(default=None, max_length=255)


class VoiceJobSummary(BaseModel):
    id: uuid.UUID
    status: str
    patient_id: uuid.UUID | None
    patient_name: str | None
    patient_reference: str | None
    patient_age: int | None
    emr_record_id: uuid.UUID | None
    error_message: str | None
    created_at: datetime


class CodeSuggestion(BaseModel):
    system: str = Field(examples=["SNOMED_CT"])
    code: str = Field(examples=["44054006"])
    display_term: str = Field(examples=["Type 2 diabetes mellitus"])
    confidence_score: float = Field(ge=0, le=1)


class EMRRecordDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    encounter_id: uuid.UUID
    source_language: str = Field(examples=["hi-IN"])
    raw_transcript: str | None
    translated_text: str | None
    structured_note: StructuredNote | None
    suggested_codes: list[CodeSuggestion] = Field(default_factory=list)
    status: Literal["draft", "pending_review", "approved", "synced_to_emr"]


class EMRReviewRequest(BaseModel):
    """Doctor's edits + confirmed codes when approving a record."""

    edited_structured_note: StructuredNote | None = Field(
        default=None,
        description="Complete corrected SOAP note. Omit to keep the generated note unchanged.",
    )
    confirmed_code_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Suggested-code link IDs confirmed by the reviewing doctor.",
    )
