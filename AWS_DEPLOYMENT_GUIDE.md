# Meridian EMR Backend: AWS Deployment Handoff

This document is the deployment source of truth for this repository. It is
written so that another coding agent (including Claude) can understand the
application, make the missing deployment changes, provision AWS infrastructure,
deploy the services, and verify the result without guessing at the architecture.

## 1. Deployment decision

Deploy the backend as containers managed by **Amazon ECS**, using an **EC2 Auto
Scaling Group capacity provider**.

Do not install and operate the application directly on an un-orchestrated EC2
server for production. EC2 supplies the compute capacity, while ECS supplies
task placement, process restart, rolling deployment, service discovery,
health-check integration, and independent scaling of the API and workers.

Do not introduce Kafka for the current background-job pipeline. The existing
messages are task commands that should be claimed by one worker, retried on
failure, and completed once:

- `voice.transcribe`
- `voice.build_patient_emr`
- `report.summarize`
- `discharge.transcribe`
- `discharge.generate`

Keep Celery with a managed Redis-compatible broker initially. Kafka may be
introduced later for durable domain events such as `emr.approved` or
`discharge.generated`, when multiple independent consumers, replay, ordered
event history, or streaming analytics are actual requirements.

### Target production stack

| Concern | AWS service |
|---|---|
| DNS | Route 53 |
| TLS certificate | AWS Certificate Manager |
| Edge protection | AWS WAF, attached to the ALB |
| HTTP entry point | Application Load Balancer |
| Container orchestration | Amazon ECS |
| Compute | EC2 Auto Scaling Group through an ECS capacity provider |
| Image registry | Amazon ECR |
| Database | Amazon RDS for PostgreSQL |
| Celery broker/result backend | Amazon ElastiCache for Valkey/Redis OSS |
| Clinical object storage | Amazon S3, after the storage adapter change described below |
| Application secrets | AWS Secrets Manager |
| Logs, metrics, alarms | Amazon CloudWatch |
| Audit of AWS actions | AWS CloudTrail |
| Email | Amazon SES or the configured external SMTP provider |

Use `ap-south-1` (Mumbai) by default. A different region must be a deliberate
business, latency, service-availability, and data-governance decision.

## 2. What this backend does

This is a multi-tenant FastAPI backend for hospital administration and clinical
EMR workflows. Tenant identity is represented by `hospital_id` on clinical
records. The major capabilities are:

- Internal administration of clients, users, locations, services, and API keys.
- Clinical user authentication, refresh-token rotation, and permissions.
- Doctor workspace, patient dashboard, encounters, medications, and reports.
- Direct browser upload of private clinical files and voice recordings.
- Multilingual transcription and English translation using Sarvam AI.
- Structured EMR, report, patient-intake, and discharge reasoning using OpenAI.
- Human review before EMR approval.
- Medical-code suggestions based on database lookup.
- Deterministic discharge-summary PDF generation.
- Append-only application audit records.

The FastAPI application is created in `app/main.py`. All application routes are
mounted under `/api/v1`. API documentation is available at `/docs`,
`/redoc`, and `/openapi.json`. The load-balancer health endpoint is currently
`GET /healthz`.

## 3. Runtime processes

The same container image should be used for all three process types. Override
the container command in each ECS task definition.

### 3.1 API service

Purpose:

- Authentication and authorization.
- Administration and doctor APIs.
- Job creation and status endpoints.
- Creation of presigned upload/download URLs.
- Verification of completed direct uploads.
- Enqueueing Celery tasks.

Production command:

```text
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

Begin with two API tasks distributed across two Availability Zones. Do not use
Uvicorn `--reload` in a container. Prefer one Uvicorn process per ECS task and
scale ECS tasks horizontally; revisit workers-per-container only after load
testing and database-pool analysis.

### 3.2 Voice-transcription worker service

Purpose:

- Consume `voice_transcription`.
- Download source audio.
- Call Sarvam transcription/translation.
- Store transcripts and enqueue the next reasoning step.

Linux production command:

```text
celery -A app.celery_app.celery_app worker \
  -Q voice_transcription \
  --loglevel=INFO \
  --hostname=transcription@%h \
  --concurrency=1
