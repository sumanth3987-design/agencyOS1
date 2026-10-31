import logging
import os
import hmac
import hashlib

import requests
import razorpay

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from app.extensions import db, get_razorpay_client, limiter, csrf
from app.models import Project, Payment, Invoice, Milestone, PACKAGE_PRICES, get_ist_now
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

logger = logging.getLogger(__name__)

def _reconcile_created_payment(project, customer_id, razorpay_order_id, amount):
    """Return a safe existing Created payment after a persistence conflict.

    Matching is intentionally strict: the reconciled payment must belong to
    the same project and customer, remain Created, and have the same amount.
    """
    reconciled = (
        Payment.query
        .filter_by(
            project_id=project.id,
            customer_id=customer_id,
            status="Created",
        )
        .order_by(Payment.created_at.desc())
        .first()
    )

    if (
        reconciled is not None
        and reconciled.razorpay_order_id
        and reconciled.amount == amount
    ):
        return reconciled

    reconciled = Payment.query.filter_by(
        razorpay_order_id=razorpay_order_id
    ).first()

    if (
        reconciled is not None
        and reconciled.project_id == project.id
        and reconciled.customer_id == customer_id
        and reconciled.status == "Created"
        and reconciled.amount == amount
    ):
        return reconciled

    return None
payments_bp = Blueprint("payments", __name__)
razorpay_client = get_razorpay_client()
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")


@payments_bp.route(
    "/payment/create-order/<int:project_id>",
    methods=["POST"]
)
@limiter.limit("10 per minute")
@login_required
def create_payment_order(project_id):

    if current_user.role != "customer":

        return jsonify({
            "success": False,
            "message": "Customer access required."
        }), 403

    if razorpay_client is None:

        return jsonify({
            "success": False,
            "message": "Payment service unavailable."
        }), 503

    project = Project.query.filter_by(
        id=project_id,
        customer_id=current_user.id
    ).first()

    if not project:

        return jsonify({
            "success": False,
            "message": "Project not found."
        }), 404

    amount = PACKAGE_PRICES.get(
        project.package
    )

    if not amount:

        return jsonify({
            "success": False,
            "message": "Invalid package amount."
        }), 400

    existing_paid_payment = (
        Payment.query
        .filter_by(
            project_id=project.id,
            customer_id=current_user.id,
            status="Paid"
        )
        .first()
    )

    if existing_paid_payment:

        return jsonify({
            "success": False,
            "message": "This project has already been paid."
        }), 400

    if project.status not in ("Payment Pending", "Pending Assignment"):

        return jsonify({
            "success": False,
            "message": "Project is not in a payable state."
        }), 400

    amount_paise = int(
        amount * 100
    )

    # Idempotency: reuse a still-open local order instead of creating
    # duplicate Razorpay orders for the same project/customer.
    existing_active_payment = (
        Payment.query
        .filter_by(
            project_id=project.id,
            customer_id=current_user.id,
            status="Created",
        )
        .order_by(Payment.created_at.desc())
        .first()
    )

    if (
        existing_active_payment is not None
        and existing_active_payment.razorpay_order_id
        and existing_active_payment.amount == amount
    ):

        return jsonify({
            "success": True,
            "order_id": existing_active_payment.razorpay_order_id,
            "amount": amount_paise,
            "currency": "INR",
            "key_id": RAZORPAY_KEY_ID,
        })

    try:

        order = razorpay_client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "receipt": f"agencyos_project_{project.id}",
            "notes": {
                "project_id": str(project.id),
                "customer_id": str(current_user.id)
            }
        })

    except (
        razorpay.errors.BadRequestError,
        razorpay.errors.GatewayError,
        razorpay.errors.ServerError,
        razorpay.errors.SignatureVerificationError,
        requests.exceptions.RequestException,
        ConnectionError,
        TimeoutError,
        OSError,
    ) as exc:
        logger.error(
            "Razorpay order creation failed: %s",
            type(exc).__name__,
        )
        return jsonify({
            "success": False,
            "message": "Unable to create Razorpay order."
        }), 500

    # Prevent duplicate Razorpay order IDs from being recorded locally.
    existing_order_payment = Payment.query.filter_by(
        razorpay_order_id=order["id"]
    ).first()

    if existing_order_payment is not None:

        if (
            existing_order_payment.project_id == project.id
            and existing_order_payment.customer_id == current_user.id
            and existing_order_payment.status == "Created"
            and existing_order_payment.amount == amount
        ):

            return jsonify({
                "success": True,
                "order_id": existing_order_payment.razorpay_order_id,
                "amount": amount_paise,
                "currency": "INR",
                "key_id": RAZORPAY_KEY_ID,
            })

        return jsonify({
            "success": False,
            "message": "Payment order already exists."
        }), 400

    payment = Payment(
        project_id=project.id,
        customer_id=current_user.id,
        razorpay_order_id=order["id"],
        amount=amount,
        currency="INR",
        status="Created"
    )

    try:

        db.session.add(
            payment
        )

        db.session.commit()

    except IntegrityError:

        db.session.rollback()

        reconciled_payment = _reconcile_created_payment(
            project,
            current_user.id,
            order["id"],
            amount,
        )

        if reconciled_payment is not None:

            return jsonify({
                "success": True,
                "order_id": reconciled_payment.razorpay_order_id,
                "amount": amount_paise,
                "currency": "INR",
                "key_id": RAZORPAY_KEY_ID,
            })

        logger.error(
            "Razorpay order could not be reconciled after local "
            "persistence failure.",
            extra={
                "razorpay_order_id": order["id"],
                "project_id": project.id,
                "customer_id": current_user.id,
            },
        )

        return jsonify({
            "success": False,
            "message": "Unable to create payment order."
        }), 400

    except SQLAlchemyError as exc:

        db.session.rollback()

        logger.error(
            "Payment order database write failed: %s",
            type(exc).__name__,
            extra={
                "razorpay_order_id": order["id"],
                "project_id": project.id,
                "customer_id": current_user.id,
            },
        )

        return jsonify({
            "success": False,
            "message": "Unable to create payment order."
        }), 500

    return jsonify({
        "success": True,
        "order_id": order["id"],
        "amount": amount_paise,
        "currency": "INR",
        "key_id": RAZORPAY_KEY_ID
    })


