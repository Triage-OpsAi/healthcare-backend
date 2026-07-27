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
REDIS_URL=rediss://default:replace-me@your-database.upstash.io:6379/0?ssl_cert_reqs=required
```

`B2_S3_ENDPOINT` is optional and is discovered through Backblaze authorization
when empty. Use the native Upstash Redis TLS URL, not the REST URL/token.
Configure bucket CORS once, then start the complete API and worker stack:

```powershell
.\.venv\Scripts\python.exe -m scripts.configure_b2_cors
.\scripts\start_all.ps1
```

The launcher checks Redis before startup and verifies the API, both queue
consumers, and all five registered tasks afterward. `--pool=solo` with
`--concurrency=1` is appropriate for local Windows development; Linux
deployments can use prefork workers and scale each queue independently.

Worker 1 downloads the recording and produces original plus English text.
Worker 2 commits the patient first, then generates the reviewable EMR.
