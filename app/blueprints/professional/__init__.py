"""AgencyOS Specialist (Professional) Talent Blueprint.

Governs specialist delivery workspaces, milestone submissions, 45% earnings
ledger tracking, skill-matched sprint applications, retainer task claiming,
automated hours bank decrementing, and out-of-band UPI / wire payout redemption requests
with strict row-level database locking for concurrency safety.
"""

from datetime import datetime
from decimal import Decimal
import json
import logging
from typing import Any
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
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
    ProjectTimeLog,
    RazorpaySubscription,
    RetainerTask,
    User,
    WalletTransaction,
    WithdrawalRequest,
    calculate_professional_profile_completion,
    get_ist_now,
    get_latest_project_delivery,
    is_valid_http_url,
)
from app.services.whatsapp import notify_deliverable_ready
from app.utils.currency import (
    INTERNATIONAL_PACKAGE_PRICES,
    compute_financial_split,
    format_currency,
    get_package_price,
    sanitize_currency,
)
from app.utils.project_uploads import save_project_upload

professional_bp = Blueprint("professional", __name__)
logger = logging.getLogger(__name__)


def _get_talent_meritocracy_tier(completed_count: int) -> dict[str, Any]:
    """Calculate talent meritocracy tier and payout split percentage."""
    if completed_count >= 10:
        return {"level": 3, "name": "Gold Master Pro", "share_pct": 50, "color": "emerald"}
    if completed_count >= 3:
        return {"level": 2, "name": "Silver Verified Pro", "share_pct": 48, "color": "indigo"}
    return {"level": 1, "name": "Bronze Specialist", "share_pct": 45, "color": "zinc"}


# =========================================================
# SPECIALIST DASHBOARD (EARNINGS & SPRINTS HUB)
# =========================================================

@professional_bp.route("/professional/dashboard")
@login_required
def professional_dashboard() -> Any:
    user_role = getattr(current_user, "role", "").lower()
    if user_role != "professional":
        if user_role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        return redirect(url_for("customer.dashboard"))

    profile = ProfessionalProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        try:
            profile = ProfessionalProfile()
            profile.user_id = current_user.id
            db.session.add(profile)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            profile = ProfessionalProfile.query.filter_by(user_id=current_user.id).first()
        except Exception as e:
            db.session.rollback()
            logger.error("Error creating initial professional profile: %s", str(e))

    if profile and profile.verification_status == "Verified" and not current_user.is_verified:
        try:
            current_user.is_verified = True
            current_user.verification_status = "Verified"
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error("Error syncing verification status for user %s: %s", current_user.id, str(e))

    my_applications = ProfessionalProjectApplication.query.filter_by(professional_id=current_user.id).all()
    assigned_projects = [app.project for app in my_applications if app.status == "Assigned" and app.project]
    
    # Also include projects assigned directly via assigned_professional_id
    direct_assigned = Project.query.filter_by(assigned_professional_id=current_user.id).all()
    for p in direct_assigned:
        if p not in assigned_projects:
            assigned_projects.append(p)

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

    # Open sprints ready for talent applications
    open_projects = (
        Project.query.filter(
            Project.status.in_(["Pending Assignment", "Payment Pending"]),
            Project.assigned_professional_id.is_(None),
        )
        .order_by(Project.created_at.desc())
        .all()
    )

    user_skills = set(profile.get_skills_list()) if profile else set()
    matched_projects = []

    for proj in open_projects:
        proj_skills = set(proj.get_required_skills_list())
        overlap = user_skills.intersection(proj_skills) if (user_skills and proj_skills) else set()
        
        p_curr = sanitize_currency(proj.currency)
        p_gross = get_package_price(proj.package, p_curr, PACKAGE_PRICES)
        p_payout, _, _ = compute_financial_split(p_gross, p_curr)
        
        matched_projects.append({
            "project": proj,
            "overlap_skills": list(overlap),
            "match_count": len(overlap),
            "is_matched": bool(overlap) or not proj_skills,
            "talent_payout": p_payout,
            "currency": p_curr,
            "formatted_payout": format_currency(p_payout, p_curr),
        })

    matched_projects.sort(key=lambda item: item["match_count"], reverse=True)
    available_projects = [item["project"] for item in matched_projects]

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
    at_capacity = active_sprints >= 2
    meritocracy_tier = _get_talent_meritocracy_tier(completed_sprints)

    withdrawal_history = (
        WithdrawalRequest.query.filter_by(user_id=current_user.id)
        .order_by(WithdrawalRequest.requested_at.desc())
        .limit(10)
        .all()
    )

    completion_score = 85
    try:
        if profile:
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
        pending_balance=getattr(profile, "pending_balance", Decimal("0.00")),
        available_balance=getattr(profile, "available_balance", Decimal("0.00")),
        withdrawal_history=withdrawal_history,
        completed_sprints=completed_sprints,
        active_sprints=active_sprints,
        at_capacity=at_capacity,
        meritocracy_tier=meritocracy_tier,
        completion_score=completion_score,
        package_prices=PACKAGE_PRICES,
        international_package_prices=INTERNATIONAL_PACKAGE_PRICES,
    )


