import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.clinical_documents import Signature

class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: uuid.UUID
    encounter_id: uuid.UUID
    template_id: str = Field(max_length=80)
    transcript: str = Field(default="", max_length=50000)
    original_transcript: str = Field(default="", max_length=50000)

class DraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    transcript: str = Field(max_length=50000)
    fields: dict[str, str] = Field(max_length=80)
    mode: Literal["structured", "narrative"] = "structured"

class RevisionRequest(BaseModel):
    revision: int = Field(ge=1)

class ReviewRequest(RevisionRequest):
    acknowledgements: dict[str, str] = Field(default_factory=dict, max_length=80)

class FinalizeRequest(RevisionRequest):
    signature: Signature
    confirmed: Literal[True]


class SuggestionRequest(BaseModel):
    patient_id: uuid.UUID
    encounter_id: uuid.UUID
    transcript: str = Field(default="", max_length=50000)
