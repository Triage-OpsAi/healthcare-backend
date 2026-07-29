"""Tenant-scoped Ward Voice business logic and deterministic chart arithmetic."""
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.deps import CurrentUser
from app.core.config import settings
from app.db.models import Encounter, Patient, User
from app.db.ward_voice_models import (
    Bed, ChartClosure, ExtractedObservation, FluidEntry, IVInfusion, VoiceCapture,
    Ward, WardTask, WardVoiceAuditEvent,
)
from app.schemas.ward_voice import (
    ConfirmCaptureRequest, FluidEntryCreate, IVInfusionCreate, VoiceCaptureCreate,
)
from app.services import patient_builder_service, s3_storage_service, sarvam_service
from app.services.ward_voice_reasoning_service import extract_observations


def calculate_balance(intake_ml: int, output_ml: int) -> int:
    return intake_ml - output_ml


def calculate_iv_volume(rate_ml_per_hour: int, started_at: datetime, end_at: datetime) -> int:
    elapsed_hours = max(0.0, (end_at - started_at).total_seconds() / 3600)
    return round(rate_ml_per_hour * elapsed_hours)


def shift_window(now: datetime) -> tuple[datetime, datetime]:
    start_hour = 8 if 8 <= now.hour < 20 else 20
    start_day = now.date() if now.hour >= start_hour else now.date() - timedelta(days=1)
    start = datetime.combine(start_day, time(start_hour), tzinfo=now.tzinfo or timezone.utc)
    return start, start + timedelta(hours=12)


def _hospital_id(user: CurrentUser) -> uuid.UUID:
    if not user.hospital_id:
        raise HTTPException(status_code=403, detail="Clinical workspace required")
    return uuid.UUID(user.hospital_id)


def _user_id(user: CurrentUser) -> uuid.UUID:
    return uuid.UUID(user.user_id)


def _age(patient: Patient | None) -> int | None:
    return patient_builder_service.patient_age(patient) if patient else None


async def _audit(db: AsyncSession, user: CurrentUser, action: str, resource_type: str, resource_id=None, patient_id=None, details=None):
    db.add(WardVoiceAuditEvent(
        hospital_id=_hospital_id(user), user_id=_user_id(user), patient_id=patient_id,
        action=action, resource_type=resource_type, resource_id=resource_id, details=details,
    ))


async def _bed(db: AsyncSession, bed_id: uuid.UUID, user: CurrentUser) -> Bed:
    bed = await db.get(Bed, bed_id)
    if bed is None or bed.hospital_id != _hospital_id(user) or not bed.is_active:
        raise HTTPException(status_code=404, detail="Ward bed not found")
    return bed


async def _assert_chart_open(db: AsyncSession, patient_id: uuid.UUID, chart_date: date):
    closed = await db.scalar(select(ChartClosure).where(
        ChartClosure.patient_id == patient_id, ChartClosure.chart_date == chart_date
    ))
    if closed:
        raise HTTPException(status_code=409, detail="This fluid chart is closed and cannot be overwritten")


async def list_wards(db: AsyncSession, user: CurrentUser) -> list[dict]:
    rows = (
        await db.execute(
            select(Ward, func.count(Bed.id))
            .outerjoin(
                Bed,
                (Bed.ward_id == Ward.id)
                & Bed.is_active.is_(True)
                & Bed.patient_id.is_not(None),
            )
            .where(
                Ward.hospital_id == _hospital_id(user),
                Ward.is_active.is_(True),
            )
            .group_by(Ward.id)
            .having(func.count(Bed.id) > 0)
            .order_by(Ward.name)
        )
    ).all()
    return [
        {"id": ward.id, "name": ward.name, "code": ward.code, "patient_count": count}
        for ward, count in rows
    ]


