"""Background clinical-report quality check and OpenAI summary."""

import asyncio
import uuid

from celery import Task

from app.celery_app import celery_app
from app.db.database import AsyncSessionLocal
from app.db.models import PatientReport
from app.services import clinical_file_storage_service, report_reasoning_service

_report_loop: asyncio.AbstractEventLoop | None = None


def _run(coroutine):
    global _report_loop
    if _report_loop is None or _report_loop.is_closed():
        _report_loop = asyncio.new_event_loop()
    return _report_loop.run_until_complete(coroutine)


async def _set_failed(report_id: str, message: str) -> None:
    async with AsyncSessionLocal() as db:
        report = await db.get(PatientReport, uuid.UUID(report_id))
        if report and report.status not in {"ready", "approved"}:
            report.status = "failed"
            report.quality_message = message[:2000]
            await db.commit()


class ReportTask(Task):
    autoretry_for = (Exception,)
    retry_backoff = True
    retry_backoff_max = 60
    max_retries = 3
    acks_late = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        if args:
            _run(_set_failed(str(args[0]), str(exc)))
        super().on_failure(exc, task_id, args, kwargs, einfo)


async def _summarize(report_id: str) -> None:
    async with AsyncSessionLocal() as db:
        report = await db.get(PatientReport, uuid.UUID(report_id))
        if report is None or report.status in {"ready", "approved", "needs_reupload"}:
            return
        report.status = "processing"
        report.quality_message = None
        await db.commit()
        file_bytes = await clinical_file_storage_service.read(
            object_key=report.object_key
        )
        result = await report_reasoning_service.summarize_report(
            file_bytes=file_bytes,
            filename=report.object_key.rsplit("/", 1)[-1],
            content_type=report.content_type,
        )
        report.document_type = result["document_type"] or None
        report.extracted_details = result["details"]
        report.quality_message = result["quality_message"] or None
        if not result["quality_acceptable"]:
            report.status = "needs_reupload"
            report.summary = None
            report.key_findings = []
        else:
            report.status = "ready"
            report.summary = result["summary"]
            report.key_findings = result["key_findings"]
        await db.commit()


@celery_app.task(bind=True, base=ReportTask, name="report.summarize")
def summarize_report(self, report_id: str) -> None:
    _run(_summarize(report_id))
