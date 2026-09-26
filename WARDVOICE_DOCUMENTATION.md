# WARDVOICE specialty documentation

Open a patient chart in the doctor portal and select **WARDVOICE**. Select an existing encounter, then confirm the suggested specialty and document type. This module does not create patients or encounters.

## Workflow

1. Record (start, pause, resume, stop) or enter dictation. Transcription uses the existing Sarvam integration and retains original-language text alongside the English transcript. Recordings remain in browser memory for playback/retry; the document stores the transcripts, not the audio clip.
2. Generate structured fields using the configured extraction provider. Only exact transcript quotes are accepted from extraction. Clinician edits have separate provenance and survive regeneration.
3. Review/edit the document. Required missing fields, including explicitly unknown/undocumented values, appear under Documentation checks. Complete them or record a review reason. Radiology critical findings add communication checks.
4. A doctor marks the current revision reviewed, then draws a signature and confirms finalization. Editing invalidates review; stale revisions return HTTP 409. Finalization requires the same doctor's review and is immutable. A repeated identical finalization request is idempotent.
5. The finalized document appears in Clinical, Reports or Documents according to its template, plus Timeline and the complete printable patient report. Its signature is repeated on its report pages. External RIS/PACS transmission is not implemented or represented as delivered.

The catalogue contains 43 templates across General Medicine, Surgery, Pediatrics, OB/GYN (separate obstetric and gynecological paths), Radiology and Oncology. Suggestions use explicit workflow phrases, the encounter department and pediatric age context. They are suggestions for clinician confirmation, not clinical decisions. Oncology compares the current document with the latest earlier finalized oncology encounter.

## Database and deployment

Additive migration:

```powershell
.venv\Scripts\python.exe -m scripts.migrate_documentation
```

Tables: `wardvoice_documents` and `wardvoice_document_revisions`, linked by foreign keys to existing hospitals, patients, encounters and users. The first holds drafts/finalized documents; the second retains a snapshot for every saved revision. Existing audit logs record reads, transcription and lifecycle actions.

`scripts.database_setup --create`, already used by deployment, also registers these models. No destructive migration or new package is required. Restart a backend process that does not use automatic reload after deploying the code.

Existing environment settings are reused: `DATABASE_URL`, `SARVAM_API_KEY`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_REASONING_EFFORT`. No new secret is required.

## Permissions and integration

API prefix: `/api/v1/ward-voice/documentation`.

- `emr:read`: templates, patient context, saved documents and workflow suggestions.
- `emr:create`: create/edit drafts, transcribe and generate.
- `emr:review` plus doctor role: mark reviewed and sign/finalize.

Every patient/document lookup is hospital scoped. Encounter ownership is validated against the selected patient. Finalization captures the patient/encounter context, versioned template, field content, source transcript, review reasons, signer and content digest.

Frontend service: `src/lib/documentation.ts`; workspace: `src/components/DocumentationWorkspace.tsx`; catalogue: `app/services/documentation_templates.py`.

## Demonstrations and verification

Seven synthetic scenarios are available under **Try isolated demonstration**. They use a synthetic patient, explicit labelled extraction and local-only review/signing. They never save to the selected real patient's record. Returning to the patient starts a separate clinical draft.

Automated backend tests cover template coverage, provenance, missing-information checks, specialty suggestions, tenant restrictions, mismatched encounters, doctor-only review, stale/finalized edits, review invalidation, preserved clinician corrections and idempotent signing. Browser verification covers all seven demos through signature/locking and responsive width. Real provider smoke tests use synthetic text/audio. The database lifecycle smoke test rolls back all synthetic records.
