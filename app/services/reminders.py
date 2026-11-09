"""Automated Payment and Deliverable Review Reminders for AgencyOS.

Dispatches multi-channel email and WhatsApp alerts for pending milestone deposits,
unreviewed deliverables, and overdue invoices.
"""

from datetime import datetime, timedelta, timezone
import logging
from urllib.parse import urlsplit

from flask import current_app

from app.extensions import db
from app.models import (
    CustomerProfile,
    IST,
    Project,
    ProjectDelivery,
    User,
    get_ist_now,
)
from app.services.email import EmailDeliveryError, send_email
from app.services.whatsapp import send_whatsapp_alert

logger = logging.getLogger(__name__)

PAYMENT_REMINDER_AFTER = timedelta(hours=24)
REVIEW_REMINDER_AFTER = timedelta(hours=48)


def _naive_ist(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(IST).replace(tzinfo=None)
    return value


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _is_due(timestamp: datetime | None, now: datetime, delay: timedelta, normalize) -> bool:
    if timestamp is None:
        return False
    return normalize(timestamp) <= normalize(now) - delay


def _workspace_url(endpoint: str, **values) -> str | None:
    base_url = current_app.config.get("APP_BASE_URL", "").strip()
    if not base_url:
        logger.error("APP_BASE_URL must be configured before sending reminders.")
        return None
    parsed_base = urlsplit(base_url)
    if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
        logger.error("APP_BASE_URL is invalid; refusing to send a reminder link.")
        return None
    adapter = current_app.url_map.bind(
        parsed_base.netloc,
        url_scheme=parsed_base.scheme,
    )
    path = adapter.build(endpoint, values)
    return f"{base_url.rstrip('/')}{path}"


def _email_delivery_available() -> bool:
    if current_app.config.get("MAIL_SUPPRESS_SEND", False):
        logger.error("Email sending is suppressed; reminder will remain pending.")
        return False
    backend = current_app.config.get("MAIL_BACKEND", "console")
    if backend == "smtp":
        return True
    if backend == "console" and current_app.config.get("TESTING", False):
        return True
    logger.error("Reminder email requires an active SMTP backend outside tests.")
    return False


def send_payment_reminder(project_id: int, *, force: bool = False, now: datetime | None = None) -> bool:
    """Dispatches asynchronous payment reminders across Email and WhatsApp."""
    project = db.session.get(Project, project_id)
    if project is None or project.status != "Payment Pending":
        return False
    if project.payment_reminder_sent_at is not None and not force:
        return False

    sent_at = now or get_ist_now()
    if not force and not _is_due(
        project.created_at, sent_at, PAYMENT_REMINDER_AFTER, _naive_utc
    ):
        return False

    customer = db.session.get(User, project.customer_id)
    if customer is None or not customer.email:
        logger.warning("Cannot send payment reminder for project #%s: no client email.", project.id)
        return False

    payment_link = _workspace_url("payments.payment_page", project_id=project.id)
    if payment_link is None or not _email_delivery_available():
        return False

    subject = f"Action required: deposit pending for project #{project.id}"
    body = (
        f"Hi {customer.name or 'Client'},\n\n"
        f"Project #{project.id} ({project.service}) is waiting for its advance "
        "deposit. Work begins once payment is authorized.\n\n"
        f"Sign in to securely continue checkout:\n{payment_link}\n\n"
        "Best regards,\nAgencyOS Team"
    )

    try:
        send_email(customer.email, subject, body)
    except EmailDeliveryError:
        logger.exception("Payment reminder email failed for project #%s.", project.id)
        return False

    # Autonomous WhatsApp Channel Trigger
    profile = CustomerProfile.query.filter_by(user_id=customer.id).first()
    if profile and profile.phone:
        try:
            whatsapp_msg = (
                f"⏰ *Reminder: Advance Deposit Pending — Project #{project.id}*\n\n"
                f"Hi {customer.name or 'Client'},\n"
                f"Your engagement for *{project.service}* is awaiting its milestone kickoff deposit.\n\n"
                f"Authorize payment securely here: {payment_link}"
            )
            send_whatsapp_alert(profile.phone, whatsapp_msg)
        except Exception as w_err:
            logger.warning("Payment reminder WhatsApp alert failed: %s", w_err)

    project.payment_reminder_sent_at = sent_at
    db.session.commit()
    logger.info("Payment reminder dispatched for project #%s.", project.id)
    return True


def send_review_reminder(project_id: int, *, force: bool = False, now: datetime | None = None) -> bool:
    """Dispatches asynchronous review reminders across Email and WhatsApp."""
    project = db.session.get(Project, project_id)
    if project is None or project.status != "Submitted":
        return False

    delivery = (
        ProjectDelivery.query.filter_by(project_id=project.id)
        .order_by(ProjectDelivery.submission_number.desc())
        .first()
    )
    if (
        delivery is None
        or delivery.status != "Submitted"
        or (delivery.review_reminder_sent_at is not None and not force)
    ):
        return False

    sent_at = now or get_ist_now()
    if not force and not _is_due(
        delivery.submitted_at, sent_at, REVIEW_REMINDER_AFTER, _naive_ist
    ):
        return False

    customer = db.session.get(User, project.customer_id)
    if customer is None or not customer.email:
        logger.warning("Cannot send review reminder for project #%s: no client email.", project.id)
        return False

    workspace_link = _workspace_url("customer.project_details", project_id=project.id)
    if workspace_link is None or not _email_delivery_available():
        return False

    subject = f"Review requested: project #{project.id} deliverable"
    body = (
        f"Hi {customer.name or 'Client'},\n\n"
        f"A specialist submitted a deliverable for project #{project.id} "
        f"({project.service}). Please review it and approve or request "
        f"revisions in your workspace:\n{workspace_link}\n\n"
        "Best regards,\nAgencyOS Team"
    )

    try:
        send_email(customer.email, subject, body)
    except EmailDeliveryError:
        logger.exception("Review reminder email failed for project #%s.", project.id)
        return False

    # Autonomous WhatsApp Channel Trigger
    profile = CustomerProfile.query.filter_by(user_id=customer.id).first()
    if profile and profile.phone:
        try:
            whatsapp_msg = (
                f"🚀 *Action Required: Milestone Deliverable Staged — Project #{project.id}*\n\n"
                f"Hi {customer.name or 'Client'},\n"
                f"A new build for *{project.service}* is ready for your sign-off.\n\n"
                f"Review and approve or request revisions: {workspace_link}"
            )
            send_whatsapp_alert(profile.phone, whatsapp_msg)
        except Exception as w_err:
            logger.warning("Review reminder WhatsApp alert failed: %s", w_err)

    delivery.review_reminder_sent_at = sent_at
    db.session.commit()
    logger.info("Review reminder dispatched for project #%s.", project.id)
    return True


def run_due_reminders(now: datetime | None = None) -> dict[str, int]:
    """Send each eligible reminder once; suitable for scheduled CLI / Celery execution."""
    sent_at = now or get_ist_now()
    payment_cutoff = _naive_utc(sent_at) - PAYMENT_REMINDER_AFTER
    review_cutoff = _naive_ist(sent_at) - REVIEW_REMINDER_AFTER
    summary = {"payment_sent": 0, "review_sent": 0, "failed": 0}

    payment_ids = [
        project_id
        for (project_id,) in db.session.query(Project.id)
        .filter(
            Project.status == "Payment Pending",
            Project.payment_reminder_sent_at.is_(None),
            Project.created_at <= payment_cutoff,
        )
        .all()
    ]
    for project_id in payment_ids:
        if send_payment_reminder(project_id, now=sent_at):
            summary["payment_sent"] += 1
        else:
            summary["failed"] += 1

    latest_delivery_id = (
        db.session.query(ProjectDelivery.id)
        .filter(ProjectDelivery.project_id == Project.id)
        .order_by(ProjectDelivery.submission_number.desc())
        .limit(1)
        .correlate(Project)
        .scalar_subquery()
    )
    review_ids = [
        project_id
        for (project_id,) in db.session.query(ProjectDelivery.project_id)
        .join(Project, Project.id == ProjectDelivery.project_id)
        .filter(
            ProjectDelivery.id == latest_delivery_id,
            Project.status == "Submitted",
            ProjectDelivery.status == "Submitted",
            ProjectDelivery.review_reminder_sent_at.is_(None),
            ProjectDelivery.submitted_at <= review_cutoff,
        )
        .distinct()
        .all()
    ]
    for project_id in review_ids:
        if send_review_reminder(project_id, now=sent_at):
            summary["review_sent"] += 1
        else:
            summary["failed"] += 1

    return summary