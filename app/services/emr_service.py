"""
Orchestrates the full pipeline discussed in the architecture review:

  audio -> Sarvam (transcribe + translate) -> LLM structuring into a SOAP
  note -> LLM entity extraction -> coding_service lookup against
  SNOMED/ICD/drug_master -> saved as 'pending_review' for a doctor to
  approve before anything is considered final.

In production this runs inside a queue worker (Celery task triggered by
the API route), not inline in the request -- see api/routes_emr.py for
where that hook goes. It's written as a plain async function here so the
pipeline logic is testable independent of the queue.
"""
import json
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import EMRRecord, EMRRecordCode
from app.services import coding_service, sarvam_service

STRUCTURING_SYSTEM_PROMPT = """You are a clinical documentation assistant. You will be given \
an English transcript of a doctor's spoken or written notes from a patient consultation \
(translated from an Indian regional language). Convert it into a structured SOAP note.

Respond with ONLY valid JSON, no other text, in this exact shape:
{
  "chief_complaint": "string",
  "subjective": "string",
  "objective": "string",
  "assessment": "string",
  "plan": "string",
  "diagnoses": ["string", ...],
  "symptoms": ["string", ...],
  "medications": [{"name": "string", "dosage": "string", "frequency": "string"}, ...]
}

Do not invent information that isn't in the transcript. Leave fields as empty strings or \
empty lists if the transcript doesn't cover them. Never output a medical code -- only \
plain-language terms; coding is handled by a separate lookup step."""

SOAP_NOTE_SCHEMA = {
    "type": "object",
    "properties": {
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
    },
    "required": [
        "chief_complaint",
        "subjective",
        "objective",
        "assessment",
        "plan",
        "diagnoses",
        "symptoms",
        "medications",
    ],
    "additionalProperties": False,
}


async def structure_note(translated_text: str) -> dict:
    """Calls OpenAI to turn translated free text into a structured SOAP note
    plus a flat list of extracted terms for the coding lookup step."""
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set -- add your OpenAI API key to .env")

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{settings.OPENAI_BASE_URL.rstrip('/')}/responses",
            headers={
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "content-type": "application/json",
            },
            json={
                "model": settings.OPENAI_MODEL,
                "max_output_tokens": 1024,
                "reasoning": {"effort": "none"},
                "instructions": STRUCTURING_SYSTEM_PROMPT,
                "input": translated_text,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "soap_note",
                        "strict": True,
                        "schema": SOAP_NOTE_SCHEMA,
                    }
                },
            },
        )
    response.raise_for_status()
    payload = response.json()

    if payload.get("status") != "completed":
        raise RuntimeError(f"OpenAI response did not complete: {payload.get('status')}")

    for output in payload.get("output", []):
        if output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if content.get("type") == "refusal":
                raise RuntimeError(f"OpenAI refused to structure the note: {content.get('refusal')}")
            if content.get("type") == "output_text":
                return json.loads(content["text"])

    raise RuntimeError("OpenAI response contained no structured note")


async def add_code_suggestions(
    db: AsyncSession,
    *,
    record: EMRRecord,
    structured_note: dict,
) -> None:
    """Attach catalog-backed clinical code suggestions without committing.

    Keeping this operation transaction-neutral lets callers create a patient,
    encounter, record, and suggestions atomically.
    """
    candidate_terms = structured_note.get("diagnoses", []) + structured_note.get("symptoms", [])
    for term in candidate_terms:
        matches = await coding_service.match_clinical_term(db, term=term)
        if not matches:
            continue
        best = matches[0]
        db.add(
            EMRRecordCode(
                emr_record_id=record.id,
                medical_code_id=best["medical_code_id"],
                field_type="diagnosis",
                confidence_score=best["confidence_score"],
                confirmed_by_doctor=best["confidence_score"]
                >= coding_service.CONFIDENCE_AUTO_ACCEPT,
            )
        )

    for medication in structured_note.get("medications", []):
        await coding_service.match_drug(
            db,
            hospital_id=str(record.hospital_id),
            drug_text=medication.get("name", ""),
        )


async def process_uploaded_audio(
    db: AsyncSession,
    *,
    emr_record_id: uuid.UUID,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    language_code: str,
) -> EMRRecord:
    """Full pipeline for one EMR record. Idempotent-ish: safe to re-run on
    the same record if a step fails, since it just overwrites the derived
    fields rather than creating duplicates."""
    result = await db.execute(select(EMRRecord).where(EMRRecord.id == emr_record_id))
    record = result.scalar_one()

    # 1. Speech-to-text + translation (Sarvam)
    sarvam_output = await sarvam_service.transcribe_and_translate(
        audio_bytes=audio_bytes,
        filename=filename,
        content_type=content_type,
        language_code=language_code,
    )
    record.raw_transcript = sarvam_output["raw_transcript"]
    record.translated_text = sarvam_output["translated_text"]

    # 2. Structure into a SOAP note (LLM)
    structured = await structure_note(record.translated_text)
    record.structured_note = structured

    # 3. Extract codeable terms and look them up (never LLM-generated codes)
    await add_code_suggestions(db, record=record, structured_note=structured)

    record.status = "pending_review"
    await db.commit()
    await db.refresh(record)
    return record
