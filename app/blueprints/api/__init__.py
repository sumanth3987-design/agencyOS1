"""AgencyOS API Blueprint.

Provides public services catalog, session identity resolution,
lead audit capture, and JSON endpoints for projects, invoices, and milestones
with full two-stage escrow releases and activity audit logging.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, cast

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import csrf, db, get_razorpay_client, limiter
from app.models import (
    ActivityLog,
    AgencySetting,
    CustomerProfile,
    Invoice,
    Milestone,
    Notification,
    PACKAGE_PRICES,
    Payment,
    ProfessionalProfile,
    ProfessionalProjectApplication,
    Project,
    ProjectAddOn,
    RazorpaySubscription,
    RazorpaySubscriptionPlan,
    SubscriptionCharge,
    User,
    WalletTransaction,
    get_ist_now,
    services,
)
from app.services.whatsapp import (
    notify_payment_received,
    notify_payout_disbursed,
    notify_retainer_renewed,
    notify_project_completed,
)
from app.utils.currency import (
    compute_financial_split,
    format_currency,
    get_package_price,
    sanitize_currency,
    to_minor_units,
)
from app.utils.decorators import roles_required

logger = logging.getLogger(__name__)
api_bp = Blueprint("api", __name__, url_prefix="/api")


# =========================================================
# API HARDENING: STANDARDIZED RESPONSE DECORATOR
# =========================================================

def api_handler(f):
    """Decorator to ensure all API routes return a standardized JSON response 
    and wrap all exceptions into a clean JSON error boundary."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as exc:
            db.session.rollback()
            logger.error("API Error [%s]: %s", request.path, str(exc), exc_info=True)
            return jsonify({
                "success": False, 
                "message": "An internal server error occurred.",
                "error_code": "INTERNAL_SERVER_ERROR"
            }), 500
    return decorated_function

from functools import wraps

# =========================================================
# UTILITIES
# =========================================================

def _resolve_authenticated_user() -> Any:
    """Retrieve current user seamlessly from Flask-Login or Session."""
    if current_user and getattr(current_user, "is_authenticated", False):
        if hasattr(current_user, "_get_current_object"):
            return current_user._get_current_object()
        return current_user
    user_id = session.get("user_id")
    if user_id:
        return db.session.get(User, int(user_id))
    return None


# =========================================================
# PUBLIC SERVICES CATALOG
# =========================================================

@api_bp.route("/services", methods=["GET"])
@api_handler
def api_services() -> Any:
    return jsonify({"success": True, "services": services}), 200


@api_bp.route("/service/<slug>", methods=["GET"])
@api_handler
def api_service(slug: str) -> Any:
    service = next((item for item in services if item.get("slug") == slug), None)
    if not service:
        return jsonify({"success": False, "message": "Service not found."}), 404
    return jsonify({"success": True, "service": service}), 200


# =========================================================
# FREE AUDIT LEAD CAPTURE (HOMEPAGE FORM)
# =========================================================

@api_bp.route("/free-audit", methods=["POST"], endpoint="api_free_audit")
@api_handler
def api_free_audit() -> Any:
    """Capture public website audit requests from the homepage."""
    data = request.get_json(silent=True) or request.form
    business_url = (data.get("business_url") or "").strip()

    if not business_url:
        return jsonify({"success": False, "message": "Please enter a valid business URL or entity name."}), 400

    success_msg = f"Audit requested for '{business_url}'. Our architectural review will be dispatched via WhatsApp shortly."

    return jsonify({"success": True, "message": success_msg, "target": business_url}), 200


# =========================================================
# CURRENT AUTHENTICATED USER ('/api/me')
# =========================================================

@api_bp.route("/me", methods=["GET"])
@api_handler
def get_current_user() -> Any:
    user = _resolve_authenticated_user()
    if not user or not getattr(user, "is_active", True):
        return jsonify({"authenticated": False}), 200

    profile = getattr(user, "customer_profile", None)
    business_name = getattr(profile, "business_name", None) if profile else None

    return jsonify({
        "authenticated": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": getattr(user, "full_name", getattr(user, "name", user.email)),
            "role": user.role,
            "company": business_name,
        },
    }), 200


# =========================================================
# CLIENT ONBOARDING (ADMIN ONLY)
# =========================================================

