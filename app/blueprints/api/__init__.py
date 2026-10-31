# Save this directly into your file: app/blueprints/api/__init__.py

import os
import secrets
import razorpay

from flask import Blueprint, jsonify, session, request
from flask_login import current_user
from app.models import User, Project, Invoice, CustomerProfile, Milestone, Payment, get_ist_now, services
from app.extensions import db
from app.utils.auth import roles_required

api_bp = Blueprint("api", __name__, url_prefix="/api")

def _resolve_authenticated_user():
    """Retrieve current user seamlessly from Flask-Login or Session."""
    if current_user and getattr(current_user, "is_authenticated", False):
        return current_user
    user_id = session.get("user_id")
    if user_id:
        return User.query.get(user_id)
    return None

# =========================================================
# PUBLIC SERVICES CATALOG
# =========================================================

@api_bp.route("/services", methods=["GET"])
def api_services():
    return jsonify({"success": True, "services": services}), 200

@api_bp.route("/service/<slug>", methods=["GET"])
def api_service(slug):
    service = next((item for item in services if item.get("slug") == slug), None)
    if not service:
        return jsonify({"success": False, "message": "Service not found."}), 404
    return jsonify({"success": True, "service": service}), 200

# =========================================================
# CURRENT AUTHENTICATED USER ('/api/me')
# =========================================================

@api_bp.route("/me", methods=["GET"])
def get_current_user():
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
            "full_name": getattr(user, "full_name", user.email),
            "role": user.role,
            "company": business_name,
        },
    }), 200

# =========================================================
# CLIENT ONBOARDING & INVITATION (ADMIN ONLY)
# =========================================================

@api_bp.route("/clients/onboard", methods=["POST"])
@roles_required("admin")
def onboard_client():
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
        new_client = User(
            email=email,
            full_name=full_name,
            role="customer",  # Synced with customer/client role
            is_active=True
        )
        if hasattr(new_client, "set_password"):
            new_client.set_password(temp_password)
        db.session.add(new_client)
        db.session.flush()

        profile = CustomerProfile(
            user_id=new_client.id,
            business_name=business_name or full_name,
            phone=phone
        )
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
                "temporary_password": temp_password
            }
        }), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": f"Failed to onboard client: {str(exc)}"}), 500

# =========================================================
# PROJECTS API
# =========================================================

@api_bp.route("/projects", methods=["GET"])
@roles_required("admin", "manager", "client", "customer")
def get_projects():
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
                "title": getattr(p, "title", f"Project #{p.id}"),
                "package": getattr(p, "package", "Custom"),
                "description": getattr(p, "description", ""),
                "budget": float(p.budget) if getattr(p, "budget", None) else 0.0,
                "currency": getattr(p, "currency", "INR"),
                "status": p.status,
                "created_at": p.created_at.isoformat() if getattr(p, "created_at", None) else None,
            }
            for p in projects
        ]
    }), 200

@api_bp.route("/projects/<int:project_id>", methods=["GET"])
@roles_required("admin", "manager", "client", "customer")
def get_project_details(project_id):
    user = _resolve_authenticated_user()
    project = Project.query.get_or_404(project_id)

    if user.role not in ["admin", "manager"] and project.customer_id != user.id:
        return jsonify({"success": False, "error": "Unauthorized access to project."}), 403

    milestones = Milestone.query.filter_by(project_id=project.id).order_by(Milestone.id.asc()).all()
    payments = Payment.query.filter_by(project_id=project.id).order_by(Payment.created_at.desc()).all()

    return jsonify({
        "success": True,
        "project": {
            "id": project.id,
            "title": getattr(project, "title", f"Project #{project.id}"),
            "package": getattr(project, "package", "Custom"),
            "status": project.status,
            "budget": float(project.budget) if getattr(project, "budget", None) else 0.0,
            "milestones": [
                {
                    "id": m.id,
                    "title": getattr(m, "title", f"Milestone #{m.id}"),
                    "amount": float(m.amount) if getattr(m, "amount", None) else 0.0,
                    "status": getattr(m, "status", "pending"),
                    "released_at": m.released_at.isoformat() if getattr(m, "released_at", None) else None
                } for m in milestones
            ],
            "payments": [
                {
                    "id": pay.id,
                    "amount": float(pay.amount),
                    "status": pay.status,
                    "razorpay_payment_id": pay.razorpay_payment_id,
                    "paid_at": pay.paid_at.isoformat() if getattr(pay, "paid_at", None) else None
                } for pay in payments
            ]
        }
    }), 200

