# Save this directly into your file: app/blueprints/payments/__init__.py

import logging
import os
import hmac
import hashlib
import secrets
from datetime import datetime
from decimal import Decimal
from typing import Protocol, cast

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app.extensions import db, get_razorpay_client, limiter, csrf
from app.models import (
    Project,
    ProjectAddOn,
    Payment,
    Invoice,
    Milestone,
    AgencySetting,
    PACKAGE_PRICES,
    RazorpaySubscriptionPlan,
    RazorpaySubscription,
    SubscriptionCharge,
    ActivityLog,
    get_ist_now,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from app.utils.currency import format_currency, get_package_price, to_minor_units

logger = logging.getLogger(__name__)


class RazorpayOrder(Protocol):
    def create(self, data: dict[str, object]) -> dict[str, object]: ...

    def fetch(self, order_id: str) -> dict[str, object]: ...


class RazorpayPayment(Protocol):
    def fetch(self, payment_id: str) -> dict[str, object]: ...


class RazorpayPlan(Protocol):
    def create(self, data: dict[str, object]) -> dict[str, object]: ...


class RazorpaySubscriptionResource(Protocol):
    def create(self, data: dict[str, object]) -> dict[str, object]: ...


class RazorpayUtility(Protocol):
    def verify_payment_signature(self, data: dict[str, str]) -> None: ...


class RazorpayClient(Protocol):
    order: RazorpayOrder
    payment: RazorpayPayment
    plan: RazorpayPlan
    subscription: RazorpaySubscriptionResource
    utility: RazorpayUtility


payments_bp = Blueprint("payments", __name__)
razorpay_client: RazorpayClient | None = cast(
    RazorpayClient | None, get_razorpay_client()
)
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
STARTER_RETAINER_PACKAGE = "Starter Retainer"
STARTER_RETAINER_AMOUNT = 14999
STARTER_RETAINER_CODE = "starter_seo_retainer"
OPEN_SUBSCRIPTION_STATUSES = (
    "initializing",
    "created",
    "authenticated",
    "active",
    "pending",
    "halted",
    "paused",
    "resumed",
)
SUBSCRIPTION_STATUS_EVENTS = {
    "subscription.authenticated": "authenticated",
    "subscription.activated": "active",
    "subscription.pending": "pending",
    "subscription.halted": "halted",
    "subscription.cancelled": "cancelled",
    "subscription.completed": "completed",
    "subscription.paused": "paused",
    "subscription.resumed": "resumed",
}


def _create_payment_record(
    project_id: int,
    customer_id: int,
    order_id: str,
    amount: Decimal,
    currency: str,
    addon_id: int | None = None,
) -> Payment:
    payment = Payment()
    payment.project_id = project_id
    payment.customer_id = customer_id
    payment.addon_id = addon_id
    payment.razorpay_order_id = order_id
    payment.amount = amount
    payment.currency = currency
    payment.status = "Created"
    return payment


def _get_order_id(order: dict[str, object]) -> str:
    order_id = order.get("id")
    if not isinstance(order_id, str) or not order_id:
        raise ValueError("Razorpay order response is missing its order ID.")
    return order_id


def _get_gateway_amount(payment_data: dict[str, object]) -> int:
    amount = payment_data.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, str)):
        return -1
    try:
        return int(amount)
    except ValueError:
        return -1


def _next_month_start_at() -> datetime:
    now = get_ist_now()
    if now.month == 12:
        year, month = now.year + 1, 1
    else:
        year, month = now.year, now.month + 1
    return now.replace(
        year=year,
        month=month,
        day=1,
        hour=9,
        minute=0,
        second=0,
        microsecond=0,
    )


def _ensure_starter_retainer_plan() -> RazorpaySubscriptionPlan:
    plan = RazorpaySubscriptionPlan.query.filter_by(
        code=STARTER_RETAINER_CODE
    ).with_for_update().first()
    if plan is None:
        plan = RazorpaySubscriptionPlan()
        plan.code = STARTER_RETAINER_CODE
        plan.name = "Starter SEO Retainer"
        plan.amount = STARTER_RETAINER_AMOUNT
        plan.currency = "INR"
        db.session.add(plan)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            plan = RazorpaySubscriptionPlan.query.filter_by(
                code=STARTER_RETAINER_CODE
            ).with_for_update().first()
            if plan is None:
                raise
    if plan.amount != STARTER_RETAINER_AMOUNT or plan.currency != "INR":
        raise ValueError("Stored Starter Retainer plan terms do not match.")

    if plan.razorpay_plan_id:
        return plan
    if razorpay_client is None:
        raise RuntimeError("Razorpay client is unavailable.")

    response = razorpay_client.plan.create({
        "period": "monthly",
        "interval": 1,
        "item": {
            "name": plan.name,
            "amount": STARTER_RETAINER_AMOUNT * 100,
            "currency": "INR",
            "description": "Monthly SEO ranking engine and optimization",
        },
    })
    gateway_plan_id = response.get("id")
    if not isinstance(gateway_plan_id, str) or not gateway_plan_id:
        raise ValueError("Razorpay plan response is missing its plan ID.")

    plan.razorpay_plan_id = gateway_plan_id
    db.session.commit()
    return plan


