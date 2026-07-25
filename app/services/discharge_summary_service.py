"""OpenAI discharge reasoning plus deterministic clinician-readable PDF rendering."""

import io
import json
from xml.sax.saxutils import escape

import httpx
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.config import settings
from app.services.patient_builder_service import _output_text

MEDICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "dosage": {"type": "string"},
        "frequency": {"type": "string"},
        "duration": {"type": "string"},
        "instructions": {"type": "string"},
    },
    "required": ["name", "dosage", "frequency", "duration", "instructions"],
    "additionalProperties": False,
}

DISCHARGE_SCHEMA = {
    "type": "object",
    "properties": {
        "admission_reason": {"type": "array", "items": {"type": "string"}},
        "final_diagnoses": {"type": "array", "items": {"type": "string"}},
        "hospital_course": {"type": "array", "items": {"type": "string"}},
        "significant_findings": {"type": "array", "items": {"type": "string"}},
        "procedures": {"type": "array", "items": {"type": "string"}},
        "condition_at_discharge": {"type": "array", "items": {"type": "string"}},
        "medications": {"type": "array", "items": MEDICATION_SCHEMA},
        "follow_up": {"type": "array", "items": {"type": "string"}},
        "diet_and_activity": {"type": "array", "items": {"type": "string"}},
        "discharge_instructions": {"type": "array", "items": {"type": "string"}},
        "warning_signs": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "admission_reason",
        "final_diagnoses",
        "hospital_course",
        "significant_findings",
        "procedures",
        "condition_at_discharge",
        "medications",
        "follow_up",
        "diet_and_activity",
        "discharge_instructions",
        "warning_signs",
    ],
    "additionalProperties": False,
}

INSTRUCTIONS = """Create a clinician-review discharge summary from the supplied longitudinal
patient chart and translated spoken discharge instructions. Use only documented facts. Never add
a diagnosis, medication, result, follow-up interval, or warning sign that is not present. Prefer
the clinician's spoken discharge instructions when they clarify the plan. Resolve duplicates
without removing material instructions. Keep every section point-wise, concise, and clinically
specific. Preserve medication dose/frequency/duration exactly. Empty arrays are required when a
section has no documented information. The result is a draft until a clinician reviews it."""


async def generate_summary(*, chart_context: dict, translated_instructions: str) -> dict:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    payload = {
        "patient_chart": chart_context,
        "spoken_discharge_instructions_english": translated_instructions,
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
                "max_output_tokens": 3000,
                "reasoning": {"effort": settings.OPENAI_REASONING_EFFORT},
                "instructions": INSTRUCTIONS,
                "input": json.dumps(payload, default=str),
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "discharge_summary",
                        "strict": True,
                        "schema": DISCHARGE_SCHEMA,
                    }
                },
            },
        )
    response.raise_for_status()
    return json.loads(_output_text(response.json()))


def _bullets(story: list, values: list[str], styles) -> None:
    if not values:
        story.append(Paragraph("Not documented.", styles["BodyText"]))
        return
    for value in values:
        story.append(Paragraph(f"• {escape(value)}", styles["BodyText"]))


def render_pdf(
    *,
    hospital_name: str,
    patient_name: str,
    patient_reference: str,
    generated_at: str,
    summary: dict,
) -> bytes:
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Discharge Summary - {patient_name}",
    )
    styles = getSampleStyleSheet()
    styles["Title"].textColor = colors.HexColor("#08757a")
    styles["Heading2"].textColor = colors.HexColor("#08757a")
    styles["BodyText"].leading = 15
    story = [
        Paragraph("DISCHARGE SUMMARY", styles["Title"]),
        Paragraph(escape(hospital_name), styles["Heading2"]),
        Spacer(1, 5 * mm),
        Table(
            [
                ["Patient", patient_name],
                ["Patient ID", patient_reference],
                ["Generated", generated_at],
                ["Status", "Draft — clinician review required"],
            ],
            colWidths=[35 * mm, 125 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#e7f4f2")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b9cfcc")),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("PADDING", (0, 0), (-1, -1), 6),
                ]
            ),
        ),
        Spacer(1, 5 * mm),
    ]
    sections = [
        ("Reason for admission", "admission_reason"),
        ("Final diagnoses", "final_diagnoses"),
        ("Hospital course", "hospital_course"),
        ("Significant investigations / findings", "significant_findings"),
        ("Procedures", "procedures"),
        ("Condition at discharge", "condition_at_discharge"),
        ("Follow-up", "follow_up"),
        ("Diet and activity", "diet_and_activity"),
        ("Discharge instructions", "discharge_instructions"),
        ("Warning signs — seek care", "warning_signs"),
    ]
    for heading, key in sections:
        story.append(Paragraph(heading, styles["Heading2"]))
        _bullets(story, summary.get(key, []), styles)
        story.append(Spacer(1, 3 * mm))

    story.append(Paragraph("Medications on discharge", styles["Heading2"]))
    medications = summary.get("medications", [])
    if medications:
        rows = [["Medication", "Dose", "Frequency", "Duration", "Instructions"]]
        rows.extend(
            [
                medication["name"],
                medication["dosage"],
                medication["frequency"],
                medication["duration"],
                medication["instructions"],
            ]
            for medication in medications
        )
        story.append(
            Table(
                rows,
                repeatRows=1,
                colWidths=[36 * mm, 24 * mm, 30 * mm, 25 * mm, 48 * mm],
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#08757a")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#b9cfcc")),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("PADDING", (0, 0), (-1, -1), 5),
                    ]
                ),
            )
        )
    else:
        story.append(Paragraph("No discharge medications documented.", styles["BodyText"]))
    story.extend(
        [
            Spacer(1, 8 * mm),
            Paragraph(
                "This AI-assisted document must be reviewed, corrected where necessary, and signed by the treating clinician before use.",
                styles["Italic"],
            ),
        ]
    )
    document.build(story)
    return output.getvalue()