# =========================================================
# SPECIALIST MY PROJECTS VIEW
# =========================================================

@professional_bp.route("/professional/my-projects")
@login_required
def professional_my_projects() -> Any:
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("professional", "admin"):
        return redirect(url_for("customer.dashboard"))

    my_applications = ProfessionalProjectApplication.query.filter_by(
        professional_id=current_user.id, status="Assigned"
    ).all()
    projects = [app.project for app in my_applications if app.project]

    direct_assigned = Project.query.filter_by(assigned_professional_id=current_user.id).all()
    for p in direct_assigned:
        if p not in projects:
            projects.append(p)

    return render_template("professional_my_projects.html", projects=projects)


# =========================================================
# OPEN SPRINTS MARKETPLACE (CAPACITY & TIER ENFORCED)
# =========================================================

@professional_bp.route("/professional/projects")
@login_required
def professional_projects() -> Any:
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("professional", "admin"):
        return redirect(url_for("customer.dashboard"))

    profile = ProfessionalProfile.query.filter_by(user_id=current_user.id).first()
    
    has_assignment = bool(
        ProfessionalProjectApplication.query.filter_by(
            professional_id=current_user.id, status="Assigned"
        ).first() or Project.query.filter_by(assigned_professional_id=current_user.id).first()
    )
    
    if user_role != "admin" and not has_assignment:
        if profile and profile.verification_status == "Pending" and not profile.expertise and not profile.experience:
            flash("Your specialist account is currently pending Admin KYC verification.", "warning")
            return redirect(url_for("professional.professional_dashboard"))

    user_skills = set(profile.get_skills_list()) if profile else set()

    open_projects = (
        Project.query.filter(
            Project.status.in_(["Pending Assignment", "Payment Pending"]),
            Project.assigned_professional_id.is_(None),
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

    scored_projects.sort(key=lambda x: x["match_count"], reverse=True)

    active_sprints_count = Project.query.filter(
        Project.assigned_professional_id == current_user.id,
        Project.status.in_(["Assigned", "In Progress", "Submitted", "Revision"]),
    ).count()

    completed_sprints_count = Project.query.filter(
        Project.assigned_professional_id == current_user.id,
        Project.status == "Completed",
    ).count()

    at_capacity = active_sprints_count >= 2
    meritocracy_tier = _get_talent_meritocracy_tier(completed_sprints_count)

    return render_template(
        "professional_projects.html",
        projects=[item["project"] for item in scored_projects],
        scored_projects=scored_projects,
        my_applied_ids=my_applied_ids,
        profile=profile,
        active_sprints_count=active_sprints_count,
        at_capacity=at_capacity,
        meritocracy_tier=meritocracy_tier,
    )


# =========================================================
# APPLY FOR AN OPEN SPRINT (2-SPRINT CAPACITY CHECK)
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/apply", methods=["POST"])
@professional_bp.route("/professional/projects/<int:project_id>/apply", methods=["POST"])
@login_required
def apply_for_project(project_id: int) -> Any:
    user_role = getattr(current_user, "role", "").lower()
    if user_role != "professional":
        flash("Specialist access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    profile = ProfessionalProfile.query.filter_by(user_id=current_user.id).first()
    is_user_verified = bool(
        getattr(current_user, "is_verified", False)
        or (profile and getattr(profile, "verification_status", "") == "Verified")
    )

    if not is_user_verified:
        flash("Your specialist account is currently pending Admin KYC verification.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    active_sprints_count = Project.query.filter(
        Project.assigned_professional_id == current_user.id,
        Project.status.in_(["Assigned", "In Progress", "Submitted", "Revision"]),
    ).count()

    if active_sprints_count >= 2:
        flash(
            "Capacity Reached: You currently have 2 active sprints in progress. "
            "Please deliver and complete your active projects before bidding on new sprints.",
            "warning",
        )
        return redirect(url_for("professional.professional_projects"))

    # ROW-LEVEL LOCK: Ensure project isn't simultaneously applied/assigned
    project = Project.query.filter_by(id=project_id).with_for_update().first()
    if not project:
        db.session.rollback()
        flash("Project not found.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    if project.status != "Pending Assignment":
        db.session.rollback()
        flash("You can only apply for projects that are pending assignment.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    if profile and profile.expertise and profile.expertise.lower() != "general":
        exp_lower = profile.expertise.lower()
        srv_lower = (project.service or "").lower()
        user_skills = set(profile.get_skills_list())
        req_skills = set(project.get_required_skills_list())
        
        if exp_lower not in srv_lower and srv_lower not in exp_lower:
            if not (user_skills and req_skills and user_skills.intersection(req_skills)):
                db.session.rollback()
                flash("Your expertise does not match the requirements for this project.", "warning")
                return redirect(url_for("professional.professional_dashboard"))

    existing = ProfessionalProjectApplication.query.filter_by(
        project_id=project.id, professional_id=current_user.id
    ).first()

    if existing:
        db.session.rollback()
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

    try:
        app = ProfessionalProjectApplication()
        app.project_id = project.id
        app.professional_id = current_user.id
        app.proposal = proposal or "Interested in delivering this sprint with standard scope."
        app.proposed_rate = proposed_rate
        app.estimated_days = estimated_days
        app.status = "Applied"
        app.created_at = get_ist_now()

        db.session.add(app)

        admins = User.query.filter_by(role="admin").all()
        for admin in admins:
            notif = Notification()
            notif.user_id = admin.id
            notif.title = f"New Specialist Application: Project #{project.id}"
            notif.message = f"{current_user.name} applied for '{project.package} - {project.service}'."
            notif.link = url_for("admin.admin_applications")
            db.session.add(notif)

        db.session.commit()
        flash(f"Application registered for Project #{project.id}.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error("Error submitting application for project %s: %s", project_id, str(e), exc_info=True)
        flash("Could not submit your application. Please try again.", "danger")

    return redirect(url_for("professional.professional_dashboard"))


# =========================================================
# START SPRINT WORKSPACE
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/start", methods=["POST"])
@professional_bp.route("/professional/projects/<int:project_id>/start", methods=["POST"])
@login_required
def start_project_work(project_id: int) -> Any:
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("professional", "admin"):
        flash("Specialist authorization required.", "danger")
        return redirect(url_for("customer.dashboard"))

    # ROW-LEVEL LOCK: Prevent simultaneous start clicks
    project = Project.query.filter_by(id=project_id).with_for_update().first()
    if not project:
        db.session.rollback()
        flash("Project not found.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    is_assigned = bool(
        project.assigned_professional_id == current_user.id
        or ProfessionalProjectApplication.query.filter_by(
            project_id=project.id,
            professional_id=current_user.id,
            status="Assigned",
        ).first()
    )

    if not is_assigned and user_role != "admin":
        db.session.rollback()
        flash("You are not assigned to this project.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    if project.status != "Assigned":
        db.session.rollback()
        flash("Project must be in Assigned status to start.", "warning")
        return redirect(url_for("professional.professional_project_workspace", project_id=project.id))

    try:
        project.status = "In Progress"
        ActivityLog.log_event(
            project_id=project.id,
            action="PROJECT_STARTED",
            description=f"Specialist {current_user.name} commenced active sprint development.",
            user_id=current_user.id,
            category="info",
        )
        db.session.commit()
        flash("Project marked as In Progress. Workspace is active.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error starting project {project_id}: {str(e)}")
        flash("Could not transition project to In Progress.", "danger")

    return redirect(url_for("professional.professional_project_workspace", project_id=project.id))


# =========================================================
# SPECIALIST DELIVERY WORKSPACE (WITH RETAINER TASKS)
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/workspace")
@professional_bp.route("/professional/projects/<int:project_id>/workspace")
@login_required
def professional_project_workspace(project_id: int) -> Any:
    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    is_assigned = bool(
        project.assigned_professional_id == current_user.id
        or ProfessionalProjectApplication.query.filter_by(
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

    retainer_tasks = (
        RetainerTask.query.filter_by(project_id=project.id)
        .order_by(RetainerTask.created_at.desc())
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
        retainer_tasks=retainer_tasks,
        specialist_share=specialist_share,
        currency=currency,
        revision_count=revision_count,
    )


# =========================================================
# CLAIM RETAINER TASK
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/task/<int:task_id>/claim", methods=["POST"])
@login_required
def claim_retainer_task(project_id: int, task_id: int) -> Any:
    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    is_assigned = bool(
        project.assigned_professional_id == current_user.id
        or ProfessionalProjectApplication.query.filter_by(
            project_id=project.id, professional_id=current_user.id, status="Assigned"
        ).first()
    )

    if not is_assigned and getattr(current_user, "role", "").lower() != "admin":
        flash("Unauthorized action.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    # ROW-LEVEL LOCK: Prevent double-claiming of tasks
    task = RetainerTask.query.filter_by(id=task_id, project_id=project.id).with_for_update().first()
    if not task:
        db.session.rollback()
        flash("Task not found.", "warning")
        return redirect(url_for("professional.professional_project_workspace", project_id=project.id))
        
    if task.status != "Queued":
        db.session.rollback()
        flash("Task has already been claimed or completed.", "warning")
        return redirect(url_for("professional.professional_project_workspace", project_id=project.id))

    try:
        task.status = "In Progress"
        task.specialist_id = current_user.id

        ActivityLog.log_event(
            project_id=project.id,
            action="TASK_CLAIMED",
            description=f"Specialist {current_user.name} claimed retainer task: {task.title}",
            user_id=current_user.id,
            category="info",
        )

        db.session.commit()
        flash(f"Claimed task '{task.title}'. Status is now In Progress.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error claiming task {task_id}: {str(e)}")
        flash("Could not claim task. Please try again.", "danger")

    return redirect(url_for("professional.professional_project_workspace", project_id=project.id))


# =========================================================
# COMPLETE RETAINER TASK & LOG HOURS (HOURS BANK AUTO-DECREMENT)
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/task/<int:task_id>/complete", methods=["POST"])
@login_required
def complete_retainer_task(project_id: int, task_id: int) -> Any:
    # ROW-LEVEL LOCK: Lock the Project and Task to prevent race conditions on hours bank decrement
    project = Project.query.filter_by(id=project_id).with_for_update().first()
    if not project:
        db.session.rollback()
        flash("Project not found.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    is_assigned = bool(
        project.assigned_professional_id == current_user.id
        or ProfessionalProjectApplication.query.filter_by(
            project_id=project.id, professional_id=current_user.id, status="Assigned"
        ).first()
    )

    if not is_assigned and getattr(current_user, "role", "").lower() != "admin":
        db.session.rollback()
        flash("Unauthorized action.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    task = RetainerTask.query.filter_by(id=task_id, project_id=project.id).with_for_update().first()
    if not task:
        db.session.rollback()
        flash("Task not found.", "warning")
        return redirect(url_for("professional.professional_project_workspace", project_id=project.id))
        
    if task.status == "Completed":
        db.session.rollback()
        flash("This task has already been completed.", "warning")
        return redirect(url_for("professional.professional_project_workspace", project_id=project.id))

    try:
        hours_spent = Decimal(request.form.get("hours_spent", "1.0").strip())
    except Exception:
        hours_spent = Decimal("1.0")

    notes = request.form.get("completion_notes", "").strip()

    try:
        # 1. Decrement Project Hours Bank safely
        curr_rem = Decimal(str(project.hours_remaining or 0))
        new_rem = max(Decimal("0.00"), curr_rem - hours_spent)
        project.hours_remaining = new_rem

        # 2. Decrement Active Subscription Hours Bank safely
        active_sub = RazorpaySubscription.query.filter_by(project_id=project.id, status="active").with_for_update().first()
        if active_sub:
            sub_curr = Decimal(str(active_sub.hours_remaining or 0))
            active_sub.hours_remaining = max(Decimal("0.00"), sub_curr - hours_spent)

        # 3. Create Immutable Time Log
        time_log = ProjectTimeLog()
        time_log.project_id = project.id
        time_log.professional_id = current_user.id
        time_log.hours = hours_spent
        time_log.hourly_rate = Decimal("0.00")
        time_log.description = f"Retainer Task #{task.id}: {task.title}. Notes: {notes}"
        time_log.logged_date = get_ist_now()
        time_log.is_billed = True
        db.session.add(time_log)

        # 4. Mark Task Completed
        task.status = "Completed"
        task.hours_spent = hours_spent
        task.completion_notes = notes
        task.completed_at = get_ist_now()

        # 5. Notify Client of Task Completion
        notif = Notification()
        notif.user_id = project.customer_id
        notif.title = f"Task Completed: {task.title} ✓"
        notif.message = (
            f"Specialist {current_user.name} completed '{task.title}' ({hours_spent} hrs logged). "
            f"Remaining Care Plan quota: {new_rem} hours."
        )
        notif.link = f"/project/{project.id}"
        notif.created_at = get_ist_now()
        db.session.add(notif)

        ActivityLog.log_event(
            project_id=project.id,
            action="TASK_COMPLETED",
            description=f"Specialist completed maintenance task: {task.title} ({hours_spent} hrs logged). Remaining: {new_rem} hrs.",
            user_id=current_user.id,
            category="success",
        )

        db.session.commit()
        flash(f"Task completed! {hours_spent} hours deducted. Remaining balance: {new_rem} hrs.", "success")

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error completing retainer task {task_id}: {str(e)}")
        flash("Could not complete task. Please try again.", "danger")

    return redirect(url_for("professional.professional_project_workspace", project_id=project.id))


# =========================================================
# SUBMIT MILESTONE DELIVERABLE
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/submit", methods=["POST"])
@professional_bp.route("/professional/projects/<int:project_id>/submit", methods=["POST"])
@login_required
def submit_deliverable(project_id: int) -> Any:
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("professional", "admin"):
        flash("Specialist authorization required.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    # ROW-LEVEL LOCK: Lock project to prevent concurrent duplicate submissions
    project = Project.query.filter_by(id=project_id).with_for_update().first()
    if not project:
        db.session.rollback()
        flash("Project not found.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    is_assigned = bool(
        project.assigned_professional_id == current_user.id
        or ProfessionalProjectApplication.query.filter_by(
            project_id=project.id,
            professional_id=current_user.id,
            status="Assigned",
        ).first()
    )

    if not is_assigned and user_role != "admin":
        db.session.rollback()
        flash("You are not assigned to this project.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    if project.status not in ("In Progress", "Revision"):
        db.session.rollback()
        flash("Project must be in In Progress or Revision status to submit deliverables.", "warning")
        return redirect(url_for("professional.professional_project_workspace", project_id=project.id))

    live_url = request.form.get("live_url", "").strip()
    preview_url = request.form.get("preview_url", "").strip()
    source_url = request.form.get("source_url", "").strip()
    notes = request.form.get("completion_notes", "").strip()

    if live_url and not is_valid_http_url(live_url):
        db.session.rollback()
        flash("Please provide a valid live URL starting with http:// or https://.", "warning")
        return redirect(url_for("professional.professional_project_workspace", project_id=project.id))

    if not notes:
        db.session.rollback()
        flash("Please provide completion notes explaining what was built.", "warning")
        return redirect(url_for("professional.professional_project_workspace", project_id=project.id))

    try:
        attachment_url = save_project_upload(
            request.files.get("deliverable_file"),
            {"zip", "pdf", "png", "jpg", "jpeg", "gif"},
        )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("professional.professional_project_workspace", project_id=project.id))

    if attachment_url:
        notes += f"\n\nAttached Deliverable File: {attachment_url}"

    try:
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

        admins = User.query.filter_by(role="admin").all()
        for admin in admins:
            admin_notif = Notification()
            admin_notif.user_id = admin.id
            admin_notif.title = f"Deliverable Submitted: Project #{project.id}"
            admin_notif.message = f"{current_user.name} submitted a staging URL for '{project.service}'. Review required."
            admin_notif.link = url_for("admin.admin_project_review")
            db.session.add(admin_notif)

        # Notify Client via WhatsApp
        customer_user = getattr(project, "customer", None) or db.session.get(User, project.customer_id)
        if customer_user:
            cust_phone = getattr(customer_user.customer_profile, "phone", None) if getattr(customer_user, "customer_profile", None) else None
            notify_deliverable_ready(
                project.id,
                project.service,
                preview_url or live_url or "Workspace",
                cust_phone,
                customer_user.name,
            )
            
        db.session.commit()

        flash(f"Milestone deliverable #{next_num} submitted to the Review Gate.", "success")
        return redirect(url_for("professional.professional_dashboard"))

    except Exception as exc:
        db.session.rollback()
        logger.error("Error submitting deliverable for project %s: %s", project_id, str(exc), exc_info=True)
        flash("An error occurred while submitting your deliverable. Please try again.", "danger")
        return redirect(url_for("professional.professional_project_workspace", project_id=project.id))


# =========================================================
# RESUBMIT DELIVERABLE
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/resubmit", methods=["POST"])
@professional_bp.route("/professional/projects/<int:project_id>/resubmit", methods=["POST"])
@login_required
def resubmit_deliverable(project_id: int) -> Any:
    project = db.session.get(Project, project_id)
    if not project or project.status != "Revision":
        flash("Project is not awaiting revision.", "warning")
        return redirect(url_for("professional.professional_project_workspace", project_id=project_id))
    return submit_deliverable(project_id)


# =========================================================
# ESCROW WALLET REDEMPTION & WITHDRAWAL (ANTI-ABANDONMENT GUARD)
# =========================================================

@professional_bp.route("/professional/wallet/withdraw", methods=["POST"])
@login_required
def withdraw_funds() -> Any:
    user_role = getattr(current_user, "role", "").lower()
    if user_role != "professional":
        flash("Specialist access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    # ROW-LEVEL LOCK: Ensure rapid double-clicks don't overdraft wallet
    profile = ProfessionalProfile.query.filter_by(user_id=current_user.id).with_for_update().first()
    if not profile:
        db.session.rollback()
        flash("Profile not found.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    current_avail = Decimal(str(profile.available_balance or 0))
    current_pend = Decimal(str(profile.pending_balance or 0))

    if current_avail <= Decimal("0.00"):
        db.session.rollback()
        if current_pend > Decimal("0.00"):
            flash(
                f"Escrow Protection: ₹{current_pend:,.2f} is currently locked in active sprint escrow. "
                "Funds unlock into your available wallet only after 100% deliverable handover is completed and signed off.",
                "warning",
            )
        else:
            flash("You currently have no available balance to redeem.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    # Active Sprint Anti-Abandonment Guard: Prevent withdrawals while assigned to active incomplete sprints
    active_sprints = Project.query.filter(
        Project.assigned_professional_id == current_user.id,
        Project.status.in_(["In Progress", "Submitted", "Revision", "Assigned"]),
    ).all()

    if active_sprints:
        db.session.rollback()
        active_refs = ", ".join(f"#{p.id}" for p in active_sprints)
        flash(
            f"Active Sprint Guard: You have active in-flight sprint(s) ({active_refs}). "
            "All milestone deliverables must reach 100% signed-off completion before redeeming wallet funds.",
            "warning",
        )
        return redirect(url_for("professional.professional_dashboard"))

    try:
        amount = Decimal(request.form.get("amount", "0").strip())
    except Exception:
        db.session.rollback()
        flash("Please enter a valid numeric amount.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    if amount <= Decimal("0.00"):
        db.session.rollback()
        flash("Withdrawal amount must be greater than zero.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    if amount > current_avail:
        db.session.rollback()
        flash(f"Requested amount (₹{amount}) exceeds available redeemable balance (₹{current_avail}).", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    payout_mode = request.form.get("payout_mode", "UPI").strip().upper()
    upi_id = request.form.get("upi_id", "").strip() or profile.upi_id
    bank_account = request.form.get("bank_account_number", "").strip() or profile.bank_account_number
    bank_ifsc = request.form.get("bank_ifsc", "").strip().upper() or profile.bank_ifsc
    beneficiary_name = request.form.get("bank_beneficiary_name", "").strip() or profile.bank_beneficiary_name or current_user.name

    if payout_mode == "UPI" and not upi_id:
        db.session.rollback()
        flash("Please provide a valid UPI ID (e.g. name@oksbi).", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    if payout_mode == "BANK_TRANSFER" and (not bank_account or not bank_ifsc):
        db.session.rollback()
        flash("Please provide complete bank account number and IFSC code.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    try:
        profile.available_balance = current_avail - amount

        if upi_id:
            profile.upi_id = upi_id
        if bank_account:
            profile.bank_account_number = bank_account
        if bank_ifsc:
            profile.bank_ifsc = bank_ifsc
        if beneficiary_name:
            profile.bank_beneficiary_name = beneficiary_name

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

        tx = WalletTransaction()
        tx.user_id = current_user.id
        tx.amount = amount
        tx.currency = "INR"
        tx.transaction_type = "WITHDRAWAL_DEBIT"
        tx.reference_type = "WITHDRAWAL"
        tx.description = f"Requested redemption of ₹{amount} via {payout_mode}."
        tx.created_at = get_ist_now()
        db.session.add(tx)

        admins = User.query.filter_by(role="admin").all()
        for admin in admins:
            notif = Notification()
            notif.user_id = admin.id
            notif.title = f"New Payout Withdrawal Request: ₹{amount}"
            notif.message = f"{current_user.name} requested ₹{amount} redemption via {payout_mode}."
            notif.link = url_for("admin.admin_transactions")
            db.session.add(notif)

        db.session.commit()
        flash(f"Redemption request for ₹{amount} submitted successfully.", "success")
    except Exception as exc:
        db.session.rollback()
        logger.error("Error processing withdrawal request for user %s: %s", current_user.id, str(exc), exc_info=True)
        flash("Failed to process your redemption request. Please try again.", "danger")

    return redirect(url_for("professional.professional_dashboard"))


# =========================================================
# SPECIALIST PAYOUT REQUEST FOR INVOICE
# =========================================================

@professional_bp.route("/professional/invoice/<int:invoice_id>/redeem", methods=["POST"])
@professional_bp.route("/professional/invoice/<int:invoice_id>/request-payout", methods=["POST"])
@professional_bp.route("/professional/payout/request/<int:invoice_id>", methods=["POST"])
@login_required
def request_payout(invoice_id: int) -> Any:
    invoice = db.session.get(Invoice, invoice_id)
    if not invoice:
        flash("Invoice not found.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    project = invoice.project
    if not project or project.status != "Completed":
        flash("Cannot request payout before client approval.", "warning")
        return redirect(url_for("professional.professional_dashboard"))

    assigned_app = ProfessionalProjectApplication.query.filter_by(
        project_id=project.id, status="Assigned"
    ).first()
    
    is_assigned = bool(
        (assigned_app and assigned_app.professional_id == current_user.id)
        or project.assigned_professional_id == current_user.id
    )

    if not is_assigned:
        flash("Only the assigned specialist can request payout.", "danger")
        return redirect(url_for("professional.professional_dashboard"))

    if invoice.payout_status == "Requested":
        flash(f"Payout has already been requested for invoice #{invoice.invoice_number}.", "info")
        return redirect(url_for("professional.professional_dashboard"))

    try:
        invoice.payout_status = "Requested"
        
        admins = User.query.filter_by(role="admin").all()
        for admin in admins:
            notif = Notification(
                user_id=admin.id,
                title="Specialist Payout Requested",
                message=f"Project #{project.id}: {current_user.name} has requested a payout.",
                link="/admin/transactions",
                created_at=get_ist_now(),
            )
            db.session.add(notif)

        db.session.commit()
        flash(f"Payout requested for invoice #{invoice.invoice_number}.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error("Error requesting invoice payout: %s", str(e))
        flash("Could not request payout.", "danger")

    return redirect(url_for("professional.professional_dashboard"))


# =========================================================
# WORKSPACE IN-PROJECT CHAT
# =========================================================

@professional_bp.route("/professional/project/<int:project_id>/message", methods=["POST"])
@login_required
def send_workspace_message(project_id: int) -> Any:
    text = request.form.get("message", "").strip()
    if not text:
        flash("Message cannot be empty.", "warning")
        return redirect(url_for("professional.professional_project_workspace", project_id=project_id))

    try:
        msg = ChatMessage()
        msg.project_id = project_id
        msg.sender_id = current_user.id
        msg.message = text
        msg.created_at = get_ist_now()

        db.session.add(msg)
        db.session.commit()
        flash("Note posted to project discussion.", "success")
    except Exception as exc:
        db.session.rollback()
        logger.error("Error sending chat message in project %s: %s", project_id, str(exc), exc_info=True)
        flash("Could not post message. Please try again.", "danger")

    return redirect(url_for("professional.professional_project_workspace", project_id=project_id))

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
def professional_profile() -> Any:
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("professional", "admin"):
        flash("Specialist access required.", "danger")
        return redirect(url_for("customer.dashboard"))

    profile = ProfessionalProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        try:
            profile = ProfessionalProfile()
            profile.user_id = current_user.id
            db.session.add(profile)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            profile = ProfessionalProfile.query.filter_by(user_id=current_user.id).first()
        except Exception as e:
            db.session.rollback()
            logger.error("Error initializing professional profile: %s", str(e))

    if profile is None:
        flash("Could not locate or initialize your professional profile.", "danger")
        return redirect(url_for("customer.dashboard"))

    if request.method == "POST":
        try:
            profile.expertise = request.form.get("expertise", "").strip()
            profile.experience = request.form.get("experience", "").strip()
            profile.about = request.form.get("about", "").strip()
            profile.phone = request.form.get("phone", "").strip()
            profile.location = request.form.get("location", "").strip()
            
            profile.skills = request.form.get("skills", "").strip()
            profile.technologies = request.form.get("technologies", "").strip()
            profile.skill_level = request.form.get("skill_level", "").strip()
            profile.project_types = request.form.get("project_types", "").strip()

            profile.portfolio = request.form.get("portfolio", "").strip()
            profile.github = request.form.get("github", "").strip()
            profile.linkedin = request.form.get("linkedin", "").strip()

            profile.education = request.form.get("education", "").strip()
            profile.certifications = request.form.get("certifications", "").strip()
            profile.work_experience = request.form.get("work_experience", "").strip()
            profile.availability = request.form.get("availability", "Available (Full-Time)").strip()
            profile.current_workload = request.form.get("current_workload", "").strip()

            profile.upi_id = request.form.get("upi_id", "").strip()
            profile.bank_account_number = request.form.get("bank_account_number", "").strip()
            profile.bank_ifsc = request.form.get("bank_ifsc", "").strip().upper()
            profile.bank_beneficiary_name = request.form.get("bank_beneficiary_name", "").strip()
            profile.pan_number = request.form.get("pan_number", "").strip().upper()

            profile.profile_completed = True
            db.session.commit()
            flash("Comprehensive specialist profile & banking coordinates saved successfully.", "success")
            return redirect(url_for("professional.professional_profile"))
        except Exception as exc:
            db.session.rollback()
            logger.error("Error saving professional profile: %s", str(exc), exc_info=True)
            flash("Failed to save profile changes. Please try again.", "danger")

    return render_template("professional_profile.html", profile=profile)

@professional_bp.route("/professional/api/project/<int:project_id>/messages")
@login_required
def api_get_professional_messages(project_id: int) -> Any:
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
        
    is_assigned = bool(
        project.assigned_professional_id == current_user.id
        or ProfessionalProjectApplication.query.filter_by(
            project_id=project.id, professional_id=current_user.id, status="Assigned"
        ).first()
    )
    if not is_assigned and getattr(current_user, "role", "").lower() != "admin":
        return jsonify({"error": "Unauthorized"}), 403

    messages = ChatMessage.query.filter_by(project_id=project.id).order_by(ChatMessage.created_at.asc()).all()
    data = [{
        "id": m.id,
        "sender_name": m.sender.name if m.sender else "Agency Team",
        "message": m.message,
        "time": m.created_at.strftime("%d %b %I:%M %p"),
        "is_me": m.sender_id == current_user.id
    } for m in messages]
    return jsonify({"success": True, "messages": data})