import re
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.api.deps import CurrentUser
from app.db.clinical_documents import Department, InvitationDepartment, UserDepartment
from app.services.clinical_documents import department as get_department
from app.core.config import settings
from app.core.security import (
    encrypt_workspace_id,
    generate_invitation_token,
)
from app.db.models import (
    AuditLog,
    ClientProfile,
    EMRRecord,
    Encounter,
    Hospital,
    NetworkHospitalProfile,
    Patient,
    PatientVisit,
    PatientSectionReview,
    Permission,
    RefreshToken,
    Role,
    User,
    UserInvitation,
)
from app.schemas.doctor import (
    ClinicalInvitationSummary,
    ClinicalRoleSummary,
    ClinicalUserSummary,
    CreateEncounterRequest,
    DoctorIdentity,
    DoctorRecordSummary,
    DoctorWorkspace,
    InviteClinicalUserRequest,
    NetworkHospitalCreate,
    NetworkHospitalSummary,
    OrganizationInfo,
    PatientDashboardSummary,
    UpdateClinicalUserRequest,
)
from app.schemas.patient_chart import PatientVisitSummary


CLINICAL_PERMISSION_DESCRIPTIONS = {
    "emr:create": "Create EMR records from clinical dictation",
    "emr:read": "View EMR records in the hospital workspace",
    "emr:review": "Review and approve EMR records",
    "users:manage": "Invite and manage hospital workspace users",
    "network:manage": "Create and view hospitals in the client network",
}


def workspace_slug(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return value[:80] or "hospital"


def workspace_path(hospital: Hospital) -> str:
    encrypted_id = encrypt_workspace_id(str(hospital.id))
    return f"/{workspace_slug(hospital.name)}/{encrypted_id}"


def _hospital_id(current_user: CurrentUser) -> uuid.UUID:
    if current_user.user_type != "clinical" or not current_user.hospital_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is restricted to clinical workspace users",
        )
    return uuid.UUID(current_user.hospital_id)


async def ensure_clinical_roles(
    db: AsyncSession, hospital_id: uuid.UUID
) -> dict[str, Role]:
    permissions: dict[str, Permission] = {}
    for code, description in CLINICAL_PERMISSION_DESCRIPTIONS.items():
        permission = await db.scalar(select(Permission).where(Permission.code == code))
        if permission is None:
            permission = Permission(code=code, description=description)
            db.add(permission)
            await db.flush()
        permissions[code] = permission

    definitions = {
        "hospital_owner": list(CLINICAL_PERMISSION_DESCRIPTIONS),
        "doctor": ["emr:create", "emr:read", "emr:review"],
        "nurse": ["emr:create", "emr:read"],
    }
    roles: dict[str, Role] = {}
    for name, codes in definitions.items():
        role = await db.scalar(
            select(Role)
            .options(selectinload(Role.permissions))
            .where(Role.hospital_id == hospital_id, Role.name == name)
        )
        required = [permissions[code] for code in codes]
        if role is None:
            role = Role(hospital_id=hospital_id, name=name, permissions=required)
            db.add(role)
            await db.flush()
        else:
            existing = {permission.code for permission in role.permissions}
            role.permissions.extend(
                permission for permission in required if permission.code not in existing
            )
        roles[name] = role
    return roles


async def create_owner_invitations(
    db: AsyncSession,
    *,
    hospital: Hospital,
    owners: list[tuple[str, str]],
    invited_by: uuid.UUID,
) -> list[tuple[UserInvitation, str]]:
    roles = await ensure_clinical_roles(db, hospital.id)
    results: list[tuple[UserInvitation, str]] = []
    seen: set[str] = set()
    for full_name, email in owners:
        normalized_email = email.strip().lower()
        if normalized_email in seen:
            continue
        seen.add(normalized_email)
        existing = await db.scalar(
            select(User.id).where(
                User.hospital_id == hospital.id,
                func.lower(User.email) == normalized_email,
            )
        )
        if existing:
            continue
        await db.execute(
            delete(UserInvitation).where(
                UserInvitation.hospital_id == hospital.id,
                func.lower(UserInvitation.email) == normalized_email,
                UserInvitation.accepted_at.is_(None),
            )
        )
        raw_token, token_hash = generate_invitation_token()
        invitation = UserInvitation(
            email=normalized_email,
            full_name=full_name.strip(),
            hospital_id=hospital.id,
            admin_organization_id=None,
            role_id=roles["hospital_owner"].id,
            token_hash=token_hash,
            invited_by=invited_by,
            expires_at=datetime.now(timezone.utc)
            + timedelta(hours=settings.INVITATION_EXPIRE_HOURS),
        )
        db.add(invitation)
        await db.flush()
        results.append((invitation, raw_token))
    await db.commit()
    return results


