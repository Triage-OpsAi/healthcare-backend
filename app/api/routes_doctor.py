import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.schemas.doctor import (
    ClinicalInvitationResponse,
    ClinicalInvitationSummary,
    ClinicalRoleSummary,
    ClinicalUserSummary,
    CreateEncounterRequest,
    CreatedEncounter,
    DoctorRecordSummary,
    DoctorWorkspace,
    InviteClinicalUserRequest,
    NetworkHospitalCreate,
    NetworkHospitalSummary,
    PatientDashboardSummary,
    UpdateClinicalUserRequest,
)
from app.services import doctor_service, invitation_email
from app.services.authorization import require_permission

router = APIRouter(prefix="/doctor", tags=["Doctor Portal"])


async def _deliver_clinical_invitation(invitation, raw_token: str, workspace: str):
    return await invitation_email.deliver_invitation(
        recipient=invitation.email,
        full_name=invitation.full_name,
        raw_token=raw_token,
        frontend_url=settings.DOCTOR_FRONTEND_URL,
        workspace_name=workspace,
    )


@router.get("/workspace", response_model=DoctorWorkspace)
async def workspace(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await doctor_service.get_workspace(db, current_user)


@router.get("/records", response_model=list[DoctorRecordSummary])
async def records(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:read")),
):
    return await doctor_service.list_records(db, current_user)


@router.get("/patients", response_model=list[PatientDashboardSummary])
async def patients(
    refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:read")),
):
    from app.services.patient_list_cache import patient_list
    return await patient_list(db, current_user, doctor_service.list_patients, refresh)


@router.get("/roles", response_model=list[ClinicalRoleSummary])
async def roles(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("users:manage")),
):
    return await doctor_service.list_roles(db, current_user)


@router.get("/users", response_model=list[ClinicalUserSummary])
async def users(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("users:manage")),
):
    return await doctor_service.list_users(db, current_user)


@router.get("/invitations", response_model=list[ClinicalInvitationSummary])
async def invitations(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("users:manage")),
):
    return await doctor_service.list_invitations(db, current_user)


@router.post(
    "/invitations",
    response_model=ClinicalInvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_user(
    payload: InviteClinicalUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("users:manage")),
):
    invitation, raw_token = await doctor_service.create_invitation(
        db, payload=payload, current_user=current_user
    )
    workspace = await doctor_service.get_workspace(db, current_user)
    delivery, magic_link = await _deliver_clinical_invitation(
        invitation, raw_token, workspace.organization.name
    )
    return ClinicalInvitationResponse(
        id=invitation.id,
        email=invitation.email,
        expires_at=invitation.expires_at,
        delivery=delivery,
        magic_link=magic_link,
    )


@router.post(
    "/invitations/{invitation_id}/resend",
    response_model=ClinicalInvitationResponse,
)
async def resend_invitation(
    invitation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("users:manage")),
):
    invitation, raw_token = await doctor_service.resend_invitation(
        db, invitation_id=invitation_id, current_user=current_user
    )
    workspace = await doctor_service.get_workspace(db, current_user)
    delivery, magic_link = await _deliver_clinical_invitation(
        invitation, raw_token, workspace.organization.name
    )
    return ClinicalInvitationResponse(
        id=invitation.id,
        email=invitation.email,
        expires_at=invitation.expires_at,
        delivery=delivery,
        magic_link=magic_link,
    )


@router.patch("/users/{user_id}", response_model=ClinicalUserSummary)
async def update_user(
    user_id: uuid.UUID,
    payload: UpdateClinicalUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("users:manage")),
):
    return await doctor_service.update_user(
        db, user_id=user_id, payload=payload, current_user=current_user
    )


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("users:manage")),
):
    await doctor_service.deactivate_user(
        db, user_id=user_id, current_user=current_user
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/network", response_model=list[NetworkHospitalSummary])
async def network(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("network:manage")),
):
    return await doctor_service.list_network(db, current_user)


@router.post(
    "/network",
    response_model=NetworkHospitalSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_network_hospital(
    payload: NetworkHospitalCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("network:manage")),
):
    hospital, invitations = await doctor_service.create_network_hospital(
        db, payload=payload, current_user=current_user
    )
    for invitation, raw_token in invitations:
        await _deliver_clinical_invitation(invitation, raw_token, hospital.name)
    return hospital


@router.post(
    "/encounters",
    response_model=CreatedEncounter,
    status_code=status.HTTP_201_CREATED,
)
async def create_encounter(
    payload: CreateEncounterRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await doctor_service.create_encounter(
        db, payload=payload, current_user=current_user
    )
