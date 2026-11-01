# Save this directly into your file: app/blueprints/professional/__init__.py

from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app.extensions import db

# 1. IMPORT MODELS & REPOSITORY HELPERS
from app.models import (
    User, ProfessionalProfile, Project, Payment,
    ProfessionalProjectApplication, ProjectDelivery, ChatMessage, Invoice, Milestone,
    get_ist_now, PACKAGE_PRICES, calculate_professional_profile_completion, get_latest_project_delivery
)

professional_bp = Blueprint("professional", __name__)


# =========================================================
# SPECIALIST DASHBOARD (EARNINGS & SPRINTS HUB)
# =========================================================

@professional_bp.route("/professional/dashboard")
@login_required
def professional_dashboard():
    user_role = getattr(current_user, "role", "").lower()
    if user_role != "professional":
        if user_role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        return redirect(url_for("customer.dashboard"))

    profile = ProfessionalProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        profile = ProfessionalProfile(user_id=current_user.id)
        db.session.add(profile)
        db.session.commit()

    # 1. Assigned Projects (Active Workspaces)
    my_applications = ProfessionalProjectApplication.query.filter_by(professional_id=current_user.id).all()
    assigned_projects = [app.project for app in my_applications if app.status == "Assigned" and app.project]
    my_applied_ids = [app.project_id for app in my_applications]

    # 2. Available Projects (Open Sprints to Claim)
    available_projects = Project.query.filter_by(status="Pending Assignment").order_by(Project.created_at.desc()).all()

    # 3. Financial Ledger (45% specialist share)
    total_earned = sum(
        int(PACKAGE_PRICES.get(p.package, 48000) * 0.45) 
        for p in assigned_projects if p.status == "Completed"
    )
    pending_payout = sum(
        int(PACKAGE_PRICES.get(p.package, 48000) * 0.45) 
        for p in assigned_projects if p.status in ["In Progress", "Submitted", "Revision", "Assigned"]
    )

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
        my_applied_ids=my_applied_ids,
        total_earned=total_earned,
        pending_payout=pending_payout,
        completed_sprints=completed_sprints,
        active_sprints=active_sprints,
        completion_score=completion_score
    )


# =========================================================
# SPECIALIST MY PROJECTS VIEW
# =========================================================

@professional_bp.route("/professional/my-projects")
@login_required
def professional_my_projects():
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("professional", "admin"):
        return redirect(url_for("customer.dashboard"))

    my_applications = ProfessionalProjectApplication.query.filter_by(professional_id=current_user.id, status="Assigned").all()
    projects = [app.project for app in my_applications if app.project]

    return render_template("professional_my_projects.html", projects=projects)


# =========================================================
# OPEN SPRINTS MARKETPLACE
# =========================================================

@professional_bp.route("/professional/projects")
@login_required
def professional_projects():
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("professional", "admin"):
        return redirect(url_for("customer.dashboard"))

    available_projects = Project.query.filter_by(status="Pending Assignment").order_by(Project.created_at.desc()).all()
    my_applications = ProfessionalProjectApplication.query.filter_by(professional_id=current_user.id).all()
    my_applied_ids = [app.project_id for app in my_applications]

    return render_template(
        "professional_projects.html",
        projects=available_projects,
        my_applied_ids=my_applied_ids
    )


# =========================================================
# APPLY FOR AN OPEN SPRINT
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/apply", methods=["POST"])
@login_required
def apply_for_project(project_id):
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

    app = ProfessionalProjectApplication(
        project_id=project.id,
        professional_id=current_user.id,
        status="Applied",
        created_at=get_ist_now()
    )
    db.session.add(app)
    db.session.commit()

    flash(f"Application submitted for Project #{project.id}! The Agency Admin will review your profile.", "success")
    return redirect(url_for("professional.professional_dashboard"))


# =========================================================
# SPECIALIST DELIVERY WORKSPACE
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/workspace")
@login_required
def professional_project_workspace(project_id):
    project = Project.query.get_or_404(project_id)
    
    # Access check: Assigned specialist or Admin
    is_assigned = bool(ProfessionalProjectApplication.query.filter_by(
        project_id=project.id, professional_id=current_user.id, status="Assigned"
    ).first())

    user_role = getattr(current_user, "role", "").lower()
    if not is_assigned and user_role != "admin":
        flash("You are not assigned to this project workspace.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    deliveries = ProjectDelivery.query.filter_by(project_id=project.id).order_by(ProjectDelivery.submission_number.desc()).all()
    messages = ChatMessage.query.filter_by(project_id=project.id).order_by(ChatMessage.created_at.asc()).all()

    specialist_share = int(PACKAGE_PRICES.get(project.package, 48000) * 0.45)
    revision_count = ProjectDelivery.query.filter_by(project_id=project.id, status="Revision").count()

    return render_template(
        "professional_project_workspace.html",
        project=project,
        deliveries=deliveries,
        messages=messages,
        specialist_share=specialist_share,
        revision_count=revision_count
    )


# =========================================================
# SUBMIT MILESTONE DELIVERABLE
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/submit", methods=["POST"])
@login_required
def submit_deliverable(project_id):
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("professional", "admin"):
        flash("Specialist authorization required.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    project = Project.query.get_or_404(project_id)
    
    live_url = request.form.get("live_url", "").strip()
    preview_url = request.form.get("preview_url", "").strip()
    source_url = request.form.get("source_url", "").strip()
    notes = request.form.get("completion_notes", "").strip()

    if not notes:
        flash("Please provide completion notes explaining what was built.", "warning")
        return redirect(url_for("professional.professional_project_workspace", project_id=project.id))

    last_delivery = ProjectDelivery.query.filter_by(project_id=project.id).order_by(ProjectDelivery.submission_number.desc()).first()
    next_num = (last_delivery.submission_number + 1) if last_delivery else 1

    delivery = ProjectDelivery(
        project_id=project.id,
        professional_id=current_user.id,
        submission_number=next_num,
        live_url=live_url,
        preview_url=preview_url,
        source_url=source_url,
        completion_notes=notes,
        status="Submitted",
        submitted_at=get_ist_now()
    )
    db.session.add(delivery)

    # Move project to Submitted status for Admin review gate
    project.status = "Submitted"
    db.session.commit()

    flash(f"Milestone deliverable #{next_num} submitted! Advanced to Quality Review Gate.", "success")
    return redirect(url_for("professional.professional_dashboard"))


# =========================================================
# WORKSPACE IN-PROJECT CHAT
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/message", methods=["POST"])
@login_required
def send_workspace_message(project_id):
    text = request.form.get("message", "").strip()
    if not text:
        flash("Note cannot be empty.", "warning")
        return redirect(url_for("professional.professional_project_workspace", project_id=project_id))

    msg = ChatMessage(
        project_id=project_id,
        sender_id=current_user.id,
        message=text,
        created_at=get_ist_now()
    )
    db.session.add(msg)
    db.session.commit()

    flash("Note posted to project discussion thread!", "success")
    return redirect(url_for("professional.professional_project_workspace", project_id=project_id))


# =========================================================
# PAYOUT & PROFILE SETTINGS (UPI / BANK / PAN)
# =========================================================

@professional_bp.route("/professional/profile", methods=["GET", "POST"])
@login_required
def professional_profile():
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("professional", "admin"):
        flash("Specialist access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    profile = ProfessionalProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        profile = ProfessionalProfile(user_id=current_user.id)
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
        flash("UPI banking details & specialist credentials saved successfully!", "success")
        return redirect(url_for("professional.professional_profile"))

    return render_template("professional_profile.html", profile=profile)