```

The repository's PowerShell scripts use `--pool=solo` because local development
runs on Windows. Do not carry that local-only choice into Linux production
without a reason. Start with concurrency 1 because one job may hold a large
audio payload in memory and wait on external services. Increase only after
measuring memory, provider rate limits, and job latency.

### 3.3 Patient/EMR reasoning worker service

Purpose:

- Consume `patient_emr`.
- Extract patient identity and clinical context.
- Create patients and encounters.
- Structure EMRs and suggest clinical codes.
- Analyze uploaded reports.
- Generate discharge summaries and PDFs.

Linux production command:

```text
celery -A app.celery_app.celery_app worker \
  -Q patient_emr \
  --loglevel=INFO \
  --hostname=patient-emr@%h \
  --concurrency=1
```

The two worker queues must remain separate ECS services. They have different
memory, external API, latency, and scaling characteristics.

## 4. Background workflow

### Voice intake

```text
Browser
  -> requests a presigned upload URL from FastAPI
  -> uploads audio directly to private object storage
  -> tells FastAPI the upload is complete
FastAPI
  -> verifies object metadata
  -> publishes voice.transcribe to voice_transcription
Transcription worker
  -> downloads audio
  -> calls Sarvam
  -> saves original and translated transcripts
  -> publishes voice.build_patient_emr to patient_emr
Reasoning worker
  -> calls OpenAI
  -> creates/reuses patient and encounter
  -> creates a pending-review EMR
  -> records code suggestions
Doctor
  -> reviews and approves through the API
```

### Clinical report

```text
Browser direct upload
  -> API verification
  -> report.summarize
  -> quality check and OpenAI summary
  -> ready, needs_reupload, or failed
  -> clinician approval
```

### Discharge summary

```text
Browser direct audio upload
  -> API verification
  -> discharge.transcribe
  -> discharge.generate
  -> chart aggregation and OpenAI reasoning
  -> deterministic PDF generation
  -> private object-storage upload
  -> short-lived signed download
```

Job state is stored durably in PostgreSQL. Celery tasks use late
acknowledgement, exponential retry, and status checks intended to make retries
safe. The broker visibility timeout is currently 3,600 seconds.

## 5. Current repository deployment gaps

Claude must not assume any of the following already exists:

- No Dockerfile is present.
- No `.dockerignore` is present.
- No Docker Compose production definition is present.
- No Terraform, CDK, CloudFormation, or other AWS IaC is present.
- No ECR or ECS configuration is present.
- No CI/CD workflow is present.
- Alembic is installed but there is no Alembic configuration or migration tree.
- Schema creation currently uses `Base.metadata.create_all()`.
- `/healthz` confirms only that the API process is running; it is not a
  database/Redis readiness probe.
- The object-storage implementation uses AWS S3 with presigned URLs.
- There is no CloudWatch queue-depth publisher for scaling Redis-backed workers.
- There are no production dashboards, alerts, runbooks, or documented rollback
  automation.

These are deployment tasks, not optional assumptions.

## 6. Required code changes before production deployment

### 6.1 Containerization

Create:

- `Dockerfile`
- `.dockerignore`
- A local `compose.yaml` for deployment-parity testing, if useful.

Dockerfile requirements:

- Use a pinned, supported Python slim base image.
- Install build dependencies only in a builder stage when possible.
- Install exactly `requirements.txt`.
- Copy application source without `.env`, `.git`, `.venv`, caches, or test data.
- Run as a non-root user.
- Set `PYTHONUNBUFFERED=1` and `PYTHONDONTWRITEBYTECODE=1`.
- Expose port 8000 for documentation only; ECS controls networking.
- Provide a safe default API command, overridden for worker task definitions.
- Include a container health check only if it does not conflict with ALB checks.
- Build and test the initial image on `linux/amd64`. Move to Graviton/ARM64 only
  after every dependency and external SDK has been validated on ARM.

Do not bake secrets or environment-specific configuration into the image.

### 6.2 Database migrations

Set up Alembic and generate a reviewed baseline migration from the current
models. Do not run `create_all()` automatically during API startup.

Deployment migration pattern:

1. Register the new ECS task definition.
2. Run a one-off ECS migration task in the private application subnets.
3. Command: `alembic upgrade head`.
4. Require a successful exit code.
5. Deploy the ECS services only after migration success.

Until Alembic is implemented, a first empty database can be bootstrapped with:

```text
python -m scripts.database_setup --create
```

That command is an interim bootstrap mechanism, not an acceptable long-term
production migration strategy.

Use backward-compatible expand/migrate/contract changes so the old and new
application revisions can coexist during rolling deployments.

### 6.3 Object storage

The current service uses boto3 with AWS S3 presigned URLs and requires
`AWS_REGION` and `AWS_S3_BUCKET`. Local development can supply
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`.

