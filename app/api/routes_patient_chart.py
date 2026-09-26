import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.schemas.clinical_documents import ApprovalRequest
from app.services.clinical_documents import signed_approve
from app.db.database import get_db
from app.schemas.patient_chart import (
    AdditionalRecordCreateRequest,
    AudioAccess,
    DischargeCompleteRequest,
    DischargeCreateRequest,
    DischargeSummaryItem,
    DischargeUploadResponse,
    HandoverCompleteRequest,
    HandoverCreateRequest,
    HandoverRecipientUpdateRequest,
    HandoverSummaryItem,
    HandoverUploadResponse,
    MedicationCreateRequest,
    PatientDetailsResponse,
    PatientDetailsUpdateRequest,
    PatientChart,
    PatientMedicationSummary,
    PatientRecordSummary,
    PatientReportSummary,
    PatientVisitSummary,
    PatientSectionKey,
    PatientSectionItemUpdateRequest,
    PatientSectionReviewSummary,
    PatientSectionUpdateRequest,
    ReportCompleteRequest,
    ReportCreateRequest,
    ReportUploadResponse,
    VisitCreateRequest,
)
from app.schemas.doctor import ClinicalUserSummary
from app.services import (
    discharge_pipeline_service,
    doctor_service,
    handover_pipeline_service,
    patient_chart_service,
)
from app.services.authorization import require_permission

router = APIRouter(prefix="/patients", tags=["Patient Chart"])


