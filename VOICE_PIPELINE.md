# Asynchronous voice pipeline

Voice intake uses a private AWS S3 bucket and two independent Celery
queues. The browser uploads directly with a short-lived presigned PUT URL, so
audio bytes never pass through the FastAPI process.

Required environment variables:

```text
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=ap-south-1
AWS_S3_BUCKET=emr-records
REDIS_URL=rediss://default:replace-me@your-database.upstash.io:6379/0?ssl_cert_reqs=required
```

For local development with Upstash, use its native Redis TLS URL, not the REST
URL/token. Production Compose overrides `REDIS_URL` with its private persistent
Redis service so queue availability does not depend on an external request quota.
Configure bucket CORS once, then start the complete API and worker stack:

```powershell
.\.venv\Scripts\python.exe -m scripts.configure_s3_cors
.\scripts\start_all.ps1
```

The launcher checks Redis before startup and verifies the API, both queue
consumers, and all five registered tasks afterward. `--pool=solo` with
`--concurrency=1` is appropriate for local Windows development; Linux
deployments can use prefork workers and scale each queue independently.

Worker 1 downloads the recording and produces original plus English text.
Worker 2 commits the patient first, then generates the reviewable EMR.
