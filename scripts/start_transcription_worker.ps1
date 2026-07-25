$ErrorActionPreference = "Stop"
& "$PSScriptRoot\..\.venv\Scripts\celery.exe" `
  -A app.celery_app.celery_app worker `
  -Q voice_transcription `
  --pool=solo `
  --loglevel=INFO `
  --hostname="transcription@%h"