# =========================================================
# VERIFY RAZORPAY PAYMENT
# =========================================================

@payments_bp.route(
    "/payment/verify",
    methods=["POST"]
)
@limiter.limit("20 per minute")
@login_required
def verify_payment():

    if current_user.role != "customer":

        return jsonify({
            "success": False,
            "message": "Customer access required."
        }), 403

    if razorpay_client is None:

        return jsonify({
            "success": False,
            "message": "Payment service unavailable."
        }), 503

    data = request.get_json(
        silent=True
    ) or {}

    razorpay_order_id = data.get(
        "razorpay_order_id",
        ""
    ).strip()

    razorpay_payment_id = data.get(
        "razorpay_payment_id",
        ""
    ).strip()

    razorpay_signature = data.get(
        "razorpay_signature",
        ""
    ).strip()

    if not (
        razorpay_order_id
        and razorpay_payment_id
        and razorpay_signature
    ):

        return jsonify({
            "success": False,
            "message": "Missing payment verification data."
        }), 400

    payment = (
        Payment.query
        .filter_by(
            razorpay_order_id=razorpay_order_id,
            customer_id=current_user.id
        )
        .first()
    )

    if not payment:

        return jsonify({
            "success": False,
            "message": "Payment record not found."
        }), 404

    # Prevent the same Razorpay payment ID from being applied to more
    # than one local payment record.
    duplicate_payment = (
        Payment.query
        .filter(
            Payment.razorpay_payment_id == razorpay_payment_id,
            Payment.id != payment.id,
        )
        .first()
    )

    if duplicate_payment is not None:

        return jsonify({
            "success": False,
            "message": "Payment ID already used."
        }), 400

    # Idempotency: already-paid payments return success without
    # re-calling Razorpay or mutating state on repeat callbacks.
    if payment.status == "Paid":

        return jsonify({
            "success": True,
            "message": "Payment already verified.",
            "project_id": payment.project_id
        })

    if payment.status != "Created":

        return jsonify({
            "success": False,
            "message": "Payment is not in a verifiable state."
        }), 400

    project = Project.query.filter_by(
        id=payment.project_id,
        customer_id=current_user.id
    ).first()

    if not project:

        return jsonify({
            "success": False,
            "message": "Project not found."
        }), 404

    if project.status not in ("Payment Pending", "Pending Assignment"):

        return jsonify({
            "success": False,
            "message": "Project is not in a payable state."
        }), 400

    try:

        razorpay_client.utility.verify_payment_signature({
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature
        })

    except (
        razorpay.errors.SignatureVerificationError,
        razorpay.errors.BadRequestError,
        razorpay.errors.GatewayError,
        razorpay.errors.ServerError,
        requests.exceptions.RequestException,
        ConnectionError,
        TimeoutError,
        OSError,
    ) as exc:
        logger.warning(
            "Razorpay signature verification failed: %s",
            type(exc).__name__,
        )
        return jsonify({
            "success": False,
            "message": "Payment signature verification failed."
        }), 400


    try:

        razorpay_order = (
            razorpay_client.order.fetch(
                razorpay_order_id
            )
        )

        razorpay_payment = (
            razorpay_client.payment.fetch(
                razorpay_payment_id
            )
        )

    except (
        razorpay.errors.BadRequestError,
        razorpay.errors.GatewayError,
        razorpay.errors.ServerError,
        razorpay.errors.SignatureVerificationError,
        requests.exceptions.RequestException,
        ConnectionError,
        TimeoutError,
        OSError,
    ) as exc:
        logger.error(
            "Razorpay payment fetch failed: %s",
            type(exc).__name__,
        )
        return jsonify({
            "success": False,
            "message":
                "Unable to verify payment with Razorpay."
        }), 500

    expected_amount = int(
        payment.amount * 100
    )

    actual_order_amount = int(
        razorpay_order.get(
            "amount",
            0
        )
    )

    actual_payment_amount = int(
        razorpay_payment.get(
            "amount",
            0
        )
    )

    order_currency = (
        razorpay_order.get(
            "currency"
        )
    )

    payment_currency = (
        razorpay_payment.get(
            "currency"
        )
    )

    captured = (
        razorpay_payment.get(
            "status"
        )
        == "captured"
    )

    order_id_matches = (
        razorpay_order.get("id") == razorpay_order_id
        and razorpay_payment.get("order_id") == razorpay_order_id
        and razorpay_order_id == payment.razorpay_order_id
    )

    amount_valid = (
        actual_order_amount
        == expected_amount
        and actual_payment_amount
        == expected_amount
    )

    currency_valid = (
        order_currency == payment.currency
        and payment_currency == payment.currency
        and payment.currency == "INR"
    )

    if not (
        captured
        and order_id_matches
        and amount_valid
        and currency_valid
    ):

        return jsonify({
            "success": False,
            "message":
                "Payment verification failed."
        }), 400

    try:

        updated_rows = (
            Payment.query
            .filter_by(
                id=payment.id,
                status="Created"
            )
            .update(
                {
                    "razorpay_payment_id": razorpay_payment_id,
                    "status": "Paid",
                    "paid_at": get_ist_now(),
                },
                synchronize_session=False
            )
        )

        if updated_rows != 1:

            db.session.rollback()

            refreshed_payment = Payment.query.get(
                payment.id
            )

            if refreshed_payment and refreshed_payment.status == "Paid":

                return jsonify({
                    "success": True,
                    "message": "Payment already verified.",
                    "project_id": refreshed_payment.project_id
                })

            return jsonify({
                "success": False,
                "message": "Payment verification failed."
            }), 400

        Project.query.filter_by(
            id=project.id
        ).update(
            {"status": "Pending Assignment"},
            synchronize_session=False
        )

        db.session.commit()

    except IntegrityError:

        db.session.rollback()

        refreshed_payment = Payment.query.get(
            payment.id
        )

        if refreshed_payment and refreshed_payment.status == "Paid":

            return jsonify({
                "success": True,
                "message": "Payment already verified.",
                "project_id": refreshed_payment.project_id
            })

        duplicate_payment = (
            Payment.query
            .filter(
                Payment.razorpay_payment_id == razorpay_payment_id,
                Payment.id != payment.id,
            )
            .first()
        )

        if duplicate_payment is not None:

            return jsonify({
                "success": False,
                "message": "Payment ID already used."
            }), 400

        return jsonify({
            "success": False,
            "message": "Payment verification failed."
        }), 400

    except SQLAlchemyError as exc:

        db.session.rollback()

        logger.error(
            "Payment verification database write failed: %s",
            type(exc).__name__,
        )

        return jsonify({
            "success": False,
            "message": "Payment verification failed."
        }), 500

    return jsonify({
        "success": True,
        "message": "Payment verified successfully.",
        "project_id": project.id
    })


