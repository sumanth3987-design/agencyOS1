import os

import razorpay

from flask import Blueprint, jsonify, session, request
from app.models import User, Project, Invoice, CustomerProfile, Milestone, get_ist_now, services
from app.extensions import db
from app.utils.auth import roles_required

api_bp = Blueprint("api", __name__, url_prefix="/api")

@api_bp.route("/services")
def api_services():
    return jsonify(services)


# =========================================================
# SINGLE SERVICE API
# =========================================================

@api_bp.route("/service/<slug>")
def api_service(slug):
    service = next(
        (item for item in services if item["slug"] == slug), None
    )

    if not service:
        return jsonify({
            "success": False,
            "message": "Service not found."
        }), 404

    return jsonify(service)


# ============================================================




@api_bp.route("/me", methods=["GET"])
def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"authenticated": False}), 200
    user = User.query.get(user_id)
    if not user:
        return jsonify({"authenticated": False}), 200
    return jsonify({
        "authenticated": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "company": (
                user.customer_profile.business_name
                if user.customer_profile
                else None
            ),
        },
    }), 200


@api_bp.route("/projects", methods=["GET"])
@roles_required("admin", "manager", "client")
def get_projects():
    user_id = session.get("user_id")
    user = User.query.get(user_id)

    if user.role in ["admin", "manager"]:
        projects = Project.query.order_by(
            Project.created_at.desc()
        ).all()
    else:
        projects = (
            Project.query.filter_by(customer_id=user.id)
            .order_by(Project.created_at.desc())
            .all()
        )

    return jsonify({
        "projects": [
            {
                "id": p.id,
                "title": p.title,
                "description": p.description,
                "budget": float(p.budget) if p.budget else 0,
                "currency": p.currency,
                "status": p.status,
                "created_at": (
                    p.created_at.isoformat() if p.created_at else None
                ),
            }
            for p in projects
        ]
    }), 200


@api_bp.route("/invoices/<int:invoice_id>/pay", methods=["POST"])
@roles_required("client", "admin")
def initiate_invoice_payment(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    if invoice.status == "paid":
        return jsonify({"error": "Invoice is already paid"}), 400

    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
    client = razorpay.Client(auth=(key_id, key_secret))

    # Razorpay expects amounts in paise (1 INR = 100 paise)
    order_payload = {
        "amount": int(float(invoice.total_amount) * 100),
        "currency": invoice.currency,
        "receipt": f"inv_{invoice.invoice_number}",
        "notes": {"invoice_id": invoice.id},
    }

    order = client.order.create(data=order_payload)
    invoice.razorpay_order_id = order["id"]
    db.session.commit()

    return jsonify({
        "order_id": order["id"],
        "amount": order["amount"],
        "currency": order["currency"],
        "razorpay_key": key_id,
        "invoice_number": invoice.invoice_number,
    }), 200


@api_bp.route("/milestones/<int:milestone_id>/approve", methods=["POST"])
@roles_required("client", "admin")
def approve_milestone(milestone_id):
    user_id = session.get("user_id")
    user = User.query.get(user_id)
    milestone = Milestone.query.get_or_404(milestone_id)

    # Ensure client only approves milestones for their own projects
    if user.role == "client":
        if not user.customer_profile or milestone.project.customer_id != user.id:
            return jsonify(
                {"error": "Unauthorized to approve this milestone"}
            ), 403

    if milestone.status == "released":
        return jsonify(
            {"error": "Milestone already approved and released"}
        ), 400

    milestone.status = "released"
    milestone.released_at = get_ist_now()
    db.session.commit()

    return jsonify({
        "message": "Milestone signed off and released successfully",
        "milestone_id": milestone.id,
        "status": milestone.status,
        "released_at": milestone.released_at.isoformat(),
    }), 200