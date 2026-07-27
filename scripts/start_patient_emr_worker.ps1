$ErrorActionPreference = "Stop"
& "$PSScriptRoot\..\.venv\Scripts\celery.exe" `
  -A app.celery_app.celery_app worker `
  -Q patient_emr `
  --pool=solo `
  --concurrency=1 `
  --loglevel=INFO `
  --hostname="patient-emr@%h"
