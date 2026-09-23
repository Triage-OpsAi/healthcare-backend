# Invitation email setup

Both administration and clinical invitations are sent by the FastAPI backend.
For local development, settings are read from the sibling `EMR-NextApp/.env`
(or the older `healthai-platform-2/.env`), followed by this backend's `.env`.
Backend `.env` values override the sibling files; process environment variables
override all files. Restart the backend after configuration changes.

For EC2 deployment, set these in the backend repository's GitHub **production**
Environment, then run the **Deploy backend to EC2** workflow:

| Setting | Location | Value |
| --- | --- | --- |
| `SMTP_HOST` | Variable | `smtp.gmail.com` for Gmail |
| `SMTP_PORT` | Variable | `587` |
| `SMTP_USERNAME` | Secret | Sending Gmail account |
| `SMTP_PASSWORD` | Secret | Gmail app password, not the account password |
| `SMTP_FROM_EMAIL` | Variable | Sending account or authorized sender |
| `SMTP_USE_TLS` | Variable | `true` |
| `SMTP_USE_SSL` | Variable | `false` for port 587 |
| `FRONTEND_URL` | Variable | Public HTTPS administration app base URL |
| `DOCTOR_FRONTEND_URL` | Variable | Public HTTPS clinical app base URL |

The workflow now includes mail settings in the container environment and rejects
missing mail configuration and invalid invitation base URLs. A local frontend
`.env` is not uploaded to EC2. Keep credentials out of Git.

After deployment, invite a user from Users and verify the email arrives. Open its
`/accept-invitation?token=...` link, choose a password, and sign in. Tokens remain
single-use and expire after `INVITATION_EXPIRE_HOURS` (48 by default). Client
onboarding emails use `DOCTOR_FRONTEND_URL`; internal user emails use `FRONTEND_URL`.
If delivery fails, inspect the backend's SMTP error logs and confirm outbound
SMTP access and the sender credentials.
