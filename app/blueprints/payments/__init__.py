# Save this directly into your file: app/blueprints/payments/__init__.py

import logging
import os
import hmac
import hashlib
import secrets
from datetime import datetime

import requests
import razorpay

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from app.extensions import db, get_razorpay_client, limiter, csrf
from app.models import Project, Payment, Invoice, Milestone, PACKAGE_PRICES, get_ist_now
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

logger = logging.getLogger(__name__)

payments_bp = Blueprint("payments", __name__)
razorpay_client = get_razorpay_client()
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")


# =========================================================
# PAYMENT CHECKOUT PAGE (SUPPORTS MILESTONE 1, 2, 3)
# =========================================================

@payments_bp.route("/payment/<int:project_id>")
@login_required
def payment_page(project_id):
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client", "admin"):
        flash("Customer access required to view checkout.", "warning")
        return redirect(url_for("customer.dashboard"))

    if user_role == "admin":
        project = Project.query.get(project_id)
    else:
        project = Project.query.filter_by(id=project_id, customer_id=current_user.id).first()

    if not project:
        flash("Project not found or not accessible from your account.", "warning")
        return redirect(url_for("customer.dashboard"))

    milestone_num = request.args.get("milestone", type=int)
    custom_amount_paise = request.args.get("amount", type=int)
    total_pkg_amount = PACKAGE_PRICES.get(project.package, 48000)

    if custom_amount_paise and custom_amount_paise > 0:
        amount = int(custom_amount_paise / 100)
    elif milestone_num in (2, 3):
        amount = int(total_pkg_amount * 0.25)
    else:
        amount = int(total_pkg_amount * 0.50) if project.package in PACKAGE_PRICES else total_pkg_amount

    return render_template(
        "payment.html",
        project=project,
        amount=amount * 100,
        gross_val=amount,
        milestone_num=milestone_num or 1,
        razorpay_key_id=RAZORPAY_KEY_ID
    )


# =========================================================
# CREATE PAYMENT ORDER
# =========================================================

@payments_bp.route("/payment/create-order/<int:project_id>", methods=["POST"])
@limiter.limit("10 per minute")
@login_required
def create_payment_order(project_id):
    if razorpay_client is None:
        return jsonify({"success": False, "message": "Payment gateway unavailable."}), 503

    user_role = getattr(current_user, "role", "").lower()
    if user_role == "admin":
        project = Project.query.get(project_id)
    else:
        project = Project.query.filter_by(id=project_id, customer_id=current_user.id).first()

    if not project:
        return jsonify({"success": False, "message": "Project not found."}), 404

    data = request.get_json(silent=True) or {}
    milestone_num = data.get("milestone", 1)
    
    total_pkg = PACKAGE_PRICES.get(project.package, 48000)
    if milestone_num in (2, 3):
        amount = int(total_pkg * 0.25)
    else:
        amount = int(total_pkg * 0.50)

    amount_paise = int(amount * 100)

    try:
        order = razorpay_client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "receipt": f"proj_{project.id}_m{milestone_num}",
            "notes": {
                "project_id": str(project.id),
                "customer_id": str(project.customer_id),
                "milestone": str(milestone_num)
            }
        })
    except Exception as exc:
        logger.error("Razorpay order error: %s", str(exc))
        return jsonify({"success": False, "message": "Unable to initialize Razorpay order."}), 500

    payment = Payment(
        project_id=project.id,
        customer_id=project.customer_id,
        razorpay_order_id=order["id"],
        amount=amount,
        currency="INR",
        status="Created"
    )
    db.session.add(payment)
    db.session.commit()

    return jsonify({
        "success": True,
        "order_id": order["id"],
        "amount": amount_paise,
        "currency": "INR",
        "key_id": RAZORPAY_KEY_ID
    })


# =========================================================
# CRASH-PROOF VERIFY PAYMENT ROUTE (WITH 45/55 SPLIT)
# =========================================================