@router.get("/handover-recipients", response_model=list[ClinicalUserSummary])
async def handover_recipients(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await doctor_service.list_users(db, current_user)


@router.patch("/{patient_id}", response_model=PatientDetailsResponse)
async def update_patient(
    patient_id: uuid.UUID,
    payload: PatientDetailsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await patient_chart_service.update_patient_details(
        db, patient_id=patient_id, payload=payload, current_user=current_user
    )


@router.get("/{patient_id}/chart", response_model=PatientChart)
async def patient_chart(
    patient_id: uuid.UUID,
    visit_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:read")),
):
    return await patient_chart_service.get_chart(
        db,
        patient_id=patient_id,
        visit_id=visit_id,
        current_user=current_user,
    )


@router.post(
    "/{patient_id}/visits",
    response_model=PatientVisitSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_patient_visit(
    patient_id: uuid.UUID,
    payload: VisitCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await patient_chart_service.create_visit(
        db, patient_id=patient_id, payload=payload, current_user=current_user
    )


@router.patch(
    "/{patient_id}/sections/{section_key}",
    response_model=PatientSectionReviewSummary,
)
async def edit_patient_section(
    patient_id: uuid.UUID,
    section_key: PatientSectionKey,
    payload: PatientSectionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await patient_chart_service.edit_section(
        db,
        patient_id=patient_id,
        section_key=section_key,
        payload=payload,
        current_user=current_user,
    )


@router.patch(
    "/{patient_id}/sections/{section_key}/items/{item_key}",
    response_model=PatientSectionReviewSummary,
)
async def edit_patient_section_item(
    patient_id: uuid.UUID,
    section_key: PatientSectionKey,
    item_key: str,
    payload: PatientSectionItemUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await patient_chart_service.edit_section_item(
        db,
        patient_id=patient_id,
        section_key=section_key,
        item_key=item_key,
        payload=payload,
        current_user=current_user,
    )


@router.delete(
    "/{patient_id}/sections/{section_key}/items/{item_key}",
    response_model=PatientSectionReviewSummary,
)
async def delete_patient_section_item(
    patient_id: uuid.UUID,
    section_key: PatientSectionKey,
    item_key: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await patient_chart_service.delete_section_item(
        db,
        patient_id=patient_id,
        section_key=section_key,
        item_key=item_key,
        current_user=current_user,
    )


@router.post(
    "/{patient_id}/sections/{section_key}/approve",
    response_model=PatientSectionReviewSummary,
)
async def approve_patient_section(
    patient_id: uuid.UUID,
    section_key: PatientSectionKey,
    payload: ApprovalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:review")),
):
    return await signed_approve(db, patient_id, section_key, payload, current_user)


@router.delete(
    "/{patient_id}/sections/{section_key}",
    response_model=PatientSectionReviewSummary,
)
async def delete_patient_section(
    patient_id: uuid.UUID,
    section_key: PatientSectionKey,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await patient_chart_service.delete_section(
        db,
        patient_id=patient_id,
        section_key=section_key,
        current_user=current_user,
    )


@router.get("/records/{record_id}/audio", response_model=AudioAccess)
async def record_audio(
    record_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:read")),
):
    return await patient_chart_service.audio_access(
        db, record_id=record_id, current_user=current_user
    )


@router.post(
    "/{patient_id}/reports",
    response_model=ReportUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_report(
    patient_id: uuid.UUID,
    payload: ReportCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await patient_chart_service.create_report(
        db, patient_id=patient_id, payload=payload, current_user=current_user
    )


@router.post(
    "/{patient_id}/reports/{report_id}/complete",
    response_model=PatientReportSummary,
)
async def complete_report(
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
    payload: ReportCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await patient_chart_service.complete_report(
        db,
        patient_id=patient_id,
        report_id=report_id,
        etag=payload.etag,
        current_user=current_user,
    )


@router.post(
    "/{patient_id}/reports/{report_id}/approve",
    response_model=PatientReportSummary,
)
async def approve_report(
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:review")),
):
    return await patient_chart_service.approve_report(
        db, patient_id=patient_id, report_id=report_id, current_user=current_user
    )


@router.get(
    "/{patient_id}/reports/{report_id}/open",
    response_model=AudioAccess,
)
async def open_report(
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:read")),
):
    return await patient_chart_service.report_access(
        db, patient_id=patient_id, report_id=report_id, current_user=current_user
    )


@router.post(
    "/{patient_id}/medications",
    response_model=PatientMedicationSummary,
    status_code=status.HTTP_201_CREATED,
)
async def add_medication(
    patient_id: uuid.UUID,
    payload: MedicationCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await patient_chart_service.add_medication(
        db, patient_id=patient_id, payload=payload, current_user=current_user
    )


@router.post(
    "/{patient_id}/records",
    response_model=PatientRecordSummary,
    status_code=status.HTTP_201_CREATED,
)
async def add_record(
    patient_id: uuid.UUID,
    payload: AdditionalRecordCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await patient_chart_service.add_record(
        db, patient_id=patient_id, payload=payload, current_user=current_user
    )


@router.post(
    "/{patient_id}/discharge-summaries",
    response_model=DischargeUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_discharge_summary(
    patient_id: uuid.UUID,
    payload: DischargeCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await discharge_pipeline_service.create(
        db, patient_id=patient_id, payload=payload, current_user=current_user
    )


@router.post(
    "/{patient_id}/discharge-summaries/{job_id}/complete",
    response_model=DischargeSummaryItem,
)
async def complete_discharge_summary(
    patient_id: uuid.UUID,
    job_id: uuid.UUID,
    payload: DischargeCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await discharge_pipeline_service.complete(
        db,
        patient_id=patient_id,
        job_id=job_id,
        etag=payload.etag,
        current_user=current_user,
    )


@router.get(
    "/{patient_id}/discharge-summaries/{job_id}/audio",
    response_model=AudioAccess,
)
async def discharge_audio(
    patient_id: uuid.UUID,
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:read")),
):
    return await discharge_pipeline_service.audio_access(
        db, patient_id=patient_id, job_id=job_id, current_user=current_user
    )


@router.get(
    "/{patient_id}/discharge-summaries/{job_id}/download",
    response_model=AudioAccess,
)
async def discharge_pdf(
    patient_id: uuid.UUID,
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:read")),
):
    return await discharge_pipeline_service.pdf_access(
        db, patient_id=patient_id, job_id=job_id, current_user=current_user
    )


@router.post(
    "/{patient_id}/handovers",
    response_model=HandoverUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_handover(
    patient_id: uuid.UUID,
    payload: HandoverCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await handover_pipeline_service.create(
        db, patient_id=patient_id, payload=payload, current_user=current_user
    )


@router.post(
    "/{patient_id}/handovers/{job_id}/complete",
    response_model=HandoverSummaryItem,
)
async def complete_handover(
    patient_id: uuid.UUID,
    job_id: uuid.UUID,
    payload: HandoverCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await handover_pipeline_service.complete(
        db,
        patient_id=patient_id,
        job_id=job_id,
        etag=payload.etag,
        current_user=current_user,
    )


@router.patch(
    "/{patient_id}/handovers/{job_id}/recipient",
    response_model=HandoverSummaryItem,
)
async def assign_handover_recipient(
    patient_id: uuid.UUID,
    job_id: uuid.UUID,
    payload: HandoverRecipientUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await handover_pipeline_service.assign_recipient(
        db,
        patient_id=patient_id,
        job_id=job_id,
        handed_over_to=payload.handed_over_to,
        current_user=current_user,
    )


@router.get(
    "/{patient_id}/handovers/{job_id}/audio",
    response_model=AudioAccess,
)
async def handover_audio(
    patient_id: uuid.UUID,
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("emr:read")),
):
    return await handover_pipeline_service.audio_access(
        db, patient_id=patient_id, job_id=job_id, current_user=current_user
    )
