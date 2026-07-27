$ErrorActionPreference = "Stop"
& "$PSScriptRoot\..\.venv\Scripts\celery.exe" `
  -A app.celery_app.celery_app worker `
  -Q voice_transcription `
  --pool=solo `
  --concurrency=1 `
  --loglevel=INFO `
  --hostname="transcription@%h"