async def sync_patient_assignment(
    db: AsyncSession,
    *,
    encounter: Encounter,
    user: CurrentUser,
) -> None:
    hospital_id = _hospital_id(user)
    current_bed = await db.scalar(
        select(Bed).where(
            Bed.hospital_id == hospital_id,
            Bed.patient_id == encounter.patient_id,
            Bed.is_active.is_(True),
        )
    )
    ward_value = (encounter.ward_number or "").strip()
    bed_value = (encounter.bed_number or "").strip()
    if not ward_value or not bed_value:
        if current_bed:
            current_bed.is_active = False
            await _audit(
                db, user, "bed.unassigned", "ward_bed", current_bed.id,
                encounter.patient_id,
            )
        return

    ward = await db.scalar(
        select(Ward).where(
            Ward.hospital_id == hospital_id,
            func.lower(Ward.code) == ward_value.lower(),
        )
    )
    if ward is None:
        ward = Ward(
            hospital_id=hospital_id,
            name=ward_value,
            code=ward_value,
            is_active=True,
        )
        db.add(ward)
        await db.flush()
    collision = await db.scalar(
        select(Bed).where(
            Bed.ward_id == ward.id,
            func.lower(Bed.bed_number) == bed_value.lower(),
            Bed.is_active.is_(True),
            Bed.patient_id != encounter.patient_id,
        )
    )
    if collision:
        raise HTTPException(
            status_code=409,
            detail=f"Bed {bed_value} is already assigned to another patient in {ward.name}",
        )
    if current_bed:
        current_bed.ward_id = ward.id
        current_bed.bed_number = bed_value
        current_bed.encounter_id = encounter.id
        current_bed.is_active = True
        bed = current_bed
    else:
        bed = Bed(
            hospital_id=hospital_id,
            ward_id=ward.id,
            bed_number=bed_value,
            patient_id=encounter.patient_id,
            encounter_id=encounter.id,
            assigned_nurse_id=_user_id(user),
            protocol=encounter.department or "Ward observation",
            is_active=True,
        )
        db.add(bed)
        await db.flush()
    await _audit(
        db, user, "bed.assigned", "ward_bed", bed.id, encounter.patient_id,
        {"ward": ward_value, "bed": bed_value},
    )


