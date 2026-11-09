"""AgencyOS API Blueprint.

Provides public services catalog, session identity resolution,
lead audit capture, and JSON endpoints for projects, invoices, and milestones
with full two-stage escrow releases and activity audit logging.
"""

from decimal import Decimal
import logging
import secrets
from typing import Any

from flask import Blueprint, flash, jsonify, redirect, request, session, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user

from app.extensions import db
from app.models import (
    ActivityLog,
    CustomerProfile,
    Invoice,
    Milestone,
    Notification,
    PACKAGE_PRICES,
    Payment,
    ProfessionalProfile,
    ProfessionalProjectApplication,
    Project,
    User,
    WalletTransaction,
    get_ist_now,
    services,
)
from app.services.whatsapp import notify_project_completed
from app.utils.auth import roles_required
from app.utils.currency import (
    compute_financial_split,
    format_currency,
    get_package_price,
    sanitize_currency,
)

logger = logging.getLogger(__name__)
api_bp = Blueprint("api", __name__, url_prefix="/api")


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
def api_services() -> ResponseReturnValue:
    return jsonify({"success": True, "services": services}), 200


@api_bp.route("/service/<slug>", methods=["GET"])
def api_service(slug: str) -> ResponseReturnValue:
    service = next((item for item in services if item.get("slug") == slug), None)
    if not service:
        return jsonify({"success": False, "message": "Service not found."}), 404
    return jsonify({"success": True, "service": service}), 200


# =========================================================
# FREE AUDIT LEAD CAPTURE (HOMEPAGE FORM)
# =========================================================

@api_bp.route("/free-audit", methods=["POST"], endpoint="api_free_audit")
def api_free_audit() -> ResponseReturnValue:
    """Capture public website audit requests from the homepage."""
    data = request.get_json(silent=True) or request.form
    business_url = (data.get("business_url") or "").strip()

    if not business_url:
        if request.is_json:
            return jsonify({"success": False, "message": "Please enter a valid business URL or entity name."}), 400
        flash("Please enter a valid website URL or business name.", "error")
        return redirect(url_for("public.index") + "#audit")

    success_msg = f"Audit requested for '{business_url}'. Our architectural review will be dispatched via WhatsApp shortly."

    if request.is_json:
        return jsonify({"success": True, "message": success_msg, "target": business_url}), 200

    flash(success_msg, "success")
    return redirect(url_for("public.index") + "#audit")


# =========================================================
# CURRENT AUTHENTICATED USER ('/api/me')
# =========================================================

@api_bp.route("/me", methods=["GET"])
def get_current_user() -> ResponseReturnValue:
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
def onboard_client() -> ResponseReturnValue:
    payload = request.get_json(silent=True) or {}
    email = payload.get("email", "").strip().lower()
    full_name = payload.get("full_name", "").strip()
    business_name = payload.get("business_name", payload.get("company_name", "")).strip()
    phone = payload.get("phone", "").strip()

    if not email or not full_name:
        return jsonify({"success": False, "error": "Email and full name are required."}), 400

    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        return jsonify({"success": False, "error": "A user with this email already exists."}), 400

    temp_password = secrets.token_urlsafe(12)
    try:
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
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": f"Failed to onboard client: {str(exc)}"}), 500


# =========================================================
# PROJECTS API
# =========================================================

@api_bp.route("/projects", methods=["GET"])
@roles_required("admin", "manager", "client", "customer")
def get_projects() -> ResponseReturnValue:
    user = _resolve_authenticated_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

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
def get_project_details(project_id: int) -> ResponseReturnValue:
    user = _resolve_authenticated_user()
    project = Project.query.get_or_404(project_id)

    if not user or (user.role not in ["admin", "manager"] and project.customer_id != user.id):
        return jsonify({"success": False, "error": "Unauthorized access to project."}), 403

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
def get_invoices() -> ResponseReturnValue:
    user = _resolve_authenticated_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

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
def get_invoice_receipt(invoice_id: int) -> ResponseReturnValue:
    user = _resolve_authenticated_user()
    invoice = Invoice.query.get_or_404(invoice_id)

    if not user or (user.role not in ["admin", "manager"] and invoice.customer_id != user.id):
        return jsonify({"error": "Unauthorized"}), 403

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
# MILESTONES APPROVAL API (WITH ATOMIC ESCROW RELEASE)
# =========================================================

@api_bp.route("/milestones/<int:milestone_id>/approve", methods=["POST"])
@roles_required("client", "customer", "admin")
def approve_milestone(milestone_id: int) -> ResponseReturnValue:
    user = _resolve_authenticated_user()
    milestone = Milestone.query.get_or_404(milestone_id)
    project = milestone.project

    if not user or (user.role not in ["admin", "manager"] and getattr(project, "customer_id", None) != user.id):
        return jsonify({"error": "Unauthorized to approve this milestone"}), 403

    if milestone.status == "released":
        return jsonify({"error": "Milestone already approved and released"}), 400

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
        total_talent_payout = Decimal(str(assigned_app.proposed_rate)) if (assigned_app and assigned_app.proposed_rate is not None) else talent_share

        all_milestones = Milestone.query.filter_by(project_id=project.id).order_by(Milestone.id.asc()).all()
        released_count = sum(1 for m in all_milestones if m.status == "released" or m.id == milestone.id)
        total_milestones_count = len(all_milestones) or 3

        if released_count < total_milestones_count:
            project.status = "In Progress"
            earned_amount = (total_talent_payout * Decimal("0.25") if released_count > 1 else total_talent_payout * Decimal("0.50")).quantize(Decimal("0.01"))
        else:
            project.status = "Completed"
            curr_pend = Decimal(str(specialist_profile.pending_balance or 0)) if specialist_profile else Decimal("0.00")
            earned_amount = curr_pend if curr_pend > Decimal("0.00") else (total_talent_payout * Decimal("0.25")).quantize(Decimal("0.01"))

        if specialist_profile and specialist_id:
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
            tx.description = f"Milestone #{milestone.id} approved via API. {format_currency(earned_amount, currency)} released to available balance."
            tx.created_at = get_ist_now()
            db.session.add(tx)

            notif = Notification()
            notif.user_id = specialist_id
            notif.title = f"Milestone Released! {format_currency(earned_amount, currency)} Unlocked 💰"
            notif.message = f"Milestone '{milestone.title}' was approved for Project #{project.id}."
            notif.link = "/professional/dashboard"
            notif.created_at = get_ist_now()
            db.session.add(notif)

        ActivityLog.log_event(
            project_id=project.id,
            action="MILESTONE_APPROVED",
            description=f"Milestone '{milestone.title}' approved via API. Escrow released.",
            user_id=user.id,
            category="success",
        )

        if project.status == "Completed":
            customer_profile = CustomerProfile.query.filter_by(user_id=project.customer_id).first()
            client_phone = customer_profile.phone if customer_profile else None
            try:
                customer_user = db.session.get(User, project.customer_id)
                notify_project_completed(
                    project_id=project.id,
                    service_name=project.service,
                    client_phone=client_phone,
                    client_name=customer_user.name if customer_user else "Client",
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

    except Exception as exc:
        db.session.rollback()
        logger.error("API milestone approval failed: %s", str(exc), exc_info=True)
        return jsonify({"success": False, "error": f"Failed to approve milestone: {str(exc)}"}), 500