For production, the S3 implementation should:

- Use the ECS task IAM role and the default boto3 credential provider chain.
- Use `AWS_REGION` and `AWS_S3_BUCKET`.
- Avoid static AWS access keys.
- Generate short-lived presigned PUT and GET URLs.
- Verify content length and, where feasible, content type/checksum.
- Preserve the existing object-key layout and private-access behavior.
- Set server-side encryption using an S3 bucket policy and a KMS key.
- Configure only the exact frontend origins in bucket CORS.
- Block all public access.
- Enable versioning and an appropriate lifecycle/retention policy.
- Log or audit sensitive object access without logging signed URLs.

Do not switch production environment variables to S3 until this refactor and
its direct-upload flow have been tested end-to-end.

### 6.4 Health and readiness

Keep `/healthz` as a lightweight liveness endpoint for the ALB.

Add a separate internal `/readyz` endpoint that:

- Performs a bounded `SELECT 1` against PostgreSQL.
- Performs a bounded Redis `PING`.
- Returns 200 only when dependencies needed to accept work are ready.
- Does not reveal credentials, hostnames, stack traces, or patient data.

Do not make the ALB liveness check depend on external OpenAI or Sarvam
availability. Those providers should have separate metrics and alarms.

### 6.5 Redis TLS

ElastiCache should require in-transit encryption and authentication. Ensure the
application accepts a `rediss://` URL and verify Celery and redis-py TLS behavior
in a staging environment. Do not disable certificate verification.

If Celery results are never retrieved from the Redis result backend, evaluate
disabling result storage with `task_ignore_result=True`. Do not make this change
until tests confirm no caller uses `AsyncResult` or stored task results.

### 6.6 Application logging

Use structured JSON logs to stdout/stderr with:

- timestamp
- environment
- service/process name
- request or correlation ID
- job ID when applicable
- route and status code
- safe exception category

Never log:

- JWTs or refresh tokens
- passwords
- API keys
- presigned URLs
- full dictation or transcript text
- patient names, phone numbers, ABHA identifiers, or report contents

CloudWatch log groups must have explicit retention rather than unlimited
retention.

## 7. AWS network design

Create one VPC across at least two Availability Zones.

### Public subnets

Place only:

- Application Load Balancer
- NAT Gateways

### Private application subnets

Place:

- ECS EC2 container instances
- API tasks
- Worker tasks
- One-off migration tasks

The workers need outbound HTTPS access to Sarvam, OpenAI, and object storage.
Use one NAT Gateway per Availability Zone for production resilience. A
single-NAT staging environment is acceptable as an explicit cost tradeoff.

### Isolated data subnets

Place:

- RDS PostgreSQL
- ElastiCache

Do not assign public IP addresses to ECS instances, tasks, RDS, or ElastiCache.

### Security groups

Create separate security groups:

1. `alb-sg`
   - Inbound 443 from the internet.
   - Optional inbound 80 only to redirect to 443.
   - Outbound to `api-sg` on port 8000.
2. `api-sg`
   - Inbound port 8000 only from `alb-sg`.
   - Outbound to PostgreSQL, Redis, HTTPS, DNS, and required AWS endpoints.
3. `worker-sg`
   - No inbound rules.
   - Outbound to PostgreSQL, Redis, HTTPS, DNS, and required AWS endpoints.
4. `db-sg`
   - Inbound 5432 only from `api-sg`, `worker-sg`, and the migration-task SG.
5. `redis-sg`
   - Inbound Redis TLS port only from `api-sg` and `worker-sg`.

Use VPC endpoints for S3, ECR, CloudWatch Logs, Secrets Manager, and Systems
Manager where the operational/cost tradeoff is justified.

## 8. ECS and EC2 capacity

### Initial EC2 capacity

Start production with:

- Auto Scaling Group minimum: 2
- Desired: 2
- Maximum: 6
- Two or three Availability Zones
- On-Demand baseline capacity
- ECS-optimized Amazon Linux AMI
- Encrypted gp3 root volumes
- IMDSv2 required
- No inbound SSH
- Systems Manager Session Manager for controlled access