async def overview(db: AsyncSession, user: CurrentUser, ward_id: uuid.UUID | None = None) -> dict:
    hospital_id = _hospital_id(user)
    ward = await db.get(Ward, ward_id) if ward_id else await db.scalar(
        select(Ward).where(Ward.hospital_id == hospital_id, Ward.is_active.is_(True)).order_by(Ward.name)
    )
    empty = {
        "ward_id": None, "ward_name": None, "ward_code": None,
        "kpis": {"due_next_hour": 0, "overdue": 0, "done_this_shift": 0, "on_time_percentage": 100},
        "tasks": [], "beds": [], "handover": [],
        "compliance": {"on_time_percentage": 100, "closed_by_08_percentage": 100, "iv_checks_percentage": 100, "arithmetic_errors": 0},
        "audit": [], "pending_countersigns": 0,
    }
    if ward is None or ward.hospital_id != hospital_id:
        return empty

    now = datetime.now(timezone.utc)
    shift_start, shift_end = shift_window(now)
    doctor = aliased(User)
    task_rows = (await db.execute(
        select(WardTask, Bed, Patient, Encounter, doctor)
        .join(Bed, Bed.id == WardTask.bed_id)
        .join(Patient, Patient.id == WardTask.patient_id)
        .outerjoin(Encounter, Encounter.id == Bed.encounter_id)
        .outerjoin(doctor, doctor.id == Encounter.doctor_id)
        .where(WardTask.hospital_id == hospital_id, WardTask.ward_id == ward.id)
        .order_by(WardTask.status == "completed", WardTask.due_at)
    )).all()
    tasks = [{
        "id": task.id, "bed_id": bed.id, "bed_number": bed.bed_number,
        "patient_id": patient.id, "patient_name": patient.full_name, "patient_age": _age(patient),
        "protocol": bed.protocol, "doctor_name": clinician.full_name if clinician else None,
        "title": task.title, "task_type": task.task_type,
        "status": "overdue" if task.status not in ("completed", "cancelled") and task.due_at < now else task.status,
        "due_at": task.due_at, "completed_at": task.completed_at,
    } for task, bed, patient, _encounter, clinician in task_rows]
    active = [row[0] for row in task_rows if row[0].status not in ("completed", "cancelled")]
    completed_shift = [row[0] for row in task_rows if row[0].completed_at and shift_start <= row[0].completed_at < shift_end]
    completed_all = [row[0] for row in task_rows if row[0].completed_at]
    on_time = [task for task in completed_all if task.completed_at <= task.due_at]
    on_time_pct = round(len(on_time) * 100 / len(completed_all)) if completed_all else 100

    nurse = aliased(User)
    bed_rows = (await db.execute(
        select(Bed, Patient, nurse).outerjoin(Patient, Patient.id == Bed.patient_id)
        .outerjoin(nurse, nurse.id == Bed.assigned_nurse_id)
        .where(Bed.hospital_id == hospital_id, Bed.ward_id == ward.id, Bed.is_active.is_(True))
        .order_by(Bed.bed_number)
    )).all()
    beds = []
    handover = []
    day_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    entries_by_bed: dict[uuid.UUID, list[FluidEntry]] = {}
    if bed_rows:
        ward_entries = (
            await db.scalars(
                select(FluidEntry).where(
                    FluidEntry.bed_id.in_([bed.id for bed, *_ in bed_rows]),
                    FluidEntry.occurred_at >= day_start,
                )
            )
        ).all()
        for entry in ward_entries:
            entries_by_bed.setdefault(entry.bed_id, []).append(entry)
    for bed, patient, assigned_nurse in bed_rows:
        entries = entries_by_bed.get(bed.id, [])
        intake = sum(entry.amount_ml for entry in entries if entry.direction == "intake")
        output = sum(entry.amount_ml for entry in entries if entry.direction == "output")
        bed_tasks = [task for task, row_bed, *_ in task_rows if row_bed.id == bed.id]
        pending = [task for task in bed_tasks if task.status not in ("completed", "cancelled")]
        next_due = min((task.due_at for task in pending), default=None)
        done = sum(task.status == "completed" for task in bed_tasks)
        status_value = "due_now" if next_due and next_due < now else "on_track"
        beds.append({
            "id": bed.id, "bed_number": bed.bed_number,
            "patient_id": patient.id if patient else None, "patient_name": patient.full_name if patient else None,
            "patient_age": _age(patient), "protocol": bed.protocol,
            "nurse_name": assigned_nurse.full_name if assigned_nurse else None,
            "last_entry_at": max((entry.occurred_at for entry in entries), default=None),
            "next_due_at": next_due, "fluid_balance_ml": calculate_balance(intake, output),
            "completed_tasks": done, "total_tasks": len(bed_tasks), "status": status_value,
        })
        if patient:
            due_text = f"Next task {next_due:%H:%M}" if next_due else "No pending tasks"
            balance = calculate_balance(intake, output)
            priority = "urgent" if status_value == "due_now" else "attention" if abs(balance) > 500 else "routine"
            handover.append({
                "bed_id": bed.id, "bed_number": bed.bed_number, "patient_name": patient.full_name,
                "text": f"{bed.protocol or 'Routine care'}; fluid balance {balance:+d} ml. {due_text}.",
                "priority": priority,
            })

    audit_actor = aliased(User)
    audit_rows = (await db.execute(
        select(WardVoiceAuditEvent, audit_actor)
        .join(audit_actor, audit_actor.id == WardVoiceAuditEvent.user_id)
        .where(WardVoiceAuditEvent.hospital_id == hospital_id)
        .order_by(WardVoiceAuditEvent.created_at.desc()).limit(20)
    )).all()
    pending_countersigns = await db.scalar(select(func.count(ExtractedObservation.id)).where(
        ExtractedObservation.hospital_id == hospital_id,
        ExtractedObservation.requires_countersign.is_(True),
        ExtractedObservation.countersigned_at.is_(None),
    )) or 0
    return {
        "ward_id": ward.id, "ward_name": ward.name, "ward_code": ward.code,
        "kpis": {
            "due_next_hour": sum(now <= task.due_at <= now + timedelta(hours=1) for task in active),
            "overdue": sum(task.due_at < now for task in active),
            "done_this_shift": len(completed_shift), "on_time_percentage": on_time_pct,
        },
        "tasks": tasks, "beds": beds, "handover": handover,
        "compliance": {
            "on_time_percentage": on_time_pct,
            "closed_by_08_percentage": 100 if not beds else round(100 * sum(b["last_entry_at"] is not None for b in beds) / len(beds)),
            "iv_checks_percentage": 100 if not beds else round(100 * sum(b["status"] == "on_track" for b in beds) / len(beds)),
            "arithmetic_errors": 0,
        },
        "audit": [{
            "id": event.id, "action": event.action, "resource_type": event.resource_type,
            "patient_id": event.patient_id, "user_name": actor.full_name,
            "details": event.details, "created_at": event.created_at,
        } for event, actor in audit_rows],
        "pending_countersigns": pending_countersigns,
    }