@api_bp.route("/clients/onboard", methods=["POST"])
@roles_required("admin")
@api_handler
def onboard_client() -> Any:
    payload = request.get_json(silent=True) or {}
    email = payload.get("email", "").strip().lower()
    full_name = payload.get("full_name", "").strip()
    business_name = payload.get("business_name", payload.get("company_name", "")).strip()
    phone = payload.get("phone", "").strip()

    if not email or not full_name:
        return jsonify({"success": False, "message": "Email and full name are required."}), 400

    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        return jsonify({"success": False, "message": "A user with this email already exists."}), 400

    temp_password = secrets.token_urlsafe(12)
    new_client = User()
    new_client.email = email
    new_client.name = full_name
    new_client.full_name = full_name
    new_client.role = "customer"
    new_client.set_password(temp_password)

    db.session.add(new_client)
    db.session.flush()

    profile = CustomerProfile()
    profile.user_id = new_client.id
    profile.business_name = business_name or full_name
    profile.phone = phone
    profile.profile_completed = False

    db.session.add(profile)
    db.session.commit()

    return jsonify({
        "success": True,
        "message": "Client account created successfully.",
        "client": {
            "id": new_client.id,
            "email": new_client.email,
            "full_name": new_client.full_name,
            "business_name": profile.business_name,
            "temporary_password": temp_password,
        },
    }), 201


# =========================================================
# PROJECTS API
# =========================================================

@api_bp.route("/projects", methods=["GET"])
@roles_required("admin", "manager", "client", "customer")
@api_handler
def get_projects() -> Any:
    user = _resolve_authenticated_user()
    if not user:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    if user.role in ["admin", "manager"]:
        projects = Project.query.order_by(Project.created_at.desc()).all()
    else:
        projects = Project.query.filter_by(customer_id=user.id).order_by(Project.created_at.desc()).all()

    return jsonify({
        "success": True,
        "projects": [
            {
                "id": p.id,
                "title": getattr(p, "service", f"Project #{p.id}"),
                "service": p.service,
                "package": p.package,
                "currency": p.currency,
                "status": p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in projects
        ],
    }), 200


@api_bp.route("/projects/<int:project_id>", methods=["GET"])
@roles_required("admin", "manager", "client", "customer")
@api_handler
def get_project_details(project_id: int) -> Any:
    user = _resolve_authenticated_user()
    project = Project.query.get_or_404(project_id)

    if not user or (user.role not in ["admin", "manager"] and project.customer_id != user.id):
        return jsonify({"success": False, "message": "Unauthorized access to project."}), 403

    milestones = Milestone.query.filter_by(project_id=project.id).order_by(Milestone.id.asc()).all()
    payments = Payment.query.filter_by(project_id=project.id).order_by(Payment.created_at.desc()).all()

    return jsonify({
        "success": True,
        "project": {
            "id": project.id,
            "service": project.service,
            "package": project.package,
            "currency": project.currency,
            "status": project.status,
            "milestones": [
                {
                    "id": m.id,
                    "title": m.title,
                    "amount": float(m.amount),
                    "status": m.status,
                    "released_at": m.released_at.isoformat() if m.released_at else None,
                }
                for m in milestones
            ],
            "payments": [
                {
                    "id": pay.id,
                    "amount": float(pay.amount),
                    "currency": pay.currency,
                    "status": pay.status,
                    "razorpay_payment_id": pay.razorpay_payment_id,
                    "paid_at": pay.paid_at.isoformat() if pay.paid_at else None,
                }
                for pay in payments
            ],
        },
    }), 200


# =========================================================
# INVOICES API
# =========================================================

@api_bp.route("/invoices", methods=["GET"])
@roles_required("admin", "manager", "client", "customer")
@api_handler
def get_invoices() -> Any:
    user = _resolve_authenticated_user()
    if not user:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    if user.role in ["admin", "manager"]:
        invoices = Invoice.query.order_by(Invoice.id.desc()).all()
    else:
        invoices = Invoice.query.filter_by(customer_id=user.id).order_by(Invoice.id.desc()).all()

    return jsonify({
        "success": True,
        "invoices": [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "amount": float(inv.gross_amount),
                "currency": inv.currency,
                "status": inv.status,
                "payout_status": inv.payout_status,
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
            }
            for inv in invoices
        ],
    }), 200


