"""Transactional SMTP and Console Email Delivery Engine for AgencyOS."""

import logging
import smtplib
from email.message import EmailMessage

from flask import current_app

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when email delivery cannot be completed safely."""


def _build_message(to: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["To"] = to
    message["From"] = current_app.config.get(
        "MAIL_DEFAULT_SENDER", "no-reply@agencyos.local"
    )
    message["Subject"] = subject
    message.set_content(body)
    return message


def send_email(to: str, subject: str, body: str) -> None:
    """Send an email using the configured backend.

    The console backend records messages only during tests and never logs
    message bodies, tokens, or recovery links.
    """
    backend = current_app.config.get("MAIL_BACKEND", "console")
    suppress = current_app.config.get("MAIL_SUPPRESS_SEND", False)

    if suppress:
        return

    if backend == "console":
        if current_app.config.get("TESTING"):
            current_app.config.setdefault("TEST_EMAILS", []).append(
                {
                    "to": to,
                    "subject": subject,
                    "body": body,
                }
            )
        else:
            logger.info("Email queued for %s", to)
        return

    if backend != "smtp":
        raise EmailDeliveryError(f"Unsupported mail backend: {backend}")

    server = current_app.config.get("MAIL_SERVER", "")
    port = current_app.config.get("MAIL_PORT", 587)
    use_tls = current_app.config.get("MAIL_USE_TLS", True)
    username = current_app.config.get("MAIL_USERNAME", "")
    password = current_app.config.get("MAIL_PASSWORD", "")
    sender = current_app.config.get(
        "MAIL_DEFAULT_SENDER", "no-reply@agencyos.local"
    )

    if not server or not sender:
        raise EmailDeliveryError("SMTP mail is not fully configured.")

    try:
        with smtplib.SMTP(server, port, timeout=10) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(
                _build_message(to, subject, body),
                from_addr=sender,
                to_addrs=[to],
            )
    except (OSError, smtplib.SMTPException) as exc:
        logger.error("Email delivery failed: %s", type(exc).__name__)
        raise EmailDeliveryError("Email delivery failed.") from exc
        