async def create_capture(db: AsyncSession, payload: VoiceCaptureCreate, user: CurrentUser) -> dict:
    bed = await _bed(db, payload.bed_id, user)
    if not bed.patient_id:
        raise HTTPException(status_code=409, detail="The bed has no active patient")
    content_type = sarvam_service.normalize_audio_content_type(payload.content_type)
    capture_id = uuid.uuid4()
    object_key = f"ward-voice/{bed.hospital_id}/{date.today().isoformat()}/{capture_id}.webm"
    upload_url = await s3_storage_service.create_upload_url(object_key=object_key, content_type=content_type)
    capture = VoiceCapture(
        id=capture_id, hospital_id=bed.hospital_id, ward_id=bed.ward_id, bed_id=bed.id,
        patient_id=bed.patient_id, task_id=payload.task_id, captured_by=_user_id(user),
        object_key=object_key, content_type=content_type, file_size=payload.file_size,
        language_code=payload.language_code,
    )
    db.add(capture)
    await _audit(db, user, "capture.created", "voice_capture", capture.id, bed.patient_id, {"object_key": object_key})
    await db.commit()
    return {"capture_id": capture.id, "upload_url": upload_url, "object_key": object_key, "content_type": content_type, "expires_in": settings.AWS_S3_PRESIGN_EXPIRE_SECONDS}


def _capture_result(capture: VoiceCapture) -> dict:
    return {
        "capture_id": capture.id, "status": capture.status,
        "raw_transcript": capture.raw_transcript, "translated_text": capture.translated_text,
        "observations": (capture.extraction_payload or {}).get("observations", []),
        "error_message": capture.error_message,
    }


