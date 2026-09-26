import math
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Signature(BaseModel):
    """Normalized pen strokes; no executable SVG or arbitrary image uploads."""
    model_config = ConfigDict(extra="forbid")
    strokes: list[list[tuple[float, float]]] = Field(min_length=1, max_length=100)

    @field_validator("strokes")
    @classmethod
    def validate_strokes(cls, strokes):
        count = sum(len(stroke) for stroke in strokes)
        if not 5 <= count <= 12000 or any(len(stroke) < 2 for stroke in strokes):
            raise ValueError("Draw a complete signature")
        points = [point for stroke in strokes for point in stroke]
        if any(not math.isfinite(v) or not 0 <= v <= 1 for point in points for v in point):
            raise ValueError("Invalid signature coordinates")
        if max(x for x, _ in points) - min(x for x, _ in points) < 0.02:
            raise ValueError("Draw a complete signature, not a dot")
        return strokes


class ApprovalRequest(BaseModel):
    signature: Signature
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    visit_id: uuid.UUID | None = None
    confirmed: Literal[True]


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value):
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("Enter a department name")
        return value


class DepartmentAssignment(BaseModel):
    department_id: uuid.UUID | None


class BilingualText(BaseModel):
    en: str = Field(min_length=2, max_length=3000)
    hi: str = Field(min_length=2, max_length=3000)

    @field_validator("en", "hi")
    @classmethod
    def nonempty(cls, value):
        if len(value.strip()) < 2:
            raise ValueError("Provide both English and Hindi text")
        return value.strip()


class ConsentForm(BaseModel):
    department_id: uuid.UUID
    visit_id: uuid.UUID | None = None
    procedure: BilingualText
    purpose: BilingualText
    benefits: BilingualText
    risks: BilingualText
    alternatives: BilingualText
    refusal: BilingualText
    language: Literal["en", "hi"]
    signer_name: str = Field(min_length=2, max_length=200)
    signer_type: Literal["patient", "representative"] = "patient"
    relationship: str = Field(default="", max_length=200)
    representative_reason: str = Field(default="", max_length=1000)
    witness_name: str = Field(default="", max_length=200)
    witness_signature: Signature | None = None
    decision: Literal["accepted", "declined"] = "accepted"
    patient_signature: Signature
    clinician_signature: Signature
    explained_and_questions_answered: Literal[True]
    bilingual_content_reviewed: Literal[True]

    @model_validator(mode="after")
    def validate_representative(self):
        if not self.signer_name.strip():
            raise ValueError("Signer name is required")
        if self.signer_type == "representative" and not (
            self.relationship.strip() and self.representative_reason.strip()
            and self.witness_name.strip() and self.witness_signature
        ):
            raise ValueError("Representative consent requires relationship, reason, and a witness signature")
        if bool(self.witness_name.strip()) != bool(self.witness_signature):
            raise ValueError("Provide both witness name and signature")
        return self


class ConsentWithdrawal(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class SpeechRequest(BaseModel):
    text: str = Field(min_length=2, max_length=2400)
    language: Literal["en", "hi"]