async def get_workspace(
    db: AsyncSession, current_user: CurrentUser
) -> DoctorWorkspace:
    hospital_id = _hospital_id(current_user)
    hospital = await db.get(Hospital, hospital_id)
    user = await db.scalar(
        select(User)
        .options(selectinload(User.role).selectinload(Role.permissions))
        .where(User.id == uuid.UUID(current_user.user_id))
    )
    if hospital is None or user is None or not hospital.is_active or not user.is_active:
        raise HTTPException(status_code=404, detail="Clinical workspace not found")

    client_profile = await db.get(ClientProfile, hospital.id)
    network_profile = await db.get(NetworkHospitalProfile, hospital.id)
    parent: Hospital | None = None
    parent_profile: ClientProfile | None = None
    if network_profile:
        parent = await db.get(Hospital, network_profile.parent_hospital_id)
        if parent:
            parent_profile = await db.get(ClientProfile, parent.id)

    source_profile = client_profile or parent_profile
    if network_profile:
        email = network_profile.email
        contact_name = network_profile.contact_name
        contact_email = network_profile.contact_email
        hq_location = network_profile.place
    elif client_profile:
        email = client_profile.email
        contact_name = client_profile.contact_name
        contact_email = client_profile.contact_email
        hq_location = client_profile.hq_location
    else:
        # Legacy/demo clinical tenants may predate platform client onboarding.
        # Keep their clinical workspace usable while showing the account owner
        # as the best available contact.
        email = user.email
        contact_name = user.full_name
        contact_email = user.email
        hq_location = None

    encrypted_id = encrypt_workspace_id(str(hospital.id))
    slug = workspace_slug(hospital.name)
    assigned_department = await db.scalar(select(Department).join(UserDepartment, UserDepartment.department_id == Department.id).where(
        UserDepartment.user_id == user.id, UserDepartment.hospital_id == hospital.id, Department.hospital_id == hospital.id))
    return DoctorWorkspace(
        organization=OrganizationInfo(
            id=hospital.id,
            name=hospital.name,
            code=hospital.code,
            email=email,
            gst_number=source_profile.gst_number if source_profile else None,
            contact_name=contact_name,
            contact_email=contact_email,
            contact_mobile=source_profile.contact_mobile if source_profile else None,
            hq_location=hq_location,
            is_network_hospital=network_profile is not None,
            parent_name=parent.name if parent else None,
        ),
        current_user=DoctorIdentity(
            id=user.id,
            full_name=user.full_name,
            email=user.email,
            role=user.role.name,
            permissions=sorted(permission.code for permission in user.role.permissions),
            department_id=assigned_department.id if assigned_department else None,
            department_name=assigned_department.name if assigned_department else None,
        ),
        workspace_slug=slug,
        encrypted_client_id=encrypted_id,
        workspace_path=f"/{slug}/{encrypted_id}",
    )


async def list_records(
    db: AsyncSession, current_user: CurrentUser
) -> list[DoctorRecordSummary]:
    hospital_id = _hospital_id(current_user)
    doctor = aliased(User)
    creator = aliased(User)
    creator_role = aliased(Role)
    rows = (
        await db.execute(
            select(EMRRecord, Encounter, Patient, doctor, creator, creator_role)
            .join(Encounter, Encounter.id == EMRRecord.encounter_id)
            .join(Patient, Patient.id == Encounter.patient_id)
            .join(doctor, doctor.id == Encounter.doctor_id)
            .join(creator, creator.id == EMRRecord.created_by)
            .join(creator_role, creator_role.id == creator.role_id)
            .where(EMRRecord.hospital_id == hospital_id)
            .order_by(EMRRecord.created_at.desc())
        )
    ).all()

    records: list[DoctorRecordSummary] = []
    today = date.today()
    for serial, (record, encounter, patient, doctor_user, created_user, role) in enumerate(
        rows, start=1
    ):
        age = None
        if patient.date_of_birth:
            born = patient.date_of_birth.date()
            age = today.year - born.year - (
                (today.month, today.day) < (born.month, born.day)
            )
        note = record.structured_note or {}
        subject = (
            note.get("chief_complaint")
            or note.get("subjective")
            or encounter.department
            or "Clinical consultation"
        )
        records.append(
            DoctorRecordSummary(
                id=record.id,
                encounter_id=encounter.id,
                patient_name=patient.full_name,
                patient_id=patient.id,
                patient_reference=patient.abha_id or str(patient.id)[:8].upper(),
                serial_number=serial,
                age=age,
                subject=str(subject),
                doctor_name=doctor_user.full_name,
                nurses=[created_user.full_name] if role.name == "nurse" else [],
                status=record.status,
                created_at=record.created_at,
            )
        )
    return records


