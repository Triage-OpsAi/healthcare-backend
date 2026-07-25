# Asynchronous voice pipeline

Voice intake uses a private Backblaze B2 bucket and two independent Celery
queues. The browser uploads directly with a short-lived presigned PUT URL, so
audio bytes never pass through the FastAPI process.

Required environment variables:

```text
B2_KEY_ID=
B2_APPLICATION_KEY=
B2_BUCKET=emr-records
B2_S3_ENDPOINT=
REDIS_URL=redis://localhost:6379/0
```

`B2_S3_ENDPOINT` is optional and is discovered through Backblaze authorization
when empty. Configure bucket CORS once, then run the workers independently:

```powershell
.\.venv\Scripts\python.exe -m scripts.configure_b2_cors
.\.venv\Scripts\celery.exe -A app.celery_app.celery_app worker -Q voice_transcription --pool=solo --loglevel=INFO
.\.venv\Scripts\celery.exe -A app.celery_app.celery_app worker -Q patient_emr --pool=solo --loglevel=INFO
```

Use a production Redis service for deployments. `--pool=solo` is appropriate
for local Windows development; Linux deployments can use prefork workers and
scale each queue independently.

Worker 1 downloads the recording and produces original plus English text.
Worker 2 commits the patient first, then generates the reviewable EMR.
