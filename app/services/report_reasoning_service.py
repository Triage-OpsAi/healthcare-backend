"""Extract a conservative, reviewable summary from an uploaded clinical report."""

import base64
import json

import httpx

from app.core.config import settings
from app.services.patient_builder_service import _output_text


REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "quality_acceptable": {"type": "boolean"},
        "quality_message": {"type": "string"},
        "document_type": {"type": "string"},
        "summary": {"type": "string"},
        "key_findings": {"type": "array", "items": {"type": "string"}},
        "details": {
            "type": "object",
            "properties": {
                "report_date": {"type": "string"},
                "facility": {"type": "string"},
                "clinician": {"type": "string"},
                "patient_name_on_document": {"type": "string"},
            },
            "required": [
                "report_date",
                "facility",
                "clinician",
                "patient_name_on_document",
            ],
            "additionalProperties": False,
        },
    },
    "required": [
        "quality_acceptable",
        "quality_message",
        "document_type",
        "summary",
        "key_findings",
        "details",
    ],
    "additionalProperties": False,
}

INSTRUCTIONS = """You summarize uploaded clinical documents for clinician review.
Extract only legible facts from the document. Never infer a diagnosis beyond what the report
explicitly states. Do not interpret CT/MRI/X-ray imagery; summarize only readable report text.
Set quality_acceptable=false when the image is blurred, cropped, glared, rotated beyond reliable
reading, too dark, or key text is illegible. In that case explain exactly how to retake the photo
and leave summary/key_findings empty. Keep the summary concise and preserve measurements, dates,
medication names, and abnormal/normal findings exactly as written. This is documentation support,
not independent medical advice."""


async def summarize_report(
    *, file_bytes: bytes, filename: str, content_type: str
) -> dict:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    encoded = base64.b64encode(file_bytes).decode("ascii")
    if content_type.startswith("image/"):
        document_input = {
            "type": "input_image",
            "image_url": f"data:{content_type};base64,{encoded}",
            "detail": "original",
        }
    else:
        document_input = {
            "type": "input_file",
            "filename": filename,
            "file_data": f"data:{content_type};base64,{encoded}",
        }

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
                "max_output_tokens": 1800,
                "reasoning": {"effort": settings.OPENAI_REASONING_EFFORT},
                "instructions": INSTRUCTIONS,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Assess readability and summarize this clinical report.",
                            },
                            document_input,
                        ],
                    }
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "clinical_report_summary",
                        "strict": True,
                        "schema": REPORT_SCHEMA,
                    }
                },
            },
        )
    response.raise_for_status()
    return json.loads(_output_text(response.json()))
