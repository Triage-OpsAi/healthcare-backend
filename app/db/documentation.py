import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.database import Base

class WardVoiceDocument(Base):
    __tablename__ = "wardvoice_documents"
    __table_args__ = (Index("ix_wv_document_patient", "hospital_id", "patient_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"))
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"))
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("encounters.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    template_id: Mapped[str] = mapped_column(String(80))
    template_version: Mapped[str] = mapped_column(String(40))
    template_snapshot: Mapped[dict] = mapped_column(JSON)
    context: Mapped[dict] = mapped_column(JSON)
    transcript: Mapped[str] = mapped_column(Text, default="")
    original_transcript: Mapped[str] = mapped_column(Text, default="")
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    mode: Mapped[str] = mapped_column(String(20), default="structured")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_checks: Mapped[dict] = mapped_column(JSON, default=dict)
    signer_name: Mapped[str | None] = mapped_column(String(255))
    signature: Mapped[dict | None] = mapped_column(JSON)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_digest: Mapped[str | None] = mapped_column(String(64))
    destination: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class WardVoiceDocumentRevision(Base):
    __tablename__ = "wardvoice_document_revisions"
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wardvoice_documents.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
