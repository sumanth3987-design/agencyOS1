"""AgencyOS Specialist (Professional) Talent Blueprint.

Governs specialist delivery workspaces, milestone submissions, 45% earnings
ledger tracking, skill-matched sprint applications, and out-of-band UPI / wire payout redemption requests.
"""

from datetime import datetime
from decimal import Decimal
import json
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
    WalletTransaction,
    WithdrawalRequest,
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

    # Sync verification flag if profile was marked verified by admin
    if profile.verification_status == "Verified" and not current_user.is_verified:
        current_user.is_verified = True
        current_user.verification_status = "Verified"
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
    # RULE: Only projects where Milestone 1 (50% deposit) has actually been PAID appear!
    paid_project_ids = [
        p.project_id for p in Payment.query.filter(Payment.status.ilike("Paid")).all()
    ]

    open_projects = (
        Project.query.filter(
            Project.id.in_(paid_project_ids),              # Must have confirmed deposit
            Project.assigned_professional_id.is_(None),    # Not yet assigned
            Project.status != "Completed",
        )
        .order_by(Project.created_at.desc())
        .all()
    )

    user_skills = set(profile.get_skills_list())
    matched_projects = []

    for proj in open_projects:
        proj_skills = set(proj.get_required_skills_list())
        overlap = user_skills.intersection(proj_skills) if (user_skills and proj_skills) else set()
        matched_projects.append({
            "project": proj,
            "overlap_skills": list(overlap),
            "match_count": len(overlap),
            "is_matched": bool(overlap) or not proj_skills,
        })

    # Sort matching projects to the top
    matched_projects.sort(key=lambda item: item["match_count"], reverse=True)
    available_projects = [item["project"] for item in matched_projects]

    # 3. Financial Ledger & Two-Stage Escrow Balances
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

    # Recent Withdrawal Requests
    withdrawal_history = (
        WithdrawalRequest.query.filter_by(user_id=current_user.id)
        .order_by(WithdrawalRequest.requested_at.desc())
        .limit(10)
        .all()
    )

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
        matched_projects=matched_projects,
        redeemable_invoices=redeemable_invoices,
        my_applied_ids=my_applied_ids,
        total_earned=total_earned,
        pending_payout=pending_payout,
        pending_balance=profile.pending_balance,
        available_balance=profile.available_balance,
        withdrawal_history=withdrawal_history,
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
# OPEN SPRINTS MARKETPLACE (WITH SKILL-MATCHING & 50% FUNDED CHECK)
# =========================================================

@professional_bp.route("/professional/projects")
@login_required
def professional_projects() -> ResponseReturnValue:
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("professional", "admin"):
        return redirect(url_for("customer.dashboard"))

    profile = ProfessionalProfile.query.filter_by(user_id=current_user.id).first()
    user_skills = set(profile.get_skills_list()) if profile else set()

    # RULE: Only projects where Milestone 1 (50% deposit) has actually been PAID appear!
    paid_project_ids = [
        p.project_id for p in Payment.query.filter(Payment.status.ilike("Paid")).all()
    ]

    open_projects = (
        Project.query.filter(
            Project.id.in_(paid_project_ids),              # Must have confirmed deposit
            Project.assigned_professional_id.is_(None),    # Not yet assigned
            Project.status != "Completed",
        )
        .order_by(Project.created_at.desc())
        .all()
    )
    
    my_applications = ProfessionalProjectApplication.query.filter_by(
        professional_id=current_user.id
    ).all()
    my_applied_ids = [app.project_id for app in my_applications]

    scored_projects = []
    for proj in open_projects:
        proj_skills = set(proj.get_required_skills_list())
        overlap = user_skills.intersection(proj_skills) if (user_skills and proj_skills) else set()
        scored_projects.append({
            "project": proj,
            "matched_skills": list(overlap),
            "match_count": len(overlap),
            "already_applied": proj.id in my_applied_ids,
        })

    # Sort matching projects to the top
    scored_projects.sort(key=lambda x: x["match_count"], reverse=True)

    return render_template(
        "professional_projects.html",
        projects=[item["project"] for item in scored_projects],
        scored_projects=scored_projects,
        my_applied_ids=my_applied_ids,
        profile=profile,
    )