# =========================================================
# INVOICES & PAYMENTS API
# =========================================================

@api_bp.route("/invoices", methods=["GET"])
@roles_required("admin", "manager", "client", "customer")
def get_invoices():
    user = _resolve_authenticated_user()
    if user.role in ["admin", "manager"]:
        invoices = Invoice.query.order_by(Invoice.id.desc()).all()
    else:
        invoices = Invoice.query.filter_by(client_id=user.id).order_by(Invoice.id.desc()).all()

    return jsonify({
        "success": True,
        "invoices": [
            {
                "id": inv.id,
                "invoice_number": getattr(inv, "invoice_number", f"INV-{inv.id}"),
                "amount": float(getattr(inv, "total_amount", inv.amount)),
                "currency": getattr(inv, "currency", "INR"),
                "status": inv.status,
                "issued_at": inv.issued_at.isoformat() if getattr(inv, "issued_at", None) else None,
                "paid_at": inv.paid_at.isoformat() if getattr(inv, "paid_at", None) else None,
            } for inv in invoices
        ]
    }), 200

@api_bp.route("/invoices/<int:invoice_id>/pay", methods=["POST"])
@roles_required("client", "customer", "admin")
def initiate_invoice_payment(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    if invoice.status == "paid":
        return jsonify({"error": "Invoice is already paid"}), 400

    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
    if not key_id or not key_secret:
        return jsonify({"error": "Payment credentials not configured"}), 500

    client = razorpay.Client(auth=(key_id, key_secret))
    total_val = float(getattr(invoice, "total_amount", invoice.amount))
    order_payload = {
        "amount": int(total_val * 100),
        "currency": getattr(invoice, "currency", "INR"),
        "receipt": f"inv_{getattr(invoice, 'invoice_number', invoice.id)}",
        "notes": {"invoice_id": invoice.id},
    }

    try:
        order = client.order.create(data=order_payload)
        invoice.razorpay_order_id = order["id"]
        db.session.commit()

        return jsonify({
            "success": True,
            "order_id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "razorpay_key": key_id,
            "invoice_number": getattr(invoice, "invoice_number", str(invoice.id)),
        }), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Failed to generate order: {str(exc)}"}), 500

@api_bp.route("/invoices/<int:invoice_id>/receipt", methods=["GET"])
@roles_required("admin", "manager", "client", "customer")
def get_invoice_receipt(invoice_id):
    user = _resolve_authenticated_user()
    invoice = Invoice.query.get_or_404(invoice_id)

    if user.role not in ["admin", "manager"] and getattr(invoice, "client_id", None) != user.id:
        return jsonify({"error": "Unauthorized"}), 403

    return jsonify({
        "success": True,
        "receipt": {
            "invoice_number": getattr(invoice, "invoice_number", f"INV-{invoice.id}"),
            "status": invoice.status,
            "amount": float(getattr(invoice, "amount", 0)),
            "tax": float(getattr(invoice, "tax", 0)),
            "total_amount": float(getattr(invoice, "total_amount", invoice.amount)),
            "currency": getattr(invoice, "currency", "INR"),
            "issued_at": invoice.issued_at.isoformat() if getattr(invoice, "issued_at", None) else None,
            "paid_at": invoice.paid_at.isoformat() if getattr(invoice, "paid_at", None) else None,
            "razorpay_order_id": getattr(invoice, "razorpay_order_id", None),
        }
    }), 200

# =========================================================
# MILESTONES APPROVAL API
# =========================================================

@api_bp.route("/milestones/<int:milestone_id>/approve", methods=["POST"])
@roles_required("client", "customer", "admin")
def approve_milestone(milestone_id):
    user = _resolve_authenticated_user()
    milestone = Milestone.query.get_or_404(milestone_id)

    if user.role not in ["admin", "manager"]:
        if getattr(milestone.project, "customer_id", None) != user.id:
            return jsonify({"error": "Unauthorized to approve this milestone"}), 403

    if milestone.status == "released":
        return jsonify({"error": "Milestone already approved and released"}), 400

    milestone.status = "released"
    milestone.released_at = get_ist_now()
    db.session.commit()

    return jsonify({
        "success": True,
        "message": "Milestone signed off and released successfully",
        "milestone_id": milestone.id,
        "status": milestone.status,
        "released_at": milestone.released_at.isoformat(),
    }), 200