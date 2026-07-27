$ErrorActionPreference = "Stop"

$Root = (Resolve-Path "$PSScriptRoot\..").Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Runtime = Join-Path $Root ".runtime"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment is missing: $Python"
}

New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
Push-Location $Root
try {
    & $Python -m scripts.verify_async_stack --broker-only
    if ($LASTEXITCODE -ne 0) {
        throw "Redis is unavailable. API and workers were not started."
    }

    $ApiListener = Get-NetTCPConnection `
        -LocalAddress 127.0.0.1 `
        -LocalPort 8000 `
        -State Listen `
        -ErrorAction SilentlyContinue

    if (-not $ApiListener) {
        Start-Process `
            -FilePath $Python `
            -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
            -WorkingDirectory $Root `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Runtime "api.stdout.log") `
            -RedirectStandardError (Join-Path $Runtime "api.stderr.log")
    }

    # A clean first start has no workers to answer yet. Celery writes that
    # expected condition to stderr, which PowerShell otherwise treats as fatal.
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $PingOutput = (
            & $Python -m celery `
                -A app.celery_app.celery_app `
                inspect ping `
                --timeout=4 2>&1 |
            Out-String
        )
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    if ($PingOutput -notmatch "transcription@") {
        Start-Process `
            -FilePath $Python `
            -ArgumentList @(
                "-m", "celery",
                "-A", "app.celery_app.celery_app",
                "worker",
                "-Q", "voice_transcription",
                "--pool=solo",
                "--concurrency=1",
                "--loglevel=INFO",
                "--hostname=transcription@%h"
            ) `
            -WorkingDirectory $Root `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Runtime "transcription.stdout.log") `
            -RedirectStandardError (Join-Path $Runtime "transcription.stderr.log")
    }

    if ($PingOutput -notmatch "patient-emr@") {
        Start-Process `
            -FilePath $Python `
            -ArgumentList @(
                "-m", "celery",
                "-A", "app.celery_app.celery_app",
                "worker",
                "-Q", "patient_emr",
                "--pool=solo",
                "--concurrency=1",
                "--loglevel=INFO",
                "--hostname=patient-emr@%h"
            ) `
            -WorkingDirectory $Root `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $Runtime "patient-emr.stdout.log") `
            -RedirectStandardError (Join-Path $Runtime "patient-emr.stderr.log")
    }

    Start-Sleep -Seconds 8
    & $Python -m scripts.verify_async_stack
    if ($LASTEXITCODE -ne 0) {
        throw "One or more application processes failed readiness checks. See .runtime logs."
    }

    Write-Output "Meridian API and all asynchronous workers are ready."
}
finally {
    Pop-Location
}