# =========================================================
# APPLY FOR AN OPEN SPRINT (WITH PROPOSAL & RATE)
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/apply", methods=["POST"])
@login_required
def apply_for_project(project_id: int) -> ResponseReturnValue:
    user_role = getattr(current_user, "role", "").lower()
    if user_role != "professional":
        flash("Specialist access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    profile = ProfessionalProfile.query.filter_by(user_id=current_user.id).first()
    is_user_verified = bool(
        current_user.is_verified or (profile and profile.verification_status == "Verified")
    )

    # KYC Verification Gate
    if not is_user_verified:
        flash("Your specialist account is currently pending Admin KYC verification. Once verified by Admin, you can apply for sprints.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    project = Project.query.get_or_404(project_id)

    existing = ProfessionalProjectApplication.query.filter_by(
        project_id=project.id, professional_id=current_user.id
    ).first()

    if existing:
        flash(f"You have already applied for Sprint #{project.id}.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    proposal = request.form.get("proposal", "").strip()
    proposed_rate_str = request.form.get("proposed_rate", "").strip()
    estimated_days_str = request.form.get("estimated_days", "").strip()

    proposed_rate = None
    if proposed_rate_str:
        try:
            proposed_rate = Decimal(proposed_rate_str)
        except Exception:
            proposed_rate = None

    estimated_days = None
    if estimated_days_str and estimated_days_str.isdigit():
        estimated_days = int(estimated_days_str)

    app = ProfessionalProjectApplication()
    app.project_id = project.id
    app.professional_id = current_user.id
    app.proposal = proposal or "Interested in delivering this sprint with standard scope."
    app.proposed_rate = proposed_rate
    app.estimated_days = estimated_days
    app.status = "Applied"
    app.created_at = get_ist_now()

    db.session.add(app)

    # Notify Admins about the new application
    admins = User.query.filter_by(role="admin").all()
    for admin in admins:
        notif = Notification()
        notif.user_id = admin.id
        notif.title = f"New Specialist Application: Project #{project.id}"
        notif.message = f"{current_user.name} applied for '{project.package} - {project.service}'."
        notif.link = url_for("admin.admin_applications")
        db.session.add(notif)

    db.session.commit()

    flash(f"Application registered for Project #{project.id}. Agency administrator will review and assign.", "success")
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

    # Notify all agency admins that a deliverable/staging URL has been submitted
    admins = User.query.filter_by(role="admin").all()
    for admin in admins:
        admin_notif = Notification()
        admin_notif.user_id = admin.id
        admin_notif.title = f"Deliverable Submitted: Project #{project.id}"
        admin_notif.message = f"{current_user.name} submitted a staging URL for '{project.service}'. Review required."
        admin_notif.link = url_for("admin.admin_project_review")
        db.session.add(admin_notif)
        
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


# =========================================================
# ESCROW WALLET REDEMPTION & WITHDRAWAL (UPI / BANK TRANSFER)
# =========================================================

@professional_bp.route("/professional/wallet/withdraw", methods=["POST"])
@login_required
def withdraw_funds() -> ResponseReturnValue:
    user_role = getattr(current_user, "role", "").lower()
    if user_role != "professional":
        flash("Specialist access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    profile = ProfessionalProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        flash("Profile not found.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    try:
        amount = Decimal(request.form.get("amount", "0").strip())
    except Exception:
        flash("Please enter a valid numeric amount.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    if amount <= Decimal("0.00"):
        flash("Withdrawal amount must be greater than zero.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    current_avail = Decimal(str(profile.available_balance or 0))
    if amount > current_avail:
        flash(f"Requested amount (₹{amount}) exceeds available redeemable balance (₹{current_avail}).", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    payout_mode = request.form.get("payout_mode", "UPI").strip().upper()
    upi_id = request.form.get("upi_id", "").strip() or profile.upi_id
    bank_account = request.form.get("bank_account_number", "").strip() or profile.bank_account_number
    bank_ifsc = request.form.get("bank_ifsc", "").strip().upper() or profile.bank_ifsc
    beneficiary_name = request.form.get("bank_beneficiary_name", "").strip() or profile.bank_beneficiary_name or current_user.name

    if payout_mode == "UPI" and not upi_id:
        flash("Please provide a valid UPI ID (e.g. name@oksbi).", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    if payout_mode == "BANK_TRANSFER" and (not bank_account or not bank_ifsc):
        flash("Please provide complete bank account number and IFSC code.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    # 1. Deduct immediately from available balance to prevent double redemption
    profile.available_balance = current_avail - amount

    # 2. Save / update profile default banking info
    if upi_id:
        profile.upi_id = upi_id
    if bank_account:
        profile.bank_account_number = bank_account
    if bank_ifsc:
        profile.bank_ifsc = bank_ifsc
    if beneficiary_name:
        profile.bank_beneficiary_name = beneficiary_name

    # 3. Create Withdrawal Request
    req = WithdrawalRequest()
    req.user_id = current_user.id
    req.amount = amount
    req.currency = "INR"
    req.payout_mode = payout_mode
    req.upi_id = upi_id if payout_mode == "UPI" else None
    req.bank_account_number = bank_account if payout_mode == "BANK_TRANSFER" else None
    req.bank_ifsc = bank_ifsc if payout_mode == "BANK_TRANSFER" else None
    req.bank_beneficiary_name = beneficiary_name if payout_mode == "BANK_TRANSFER" else None
    req.status = "Pending"
    req.requested_at = get_ist_now()

    db.session.add(req)

    # 4. Log immutable wallet transaction
    tx = WalletTransaction()
    tx.user_id = current_user.id
    tx.amount = amount
    tx.currency = "INR"
    tx.transaction_type = "WITHDRAWAL_DEBIT"
    tx.reference_type = "WITHDRAWAL"
    tx.description = f"Requested redemption of ₹{amount} via {payout_mode}."
    tx.created_at = get_ist_now()
    db.session.add(tx)

    # 5. Notify all admins
    admins = User.query.filter_by(role="admin").all()
    for admin in admins:
        notif = Notification()
        notif.user_id = admin.id
        notif.title = f"New Payout Withdrawal Request: ₹{amount}"
        notif.message = f"{current_user.name} requested ₹{amount} redemption via {payout_mode}."
        notif.link = url_for("admin.admin_transactions")
        db.session.add(notif)

    db.session.commit()

    flash(f"Redemption request for ₹{amount} submitted. Admin will process transfer to your {payout_mode}.", "success")
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

# Clean endpoint alias for templates calling post_chat_message
professional_bp.add_url_rule(
    "/professional/project/<int:project_id>/message-alt",
    endpoint="post_chat_message",
    view_func=send_workspace_message,
    methods=["POST"]
)

# =========================================================
# PAYOUT & COMPREHENSIVE PROFILE SETTINGS
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
        # Professional Dossier & Bio
        profile.expertise = request.form.get("expertise", "").strip()
        profile.experience = request.form.get("experience", "").strip()
        profile.about = request.form.get("about", "").strip()
        profile.phone = request.form.get("phone", "").strip()
        profile.location = request.form.get("location", "").strip()
        
        # Tech Stack & Skills
        profile.skills = request.form.get("skills", "").strip()
        profile.technologies = request.form.get("technologies", "").strip()
        profile.skill_level = request.form.get("skill_level", "").strip()
        profile.project_types = request.form.get("project_types", "").strip()

        # Socials & Portfolios
        profile.portfolio = request.form.get("portfolio", "").strip()
        profile.github = request.form.get("github", "").strip()
        profile.linkedin = request.form.get("linkedin", "").strip()

        # Credentials & Work History
        profile.education = request.form.get("education", "").strip()
        profile.certifications = request.form.get("certifications", "").strip()
        profile.work_experience = request.form.get("work_experience", "").strip()
        profile.availability = request.form.get("availability", "Available (Full-Time)").strip()
        profile.current_workload = request.form.get("current_workload", "").strip()

        # Payout & Banking Coordinates
        profile.upi_id = request.form.get("upi_id", "").strip()
        profile.bank_account_number = request.form.get("bank_account_number", "").strip()
        profile.bank_ifsc = request.form.get("bank_ifsc", "").strip().upper()
        profile.bank_beneficiary_name = request.form.get("bank_beneficiary_name", "").strip()
        profile.pan_number = request.form.get("pan_number", "").strip().upper()

        profile.profile_completed = True
        db.session.commit()
        flash("Comprehensive specialist profile & banking coordinates saved successfully.", "success")
        return redirect(url_for("professional.professional_profile"))

    return render_template("professional_profile.html", profile=profile)