@api_bp.route("/invoices/<int:invoice_id>/receipt", methods=["GET"])
@roles_required("admin", "manager", "client", "customer")
@api_handler
def get_invoice_receipt(invoice_id: int) -> Any:
    user = _resolve_authenticated_user()
    invoice = Invoice.query.get_or_404(invoice_id)

    if not user or (user.role not in ["admin", "manager"] and invoice.customer_id != user.id):
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    return jsonify({
        "success": True,
        "receipt": {
            "invoice_number": invoice.invoice_number,
            "status": invoice.status,
            "gross_amount": float(invoice.gross_amount),
            "tax_amount": float(invoice.tax_amount),
            "currency": invoice.currency,
            "payout_status": invoice.payout_status,
            "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
            "razorpay_order_id": invoice.razorpay_order_id,
        },
    }), 200


# =========================================================
# SECURE CHAT STREAM ENDPOINT WITH IDOR PROTECTION
# =========================================================

@api_bp.route("/project/<int:project_id>/messages", methods=["GET"])
@login_required
@api_handler
def get_project_messages(project_id: int) -> Any:
    """Fetch chat messages with strict IDOR access control."""
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({"success": False, "message": "Project not found."}), 404

    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("admin", "manager"):
        is_owner = project.customer_id == current_user.id
        is_assigned_pro = bool(
            ProfessionalProjectApplication.query.filter_by(
                project_id=project.id,
                professional_id=current_user.id,
                status="Assigned",
            ).first()
        ) or (project.assigned_professional_id == current_user.id)

        if not is_owner and not is_assigned_pro:
            return jsonify({"success": False, "message": "Unauthorized access to project messages."}), 403

    from app.models import ChatMessage
    messages = (
        ChatMessage.query.filter_by(project_id=project.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    data = [
        {
            "id": m.id,
            "sender_name": m.sender.name if m.sender else "Agency Team",
            "message": m.message,
            "time": m.created_at.strftime("%d %b %I:%M %p") if m.created_at else "",
            "is_me": m.sender_id == current_user.id,
        }
        for m in messages
    ]
    return jsonify({"success": True, "messages": data}), 200


@api_bp.route("/ai/generate-scope", methods=["POST"])
@login_required
@api_handler
def api_generate_scope() -> Any:
    """Secure proxy for Gemini AI Statement of Work generation (Admin-only)."""
    user_role = getattr(current_user, "role", "").lower()
    if user_role != "admin":
        return jsonify({"success": False, "message": "Admin authorization required."}), 403

    import os
    import json
    import urllib.request

    client_name = request.form.get("client_name", "Valued Client").strip()
    service_type = request.form.get("service_type", "Website Design & Development").strip()
    notes = request.form.get("notes", "").strip()

    if not notes:
        return jsonify({"success": False, "message": "Please provide project requirements or notes."}), 400

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        fallback_sow = (
            f"### Scope of Work: {service_type}\n"
            f"**Client Entity:** {client_name}\n"
            f"**Project Brief:** {notes}\n\n"
            f"#### Milestone 1: Strategic Architecture & Wireframes (50% Advance)\n"
            f"#### Milestone 2: Functional Staging & Payment Rails (25%)\n"
            f"#### Milestone 3: DNS Production Cutover & Handover (25%)\n"
        )
        return jsonify({"success": True, "scope": fallback_sow}), 200

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        prompt = f"Create a structured 3-milestone proposal (50/25/25 split) for {client_name} for {service_type}: {notes}"
        req_data = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as response:
            res_body = json.loads(response.read().decode("utf-8"))
            scope_text = res_body["candidates"][0]["content"]["parts"][0]["text"]
            return jsonify({"success": True, "scope": scope_text}), 200
    except Exception as exc:
        logger.error("AI service proxy error: %s", str(exc), exc_info=True)
        return jsonify({"success": False, "message": f"AI service error: {str(exc)}"}), 500


# =========================================================
# MILESTONES APPROVAL API (WITH ATOMIC ESCROW RELEASE)
# =========================================================

@api_bp.route("/milestones/<int:milestone_id>/approve", methods=["POST"])
@roles_required("client", "customer", "admin")
@api_handler
def approve_milestone(milestone_id: int) -> Any:
    user = _resolve_authenticated_user()
    milestone = Milestone.query.get_or_404(milestone_id)
    project = milestone.project

    if not user or (user.role not in ["admin", "manager"] and getattr(project, "customer_id", None) != user.id):
        return jsonify({"success": False, "message": "Unauthorized to approve this milestone"}), 403

    if milestone.status == "released":
        return jsonify({"success": False, "message": "Milestone already approved and released"}), 400

    try:
        milestone.status = "released"
        milestone.released_at = get_ist_now()

        # Reconcile Two-Stage Escrow Release
        assigned_app = ProfessionalProjectApplication.query.filter_by(
            project_id=project.id, status="Assigned"
        ).first()

        specialist_id = project.assigned_professional_id or (assigned_app.professional_id if assigned_app else None)
        specialist_profile = ProfessionalProfile.query.filter_by(user_id=specialist_id).first() if specialist_id else None

        currency = sanitize_currency(project.currency)
        gross = get_package_price(project.package, currency, PACKAGE_PRICES)
        talent_share, _, _ = compute_financial_split(gross, currency)
        total_talent_payout = (
            Decimal(str(assigned_app.proposed_rate))
            if (assigned_app and assigned_app.proposed_rate is not None)
            else talent_share
        )

        all_milestones = Milestone.query.filter_by(project_id=project.id).order_by(Milestone.id.asc()).all()
        released_count = sum(1 for m in all_milestones if m.status == "released" or m.id == milestone.id)
        total_milestones_count = len(all_milestones) or 3

        if released_count < total_milestones_count:
            project.status = "In Progress"
            if all_milestones and len(all_milestones) >= 1:
                all_milestones[0].status = "completed"
                all_milestones[0].released_at = get_ist_now()

            ActivityLog.log_event(
                project_id=project.id,
                action="AUTO_APPROVED_INACTIVITY",
                description=f"Gate 1 deliverable auto-approved after inactivity.",
                category="warning",
            )

        elif released_count == 2:
            project.status = "In Progress"
            if all_milestones and len(all_milestones) >= 2:
                all_milestones[1].status = "completed"
                all_milestones[1].released_at = get_ist_now()

            ActivityLog.log_event(
                project_id=project.id,
                action="AUTO_APPROVED_INACTIVITY",
                description=f"Gate 2 deliverable auto-approved after inactivity.",
                category="warning",
            )

        else:
            # Milestone 3 -> Complete sprint and unlock full payout
            project.status = "Completed"
            if specialist_profile:
                curr_pend = Decimal(str(specialist_profile.pending_balance or 0))
                curr_avail = Decimal(str(specialist_profile.available_balance or 0))

                specialist_profile.pending_balance = max(Decimal("0.00"), curr_pend - total_talent_payout)
                specialist_profile.available_balance = curr_avail + total_talent_payout

                tx = WalletTransaction()
                tx.user_id = specialist_id
                tx.amount = total_talent_payout
                tx.currency = currency
                tx.transaction_type="ESCROW_RELEASED"
                tx.reference_type="PROJECT"
                tx.reference_id=project.id
                tx.description=f"Project #{project.id} auto-approved. {format_currency(total_talent_payout, currency)} released."
                tx.created_at=get_ist_now()
                db.session.add(tx)

            for m in all_milestones:
                m.status = "completed"
                m.released_at = get_ist_now()

            ActivityLog.log_event(
                project_id=project.id,
                action="AUTO_APPROVED_INACTIVITY",
                description=f"Final handover auto-approved. Escrow payout unlocked.",
                category="success",
            )

            customer_user = getattr(project, "customer", None) or db.session.get(User, project.customer_id)
            if customer_user:
                try:
                    notify_project_completed(
                        project.id,
                        project.service,
                        getattr(customer_user.customer_profile, "phone", None),
                        customer_user.name,
                    )
                except Exception as w_err:
                    logger.warning("WhatsApp trigger notice failed: %s", w_err)

            db.session.commit()
            return jsonify({
                "success": True,
                "message": "Milestone signed off and escrow released successfully.",
                "milestone_id": milestone.id,
                "status": milestone.status,
                "released_at": milestone.released_at.isoformat(),
                "project_status": project.status,
            }), 200

        db.session.commit()
        return jsonify({
            "success": True,
            "message": "Milestone signed off and escrow released successfully.",
            "milestone_id": milestone.id,
            "status": milestone.status,
            "released_at": milestone.released_at.isoformat(),
            "project_status": project.status,
        }), 200

    except Exception as exc:
        db.session.rollback()
        logger.error("API milestone approval failed: %s", str(exc), exc_info=True)
        return jsonify({"success": False, "message": f"Failed to approve milestone: {str(exc)}"}), 500