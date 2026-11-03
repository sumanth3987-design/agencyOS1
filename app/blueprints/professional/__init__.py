"""AgencyOS Specialist (Professional) Talent Blueprint.

Governs specialist delivery workspaces, milestone submissions, 45% earnings
ledger tracking, and out-of-band UPI / wire payout redemption requests.
"""

from datetime import datetime
from decimal import Decimal
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import (
    ActivityLog,
    ChatMessage,
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
    calculate_professional_profile_completion,
    get_ist_now,
    get_latest_project_delivery,
)
from app.utils.currency import (
    INTERNATIONAL_PACKAGE_PRICES,
    compute_financial_split,
    get_package_price,
    sanitize_currency,
)
from app.utils.project_uploads import save_project_upload

professional_bp = Blueprint("professional", __name__)


# =========================================================
# SPECIALIST DASHBOARD (EARNINGS & SPRINTS HUB)
# =========================================================

@professional_bp.route("/professional/dashboard")
@login_required
def professional_dashboard() -> ResponseReturnValue:
    user_role = getattr(current_user, "role", "").lower()
    if user_role != "professional":
        if user_role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        return redirect(url_for("customer.dashboard"))

    profile = ProfessionalProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        profile = ProfessionalProfile()
        profile.user_id = current_user.id
        db.session.add(profile)
        db.session.commit()

    # 1. Assigned Projects (Active Workspaces)
    my_applications = ProfessionalProjectApplication.query.filter_by(professional_id=current_user.id).all()
    assigned_projects = [app.project for app in my_applications if app.status == "Assigned" and app.project]
    my_applied_ids = [app.project_id for app in my_applications]

    completed_project_ids = [project.id for project in assigned_projects if project.status == "Completed"]
    redeemable_invoices: list[Invoice] = []
    if completed_project_ids:
        redeemable_invoices = (
            Invoice.query.filter(
                Invoice.project_id.in_(completed_project_ids),
                Invoice.status.ilike("Paid"),
                Invoice.payout_status.in_(("Pending Approval", "Requested", "Processing", "Settled")),
            )
            .order_by(Invoice.created_at.desc())
            .all()
        )

    # 2. Available Projects (Open Sprints)
    # WIDENED NET: Fetch any project not assigned or completed
    available_projects = (
        Project.query.filter(Project.status.in_(["Pending Assignment", "Payment Pending", "Draft"]))
        .order_by(Project.created_at.desc())
        .all()
    )

    # 3. Financial Ledger (45% specialist share)
    total_earned: dict[str, Decimal] = {}
    pending_payout: dict[str, Decimal] = {}

    for project in assigned_projects:
        currency = sanitize_currency(project.currency)
        gross = get_package_price(project.package, currency, PACKAGE_PRICES)
        talent_payout, _, _ = compute_financial_split(gross, currency)

        if project.status == "Completed":
            total_earned[currency] = total_earned.get(currency, Decimal("0.00")) + talent_payout
        elif project.status in {"In Progress", "Submitted", "Revision", "Assigned"}:
            pending_payout[currency] = pending_payout.get(currency, Decimal("0.00")) + talent_payout

    completed_sprints = sum(1 for p in assigned_projects if p.status == "Completed")
    active_sprints = sum(1 for p in assigned_projects if p.status in ["In Progress", "Submitted", "Revision", "Assigned"])

    completion_score = 85
    try:
        completion_score = calculate_professional_profile_completion(profile)
    except Exception:
        completion_score = 85

    return render_template(
        "professional_dashboard.html",
        profile=profile,
        assigned_projects=assigned_projects,
        available_projects=available_projects,
        redeemable_invoices=redeemable_invoices,
        my_applied_ids=my_applied_ids,
        total_earned=total_earned,
        pending_payout=pending_payout,
        completed_sprints=completed_sprints,
        active_sprints=active_sprints,
        completion_score=completion_score,
        package_prices=PACKAGE_PRICES,
        international_package_prices=INTERNATIONAL_PACKAGE_PRICES,
    )


# =========================================================
# SPECIALIST MY PROJECTS VIEW
# =========================================================

@professional_bp.route("/professional/my-projects")
@login_required
def professional_my_projects() -> ResponseReturnValue:
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("professional", "admin"):
        return redirect(url_for("customer.dashboard"))

    my_applications = ProfessionalProjectApplication.query.filter_by(
        professional_id=current_user.id, status="Assigned"
    ).all()
    projects = [app.project for app in my_applications if app.project]

    return render_template("professional_my_projects.html", projects=projects)


# =========================================================
# OPEN SPRINTS MARKETPLACE
# =========================================================