def _subscription_checkout_response(subscription: RazorpaySubscription):
    settings = db.session.get(AgencySetting, 1)
    return jsonify({
        "success": True,
        "subscription_id": subscription.razorpay_subscription_id,
        "razorpay_key": RAZORPAY_KEY_ID,
        "amount": subscription.amount * 100,
        "currency": subscription.currency,
        "agency_name": settings.agency_name if settings else "AgencyOS",
        "primary_color": settings.primary_color if settings else "#6366f1",
        "status": subscription.status,
    })


def _mark_addon_payment_paid(payment):
    addon = ProjectAddOn.query.filter_by(id=payment.addon_id).with_for_update().first()
    if addon is None:
        raise LookupError(f"Scope add-on #{payment.addon_id} no longer exists.")

    addon.status = "Paid"
    addon.invoice.status = "Paid"
    if not addon.invoice.razorpay_order_id:
        addon.invoice.razorpay_order_id = payment.razorpay_order_id
    return addon


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
    addon_id = request.args.get("addon_id", type=int)
    addon = None
    if addon_id is not None:
        addon = ProjectAddOn.query.filter_by(
            id=addon_id, project_id=project.id
        ).first()
        if addon is None or addon.status != "Pending Payment":
            flash("This scope add-on is unavailable or already paid.", "warning")
            return redirect(url_for("customer.project_details", project_id=project.id))

    if (
        addon is None
        and project.package.strip().casefold()
        == STARTER_RETAINER_PACKAGE.casefold()
    ):
        flash(
            "The Starter Retainer is billed through a recurring mandate. "
            "Set up AutoPay from the project workspace.",
            "info",
        )
        return redirect(url_for("customer.project_details", project_id=project.id))

    milestone_num = request.args.get("milestone", type=int)
    if addon is not None:
        amount = Decimal(addon.amount)
        project_currency = addon.invoice.currency
    else:
        project_currency = project.currency or "INR"
        try:
            total_pkg_amount = get_package_price(
                project.package,
                project_currency,
                PACKAGE_PRICES,
            )
        except ValueError as exc:
            logger.warning("Cannot price project #%s for checkout: %s", project.id, exc)
            flash("This project package has no configured price in its billing currency.", "warning")
            return redirect(url_for("customer.project_details", project_id=project.id))
        if milestone_num in (2, 3):
            amount = (total_pkg_amount * Decimal("0.25")).quantize(Decimal("0.01"))
        else:
            amount = (total_pkg_amount * Decimal("0.50")).quantize(Decimal("0.01"))
    amount_minor_units = to_minor_units(amount)

    return render_template(
        "payment.html",
        project=project,
        addon=addon,
        amount=amount_minor_units,
        gross_val=amount,
        milestone_num=milestone_num or 1,
        currency=project_currency,
        razorpay_key_id=RAZORPAY_KEY_ID
    )


