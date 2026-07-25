from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "meridian_voice_pipeline",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.workers.voice_tasks",
        "app.workers.report_tasks",
        "app.workers.discharge_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "voice.transcribe": {"queue": "voice_transcription"},
        "voice.build_patient_emr": {"queue": "patient_emr"},
        "report.summarize": {"queue": "patient_emr"},
        "discharge.transcribe": {"queue": "voice_transcription"},
        "discharge.generate": {"queue": "patient_emr"},
    },
    broker_transport_options={"visibility_timeout": 3600},
)
