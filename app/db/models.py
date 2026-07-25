"""
Multi-tenant schema for the EMR translation/coding platform.

Design principles applied throughout (discussed in the architecture review):

1. Every tenant-scoped table carries `hospital_id` from day one, even though
   Rainbow is the only hospital today. Retrofitting tenant isolation later
   on a live medical-records system is high-risk; it's cheap now.
2. Reference/coding tables (SNOMED CT, ICD-10, drug master) are NOT
   tenant-scoped (except drug_master, which is hospital-specific formulary)
   -- they're shared lookup data loaded once and reused across all tenants.
3. `emr_records` never overwrites the original transcript/translation --
   raw_transcript and translated_text are preserved permanently for audit
   and medico-legal defensibility, even after a doctor edits the structured
   note.
4. Codes are never free-text generated -- `emr_record_codes` always points
   to a row in `medical_codes`, enforced by foreign key. This is the
   guardrail against an LLM hallucinating a SNOMED/ICD code.
5. `audit_logs` is append-only and captures every read/write of patient
   data, which you'll need for DPDP Act compliance and hospital audits.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def uuid_pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# --------------------------------------------------------------------------
# Tenancy
# --------------------------------------------------------------------------

class AdminOrganization(Base):
    """The company workspace that owns internal users and client tenants."""
    __tablename__ = "admin_organizations"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Hospital(Base):
    __tablename__ = "hospitals"

    id: Mapped[uuid.UUID] = uuid_pk()
    admin_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_organizations.id"), nullable=True, index=True
    )
    parent_hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospitals.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)  # e.g. RAINBOW-BLR
    hfr_id: Mapped[str | None] = mapped_column(String(100))  # ABDM Health Facility Registry ID
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class State(Base):
    __tablename__ = "states"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class District(Base):
    __tablename__ = "districts"
    __table_args__ = (
        UniqueConstraint("state_id", "name", name="uq_district_per_state"),
        Index("ix_districts_state_name", "state_id", "name"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    state_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("states.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    state: Mapped["State"] = relationship(lazy="joined")


class City(Base):
    __tablename__ = "cities"
    __table_args__ = (
        UniqueConstraint("district_id", "name", name="uq_city_per_district"),
        Index("ix_cities_district_name", "district_id", "name"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    district_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("districts.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    district: Mapped["District"] = relationship(lazy="joined")


class ClientProfile(Base):
    """Administration/onboarding data for a Hospital tenant.

    Hospital remains the tenant root; this one-to-one extension avoids a
    competing client identity while keeping the clinical schema stable.
    """
    __tablename__ = "client_profiles"
    __table_args__ = (
        Index("ix_client_profiles_state_id", "state_id"),
        Index("ix_client_profiles_service_start_date", "service_start_date"),
        Index("ix_client_profiles_email", "email"),
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), primary_key=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hq_location: Mapped[str] = mapped_column(String(255), nullable=False)
    service_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    requested_services: Mapped[list | None] = mapped_column(JSON)
    api_key_hash: Mapped[str | None] = mapped_column(String(64))
    api_key_hint: Mapped[str | None] = mapped_column(String(32))
    pan_number: Mapped[str] = mapped_column(String(10), nullable=False)
    gst_number: Mapped[str] = mapped_column(String(15), nullable=False)
    complete_address: Mapped[str] = mapped_column(Text, nullable=False)
    state_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("states.id"), nullable=False)
    district_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("districts.id"), nullable=False)
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cities.id"), nullable=False)
    pincode: Mapped[str] = mapped_column(String(6), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_mobile: Mapped[str] = mapped_column(String(15), nullable=False)
    emergency_contact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    emergency_contact_mobile: Mapped[str] = mapped_column(String(15), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    hospital: Mapped["Hospital"] = relationship(lazy="joined")
    state: Mapped["State"] = relationship(lazy="joined")
    district: Mapped["District"] = relationship(lazy="joined")
    city: Mapped["City"] = relationship(lazy="joined")


class NetworkHospitalProfile(Base):
    """Lightweight onboarding data for a branch created by a client owner."""

    __tablename__ = "network_hospital_profiles"
    __table_args__ = (
        Index("ix_network_hospital_profiles_parent", "parent_hospital_id"),
        Index("ix_network_hospital_profiles_email", "email"),
    )

    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), primary_key=True
    )
    parent_hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False
    )
    place: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    hospital: Mapped["Hospital"] = relationship(
        foreign_keys=[hospital_id], lazy="joined"
    )


class Service(Base):
    """A billable service offered by one administration organization."""
    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint(
            "admin_organization_id",
            "name",
            name="uq_service_name_per_admin_organization",
        ),
        UniqueConstraint(
            "admin_organization_id",
            "code",
            name="uq_service_code_per_admin_organization",
        ),
        Index("ix_services_admin_organization_id", "admin_organization_id"),
        Index("ix_services_active", "admin_organization_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    admin_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(6), nullable=False)
    about: Mapped[str] = mapped_column(Text, nullable=False)
    features: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    service_charge: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    gst_included: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    gst_percentage: Mapped[float] = mapped_column(
        Numeric(5, 2), default=18, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ClientService(Base):
    """Services selected for a client during onboarding."""
    __tablename__ = "client_services"
    __table_args__ = (Index("ix_client_services_service_id", "service_id"),)

    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), primary_key=True
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ServiceApiKey(Base):
    """A service-scoped API key. Only its hash and a display hint are stored."""
    __tablename__ = "service_api_keys"
    __table_args__ = (
        Index("ix_service_api_keys_admin_organization_id", "admin_organization_id"),
        Index("ix_service_api_keys_service_id", "service_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    admin_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_organizations.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str | None] = mapped_column(String(100))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    service: Mapped["Service"] = relationship(lazy="joined")


# --------------------------------------------------------------------------
# RBAC: roles / permissions
# --------------------------------------------------------------------------

class Permission(Base):
    """Global catalog of permission codes, e.g. 'emr:create', 'emr:approve'."""
    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255))


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("hospital_id", "name", name="uq_role_per_hospital"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    # nullable hospital_id = a global/system role (e.g. platform admin)
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hospitals.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # doctor, nurse, records_admin, etc.
    permissions: Mapped[list["Permission"]] = relationship(
        secondary="role_permissions", lazy="selectin"
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"), primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("permissions.id"), primary_key=True)


# --------------------------------------------------------------------------
# Users & auth
# --------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("hospital_id", "email", name="uq_user_email_per_hospital"),
        UniqueConstraint(
            "admin_organization_id",
            "email",
            name="uq_admin_user_email_per_organization",
        ),
        Index("ix_users_hospital_id", "hospital_id"),
        Index("ix_users_admin_organization_id", "admin_organization_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospitals.id"), nullable=True
    )
    admin_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_organizations.id"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hpr_id: Mapped[str | None] = mapped_column(String(100))  # ABDM Health Professional Registry ID
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    role: Mapped["Role"] = relationship(lazy="selectin")


class UserClientAccess(Base):
    """Explicit visibility grant from an internal user to a client tenant."""
    __tablename__ = "user_client_access"
    __table_args__ = (
        Index("ix_user_client_access_hospital_id", "hospital_id"),
        Index("ix_user_client_access_user_id", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), primary_key=True
    )
    can_manage: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    hospital: Mapped["Hospital"] = relationship(lazy="joined")


class UserInvitation(Base):
    __tablename__ = "user_invitations"
    __table_args__ = (
        Index("ix_user_invitations_email", "email"),
        Index("ix_user_invitations_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospitals.id"), nullable=True
    )
    admin_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_organizations.id"), nullable=True, index=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    role: Mapped["Role"] = relationship(lazy="joined")


class InvitationClientAccess(Base):
    __tablename__ = "invitation_client_access"

    invitation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_invitations.id", ondelete="CASCADE"), primary_key=True
    )
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="CASCADE"), primary_key=True
    )
    can_manage: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RefreshToken(Base):
    """Stores only the HASH of the refresh token, so a leaked DB doesn't leak usable tokens."""
    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------
