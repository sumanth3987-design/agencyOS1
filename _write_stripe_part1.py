import json
import logging
import os
from decimal import Decimal

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models import (
    Invoice,
    Payment,
    Project,
    StripeWebhookEvent,
    ActivityLog,
    get_ist_now,
    PACKAGE_PRICES,
)
from app.utils.currency import calculate_tax_rate, format_currency, get_package_price, to_minor_units
from app.utils.stripe_config import (
    SUPPORTED_STRIPE_CURRENCIES,
    STRIPE_PUBLISHABLE_KEY,
    STRIPE_WEBHOOK_SECRET,
    build_checkout_session_params,
    get_stripe_price_id,
)

import stripe

logger = logging.getLogger(__name__)

stripe_bp = Blueprint("stripe", __name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")


# ---------------------------------------------------------------------------
# Checkout Session Creator
# ---------------------------------------------------------------------------

@stripe_bp.route("/payment/stripe/checkout/<int:project_id>", methods=["POST"])
@login_required
def create_stripe_checkout(project_id):
    """Create a Stripe Checkout Session for an international (USD/EUR/GBP) project."""

    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client", "admin"):
        return jsonify({"success": False, "message": "Customer access required."}), 403

    if user_role == "admin":
        project = Project.query.get(project_id)
    else:
        project = Project.query.filter_by(id=project_id, customer_id=current_user.id).first()

    if not project:
        return jsonify({"success": False, "message": "Project not found."}), 404

    # Payable state enforcement
    if project.status not in ("Payment Pending", "Pending Assignment"):
        return jsonify({
            "success": False,
            "message": "Project is not in a payable state.",
        }), 400

    # Already paid guard
    existing_paid = Payment.query.filter_by(
        project_id=project.id, status="Paid"
    ).first()
    if existing_paid is not None:
        return jsonify({"success": False, "message": "Project has already been paid."}), 400

    # Reuse an existing open Stripe payment without calling Stripe again
    existing_stripe = Payment.query.filter_by(
        project_id=project.id,
        gateway_type="stripe",
        status="Created",
    ).first()
    if existing_stripe is not None:
        return jsonify({
            "success": True,
            "session_id": existing_stripe.stripe_session_id,
            "amount": to_minor_units(existing_stripe.amount),
            "currency": existing_stripe.currency,
            "publishable_key": STRIPE_PUBLISHABLE_KEY,
        })

    data = request.get_json(silent=True) or {}
    package_name = data.get("package_name") or project.package
    currency = data.get("currency") or (project.currency or "USD")

    if currency not in SUPPORTED_STRIPE_CURRENCIES:
        return jsonify({
            "success": False,
            "message": f"Currency {currency} is not supported for Stripe checkout.",
        }), 400

    try:
        total_pkg = get_package_price(package_name, currency, PACKAGE_PRICES)
    except ValueError as exc:
        logger.warning("Cannot price project #%s for Stripe checkout: %s", project.id, exc)
        return jsonify({
            "success": False,
            "message": "This project package has no configured price in its billing currency.",
        }), 400

    milestone_num = data.get("milestone", 1)
    if milestone_num in (2, 3):
        amount = (total_pkg * Decimal("0.25")).quantize(Decimal("0.01"))
    elif milestone_num == 1:
        amount = (total_pkg * Decimal("0.50")).quantize(Decimal("0.01"))
    else:
        amount = total_pkg

    tax_rate = calculate_tax_rate(
        client_country=(getattr(project, "billing_country", "") or ""),
        currency=currency,
    )
    tax_amount = (amount * tax_rate).quantize(Decimal("0.01"))
    gross_amount = (amount + tax_amount).quantize(Decimal("0.01"))

    amount_minor = to_minor_units(gross_amount)

    base_url = current_app.config.get("APP_BASE_URL") or ""
    success_url = f"{base_url}/payment/stripe/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{base_url}/payment/stripe/cancel"

    try:
        session_params = build_checkout_session_params(
            project_id=project.id,
            package_name=package_name,
            currency=currency,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        session = stripe.checkout.Session.create(**session_params)
    except Exception as exc:
        logger.error("Stripe checkout creation failed: %s", str(exc))
        return jsonify({
            "success": False,
            "message": "Unable to initiate Stripe checkout session.",
        }), 500

    session_id = session.id

    # Persist a Created payment record before redirecting to Stripe
    payment = Payment()
    payment.project_id = project.id
    payment.customer_id = project.customer_id
    payment.stripe_session_id = session_id
    payment.gateway_type = "stripe"
    payment.amount = gross_amount
    payment.currency = currency
    payment.status = "Created"
    db.session.add(payment)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Could not save Stripe payment record for #%s", project.id)
        return jsonify({
            "success": False,
            "message": "Unable to create payment record.",
        }), 500

    return jsonify({
        "success": True,
        "session_id": session_id,
        "url": session.url,
        "amount": amount_minor,
        "currency": currency,
        "publishable_key": STRIPE_PUBLISHABLE_KEY,
    })