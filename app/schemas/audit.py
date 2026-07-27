import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AuditEventCreate(BaseModel):
    client_event_id: str = Field(min_length=8, max_length=64)
    action: str = Field(min_length=2, max_length=100)
    event_category: str = Field(default="clinical", max_length=50)
    resource_type: str = Field(default="system", max_length=50)
    resource_id: uuid.UUID | None = None
    patient_id: uuid.UUID | None = None
    encounter_id: uuid.UUID | None = None
    outcome: Literal["success", "failure", "denied", "queued"] = "success"
    source: str = Field(default="web", max_length=30)
    request_id: str | None = Field(default=None, max_length=100)
    changes: dict | None = None
    event_metadata: dict | None = None
    occurred_at: datetime


class AuditEventSummary(BaseModel):
    id: uuid.UUID
    client_event_id: str | None
    action: str
    event_category: str
    actor_name: str
    actor_role: str | None
    patient_id: uuid.UUID | None
    patient_name: str | None
    patient_reference: str | None
    encounter_id: uuid.UUID | None
    resource_type: str
    resource_id: uuid.UUID | None
    outcome: str
    source: str
    changes: dict | None
    event_metadata: dict | None
    occurred_at: datetime
    recorded_at: datetime


class AuditEventList(BaseModel):
    events: list[AuditEventSummary]
    total: int
