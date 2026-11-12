"""Automated Payment and Deliverable Review Reminders for AgencyOS.

Dispatches multi-channel email and WhatsApp alerts for pending milestone deposits,
unreviewed deliverables, 48-hour urgency review nudges, and overdue invoices with robust exception handling.
"""

from datetime import datetime, timedelta, timezone
import logging
from urllib.parse import urlsplit

from flask import current_app

from app.extensions import db
from app.models import (
    ActivityLog,
    CustomerProfile,
    IST,
    Project,
    ProjectDelivery,
    User,
    get_ist_now,
)
from app.services.email import EmailDeliveryError, send_email
from app.services.whatsapp import notify_review_nudge, send_whatsapp_alert

logger = logging.getLogger(__name__)

PAYMENT_REMINDER_AFTER = timedelta(hours=24)
REVIEW_REMINDER_AFTER = timedelta(hours=48)
REVIEW_URGENCY_NUDGE_AFTER = timedelta(hours=24)


def _naive_ist(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(IST).replace(tzinfo=None)
    if value.tzinfo is not None:
        return value.astimezone(IST).replace(tzinfo=None)
    return value


def _naive_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _is_due(timestamp: datetime | None, now: datetime, delay: timedelta, normalize) -> bool:
    if timestamp is None:
        return False
    return normalize(timestamp) <= normalize(now) - delay


def _workspace_url(endpoint: str, **values) -> str | None:
    try:
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
    except Exception as exc:
        logger.error("Error building workspace URL for reminders: %s", exc)
        return None


def _email_delivery_available() -> bool:
    try:
        if current_app.config.get("MAIL_SUPPRESS_SEND", False):
            return False
        backend = current_app.config.get("MAIL_BACKEND", "console")
        if backend == "smtp":
            return True
        if backend == "console" and current_app.config.get("TESTING", False):
            return True
        return False
    except Exception:
        return False


def send_payment_reminder(project_id: int, *, force: bool = False, now: datetime | None = None) -> bool:
    """Dispatches asynchronous payment reminders across Email and WhatsApp with fault tolerance."""
    try:
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

        email_sent = False
        payment_link = _workspace_url("payments.payment_page", project_id=project.id)
        if _email_delivery_available():
            subject = f"Action required: deposit pending for project #{project.id}"
            body = (
                f"Hi {customer.name or 'Client'},\n\n"
                f"Project #{project.id} ({project.service}) is waiting for its advance "
                "deposit. Work begins once payment is authorized.\n\n"
                f"Sign in to securely continue checkout:\n{payment_link or ''}\n\n"
                "Best regards,\nAgencyOS Team"
            )
            try:
                email_sent = send_email(customer.email, subject, body)
            except Exception:
                logger.exception("Payment reminder email failed for project #%s.", project.id)
                email_sent = False

            # If email delivery was active and failed, abort and do not mark as sent
            if not email_sent:
                return False

        # Autonomous WhatsApp Channel Trigger
        profile = CustomerProfile.query.filter_by(user_id=customer.id).first()
        if profile and profile.phone and payment_link:
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
    except Exception as exc:
        db.session.rollback()
        logger.error("Exception in send_payment_reminder for project #%s: %s", project_id, exc)
        return False


def send_review_reminder(project_id: int, *, force: bool = False, now: datetime | None = None) -> bool:
    """Dispatches asynchronous review reminders across Email and WhatsApp with fault tolerance."""
    try:
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

        email_sent = False
        workspace_link = _workspace_url("customer.project_details", project_id=project.id)
        if _email_delivery_available():
            subject = f"Review requested: project #{project.id} deliverable"
            body = (
                f"Hi {customer.name or 'Client'},\n\n"
                f"A specialist submitted a deliverable for project #{project.id} "
                f"({project.service}). Please review it and approve or request "
                f"revisions in your workspace:\n{workspace_link or ''}\n\n"
                "Best regards,\nAgencyOS Team"
            )
            try:
                email_sent = send_email(customer.email, subject, body)
            except Exception:
                logger.exception("Review reminder email failed for project #%s.", project.id)
                email_sent = False

            # If email delivery was active and failed, abort and do not mark as sent
            if not email_sent:
                return False

        # Autonomous WhatsApp Channel Trigger
        profile = CustomerProfile.query.filter_by(user_id=customer.id).first()
        if profile and profile.phone and workspace_link:
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
    except Exception as exc:
        db.session.rollback()
        logger.error("Exception in send_review_reminder for project #%s: %s", project_id, exc)
        return False


def send_review_urgency_nudge(project_id: int, *, now: datetime | None = None) -> bool:
    """Dispatches a 48h urgency WhatsApp nudge before the 72h auto-approval executes safely.

    Protected against repetitive spamming by checking ActivityLog.
    """
    try:
        project = db.session.get(Project, project_id)
        if project is None or project.status != "Submitted":
            return False

        delivery = (
            ProjectDelivery.query.filter_by(project_id=project.id)
            .order_by(ProjectDelivery.submission_number.desc())
            .first()
        )
        if delivery is None or delivery.status != "Submitted":
            return False

        # Spam guard: check if this submission has already been nudged
        already_nudged = (
            ActivityLog.query.filter_by(
                project_id=project.id,
                action=f"REVIEW_URGENCY_NUDGE_DELIVERY_{delivery.id}",
            ).first()
            is not None
        )
        if already_nudged:
            return False

        # Normalize both datetimes to naive IST to prevent subtraction TypeError
        sent_at_naive = _naive_ist(now or get_ist_now())
        sub_time_naive = _naive_ist(delivery.submitted_at)
        elapsed = sent_at_naive - sub_time_naive

        if not (timedelta(hours=48) <= elapsed <= timedelta(hours=72)):
            return False

        customer = db.session.get(User, project.customer_id)
        if customer is None:
            return False

        profile = CustomerProfile.query.filter_by(user_id=customer.id).first()
        if profile and profile.phone:
            try:
                hours_left = max(1, 72 - int(elapsed.total_seconds() / 3600))
                notify_review_nudge(
                    project.id,
                    project.service,
                    profile.phone,
                    getattr(customer, "name", "Client") or "Client",
                    hours_left=hours_left,
                )
                ActivityLog.log_event(
                    project_id=project.id,
                    action=f"REVIEW_URGENCY_NUDGE_DELIVERY_{delivery.id}",
                    description=f"48-hour deliverable review urgency nudge dispatched ({hours_left}h remaining).",
                    category="warning",
                )
                db.session.commit()
                logger.info("Review urgency nudge dispatched for project #%s.", project.id)
                return True
            except Exception as exc:
                db.session.rollback()
                logger.warning("Review urgency nudge failed: %s", exc)

        return False
    except Exception as exc:
        db.session.rollback()
        logger.error("Exception in send_review_urgency_nudge for project #%s: %s", project_id, exc)
        return False


def run_due_reminders(now: datetime | None = None) -> dict[str, int]:
    """Send each eligible reminder once safely; suitable for scheduled CLI execution."""
    sent_at = now or get_ist_now()
    payment_cutoff = _naive_utc(sent_at) - PAYMENT_REMINDER_AFTER
    review_cutoff = _naive_ist(sent_at) - REVIEW_REMINDER_AFTER
    summary = {"payment_sent": 0, "review_sent": 0, "urgency_nudges_sent": 0, "failed": 0}

    try:
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
    except Exception as exc:
        logger.error("Error querying payment reminders: %s", exc)

    try:
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
    except Exception as exc:
        logger.error("Error querying review reminders: %s", exc)

    try:
        submitted_project_ids = [p.id for p in Project.query.filter_by(status="Submitted").all()]
        for pid in submitted_project_ids:
            if send_review_urgency_nudge(pid, now=sent_at):
                summary["urgency_nudges_sent"] += 1
    except Exception as exc:
        logger.error("Error running review urgency nudges: %s", exc)

    return summary