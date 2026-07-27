"""Verify Redis, API liveness, worker presence, queues, and registered tasks."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping

import httpx
import redis

from app.celery_app import celery_app
from app.core.config import settings


EXPECTED_WORKERS = {
    "transcription@": {
        "queue": "voice_transcription",
        "tasks": {"voice.transcribe", "discharge.transcribe"},
    },
    "patient-emr@": {
        "queue": "patient_emr",
        "tasks": {
            "voice.build_patient_emr",
            "report.summarize",
            "discharge.generate",
        },
    },
}


def _worker_payload(
    payload: Mapping[str, object] | None, worker_prefix: str
) -> object | None:
    if not payload:
        return None
    return next(
        (value for name, value in payload.items() if name.startswith(worker_prefix)),
        None,
    )


def verify_broker() -> None:
    client = redis.Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=10,
        socket_timeout=10,
    )
    try:
        if client.ping() is not True:
            raise RuntimeError("Redis PING did not return success")
    finally:
        client.close()
    print("redis: ok")


def verify_api(api_url: str) -> None:
    response = httpx.get(f"{api_url.rstrip('/')}/healthz", timeout=10)
    response.raise_for_status()
    if response.json().get("status") != "ok":
        raise RuntimeError("API health response was not ok")
    print("api: ok")


def verify_workers() -> None:
    inspector = celery_app.control.inspect(timeout=10)
    pings = inspector.ping() or {}
    registered = inspector.registered() or {}
    active_queues = inspector.active_queues() or {}
    errors: list[str] = []

    for worker_prefix, expectation in EXPECTED_WORKERS.items():
        if _worker_payload(pings, worker_prefix) is None:
            errors.append(f"missing worker {worker_prefix}*")
            continue

        worker_tasks = set(_worker_payload(registered, worker_prefix) or [])
        missing_tasks = expectation["tasks"] - worker_tasks
        if missing_tasks:
            errors.append(
                f"{worker_prefix}* missing tasks: {', '.join(sorted(missing_tasks))}"
            )

        queue_rows = _worker_payload(active_queues, worker_prefix) or []
        queue_names = {
            row.get("name") for row in queue_rows if isinstance(row, Mapping)
        }
        if expectation["queue"] not in queue_names:
            errors.append(
                f"{worker_prefix}* is not consuming {expectation['queue']}"
            )

    if errors:
        raise RuntimeError("; ".join(errors))

    print("workers: ok")
    print("queues: voice_transcription, patient_emr")
    print(
        "tasks: voice.transcribe, voice.build_patient_emr, "
        "report.summarize, discharge.transcribe, discharge.generate"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--broker-only", action="store_true")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    try:
        verify_broker()
        if not args.broker_only:
            verify_api(args.api_url)
            verify_workers()
    except Exception as exc:
        print(f"async stack check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