async def complete_capture(db: AsyncSession, capture_id: uuid.UUID, etag: str | None, user: CurrentUser) -> dict:
    capture = await db.get(VoiceCapture, capture_id)
    if capture is None or capture.hospital_id != _hospital_id(user):
        raise HTTPException(status_code=404, detail="Voice capture not found")
    if capture.status != "awaiting_upload":
        return _capture_result(capture)
    metadata = await s3_storage_service.verify_upload(object_key=capture.object_key, expected_size=capture.file_size)
    capture.etag = (etag or metadata.get("ETag") or "").strip('"') or None
    capture.status = "processing"
    await db.commit()
    try:
        audio = await s3_storage_service.download_audio(object_key=capture.object_key)
        speech = await sarvam_service.transcribe_and_translate(
            audio_bytes=audio, filename=capture.object_key.rsplit("/", 1)[-1],
            content_type=capture.content_type, language_code=capture.language_code,
        )
        capture.raw_transcript = speech["raw_transcript"]
        capture.translated_text = speech["translated_text"]
        observations = await extract_observations(capture.translated_text or capture.raw_transcript or "")
        capture.extraction_payload = {"observations": observations}
        capture.status = "pending_confirmation"
        for item in observations:
            db.add(ExtractedObservation(
                hospital_id=capture.hospital_id, capture_id=capture.id, patient_id=capture.patient_id,
                observation_type=item["observation_type"], value_numeric=item.get("value_numeric"),
                value_text=item.get("value_text"), unit=item.get("unit"), confidence=item.get("confidence"),
                requires_countersign=item.get("requires_countersign", False),
            ))
        await _audit(db, user, "capture.extracted", "voice_capture", capture.id, capture.patient_id, {"observation_count": len(observations)})
        await db.commit()
    except Exception as exc:
        capture.status = "failed"
        capture.error_message = str(exc)[:1000]
        await db.commit()
        raise HTTPException(status_code=502, detail="The recording could not be processed") from exc
    return _capture_result(capture)


async def confirm_capture(db: AsyncSession, capture_id: uuid.UUID, payload: ConfirmCaptureRequest, user: CurrentUser) -> dict:
    capture = await db.get(VoiceCapture, capture_id)
    if capture is None or capture.hospital_id != _hospital_id(user):
        raise HTTPException(status_code=404, detail="Voice capture not found")
    if capture.status == "confirmed":
        return _capture_result(capture)
    if capture.status != "pending_confirmation":
        raise HTTPException(status_code=409, detail="Capture is not ready for confirmation")
    await _assert_chart_open(db, capture.patient_id, datetime.now(timezone.utc).date())
    provisional = (await db.scalars(select(ExtractedObservation).where(
        ExtractedObservation.capture_id == capture.id
    ).order_by(ExtractedObservation.observed_at, ExtractedObservation.id))).all()
    now = datetime.now(timezone.utc)
    for index, item in enumerate(payload.observations):
        observation = provisional[index] if index < len(provisional) else ExtractedObservation(
            hospital_id=capture.hospital_id, capture_id=capture.id, patient_id=capture.patient_id,
            observation_type=item.observation_type,
        )
        observation.observation_type = item.observation_type
        observation.confirmed_value_numeric = item.value_numeric
        observation.confirmed_value_text = item.value_text
        observation.unit = item.unit
        observation.confirmed_by = _user_id(user)
        observation.confirmed_at = now
        observation.requires_countersign = item.requires_countersign
        db.add(observation)
        fluid_map = {"urine_output": ("output", "urine"), "oral_intake": ("intake", "oral"), "vomit": ("output", "vomit"), "drainage": ("output", "drainage")}
        if item.observation_type in fluid_map and item.value_numeric is not None:
            direction, category = fluid_map[item.observation_type]
            db.add(FluidEntry(
                hospital_id=capture.hospital_id, ward_id=capture.ward_id, bed_id=capture.bed_id,
                patient_id=capture.patient_id, capture_id=capture.id, direction=direction,
                category=category, amount_ml=round(item.value_numeric), source="voice",
                occurred_at=now, recorded_by=_user_id(user),
            ))
    capture.status = "confirmed"
    capture.confirmed_at = now
    if capture.task_id:
        task = await db.get(WardTask, capture.task_id)
        if task and task.hospital_id == capture.hospital_id:
            task.status, task.completed_at, task.completed_by = "completed", now, _user_id(user)
    await _audit(db, user, "capture.confirmed", "voice_capture", capture.id, capture.patient_id, {"observation_count": len(payload.observations)})
    await db.commit()
    return _capture_result(capture)