A reasonable initial x86 instance class is 2 vCPU and 8 GiB RAM per instance,
subject to load testing. Do not treat this as final sizing.

Enable:

- ECS managed scaling.
- Managed termination protection.
- Managed instance draining.
- Availability-Zone spread.
- Capacity-provider target capacity chosen to leave enough room for safe rolling
  deployment.

Use Spot only for retry-safe worker capacity after interruption testing. Keep a
stable On-Demand baseline and do not place the only API copy or only worker copy
on Spot.

### Initial ECS task sizing

Starting points, to be validated:

| Service | Desired count | CPU | Memory |
|---|---:|---:|---:|
| API | 2 | 512 CPU units | 1 GiB |
| Voice worker | 1 | 1024 CPU units | 2 GiB |
| Reasoning worker | 1 | 1024 CPU units | 2-4 GiB |

Set both reservations and hard limits deliberately. PDF/image/report processing
and in-memory audio downloads make worker memory the first metric to watch.

### ECS service behavior

- API: ALB target group, rolling deployment, circuit breaker with rollback.
- Workers: no load balancer, rolling deployment, long enough stop timeout for
  graceful Celery shutdown.
- Enable ECS Exec only for tightly controlled break-glass access with CloudTrail
  and session logging.
- Do not expose worker ports.
- Use separate task definitions or at least separate task-definition families
  for API, transcription, and reasoning.

## 9. RDS PostgreSQL

Production configuration:

- PostgreSQL in isolated subnets.
- Multi-AZ.
- Storage encryption with a customer-managed KMS key where required.
- Automated backups and point-in-time recovery.
- Deletion protection.
- Performance Insights/Database Insights as appropriate.
- TLS required.
- Credentials stored in Secrets Manager.
- A dedicated least-privilege application database user.
- A separate migration owner/user if operationally feasible.

The SQLAlchemy engine currently permits up to 30 connections per process
(`pool_size=10`, `max_overflow=20`). Multiplying that by every API and worker
process can exhaust a small RDS instance. Before load testing:

- Set pool size and overflow through environment configuration.
- Calculate a total connection budget across maximum ECS task count.
- Consider RDS Proxy only after validating asyncpg compatibility and actual
  connection pressure.

The current medical-code embeddings are JSON arrays, not native pgvector
columns. A future pgvector migration and ANN index is a separate application
optimization, not an infrastructure prerequisite for the first deployment.

## 10. ElastiCache

Use a managed Valkey/Redis-compatible deployment:

- Private data subnets.
- Multi-AZ/failover for production.
- In-transit encryption.
- At-rest encryption.
- Authentication/RBAC.
- No public endpoint.
- Backups where supported and justified.

The Celery broker URL should be injected from Secrets Manager, for example as a
`rediss://` URL. Never log it.

Monitor:

- connection count
- engine CPU
- memory utilization
- evictions
- replication health
- command latency
- queue depth and oldest-job age

Redis is a transport, not the source of truth for clinical workflow state.
PostgreSQL remains the durable job-state record.

## 11. Secrets and runtime configuration

Inject secrets through ECS task-definition `secrets`, backed by Secrets Manager.
Inject non-secret values through ordinary ECS environment variables.

### Secrets

- `DATABASE_URL`
- `REDIS_URL`
- `JWT_SECRET_KEY`
- `SARVAM_API_KEY`
- `OPENAI_API_KEY`
- SMTP password or SES SMTP credentials, if used
- `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` only when an IAM role is unavailable

### Non-secret configuration

- `JWT_ALGORITHM`
- `ACCESS_TOKEN_EXPIRE_MINUTES`
- `REFRESH_TOKEN_EXPIRE_DAYS`
- `SARVAM_BASE_URL`
- `SARVAM_STT_MODEL`
- `OPENAI_MODEL`
- `OPENAI_BASE_URL`
- `OPENAI_EMBEDDING_MODEL`
- `OPENAI_REASONING_EFFORT`
- frontend URLs
- exact CORS origin list
- invitation lifetime
- object-storage bucket and region
- presigned URL expiration
- upload maximum
- `ENVIRONMENT=production`

Generate a strong JWT secret with an approved cryptographic random generator.
Plan rotation; changing it invalidates existing access tokens.

The frontend and doctor-frontend URLs must be real HTTPS origins before
production. Never use wildcard CORS with credentials.

## 12. IAM

Use separate IAM roles:

