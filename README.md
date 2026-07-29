# Rainbow EMR Translation & Coding Agent — Backend MVP

For the production AWS architecture, implementation sequence, security controls,
and deployment handoff, see [AWS_DEPLOYMENT_GUIDE.md](AWS_DEPLOYMENT_GUIDE.md).

Converts a doctor's dictation in any Indian regional language into a structured,
medically-coded English EMR record, with a human review step before anything
is final.

## Running services

The system is intentionally split into API, infrastructure, workers, and
domain services. The API can remain responsive while clinical files and audio
are processed in the background.

### Processes and infrastructure

| Service | Purpose | Local command / endpoint |
|---|---|---|
| Doctor portal | Patient dashboard, chart, uploads, recording and downloads | `npm run dev` in `doctor-portal` (`http://localhost:3000`) |
| FastAPI clinical API | Authentication, tenant checks and job lifecycle endpoints | `.\scripts\start_api.ps1` (`http://127.0.0.1:8000`) |
| PostgreSQL | Durable patients, EMRs, reports, medications and job state | Configured by `DATABASE_URL` |
| Redis-compatible broker | Routes durable background tasks | Private persistent Redis in production Compose; `REDIS_URL` for local/external brokers |
| Voice transcription worker (worker 1) | Voice intake/discharge audio, Sarvam transcription and English translation | `.\scripts\start_transcription_worker.ps1` |
| Patient/EMR reasoning worker (worker 2) | Patient creation, report reasoning, discharge reasoning and PDF generation | `.\scripts\start_patient_emr_worker.ps1` |
| AWS S3 | Private source audio, clinical documents and generated discharge PDFs | Private bucket configured with `AWS_*` |
| Sarvam AI | Auto-detected Indian-language speech-to-text and English translation | External API configured by `SARVAM_API_KEY` |
| OpenAI Responses API | Structured patient intake, document summaries and discharge summaries | External API configured by `OPENAI_API_KEY` |

### Logical backend services

| Module | Responsibility |
|---|---|
| `s3_storage_service.py` | Low-level AWS S3 client and signed URLs |
| `clinical_file_storage_service.py` | The only clinical-file upload/read/generated-file storage boundary |
| `report_reasoning_service.py` | PDF, Word and image report quality checks and structured summaries |
| `discharge_pipeline_service.py` | Durable discharge upload state and signed listen/download access |
| `discharge_summary_service.py` | OpenAI discharge drafting and deterministic PDF rendering |
| `sarvam_service.py` | Multilingual transcription and translation |
| `patient_builder_service.py` | Patient identity extraction and patient/encounter creation |

### Queue routes

- `voice_transcription`: `voice.transcribe`, `discharge.transcribe`
- `patient_emr`: `voice.build_patient_emr`, `report.summarize`, `discharge.generate`

Start the API and every asynchronous task consumer with one command:

```powershell
.\scripts\start_all.ps1
```

The launcher verifies Redis before starting, avoids duplicate API/workers, and
then checks API health, worker responses, queue bindings, and all five registered
task types. Run the same verification independently with:

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_async_stack
```

Clinical report uploads accept PDF, `.doc`, `.docx`, JPEG, PNG and WEBP. Files
go directly from the browser to private AWS S3 storage; the API only issues
and verifies short-lived signed URLs.

## 1. Getting your Sarvam AI API key

1. Go to **https://dashboard.sarvam.ai** and sign up (this is separate from the docs site).
2. In the dashboard, open **API Keys** → **Create New Key**.
3. Copy the key and put it in `.env` as `SARVAM_API_KEY`.
4. Keep it out of git — `.env` is already the convention here, never commit it.

Auth on every Sarvam request is a header, not Bearer-style OAuth:
```
api-subscription-key: <your key>
```
That's already wired up in `app/services/sarvam_service.py`. Docs: https://docs.sarvam.ai

Model used: **`saaras:v3`**, called via `/speech-to-text` with `mode=transcribe`
(original language) and `mode=translate` (English) — one clip, two calls, so the
original-language transcript is never a back-translation of the English version.
Sarvam's REST endpoint handles clips **under 30 seconds**; for full-consult-length
recordings, switch to their **Batch API** (async, up to 2 hours) — flagged as a
near-term follow-up, not needed for MVP dictation-style usage.

## 2. What's in this backend

```
app/
  core/
    config.py        # all env-driven settings, one source of truth
    security.py       # password hashing + JWT create/verify
  db/
    database.py        # async SQLAlchemy engine/session
    models.py           # full multi-tenant schema (see below)
  schemas/               # Pydantic request/response models
  services/
    auth_service.py       # login, refresh-token rotation, logout
    authorization.py       # require_permission() dependency, tenant guard
    sarvam_service.py       # Sarvam ASR + translation calls
    coding_service.py        # SNOMED/ICD/drug lookup via embedding match
    emr_service.py            # orchestrates the full pipeline
    audit_service.py           # append-only audit log writer
    admin_service.py           # client visibility, onboarding, users, invitations
    invitation_email.py        # SMTP/development magic-link delivery
  api/
    deps.py                     # decode JWT -> CurrentUser
    routes_auth.py                # /auth/login /auth/refresh /auth/logout
    routes_emr.py                  # /emr/records/upload /records/{id} /review
    routes_admin.py                # /clients /users /locations
  main.py                           # FastAPI app, routers mounted at /api/v1