@professional_bp.route("/professional/projects")
@login_required
def professional_projects() -> ResponseReturnValue:
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("professional", "admin"):
        return redirect(url_for("customer.dashboard"))

    # WIDENED NET: Fetch any project not assigned or completed
    available_projects = (
        Project.query.filter(Project.status.in_(["Pending Assignment", "Payment Pending", "Draft"]))
        .order_by(Project.created_at.desc())
        .all()
    )
    
    my_applications = ProfessionalProjectApplication.query.filter_by(
        professional_id=current_user.id
    ).all()
    my_applied_ids = [app.project_id for app in my_applications]

    return render_template(
        "professional_projects.html",
        projects=available_projects,
        my_applied_ids=my_applied_ids,
    )


# =========================================================
# APPLY FOR AN OPEN SPRINT
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/apply", methods=["POST"])
@login_required
def apply_for_project(project_id: int) -> ResponseReturnValue:
    user_role = getattr(current_user, "role", "").lower()
    if user_role != "professional":
        flash("Specialist access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(project_id)

    existing = ProfessionalProjectApplication.query.filter_by(
        project_id=project.id, professional_id=current_user.id
    ).first()

    if existing:
        flash(f"You have already applied for Sprint #{project.id}.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    app = ProfessionalProjectApplication()
    app.project_id = project.id
    app.professional_id = current_user.id
    app.status = "Applied"
    app.created_at = get_ist_now()

    db.session.add(app)
    db.session.commit()

    flash(f"Application registered for Project #{project.id}. Agency administrator will review.", "success")
    return redirect(url_for("professional.professional_dashboard"))


# =========================================================
# SPECIALIST DELIVERY WORKSPACE
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/workspace")
@login_required
def professional_project_workspace(project_id: int) -> ResponseReturnValue:
    project = Project.query.get_or_404(project_id)

    is_assigned = bool(
        ProfessionalProjectApplication.query.filter_by(
            project_id=project.id, professional_id=current_user.id, status="Assigned"
        ).first()
    )

    user_role = getattr(current_user, "role", "").lower()
    if not is_assigned and user_role != "admin":
        flash("You are not assigned to this project workspace.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    deliveries = (
        ProjectDelivery.query.filter_by(project_id=project.id)
        .order_by(ProjectDelivery.submission_number.desc())
        .all()
    )
    messages = (
        ChatMessage.query.filter_by(project_id=project.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    currency = sanitize_currency(project.currency)
    gross = get_package_price(project.package, currency, PACKAGE_PRICES)
    specialist_share, _, _ = compute_financial_split(gross, currency)
    revision_count = ProjectDelivery.query.filter_by(project_id=project.id, status="Revision").count()

    return render_template(
        "professional_project_workspace.html",
        project=project,
        deliveries=deliveries,
        messages=messages,
        specialist_share=specialist_share,
        currency=currency,
        revision_count=revision_count,
    )


# =========================================================
# SUBMIT MILESTONE DELIVERABLE
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/submit", methods=["POST"])
@login_required
def submit_deliverable(project_id: int) -> ResponseReturnValue:
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("professional", "admin"):
        flash("Specialist authorization required.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    project = Project.query.get_or_404(project_id)

    if user_role != "admin" and not ProfessionalProjectApplication.query.filter_by(
        project_id=project.id,
        professional_id=current_user.id,
        status="Assigned",
    ).first():
        flash("You are not assigned to this project.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    live_url = request.form.get("live_url", "").strip()
    preview_url = request.form.get("preview_url", "").strip()
    source_url = request.form.get("source_url", "").strip()
    notes = request.form.get("completion_notes", "").strip()

    if not notes:
        flash("Please provide completion notes explaining what was built.", "warning")
        return redirect(url_for("professional.professional_project_workspace", project_id=project.id))

    try:
        attachment_url = save_project_upload(
            request.files.get("deliverable_file"),
            {"zip", "pdf", "png", "jpg", "jpeg", "gif"},
        )
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("professional.professional_project_workspace", project_id=project.id))

    if attachment_url:
        notes += f"\n\nAttached Deliverable File: {attachment_url}"

    last_delivery = (
        ProjectDelivery.query.filter_by(project_id=project.id)
        .order_by(ProjectDelivery.submission_number.desc())
        .first()
    )
    next_num = (last_delivery.submission_number + 1) if last_delivery else 1

    delivery = ProjectDelivery()
    delivery.project_id = project.id
    delivery.professional_id = current_user.id
    delivery.submission_number = next_num
    delivery.live_url = live_url
    delivery.preview_url = preview_url
    delivery.source_url = source_url
    delivery.completion_notes = notes
    delivery.status = "Submitted"
    delivery.submitted_at = get_ist_now()

    db.session.add(delivery)

    project.status = "Submitted"
    summary = notes[:60] + ("..." if len(notes) > 60 else "")

    ActivityLog.log_event(
        project_id=project.id,
        action="DELIVERABLE_SUBMITTED",
        description=f"Specialist submitted deliverable #{next_num}: {summary}",
        user_id=current_user.id,
        category="accent",
    )
    if attachment_url:
        ActivityLog.log_event(
            project_id=project.id,
            action="DOCUMENT_UPLOADED",
            description="Specialist uploaded a deliverable file.",
            user_id=current_user.id,
            category="info",
        )
    db.session.commit()

    try:
        from app.services.whatsapp import notify_deliverable_ready
        client_phone = getattr(project.customer.customer_profile, "phone", None) if project.customer else None
        client_name = getattr(project.customer, "name", "Client") if project.customer else "Client"
        if client_phone:
            preview = preview_url or live_url or "https://yourdomain.com"
            notify_deliverable_ready(project.id, project.service, preview, client_phone, client_name)
    except Exception as e:
        flash(f"Deliverable submitted. (WhatsApp notification skipped: {str(e)})", "info")

    flash(f"Milestone deliverable #{next_num} submitted to the Review Gate.", "success")
    return redirect(url_for("professional.professional_dashboard"))


@professional_bp.route("/professional/invoice/<int:invoice_id>/redeem", methods=["POST"])
@login_required
def redeem_payout(invoice_id: int) -> ResponseReturnValue:
    if getattr(current_user, "role", "").lower() != "professional":
        flash("Specialist access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    invoice = Invoice.query.get_or_404(invoice_id)
    assigned_application = ProfessionalProjectApplication.query.filter_by(
        project_id=invoice.project_id,
        professional_id=current_user.id,
        status="Assigned",
    ).first()
    if not assigned_application:
        flash("You are not assigned to this project's payout allocation.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    project = Project.query.get_or_404(invoice.project_id)
    if project.status != "Completed" or invoice.status.lower() != "paid":
        flash("Payout redemption is available after the client approves a paid milestone.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    if invoice.payout_status == "Settled":
        flash("This payout has already been settled.", "info")
        return redirect(url_for("professional.professional_dashboard"))
    if invoice.payout_status in {"Requested", "Processing"}:
        flash("This payout redemption is already being processed.", "info")
        return redirect(url_for("professional.professional_dashboard"))
    if invoice.payout_status != "Pending Approval":
        flash("This payout is not available for redemption.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    admins = User.query.filter_by(role="admin").all()
    invoice.payout_status = "Requested"

    for admin in admins:
        notif = Notification()
        notif.user_id = admin.id
        notif.title = "Payout redemption requested"
        notif.message = (
            f"{current_user.name} requested payout for Project #{project.id} "
            f"({invoice.talent_payout} {invoice.currency})."
        )
        notif.link = url_for("admin.admin_transactions")
        db.session.add(notif)

    db.session.commit()
    flash("Payout redemption requested. Agency administrator notified.", "success")
    return redirect(url_for("professional.professional_dashboard"))


# =========================================================
# WORKSPACE IN-PROJECT CHAT
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/message", methods=["POST"])
@login_required
def send_workspace_message(project_id: int) -> ResponseReturnValue:
    text = request.form.get("message", "").strip()
    if not text:
        flash("Message cannot be empty.", "warning")
        return redirect(url_for("professional.professional_project_workspace", project_id=project_id))

    msg = ChatMessage()
    msg.project_id = project_id
    msg.sender_id = current_user.id
    msg.message = text
    msg.created_at = get_ist_now()

    db.session.add(msg)
    db.session.commit()

    flash("Note posted to project discussion.", "success")
    return redirect(url_for("professional.professional_project_workspace", project_id=project_id))


# =========================================================
# PAYOUT & PROFILE SETTINGS (UPI / BANK / PAN)
# =========================================================

@professional_bp.route("/professional/profile", methods=["GET", "POST"])
@login_required
def professional_profile() -> ResponseReturnValue:
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("professional", "admin"):
        flash("Specialist access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    profile = ProfessionalProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        profile = ProfessionalProfile()
        profile.user_id = current_user.id
        db.session.add(profile)
        db.session.commit()

    if request.method == "POST":
        profile.upi_id = request.form.get("upi_id", "").strip()
        profile.bank_account_number = request.form.get("bank_account_number", "").strip()
        profile.bank_ifsc = request.form.get("bank_ifsc", "").strip().upper()
        profile.pan_number = request.form.get("pan_number", "").strip().upper()
        profile.phone = request.form.get("phone", "").strip()
        profile.location = request.form.get("location", "").strip()
        profile.expertise = request.form.get("expertise", "Website Development").strip()
        profile.skills = request.form.get("skills", "").strip()
        profile.experience = request.form.get("experience", "").strip()
        profile.portfolio = request.form.get("portfolio", "").strip()
        profile.availability = request.form.get("availability", "Available (Full-Time)").strip()
        profile.profile_completed = True

        db.session.commit()
        flash("UPI banking details & specialist profile saved.", "success")
        return redirect(url_for("professional.professional_profile"))

    return render_template("professional_profile.html", profile=profile)