async def list_patients(
    db: AsyncSession, current_user: CurrentUser
) -> list[PatientDashboardSummary]:
    """Return every tenant patient once, enriched with their latest EMR if present."""
    hospital_id = _hospital_id(current_user)
    approved_counts = dict(
        (
            await db.execute(
                select(
                    PatientSectionReview.patient_id,
                    func.count(PatientSectionReview.id),
                )
                .where(
                    PatientSectionReview.hospital_id == hospital_id,
                    PatientSectionReview.is_approved.is_(True),
                )
                .group_by(PatientSectionReview.patient_id)
            )
        ).all()
    )
    doctor = aliased(User)
    creator = aliased(User)
    creator_role = aliased(Role)
    rows = (
        await db.execute(
            select(Patient, Encounter, EMRRecord, doctor, creator, creator_role)
            .outerjoin(
                Encounter,
                (Encounter.patient_id == Patient.id)
                & (Encounter.hospital_id == hospital_id),
            )
            .outerjoin(EMRRecord, EMRRecord.encounter_id == Encounter.id)
            .outerjoin(doctor, doctor.id == Encounter.doctor_id)
            .outerjoin(creator, creator.id == EMRRecord.created_by)
            .outerjoin(creator_role, creator_role.id == creator.role_id)
            .where(Patient.hospital_id == hospital_id)
            .order_by(
                Patient.created_at.desc(),
                Encounter.created_at.desc().nullslast(),
                EMRRecord.created_at.desc().nullslast(),
            )
        )
    ).all()

    visit_creator = aliased(User)
    visit_rows = (
        await db.execute(
            select(PatientVisit, Encounter, EMRRecord, visit_creator)
            .outerjoin(Encounter, Encounter.visit_id == PatientVisit.id)
            .outerjoin(EMRRecord, EMRRecord.encounter_id == Encounter.id)
            .join(visit_creator, visit_creator.id == PatientVisit.created_by)
            .where(
                PatientVisit.hospital_id == hospital_id,
                select(AuditLog.id)
                .where(
                    AuditLog.action == "patient_visit.created",
                    AuditLog.resource_type == "patient_visit",
                    AuditLog.resource_id == PatientVisit.id,
                )
                .exists(),
            )
            .order_by(
                PatientVisit.created_at.asc(),
                Encounter.created_at.desc().nullslast(),
                EMRRecord.created_at.desc().nullslast(),
            )
        )
    ).all()
    visit_groups: dict[uuid.UUID, dict[uuid.UUID, dict]] = {}
    for patient_visit, encounter, record, visit_user in visit_rows:
        group = visit_groups.setdefault(patient_visit.patient_id, {})
        visit = group.setdefault(
            patient_visit.id,
            {
                "visit": patient_visit,
                "encounters": {},
                "doctor_name": visit_user.full_name,
                "summary": None,
            },
        )
        if encounter is not None:
            visit["encounters"][encounter.id] = encounter
        if record is not None and visit["summary"] is None:
            note = record.structured_note or {}
            visit["summary"] = (
                    note.get("chief_complaint")
                    or note.get("assessment")
                    or note.get("subjective")
                )

    patient_visits: dict[uuid.UUID, list[PatientVisitSummary]] = {}
    for patient_id, group in visit_groups.items():
        chronological = sorted(group.values(), key=lambda item: item["visit"].created_at)
        summaries = [
            PatientVisitSummary(
                id=item["visit"].id,
                visit_number=item["visit"].visit_number,
                encounter_number=(next(iter(item["encounters"].values())).encounter_number if item["encounters"] else None),
                department=(next(iter(item["encounters"].values())).department if item["encounters"] else None),
                ward_number=(next(iter(item["encounters"].values())).ward_number if item["encounters"] else None),
                bed_number=(next(iter(item["encounters"].values())).bed_number if item["encounters"] else None),
                status=item["visit"].status,
                doctor_name=item["doctor_name"],
                summary=str(item["summary"] or "Visit created; no encounters recorded yet."),
                encounter_count=len(item["encounters"]),
                created_at=item["visit"].created_at,
            )
            for item in chronological
        ]
        patient_visits[patient_id] = list(reversed(summaries))

    patients: list[PatientDashboardSummary] = []
    seen: set[uuid.UUID] = set()
    today = date.today()
    for patient, encounter, record, doctor_user, created_user, role in rows:
        if patient.id in seen:
            continue
        seen.add(patient.id)
        visits_for_patient = patient_visits.get(patient.id, [])
        latest_visit = visits_for_patient[0] if visits_for_patient else None
        age = None
        if patient.date_of_birth:
            born = patient.date_of_birth.date()
            age = today.year - born.year - (
                (today.month, today.day) < (born.month, born.day)
            )
        note = record.structured_note if record and record.structured_note else {}
        subject = (
            note.get("chief_complaint")
            or note.get("subjective")
            or (encounter.department if encounter else None)
            or "Patient profile — no EMR yet"
        )
        patients.append(
            PatientDashboardSummary(
                id=patient.id,
                latest_record_id=record.id if record else None,
                encounter_id=encounter.id if encounter else None,
                encounter_number=latest_visit.encounter_number if latest_visit else None,
                ward_number=latest_visit.ward_number if latest_visit else None,
                bed_number=latest_visit.bed_number if latest_visit else None,
                patient_name=patient.full_name,
                patient_reference=patient.abha_id or str(patient.id)[:8].upper(),
                serial_number=len(patients) + 1,
                age=age,
                gender=patient.gender,
                phone=patient.phone,
                subject=str(subject),
                doctor_name=latest_visit.doctor_name if latest_visit else (doctor_user.full_name if doctor_user else None),
                nurses=(
                    [created_user.full_name]
                    if created_user and role and role.name == "nurse"
                    else []
                ),
                status=(record.status if record and latest_visit and latest_visit.encounter_count else "no_record"),
                created_at=patient.created_at,
                last_visit_at=latest_visit.created_at if latest_visit else None,
                approval_percentage=round(
                    100 * approved_counts.get(patient.id, 0) / 8
                ),
                visits=visits_for_patient,
            )
        )
    return patients


