# Save this directly into your file: app/blueprints/admin/__init__.py

import csv
import json
import os
import re
import secrets
import urllib.request
from decimal import Decimal, InvalidOperation
from io import StringIO
from datetime import datetime
from urllib.parse import urlsplit
import ipaddress
from sqlalchemy.exc import IntegrityError

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, Response, current_app
from flask_login import login_required, current_user
from app.extensions import db, limiter
from app.models import (
    User, CustomerProfile, Business, ProfessionalProfile, Project, Payment,
    ProfessionalProjectApplication, ProjectDelivery, get_ist_now, PACKAGE_PRICES,
    calculate_professional_profile_completion, get_latest_project_delivery,
    Invoice, Notification, ProjectAddOn, AgencySetting, ActivityLog
)
from app.utils.currency import format_currency, get_package_price

admin_bp = Blueprint("admin", __name__)


def _validated_agency_settings(form):
    values = {
        "agency_name": form.get("agency_name", "").strip(),
        "primary_color": form.get("primary_color", "").strip(),
        "logo_url": form.get("logo_url", "").strip(),
        "whatsapp_support_number": form.get("whatsapp_support_number", "").strip(),
    }
    errors = {}

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
        errors["whatsapp_support_number"] = (
            "Enter 8–15 digits, with an optional leading +."
        )

    return values, errors


def _agency_settings_form_values(settings, submitted=None):
    defaults = {
        "agency_name": "AgencyOS",
        "primary_color": "#6366f1",
        "logo_url": "",
        "whatsapp_support_number": "+919876543210",
    }
    if settings:
        defaults.update({
            "agency_name": settings.agency_name,
            "primary_color": settings.primary_color,
            "logo_url": settings.logo_url or "",
            "whatsapp_support_number": settings.whatsapp_support_number,
        })
    if submitted is not None:
        defaults.update(submitted)
    return defaults


def _apply_agency_settings(settings, values):
    for key, value in values.items():
        setattr(settings, key, (value or None) if key == "logo_url" else value)


# =========================================================
# ADMIN EXECUTIVE DASHBOARD
# =========================================================

@admin_bp.route("/admin/dashboard")
@admin_bp.route("/admin")
@login_required
def admin_dashboard():
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
    pending_verification_count = ProfessionalProfile.query.filter_by(verification_status="Pending").count()
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
                Project.requirements.ilike(search_term)
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
        search_query=search_query
    )


@admin_bp.route("/admin/settings", methods=["GET", "POST"])
@login_required
def agency_settings():
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    settings = db.session.get(AgencySetting, 1)
    errors = {}
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
                settings = AgencySetting(id=1)
                db.session.add(settings)
            _apply_agency_settings(settings, form_values)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            settings = db.session.get(AgencySetting, 1)
            if settings is None:
                raise
            _apply_agency_settings(settings, form_values)
            db.session.commit()

        flash("Agency branding settings saved.", "success")
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
def admin_project_view(project_id):
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    # Direct redirect into the unified project workspace with Admin privileges
    return redirect(url_for("customer.project_details", project_id=project_id))


# =========================================================
# ADMIN CLIENT CRM
# =========================================================

@admin_bp.route("/admin/customers")
@login_required
def admin_customers():
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
        customer_projects=customer_projects
    )


# =========================================================
# SPECIALIST TALENT MANAGEMENT & VERIFICATION
# =========================================================

@admin_bp.route("/admin/professionals")
@login_required
def admin_professionals():
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    profiles = ProfessionalProfile.query.order_by(ProfessionalProfile.created_at.desc()).all()
    completion_map = {p.id: calculate_professional_profile_completion(p) for p in profiles}
    meritocracy_map = {p.id: p.get_performance_metrics() for p in profiles}

    return render_template(
        "admin_professionals.html",
        profiles=profiles,
        completion_map=completion_map,
        meritocracy_map=meritocracy_map,
    )


@admin_bp.route("/admin/professionals/<int:profile_id>/verify", methods=["POST"])
@login_required
def verify_professional(profile_id):
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    profile = ProfessionalProfile.query.get_or_404(profile_id)
    profile.verification_status = "Verified"
    profile.verification_notes = "Verified by admin."
    db.session.commit()

    flash("Specialist verified successfully.", "success")
    return redirect(url_for("admin.admin_professionals"))


@admin_bp.route("/admin/professionals/<int:profile_id>/reject", methods=["POST"])
@login_required
def reject_professional(profile_id):
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    profile = ProfessionalProfile.query.get_or_404(profile_id)
    profile.verification_status = "Rejected"
    db.session.commit()

    flash("Specialist onboarding rejected.", "warning")
    return redirect(url_for("admin.admin_professionals"))


