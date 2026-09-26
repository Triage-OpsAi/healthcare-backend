"""Versioned hospital consent form. Printed wording is owned by the server."""
from datetime import date, datetime
import copy
import json
from pathlib import Path

TEMPLATE = json.loads(Path(__file__).with_name("demo_consent.json").read_text(encoding="utf-8"))

LEGACY_TEMPLATE = json.loads(Path(__file__).with_name("demo_consent_v1.json").read_text(encoding="utf-8"))


def consent_prefill(patient, clinician_name, today=None):
    today = today or date.today()
    birth = getattr(patient, "date_of_birth", None)
    if isinstance(birth, datetime):
        birth = birth.date()
    age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day)) if birth and birth <= today else None
    values = {"patient_name": patient.full_name, "representative_name": patient.full_name,
              "gender": getattr(patient, "gender", None), "mobile": getattr(patient, "phone", None),
              "age": str(age) if age is not None else None, "clinician_name": clinician_name}
    # The patient model has no address or clinician registration field. Do not invent them.
    return {key: {"text": str(value), "ink": None} for key, value in values.items() if value is not None and str(value).strip()}


def build_demo_form(payload, patient, hospital_name, clinician_name, signed_at):
    template = LEGACY_TEMPLATE if payload.template_version == LEGACY_TEMPLATE["version"] else TEMPLATE
    data = payload.model_dump(mode="json")
    def text(key, fallback):
        return data["fields"].get(key, {}).get("text") or fallback
    def bilingual(key):
        return {lang: text(key, "Handwritten entry" if lang == "en" else "हस्तलिखित विवरण") for lang in ("en", "hi")}
    return {
        "version": template["version"], "patient_name": patient.full_name,
        "language": payload.language,
        "signer_name": text("representative_name", "Handwritten name / हस्तलिखित नाम"),
        "signer_type": "patient_or_representative", "relationship": "",
        "witness_name": text("witness_name", "Handwritten name / हस्तलिखित नाम") if payload.witness_signature else "",
        "patient_signature": data["patient_signature"], "clinician_signature": data["clinician_signature"],
        "witness_signature": data["witness_signature"],
        # Compatibility for older record viewers; scope-specific choices remain authoritative.
        "decision": "accepted" if "accepted" in (payload.data_decision, payload.procedure_decision) else "declined",
        "procedure": bilingual("procedure"), "purpose": bilingual("purpose"),
        "benefits": {lang: template[lang]["procedure_items"][1] for lang in ("en", "hi")},
        "risks": {lang: template[lang]["procedure_intro"] for lang in ("en", "hi")},
        "alternatives": {lang: template[lang]["questions"] for lang in ("en", "hi")},
        "refusal": {lang: template[lang]["procedure_items"][-1] for lang in ("en", "hi")},
        "demo": {**data, "template": copy.deepcopy(template), "hospital_name": hospital_name,
                 "patient_reference": getattr(patient, "abha_id", None) or str(patient.id)[:8].upper(), "patient_name": patient.full_name,
                 "recorded_by": clinician_name, "date": signed_at.isoformat()},
    }
