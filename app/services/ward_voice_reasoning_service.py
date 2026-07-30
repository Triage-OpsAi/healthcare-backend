"""Conservative extraction of nursing observations; all output stays provisional."""
import json

import httpx

from app.core.config import settings
from app.services.patient_builder_service import _output_text


OBSERVATION_SCHEMA = {
    "type": "object",
    "properties": {
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "observation_type": {
                        "type": "string",
                        "enum": ["systolic_bp", "diastolic_bp", "temperature", "pulse", "spo2", "urine_output", "fluid_output", "oral_intake", "consumable", "vomit", "drainage", "note"],
                    },
                    "value_numeric": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                    "value_text": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "unit": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "requires_countersign": {"type": "boolean"},
                },
                "required": ["observation_type", "value_numeric", "value_text", "unit", "confidence", "requires_countersign"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["observations"],
    "additionalProperties": False,
}

INSTRUCTIONS = """Extract only explicitly spoken nursing observations from the transcript.
Do not infer, calculate, normalize, diagnose, or fill missing fields. Preserve the spoken
numeric fluid amount and its original unit (ml, litre, or liter); deterministic application
code performs unit conversion after nurse confirmation. Classify drinking/water as oral_intake
and preserve the consumed item (for example, water or juice) in value_text. Classify meals,
foods, supplements, and other consumed items as consumable. Create one consumable observation
for each explicitly named item; if only a meal such as breakfast is spoken, store that meal
name in value_text. Preserve an explicitly spoken quantity in value_numeric and unit. Every
oral_intake and consumable observation must require a doctor countersign.
Classify an explicitly spoken output volume with no urine/vomit/drain type as fluid_output;
do not downgrade it to a note. Use mmHg for blood pressure, C for temperature, /min for pulse,
and % for SpO2. Mark clinically unusual values or confidence below 0.85 as requiring
countersign. This output is a suggestion and must never be charted without nurse confirmation."""


async def extract_observations(transcript: str) -> list[dict]:
    if not transcript.strip():
        return []
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{settings.OPENAI_BASE_URL.rstrip('/')}/responses",
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "content-type": "application/json"},
            json={
                "model": settings.OPENAI_MODEL,
                "store": False,
                "max_output_tokens": 1200,
                "reasoning": {"effort": settings.OPENAI_REASONING_EFFORT},
                "instructions": INSTRUCTIONS,
                "input": transcript,
                "text": {"format": {"type": "json_schema", "name": "ward_observations", "strict": True, "schema": OBSERVATION_SCHEMA}},
            },
        )
    response.raise_for_status()
    return json.loads(_output_text(response.json()))["observations"]