# =========================================================
# TALENT APPLICATION ASSIGNMENTS
# =========================================================

@admin_bp.route("/admin/applications")
@login_required
def admin_applications():
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    applications = ProfessionalProjectApplication.query.order_by(
        ProfessionalProjectApplication.created_at.desc()
    ).all()

    return render_template("admin_applications.html", applications=applications)


@admin_bp.route("/admin/applications/<int:application_id>/assign", methods=["POST"])
@login_required
def assign_professional(application_id):
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    app = ProfessionalProjectApplication.query.get_or_404(application_id)
    project = app.project

    if project.status != "Pending Assignment" or app.status != "Applied":
        flash("This project or application is no longer eligible for assignment.", "warning")
        return redirect(url_for("admin.admin_applications"))

    app.status = "Assigned"
    project.status = "Assigned"

    other_apps = ProfessionalProjectApplication.query.filter(
        ProfessionalProjectApplication.project_id == project.id,
        ProfessionalProjectApplication.id != app.id,
        ProfessionalProjectApplication.status == "Applied"
    ).all()
    for o in other_apps:
        o.status = "Not Selected"

    db.session.commit()
    flash("Specialist successfully assigned to project sprint.", "success")
    return redirect(url_for("admin.admin_applications"))


# =========================================================
# PIPELINES & KANBAN
# =========================================================

@admin_bp.route("/admin/project-management")
@login_required
def admin_project_management():
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    projects = Project.query.order_by(Project.created_at.desc()).all()
    return render_template("admin_project_management.html", projects=projects)


@admin_bp.route(
    "/admin/project/<int:project_id>/send-reminder", methods=["POST"]
)
@login_required
def send_admin_reminder(project_id):
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(project_id)
    if project.status == "Payment Pending":
        from app.services.reminders import send_payment_reminder

        sent = send_payment_reminder(project.id, force=True)
        reminder_type = "payment"
    elif project.status == "Submitted":
        from app.services.reminders import send_review_reminder

        sent = send_review_reminder(project.id, force=True)
        reminder_type = "review"
    else:
        flash(
            "This project is not awaiting payment or client review.",
            "info",
        )
        return redirect(url_for("admin.admin_project_management"))

    if sent:
        flash(
            f"{reminder_type.title()} reminder sent for Project #{project.id}.",
            "success",
        )
    else:
        flash(
            "Reminder could not be sent. Check the client email, APP_BASE_URL, "
            "and email delivery configuration.",
            "warning",
        )
    return redirect(url_for("admin.admin_project_management"))


# =========================================================
# QUALITY REVIEW GATE
# =========================================================

@admin_bp.route("/admin/project-review")
@login_required
def admin_project_review():
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
        project_applications=project_applications
    )


@admin_bp.route("/admin/project/<int:project_id>/revision", methods=["POST"])
@login_required
def request_project_revision(project_id):
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(project_id)
    comment = request.form.get("revision_comment", "").strip()

    if not comment:
        flash("Please provide a revision comment.", "warning")
        return redirect(url_for("admin.admin_project_review"))

    latest_delivery = get_latest_project_delivery(project.id)
    if latest_delivery:
        latest_delivery.status = "Revision"
        latest_delivery.review_comment = comment
        latest_delivery.reviewed_at = get_ist_now()

    project.status = "Revision"
    project.revision_comment = comment
    ActivityLog.log_event(
        project.id,
        "REVISION_REQUESTED",
        f"Agency requested a revision: {comment}",
        current_user.id,
        "warning",
    )
    db.session.commit()

    flash("Revision requested successfully.", "warning")
    return redirect(url_for("admin.admin_project_review"))


@admin_bp.route("/admin/project/<int:project_id>/approve", methods=["POST"])
@login_required
def approve_project(project_id):
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(project_id)
    latest_delivery = get_latest_project_delivery(project.id)
    if latest_delivery:
        latest_delivery.status = "Approved"
        latest_delivery.reviewed_at = get_ist_now()

    project.status = "Completed"
    project.revision_comment = None
    ActivityLog.log_event(
        project.id,
        "MILESTONE_APPROVED",
        "Agency approved the milestone deliverable. Project marked Completed.",
        current_user.id,
        "success",
    )
    db.session.commit()

    flash("Project deliverable approved and marked as Completed.", "success")
    return redirect(url_for("admin.admin_project_review"))


# =========================================================
# MANUAL OFFLINE PAYMENT SETTLEMENT & MANAGEMENT
# =========================================================

