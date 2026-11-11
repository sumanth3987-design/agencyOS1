"""Flask Command Line Interface Commands for AgencyOS.

Includes administrative management commands, user seeding, and background
maintenance runners such as the 72-hour Ghost Client Auto-Approval daemon.
"""

from datetime import datetime, timedelta
from decimal import Decimal
import logging
import click
from flask.cli import with_appcontext

from app.extensions import db
from app.models import (
    ActivityLog,
    Invoice,
    Milestone,
    Notification,
    PACKAGE_PRICES,
    Payment,
    ProfessionalProfile,
    ProfessionalProjectApplication,
    Project,
    ProjectDelivery,
    User,
    WalletTransaction,
    get_ist_now,
)
from app.services.whatsapp import notify_project_completed, send_whatsapp_alert
from app.utils.currency import (
    compute_financial_split,
    format_currency,
    get_package_price,
    sanitize_currency,
)

logger = logging.getLogger(__name__)

@click.command("create-admin")
@click.option("--email", prompt="Admin email", help="Admin email address")
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    help="Admin password",
)
@click.option(
    "--name", default="Agency Owner", prompt="Full Name", help="Admin full name"
)
@with_appcontext
def create_admin_command(email, password, name):
    """Create or promote a user to the AgencyOS Admin role."""
    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        existing_user.role = "admin"
        existing_user.set_password(password)
        existing_user.name = name
        existing_user.full_name = name
        db.session.commit()
        click.echo(f"Updated existing user {email} to Admin role successfully.")
        return

    admin = User()
    admin.email = email
    admin.name = name
    admin.full_name = name
    admin.role = "admin"
    admin.set_password(password)

    db.session.add(admin)
    db.session.commit()
    click.echo(f"Successfully created new Admin user: {email}")


@click.command("auto-approve-sprints")
@click.option(
    "--hours",
    default=72,
    type=int,
    help="Inactivity threshold in hours before auto-approval executes (default: 72)",
)
@with_appcontext
def auto_approve_sprints_command(hours):
    """Scan and auto-approve deliverables awaiting client review past the inactivity threshold."""
    now = get_ist_now()
    cutoff_time = now - timedelta(hours=hours)
    click.secho(f"[*] Running 72h Ghost Client Auto-Approval Daemon (Cutoff: {cutoff_time.strftime('%Y-%m-%d %H:%M:%S IST')})...", fg="cyan")

    # Find candidate projects currently in Submitted status
    candidate_projects = Project.query.filter_by(status="Submitted").all()
    approved_count = 0
    errors_count = 0

    for project in candidate_projects:
        # Re-fetch project and latest delivery within the loop to ensure fresh state
        latest_delivery = (
            ProjectDelivery.query.filter_by(project_id=project.id)
            .order_by(ProjectDelivery.submission_number.desc())
            .first()
        )
        
        if not latest_delivery or latest_delivery.status != "Submitted":
            continue

        submission_time = latest_delivery.submitted_at
        if submission_time.tzinfo is None:
            from zoneinfo import ZoneInfo
            submission_time = submission_time.replace(tzinfo=ZoneInfo("Asia/Kolkata"))

        if submission_time > cutoff_time:
            continue

        try:
            # Wrap each project processing in its own sub-transaction
            with db.session.begin_nested():
                click.echo(f" -> Auto-approving Project #{project.id} ({project.service})...")
                
                latest_delivery.status = "Approved"
                latest_delivery.reviewed_at = now

                paid_payments_count = Payment.query.filter_by(
                    project_id=project.id, status="Paid", addon_id=None
                ).count()

                assigned_app = ProfessionalProjectApplication.query.filter_by(
                    project_id=project.id, status="Assigned"
                ).first()

                specialist_id = project.assigned_professional_id or (assigned_app.professional_id if assigned_app else None)
                specialist_profile = ProfessionalProfile.query.filter_by(user_id=specialist_id).with_for_update().first() if specialist_id else None

                currency = sanitize_currency(project.currency)
                gross = get_package_price(project.package, currency, PACKAGE_PRICES)
                talent_share, _, _ = compute_financial_split(gross, currency)
                total_talent_payout = (
                    Decimal(str(assigned_app.proposed_rate))
                    if (assigned_app and assigned_app.proposed_rate is not None)
                    else talent_share
                )

                milestones = Milestone.query.filter_by(project_id=project.id).order_by(Milestone.id.asc()).all()

                if paid_payments_count <= 1:
                    project.status = "In Progress"
                    if milestones and len(milestones) >= 1:
                        milestones[0].status = "completed"
                        milestones[0].released_at = now
                    ActivityLog.log_event(project.id, "AUTO_APPROVED", "Gate 1 auto-approved.", category="warning")

                elif paid_payments_count == 2:
                    project.status = "In Progress"
                    if milestones and len(milestones) >= 2:
                        milestones[1].status = "completed"
                        milestones[1].released_at = now
                    ActivityLog.log_event(project.id, "AUTO_APPROVED", "Gate 2 auto-approved.", category="warning")

                else:
                    project.status = "Completed"
                    if specialist_profile:
                        specialist_profile.pending_balance = max(Decimal("0.00"), specialist_profile.pending_balance - total_talent_payout)
                        specialist_profile.available_balance += total_talent_payout

                        tx = WalletTransaction(
                            user_id=specialist_id,
                            amount=total_talent_payout,
                            currency=currency,
                            transaction_type="ESCROW_RELEASED",
                            reference_type="PROJECT",
                            reference_id=project.id,
                            description=f"Auto-approved #{project.id}. Payout unlocked.",
                        )
                        db.session.add(tx)

                    for m in milestones:
                        m.status = "completed"
                        m.released_at = now
                    
                    ActivityLog.log_event(project.id, "AUTO_APPROVED", "Project Completed (Auto).", category="success")

                    # Safe Notification Dispatch
                    try:
                        customer_user = db.session.get(User, project.customer_id)
                        if customer_user:
                            notify_project_completed(
                                project.id,
                                project.service,
                                getattr(customer_user.customer_profile, "phone", None),
                                customer_user.name,
                            )
                    except Exception as whatsapp_exc:
                        logger.error(f"WhatsApp notification failed for auto-approval {project.id}: {whatsapp_exc}")

            db.session.commit()
            approved_count += 1

        except Exception as exc:
            db.session.rollback()
            errors_count += 1
            logger.error(f"Error auto-approving Project #{project.id}: {str(exc)}")
            click.secho(f" [!] Error Project #{project.id}: {str(exc)}", fg="red")

    click.secho(f"[✓] Auto-approval daemon completed. Approved: {approved_count}, Failed: {errors_count}.", fg="green")


def init_app(app):
    """Register CLI commands with the Flask application factory."""
    app.cli.add_command(create_admin_command)
    app.cli.add_command(auto_approve_sprints_command)