### ECS container-instance role

Only the permissions needed by the ECS agent and Systems Manager.

### ECS task-execution role

Permissions to:

- Pull images from ECR.
- Write to the assigned CloudWatch log groups.
- Read the specific Secrets Manager secrets referenced by the task definition.
- Decrypt the corresponding KMS keys.

### Application task role

Permissions to:

- Read/write only the required S3 bucket prefixes.
- Use the specific KMS key for those objects.
- Publish custom CloudWatch metrics if the application does so.
- Send email through SES, if SES is selected.

The API and worker roles may be split further so the API primarily signs and
verifies objects while workers read source objects and write generated output.
Do not use `s3:*`, `kms:*`, or administrator policies.

## 13. Infrastructure provisioning order

Implement the infrastructure with Terraform by default unless the repository
owner explicitly selects CDK or CloudFormation.

Suggested module/order:

1. `network`
   - VPC, subnets, route tables, NAT, endpoints, security groups.
2. `kms`
   - Keys and aliases for RDS, S3, secrets, and logs as required.
3. `storage`
   - Private S3 bucket, CORS, encryption, versioning, lifecycle, access logging.
4. `database`
   - RDS subnet group, parameter group, instance/cluster, backups.
5. `cache`
   - ElastiCache subnet group and replication/serverless configuration.
6. `registry`
   - ECR repository with immutable tags and image scanning.
7. `iam`
   - Instance, execution, task, deployment, and CI roles.
8. `ecs_cluster`
   - Launch template, Auto Scaling Group, capacity provider, ECS cluster.
9. `load_balancer`
   - ALB, HTTPS listener, target group, WAF association.
10. `ecs_services`
    - API and worker task definitions/services.
11. `observability`
    - Log groups, dashboards, alarms, notification topics.
12. `dns`
    - ACM validation and Route 53 alias.

Keep state in a dedicated encrypted S3 backend with locking supported by the
selected Terraform version/workflow. Separate production and staging state.
Never store generated secrets in committed Terraform files or unprotected
outputs.

## 14. Image build and release

Use immutable image identifiers. Never deploy `latest`.

Illustrative process:

```text
aws ecr get-login-password --region ap-south-1 |
  docker login --username AWS --password-stdin ACCOUNT_ID.dkr.ecr.ap-south-1.amazonaws.com

docker build --platform linux/amd64 -t meridian-emr:GIT_SHA .
docker tag meridian-emr:GIT_SHA \
  ACCOUNT_ID.dkr.ecr.ap-south-1.amazonaws.com/meridian-emr:GIT_SHA
docker push ACCOUNT_ID.dkr.ecr.ap-south-1.amazonaws.com/meridian-emr:GIT_SHA
```

In automation, prefer GitHub/OpenID Connect or the CI system's AWS OIDC
integration over long-lived AWS access keys.

Release sequence:

1. Run static checks and tests.
2. Build the image once.
3. Scan it.
4. Push the immutable SHA tag.
5. Register task-definition revisions using that exact image digest/tag.
6. Run the migration task.
7. Deploy workers if required by backward-compatible workflow changes.
8. Deploy the API.
9. Wait for ECS service stability and ALB health.
10. Run smoke tests.
11. Mark the release successful or roll back task-definition revisions.

## 15. CI/CD requirements

The pipeline should have:

### Pull request

- Python compile/import validation.
- Unit and integration tests.
- Lint/format checks once the project selects tools.
- Dependency and secret scanning.
- Docker build validation.
- Terraform format, validate, and plan for IaC changes.

### Main branch/release

- Build once and push immutable ECR image.
- Deploy automatically to staging.
- Run migration and smoke tests.
- Require approval for production.
- Run production migration.
- Update ECS services.
- Wait for deployment stability.
- Run authenticated and unauthenticated smoke checks.
- Publish deployment metadata including Git SHA and task-definition revisions.

Do not run destructive database rollback automatically. Application rollback and
database roll-forward are safer when migrations are designed compatibly.

## 16. Autoscaling

### API

Scale on a combination of:

- ALB request count per target
- CPU
- memory
- p95 latency

Set minimum two production tasks.

### Workers

Scale primarily on:

- queue depth per queue
- age of oldest job
- observed average processing duration
- external provider rate limits

Redis queue depth is not automatically an ECS metric. Publish a bounded custom
CloudWatch metric or run a small queue-metrics component. Configure independent
policies for `voice_transcription` and `patient_emr`.