async def list_roles(
    db: AsyncSession, current_user: CurrentUser
) -> list[ClinicalRoleSummary]:
    hospital_id = _hospital_id(current_user)
    await ensure_clinical_roles(db, hospital_id)
    await db.commit()
    roles = (
        await db.execute(
            select(Role)
            .options(selectinload(Role.permissions))
            .where(Role.hospital_id == hospital_id)
            .order_by(Role.name)
        )
    ).scalars().unique().all()
    return [
        ClinicalRoleSummary(
            id=role.id,
            name=role.name,
            permissions=sorted(permission.code for permission in role.permissions),
        )
        for role in roles
    ]


async def list_users(
    db: AsyncSession, current_user: CurrentUser
) -> list[ClinicalUserSummary]:
    hospital_id = _hospital_id(current_user)
    users = (
        await db.execute(
            select(User)
            .options(selectinload(User.role).selectinload(Role.permissions))
            .where(User.hospital_id == hospital_id)
            .order_by(User.full_name)
        )
    ).scalars().unique().all()
    return [
        ClinicalUserSummary(
            id=user.id,
            full_name=user.full_name,
            email=user.email,
            role_id=user.role_id,
            role=user.role.name,
            permissions=sorted(permission.code for permission in user.role.permissions),
            is_active=user.is_active,
            is_current_user=str(user.id) == current_user.user_id,
        )
        for user in users
    ]


async def list_invitations(
    db: AsyncSession, current_user: CurrentUser
) -> list[ClinicalInvitationSummary]:
    hospital_id = _hospital_id(current_user)
    rows = (
        await db.execute(
            select(UserInvitation)
            .options(selectinload(UserInvitation.role))
            .where(
                UserInvitation.hospital_id == hospital_id,
                UserInvitation.accepted_at.is_(None),
            )
            .order_by(UserInvitation.created_at.desc())
        )
    ).scalars().all()
    return [
        ClinicalInvitationSummary(
            id=row.id,
            full_name=row.full_name,
            email=row.email,
            role=row.role.name,
            expires_at=row.expires_at,
            created_at=row.created_at,
        )
        for row in rows
    ]