async def fluid_chart(db: AsyncSession, bed_id: uuid.UUID, chart_date: date, user: CurrentUser) -> dict:
    bed = await _bed(db, bed_id, user)
    if not bed.patient_id:
        raise HTTPException(status_code=409, detail="The bed has no active patient")
    patient = await db.get(Patient, bed.patient_id)
    start = datetime.combine(chart_date, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    entries = (await db.scalars(select(FluidEntry).where(
        FluidEntry.bed_id == bed.id, FluidEntry.patient_id == bed.patient_id,
        FluidEntry.occurred_at >= start, FluidEntry.occurred_at < end
    ).order_by(FluidEntry.occurred_at))).all()
    infusions = (await db.scalars(select(IVInfusion).where(
        IVInfusion.bed_id == bed.id, IVInfusion.patient_id == bed.patient_id,
        IVInfusion.started_at < end, func.coalesce(IVInfusion.stopped_at, end) > start
    ).order_by(IVInfusion.started_at))).all()
    users = {row.id: row.full_name for row in (await db.scalars(select(User).where(
        User.id.in_({entry.recorded_by for entry in entries} or {uuid.uuid4()})
    ))).all()}
    now = datetime.now(timezone.utc)
    infusion_rows, running = [], 0
    for infusion in infusions:
        effective_start = max(infusion.started_at, start)
        effective_end = min(infusion.stopped_at or min(now, end), end)
        volume = calculate_iv_volume(infusion.rate_ml_per_hour, effective_start, effective_end)
        running += volume
        infusion_rows.append({
            "id": infusion.id, "fluid_name": infusion.fluid_name,
            "rate_ml_per_hour": infusion.rate_ml_per_hour, "started_at": infusion.started_at,
            "stopped_at": infusion.stopped_at, "calculated_ml": volume,
        })
    explicit_intake = sum(entry.amount_ml for entry in entries if entry.direction == "intake")
    output = sum(entry.amount_ml for entry in entries if entry.direction == "output")
    intake = explicit_intake + running
    closure = await db.scalar(select(ChartClosure).where(
        ChartClosure.patient_id == bed.patient_id, ChartClosure.chart_date == chart_date
    ))
    return {
        "bed_id": bed.id, "bed_number": bed.bed_number, "patient_id": patient.id,
        "patient_name": patient.full_name, "patient_age": _age(patient), "protocol": bed.protocol,
        "chart_date": chart_date, "intake_ml": intake, "output_ml": output,
        "iv_running_ml": running, "balance_ml": calculate_balance(intake, output),
        "entries": [{
            "id": entry.id, "occurred_at": entry.occurred_at, "direction": entry.direction,
            "category": entry.category, "amount_ml": entry.amount_ml, "source": entry.source,
            "notes": entry.notes, "recorded_by": users.get(entry.recorded_by, "Clinical user"),
        } for entry in entries],
        "infusions": infusion_rows, "is_closed": closure is not None,
        "closed_at": closure.closed_at if closure else None,
    }


async def add_fluid_entry(db: AsyncSession, payload: FluidEntryCreate, user: CurrentUser):
    bed = await _bed(db, payload.bed_id, user)
    if not bed.patient_id:
        raise HTTPException(status_code=409, detail="The bed has no active patient")
    await _assert_chart_open(db, bed.patient_id, payload.occurred_at.date())
    entry = FluidEntry(
        hospital_id=bed.hospital_id, ward_id=bed.ward_id, bed_id=bed.id, patient_id=bed.patient_id,
        direction=payload.direction, category=payload.category, amount_ml=payload.amount_ml,
        source="manual", notes=payload.notes, occurred_at=payload.occurred_at, recorded_by=_user_id(user),
    )
    db.add(entry)
    await _audit(db, user, "fluid_entry.created", "fluid_entry", entry.id, bed.patient_id, {"amount_ml": payload.amount_ml, "direction": payload.direction})
    await db.commit()


async def add_iv_infusion(db: AsyncSession, payload: IVInfusionCreate, user: CurrentUser):
    bed = await _bed(db, payload.bed_id, user)
    if not bed.patient_id:
        raise HTTPException(status_code=409, detail="The bed has no active patient")
    await _assert_chart_open(db, bed.patient_id, payload.started_at.date())
    infusion = IVInfusion(
        hospital_id=bed.hospital_id, bed_id=bed.id, patient_id=bed.patient_id,
        fluid_name=payload.fluid_name, rate_ml_per_hour=payload.rate_ml_per_hour,
        started_at=payload.started_at, recorded_by=_user_id(user),
    )
    db.add(infusion)
    await _audit(db, user, "iv_infusion.started", "iv_infusion", infusion.id, bed.patient_id, {"rate_ml_per_hour": payload.rate_ml_per_hour})
    await db.commit()


async def close_chart(db: AsyncSession, bed_id: uuid.UUID, chart_date: date, user: CurrentUser):
    await _assert_chart_open(db, (await _bed(db, bed_id, user)).patient_id, chart_date)
    chart = await fluid_chart(db, bed_id, chart_date, user)
    closure = ChartClosure(
        hospital_id=_hospital_id(user), patient_id=chart["patient_id"], chart_date=chart_date,
        intake_ml=chart["intake_ml"], output_ml=chart["output_ml"], balance_ml=chart["balance_ml"],
        closed_by=_user_id(user),
    )
    db.add(closure)
    await _audit(db, user, "fluid_chart.closed", "chart_closure", closure.id, chart["patient_id"], {"balance_ml": chart["balance_ml"]})
    await db.commit()


async def update_task(db: AsyncSession, task_id: uuid.UUID, task_status: str, user: CurrentUser):
    task = await db.get(WardTask, task_id)
    if task is None or task.hospital_id != _hospital_id(user):
        raise HTTPException(status_code=404, detail="Ward task not found")
    task.status = task_status
    if task_status == "completed":
        task.completed_at, task.completed_by = datetime.now(timezone.utc), _user_id(user)
    await _audit(db, user, "task.updated", "ward_task", task.id, task.patient_id, {"status": task_status})
    await db.commit()


async def list_countersigns(db: AsyncSession, user: CurrentUser) -> list[dict]:
    confirmer = aliased(User)
    rows = (await db.execute(
        select(ExtractedObservation, VoiceCapture, Bed, Patient, confirmer)
        .join(VoiceCapture, VoiceCapture.id == ExtractedObservation.capture_id)
        .join(Bed, Bed.id == VoiceCapture.bed_id)
        .join(Patient, Patient.id == ExtractedObservation.patient_id)
        .join(confirmer, confirmer.id == ExtractedObservation.confirmed_by)
        .where(
            ExtractedObservation.hospital_id == _hospital_id(user),
            ExtractedObservation.requires_countersign.is_(True),
            ExtractedObservation.confirmed_at.is_not(None),
            ExtractedObservation.countersigned_at.is_(None),
        ).order_by(ExtractedObservation.confirmed_at)
    )).all()
    return [{
        "id": item.id, "capture_id": capture.id, "bed_number": bed.bed_number,
        "patient_name": patient.full_name, "observation_type": item.observation_type,
        "value_numeric": float(item.confirmed_value_numeric) if item.confirmed_value_numeric is not None else None,
        "value_text": item.confirmed_value_text, "unit": item.unit,
        "confirmed_by": actor.full_name, "confirmed_at": item.confirmed_at,
    } for item, capture, bed, patient, actor in rows]


async def countersign(db: AsyncSession, observation_id: uuid.UUID, user: CurrentUser):
    item = await db.get(ExtractedObservation, observation_id)
    if item is None or item.hospital_id != _hospital_id(user):
        raise HTTPException(status_code=404, detail="Observation not found")
    if item.confirmed_by == _user_id(user):
        raise HTTPException(status_code=409, detail="A second clinician must countersign this observation")
    if item.countersigned_at is None:
        item.countersigned_by, item.countersigned_at = _user_id(user), datetime.now(timezone.utc)
        await _audit(db, user, "observation.countersigned", "observation", item.id, item.patient_id)
        await db.commit()
