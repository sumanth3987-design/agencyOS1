"""AgencyOS Admin Command Center Blueprint.

Governs project pipelines, specialist talent allocations, application review gates,
offline manual settlements, broadcast notifications, two-stage escrow releases,
UPI/Bank payout approvals, and AI SOW drafting.
"""

import csv
import json
import logging
import os
import re
import secrets
import urllib.request
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
import ipaddress
from typing import Any
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db, limiter
from app.models import (
    ActivityLog,
    AgencySetting,
    Business,
    ChatMessage,
    CustomerProfile,
    Dispute,
    Invoice,
    Milestone,
    Notification,
    PACKAGE_PRICES,
    Payment,
    ProfessionalProfile,
    ProfessionalProjectApplication,
    Project,
    ProjectAddOn,
    ProjectDelivery,
    RazorpaySubscription,
    Review,
    User,
    WalletTransaction,
    WithdrawalRequest,
    calculate_professional_profile_completion,
    get_ist_now,
    get_latest_project_delivery,
)
from app.utils.currency import (
    compute_financial_split,
    format_currency,
    get_package_price,
    sanitize_currency,
)

logger = logging.getLogger(__name__)
admin_bp = Blueprint("admin", __name__)


def _validated_agency_settings(form: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    values = {
        "agency_name": form.get("agency_name", "").strip(),
        "primary_color": form.get("primary_color", "").strip(),
        "logo_url": form.get("logo_url", "").strip(),
        "whatsapp_support_number": form.get("whatsapp_support_number", "").strip(),
    }
    errors: dict[str, str] = {}

    if not values["agency_name"]:
        errors["agency_name"] = "Enter an agency name."
    elif len(values["agency_name"]) > 120:
        errors["agency_name"] = "Use 120 characters or fewer."

    if not re.fullmatch(r"#[0-9a-fA-F]{6}", values["primary_color"]):
        errors["primary_color"] = "Enter a six-digit hex color, such as #6366f1."

    if values["logo_url"]:
        logo = values["logo_url"]
        try:
            parsed = urlsplit(logo)
            host = parsed.hostname
            if (
                len(logo) > 500
                or parsed.scheme.lower() not in ("http", "https")
                or not parsed.netloc
                or not host
                or parsed.username is not None
                or parsed.password is not None
                or any(char.isspace() or ord(char) < 32 for char in logo)
            ):
                raise ValueError
            try:
                ipaddress.ip_address(host)
            except ValueError:
                ascii_host = host.encode("idna").decode("ascii")
                labels = ascii_host.rstrip(".").split(".")
                if not labels or any(
                    not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                    for label in labels
                ):
                    raise ValueError
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError
        except (ValueError, UnicodeError):
            errors["logo_url"] = "Use a valid HTTP or HTTPS URL with a host and no credentials."

    phone = values["whatsapp_support_number"]
    digits = phone[1:] if phone.startswith("+") else phone
    if not re.fullmatch(r"\d{8,15}", digits):
        errors["whatsapp_support_number"] = "Enter 8–15 digits, with an optional leading +."

    return values, errors


def _agency_settings_form_values(settings: AgencySetting | None, submitted: dict[str, str] | None = None) -> dict[str, str]:
    defaults = {
        "agency_name": "AgencyOS",
        "primary_color": "#6366f1",
        "logo_url": "",
        "whatsapp_support_number": "+919876543210",
    }
    if settings:
        defaults.update({
            "agency_name": settings.agency_name,
            "primary_color": settings.primary_color or "#6366f1",
            "logo_url": settings.logo_url or "",
            "whatsapp_support_number": settings.whatsapp_support_number or "+919876543210",
        })
    if submitted is not None:
        defaults.update(submitted)
    return defaults


def _apply_agency_settings(settings: AgencySetting, values: dict[str, str]) -> None:
    for key, value in values.items():
        setattr(settings, key, (value or None) if key == "logo_url" else value)


# =========================================================
# ADMIN EXECUTIVE DASHBOARD
# =========================================================

@admin_bp.route("/admin/dashboard")
@admin_bp.route("/admin")
@login_required
def admin_dashboard() -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    page = request.args.get("page", 1, type=int)
    per_page = 15
    status_filter = request.args.get("status", "all")
    search_query = request.args.get("search", "", type=str).strip()

    customer_count = User.query.filter(User.role.in_(["customer", "client"])).count()
    professional_count = User.query.filter_by(role="professional").count()
    project_count = Project.query.count()

    pending_project_count = Project.query.filter_by(status="Pending Assignment").count()
    pending_verification_count = (
        User.query.filter(User.is_verified == False, User.role.in_(["customer", "client", "professional"])).count()
    )
    application_count = ProfessionalProjectApplication.query.count()

    submitted_project_count = Project.query.filter_by(status="Submitted").count()
    in_progress_project_count = Project.query.filter_by(status="In Progress").count()
    completed_project_count = Project.query.filter_by(status="Completed").count()
    payment_pending_project_count = Project.query.filter_by(status="Payment Pending").count()
    assigned_project_count = Project.query.filter_by(status="Assigned").count()
    revision_project_count = Project.query.filter_by(status="Revision").count()

    query = Project.query
    if status_filter != "all":
        query = query.filter(Project.status == status_filter)

    if search_query:
        search_term = f"%{search_query}%"
        query = query.filter(
            db.or_(
                Project.service.ilike(search_term),
                Project.requirements.ilike(search_term),
            )
        )

    pagination = query.order_by(Project.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    recent_projects = pagination.items

    return render_template(
        "admin_dashboard.html",
        customer_count=customer_count,
        professional_count=professional_count,
        project_count=project_count,
        pending_project_count=pending_project_count,
        pending_verification_count=pending_verification_count,
        application_count=application_count,
        submitted_project_count=submitted_project_count,
        in_progress_project_count=in_progress_project_count,
        completed_project_count=completed_project_count,
        payment_pending_project_count=payment_pending_project_count,
        assigned_project_count=assigned_project_count,
        revision_project_count=revision_project_count,
        recent_projects=recent_projects,
        pagination=pagination,
        current_status=status_filter,
        search_query=search_query,
    )


@admin_bp.route("/admin/settings", methods=["GET", "POST"])
@login_required
def agency_settings() -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    settings = db.session.get(AgencySetting, 1)
    errors: dict[str, str] = {}
    form_values = _agency_settings_form_values(settings)

    if request.method == "POST":
        form_values, errors = _validated_agency_settings(request.form)
        color_picker_value = (
            form_values["primary_color"]
            if re.fullmatch(r"#[0-9a-fA-F]{6}", form_values["primary_color"])
            else (
                settings.primary_color
                if settings and re.fullmatch(r"#[0-9a-fA-F]{6}", settings.primary_color or "")
                else "#6366f1"
            )
        )
        if errors:
            return render_template(
                "admin_settings.html",
                form_values=form_values,
                errors=errors,
                color_picker_value=color_picker_value,
            ), 400

        try:
            if settings is None:
                settings = AgencySetting()
                settings.id = 1
                db.session.add(settings)
            _apply_agency_settings(settings, form_values)
            db.session.commit()
            flash("Agency branding settings saved.", "success")
        except IntegrityError:
            db.session.rollback()
            settings = db.session.get(AgencySetting, 1)
            if settings is None:
                raise
            _apply_agency_settings(settings, form_values)
            db.session.commit()
            flash("Agency branding settings saved.", "success")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error saving settings: {str(e)}")
            flash("An unexpected error occurred while saving settings.", "danger")

        return redirect(url_for("admin.agency_settings"))

    return render_template(
        "admin_settings.html",
        form_values=form_values,
        errors=errors,
        color_picker_value=(
            settings.primary_color
            if settings and re.fullmatch(r"#[0-9a-fA-F]{6}", settings.primary_color or "")
            else "#6366f1"
        ),
    )


# =========================================================
# ADMIN PROJECT DIRECT INSPECTION ROUTE
# =========================================================

@admin_bp.route("/admin/project/<int:project_id>")
@login_required
def admin_project_view(project_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    return redirect(url_for("customer.project_details", project_id=project_id))


# =========================================================
# ADMIN CLIENT CRM & KYC VERIFICATION
# =========================================================

@admin_bp.route("/admin/customers")
@login_required
def admin_customers() -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    customers = User.query.filter(User.role.in_(["customer", "client"])).order_by(User.created_at.desc()).all()
    all_projects = Project.query.order_by(Project.created_at.desc()).all()

    customer_profiles = {c.id: CustomerProfile.query.filter_by(user_id=c.id).first() for c in customers}
    customer_projects = {c.id: [p for p in all_projects if p.customer_id == c.id] for c in customers}

    return render_template(
        "admin_customers.html",
        customers=customers,
        customer_profiles=customer_profiles,
        customer_projects=customer_projects,
    )


@admin_bp.route("/admin/customers/<int:user_id>/verify", methods=["POST"])
@login_required
def verify_customer(user_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        customer = db.session.get(User, user_id)
        if not customer:
            flash("Customer not found.", "danger")
            return redirect(url_for("admin.admin_customers"))

        customer.is_verified = True
        customer.verification_status = "Verified"
        customer.verified_at = get_ist_now()

        notif = Notification()
        notif.user_id = customer.id
        notif.title = "Account Verified 🎉"
        notif.message = "Your customer account has been approved by the agency administrator. You can now commission projects."
        notif.link = "/dashboard"
        db.session.add(notif)

        db.session.commit()
        flash(f"Customer {customer.email} verified successfully.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error verifying customer {user_id}: {str(e)}")
        flash("An error occurred while verifying the customer.", "danger")
        
    return redirect(url_for("admin.admin_customers"))


@admin_bp.route("/admin/customers/<int:user_id>/reject", methods=["POST"])
@login_required
def reject_customer(user_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        customer = db.session.get(User, user_id)
        if not customer:
            flash("Customer not found.", "danger")
            return redirect(url_for("admin.admin_customers"))

        customer.is_verified = False
        customer.verification_status = "Rejected"
        db.session.commit()
        flash(f"Customer {customer.email} onboarding marked as Rejected.", "warning")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error rejecting customer {user_id}: {str(e)}")
        flash("An error occurred while rejecting the customer.", "danger")
        
    return redirect(url_for("admin.admin_customers"))


# =========================================================
# SPECIALIST TALENT MANAGEMENT & READ-ONLY PROFILE INSPECTION
# =========================================================

@admin_bp.route("/admin/professionals")
@login_required
def admin_professionals() -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    profiles = (
        ProfessionalProfile.query
        .join(User, ProfessionalProfile.user_id == User.id)
        .filter(User.role != "admin")
        .order_by(ProfessionalProfile.created_at.desc())
        .all()
    )
    completion_map = {p.id: calculate_professional_profile_completion(p) for p in profiles}
    meritocracy_map = {p.id: p.get_performance_metrics() for p in profiles}

    return render_template(
        "admin_professionals.html",
        profiles=profiles,
        completion_map=completion_map,
        meritocracy_map=meritocracy_map,
    )


@admin_bp.route("/admin/professionals/<int:profile_id>/view")
@login_required
def admin_view_professional(profile_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    profile = db.session.get(ProfessionalProfile, profile_id)
    if not profile:
        flash("Specialist profile not found.", "danger")
        return redirect(url_for("admin.admin_professionals"))

    metrics = profile.get_performance_metrics()
    return render_template("admin_professional_profile.html", profile=profile, metrics=metrics)


@admin_bp.route("/admin/professionals/<int:profile_id>/verify", methods=["POST"])
@limiter.limit("20 per minute")
@login_required
def verify_professional(profile_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        profile = db.session.get(ProfessionalProfile, profile_id)
        if not profile:
            flash("Profile not found.", "danger")
            return redirect(url_for("admin.admin_professionals"))

        profile.verification_status = "Verified"
        profile.verification_notes = "Verified by agency administrator."
        
        target_user = getattr(profile, "user", None) or db.session.get(User, profile.user_id)
        if target_user:
            target_user.is_verified = True
            target_user.verification_status = "Verified"
            target_user.verified_at = get_ist_now()

            notif = Notification()
            notif.user_id = target_user.id
            notif.title = "Specialist Profile Approved 🛡️"
            notif.message = "Your profile is verified. You can now apply for matched sprint projects."
            notif.link = "/professional/dashboard"
            db.session.add(notif)

        db.session.commit()
        flash("Specialist verified successfully.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error verifying professional profile {profile_id}: {str(e)}")
        flash("An error occurred while verifying the specialist.", "danger")
        
    return redirect(url_for("admin.admin_professionals"))


@admin_bp.route("/admin/professionals/<int:profile_id>/reject", methods=["POST"])
@limiter.limit("20 per minute")
@login_required
def reject_professional(profile_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        profile = db.session.get(ProfessionalProfile, profile_id)
        if not profile:
            flash("Profile not found.", "danger")
            return redirect(url_for("admin.admin_professionals"))

        profile.verification_status = "Rejected"
        target_user = getattr(profile, "user", None) or db.session.get(User, profile.user_id)
        if target_user:
            target_user.is_verified = False
            target_user.verification_status = "Rejected"

        db.session.commit()
        flash("Specialist onboarding application rejected.", "warning")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error rejecting professional profile {profile_id}: {str(e)}")
        flash("An error occurred while rejecting the specialist.", "danger")
        
    return redirect(url_for("admin.admin_professionals"))


# =========================================================
# TALENT APPLICATION ASSIGNMENTS & TWO-STAGE ESCROW LOCK
# =========================================================

@admin_bp.route("/admin/applications")
@login_required
def admin_applications() -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    applications = ProfessionalProjectApplication.query.order_by(
        ProfessionalProjectApplication.created_at.desc()
    ).all()

    return render_template("admin_applications.html", applications=applications)


@admin_bp.route("/admin/applications/<int:application_id>/assign", methods=["POST"])
@login_required
def assign_professional(application_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    app_record = db.session.get(ProfessionalProjectApplication, application_id)
    if not app_record:
        flash("Application not found.", "danger")
        return redirect(url_for("admin.admin_applications"))

    project: Any = db.session.get(Project, app_record.project_id) if app_record.project_id else getattr(app_record, "project", None)
    if not project:
        flash("Associated project not found.", "danger")
        return redirect(url_for("admin.admin_applications"))

    if app_record.status != "Applied":
        flash("This application has already been processed.", "warning")
        return redirect(url_for("admin.admin_applications"))

    if project.status != "Pending Assignment":
        flash("Cannot assign a project that is not in Pending Assignment status.", "warning")
        return redirect(url_for("admin.admin_applications"))

    specialist_profile = ProfessionalProfile.query.filter_by(user_id=app_record.professional_id).first()
    if not specialist_profile or (specialist_profile.verification_status or "").strip() != "Verified":
        flash("Cannot assign an unverified specialist.", "warning")
        return redirect(url_for("admin.admin_applications"))

    try:
        app_record.status = "Assigned"
        project.status = "Assigned"
        project.assigned_professional_id = app_record.professional_id

        other_apps = ProfessionalProjectApplication.query.filter(
            ProfessionalProjectApplication.project_id == project.id,
            ProfessionalProjectApplication.id != app_record.id,
            ProfessionalProjectApplication.status == "Applied",
        ).all()
        for o in other_apps:
            o.status = "Not Selected"

        currency = sanitize_currency(project.currency)
        gross = get_package_price(project.package, currency, PACKAGE_PRICES)
        talent_share, _, _ = compute_financial_split(gross, currency)
        
        locked_amount = Decimal(str(app_record.proposed_rate)) if app_record.proposed_rate is not None else talent_share

        current_pending = Decimal(str(specialist_profile.pending_balance or 0))
        specialist_profile.pending_balance = current_pending + locked_amount

        tx = WalletTransaction()
        tx.user_id = app_record.professional_id
        tx.amount = locked_amount
        tx.currency = currency
        tx.transaction_type = "ESCROW_LOCKED"
        tx.reference_type = "PROJECT"
        tx.reference_id = project.id
        tx.description = f"Project #{project.id} assigned. ₹{locked_amount} locked in escrow."
        tx.created_at = get_ist_now()
        db.session.add(tx)

        notif = Notification()
        notif.user_id = app_record.professional_id
        notif.title = f"Sprint Assigned: #{project.id} {project.service}"
        notif.message = (
            f"You have been assigned to Project #{project.id}. ₹{locked_amount} has been locked in your escrow wallet. "
            "Open your workspace to begin."
        )
        notif.link = f"/professional/project/{project.id}/workspace"
        notif.created_at = get_ist_now()
        db.session.add(notif)

        specialist_user = getattr(app_record, "professional", None) or db.session.get(User, app_record.professional_id)
        specialist_name = specialist_user.name if specialist_user else "Specialist"

        ActivityLog.log_event(
            project_id=project.id,
            action="SPECIALIST_ASSIGNED",
            description=f"Specialist {specialist_name} assigned to project #{project.id}. Escrow locked: ₹{locked_amount}.",
            user_id=current_user.id,
            category="success",
        )

        db.session.commit()
        flash(f"Specialist {specialist_name} assigned to Project #{project.id}. ₹{locked_amount} locked in escrow.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error assigning specialist application {application_id}: {str(e)}")
        flash("An error occurred while assigning the specialist.", "danger")
        
    return redirect(url_for("admin.admin_applications"))


# =========================================================
# PIPELINES & SPRINT REMINDERS
# =========================================================

@admin_bp.route("/admin/project-management")
@login_required
def admin_project_management() -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    projects = Project.query.order_by(Project.created_at.desc()).all()
    return render_template("admin_project_management.html", projects=projects)


@admin_bp.route("/admin/project/<int:project_id>/send-reminder", methods=["POST"])
@login_required
def send_admin_reminder(project_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("admin.admin_project_management"))

    if project.status == "Payment Pending":
        from app.services.reminders import send_payment_reminder
        sent = send_payment_reminder(project.id, force=True)
        reminder_type = "payment"
    elif project.status == "Submitted":
        from app.services.reminders import send_review_reminder
        sent = send_review_reminder(project.id, force=True)
        reminder_type = "review"
    else:
        flash("This project is not awaiting payment or client review.", "info")
        return redirect(url_for("admin.admin_project_management"))

    if sent:
        flash(f"{reminder_type.title()} reminder sent for Project #{project.id}.", "success")
    else:
        flash("Reminder could not be sent. Check email delivery configuration.", "warning")
    return redirect(url_for("admin.admin_project_management"))


# =========================================================
# QUALITY REVIEW GATE & ESCROW FUND UNLOCK
# =========================================================

@admin_bp.route("/admin/project-review")
@login_required
def admin_project_review() -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    projects = Project.query.order_by(Project.created_at.desc()).all()
    project_deliveries = {
        p.id: ProjectDelivery.query.filter_by(project_id=p.id).order_by(ProjectDelivery.submission_number.desc()).first()
        for p in projects
    }
    project_applications = {
        p.id: ProfessionalProjectApplication.query.filter_by(project_id=p.id, status="Assigned").first()
        for p in projects
    }

    return render_template(
        "admin_project_review.html",
        projects=projects,
        project_deliveries=project_deliveries,
        project_applications=project_applications,
    )


@admin_bp.route("/admin/project/<int:project_id>/revision", methods=["POST"])
@login_required
def request_project_revision(project_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("admin.admin_project_review"))

    if project.status != "Submitted":
        flash("Project must be in Submitted status to request a revision.", "warning")
        return redirect(url_for("admin.admin_project_review"))

    comment = request.form.get("revision_comment", "").strip()
    if not comment:
        flash("Please provide a revision comment.", "warning")
        return redirect(url_for("admin.admin_project_review"))

    try:
        latest_delivery = get_latest_project_delivery(project.id)
        if latest_delivery:
            latest_delivery.status = "Revision"
            latest_delivery.review_comment = comment
            latest_delivery.reviewed_at = get_ist_now()

        project.status = "Revision"
        project.revision_comment = comment
        ActivityLog.log_event(
            project_id=project.id,
            action="REVISION_REQUESTED",
            description=f"Agency administrator requested revision: {comment}",
            user_id=current_user.id,
            category="warning",
        )
        db.session.commit()
        flash("Revision requested successfully.", "warning")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error requesting revision for project {project_id}: {str(e)}")
        flash("An error occurred while requesting revision.", "danger")
        
    return redirect(url_for("admin.admin_project_review"))


@admin_bp.route("/admin/project/<int:project_id>/approve", methods=["POST"])
@login_required
def approve_project(project_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("admin.admin_project_review"))

    if project.status != "Submitted":
        flash("Project must be in Submitted status to approve deliverables.", "warning")
        return redirect(url_for("admin.admin_project_review"))
    
    try:
        latest_delivery = get_latest_project_delivery(project.id)
        if latest_delivery:
            latest_delivery.status = "Approved"
            latest_delivery.reviewed_at = get_ist_now()

        project.status = "Completed"
        project.revision_comment = None

        assigned_app = ProfessionalProjectApplication.query.filter_by(
            project_id=project.id, status="Assigned"
        ).first()

        if assigned_app and assigned_app.professional_id:
            specialist_id = assigned_app.professional_id
            specialist_profile = ProfessionalProfile.query.filter_by(user_id=specialist_id).first()

            currency = sanitize_currency(project.currency)
            gross = get_package_price(project.package, currency, PACKAGE_PRICES)
            talent_share, _, _ = compute_financial_split(gross, currency)
            
            earned_amount = Decimal(str(assigned_app.proposed_rate)) if assigned_app.proposed_rate is not None else talent_share

            if specialist_profile:
                curr_pend = Decimal(str(specialist_profile.pending_balance or 0))
                curr_avail = Decimal(str(specialist_profile.available_balance or 0))

                specialist_profile.pending_balance = max(Decimal("0.00"), curr_pend - earned_amount)
                specialist_profile.available_balance = curr_avail + earned_amount

            tx = WalletTransaction()
            tx.user_id = specialist_id
            tx.amount = earned_amount
            tx.currency = currency
            tx.transaction_type = "ESCROW_RELEASED"
            tx.reference_type = "PROJECT"
            tx.reference_id = project.id
            tx.description = f"Project #{project.id} deliverables approved. ₹{earned_amount} released to available balance."
            tx.created_at = get_ist_now()
            db.session.add(tx)

            notif = Notification()
            notif.user_id = specialist_id
            notif.title = f"Milestone Approved! ₹{earned_amount} Unlocked 💰"
            notif.message = (
                f"Deliverables for Project #{project.id} have been approved. ₹{earned_amount} has been released "
                "to your available balance."
            )
            notif.link = "/professional/dashboard"
            notif.created_at = get_ist_now()
            db.session.add(notif)

        ActivityLog.log_event(
            project_id=project.id,
            action="MILESTONE_APPROVED",
            description="Agency approved the milestone deliverable.",
            user_id=current_user.id,
            category="success",
        )
        db.session.commit()
        flash("Project deliverable approved. Funds unlocked and moved to specialist's available balance.", "success")
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error approving project {project_id}: {str(e)}")
        flash("An error occurred while approving the project deliverable.", "danger")
        
    return redirect(url_for("admin.admin_project_review"))


# =========================================================
# MANUAL OFFLINE PAYMENT SETTLEMENT & MANAGEMENT
# =========================================================

@admin_bp.route("/admin/project/<int:project_id>/mark-paid", methods=["POST"])
@login_required
def admin_mark_paid(project_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        project = db.session.get(Project, project_id)
        if not project:
            flash("Project not found.", "danger")
            return redirect(url_for("admin.admin_dashboard"))

        currency = sanitize_currency(project.currency)
        amount = get_package_price(project.package, currency, PACKAGE_PRICES)

        payment = Payment.query.filter_by(project_id=project.id, addon_id=None).first()
        if not payment:
            payment = Payment()
            payment.project_id = project.id
            payment.customer_id = project.customer_id
            payment.razorpay_order_id = f"offline_order_{project.id}"
            payment.razorpay_payment_id = f"offline_wire_{project.id}_{secrets.token_hex(2).upper()}"
            payment.amount = amount
            payment.currency = currency
            payment.status = "Paid"
            payment.paid_at = get_ist_now()
            db.session.add(payment)
        else:
            payment.amount = amount
            payment.currency = currency
            payment.status = "Paid"
            payment.paid_at = get_ist_now()

        project.status = "Pending Assignment"
        ActivityLog.log_event(
            project_id=project.id,
            action="PAYMENT_RECEIVED",
            description=f"Milestone payment of {format_currency(amount, currency)} recorded via offline wire.",
            user_id=current_user.id,
            category="success",
        )

        talent_share, agency_share, tax_amount = compute_financial_split(amount, currency)
        invoice = Invoice.query.filter_by(project_id=project.id).first()
        if not invoice:
            new_inv = Invoice()
            new_inv.invoice_number = f"INV-2026-{project.id:04d}"
            new_inv.project_id = project.id
            new_inv.customer_id = project.customer_id
            new_inv.gross_amount = amount
            new_inv.talent_payout = talent_share
            new_inv.agency_profit = agency_share
            new_inv.tax_amount = tax_amount
            new_inv.currency = currency
            new_inv.status = "Paid"
            new_inv.payout_status = "Pending Approval"
            db.session.add(new_inv)
        else:
            invoice.status = "Paid"
            invoice.currency = currency
            if not invoice.talent_payout:
                invoice.talent_payout = talent_share
            if not invoice.agency_profit:
                invoice.agency_profit = agency_share

        db.session.commit()
        flash(f"Project #{project.id} marked as Paid via Offline Bank Settlement.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error marking project paid {project_id}: {str(e)}")
        flash("An error occurred while marking the project as paid.", "danger")
        
    return redirect(request.referrer or url_for("admin.admin_dashboard"))


@admin_bp.route("/admin/project/<int:project_id>/add-on", methods=["POST"])
@login_required
def create_project_addon(project_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("customer.dashboard"))

    if project.status not in {"Assigned", "In Progress", "Submitted", "Revision"}:
        flash("Paid scope add-ons can only be issued for active projects.", "warning")
        return redirect(url_for("customer.project_details", project_id=project.id))

    title = request.form.get("title", "").strip()
    raw_amount = request.form.get("amount", "").strip()
    try:
        amount = Decimal(raw_amount)
    except (InvalidOperation, ValueError):
        amount = Decimal("0")

    if (
        not title
        or len(title) > 200
        or not amount.is_finite()
        or amount <= 0
        or amount != amount.quantize(Decimal("0.01"))
        or amount > Decimal("999999999")
    ):
        flash("Enter a valid title and positive amount with at most two decimal places.", "warning")
        return redirect(url_for("customer.project_details", project_id=project.id))

    try:
        currency = sanitize_currency(project.currency)
        gross_amount = amount.quantize(Decimal("0.01"))
        _, agency_profit, tax_amount = compute_financial_split(gross_amount, currency)

        invoice = Invoice()
        invoice.invoice_number = f"ADDON-{project.id}-{secrets.token_hex(4).upper()}"
        invoice.project_id = project.id
        invoice.customer_id = project.customer_id
        invoice.gross_amount = gross_amount
        invoice.talent_payout = Decimal("0.00")
        invoice.agency_profit = agency_profit
        invoice.tax_amount = tax_amount
        invoice.currency = currency
        invoice.status = "Pending"
        invoice.payout_status = "Pending Approval"

        db.session.add(invoice)
        db.session.flush()

        addon = ProjectAddOn()
        addon.project_id = project.id
        addon.invoice_id = invoice.id
        addon.title = title
        addon.amount = gross_amount
        addon.status = "Pending Payment"

        db.session.add(addon)
        db.session.commit()
        flash(f"Paid scope add-on '{title}' issued to client.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Failed to create scope add-on for project #%s: %s", project.id, str(e))
        flash("Could not issue the scope add-on. Please try again.", "danger")

    return redirect(url_for("customer.project_details", project_id=project.id))


@admin_bp.route("/admin/project/<int:project_id>/unassign", methods=["POST"])
@login_required
def unassign_professional(project_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        project = db.session.get(Project, project_id)
        if not project:
            flash("Project not found.", "danger")
            return redirect(url_for("admin.admin_dashboard"))

        assigned_apps = ProfessionalProjectApplication.query.filter_by(
            project_id=project.id, status="Assigned"
        ).all()
        for app_item in assigned_apps:
            app_item.status = "Applied"

        project.status = "Pending Assignment"
        project.assigned_professional_id = None
        db.session.commit()

        flash(f"Specialist removed from Project #{project.id}. Reset to Pending Assignment.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error unassigning professional from project {project_id}: {str(e)}")
        flash("An error occurred while unassigning the specialist.", "danger")
        
    return redirect(request.referrer or url_for("admin.admin_dashboard"))


@admin_bp.route("/admin/project/<int:project_id>/delete", methods=["POST"])
@login_required
def delete_project(project_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("admin.admin_dashboard"))

    try:
        db.session.execute(db.delete(ChatMessage).where(ChatMessage.project_id == project.id))
        db.session.execute(db.delete(ActivityLog).where(ActivityLog.project_id == project.id))
        db.session.execute(db.delete(Invoice).where(Invoice.project_id == project.id))
        db.session.execute(db.delete(Review).where(Review.project_id == project.id))
        db.session.execute(db.delete(ProjectAddOn).where(ProjectAddOn.project_id == project.id))
        db.session.execute(db.delete(RazorpaySubscription).where(RazorpaySubscription.project_id == project.id))
        db.session.execute(db.delete(ProfessionalProjectApplication).where(ProfessionalProjectApplication.project_id == project.id))
        db.session.execute(db.delete(ProjectDelivery).where(ProjectDelivery.project_id == project.id))
        db.session.execute(db.delete(Payment).where(Payment.project_id == project.id))
        db.session.execute(db.delete(Milestone).where(Milestone.project_id == project.id))

        db.session.delete(project)
        db.session.commit()
        flash(f"Project #{project_id} permanently deleted.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Failed to delete project #%s", project_id)
        flash(f"Could not delete project: {str(e)}", "danger")

    return redirect(request.referrer or url_for("admin.admin_dashboard"))


# =========================================================
# TRANSACTIONS, UPI/BANK WITHDRAWALS & EXPORTS
# =========================================================

@admin_bp.route("/admin/transactions")
@login_required
def admin_transactions() -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    page = request.args.get("page", 1, type=int)
    per_page = 15
    status_filter = request.args.get("status", "all")

    all_payments = Payment.query.all()
    total_collected: dict[str, Decimal] = {}
    pending_amount: dict[str, Decimal] = {}

    for payment in all_payments:
        currency = sanitize_currency(payment.currency)
        status = getattr(payment, "status", "").lower()
        if status == "paid":
            total_collected[currency] = total_collected.get(currency, Decimal("0")) + Decimal(str(payment.amount or 0))
        elif status in {"created", "pending"}:
            pending_amount[currency] = pending_amount.get(currency, Decimal("0")) + Decimal(str(payment.amount or 0))

    query = Payment.query
    if status_filter != "all":
        query = query.filter(Payment.status.ilike(status_filter))

    pagination = query.order_by(Payment.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    payments = pagination.items

    withdrawal_requests = WithdrawalRequest.query.order_by(WithdrawalRequest.requested_at.desc()).all()

    return render_template(
        "admin_transactions.html",
        payments=payments,
        pagination=pagination,
        current_status=status_filter,
        total_collected=total_collected,
        pending_amount=pending_amount,
        withdrawal_requests=withdrawal_requests,
    )


@admin_bp.route("/admin/withdrawals/<int:request_id>/approve", methods=["POST"])
@login_required
def approve_withdrawal(request_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        req = db.session.get(WithdrawalRequest, request_id)
        if not req:
            flash("Withdrawal request not found.", "danger")
            return redirect(url_for("admin.admin_transactions"))

        if req.status != "Pending":
            flash("This withdrawal request has already been processed.", "warning")
            return redirect(url_for("admin.admin_transactions"))

        from app.services.payouts import disburse_payout
        success, utr_reference, message = disburse_payout(req)

        if not success:
            flash(f"Payout transfer failed: {message}", "danger")
            return redirect(url_for("admin.admin_transactions"))

        req.status = "Processed"
        req.utr_reference = utr_reference
        req.processed_at = get_ist_now()

        notif = Notification()
        notif.user_id = req.user_id
        notif.title = f"Payout Disbursed! ₹{req.amount} Sent 🚀"
        destination = req.upi_id if req.payout_mode == "UPI" else f"Bank A/C ending {str(req.bank_account_number or '')[-4:]}"
        notif.message = f"Your withdrawal of ₹{req.amount} has been successfully transferred via RazorpayX ({req.payout_mode}) to {destination}. UTR: {utr_reference}."
        notif.link = "/professional/dashboard"
        notif.created_at = get_ist_now()
        db.session.add(notif)

        db.session.commit()
        flash(f"Withdrawal #{req.id} for ₹{req.amount} successfully disbursed via RazorpayX! UTR: {utr_reference}", "success")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error approving withdrawal {request_id}: {str(e)}")
        flash("An error occurred while approving the withdrawal.", "danger")
        
    return redirect(url_for("admin.admin_transactions"))


@admin_bp.route("/admin/withdrawals/<int:request_id>/reject", methods=["POST"])
@login_required
def reject_withdrawal(request_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        req = db.session.get(WithdrawalRequest, request_id)
        if not req:
            flash("Withdrawal request not found.", "danger")
            return redirect(url_for("admin.admin_transactions"))

        if req.status != "Pending":
            flash("This withdrawal request is not pending.", "warning")
            return redirect(url_for("admin.admin_transactions"))

        reason = request.form.get("reason", "Details mismatch").strip()
        req.status = "Rejected"
        req.admin_notes = reason
        req.processed_at = get_ist_now()

        profile = ProfessionalProfile.query.filter_by(user_id=req.user_id).first()
        if profile:
            profile.available_balance = Decimal(str(profile.available_balance or 0)) + req.amount

        tx = WalletTransaction()
        tx.user_id = req.user_id
        tx.amount = req.amount
        tx.currency = "INR"
        tx.transaction_type = "WITHDRAWAL_REVERSED"
        tx.reference_type = "WITHDRAWAL"
        tx.reference_id = req.id
        tx.description = f"Withdrawal request #{req.id} rejected: {reason}. ₹{req.amount} refunded to available balance."
        tx.created_at = get_ist_now()
        db.session.add(tx)

        notif = Notification()
        notif.user_id = req.user_id
        notif.title = "Withdrawal Request Rejected"
        notif.message = f"Your request for ₹{req.amount} was rejected ({reason}). Funds have been credited back to your available balance."
        notif.link = "/professional/dashboard"
        notif.created_at = get_ist_now()
        db.session.add(notif)

        db.session.commit()
        flash(f"Withdrawal request #{req.id} rejected and ₹{req.amount} credited back to specialist balance.", "info")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error rejecting withdrawal {request_id}: {str(e)}")
        flash("An error occurred while rejecting the withdrawal.", "danger")
        
    return redirect(url_for("admin.admin_transactions"))


@admin_bp.route("/admin/invoice/<int:invoice_id>/release-payout", methods=["POST"])
@login_required
def release_talent_payout(invoice_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        invoice = db.session.get(Invoice, invoice_id)
        if not invoice:
            flash("Invoice not found.", "danger")
            return redirect(url_for("admin.admin_transactions"))

        if invoice.status.lower() != "paid":
            flash("Cannot release payout for an unpaid milestone.", "warning")
            return redirect(url_for("admin.admin_transactions"))

        if invoice.payout_status == "Settled":
            flash("This specialist payout has already been settled.", "info")
            return redirect(url_for("admin.admin_transactions"))

        assigned_application = ProfessionalProjectApplication.query.filter_by(
            project_id=invoice.project_id,
            status="Assigned",
        ).first()
        if not assigned_application:
            flash("Cannot release payout until a specialist is assigned to the project.", "warning")
            return redirect(url_for("admin.admin_transactions"))

        utr_number = request.form.get("utr_number", "").strip()
        if len(utr_number) > 100:
            flash("The UTR reference must be 100 characters or fewer.", "danger")
            return redirect(url_for("admin.admin_transactions"))

        invoice.payout_status = "Settled"
        invoice.payout_utr = utr_number or "WIRE_SETTLED"
        invoice.payout_paid_at = get_ist_now()

        if invoice.project_id:
            ActivityLog.log_event(
                project_id=invoice.project_id,
                action="PAYOUT_SETTLED",
                description=f"Specialist payout settled. UTR: {invoice.payout_utr}",
                user_id=current_user.id,
                category="success",
            )

        db.session.commit()
        flash(f"Talent payout settled. UTR logged: {invoice.payout_utr}", "success")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error releasing talent payout {invoice_id}: {str(e)}")
        flash("An error occurred while releasing the talent payout.", "danger")
        
    return redirect(url_for("admin.admin_transactions"))


@admin_bp.route("/admin/notifications/broadcast", methods=["POST"])
@login_required
def broadcast_notification() -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        target_role = request.form.get("target_role", "all")
        title = request.form.get("title", "").strip()
        message = request.form.get("message", "").strip()
        link = request.form.get("link", "/dashboard").strip()

        if not title or not message:
            flash("Title and message are required for broadcasts.", "danger")
            return redirect(request.referrer or url_for("admin.admin_dashboard"))

        recipients = (
            User.query.filter(User.role != "admin").all()
            if target_role == "all"
            else User.query.filter_by(role=target_role).all()
        )

        for u in recipients:
            notif = Notification()
            notif.user_id = u.id
            notif.title = title
            notif.message = message
            notif.link = link
            db.session.add(notif)

        db.session.commit()
        flash(f"Broadcast sent successfully to {len(recipients)} users.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error broadcasting notification: {str(e)}")
        flash("An error occurred while broadcasting the notification.", "danger")
        
    return redirect(request.referrer or url_for("admin.admin_dashboard"))


@admin_bp.route("/admin/reports/export/<string:report_type>")
@login_required
def export_reports(report_type: str) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    si = StringIO()
    si.write("\ufeff")
    writer = csv.writer(si)

    try:
        if report_type == "revenue":
            writer.writerow(["Invoice #", "Payment Status", "Gateway Payment ID", "Date", "Currency", "Gross", "Taxable Value", "Tax Amount"])
            payments = Payment.query.order_by(Payment.created_at.desc()).all()
            for p in payments:
                currency = sanitize_currency(p.currency)
                gross = Decimal(str(p.amount or 0))
                _, _, tax_val = compute_financial_split(gross, currency)
                base_val = gross - tax_val
                writer.writerow([
                    f"INV-{p.id:04d}",
                    p.status,
                    p.razorpay_payment_id or "—",
                    p.created_at.strftime("%Y-%m-%d") if p.created_at else "—",
                    currency,
                    gross,
                    base_val,
                    tax_val,
                ])
            filename = f"AgencyOS_Revenue_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        else:
            writer.writerow(["User ID", "Name", "Email", "Role", "Joined Date"])
            users = User.query.all()
            for u in users:
                writer.writerow([
                    u.id,
                    getattr(u, "name", getattr(u, "full_name", "User")),
                    u.email,
                    u.role,
                    u.created_at.strftime("%Y-%m-%d") if u.created_at else "—",
                ])
            filename = f"AgencyOS_Users_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            
    except Exception as e:
        logger.error(f"Error exporting report {report_type}: {str(e)}")
        flash("An error occurred while generating the report.", "danger")
        return redirect(request.referrer or url_for("admin.admin_dashboard"))

    output = Response(si.getvalue(), mimetype="text/csv; charset=utf-8")
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return output


@admin_bp.route("/admin/ai/generate-sow", methods=["POST"])
@login_required
def ai_generate_sow() -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        return jsonify({"error": "Admin access required."}), 403

    client_name = request.form.get("client_name", "Valued Client").strip()
    service_type = request.form.get("service_type", "Website Design & Development").strip()
    notes = request.form.get("notes", "").strip()

    if not notes:
        return jsonify({"error": "Please provide project requirements or notes."}), 400

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        fallback_sow = (
            f"### Scope of Work: {service_type}\n"
            f"**Client Entity:** {client_name}\n"
            f"**Brief:** {notes}\n\n"
            f"#### Milestone 1: Strategic Architecture & Wireframes (50% Advance)\n"
            f"#### Milestone 2: Functional Staging & Payment Rails (25%)\n"
            f"#### Milestone 3: DNS Production Cutover & Handover (25%)\n"
        )
        return jsonify({"sow": fallback_sow})

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        prompt = f"Create a structured 3-milestone proposal (50/25/25 split) for {client_name} for {service_type}: {notes}"
        req_data = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as response:
            res_body = json.loads(response.read().decode("utf-8"))
            return jsonify({"sow": res_body["candidates"][0]["content"]["parts"][0]["text"]})
    except Exception as e:
        return jsonify({"error": f"AI service error: {str(e)}"}), 500

@admin_bp.route("/admin/disputes")
@login_required
def admin_disputes() -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    disputes = Dispute.query.order_by(Dispute.created_at.desc()).all()
    return render_template("admin_disputes.html", disputes=disputes)


@admin_bp.route("/admin/disputes/<int:dispute_id>/resolve", methods=["POST"])
@login_required
def resolve_dispute(dispute_id: int) -> Any:
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        dispute = db.session.get(Dispute, dispute_id)
        if not dispute:
            flash("Dispute not found.", "danger")
            return redirect(url_for("admin.admin_disputes"))

        verdict = request.form.get("verdict")
        notes = request.form.get("admin_notes", "").strip()

        dispute.status = verdict
        dispute.admin_verdict_notes = notes
        dispute.resolved_at = get_ist_now()

        project: Any = db.session.get(Project, dispute.project_id) if dispute.project_id else getattr(dispute, "project", None)
        assigned_app = ProfessionalProjectApplication.query.filter_by(project_id=project.id, status="Assigned").first() if project else None
        specialist_profile = ProfessionalProfile.query.filter_by(user_id=assigned_app.professional_id).first() if assigned_app else None

        currency = sanitize_currency(project.currency) if project else "INR"
        gross = get_package_price(project.package, currency, PACKAGE_PRICES) if project else Decimal("0.00")
        talent_share, _, _ = compute_financial_split(gross, currency)
        
        amount = Decimal(str(assigned_app.proposed_rate)) if (assigned_app and assigned_app.proposed_rate is not None) else talent_share

        if verdict == "RELEASED_SPECIALIST":
            if project:
                project.status = "Completed"
            if specialist_profile:
                specialist_profile.pending_balance = max(Decimal("0.00"), Decimal(str(specialist_profile.pending_balance or 0)) - amount)
                specialist_profile.available_balance = Decimal(str(specialist_profile.available_balance or 0)) + amount
            
            if assigned_app and project:
                db.session.add(Notification(
                    user_id=assigned_app.professional_id,
                    title="⚖️ Dispute Resolved in Your Favor",
                    message=f"Admin resolved dispute for Project #{project.id}. Full escrow of ₹{amount} released to your available balance.",
                    link="/professional/dashboard"
                ))
        elif verdict == "REFUNDED_CLIENT":
            if project:
                project.status = "Cancelled"
            if specialist_profile:
                specialist_profile.pending_balance = max(Decimal("0.00"), Decimal(str(specialist_profile.pending_balance or 0)) - amount)

            if project:
                db.session.add(Notification(
                    user_id=project.customer_id,
                    title="⚖️ Dispute Resolved — Refund Issued",
                    message=f"Admin resolved dispute for Project #{project.id}. Refund verdict executed.",
                    link=f"/project/{project.id}"
                ))
        elif verdict == "SPLIT":
            if project:
                project.status = "Completed"
            half_amount = (amount / Decimal("2")).quantize(Decimal("0.01"))
            if specialist_profile:
                specialist_profile.pending_balance = max(Decimal("0.00"), Decimal(str(specialist_profile.pending_balance or 0)) - amount)
                specialist_profile.available_balance = Decimal(str(specialist_profile.available_balance or 0)) + half_amount

        if project:
            ActivityLog.log_event(
                project_id=project.id,
                action="DISPUTE_RESOLVED",
                description=f"Admin resolved dispute with verdict '{verdict}': {notes}",
                user_id=current_user.id,
                category="success",
            )

        db.session.commit()
        flash(f"Dispute resolved with binding verdict: {verdict}", "success")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error resolving dispute {dispute_id}: {str(e)}")
        flash("An error occurred while resolving the dispute.", "danger")
        
    return redirect(url_for("admin.admin_disputes"))