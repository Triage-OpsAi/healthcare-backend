# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv "${VIRTUAL_ENV}"

COPY requirements.txt /tmp/requirements.txt

# Keep compilers out of the runtime image. They are available here in case a
# dependency does not publish a wheel for the selected Python/platform pair.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential \
    && python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --requirement /tmp/requirements.txt \
    && apt-get purge --yes --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*


FROM ${PYTHON_IMAGE} AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

LABEL org.opencontainers.image.title="Meridian EMR Backend" \
      org.opencontainers.image.description="FastAPI API and Celery workers for the Meridian EMR platform"

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" app \
    && useradd \
        --uid "${APP_UID}" \
        --gid "${APP_GID}" \
        --create-home \
        --home-dir /home/app \
        --shell /usr/sbin/nologin \
        app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts

USER app

EXPOSE 8000

STOPSIGNAL SIGTERM

# This image is also used by non-HTTP Celery services, so health checks belong
# in the ECS service/task definitions rather than at image level.
#
# Voice worker command:
# celery -A app.celery_app.celery_app worker -Q voice_transcription
#   --loglevel=INFO --hostname=transcription@%h --concurrency=1
#
# Patient/EMR worker command:
# celery -A app.celery_app.celery_app worker -Q patient_emr
#   --loglevel=INFO --hostname=patient-emr@%h --concurrency=1
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
