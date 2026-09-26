# Departments, consent, and signed patient records

Clinical UI: `emr-doctor-portal`. APIs and persistence: `healthcare-backend`.
There are no clinical UI changes in the administration Next.js project.

## Deployment

Deploy the backend before the portal. Run from the backend environment with
database connectivity:

```sh
python -m scripts.database_setup --create
python -m scripts.migrate_clinical_consent
```

The first command creates the six additive tables: `departments`,
`user_departments`, `invitation_departments`, `patient_consents`, and
`clinical_attestations`, and `clinical_feature_rollouts`. The EC2 deployment
workflow runs both commands before starting the API.

On its first run, the second command creates department names for every hospital and randomly
assigns every previously unassigned clinical user for testing, regardless of
role or active status. It preserves existing assignments and records test
assignments in the audit log. A rollout marker makes subsequent runs preserve
all assignments without assigning new users randomly. After the requested test setup,
review and correct assignments in Users → Departments. To add only department
names later, use `python -m scripts.seed_departments --all-hospitals`.
The seed utility also accepts `--hospital-id UUID` to target one hospital.

New invitations require a department. The chosen assignment is applied when
the invitation is accepted; existing legacy invitations remain redeemable.
Departments do not grant permissions. Workspace administrators manage
assignments for all roles, including their own department.

## Consent and signatures

Patient page → Department consent provides procedure-specific English and
Hindi content, reading and audio playback, patient/representative signatures,
staff signature, optional witness signature, refusal, and withdrawal history.
Representative signing requires a relationship, reason, and signed witness.
Clinical wording for purpose, benefits, material risks, alternatives, and
refusal consequences must be entered and reviewed by the care team in both
languages. Audio reads that wording; it does not translate or replace consent
discussion. The backend uses the existing `SARVAM_API_KEY` for Bulbul v3 audio.
If speech generation fails, the form remains readable and displays an error.

Review and sign opens the latest section content before signature capture.
The authenticated staff identity, timestamp, signature strokes, exact snapshot,
and SHA-256 version are saved. Changed content needs a new approval; signatures
on earlier versions remain in history. Existing approvals without a captured
signature require signing again. These are drawn electronic attestations,
not CCA-certified DSC/eSign signatures; certified signing needs a separate
approved provider integration.

View patient record includes all visits, clinical sections, source transcripts,
ward observations/entries, consent forms, and historical signed snapshots.
Print/save PDF paginates content and repeats the corresponding signatures at
the bottom of each signed page. Unsigned or changed pages are not stamped with
an unrelated signature. Uploaded binary reports are represented by their
recorded metadata and extracted clinical content; original attachments remain
available from the existing report controls.

## Research basis and clinical scope

- [MoHFW/DGHS patient rights](https://clinicalestablishments.mohfw.gov.in/sites/default/files/2023-03/2911.pdf): informed consent and treatment choices.
- [NMC medical ethics regulations](https://nmc.org.in/page/rules-regulations-rules-regulations-of-erstwhile-mci-code-of-medical-ethics-regulations-2002): written operative consent and recordkeeping.
- [CCA eSign](https://www.cca.gov.in/eSign.html): certified electronic signing is distinct from drawing a signature.
- [Sarvam TTS](https://docs.sarvam.ai/api-reference/text-to-speech/convert): English/Hindi speech generation, request limits, and audio format.

Use the workflow where hospital policy requires procedure-specific consent,
including relevant radiology/interventional procedures, cardiology interventions,
surgery, anaesthesia, transfusion, and other higher-risk treatments. Routine
documentation approval is separate from patient consent. Hospital-approved
wording and qualified explanation are needed; the software does not determine
capacity, representative authority, or emergency exceptions automatically.

## Validation

```sh
python -m unittest discover -s tests -p test_clinical_documents.py -v
# In emr-doctor-portal:
npm run lint
npm run build
```

Tests cover signature validation, bilingual consent, representative witnessing,
tenant boundaries, role-independent assignment, stale snapshots, visit scope,
required department selection, and approval permissions.