async def create_invitation(
    db: AsyncSession,
    *,
    payload: InviteClinicalUserRequest,
    current_user: CurrentUser,
) -> tuple[UserInvitation, str]:
    hospital_id = _hospital_id(current_user)
    selected_department = await get_department(db, payload.department_id, hospital_id)
    role = await db.scalar(
        select(Role).where(
            Role.id == payload.role_id,
            Role.hospital_id == hospital_id,
        )
    )
    if role is None:
        raise HTTPException(status_code=422, detail="Role is not assignable")
    existing = await db.scalar(
        select(User.id).where(
            User.hospital_id == hospital_id,
            func.lower(User.email) == str(payload.email).lower(),
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="This user already has an account")
    await db.execute(
        delete(UserInvitation).where(
            UserInvitation.hospital_id == hospital_id,
            func.lower(UserInvitation.email) == str(payload.email).lower(),
            UserInvitation.accepted_at.is_(None),
        )
    )
    raw_token, token_hash = generate_invitation_token()
    invitation = UserInvitation(
        email=str(payload.email).lower(),
        full_name=payload.full_name,
        hospital_id=hospital_id,
        admin_organization_id=None,
        role_id=role.id,
        token_hash=token_hash,
        invited_by=uuid.UUID(current_user.user_id),
        expires_at=datetime.now(timezone.utc)
        + timedelta(hours=settings.INVITATION_EXPIRE_HOURS),
    )
    db.add(invitation)
    await db.flush()
    db.add(InvitationDepartment(invitation_id=invitation.id, hospital_id=hospital_id, department_id=selected_department.id))
    await db.commit()
    await db.refresh(invitation)
    return invitation, raw_token


async def resend_invitation(
    db: AsyncSession,
    *,
    invitation_id: uuid.UUID,
    current_user: CurrentUser,
) -> tuple[UserInvitation, str]:
    hospital_id = _hospital_id(current_user)
    invitation = await db.scalar(
        select(UserInvitation).where(
            UserInvitation.id == invitation_id,
            UserInvitation.hospital_id == hospital_id,
            UserInvitation.accepted_at.is_(None),
        )
    )
    if invitation is None:
        raise HTTPException(status_code=404, detail="Pending invitation not found")
    raw_token, token_hash = generate_invitation_token()
    invitation.token_hash = token_hash
    invitation.expires_at = datetime.now(timezone.utc) + timedelta(
        hours=settings.INVITATION_EXPIRE_HOURS
    )
    await db.commit()
    await db.refresh(invitation)
    return invitation, raw_token


async def update_user(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    payload: UpdateClinicalUserRequest,
    current_user: CurrentUser,
) -> ClinicalUserSummary:
    hospital_id = _hospital_id(current_user)
    if str(user_id) == current_user.user_id:
        raise HTTPException(status_code=422, detail="You cannot change your own access")
    role = await db.scalar(
        select(Role).where(Role.id == payload.role_id, Role.hospital_id == hospital_id)
    )
    user = await db.scalar(
        select(User)
        .options(selectinload(User.role).selectinload(Role.permissions))
        .where(User.id == user_id, User.hospital_id == hospital_id)
    )
    if role is None or user is None:
        raise HTTPException(status_code=404, detail="User or role not found")
    user.role_id = role.id
    user.is_active = payload.is_active
    await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
    await db.commit()
    refreshed = await db.scalar(
        select(User)
        .options(selectinload(User.role).selectinload(Role.permissions))
        .where(User.id == user.id)
    )
    return ClinicalUserSummary(
        id=refreshed.id,
        full_name=refreshed.full_name,
        email=refreshed.email,
        role_id=refreshed.role_id,
        role=refreshed.role.name,
        permissions=sorted(permission.code for permission in refreshed.role.permissions),
        is_active=refreshed.is_active,
        is_current_user=False,
    )


async def deactivate_user(
    db: AsyncSession, *, user_id: uuid.UUID, current_user: CurrentUser
) -> None:
    hospital_id = _hospital_id(current_user)
    if str(user_id) == current_user.user_id:
        raise HTTPException(status_code=422, detail="You cannot delete your own account")
    user = await db.scalar(
        select(User).where(User.id == user_id, User.hospital_id == hospital_id)
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
    await db.commit()


def _code_part(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())[:6] or "SITE"


async def _unique_network_code(
    db: AsyncSession, root_name: str, branch_name: str
) -> str:
    prefix = _code_part(root_name)
    suffix = _code_part(branch_name)
    candidate = f"{prefix}-{suffix}"
    number = 1
    while await db.scalar(select(Hospital.id).where(Hospital.code == candidate)):
        number += 1
        tail = str(number)
        candidate = f"{prefix}-{suffix[: max(1, 6 - len(tail))]}{tail}"
    return candidate


async def list_network(
    db: AsyncSession, current_user: CurrentUser
) -> list[NetworkHospitalSummary]:
    hospital_id = _hospital_id(current_user)
    hospital = await db.get(Hospital, hospital_id)
    root_id = hospital.parent_hospital_id or hospital.id
    profiles = (
        await db.execute(
            select(NetworkHospitalProfile)
            .where(NetworkHospitalProfile.parent_hospital_id == root_id)
            .order_by(NetworkHospitalProfile.created_at.desc())
        )
    ).scalars().all()
    return [
        NetworkHospitalSummary(
            id=profile.hospital.id,
            name=profile.hospital.name,
            code=profile.hospital.code,
            place=profile.place,
            email=profile.email,
            contact_name=profile.contact_name,
            contact_email=profile.contact_email,
            created_at=profile.created_at,
            is_active=profile.hospital.is_active,
            workspace_path=workspace_path(profile.hospital),
        )
        for profile in profiles
    ]


async def create_network_hospital(
    db: AsyncSession,
    *,
    payload: NetworkHospitalCreate,
    current_user: CurrentUser,
) -> tuple[NetworkHospitalSummary, list[tuple[UserInvitation, str]]]:
    hospital_id = _hospital_id(current_user)
    current = await db.get(Hospital, hospital_id)
    root = await db.get(Hospital, current.parent_hospital_id) if current.parent_hospital_id else current
    code = await _unique_network_code(db, root.name, payload.name)
    hospital = Hospital(
        admin_organization_id=root.admin_organization_id,
        parent_hospital_id=root.id,
        name=payload.name,
        code=code,
        is_active=True,
    )
    db.add(hospital)
    await db.flush()
    profile = NetworkHospitalProfile(
        hospital_id=hospital.id,
        parent_hospital_id=root.id,
        place=payload.place,
        email=str(payload.email).lower(),
        contact_name=payload.contact_name,
        contact_email=str(payload.contact_email).lower(),
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    invitations = await create_owner_invitations(
        db,
        hospital=hospital,
        owners=[
            (f"{hospital.name} Owner", str(payload.email)),
            (payload.contact_name, str(payload.contact_email)),
        ],
        invited_by=uuid.UUID(current_user.user_id),
    )
    return (
        NetworkHospitalSummary(
            id=hospital.id,
            name=hospital.name,
            code=hospital.code,
            place=profile.place,
            email=profile.email,
            contact_name=profile.contact_name,
            contact_email=profile.contact_email,
            created_at=profile.created_at,
            is_active=hospital.is_active,
            workspace_path=workspace_path(hospital),
        ),
        invitations,
    )


async def create_encounter(
    db: AsyncSession,
    *,
    payload: CreateEncounterRequest,
    current_user: CurrentUser,
) -> Encounter:
    hospital_id = _hospital_id(current_user)
    dob = payload.date_of_birth
    if dob is None and payload.age is not None:
        dob = date(date.today().year - payload.age, 1, 1)
    patient = Patient(
        hospital_id=hospital_id,
        abha_id=payload.patient_reference,
        full_name=payload.patient_name,
        date_of_birth=(
            datetime(dob.year, dob.month, dob.day, tzinfo=timezone.utc) if dob else None
        ),
        gender=payload.gender,
        phone=payload.phone,
    )
    db.add(patient)
    await db.flush()
    encounter = Encounter(
        hospital_id=hospital_id,
        patient_id=patient.id,
        visit_id=None,
        doctor_id=uuid.UUID(current_user.user_id),
        department=payload.department,
        status="open",
    )
    db.add(encounter)
    await db.commit()
    await db.refresh(encounter)
    return encounter
