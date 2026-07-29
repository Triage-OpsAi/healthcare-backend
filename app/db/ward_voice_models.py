"""Isolated persistence models for the Ward Voice nursing workflow."""
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models import uuid_pk


class Ward(Base):
    __tablename__ = "wards"
    __table_args__ = (UniqueConstraint("hospital_id", "code", name="uq_ward_hospital_code"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Bed(Base):
    __tablename__ = "ward_beds"
    __table_args__ = (
        UniqueConstraint("ward_id", "bed_number", name="uq_ward_bed_number"),
        Index("ix_ward_beds_hospital_ward", "hospital_id", "ward_id"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    ward_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wards.id"), nullable=False)
    bed_number: Mapped[str] = mapped_column(String(30), nullable=False)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patients.id"))
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("encounters.id"))
    assigned_nurse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    protocol: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WardTask(Base):
    __tablename__ = "ward_tasks"
    __table_args__ = (Index("ix_ward_tasks_hospital_due", "hospital_id", "due_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    ward_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wards.id"), nullable=False)
    bed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ward_beds.id"), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    task_type: Mapped[str] = mapped_column(String(60), default="observation", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="due", nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VoiceCapture(Base):
    __tablename__ = "ward_voice_captures"
    __table_args__ = (
        Index("ix_ward_voice_capture_hospital_created", "hospital_id", "created_at"),
        Index("ix_ward_voice_capture_task", "task_id"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    ward_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wards.id"), nullable=False)
    bed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ward_beds.id"), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ward_tasks.id"))
    captured_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    etag: Mapped[str | None] = mapped_column(String(255))
    language_code: Mapped[str] = mapped_column(String(10), default="unknown", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="awaiting_upload", nullable=False)
    raw_transcript: Mapped[str | None] = mapped_column(Text)
    translated_text: Mapped[str | None] = mapped_column(Text)
    extraction_payload: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExtractedObservation(Base):
    __tablename__ = "ward_extracted_observations"
    __table_args__ = (Index("ix_ward_observation_patient_time", "patient_id", "observed_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    capture_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ward_voice_captures.id"), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), nullable=False)
    observation_type: Mapped[str] = mapped_column(String(60), nullable=False)
    value_numeric: Mapped[float | None] = mapped_column(Numeric(12, 3))
    value_text: Mapped[str | None] = mapped_column(String(255))
    unit: Mapped[str | None] = mapped_column(String(30))
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    confirmed_value_numeric: Mapped[float | None] = mapped_column(Numeric(12, 3))
    confirmed_value_text: Mapped[str | None] = mapped_column(String(255))
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requires_countersign: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    countersigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    countersigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FluidEntry(Base):
    __tablename__ = "ward_fluid_entries"
    __table_args__ = (Index("ix_fluid_entry_patient_time", "patient_id", "occurred_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    ward_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wards.id"), nullable=False)
    bed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ward_beds.id"), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), nullable=False)
    capture_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ward_voice_captures.id"))
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    amount_ml: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)
    notes: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IVInfusion(Base):
    __tablename__ = "ward_iv_infusions"
    __table_args__ = (Index("ix_iv_infusion_patient_start", "patient_id", "started_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    bed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ward_beds.id"), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), nullable=False)
    fluid_name: Mapped[str] = mapped_column(String(120), nullable=False)
    rate_ml_per_hour: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class ChartClosure(Base):
    __tablename__ = "ward_chart_closures"
    __table_args__ = (UniqueConstraint("patient_id", "chart_date", name="uq_patient_chart_closure"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), nullable=False)
    chart_date: Mapped[date] = mapped_column(Date, nullable=False)
    intake_ml: Mapped[int] = mapped_column(Integer, nullable=False)
    output_ml: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_ml: Mapped[int] = mapped_column(Integer, nullable=False)
    closed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WardVoiceAuditEvent(Base):
    __tablename__ = "ward_voice_audit_events"
    __table_args__ = (Index("ix_ward_voice_audit_hospital_time", "hospital_id", "created_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patients.id"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column()
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