@admin_bp.route("/admin/project/<int:project_id>/mark-paid", methods=["POST"])
@login_required
def admin_mark_paid(project_id):
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(project_id)
    amount = get_package_price(
        project.package,
        project.currency,
        PACKAGE_PRICES,
    )

    payment = Payment.query.filter_by(
        project_id=project.id,
        addon_id=None,
    ).first()
    if not payment:
        payment = Payment(
            project_id=project.id,
            customer_id=project.customer_id,
            razorpay_order_id=f"offline_order_{project.id}",
            razorpay_payment_id=f"offline_bank_transfer_{project.id}",
            amount=amount,
            currency=project.currency,
            status="Paid",
            paid_at=get_ist_now()
        )
        db.session.add(payment)
    else:
        payment.amount = amount
        payment.currency = project.currency
        payment.status = "Paid"
        payment.paid_at = get_ist_now()

    project.status = "Pending Assignment"
    ActivityLog.log_event(
        project.id,
        "PAYMENT_RECEIVED",
        f"Milestone payment of {format_currency(amount, project.currency)} recorded via offline bank settlement.",
        current_user.id,
        "success",
    )

    # Ensure invoice exists
    invoice = Invoice.query.filter_by(project_id=project.id).first()
    if not invoice:
        tax_amount = (
            amount - (amount / Decimal("1.18")).quantize(Decimal("0.01"))
        ).quantize(Decimal("0.01"))
        talent_payout = (amount * Decimal("0.45")).quantize(Decimal("0.01"))
        db.session.add(Invoice(
            invoice_number=f"INV-2026-{project.id:04d}",
            project_id=project.id,
            customer_id=project.customer_id,
            gross_amount=amount,
            talent_payout=talent_payout,
            agency_profit=amount - talent_payout,
            tax_amount=tax_amount,
            currency=project.currency,
            status="Paid",
            payout_status="Pending Approval"
        ))
    else:
        invoice.status = "Paid"
        invoice.currency = project.currency

    db.session.commit()
    flash(f"Project #{project.id} marked as Paid via Offline Bank Settlement.", "success")
    return redirect(request.referrer or url_for("admin.admin_dashboard"))


@admin_bp.route("/admin/project/<int:project_id>/add-on", methods=["POST"])
@login_required
def create_project_addon(project_id):
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(project_id)
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
        flash(
            "Enter a title (up to 200 characters) and a positive amount with at most two decimal places.",
            "warning",
        )
        return redirect(url_for("customer.project_details", project_id=project.id))

    gross_amount = amount.quantize(Decimal("0.01"))
    tax_amount = (
        gross_amount
        - (gross_amount / Decimal("1.18")).quantize(Decimal("0.01"))
    ).quantize(Decimal("0.01"))
    invoice = Invoice(
        invoice_number=f"ADDON-{project.id}-{secrets.token_hex(4).upper()}",
        project_id=project.id,
        customer_id=project.customer_id,
        gross_amount=gross_amount,
        talent_payout=0,
        agency_profit=gross_amount - tax_amount,
        tax_amount=tax_amount,
        currency=project.currency,
        status="Pending",
        payout_status="Pending Approval",
    )

    try:
        db.session.add(invoice)
        db.session.flush()
        addon = ProjectAddOn(
            project_id=project.id,
            invoice_id=invoice.id,
            title=title,
            amount=gross_amount,
            status="Pending Payment",
        )
        db.session.add(addon)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to create a scope add-on for project #%s", project.id
        )
        flash("Could not issue the paid scope add-on. Please try again.", "danger")
        return redirect(url_for("customer.project_details", project_id=project.id))

    flash(f"Paid scope add-on '{title}' issued to the client.", "success")
    return redirect(url_for("customer.project_details", project_id=project.id))


@admin_bp.route("/admin/project/<int:project_id>/unassign", methods=["POST"])
@login_required
def unassign_professional(project_id):
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(project_id)
    assigned_apps = ProfessionalProjectApplication.query.filter_by(
        project_id=project.id, status="Assigned"
    ).all()
    for app in assigned_apps:
        app.status = "Applied"

    project.status = "Pending Assignment"
    db.session.commit()

    flash(f"Specialist removed from Project #{project.id}. Reset to Pending Assignment.", "success")
    return redirect(request.referrer or url_for("admin.admin_dashboard"))


@admin_bp.route("/admin/project/<int:project_id>/delete", methods=["POST"])
@login_required
def delete_project(project_id):
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(project_id)

    ProfessionalProjectApplication.query.filter_by(project_id=project.id).delete()
    ProjectDelivery.query.filter_by(project_id=project.id).delete()
    Payment.query.filter_by(project_id=project.id).delete()

    db.session.delete(project)
    db.session.commit()

    flash(f"Project #{project_id} has been permanently deleted.", "success")
    return redirect(request.referrer or url_for("admin.admin_dashboard"))


