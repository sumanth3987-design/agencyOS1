"""AgencyOS Stripe Multi-Currency Gateway Blueprint."""

from decimal import Decimal
import json
import logging
import os
from typing import Any, cast

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

import stripe
from stripe import SignatureVerificationError

from app.extensions import csrf, db
from app.models import (
    ActivityLog,
    Invoice,
    PACKAGE_PRICES,
    Payment,
    Project,
    StripeWebhookEvent,
    get_ist_now,
)
from app.utils.currency import calculate_tax_rate, format_currency, get_package_price, to_minor_units
from app.utils.stripe_config import (
    STRIPE_PUBLISHABLE_KEY,
    STRIPE_WEBHOOK_SECRET,
    SUPPORTED_STRIPE_CURRENCIES,
    build_checkout_session_params,
    get_stripe_price_id,
)

logger = logging.getLogger(__name__)

stripe_bp = Blueprint("stripe", __name__)

# Configure Stripe API key with safe type annotation for Pylance
stripe_module = cast(Any, stripe)
stripe_module.api_key = os.getenv("STRIPE_SECRET_KEY", "")


# ---------------------------------------------------------------------------
# Checkout Session Creator
# ---------------------------------------------------------------------------

@stripe_bp.route("/payment/stripe/checkout/<int:project_id>", methods=["POST"])
@login_required
def create_stripe_checkout(project_id: int) -> Any:
    """Create a Stripe Checkout Session for an international project."""
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

    # Reuse existing open Stripe payment
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
        session = stripe_module.checkout.Session.create(**session_params)
    except Exception as exc:
        logger.error("Stripe checkout creation failed: %s", str(exc))
        return jsonify({
            "success": False,
            "message": "Unable to initiate Stripe checkout session.",
        }), 500

    session_id = session.id

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


# ---------------------------------------------------------------------------
# Stripe Webhook Handler (state-safe & idempotent)
# ---------------------------------------------------------------------------

@stripe_bp.route("/payment/stripe/webhook", methods=["POST"])
@csrf.exempt
def stripe_webhook() -> Any:
    """Handle Stripe webhook events with idempotency and state guards."""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")
    endpoint_secret = STRIPE_WEBHOOK_SECRET

    if not sig_header:
        return jsonify({"error": "Missing Stripe signature header"}), 400

    try:
        event = stripe_module.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except ValueError as exc:
        logger.warning("Stripe webhook: invalid payload: %s", str(exc))
        return jsonify({"error": "Invalid payload"}), 400
    except SignatureVerificationError as exc:
        logger.warning("Stripe webhook: invalid signature: %s", str(exc))
        return jsonify({"error": "Invalid signature"}), 400

    event_id = event["id"]
    event_type = event["type"]

    if StripeWebhookEvent.is_processed(event_id):
        return jsonify({"status": "ignored", "reason": "duplicate_event"}), 200

    if event_type == "checkout.session.completed":
        session_obj = event["data"]["object"]
        metadata = session_obj.get("metadata", {})
        project_id_str = metadata.get("project_id")
        amount_total = session_obj.get("amount_total", 0)
        currency = session_obj.get("currency", "").upper()
        payment_status = session_obj.get("payment_status", "")

        project_id = int(project_id_str) if project_id_str else None

        if project_id and payment_status == "paid":
            project = Project.query.get(project_id)
            if project:
                if project.status in ("Payment Pending", "Pending Assignment"):
                    payment = Payment.query.filter_by(
                        stripe_session_id=session_obj.get("id")
                    ).first()
                    if payment is None:
                        payment = Payment()
                        payment.project_id = project.id
                        payment.customer_id = project.customer_id
                        payment.stripe_session_id = session_obj.get("id")
                        payment.gateway_type = "stripe"
                        payment.amount = Decimal(str(amount_total)) / 100
                        payment.currency = currency
                        payment.status = "Created"
                        db.session.add(payment)

                    payment.status = "Paid"
                    payment.gateway_payment_id = session_obj.get("payment_intent")

                    invoice = Invoice.query.filter_by(project_id=project.id).first()
                    if invoice is None:
                        tax_rate = calculate_tax_rate(
                            client_country=(getattr(project, "billing_country", "") or ""),
                            currency=currency,
                        )
                        tax_amount = (
                            (payment.amount * tax_rate).quantize(Decimal("0.01"))
                            if tax_rate > 0 else Decimal("0.00")
                        )
                        invoice = Invoice()
                        invoice.project_id = project.id
                        invoice.customer_id = project.customer_id
                        invoice.gross_amount = payment.amount
                        invoice.tax_amount = tax_amount
                        invoice.currency = currency
                        invoice.status = "Paid"
                        db.session.add(invoice)
                    else:
                        invoice.status = "Paid"

                    project.status = "Paid"

                    ActivityLog.log_event(
                        project.id,
                        "STRIPE_PAYMENT_RECEIVED",
                        f"Stripe payment of {format_currency(payment.amount, currency)} received via Checkout session {session_obj.get('id')}.",
                        project.customer_id,
                        "success",
                    )

                    db.session.flush()

                    StripeWebhookEvent.mark_processed(
                        event_id=event_id,
                        event_type=event_type,
                        project_id=project.id,
                        payload=json.dumps(payload)[:5000] if payload else None,
                    )

                    db.session.commit()

                    logger.info(
                        "Stripe webhook: project #%s marked Paid via session %s",
                        project.id,
                        session_obj.get("id"),
                    )
                else:
                    StripeWebhookEvent.mark_processed(
                        event_id=event_id,
                        event_type=event_type,
                        project_id=project_id,
                        payload=json.dumps(payload)[:5000] if payload else None,
                    )
                    db.session.commit()
            else:
                StripeWebhookEvent.mark_processed(
                    event_id=event_id,
                    event_type=event_type,
                    project_id=project_id,
                    payload=json.dumps(payload)[:5000] if payload else None,
                )
                db.session.commit()
        else:
            StripeWebhookEvent.mark_processed(
                event_id=event_id,
                event_type=event_type,
                project_id=project_id,
                payload=json.dumps(payload)[:5000] if payload else None,
            )
            db.session.commit()

    else:
        StripeWebhookEvent.mark_processed(
            event_id=event_id,
            event_type=event_type,
            project_id=None,
            payload=json.dumps(payload)[:5000] if payload else None,
        )
        db.session.commit()

    return jsonify({"status": "success"}), 200


# ---------------------------------------------------------------------------
# Success / Cancel pages
# ---------------------------------------------------------------------------

@stripe_bp.route("/payment/stripe/success", methods=["GET"])
def stripe_success() -> Any:
    """Stripe Checkout success redirect page."""
    session_id = request.args.get("session_id")
    return jsonify({
        "status": "success",
        "message": "Payment session completed. Check your dashboard for updates.",
        "session_id": session_id,
    }), 200


@stripe_bp.route("/payment/stripe/cancel", methods=["GET"])
def stripe_cancel() -> Any:
    """Stripe Checkout cancel redirect page."""
    return jsonify({
        "status": "canceled",
        "message": "Payment was canceled. You can retry checkout from your dashboard.",
    }), 200


__all__ = ["stripe_bp"] 