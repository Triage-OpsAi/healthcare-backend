"""
Medical coding service.

THE CORE RULE: a code is only ever attached to a record by looking it up
in the `medical_codes` / `drug_master` reference tables. Nothing in this
file asks an LLM "what is the SNOMED code for X" -- that's how you get
hallucinated codes in a legal medical record. Instead:

  1. An LLM extracts candidate clinical terms from the structured note
     (see emr_service.py) -- plain text, e.g. "type 2 diabetes", "amoxicillin".
  2. This service embeds that term and does nearest-neighbour search
     against the pre-loaded SNOMED CT / ICD-10 / drug_master tables.
  3. Matches above CONFIDENCE_AUTO_ACCEPT are attached automatically;
     anything below is surfaced as 2-3 candidates for the doctor to pick
     from during review, and nothing is silently guessed.

In production, swap `_cosine_similarity_search` for a native pgvector
`ORDER BY embedding <=> :query_vector LIMIT k` query -- it's indexable
(ivfflat/hnsw) and won't require pulling every row into Python. This
in-process version is here so the matching logic is easy to read and
unit test independent of a live Postgres+pgvector instance.
"""
import math

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import DrugMasterEntry, MedicalCode

CONFIDENCE_AUTO_ACCEPT = 0.90
CONFIDENCE_SUGGEST_MIN = 0.60


async def embed_text(text: str) -> list[float]:
    """Create an OpenAI embedding for clinical terminology matching."""
    normalized_text = text.strip()
    if not normalized_text:
        return []
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set -- add your OpenAI API key to .env")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{settings.OPENAI_BASE_URL.rstrip('/')}/embeddings",
            headers={
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "content-type": "application/json",
            },
            json={
                "model": settings.OPENAI_EMBEDDING_MODEL,
                "input": normalized_text,
                "encoding_format": "float",
            },
        )
    response.raise_for_status()
    payload = response.json()
    try:
        return payload["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("OpenAI embedding response contained no vector") from exc


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def match_clinical_term(
    db: AsyncSession, *, term: str, system: str | None = None, top_k: int = 3
) -> list[dict]:
    """Match a free-text clinical term (e.g. 'sugar problem') against
    medical_codes and return the top_k candidates with confidence scores."""
    stmt = select(MedicalCode)
    if system is not None:
        stmt = stmt.where(MedicalCode.system == system)
    result = await db.execute(stmt)
    candidates = result.scalars().all()
    if not candidates:
        return []

    query_embedding = await embed_text(term)
    if not query_embedding:
        return []

    scored = [
        {
            "system": c.system,
            "code": c.code,
            "display_term": c.display_term,
            "confidence_score": _cosine_similarity(query_embedding, c.embedding or []),
            "medical_code_id": c.id,
        }
        for c in candidates
        if c.embedding
    ]
    scored.sort(key=lambda x: x["confidence_score"], reverse=True)
    return scored[:top_k]


async def match_drug(db: AsyncSession, *, hospital_id: str, drug_text: str, top_k: int = 3) -> list[dict]:
    """Match an extracted drug mention against the hospital's own formulary,
    not a generic global drug ontology -- brand names and stock vary by hospital."""
    stmt = select(DrugMasterEntry).where(DrugMasterEntry.hospital_id == hospital_id)
    result = await db.execute(stmt)
    candidates = result.scalars().all()
    if not candidates:
        return []

    query_embedding = await embed_text(drug_text)
    if not query_embedding:
        return []

    scored = [
        {
            "brand_name": d.brand_name,
            "generic_name": d.generic_name,
            "confidence_score": _cosine_similarity(query_embedding, d.embedding or []),
            "drug_master_id": d.id,
        }
        for d in candidates
        if d.embedding
    ]
    scored.sort(key=lambda x: x["confidence_score"], reverse=True)
    return scored[:top_k]