Scale-in must be conservative so active long-running jobs are not repeatedly
terminated. Verify Celery graceful shutdown, ECS stop timeout, late
acknowledgement, and visibility-timeout behavior together.

## 17. Monitoring and alarms

Minimum dashboards:

- ALB request rate, latency, 4xx, 5xx, unhealthy targets.
- API ECS desired/running/pending tasks, CPU, memory, restarts.
- Each worker service CPU, memory, restarts, queue depth, oldest-job age.
- Job success/failure/retry counts by task type.
- RDS connections, CPU, free storage, latency, failover, replica health.
- ElastiCache memory, evictions, CPU, connections, replication.
- S3 request errors.
- OpenAI and Sarvam latency, rate-limit responses, and safe error counts.

Minimum alarms:

- No healthy API targets.
- Sustained API 5xx rate.
- ECS desired count differs from running count.
- Repeated task/container restarts.
- Queue depth or job age over the agreed service objective.
- High failed-job rate.
- RDS storage, CPU, connection saturation, or backup failure.
- Redis eviction, memory saturation, or failover.
- Certificate expiration/renewal failure.
- WAF anomaly/rate threshold.

Send operational alarms to an owned notification destination with an on-call
response process. Do not include PHI in alarm messages.

## 18. Security and clinical-data controls

This is a clinical system. Infrastructure configuration alone does not establish
legal or regulatory compliance.

Before production:

- Confirm the hospital's applicable DPDP, contractual, retention, and security
  requirements with qualified stakeholders.
- Confirm whether identifiable clinical content may be sent to OpenAI and
  Sarvam, including retention and geographic-processing terms.
- Confirm the object-storage provider and region are contractually acceptable.
- Encrypt traffic and storage.
- Use least privilege.
- Enable CloudTrail and preserve audit evidence.
- Establish vulnerability, dependency, and OS patching processes.
- Test restore, not only backup.
- Define breach-response and credential-rotation procedures.
- Avoid production PHI in development and staging.
- Use synthetic data for routine testing.
- Protect CloudWatch logs from accidental PHI ingestion.

Application logs and AWS audit logs solve different problems; retain both
according to an approved policy.

## 19. Backup, recovery, and availability

Define explicit RPO and RTO before final production sizing.

At minimum:

- RDS Multi-AZ for availability.
- RDS automated backups and point-in-time recovery.
- Periodic restore drills into an isolated environment.
- S3 versioning and retention/lifecycle policy.
- ElastiCache failover; do not depend on Redis as the only workflow state.
- Infrastructure reproducible from version-controlled IaC.
- Container releases reproducible from immutable ECR images.
- Secrets rotation runbook.
- Regional outage strategy documented, even if initial recovery is manual.

A two-AZ deployment protects against an Availability Zone failure, not an entire
regional failure.

## 20. Deployment verification

### Infrastructure checks

- ALB exposes only HTTPS.
- HTTP redirects to HTTPS or is disabled.
- API/worker instances and tasks have no public IP.
- RDS and Redis are unreachable from the internet.
- Security-group references match the design.
- Secrets do not appear in task-definition plaintext or logs.
- S3 public-access block is enabled.
- S3 direct upload works only from approved frontend origins.

### API checks

- `GET /healthz` returns 200 through the public domain.
- `/readyz` returns 200 internally.
- Swagger is either intentionally available or restricted for production.
- CORS succeeds from both approved frontend domains and fails from an
  unapproved origin.
- Authentication, refresh, logout, and tenant isolation work.
- Database-unavailable errors do not leak internals.

### Workflow checks

Run with synthetic clinical data:

1. Create/login a clinical user.
2. Create or select a patient/encounter.
3. Request an audio upload URL.
4. Upload directly to object storage.
5. Complete the upload.
6. Observe `voice.transcribe`.
7. Observe handoff to `voice.build_patient_emr`.
8. Confirm the EMR becomes `pending_review`.
9. Review and approve it.
10. Upload a sample report and confirm summary/review behavior.
11. Run discharge transcription, summary, PDF creation, and signed download.
12. Force a retryable provider failure and verify retry/job state.
13. Restart a worker during a task and verify safe redelivery.
14. Confirm no clinical content or signed URL appears in logs.

Run the existing repository smoke scripts where compatible, but do not use
hard-coded demo credentials or production patient data.