# =========================================================
# TRANSACTIONS, EXPORTS & AI SOW
# =========================================================

@admin_bp.route("/admin/transactions")
@login_required
def admin_transactions():
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    page = request.args.get("page", 1, type=int)
    per_page = 15
    status_filter = request.args.get("status", "all")

    # Base query for metrics
    all_payments = Payment.query.all()
    total_collected = {}
    pending_amount = {}
    for payment in all_payments:
        currency = payment.currency or "INR"
        status = getattr(payment, "status", "").lower()
        if status == "paid":
            total_collected[currency] = (
                total_collected.get(currency, Decimal("0"))
                + Decimal(str(payment.amount or 0))
            )
        elif status in {"created", "pending"}:
            pending_amount[currency] = (
                pending_amount.get(currency, Decimal("0"))
                + Decimal(str(payment.amount or 0))
            )

    # Query with filters
    query = Payment.query

    if status_filter != "all":
        query = query.filter(Payment.status.ilike(status_filter))

    # Paginate and order by newest first
    pagination = query.order_by(Payment.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    payments = pagination.items

    return render_template(
        "admin_transactions.html",
        payments=payments,
        pagination=pagination,
        current_status=status_filter,
        total_collected=total_collected,
        pending_amount=pending_amount
    )


@admin_bp.route("/admin/invoice/<int:invoice_id>/release-payout", methods=["POST"])
@login_required
def release_talent_payout(invoice_id):
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    invoice = Invoice.query.get_or_404(invoice_id)
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
    invoice.payout_utr = utr_number or "UPI_INSTANT"
    invoice.payout_paid_at = get_ist_now()
    db.session.commit()

    flash(f"Talent payout marked as Settled! UTR logged: {invoice.payout_utr}", "success")
    return redirect(url_for("admin.admin_transactions"))


@admin_bp.route("/admin/notifications/broadcast", methods=["POST"])
@login_required
def broadcast_notification():
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

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
        db.session.add(Notification(
            user_id=u.id,
            title=title,
            message=message,
            link=link
        ))

    db.session.commit()
    flash(f"Broadcast sent successfully to {len(recipients)} recipient(s).", "success")
    return redirect(request.referrer or url_for("admin.admin_dashboard"))


@admin_bp.route("/admin/reports/export/<string:report_type>")
@login_required
def export_reports(report_type):
    if getattr(current_user, "role", "").lower() != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    si = StringIO()
    si.write("\ufeff")
    writer = csv.writer(si)

    if report_type == "revenue":
        writer.writerow(["Invoice #", "Payment Status", "Razorpay Payment ID", "Date", "Currency", "Gross", "Taxable Value", "Tax / Tax Estimate"])
        payments = Payment.query.order_by(Payment.created_at.desc()).all()
        for p in payments:
            gross = Decimal(str(p.amount or 0))
            base_val = (gross / Decimal("1.18")).quantize(Decimal("0.01")) if gross > 0 else Decimal("0")
            tax_val = gross - base_val
            writer.writerow([f"INV-{p.id:04d}", p.status, p.razorpay_payment_id or "—", p.created_at.strftime("%Y-%m-%d") if p.created_at else "—", p.currency or "INR", gross, base_val, tax_val])
        filename = f"AgencyOS_Revenue_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    else:
        writer.writerow(["User ID", "Name", "Email", "Role", "Joined Date"])
        users = User.query.all()
        for u in users:
            writer.writerow([u.id, getattr(u, "name", getattr(u, "full_name", "User")), u.email, u.role, u.created_at.strftime("%Y-%m-%d") if u.created_at else "—"])
        filename = f"AgencyOS_Users_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

    output = Response(si.getvalue(), mimetype="text/csv; charset=utf-8")
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return output


@admin_bp.route("/admin/ai/generate-sow", methods=["POST"])
@login_required
def ai_generate_sow():
    if getattr(current_user, "role", "").lower() != "admin":
        return jsonify({"error": "Admin access required"}), 403

    client_name = request.form.get("client_name", "Valued Client").strip()
    service_type = request.form.get("service_type", "Website Design & Development").strip()
    notes = request.form.get("notes", "").strip()

    if not notes:
        return jsonify({"error": "Please provide project requirements or notes."}), 400

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        fallback_sow = f"### Scope of Work: {service_type}\n**Client:** {client_name}\n**Brief:** {notes}\n\n#### Milestone 1: Strategic Discovery (50% Advance)\n#### Milestone 2: Production Development (25%)\n#### Milestone 3: Handover & Live Launch (25%)\n"
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