"""Short-lived, tenant-scoped Redis cache with database-backed invalidation."""
import asyncio
import hashlib
import json
import time
from datetime import date

from redis.asyncio import Redis
from sqlalchemy import func, select
from app.core.config import settings
from app.db.models import AuditLog, EMRRecord, Encounter, Patient, PatientVisit, PatientSectionReview

_client = None
_retry_at = 0.0
TTL = 30


async def redis_call(method, *args, **kwargs):
    global _client, _retry_at
    if time.monotonic() < _retry_at:
        return None
    try:
        if _client is None:
            _client = Redis.from_url(settings.REDIS_URL, decode_responses=True,
                                     socket_connect_timeout=0.3, socket_timeout=0.3)
        return await asyncio.wait_for(getattr(_client, method)(*args, **kwargs), 0.35)
    except Exception:
        _retry_at = time.monotonic() + 30
        return None


async def cache_key(db, user):
    from app.services.doctor_service import _hospital_id
    hospital = _hospital_id(user)
    # A version derived from committed database changes also covers worker processes.
    parts = []
    for model, column in ((Patient, Patient.created_at), (Encounter, Encounter.created_at),
                          (PatientVisit, PatientVisit.created_at), (EMRRecord, EMRRecord.updated_at),
                          (PatientSectionReview, PatientSectionReview.updated_at), (AuditLog, AuditLog.timestamp)):
        condition = [model.hospital_id == hospital]
        if model is AuditLog:
            condition.append(~AuditLog.action.like("%.viewed"))
        parts += [select(func.count(model.id)).where(*condition).scalar_subquery(),
                  select(func.max(column)).where(*condition).scalar_subquery()]
    version = (await db.execute(select(*parts))).one()
    digest = hashlib.sha256(json.dumps([str(v) for v in version] + [date.today().isoformat(), sorted(user.permissions)], default=str).encode()).hexdigest()
    return f"patient-list:v1:{hospital}:{user.user_id}:{digest}"


async def patient_list(db, user, loader, refresh=False):
    from app.schemas.doctor import PatientDashboardSummary
    if time.monotonic() < _retry_at:
        return await loader(db, user)
    key = await cache_key(db, user)
    if not refresh:
        cached = await redis_call("get", key)
        if cached:
            try:
                return [PatientDashboardSummary.model_validate(row) for row in json.loads(cached)]
            except (ValueError, TypeError):
                pass
    result = await loader(db, user)
    await redis_call("set", key, json.dumps([row.model_dump(mode="json") for row in result]), ex=TTL)
    return result