# Clinical core
# --------------------------------------------------------------------------

class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (Index("ix_patients_hospital_id", "hospital_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    abha_id: Mapped[str | None] = mapped_column(String(50))  # Ayushman Bharat Health Account
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    date_of_birth: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gender: Mapped[str | None] = mapped_column(String(20))
    phone: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Encounter(Base):
    """A single visit/consultation. One encounter -> one EMR record."""
    __tablename__ = "encounters"
    __table_args__ = (
        Index("ix_encounters_hospital_id", "hospital_id"),
        Index("ix_encounters_patient_id", "patient_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    encounter_number: Mapped[str | None] = mapped_column(String(50))
    ward_number: Mapped[str | None] = mapped_column(String(50))
    bed_number: Mapped[str | None] = mapped_column(String(50))
    department: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="open")  # open, closed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EMRRecord(Base):
    """
    The core artifact of the whole product.

    Pipeline stages captured here:
      raw_transcript      -> Sarvam ASR output, original language
      translated_text     -> Sarvam translation to English
      structured_note     -> LLM-structured SOAP note (JSON)
      status               -> draft -> pending_review -> approved -> synced_to_emr
    """
    __tablename__ = "emr_records"
    __table_args__ = (
        Index("ix_emr_records_hospital_id", "hospital_id"),
        Index("ix_emr_records_encounter_id", "encounter_id"),
        Index("ix_emr_records_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("encounters.id"), nullable=False)

    source_language: Mapped[str] = mapped_column(String(10), nullable=False)  # BCP-47, e.g. kn-IN
    audio_storage_url: Mapped[str | None] = mapped_column(String(500))

    raw_transcript: Mapped[str | None] = mapped_column(Text)       # original-language text
    translated_text: Mapped[str | None] = mapped_column(Text)      # English, from Sarvam
    structured_note: Mapped[dict | None] = mapped_column(JSON)     # SOAP-formatted note

    status: Mapped[str] = mapped_column(
        Enum("draft", "pending_review", "approved", "synced_to_emr", name="emr_status"),
        default="draft",
    )

    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VoiceIntakeJob(Base):
    """Durable state for direct-to-object-storage voice processing."""

    __tablename__ = "voice_intake_jobs"
    __table_args__ = (
        Index("ix_voice_jobs_hospital_created", "hospital_id", "created_at"),
        Index("ix_voice_jobs_status", "status"),
        Index("ix_voice_jobs_patient_id", "patient_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    bucket_name: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(nullable=False)
    etag: Mapped[str | None] = mapped_column(String(255))
    language_code: Mapped[str] = mapped_column(String(10), default="unknown")
    department: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40), default="awaiting_upload")
    raw_transcript: Mapped[str | None] = mapped_column(Text)
    translated_text: Mapped[str | None] = mapped_column(Text)
    extracted_intake: Mapped[dict | None] = mapped_column(JSON)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patients.id"))
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("encounters.id"))
    emr_record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("emr_records.id")
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PatientReport(Base):
    """A private clinical document uploaded for a patient and summarized asynchronously."""

    __tablename__ = "patient_reports"
    __table_args__ = (
        Index("ix_patient_reports_hospital_patient", "hospital_id", "patient_id"),
        Index("ix_patient_reports_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    bucket_name: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(nullable=False)
    etag: Mapped[str | None] = mapped_column(String(255))
    capture_source: Mapped[str] = mapped_column(String(20), default="file")
    status: Mapped[str] = mapped_column(String(40), default="awaiting_upload")
    document_type: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str | None] = mapped_column(Text)
    key_findings: Mapped[list | None] = mapped_column(JSON)
    extracted_details: Mapped[dict | None] = mapped_column(JSON)
    quality_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PatientMedication(Base):
    """Clinician-entered medication attached to a longitudinal patient chart."""

    __tablename__ = "patient_medications"
    __table_args__ = (Index("ix_patient_medications_hospital_patient", "hospital_id", "patient_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), nullable=False)
    prescribed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dosage: Mapped[str | None] = mapped_column(String(100))
    frequency: Mapped[str | None] = mapped_column(String(100))
    route: Mapped[str | None] = mapped_column(String(100))
    duration: Mapped[str | None] = mapped_column(String(100))
    instructions: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DischargeSummaryJob(Base):
    """Durable multilingual discharge-instruction and generated-PDF pipeline."""

    __tablename__ = "discharge_summary_jobs"
    __table_args__ = (
        Index("ix_discharge_jobs_hospital_patient", "hospital_id", "patient_id"),
        Index("ix_discharge_jobs_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    audio_bucket_name: Mapped[str] = mapped_column(String(255), nullable=False)
    audio_object_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    audio_content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    audio_file_size: Mapped[int] = mapped_column(nullable=False)
    audio_etag: Mapped[str | None] = mapped_column(String(255))
    source_language: Mapped[str] = mapped_column(String(10), default="unknown")
    status: Mapped[str] = mapped_column(String(40), default="awaiting_upload")
    raw_transcript: Mapped[str | None] = mapped_column(Text)
    translated_instructions: Mapped[str | None] = mapped_column(Text)
    summary_data: Mapped[dict | None] = mapped_column(JSON)
    pdf_bucket_name: Mapped[str | None] = mapped_column(String(255))
    pdf_object_key: Mapped[str | None] = mapped_column(String(500), unique=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --------------------------------------------------------------------------
# Medical coding (SNOMED CT / ICD-10 / drug master)
# --------------------------------------------------------------------------

class MedicalCode(Base):
    """
    Shared reference table -- NOT tenant-scoped. Loaded once from the
    India SNOMED CT National Release (via NRCeS) and WHO ICD-10.
    `embedding` powers semantic lookup (see coding_service.py) so free-text
    like 'sugar problem' can still match 'Diabetes mellitus'.
    """
    __tablename__ = "medical_codes"
    __table_args__ = (
        Index("ix_medical_codes_system", "system"),
        UniqueConstraint("system", "code", name="uq_code_per_system"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    system: Mapped[str] = mapped_column(String(20), nullable=False)  # 'SNOMED_CT' | 'ICD10' | 'LOINC'
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    display_term: Mapped[str] = mapped_column(String(500), nullable=False)
    # Stored as JSON list of floats here for portability; use pgvector's
    # native `Vector` column type in production for indexed ANN search.
    embedding: Mapped[list | None] = mapped_column(JSON)


class EMRRecordCode(Base):
    """
    Join table: which codes were attached to a given EMR record, with the
    match confidence and whether a human confirmed it. A code NEVER gets
    here by an LLM writing text -- only by a lookup against MedicalCode.
    """
    __tablename__ = "emr_record_codes"
    __table_args__ = (Index("ix_emr_record_codes_record_id", "emr_record_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    emr_record_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("emr_records.id"), nullable=False)
    medical_code_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("medical_codes.id"), nullable=False)
    field_type: Mapped[str] = mapped_column(String(30))  # diagnosis, symptom, procedure
    confidence_score: Mapped[float] = mapped_column(Numeric(4, 3))  # 0.000 - 1.000
    confirmed_by_doctor: Mapped[bool] = mapped_column(Boolean, default=False)


class DrugMasterEntry(Base):
    """Hospital-specific formulary -- tenant-scoped, since brand names/stock vary by hospital."""
    __tablename__ = "drug_master"
    __table_args__ = (Index("ix_drug_master_hospital_id", "hospital_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), nullable=False)
    brand_name: Mapped[str] = mapped_column(String(255), nullable=False)
    generic_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dosage_form: Mapped[str | None] = mapped_column(String(100))  # tablet, syrup, injection
    embedding: Mapped[list | None] = mapped_column(JSON)


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------

class AuditLog(Base):
    """Append-only. Never updated or deleted. Required for DPDP Act / hospital audit trails."""
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_hospital_id", "hospital_id"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospitals.id"), nullable=True
    )
    admin_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_organizations.id"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. 'emr_record.read'
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ip_address: Mapped[str | None] = mapped_column(String(50))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