@payments_bp.route("/subscription/create/<int:project_id>", methods=["POST"])
@limiter.limit("5 per minute")
@login_required
def create_subscription(project_id):
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client", "admin"):
        return jsonify({"success": False, "message": "Customer access required."}), 403
    if razorpay_client is None:
        return jsonify({
            "success": False,
            "message": "Payment gateway unavailable.",
        }), 503

    project = Project.query.filter_by(id=project_id).first()
    if project is None or (
        user_role != "admin" and project.customer_id != current_user.id
    ):
        return jsonify({"success": False, "message": "Project not found."}), 404
    if project.package.strip().casefold() != STARTER_RETAINER_PACKAGE.casefold():
        return jsonify({
            "success": False,
            "message": "Auto-debit is only available for the Starter Retainer.",
        }), 400
    if project.currency != "INR":
        return jsonify({
            "success": False,
            "message": "Starter Retainer auto-debit is currently available in INR only.",
        }), 400

    existing = (
        RazorpaySubscription.query.filter(
            RazorpaySubscription.project_id == project.id,
            RazorpaySubscription.status.in_(OPEN_SUBSCRIPTION_STATUSES),
        )
        .order_by(RazorpaySubscription.id.desc())
        .first()
    )
    if existing is not None:
        if not existing.razorpay_subscription_id:
            return jsonify({
                "success": False,
                "message": (
                    "Subscription setup is being reconciled. Please contact "
                    "the agency before retrying."
                ),
            }), 409
        return _subscription_checkout_response(existing)

    try:
        plan = _ensure_starter_retainer_plan()
    except Exception as exc:
        db.session.rollback()
        logger.error("Could not initialize Starter Retainer plan: %s", str(exc))
        return jsonify({
            "success": False,
            "message": "Unable to initialize the recurring payment plan.",
        }), 502

    project = Project.query.filter_by(id=project_id).with_for_update().first()
    if project is None or (
        user_role != "admin" and project.customer_id != current_user.id
    ):
        db.session.rollback()
        return jsonify({"success": False, "message": "Project not found."}), 404

    existing = (
        RazorpaySubscription.query.filter(
            RazorpaySubscription.project_id == project.id,
            RazorpaySubscription.status.in_(OPEN_SUBSCRIPTION_STATUSES),
        )
        .order_by(RazorpaySubscription.id.desc())
        .first()
    )
    if existing is not None:
        if not existing.razorpay_subscription_id:
            return jsonify({
                "success": False,
                "message": (
                    "Subscription setup is being reconciled. Please contact "
                    "the agency before retrying."
                ),
            }), 409
        return _subscription_checkout_response(existing)

    start_at = _next_month_start_at()
    subscription = RazorpaySubscription()
    subscription.project_id = project.id
    subscription.customer_id = project.customer_id
    subscription.plan_id = plan.id
    subscription.amount = STARTER_RETAINER_AMOUNT
    subscription.currency = "INR"
    subscription.status = "initializing"
    subscription.start_at = start_at
    db.session.add(subscription)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = (
            RazorpaySubscription.query.filter(
                RazorpaySubscription.project_id == project_id,
                RazorpaySubscription.status.in_(OPEN_SUBSCRIPTION_STATUSES),
            )
            .order_by(RazorpaySubscription.id.desc())
            .first()
        )
        if existing is not None and existing.razorpay_subscription_id:
            return _subscription_checkout_response(existing)
        if existing is not None:
            return jsonify({
                "success": False,
                "message": (
                    "Subscription setup is being reconciled. Please contact "
                    "the agency before retrying."
                ),
            }), 409
        logger.exception(
            "Could not reserve recurring subscription for project #%s",
            project_id,
        )
        return jsonify({
            "success": False,
            "message": "Unable to reserve the recurring subscription.",
        }), 500
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error(
            "Could not reserve recurring subscription for project #%s: %s",
            project_id,
            str(exc),
        )
        return jsonify({
            "success": False,
            "message": "Unable to reserve the recurring subscription.",
        }), 500

    if not plan.razorpay_plan_id:
        logger.error(
            "Starter Retainer plan ID missing for local subscription #%s",
            subscription.id,
        )
        return jsonify({
            "success": False,
            "message": "Unable to initialize the recurring subscription.",
        }), 500

    try:
        response = razorpay_client.subscription.create({
            "plan_id": plan.razorpay_plan_id,
            "customer_notify": 1,
            "total_count": 12,
            "start_at": int(start_at.timestamp()),
            "notes": {
                "project_id": str(project.id),
                "customer_id": str(project.customer_id),
                "local_subscription_id": str(subscription.id),
            },
        })
    except Exception as exc:
        db.session.rollback()
        logger.error(
            "Razorpay subscription creation failed for project #%s "
            "(local subscription #%s requires reconciliation): %s",
            project.id,
            subscription.id,
            str(exc),
        )
        return jsonify({
            "success": False,
            "message": "Unable to initialize the recurring subscription.",
        }), 502

    subscription_id = response.get("id")
    if not isinstance(subscription_id, str) or not subscription_id:
        db.session.rollback()
        logger.error(
            "Razorpay subscription response has no ID for local subscription #%s",
            subscription.id,
        )
        return jsonify({
            "success": False,
            "message": "Unable to initialize the recurring subscription.",
        }), 502

    gateway_status = response.get("status")
    subscription.razorpay_subscription_id = subscription_id
    subscription.status = (
        gateway_status.lower()
        if isinstance(gateway_status, str) and gateway_status
        else "created"
    )
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error(
            "Could not finalize Razorpay subscription #%s for project #%s: %s",
            subscription_id,
            project.id,
            str(exc),
        )
        return jsonify({
            "success": False,
            "message": (
                "Subscription setup could not be finalized. Please contact "
                "the agency before retrying."
            ),
        }), 500

    return _subscription_checkout_response(subscription)


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
    if user_role not in ("customer", "client", "admin"):
        return jsonify({"success": False, "message": "Customer access required."}), 403
    if user_role == "admin":
        project = Project.query.get(project_id)
    else:
        project = Project.query.filter_by(id=project_id, customer_id=current_user.id).first()

    if not project:
        return jsonify({"success": False, "message": "Project not found."}), 404

    data = request.get_json(silent=True) or {}
    addon_id = data.get("addon_id")
    if addon_id is not None:
        try:
            addon_id = int(addon_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Invalid add-on."}), 400

        addon = ProjectAddOn.query.filter_by(
            id=addon_id, project_id=project.id
        ).first()
        if addon is None:
            return jsonify({"success": False, "message": "Add-on not found."}), 404
        if addon.status != "Pending Payment":
            return jsonify({"success": False, "message": "Add-on is not payable."}), 400

        existing_payment = Payment.query.filter_by(
            addon_id=addon.id, status="Created"
        ).first()
        if existing_payment:
            return jsonify({
                "success": True,
                "order_id": existing_payment.razorpay_order_id,
                "amount": to_minor_units(addon.amount),
                "currency": addon.invoice.currency,
                "key_id": RAZORPAY_KEY_ID,
            })

        amount_paise = to_minor_units(addon.amount)
        try:
            order = razorpay_client.order.create({
                "amount": amount_paise,
                "currency": addon.invoice.currency,
                "receipt": f"addon_{addon.id}_invoice_{addon.invoice_id}",
                "notes": {
                    "project_id": str(project.id),
                    "customer_id": str(project.customer_id),
                    "addon_id": str(addon.id),
                    "invoice_id": str(addon.invoice_id),
                },
            })
        except Exception as exc:
            logger.error("Razorpay add-on order error: %s", str(exc))
            return jsonify({
                "success": False,
                "message": "Unable to initialize the add-on payment order.",
            }), 500

        try:
            order_id = _get_order_id(order)
        except ValueError as exc:
            logger.error("%s", str(exc))
            return jsonify({
                "success": False,
                "message": "Unable to initialize the add-on payment order.",
            }), 502

        db.session.add(_create_payment_record(
            project_id=project.id,
            customer_id=project.customer_id,
            addon_id=addon.id,
            order_id=order_id,
            amount=Decimal(addon.amount),
            currency=addon.invoice.currency,
        ))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            existing_payment = Payment.query.filter_by(
                addon_id=addon.id, status="Created"
            ).first()
            if existing_payment is None:
                logger.exception("Could not save Razorpay add-on order for #%s", addon.id)
                return jsonify({
                    "success": False,
                    "message": "Unable to save the add-on payment order.",
                }), 500
            order_id = existing_payment.razorpay_order_id
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.error("Could not save Razorpay add-on order: %s", str(exc))
            return jsonify({
                "success": False,
                "message": "Unable to save the add-on payment order.",
            }), 500

        return jsonify({
            "success": True,
            "order_id": order_id,
            "amount": amount_paise,
            "currency": addon.invoice.currency,
            "key_id": RAZORPAY_KEY_ID,
        })

    if project.status not in ("Payment Pending", "Pending Assignment"):
        return jsonify({
            "success": False,
            "message": "Project is not in a payable state.",
        }), 400

    existing_paid = Payment.query.filter_by(
        project_id=project.id, status="Paid"
    ).first()
    if existing_paid is not None:
        return jsonify({"success": False, "message": "Project has already been paid."}), 400

    existing_created = Payment.query.filter_by(
        project_id=project.id, status="Created"
    ).first()
    if existing_created is not None:
        return jsonify({
            "success": True,
            "order_id": existing_created.razorpay_order_id,
            "amount": to_minor_units(existing_created.amount),
            "currency": existing_created.currency,
            "key_id": RAZORPAY_KEY_ID,
        })
    if project.package.strip().casefold() == STARTER_RETAINER_PACKAGE.casefold():
        return jsonify({
            "success": False,
            "message": "Starter Retainer payments require recurring mandate authorization.",
        }), 400

    milestone_num = data.get("milestone")
    
    currency = project.currency or "INR"
    try:
        total_pkg = get_package_price(project.package, currency, PACKAGE_PRICES)
    except ValueError as exc:
        logger.warning("Cannot price project #%s for checkout: %s", project.id, exc)
        return jsonify({
            "success": False,
            "message": "This project package has no configured price in its billing currency.",
        }), 400
    if milestone_num in (2, 3):
        amount = (total_pkg * Decimal("0.25")).quantize(Decimal("0.01"))
    elif milestone_num == 1:
        amount = (total_pkg * Decimal("0.50")).quantize(Decimal("0.01"))
    else:
        amount = total_pkg

    amount_paise = to_minor_units(amount)

    try:
        order = razorpay_client.order.create({
            "amount": amount_paise,
            "currency": currency,
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

    try:
        order_id = _get_order_id(order)
    except ValueError as exc:
        logger.error("%s", str(exc))
        return jsonify({
            "success": False,
            "message": "Unable to initialize Razorpay order.",
        }), 502

    db.session.add(_create_payment_record(
        project_id=project.id,
        customer_id=project.customer_id,
        order_id=order_id,
        amount=amount,
        currency=currency,
    ))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        conflicting = Payment.query.filter_by(
            razorpay_order_id=order_id
        ).first()
        if conflicting is not None:
            return jsonify({
                "success": False,
                "message": "Payment order already exists.",
            }), 400
        logger.error(
            "Razorpay order could not be reconciled",
            extra={
                "razorpay_order_id": order_id,
                "project_id": project.id,
            },
        )
        return jsonify({
            "success": False,
            "message": "Unable to create payment order.",
        }), 400
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("Could not save Razorpay order: %s", str(exc))
        return jsonify({
            "success": False,
            "message": "Unable to create payment order.",
        }), 500

    return jsonify({
        "success": True,
        "order_id": order_id,
        "amount": amount_paise,
        "currency": currency,
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
    if razorpay_client is None:
        return jsonify({
            "success": False,
            "message": "Payment gateway unavailable.",
        }), 503

    data = request.get_json(silent=True) or {}
    razorpay_order_id = data.get("razorpay_order_id", "").strip()
    razorpay_payment_id = data.get("razorpay_payment_id", "").strip()
    razorpay_signature = data.get("razorpay_signature", "").strip()

    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client", "admin"):
        return jsonify({"success": False, "message": "Customer access required."}), 403

    if not (razorpay_order_id and razorpay_payment_id and razorpay_signature):
        return jsonify({"success": False, "message": "Missing verification parameters."}), 400

    payment = Payment.query.filter_by(razorpay_order_id=razorpay_order_id).first()
    if not payment:
        return jsonify({"success": False, "message": "Payment record not found."}), 404

    if user_role != "admin" and payment.customer_id != current_user.id:
        return jsonify({"success": False, "message": "Payment not found."}), 404

    if payment.status == "Paid":
        return jsonify({
            "success": True,
            "message": "Payment already verified.",
            "project_id": payment.project_id,
            "addon_id": payment.addon_id,
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

    try:
        razorpay_order = razorpay_client.order.fetch(razorpay_order_id)
        razorpay_payment = razorpay_client.payment.fetch(razorpay_payment_id)
    except Exception as exc:
        logger.error("Could not verify Razorpay payment details: %s", str(exc))
        return jsonify({
            "success": False,
            "message": "Unable to verify payment details with the gateway.",
        }), 500

    expected_amount_paise = to_minor_units(payment.amount)
    amount_matches = (
        _get_gateway_amount(razorpay_order) == expected_amount_paise
        and _get_gateway_amount(razorpay_payment) == expected_amount_paise
    )

    if (
        razorpay_order.get("id") != razorpay_order_id
        or razorpay_order.get("currency") != payment.currency
        or razorpay_payment.get("order_id") != razorpay_order_id
        or razorpay_payment.get("currency") != payment.currency
        or razorpay_payment.get("status") != "captured"
        or not amount_matches
    ):
        return jsonify({
            "success": False,
            "message": "Payment amount, currency, or captured status did not match.",
        }), 400

    existing_payment = Payment.query.filter_by(
        razorpay_payment_id=razorpay_payment_id
    ).first()
    if existing_payment is not None and existing_payment.id != payment.id:
        return jsonify({
            "success": False,
            "message": "This gateway payment is already linked to another payment.",
        }), 400

    # 3. Database State Update (with Talent Payout & Agency Profit)
    try:
        payment.razorpay_payment_id = razorpay_payment_id
        payment.status = "Paid"
        payment.paid_at = get_ist_now()

        if payment.addon_id is not None:
            addon = _mark_addon_payment_paid(payment)
            ActivityLog.log_event(
                payment.project_id,
                "PAYMENT_RECEIVED",
                f"Scope add-on payment of {format_currency(payment.amount, payment.currency)} verified via Razorpay.",
                payment.customer_id,
                "success",
            )
            db.session.commit()
            logger.info(
                "Scope add-on #%s paid for project #%s",
                addon.id,
                payment.project_id,
            )
            return jsonify({
                "success": True,
                "message": "Add-on payment verified successfully.",
                "project_id": payment.project_id,
                "addon_id": addon.id,
            })

        # Update Project status to active
        if payment.project_id:
            project = Project.query.get(payment.project_id)
            if project:
                project.status = "In Progress"
            ActivityLog.log_event(
                payment.project_id,
                "PAYMENT_RECEIVED",
                f"Milestone payment of {format_currency(payment.amount, payment.currency)} verified via Razorpay.",
                payment.customer_id,
                "success",
            )

        # Calculate exact 45/55 split and statutory GST
        amt = Decimal(str(payment.amount or 24000))
        talent_share = (amt * Decimal("0.45")).quantize(Decimal("0.01"))
        agency_share = amt - talent_share
        tax_val = (
            amt - (amt / Decimal("1.18")).quantize(Decimal("0.01"))
        ).quantize(Decimal("0.01"))
        unique_inv_number = f"INV-2026-{payment.project_id or 1:04d}-{secrets.token_hex(2).upper()}"

        # Safe GST Invoice handling
        invoice = None
        if payment.project_id:
            invoice = Invoice.query.filter_by(project_id=payment.project_id).first()

        if invoice:
            invoice.status = "Paid"
            invoice.currency = payment.currency
            if not invoice.razorpay_order_id:
                invoice.razorpay_order_id = razorpay_order_id
            if not invoice.talent_payout:
                invoice.talent_payout = talent_share
            if not invoice.agency_profit:
                invoice.agency_profit = agency_share
        else:
            new_inv = Invoice()
            new_inv.invoice_number = unique_inv_number
            new_inv.project_id = payment.project_id
            new_inv.customer_id = payment.customer_id
            new_inv.gross_amount = amt
            new_inv.currency = payment.currency
            new_inv.talent_payout = talent_share
            new_inv.agency_profit = agency_share
            new_inv.tax_amount = tax_val
            new_inv.status = "Paid"
            new_inv.payout_status = "Pending Approval"
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

def _handle_subscription_charged(
    event_payload: dict[str, object],
    event_id: str | None,
):
    payload = event_payload.get("payload")
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid subscription charge payload"}), 400

    subscription_payload = payload.get("subscription")
    payment_payload = payload.get("payment")
    subscription_entity = (
        subscription_payload.get("entity")
        if isinstance(subscription_payload, dict)
        else None
    )
    payment_entity = (
        payment_payload.get("entity")
        if isinstance(payment_payload, dict)
        else None
    )
    if not isinstance(subscription_entity, dict) or not isinstance(payment_entity, dict):
        return jsonify({"error": "Invalid subscription charge payload"}), 400

    gateway_subscription_id = subscription_entity.get("id")
    gateway_payment_id = payment_entity.get("id")
    payment_subscription_id = payment_entity.get("subscription_id")
    gateway_plan_id = subscription_entity.get("plan_id")
    if not isinstance(gateway_subscription_id, str) or not gateway_subscription_id:
        return jsonify({"error": "Subscription charge identifiers are missing"}), 400
    if not isinstance(gateway_payment_id, str) or not gateway_payment_id:
        return jsonify({"error": "Subscription charge identifiers are missing"}), 400
    if not isinstance(gateway_plan_id, str) or not gateway_plan_id:
        return jsonify({"error": "Subscription charge identifiers are missing"}), 400
    if payment_subscription_id is not None and not isinstance(
        payment_subscription_id, str
    ):
        return jsonify({"error": "Invalid payment subscription reference"}), 400
    if len(gateway_payment_id) > 100 or len(gateway_subscription_id) > 100:
        return jsonify({"error": "Subscription charge identifiers are invalid"}), 400
    if event_id is not None and len(event_id) > 100:
        return jsonify({"error": "Invalid webhook event ID"}), 400

    if event_id and SubscriptionCharge.query.filter_by(event_id=event_id).first():
        return jsonify({"status": "duplicate"}), 200
    existing_charge = SubscriptionCharge.query.filter_by(
        razorpay_payment_id=gateway_payment_id
    ).first()
    if existing_charge:
        return jsonify({"status": "duplicate"}), 200

    subscription = (
        RazorpaySubscription.query.filter_by(
            razorpay_subscription_id=gateway_subscription_id
        )
        .with_for_update()
        .first()
    )
    if subscription is None:
        return jsonify({"error": "Subscription not found"}), 404
    duplicate_charge = SubscriptionCharge.query.filter_by(
        razorpay_payment_id=gateway_payment_id
    ).first()
    if duplicate_charge is None and event_id:
        duplicate_charge = SubscriptionCharge.query.filter_by(
            event_id=event_id
        ).first()
    if duplicate_charge is not None:
        return jsonify({"status": "duplicate"}), 200
    if (
        (
            payment_subscription_id is not None
            and payment_subscription_id != gateway_subscription_id
        )
        or gateway_plan_id != subscription.plan.razorpay_plan_id
    ):
        return jsonify({"error": "Subscription details do not match"}), 400

    amount_paise = _get_gateway_amount(payment_entity)
    if (
        amount_paise != subscription.amount * 100
        or payment_entity.get("currency") != subscription.currency
        or payment_entity.get("status") != "captured"
    ):
        logger.warning(
            "Subscription payment validation failed for subscription %s",
            gateway_subscription_id,
        )
        return jsonify({"error": "Subscription payment amount or status mismatch"}), 400

    project = Project.query.filter_by(id=subscription.project_id).with_for_update().first()
    if project is None:
        return jsonify({"error": "Subscription project not found"}), 404

    existing_payment = Payment.query.filter_by(
        razorpay_payment_id=gateway_payment_id
    ).first()
    if existing_payment is not None:
        logger.error(
            "Razorpay subscription payment %s is already linked to a different charge",
            gateway_payment_id,
        )
        return jsonify({"error": "Subscription payment is already recorded"}), 409

    amount = subscription.amount
    charged_at = get_ist_now()
    tax_amount = int(round(amount - amount / 1.18))
    talent_payout = int(amount * 0.45)
    payment_reference = hashlib.sha256(
        f"{gateway_subscription_id}:{gateway_payment_id}".encode("utf-8")
    ).hexdigest()[:16].upper()
    gateway_order_id = payment_entity.get("order_id")
    payment = Payment()
    payment.project_id = subscription.project_id
    payment.customer_id = subscription.customer_id
    payment.razorpay_order_id = (
        gateway_order_id
        if isinstance(gateway_order_id, str)
        and gateway_order_id
        and len(gateway_order_id) <= 100
        else f"sub_{payment_reference}"
    )
    payment.razorpay_payment_id = gateway_payment_id
    payment.gateway_payment_id = gateway_payment_id
    payment.amount = amount
    payment.currency = subscription.currency
    payment.status = "Paid"
    payment.paid_at = charged_at

    invoice = Invoice()
    invoice.invoice_number = (
        f"RET-{subscription.project_id:04d}-{payment_reference}"
    )
    invoice.project_id = subscription.project_id
    invoice.customer_id = subscription.customer_id
    invoice.gross_amount = amount
    invoice.currency = subscription.currency
    invoice.talent_payout = talent_payout
    invoice.agency_profit = amount - talent_payout
    invoice.tax_amount = tax_amount
    invoice.status = "Paid"
    invoice.payout_status = "Pending Approval"
    invoice.created_at = charged_at

    subscription_status = subscription_entity.get("status")
    if isinstance(subscription_status, str) and subscription_status:
        subscription.status = subscription_status[:30].lower()
    if project.status in ("Payment Pending", "Pending Assignment"):
        project.status = "In Progress"

    db.session.add_all((payment, invoice))
    ActivityLog.log_event(
        subscription.project_id,
        "PAYMENT_RECEIVED",
        f"Monthly retainer payment of {format_currency(amount, subscription.currency)} received via Razorpay.",
        subscription.customer_id,
        "success",
    )
    try:
        db.session.flush()
        charge = SubscriptionCharge()
        charge.subscription_id = subscription.id
        charge.payment_id = payment.id
        charge.invoice_id = invoice.id
        charge.razorpay_payment_id = gateway_payment_id
        charge.event_id = event_id or None
        charge.amount = amount
        charge.currency = subscription.currency
        charge.charged_at = charged_at
        db.session.add(charge)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        duplicate = SubscriptionCharge.query.filter_by(
            razorpay_payment_id=gateway_payment_id
        ).first()
        if duplicate is None and event_id:
            duplicate = SubscriptionCharge.query.filter_by(
                event_id=event_id
            ).first()
        if duplicate:
            return jsonify({"status": "duplicate"}), 200
        logger.exception(
            "Could not persist subscription charge %s", gateway_payment_id
        )
        return jsonify({"error": "Could not record subscription payment"}), 500
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error(
            "Could not persist subscription charge %s: %s",
            gateway_payment_id,
            str(exc),
        )
        return jsonify({"error": "Could not record subscription payment"}), 500

    logger.info(
        "Recorded retainer renewal payment %s for project #%s",
        gateway_payment_id,
        subscription.project_id,
    )
    return jsonify({
        "status": "success",
        "payment_id": payment.id,
        "invoice_id": invoice.id,
    }), 200


def _handle_subscription_status_event(
    event_payload: dict[str, object],
    event: str,
):
    payload = event_payload.get("payload")
    subscription_payload = (
        payload.get("subscription") if isinstance(payload, dict) else None
    )
    subscription_entity = (
        subscription_payload.get("entity")
        if isinstance(subscription_payload, dict)
        else None
    )
    if not isinstance(subscription_entity, dict):
        return jsonify({"error": "Invalid subscription status payload"}), 400

    gateway_subscription_id = subscription_entity.get("id")
    gateway_plan_id = subscription_entity.get("plan_id")
    if (
        not isinstance(gateway_subscription_id, str)
        or not gateway_subscription_id
        or not isinstance(gateway_plan_id, str)
        or not gateway_plan_id
    ):
        return jsonify({"error": "Subscription identifiers are missing"}), 400

    subscription = (
        RazorpaySubscription.query.filter_by(
            razorpay_subscription_id=gateway_subscription_id
        )
        .with_for_update()
        .first()
    )
    if subscription is None:
        return jsonify({"error": "Subscription not found"}), 404
    if gateway_plan_id != subscription.plan.razorpay_plan_id:
        return jsonify({"error": "Subscription plan does not match"}), 400

    status = SUBSCRIPTION_STATUS_EVENTS[event]
    if event == "subscription.updated":
        updated_status = subscription_entity.get("status")
        allowed_statuses = {
            "authenticated",
            "active",
            "pending",
            "halted",
            "cancelled",
            "completed",
            "paused",
            "resumed",
        }
        if not isinstance(updated_status, str) or updated_status not in allowed_statuses:
            return jsonify({"status": "ignored"}), 200
        status = updated_status

    subscription.status = status
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error(
            "Could not update Razorpay subscription %s status: %s",
            gateway_subscription_id,
            str(exc),
        )
        return jsonify({"error": "Could not update subscription status"}), 500

    return jsonify({"status": "success", "subscription_status": status}), 200


@payments_bp.route("/webhook", methods=["POST"])
@payments_bp.route("/api/billing/webhook", methods=["POST"])
@payments_bp.route("/subscription/webhook", methods=["POST"])
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

    event_payload = request.get_json(silent=True)
    if not isinstance(event_payload, dict):
        return jsonify({"error": "Invalid webhook payload"}), 400

    if event_payload.get("event") == "subscription.charged":
        return _handle_subscription_charged(
            event_payload,
            request.headers.get("X-Razorpay-Event-Id"),
        )
    subscription_event = event_payload.get("event")
    if isinstance(subscription_event, str) and (
        subscription_event in SUBSCRIPTION_STATUS_EVENTS
        or subscription_event == "subscription.updated"
    ):
        return _handle_subscription_status_event(event_payload, subscription_event)

    if event_payload.get("event") == "payment.captured":
        entity = event_payload.get("payload", {}).get("payment", {}).get("entity", {})
        pid = entity.get("id")
        oid = entity.get("order_id")

        existing = Payment.query.filter_by(razorpay_payment_id=pid).first()
        if existing and existing.status == "Paid":
            return jsonify({"status": "ignored"}), 200

        order_payment = Payment.query.filter_by(razorpay_order_id=oid).first()
        if order_payment:
            if order_payment.addon_id is not None:
                try:
                    captured_amount = int(entity.get("amount", -1))
                except (TypeError, ValueError):
                    captured_amount = -1
                if (
                    captured_amount != int(order_payment.amount) * 100
                    or entity.get("currency") != "INR"
                ):
                    logger.warning(
                        "Captured amount or currency mismatch for add-on #%s",
                        order_payment.addon_id,
                    )
                    return jsonify({"error": "Add-on payment amount mismatch"}), 400

            order_payment.razorpay_payment_id = pid
            order_payment.status = "Paid"
            order_payment.paid_at = get_ist_now()
            if order_payment.addon_id is not None:
                try:
                    _mark_addon_payment_paid(order_payment)
                except LookupError:
                    db.session.rollback()
                    logger.exception(
                        "Could not apply captured payment to add-on #%s",
                        order_payment.addon_id,
                    )
                    return jsonify({"error": "Add-on payment record is unavailable"}), 500
                ActivityLog.log_event(
                    order_payment.project_id,
                    "PAYMENT_RECEIVED",
                    f"Scope add-on payment of ₹{order_payment.amount:,.2f} captured via Razorpay.",
                    order_payment.customer_id,
                    "success",
                )
            elif order_payment.project_id:
                Project.query.filter_by(id=order_payment.project_id).update({"status": "In Progress"})
                ActivityLog.log_event(
                    order_payment.project_id,
                    "PAYMENT_RECEIVED",
                    f"Milestone payment of ₹{order_payment.amount:,.2f} captured via Razorpay.",
                    order_payment.customer_id,
                    "success",
                )
            db.session.commit()

    return jsonify({"status": "success"}), 200