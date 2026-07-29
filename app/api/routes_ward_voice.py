"""Ward Voice API routes."""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.db.database import get_db
from app.schemas.ward_voice import (
    CloseChartRequest, ConfirmCaptureRequest, CountersignSummary, FluidChartResponse,
    FluidEntryCreate, IVInfusionCreate, TaskUpdate, VoiceCaptureComplete,
    VoiceCaptureCreate, VoiceCaptureResult, VoiceCaptureUpload, WardCard, WardVoiceOverview,
)
from app.services import ward_voice_service
from app.services.authorization import require_any_permission, require_permission

router = APIRouter(prefix="/ward-voice", tags=["Ward Voice"])


@router.get("/wards", response_model=list[WardCard])
async def wards(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_any_permission("emr:read", "emr:create")),
):
    return await ward_voice_service.list_wards(db, user)


@router.get("/overview", response_model=WardVoiceOverview)
async def overview(
    ward_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_any_permission("emr:read", "emr:create")),
):
    return await ward_voice_service.overview(db, user, ward_id)


@router.patch("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_task(
    task_id: uuid.UUID, payload: TaskUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_permission("emr:create")),
):
    await ward_voice_service.update_task(db, task_id, payload.status, user)
    return Response(status_code=204)


@router.post("/captures", response_model=VoiceCaptureUpload, status_code=status.HTTP_201_CREATED)
async def create_capture(
    payload: VoiceCaptureCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await ward_voice_service.create_capture(db, payload, user)


@router.post("/captures/{capture_id}/complete", response_model=VoiceCaptureResult)
async def complete_capture(
    capture_id: uuid.UUID, payload: VoiceCaptureComplete,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await ward_voice_service.complete_capture(db, capture_id, payload.etag, user)


@router.post("/captures/{capture_id}/confirm", response_model=VoiceCaptureResult)
async def confirm_capture(
    capture_id: uuid.UUID, payload: ConfirmCaptureRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_permission("emr:create")),
):
    return await ward_voice_service.confirm_capture(db, capture_id, payload, user)


@router.get("/fluid-charts/{bed_id}", response_model=FluidChartResponse)
async def fluid_chart(
    bed_id: uuid.UUID, chart_date: date | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_any_permission("emr:read", "emr:create")),
):
    return await ward_voice_service.fluid_chart(db, bed_id, chart_date or date.today(), user)


@router.post("/fluid-entries", status_code=status.HTTP_201_CREATED)
async def add_fluid_entry(
    payload: FluidEntryCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_permission("emr:create")),
):
    await ward_voice_service.add_fluid_entry(db, payload, user)
    return {"status": "created"}


@router.post("/iv-infusions", status_code=status.HTTP_201_CREATED)
async def add_iv_infusion(
    payload: IVInfusionCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_permission("emr:create")),
):
    await ward_voice_service.add_iv_infusion(db, payload, user)
    return {"status": "created"}


@router.post("/fluid-charts/{bed_id}/close", status_code=status.HTTP_201_CREATED)
async def close_chart(
    bed_id: uuid.UUID, payload: CloseChartRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_permission("emr:review")),
):
    await ward_voice_service.close_chart(db, bed_id, payload.chart_date, user)
    return {"status": "closed"}


@router.get("/handovers")
async def handovers(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_any_permission("emr:read", "emr:create")),
):
    data = await ward_voice_service.overview(db, user)
    return data["handover"]


@router.get("/compliance")
async def compliance(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_any_permission("emr:read", "audit:read")),
):
    data = await ward_voice_service.overview(db, user)
    return {"summary": data["compliance"], "audit": data["audit"]}


@router.get("/countersigns", response_model=list[CountersignSummary])
async def countersigns(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_permission("emr:review")),
):
    return await ward_voice_service.list_countersigns(db, user)


@router.post("/countersigns/{observation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def countersign(
    observation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_permission("emr:review")),
):
    await ward_voice_service.countersign(db, observation_id, user)
    return Response(status_code=204)