```

The administration surface is mounted at `/api/v1/clients`, `/users`, and
`/locations`; invitation setup is `/api/v1/auth/invitations/accept`.

## 3. Running it locally

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Copy .env.example to .env, then provide the required credentials.

# You'll need Postgres running locally (or docker run -p 5432:5432 postgres:16)
# Then create tables (simple MVP approach — swap for Alembic migrations once
# the schema stabilizes):
.\.venv\Scripts\python.exe -m scripts.database_setup --create

# Load nationwide state/district/city masters from an official India Post
# checkout or a local OGD CSV, then seed roles/permissions:
git clone --depth 1 https://github.com/IndiaPost/pin.git .india-post-pin
.\.venv\Scripts\python.exe -m scripts.import_india_locations --directory .india-post-pin\api\v01\csv
.\.venv\Scripts\python.exe -m scripts.seed_admin

# Create/reuse 30 stable encounters for doctor voice-upload testing:
.\.venv\Scripts\python.exe -m scripts.seed_voice_test_encounters

# Run API contract and administration smoke suites:
.\.venv\Scripts\python.exe -m scripts.api_smoke_test
.\.venv\Scripts\python.exe -m scripts.admin_smoke_test
.\.venv\Scripts\python.exe -m scripts.admin_write_smoke_test

# Start the API using the virtual environment:
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Swagger UI is available at **http://127.0.0.1:8000/docs**, ReDoc at
**http://127.0.0.1:8000/redoc**, and the raw OpenAPI document at
**http://127.0.0.1:8000/openapi.json**. Log in first, copy the returned
`access_token`, then use Swagger's **Authorize** button for protected EMR APIs.

### Doctor login and voice-upload test encounters

Use either `POST /api/v1/auth/login` or
`POST /api/v1/auth/clinical/login` with:

```json
{
  "email": "doctor@example.com",
  "password": "Doctor@123",
  "hospital_code": "RAINBOW-BLR"
}
```

Copy the returned `access_token`, click **Authorize** in Swagger, and enter the
token. Then call `POST /api/v1/emr/records/upload`, select an audio file, set the
language code, and use any encounter ID below:



3. `1123d234-da80-477f-9d79-92bcf8fc017d`
4. `f49a385a-7734-439f-90d0-e3b0db5b98b5`
5. `808c35be-68c0-4670-acf8-8a358b007609`
6. `d8b43d63-d69f-4626-9977-45fcf0fa1f51`
7. `10310980-734e-4e6a-afce-f4b2352a28e9`
8. `3c5d08d4-fed1-4c37-89cc-b757b29ee74e`
9. `62a73fec-4a7a-4688-b537-b5f69e2584ad`
10. `b19c9eb3-769d-4fae-b2e3-20c474115ee7`
11. `029a3e66-2943-4782-a5b1-d3ee4c32ecf4`
12. `033e6fdf-3bbd-441f-9a31-dc60bd747de1`
13. `10fbf234-f7a2-4490-87ca-08d04740f90a`
14. `f46063ff-6f19-4e05-985f-9dc05000d4a3`
15. `204640a8-1840-4ca6-9557-4425edc0e51e`
16. `000026b9-c193-4ca9-a0f4-103bcc521d81`
17. `c971b3d9-e0c9-4c69-a413-5714c823890d`
18. `e474c32a-7748-4d90-b5fa-d46141245452`
19. `ebefc8a2-02f2-44cb-9527-b77fbc666b9c`
20. `1d2ae555-9c58-4fb4-b6d9-27273b78f435`
21. `aacde4b9-05d4-400a-aab2-1aa04a453d94`
22. `9a32da33-2b39-47c7-8c59-896038b64389`
23. `fc6782d7-74b0-43cf-b2ac-62762b48c7c8`
24. `1c4c4a6f-2b65-4499-94e0-9ba9fca61716`
25. `fe056978-6a44-4d4b-b1f0-539943e4f547`
26. `390fb82d-72a2-44d2-8aa2-fbafa765f0d3`
27. `0284dfe2-49e6-4ab3-97aa-960745a5ff15`
28. `1277c229-beed-49f6-af6e-cd0358247cee`
29. `f97cf470-fc74-4277-b26d-dd6bf3e1d16a`
30. `dac9e3c0-d186-44b5-b92f-d1e2672c708f`

These IDs are generated idempotently by
`python -m scripts.seed_voice_test_encounters`; rerunning the command reuses
the same patient encounters rather than creating duplicates.

`hospitals` remains the tenant/client identity. `client_profiles` is its
one-to-one onboarding extension, and `user_client_access` is the indexed
visibility boundary for internal users. Invitation and refresh tokens are
stored only as hashes. Client-provided API keys are also one-way hashed, with
only a masked hint returned by APIs.

One data-loading task remains before clinical coding can return real suggestions:

- **Loading `medical_codes`** — you need to bulk-load the India SNOMED CT
  National Release (via NRCeS, free for India as a SNOMED member country)
  and WHO ICD-10 into that table, each row embedded via the function above.
  This is a one-time ETL script, not part of the request-time pipeline.

## 4. How the pieces map to what we discussed

**Authentication** (`auth_service.py` + `security.py`) — JWT access tokens
(15 min expiry) carry the user's `hospital_id`, `role`, and flattened
`permissions` list directly in the token claims. Refresh tokens are opaque,
stored server-side only as a SHA-256 hash (so a leaked DB doesn't leak usable
tokens), and rotate on every use — a stolen refresh token is a one-shot window,
not a standing key.

**Authorization** (`authorization.py`) — `require_permission("emr:create")`
is a FastAPI dependency applied per-route. Because permissions live in the
JWT, checking them is an in-memory operation on every request — no DB hit —
which is what lets this scale horizontally without the auth check becoming a
bottleneck. `assert_same_hospital()` is the cross-tenant guard: even a valid
token from Hospital A's doctor can't read Hospital B's record by guessing an ID.

**Database schema** (`models.py`) — every tenant-scoped table carries
`hospital_id` from day one (this was the "scalable from day 1" requirement —
retrofitting tenant isolation on a live medical-records system later is far
riskier than building it in now, even with one hospital). Reference tables
(`medical_codes`) are shared across tenants; `drug_master` is per-hospital
since formularies differ. `audit_logs` is append-only for DPDP Act compliance.

**Regional language → English + medical coding** (`sarvam_service.py`,
`emr_service.py`, `coding_service.py`) — this is the pipeline we walked
through: Sarvam does ASR + translation, an LLM structures the English text
into a SOAP note and extracts candidate clinical terms, and — critically —
**codes are only ever attached by looking up an extracted term against the
SNOMED CT / ICD-10 / drug_master tables, never by asking an LLM to output a
code from memory.** That's the guardrail against hallucinated codes in a
legal medical record. Below a confidence threshold, matches are surfaced as
suggestions rather than auto-applied.

**Human-in-the-loop** (`routes_emr.py: /review`) — every record sits in
`pending_review` until a doctor calls this endpoint, which is the only path
to `approved`. Nothing reaches the hospital EMR without it.

## 5. Known gaps to close before this is production-ready

- Swap the inline `await emr_service.process_uploaded_audio(...)` call in
  `routes_emr.py` for a Celery task dispatched to the Redis queue — it's
  called synchronously here purely so the reference implementation runs
  without standing up infrastructure first.
- `coding_service.py`'s cosine-similarity search pulls all rows into Python;
  swap `medical_codes.embedding` for a native `pgvector` column and use
  `ORDER BY embedding <=> :query LIMIT k` for an indexed ANN search once the
  table has more than a few thousand rows.
- Add Alembic migrations instead of `create_all()` once schema changes need
  to be tracked and rolled forward on a live database.
- Add rate limiting on `/emr/records/upload` (per-user and per-hospital) —
  not included here, but straightforward to add via `slowapi` or an
  API-gateway-level limiter.
- ABDM/FHIR write-back to the actual EMR isn't implemented yet — right now
  an approved record just sits at `status=approved`; the next step is a
  service that maps `structured_note` + confirmed codes into FHIR resources
  and pushes them via ABDM's APIs.
