"""Build or safely match a patient and encounter from a voice transcript."""

import json
import re
import uuid
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.config import settings
from app.db.models import Encounter, Patient


class PatientDetailsError(ValueError):
    """The voice transcript did not contain safe, usable patient identity data."""


VOICE_INTAKE_SYSTEM_PROMPT = """You extract patient demographics and clinical documentation \
from a nurse or doctor's spoken intake. The transcript may be translated from an Indian \
regional language.

Extract only information explicitly present in the transcript. Never guess identity data. \
Return an empty patient full name when it was not spoken clearly; the system will create an \
editable unidentified-patient record with a generated ID. \
patient_reference means a hospital ID, MRN, ABHA ID, or other explicitly spoken identifier. \
For date_of_birth use YYYY-MM-DD only when a complete date was spoken. Keep age separately \
when only an age was spoken. Put consultation content into the SOAP note. Do not output \
medical codes."""

SOAP_PROPERTIES = {
    "chief_complaint": {"type": "string"},
    "subjective": {"type": "string"},
    "objective": {"type": "string"},
    "assessment": {"type": "string"},
    "plan": {"type": "string"},
    "diagnoses": {"type": "array", "items": {"type": "string"}},
    "symptoms": {"type": "array", "items": {"type": "string"}},
    "medications": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "dosage": {"type": "string"},
                "frequency": {"type": "string"},
            },
            "required": ["name", "dosage", "frequency"],
            "additionalProperties": False,
        },
    },
}

VOICE_INTAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "patient": {
            "type": "object",
            "properties": {
                "full_name": {"type": "string"},
                "age": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                "date_of_birth": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "gender": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "phone": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "patient_reference": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": [
                "full_name",
                "age",
                "date_of_birth",
                "gender",
                "phone",
                "patient_reference",
            ],
            "additionalProperties": False,
        },
        "department": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "clinical_note": {
            "type": "object",
            "properties": SOAP_PROPERTIES,
            "required": list(SOAP_PROPERTIES),
            "additionalProperties": False,
        },
    },
    "required": ["patient", "department", "clinical_note"],
    "additionalProperties": False,
}


def _output_text(payload: dict) -> str:
    if payload.get("status") != "completed":
        raise RuntimeError(f"OpenAI response did not complete: {payload.get('status')}")
    for output in payload.get("output", []):
        if output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if content.get("type") == "refusal":
                raise RuntimeError(
                    f"OpenAI refused to extract the intake: {content.get('refusal')}"
                )
            if content.get("type") == "output_text":
                return content["text"]
    raise RuntimeError("OpenAI response contained no voice intake")


async def extract_voice_intake(translated_text: str) -> dict:
    """Extract demographics and a SOAP note in one strict-schema model call."""
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set -- add your OpenAI API key to .env")
    if not translated_text.strip():
        raise PatientDetailsError("No speech was detected in the recording.")

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{settings.OPENAI_BASE_URL.rstrip('/')}/responses",
            headers={
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "content-type": "application/json",
            },
            json={
                "model": settings.OPENAI_MODEL,
                "max_output_tokens": 1600,
                "reasoning": {"effort": settings.OPENAI_REASONING_EFFORT},
                "instructions": VOICE_INTAKE_SYSTEM_PROMPT,
                "input": translated_text,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "voice_patient_intake",
                        "strict": True,
                        "schema": VOICE_INTAKE_SCHEMA,
                    }
                },
            },
        )
    response.raise_for_status()
    intake = json.loads(_output_text(response.json()))
    patient = intake["patient"]
    patient["full_name"] = " ".join(patient["full_name"].split())
    if patient["age"] is not None and not 0 <= patient["age"] <= 130:
        raise PatientDetailsError("The spoken patient age must be between 0 and 130.")
    return intake


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    prefix = "+" if value.strip().startswith("+") else ""
    digits = re.sub(r"\D", "", value)
    return f"{prefix}{digits}" if digits else None


def _date_of_birth(patient_data: dict) -> datetime | None:
    raw_dob = patient_data.get("date_of_birth")
    if raw_dob:
        try:
            parsed = date.fromisoformat(raw_dob)
        except ValueError as exc:
            raise PatientDetailsError("The spoken date of birth was not valid.") from exc
        if parsed > date.today():
            raise PatientDetailsError("Patient date of birth cannot be in the future.")
        return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)

    age = patient_data.get("age")
    if age is None:
        return None
    estimated = date(date.today().year - age, 1, 1)
    return datetime(estimated.year, estimated.month, estimated.day, tzinfo=timezone.utc)


async def build_patient_and_encounter(
    db: AsyncSession,
    *,
    intake: dict,
    current_user: CurrentUser,
    department_override: str | None = None,
) -> tuple[Patient, Encounter, bool]:
    """Resolve a stable identifier or create a patient, then create the visit.

    Names are deliberately not used for automatic matching because two patients
    can share a name. A patient reference is preferred; phone is the fallback.
    """
    if not current_user.hospital_id:
        raise PatientDetailsError("A clinical hospital workspace is required.")

    hospital_id = uuid.UUID(current_user.hospital_id)
    data = intake["patient"]
    reference = _clean_optional(data.get("patient_reference"))
    phone = _normalize_phone(data.get("phone"))
    patient: Patient | None = None

    # Serialize only requests resolving the same stable identity. This
    # transaction-scoped PostgreSQL lock works across horizontally scaled API
    # workers and closes the select-then-insert race without locking the table.
    match_key = (
        f"{hospital_id}:reference:{reference.lower()}"
        if reference
        else f"{hospital_id}:phone:{phone}" if phone else None
    )
    if match_key:
        await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(match_key))))

    if reference:
        patient = await db.scalar(
            select(Patient)
            .where(
                Patient.hospital_id == hospital_id,
                func.lower(Patient.abha_id) == reference.lower(),
            )
            .limit(1)
        )
    elif phone:
        patient = await db.scalar(
            select(Patient)
            .where(Patient.hospital_id == hospital_id, Patient.phone == phone)
            .limit(1)
        )

    created = patient is None
    dob = _date_of_birth(data)
    gender = _clean_optional(data.get("gender"))
    if patient is None:
        patient = Patient(
            hospital_id=hospital_id,
            abha_id=reference,
            full_name=data["full_name"] or "Unidentified patient",
            date_of_birth=dob,
            gender=gender,
            phone=phone,
        )
        db.add(patient)
        await db.flush()
        if not data["full_name"]:
            patient.full_name = f"Unidentified patient {str(patient.id)[:8].upper()}"
    else:
        # Preserve existing identity values and only fill information that is absent.
        patient.full_name = patient.full_name or data["full_name"]
        patient.date_of_birth = patient.date_of_birth or dob
        patient.gender = patient.gender or gender
        patient.phone = patient.phone or phone
        patient.abha_id = patient.abha_id or reference

    department = _clean_optional(department_override) or _clean_optional(
        intake.get("department")
    )
    encounter = Encounter(
        hospital_id=hospital_id,
        patient_id=patient.id,
        doctor_id=uuid.UUID(current_user.user_id),
        department=department,
        status="open",
    )
    db.add(encounter)
    await db.flush()
    return patient, encounter, created


def patient_age(patient: Patient) -> int | None:
    if not patient.date_of_birth:
        return None
    born = patient.date_of_birth.date()
    today = date.today()
    return today.year - born.year - (
        (today.month, today.day) < (born.month, born.day)
    )
