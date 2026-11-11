"""Transactional SMTP and Console Email Delivery Engine for AgencyOS.

Provides exception-bounded email dispatch with environment-safe fallback guards
for testing, development, and production environments.
"""

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


def send_email(to: str, subject: str, body: str) -> bool:
    """Send an email using the configured backend safely.

    Wrapped in an exception boundary to ensure SMTP timeouts or misconfigurations
    never crash customer or admin HTTP request flows. Returns True on success, False otherwise.
    """
    try:
        backend = current_app.config.get("MAIL_BACKEND", "console")
        suppress = current_app.config.get("MAIL_SUPPRESS_SEND", False)

        if suppress:
            return True

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
                logger.info("Email simulated for recipient (Console Backend)")
            return True

        if backend != "smtp":
            logger.error("Unsupported mail backend configured: %s", backend)
            return False

        server = current_app.config.get("MAIL_SERVER", "")
        port = current_app.config.get("MAIL_PORT", 587)
        use_tls = current_app.config.get("MAIL_USE_TLS", True)
        username = current_app.config.get("MAIL_USERNAME", "")
        password = current_app.config.get("MAIL_PASSWORD", "")
        sender = current_app.config.get(
            "MAIL_DEFAULT_SENDER", "no-reply@agencyos.local"
        )

        if not server or not sender:
            logger.warning("SMTP mail configuration is incomplete (server or sender missing).")
            return False

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
        return True
    except (OSError, smtplib.SMTPException, Exception) as exc:
        logger.error("Email delivery failed safely: %s", type(exc).__name__)
        return False