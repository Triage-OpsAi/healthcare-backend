import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("hospital_id", "name", name="uq_department_hospital_name"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ClinicalFeatureRollout(Base):
    __tablename__ = "clinical_feature_rollouts"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserDepartment(Base):
    __tablename__ = "user_departments"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), index=True)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"))
    assigned_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class InvitationDepartment(Base):
    __tablename__ = "invitation_departments"
    invitation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_invitations.id", ondelete="CASCADE"), primary_key=True)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"))
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"))


class ClinicalAttestation(Base):
    """Immutable signed snapshot. New approvals create new rows."""
    __tablename__ = "clinical_attestations"
    __table_args__ = (Index("ix_attestation_patient_section", "hospital_id", "patient_id", "section_key"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"))
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"))
    visit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patient_visits.id"))
    section_key: Mapped[str] = mapped_column(String(40))
    signer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    signer_name: Mapped[str] = mapped_column(String(255))
    signer_role: Mapped[str] = mapped_column(String(100))
    signature: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[str] = mapped_column(String(64))
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PatientConsent(Base):
    __tablename__ = "patient_consents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    visit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patient_visits.id"))
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"))
    department_name: Mapped[str] = mapped_column(String(100))
    recorded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    clinician_name: Mapped[str] = mapped_column(String(255))
    form: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[str] = mapped_column(String(64))
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    withdrawal_reason: Mapped[str | None] = mapped_column(Text)
