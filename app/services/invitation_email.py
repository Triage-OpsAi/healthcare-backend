import asyncio
import logging
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr

from app.core.config import settings

logger = logging.getLogger(__name__)


def _send_smtp(
    recipient: str,
    full_name: str,
    magic_link: str,
    workspace_name: str,
) -> None:
    message = EmailMessage()
    message["Subject"] = "You are invited to Meridian Health AI"
    message["From"] = formataddr(
        (settings.SMTP_FROM_NAME, settings.SMTP_FROM_EMAIL)
    )
    message["To"] = recipient
    message.set_content(
        f"""Hello {full_name},

You have been invited to join {workspace_name} on Meridian Health AI.
Complete your account setup using this one-time link:

{magic_link}

This invitation expires in {settings.INVITATION_EXPIRE_HOURS} hours. If you
did not expect it, you can safely ignore this message.
"""
    )

    context = ssl.create_default_context()
    smtp_class = smtplib.SMTP_SSL if settings.SMTP_USE_SSL else smtplib.SMTP
    last_error: Exception | None = None
    attempts = max(1, settings.SMTP_RETRY_ATTEMPTS)
    for attempt in range(attempts):
        try:
            kwargs = {
                "host": settings.SMTP_HOST,
                "port": settings.SMTP_PORT,
                "timeout": settings.SMTP_TIMEOUT_SECONDS,
            }
            if settings.SMTP_USE_SSL:
                kwargs["context"] = context
            with smtp_class(**kwargs) as smtp:
                if settings.SMTP_USE_TLS and not settings.SMTP_USE_SSL:
                    smtp.starttls(context=context)
                if settings.SMTP_USERNAME:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(message)
            return
        except (OSError, smtplib.SMTPException) as exc:
            last_error = exc
            logger.warning(
                "SMTP invitation attempt %s/%s failed for %s: %s",
                attempt + 1,
                attempts,
                recipient,
                exc,
            )
            if attempt + 1 < attempts:
                time.sleep(settings.SMTP_RETRY_BASE_SECONDS * (2**attempt))
    if last_error is not None:
        raise last_error


async def deliver_invitation(
    *,
    recipient: str,
    full_name: str,
    raw_token: str,
    frontend_url: str | None = None,
    workspace_name: str = "the Meridian Health AI administration workspace",
) -> tuple[str, str | None]:
    base_url = frontend_url or settings.FRONTEND_URL
    magic_link = (
        f"{base_url.rstrip('/')}/accept-invitation"
        f"?token={raw_token}"
    )
    if not settings.SMTP_HOST:
        logger.warning(
            "Development invitation for %s: %s",
            recipient,
            magic_link,
        )
        return "development_outbox", magic_link

    await asyncio.to_thread(
        _send_smtp, recipient, full_name, magic_link, workspace_name
    )
    return "email", None