# =========================================================
# RAZORPAY WEBHOOK (HMAC Signature Verification)
# =========================================================

@payments_bp.route("/webhook", methods=["POST"])
@csrf.exempt
def razorpay_webhook():
    """Verify Razorpay webhook signature and acknowledge payment events.

    Only requests signed with RAZORPAY_WEBHOOK_SECRET are accepted,
    preventing fake callbacks from marking invoices as paid.
    """
    webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    received_sig = request.headers.get("X-Razorpay-Signature")

    if not webhook_secret:
        current_app.logger.error("RAZORPAY_WEBHOOK_SECRET is not configured.")
        return jsonify({"error": "Server configuration missing"}), 500

    if not received_sig:
        return jsonify({"error": "Missing signature header"}), 400

    raw_body = request.get_data()
    expected_sig = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, received_sig):
        current_app.logger.warning(
            "Unauthorized webhook attempt detected - signature mismatch."
        )
        return jsonify({"error": "Invalid signature"}), 403

    event_payload = request.get_json(silent=True) or {}
    event_name = event_payload.get("event")

    # Handle payment capture with atomic idempotency
    if event_name == "payment.captured":
        payment_entity = (
            event_payload.get("payload", {}).get("payment", {}).get("entity", {})
        )
        razorpay_payment_id = payment_entity.get("id")
        order_id = payment_entity.get("order_id")
        amount_paid = int(float(payment_entity.get("amount", 0)) / 100.0)
        currency = payment_entity.get("currency", "INR")
        payment_method = payment_entity.get("method", "unknown")

        # 1. Idempotency Check: prevent duplicate processing
        existing_payment = Payment.query.filter_by(
            razorpay_payment_id=razorpay_payment_id
        ).first()
        if existing_payment:
            return jsonify(
                {"status": "ignored", "message": "Payment already recorded"}
            ), 200

        # 2. Atomic Database Transaction
        try:
            new_payment = Payment(
                project_id=None,
                customer_id=None,
                razorpay_order_id=order_id,
                razorpay_payment_id=razorpay_payment_id,
                amount=amount_paid,
                currency=currency,
                status="Paid",
                paid_at=get_ist_now(),
            )
            db.session.add(new_payment)

            # Update matching invoice if linked
            if order_id:
                invoice = Invoice.query.filter_by(
                    razorpay_order_id=order_id
                ).first()
                if invoice:
                    invoice.status = "paid"
                    invoice.paid_at = get_ist_now()

                    # Mark linked project milestones as funded
                    if invoice.project_id:
                        milestones = Milestone.query.filter_by(
                            project_id=invoice.project_id,
                            status="pending",
                        ).all()
                        for m in milestones:
                            m.status = "funded"

            db.session.commit()
            return jsonify(
                {
                    "status": "success",
                    "message": "Payment captured and invoice updated",
                }
            ), 200

        except Exception as exc:
            db.session.rollback()
            current_app.logger.error(
                "Failed to process payment capture: %s", str(exc)
            )
            return jsonify({"error": "Database transaction failed"}), 500

    # Acknowledge receipt securely
    return jsonify({"status": "ignored", "message": "Unhandled event type"}), 200


# =========================================================
# PAYMENT PAGE
# =========================================================

@payments_bp.route(
    "/payment/<int:project_id>"
)
@login_required
def payment_page(project_id):

    if current_user.role != "customer":

        return redirect(
            url_for("dashboard")
        )

    project = Project.query.filter_by(
        id=project_id,
        customer_id=current_user.id
    ).first()

    if not project:

        flash(
            "Project not found."
        )

        return redirect(
            url_for("dashboard")
        )

    amount = PACKAGE_PRICES.get(
        project.package,
        0
    )

    payment = (
        Payment.query
        .filter_by(
            project_id=project.id,
            customer_id=current_user.id
        )
        .order_by(
            Payment.created_at.desc()
        )
        .first()
    )

    if payment and payment.status == "Paid":

        return redirect(
            url_for(
                "project_details",
                project_id=project.id
            )
        )

    return render_template(
        "payment.html",
        project=project,
        amount=amount,
        razorpay_key_id=RAZORPAY_KEY_ID
    )


# =========================================================
# SERVICES API
# =========================================================


