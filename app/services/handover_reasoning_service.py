"""Structured clinician handover reasoning from the chart and spoken update."""

import json

import httpx

from app.core.config import settings
from app.services.patient_builder_service import _output_text

SECTION = {"type": "array", "items": {"type": "string"}}
HANDOVER_SCHEMA = {
    "type": "object",
    "properties": {
        "situation": SECTION,
        "background": SECTION,
        "assessment": SECTION,
        "recommendations": SECTION,
        "immediate_priorities": SECTION,
        "risks_and_watchouts": SECTION,
        "pending_actions": SECTION,
        "contingency_plan": SECTION,
        "clinical_reasoning": SECTION,
    },
    "required": [
        "situation",
        "background",
        "assessment",
        "recommendations",
        "immediate_priorities",
        "risks_and_watchouts",
        "pending_actions",
        "contingency_plan",
        "clinical_reasoning",
    ],
    "additionalProperties": False,
}

INSTRUCTIONS = """Create a concise, point-wise clinician-to-clinician handover using SBAR.
Use only facts supplied in the patient chart or spoken handover. Do not invent observations,
diagnoses, medicines, thresholds, or tasks. Put the most time-critical facts first. Remove
duplicates while preserving medication doses and explicit timing. In clinical_reasoning,
briefly connect documented evidence to its operational significance; label uncertainty and
never present an inference as a confirmed fact. Keep empty arrays when information is absent.
This is a clinician-review draft, not autonomous medical advice."""


async def generate_summary(*, chart_context: dict, translated_instructions: str) -> dict:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{settings.OPENAI_BASE_URL.rstrip('/')}/responses",
            headers={
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "content-type": "application/json",
            },
            json={
                "model": settings.OPENAI_MODEL,
                "store": False,
                "max_output_tokens": 2600,
                "reasoning": {"effort": settings.OPENAI_REASONING_EFFORT},
                "instructions": INSTRUCTIONS,
                "input": json.dumps(
                    {
                        "patient_chart": chart_context,
                        "spoken_handover_english": translated_instructions,
                    },
                    default=str,
                ),
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "clinical_handover",
                        "strict": True,
                        "schema": HANDOVER_SCHEMA,
                    }
                },
            },
        )
    response.raise_for_status()
    return json.loads(_output_text(response.json()))