@payments_bp.route("/payment/verify", methods=["POST"])
@payments_bp.route("/api/billing/verify-payment", methods=["POST"])
@limiter.limit("20 per minute")
@login_required
def verify_payment():
    # 1. Reset any stale/aborted database session
    db.session.rollback()

    data = request.get_json(silent=True) or {}
    razorpay_order_id = data.get("razorpay_order_id", "").strip()
    razorpay_payment_id = data.get("razorpay_payment_id", "").strip()
    razorpay_signature = data.get("razorpay_signature", "").strip()

    if not (razorpay_order_id and razorpay_payment_id and razorpay_signature):
        return jsonify({"success": False, "message": "Missing verification parameters."}), 400

    payment = Payment.query.filter_by(razorpay_order_id=razorpay_order_id).first()
    if not payment:
        return jsonify({"success": False, "message": "Payment record not found."}), 404

    if payment.status == "Paid":
        return jsonify({
            "success": True,
            "message": "Payment already verified.",
            "project_id": payment.project_id
        })

    # 2. Cryptographic Signature Verification
    try:
        razorpay_client.utility.verify_payment_signature({
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature
        })
    except Exception as exc:
        logger.warning("Signature verification failed: %s", str(exc))
        return jsonify({"success": False, "message": "Invalid payment signature."}), 400

    # 3. Database State Update (with Talent Payout & Agency Profit)
    try:
        payment.razorpay_payment_id = razorpay_payment_id
        payment.status = "Paid"
        payment.paid_at = get_ist_now()

        # Update Project status to active
        if payment.project_id:
            project = Project.query.get(payment.project_id)
            if project:
                project.status = "In Progress"

        # Calculate exact 45/55 split and statutory GST
        amt = float(payment.amount or 24000)
        talent_share = int(amt * 0.45)
        agency_share = int(amt * 0.55)
        tax_val = round(amt - (amt / 1.18), 2)
        unique_inv_number = f"INV-2026-{payment.project_id or 1:04d}-{secrets.token_hex(2).upper()}"

        # Safe GST Invoice handling
        invoice = None
        if payment.project_id:
            invoice = Invoice.query.filter_by(project_id=payment.project_id).first()

        if invoice:
            invoice.status = "Paid"
            if hasattr(invoice, "paid_at"):
                invoice.paid_at = get_ist_now()
            if hasattr(invoice, "razorpay_order_id") and not invoice.razorpay_order_id:
                invoice.razorpay_order_id = razorpay_order_id
            if hasattr(invoice, "talent_payout") and not invoice.talent_payout:
                invoice.talent_payout = talent_share
            if hasattr(invoice, "agency_profit") and not invoice.agency_profit:
                invoice.agency_profit = agency_share
        else:
            new_inv = Invoice()
            if hasattr(new_inv, "invoice_number"):
                new_inv.invoice_number = unique_inv_number
            if hasattr(new_inv, "project_id"):
                new_inv.project_id = payment.project_id
            if hasattr(new_inv, "customer_id"):
                new_inv.customer_id = payment.customer_id
            if hasattr(new_inv, "client_id"):
                new_inv.client_id = payment.customer_id
            if hasattr(new_inv, "gross_amount"):
                new_inv.gross_amount = amt
            if hasattr(new_inv, "amount"):
                new_inv.amount = amt
            if hasattr(new_inv, "total_amount"):
                new_inv.total_amount = amt
            if hasattr(new_inv, "talent_payout"):
                new_inv.talent_payout = talent_share
            if hasattr(new_inv, "agency_profit"):
                new_inv.agency_profit = agency_share
            if hasattr(new_inv, "tax_amount"):
                new_inv.tax_amount = tax_val
            if hasattr(new_inv, "tax"):
                new_inv.tax = tax_val
            if hasattr(new_inv, "status"):
                new_inv.status = "Paid"
            if hasattr(new_inv, "payout_status"):
                new_inv.payout_status = "Pending Approval"
            if hasattr(new_inv, "paid_at"):
                new_inv.paid_at = get_ist_now()
            if hasattr(new_inv, "razorpay_order_id"):
                new_inv.razorpay_order_id = razorpay_order_id

            db.session.add(new_inv)

        # Unlock any linked pending milestones
        if payment.project_id:
            try:
                Milestone.query.filter_by(project_id=payment.project_id, status="pending").update(
                    {"status": "funded"}, synchronize_session=False
                )
            except Exception:
                pass

        db.session.commit()
        logger.info(f"Payment {razorpay_payment_id} verified successfully for project #{payment.project_id}")

        return jsonify({
            "success": True,
            "message": "Payment verified successfully.",
            "project_id": payment.project_id
        })

    except Exception as exc:
        db.session.rollback()
        logger.error(f"Database write failure in verify_payment: {str(exc)}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Database write failure: {str(exc)}"
        }), 500


# =========================================================
# ASYNC RAZORPAY WEBHOOK
# =========================================================

@payments_bp.route("/webhook", methods=["POST"])
@payments_bp.route("/api/billing/webhook", methods=["POST"])
@csrf.exempt
def razorpay_webhook():
    webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    received_sig = request.headers.get("X-Razorpay-Signature")

    if not webhook_secret or not received_sig:
        return jsonify({"error": "Missing signature"}), 400

    raw_body = request.get_data()
    expected_sig = hmac.new(webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_sig, received_sig):
        return jsonify({"error": "Signature mismatch"}), 403

    event_payload = request.get_json(silent=True) or {}
    if event_payload.get("event") == "payment.captured":
        entity = event_payload.get("payload", {}).get("payment", {}).get("entity", {})
        pid = entity.get("id")
        oid = entity.get("order_id")

        existing = Payment.query.filter_by(razorpay_payment_id=pid).first()
        if existing and existing.status == "Paid":
            return jsonify({"status": "ignored"}), 200

        order_payment = Payment.query.filter_by(razorpay_order_id=oid).first()
        if order_payment:
            order_payment.razorpay_payment_id = pid
            order_payment.status = "Paid"
            order_payment.paid_at = get_ist_now()
            if order_payment.project_id:
                Project.query.filter_by(id=order_payment.project_id).update({"status": "In Progress"})
            db.session.commit()

    return jsonify({"status": "success"}), 200