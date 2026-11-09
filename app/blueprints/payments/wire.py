"""AgencyOS Offline Wire Transfer Payment Gateway Blueprint."""

from decimal import Decimal
import logging
from typing import Any

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models import ActivityLog, PACKAGE_PRICES, Payment, Project
from app.utils.currency import get_package_price, sanitize_currency

logger = logging.getLogger(__name__)

wire_bp = Blueprint("wire_bp", __name__, url_prefix="/payment/wire")


@wire_bp.route("/select/<int:project_id>", methods=["POST"])
@login_required
def select_wire_transfer(project_id: int) -> Any:
    """Allows a client or admin to select Wire Transfer as the payment method."""
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client", "admin"):
        return jsonify({"error": "Unauthorized access."}), 403

    project = Project.query.get_or_404(project_id)

    if user_role != "admin" and project.customer_id != current_user.id:
        return jsonify({"error": "Unauthorized access to project."}), 403

    if project.status not in ("Payment Pending", "Pending Assignment"):
        return jsonify({"error": "Project is not in a payable state."}), 400

    existing_paid = Payment.query.filter_by(project_id=project.id, status="Paid").first()
    if existing_paid:
        return jsonify({"error": "A paid payment already exists for this project."}), 400

    try:
        currency = sanitize_currency(project.currency)
        total_pkg_amount = get_package_price(project.package, currency, PACKAGE_PRICES)
        advance_deposit_amount = (total_pkg_amount * Decimal("0.50")).quantize(Decimal("0.01"))

        payment = Payment.query.filter_by(project_id=project.id, gateway_type="wire").first()
        if not payment:
            payment = Payment()
            payment.project_id = project.id
            payment.customer_id = project.customer_id
            payment.gateway_type = "wire"
            payment.status = "awaiting_wire"
            payment.currency = currency
            payment.amount = advance_deposit_amount
            payment.razorpay_order_id = f"wire_inv_{project.id}"
            db.session.add(payment)
        else:
            payment.status = "awaiting_wire"
            payment.amount = advance_deposit_amount
            payment.currency = currency

        project.status = "Payment Pending"

        ActivityLog.log_event(
            project_id=project.id,
            action="WIRE_PAYMENT_SELECTED",
            description=f"Client selected offline wire transfer for advance deposit of {currency} {advance_deposit_amount}.",
            user_id=current_user.id,
            category="info",
        )

        db.session.commit()

        wire_instructions = {
            "bank_name": current_app.config.get("WIRE_BANK_NAME", "Global Business Account"),
            "account_name": current_app.config.get("WIRE_ACCOUNT_NAME", "AgencyOS Technologies"),
            "account_number": current_app.config.get("WIRE_ACCOUNT_NUMBER", "XXXX-XXXX-XXXX"),
            "swift_bic": current_app.config.get("WIRE_SWIFT_BIC", "HDFCINBBXXX"),
            "iban": current_app.config.get("WIRE_IBAN", "IN37HDFC0000..."),
            "reference_code": f"AGENCYOS-PRJ-{project.id}",
            "amount_due": str(advance_deposit_amount),
            "currency": currency,
        }

        return jsonify({
            "message": "Wire transfer selected successfully. Please transfer funds using the instructions provided.",
            "instructions": wire_instructions,
        }), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error("Wire selection error for project %s: %s", project_id, str(e), exc_info=True)
        return jsonify({"error": "Failed to initialize wire transfer processing."}), 500


@wire_bp.route("/admin/reconcile/<int:project_id>", methods=["POST"])
@login_required
def admin_reconcile_wire(project_id: int) -> Any:
    """Admin-only endpoint to verify incoming wire funds and mark payment as Paid."""
    if getattr(current_user, "role", "").lower() != "admin":
        return jsonify({"error": "Admin privileges required."}), 403

    project = Project.query.get_or_404(project_id)
    payment = Payment.query.filter_by(project_id=project.id, gateway_type="wire").first()

    if not payment or payment.status != "awaiting_wire":
        return jsonify({"error": "No pending wire transfer found for this project."}), 400

    try:
        from app.models import get_ist_now
        payment.status = "Paid"
        payment.paid_at = get_ist_now()
        payment.gateway_payment_id = f"WIRE_REC_{project.id}_{get_ist_now().strftime('%Y%m%d%H%M')}"
        project.status = "In Progress"

        ActivityLog.log_event(
            project_id=project.id,
            action="WIRE_PAYMENT_RECONCILED",
            description=f"Wire transfer for project #{project.id} reconciled and marked as Paid by Admin.",
            user_id=current_user.id,
            category="success",
        )

        db.session.commit()

        return jsonify({
            "message": f"Wire transfer for project #{project_id} successfully reconciled and marked as Paid."
        }), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error("Wire reconciliation error for project %s: %s", project_id, str(e), exc_info=True)
        return jsonify({"error": "Failed to reconcile wire transfer."}), 500