## 21. Rollback

For an application-only failure:

1. Stop further rollout.
2. Revert each affected ECS service to the previously known-good task-definition
   revision/image.
3. Wait for service stability.
4. Run health and workflow smoke checks.

For a migration-related failure:

- Do not blindly run a down migration.
- Assess whether the previous application remains compatible.
- Prefer a corrective forward migration.
- Restore from backup only under the approved incident/recovery process because
  restore can discard valid clinical writes after the recovery point.

Keep the last known-good image and task-definition revision immediately
available.

## 22. When to add Kafka

Do not use Kafka as a replacement for the current Celery queues during the first
AWS deployment.

Reassess Amazon MSK when at least one of these becomes real:

- The same domain event has multiple independent consumers.
- Consumers require replay over a retained event history.
- Per-patient or per-hospital event ordering is required.
- Streaming analytics is required.
- Event throughput is high enough to justify the platform.
- The team can operate schemas, compatibility, partitions, lag, retries, and
  dead-letter/recovery procedures.

At that point, use an outbox pattern:

```text
Application database transaction
  -> clinical state change + outbox row
Outbox publisher
  -> Kafka/MSK domain event
Independent consumers
  -> ABDM, billing, notifications, analytics, audit archive
```

Do not dual-write PostgreSQL and Kafka without an outbox or equivalent
consistency design. Keep Celery task commands separate from domain events.

## 23. Cost controls

- Tag all resources with application, environment, owner, and cost center.
- Configure AWS Budgets and anomaly detection.
- Begin with measured, modest task and instance sizes.
- Use Compute Savings Plans only after observing steady baseline usage.
- Keep On-Demand capacity for critical services.
- Use Spot for tested retry-safe surplus worker capacity.
- Set CloudWatch log retention.
- Use S3 lifecycle policies consistent with approved clinical retention.
- Review NAT Gateway traffic; VPC endpoints may reduce or redistribute cost.
- Shut down non-production capacity on a schedule only when doing so will not
  destroy required state or block testing.

Do not choose Kafka/MSK, EKS, or additional databases merely in anticipation of
future scale.

## 24. Claude implementation brief

When asked to implement this deployment, Claude should work in this order:

1. Inspect the repository and preserve unrelated user changes.
2. Add containerization and prove all three commands run from one image.
3. Add application tests for liveness, readiness, broker connection, and direct
   object upload.
4. Introduce environment-driven database pool sizing.
5. Add Alembic and a reviewed baseline migration.
6. Replace static S3 credentials with an IAM task role in production.
7. Add structured safe logging and correlation IDs.
8. Add Terraform for staging using the architecture above.
9. Add ECS task definitions for API, transcription worker, reasoning worker,
   and one-off migrations.
10. Add CI validation and staging deployment.
11. Deploy staging and execute the complete synthetic workflow.
12. Load test API and worker memory/concurrency.
13. Tune instance/task/database/cache sizing.
14. Add production IaC, alarms, backup settings, WAF, DNS, and release approval.
15. Produce a deployment record and operational runbook.

Claude must pause for repository-owner decisions when values cannot safely be
inferred, especially:

- AWS account and organizational boundary.
- Domain and hosted zone.
- Production and staging frontend origins.
- Approved AWS region.
- RPO/RTO and retention requirements.
- External AI clinical-data approval.
- Email provider.
- Whether staging may use reduced availability.
- Terraform backend/account arrangement.
- On-call notification destination.

Claude should not request values that can be discovered safely from the AWS
account or repository, and should never ask for secrets to be pasted into source
files or chat. Secrets must be created or entered directly into the approved
secret-management workflow.

## 25. Production definition of done

Deployment is complete only when:

- Infrastructure is reproducible from reviewed IaC.
- All images are immutable and traceable to source.
- API and both workers run as independently deployable ECS services.
- Production has multi-AZ compute, database, and broker design.
- Database changes run through reviewed migrations.
- Clinical objects are private and encrypted.
- No long-lived AWS credentials exist in containers or CI.
- TLS, CORS, IAM, WAF, backups, logs, dashboards, and alarms are verified.
- Full synthetic voice, report, and discharge workflows pass.
- Worker termination/retry behavior has been tested.
- Restore and rollback procedures have been exercised.
- External AI and clinical-data handling have received the necessary approval.
- An identified team owns monitoring and incident response.
