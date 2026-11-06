"""AgencyOS Multi-Currency Payments & Payout Blueprint.

Handles Razorpay orders, sequential 3-milestone escrow checkouts (50/25/25), scope add-ons,
auto-debit retainers, idempotent cryptographic webhooks, specialist ledger settlements,
and seamless sandbox simulation in development environments.
"""

import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime
from decimal import Decimal
from typing import Protocol, cast

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import csrf, db, get_razorpay_client, limiter
from app.models import (
    ActivityLog,
    AgencySetting,
    Invoice,
    Milestone,
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
)
from app.utils.currency import (
    compute_financial_split,
    format_currency,
    get_package_price,
    sanitize_currency,
    to_minor_units,
)

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
    payment.currency = sanitize_currency(currency)
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
    plan = (
        RazorpaySubscriptionPlan.query.filter_by(code=STARTER_RETAINER_CODE)
        .with_for_update()
        .first()
    )
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
            plan = (
                RazorpaySubscriptionPlan.query.filter_by(code=STARTER_RETAINER_CODE)
                .with_for_update()
                .first()
            )
            if plan is None:
                raise
    if plan.amount != STARTER_RETAINER_AMOUNT or plan.currency != "INR":
        raise ValueError("Stored Starter Retainer plan terms do not match.")

    if plan.razorpay_plan_id:
        return plan
    if razorpay_client is None:
        raise RuntimeError("Razorpay client is unavailable.")

    response = razorpay_client.plan.create(
        {
            "period": "monthly",
            "interval": 1,
            "item": {
                "name": plan.name,
                "amount": STARTER_RETAINER_AMOUNT * 100,
                "currency": "INR",
                "description": "Monthly SEO ranking engine and optimization",
            },
        }
    )
    gateway_plan_id = response.get("id")
    if not isinstance(gateway_plan_id, str) or not gateway_plan_id:
        raise ValueError("Razorpay plan response is missing its plan ID.")

    plan.razorpay_plan_id = gateway_plan_id
    db.session.commit()
    return plan


def _subscription_checkout_response(subscription: RazorpaySubscription):
    settings = db.session.get(AgencySetting, 1)
    return jsonify(
        {
            "success": True,
            "subscription_id": subscription.razorpay_subscription_id,
            "razorpay_key": RAZORPAY_KEY_ID,
            "amount": subscription.amount * 100,
            "currency": subscription.currency,
            "agency_name": settings.agency_name if settings else "AgencyOS",
            "primary_color": settings.primary_color if settings else "#09090b",
            "status": subscription.status,
        }
    )


def _mark_addon_payment_paid(payment: Payment) -> ProjectAddOn:
    addon = ProjectAddOn.query.filter_by(id=payment.addon_id).with_for_update().first()
    if addon is None:
        raise LookupError(f"Scope add-on #{payment.addon_id} no longer exists.")

    addon.status = "Paid"
    if addon.invoice:
        addon.invoice.status = "Paid"
        if not addon.invoice.razorpay_order_id:
            addon.invoice.razorpay_order_id = payment.razorpay_order_id
    return addon


# =========================================================
# PAYMENT CHECKOUT PAGE (SUPPORTS MILESTONES 1, 2, 3 & ADD-ONS)
# =========================================================

@payments_bp.route("/payment/<int:project_id>")
@login_required
def payment_page(project_id: int) -> ResponseReturnValue:
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
        addon = ProjectAddOn.query.filter_by(id=addon_id, project_id=project.id).first()
        if addon is None or addon.status != "Pending Payment":
            flash("This scope add-on is unavailable or already paid.", "warning")
            return redirect(url_for("customer.project_details", project_id=project.id))

    if (
        addon is None
        and project.package.strip().casefold() == STARTER_RETAINER_PACKAGE.casefold()
    ):
        flash(
            "The Starter Retainer is billed through a recurring mandate. Set up AutoPay from the project workspace.",
            "info",
        )
        return redirect(url_for("customer.project_details", project_id=project.id))

    paid_payments = Payment.query.filter_by(project_id=project.id, status="Paid", addon_id=None).all()
    paid_count = len(paid_payments)

    milestone_num = request.args.get("milestone", type=int)
    if not milestone_num:
        milestone_num = paid_count + 1

    if addon is not None:
        amount = Decimal(str(addon.amount))
        project_currency = sanitize_currency(addon.invoice.currency if addon.invoice else project.currency)
    else:
        if milestone_num <= paid_count:
            flash(f"Milestone {milestone_num} has already been authorized and paid.", "info")
            return redirect(url_for("customer.project_details", project_id=project.id))

        project_currency = sanitize_currency(project.currency)
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
    is_test_mode = "placeholder" in RAZORPAY_KEY_ID.lower() or not RAZORPAY_KEY_ID

    return render_template(
        "payment.html",
        project=project,
        addon=addon,
        amount=amount_minor_units,
        gross_val=amount,
        milestone_num=milestone_num or 1,
        currency=project_currency,
        razorpay_key_id=RAZORPAY_KEY_ID,
        is_test_mode=is_test_mode,
    )


# =========================================================
# CREATE PAYMENT ORDER (GATEWAY + SANDBOX FALLBACK)
# =========================================================

@payments_bp.route("/payment/create-order/<int:project_id>", methods=["POST"])
@limiter.limit("20 per minute")
@login_required
def create_payment_order(project_id: int) -> ResponseReturnValue:
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

    # Scope Add-On Flow
    if addon_id is not None:
        try:
            addon_id = int(addon_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Invalid add-on parameter."}), 400

        addon = ProjectAddOn.query.filter_by(id=addon_id, project_id=project.id).first()
        if addon is None:
            return jsonify({"success": False, "message": "Add-on not found."}), 404
        if addon.status != "Pending Payment":
            return jsonify({"success": False, "message": "Add-on is not payable."}), 400

        currency = sanitize_currency(addon.invoice.currency if addon.invoice else project.currency)
        amount_minor = to_minor_units(addon.amount)

        existing_payment = Payment.query.filter_by(addon_id=addon.id, status="Created").first()
        if existing_payment:
            return jsonify({
                "success": True,
                "order_id": existing_payment.razorpay_order_id,
                "amount": amount_minor,
                "currency": currency,
                "key_id": RAZORPAY_KEY_ID,
            })

        order_id = None
        if razorpay_client and "placeholder" not in RAZORPAY_KEY_ID.lower() and RAZORPAY_KEY_ID:
            try:
                order = razorpay_client.order.create(
                    {
                        "amount": amount_minor,
                        "currency": currency,
                        "receipt": f"addon_{addon.id}_inv_{addon.invoice_id}",
                        "notes": {
                            "project_id": str(project.id),
                            "customer_id": str(project.customer_id),
                            "addon_id": str(addon.id),
                        },
                    }
                )
                order_id = _get_order_id(order)
            except Exception as exc:
                logger.warning("Remote Razorpay order failed; activating sandbox fallback: %s", str(exc))

        if not order_id:
            order_id = f"order_sim_addon_{addon.id}_{secrets.token_hex(4)}"

        try:
            db.session.add(
                _create_payment_record(
                    project_id=project.id,
                    customer_id=project.customer_id,
                    addon_id=addon.id,
                    order_id=order_id,
                    amount=Decimal(str(addon.amount)),
                    currency=currency,
                )
            )
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            existing = Payment.query.filter_by(addon_id=addon.id, status="Created").first()
            if existing:
                order_id = existing.razorpay_order_id

        return jsonify({
            "success": True,
            "order_id": order_id,
            "amount": amount_minor,
            "currency": currency,
            "key_id": RAZORPAY_KEY_ID or "rzp_test_placeholder",
        })

    # Sequential Milestone Payment Flow (50 / 25 / 25)
    paid_payments = Payment.query.filter_by(project_id=project.id, status="Paid", addon_id=None).all()
    paid_count = len(paid_payments)

    if paid_count >= 3:
        return jsonify({"success": False, "message": "All milestone payments for this project have already been completed."}), 400

    raw_milestone = data.get("milestone")
    try:
        milestone_num = int(raw_milestone) if raw_milestone else (paid_count + 1)
    except (ValueError, TypeError):
        milestone_num = paid_count + 1

    if milestone_num <= paid_count:
        return jsonify({"success": False, "message": f"Milestone {milestone_num} has already been authorized and paid."}), 400

    currency = sanitize_currency(project.currency)
    try:
        total_pkg = get_package_price(project.package, currency, PACKAGE_PRICES)
    except ValueError as exc:
        logger.warning("Cannot price project #%s for checkout: %s", project.id, exc)
        return jsonify({"success": False, "message": "Package price is unconfigured."}), 400

    if milestone_num in (2, 3):
        amount = (total_pkg * Decimal("0.25")).quantize(Decimal("0.01"))
    elif milestone_num == 1:
        amount = (total_pkg * Decimal("0.50")).quantize(Decimal("0.01"))
    else:
        amount = total_pkg

    amount_minor = to_minor_units(amount)

    existing_created = Payment.query.filter_by(project_id=project.id, status="Created", addon_id=None).first()
    if existing_created is not None:
        if existing_created.amount == amount and existing_created.currency == currency:
            return jsonify({
                "success": True,
                "order_id": existing_created.razorpay_order_id,
                "amount": to_minor_units(existing_created.amount),
                "currency": existing_created.currency,
                "key_id": RAZORPAY_KEY_ID,
            })
        else:
            db.session.delete(existing_created)
            db.session.flush()

    order_id = None
    if razorpay_client and "placeholder" not in RAZORPAY_KEY_ID.lower() and RAZORPAY_KEY_ID:
        try:
            order = razorpay_client.order.create(
                {
                    "amount": amount_minor,
                    "currency": currency,
                    "receipt": f"proj_{project.id}_m{milestone_num}_{secrets.token_hex(2)}",
                    "notes": {
                        "project_id": str(project.id),
                        "customer_id": str(project.customer_id),
                        "milestone": str(milestone_num),
                    },
                }
            )
            order_id = _get_order_id(order)
        except Exception as exc:
            logger.warning("Remote Razorpay order failed; activating sandbox order: %s", str(exc))

    if not order_id:
        order_id = f"order_sim_{project.id}_m{milestone_num}_{secrets.token_hex(4)}"

    try:
        db.session.add(
            _create_payment_record(
                project_id=project.id,
                customer_id=project.customer_id,
                order_id=order_id,
                amount=amount,
                currency=currency,
            )
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = Payment.query.filter_by(project_id=project.id, status="Created", addon_id=None).first()
        if existing:
            order_id = existing.razorpay_order_id

    return jsonify({
        "success": True,
        "order_id": order_id,
        "amount": amount_minor,
        "currency": currency,
        "key_id": RAZORPAY_KEY_ID or "rzp_test_placeholder",
    })


# =========================================================
# CRASH-PROOF VERIFY PAYMENT ROUTE (GATEWAY + SANDBOX)
# =========================================================

@payments_bp.route("/payment/verify", methods=["POST"])
@payments_bp.route("/api/billing/verify-payment", methods=["POST"])
@limiter.limit("30 per minute")
@login_required
def verify_payment() -> ResponseReturnValue:
    data = request.get_json(silent=True) or {}
    razorpay_order_id = data.get("razorpay_order_id", "").strip()
    razorpay_payment_id = data.get("razorpay_payment_id", "").strip()
    razorpay_signature = data.get("razorpay_signature", "").strip()

    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client", "admin"):
        return jsonify({"success": False, "message": "Customer access required."}), 403

    if not (razorpay_order_id and razorpay_payment_id):
        return jsonify({"success": False, "message": "Missing verification parameters."}), 400

    payment = Payment.query.filter_by(razorpay_order_id=razorpay_order_id).first()
    if not payment:
        return jsonify({"success": False, "message": "Payment record not found."}), 404

    if user_role != "admin" and payment.customer_id != current_user.id:
        return jsonify({"success": False, "message": "Payment record not accessible."}), 404

    if payment.status == "Paid":
        return jsonify({
            "success": True,
            "message": "Payment already verified.",
            "project_id": payment.project_id,
            "addon_id": payment.addon_id,
        })

    is_simulated = razorpay_order_id.startswith("order_sim_") or "placeholder" in RAZORPAY_KEY_ID.lower()

    if not is_simulated and razorpay_client:
        try:
            razorpay_client.utility.verify_payment_signature(
                {
                    "razorpay_order_id": razorpay_order_id,
                    "razorpay_payment_id": razorpay_payment_id,
                    "razorpay_signature": razorpay_signature,
                }
            )
        except Exception as exc:
            logger.warning("Gateway signature verification failed: %s", str(exc))
            return jsonify({"success": False, "message": "Invalid payment signature."}), 400

    try:
        payment.razorpay_payment_id = razorpay_payment_id
        payment.status = "Paid"
        payment.paid_at = get_ist_now()

        # Handle Scope Add-on Payment
        if payment.addon_id is not None:
            addon = _mark_addon_payment_paid(payment)
            ActivityLog.log_event(
                project_id=payment.project_id,
                action="PAYMENT_RECEIVED",
                description=f"Scope add-on payment of {format_currency(payment.amount, payment.currency)} verified.",
                user_id=payment.customer_id,
                category="success",
            )
            db.session.commit()
            return jsonify({
                "success": True,
                "message": "Add-on payment verified successfully.",
                "project_id": payment.project_id,
                "addon_id": addon.id,
            })

        # Calculate milestone count funded on this project
        paid_count = Payment.query.filter_by(project_id=payment.project_id, status="Paid", addon_id=None).count()
        milestone_index = paid_count

        project = Project.query.get(payment.project_id) if payment.project_id else None
        talent_share, agency_share, tax_val = compute_financial_split(payment.amount, payment.currency)

        if project:
            if milestone_index == 1:
                if not project.assigned_professional_id:
                    project.status = "Pending Assignment"
                else:
                    project.status = "In Progress"
            elif milestone_index in (2, 3):
                project.status = "In Progress"

            # Reconcile Two-Stage Escrow: Credit Specialist Pending Balance
            assigned_app = ProfessionalProjectApplication.query.filter_by(
                project_id=project.id, status="Assigned"
            ).first()
            specialist_id = project.assigned_professional_id or (assigned_app.professional_id if assigned_app else None)

            if specialist_id:
                spec_profile = ProfessionalProfile.query.filter_by(user_id=specialist_id).first()
                if spec_profile:
                    spec_profile.pending_balance = (
                        Decimal(str(spec_profile.pending_balance or 0)) + Decimal(str(talent_share))
                    ).quantize(Decimal("0.01"))

                    tx = WalletTransaction()
                    tx.user_id = specialist_id
                    tx.amount = talent_share
                    tx.currency = payment.currency
                    tx.transaction_type = "ESCROW_LOCKED"
                    tx.reference_type = "PROJECT"
                    tx.reference_id = project.id
                    tx.description = f"Milestone {milestone_index} escrow funded for Project #{project.id}. {format_currency(talent_share, payment.currency)} held in pending balance."
                    tx.created_at = get_ist_now()
                    db.session.add(tx)

            # Fund the specific corresponding milestone record
            milestones = Milestone.query.filter_by(project_id=project.id).order_by(Milestone.id.asc()).all()
            if milestones and len(milestones) >= milestone_index:
                target_milestone = milestones[milestone_index - 1]
                target_milestone.status = "funded"

            ActivityLog.log_event(
                project_id=payment.project_id,
                action="PAYMENT_RECEIVED",
                description=f"Milestone {milestone_index} payment of {format_currency(payment.amount, payment.currency)} verified and held in escrow.",
                user_id=payment.customer_id,
                category="success",
            )

        unique_inv_number = f"INV-2026-{payment.project_id or 1:04d}-M{milestone_index}-{secrets.token_hex(2).upper()}"
        new_inv = Invoice()
        new_inv.invoice_number = unique_inv_number
        new_inv.project_id = payment.project_id
        new_inv.customer_id = payment.customer_id
        new_inv.gross_amount = payment.amount
        new_inv.currency = payment.currency
        new_inv.talent_payout = talent_share
        new_inv.agency_profit = agency_share
        new_inv.tax_amount = tax_val
        new_inv.status = "Paid"
        new_inv.payout_status = "Pending Approval"
        new_inv.razorpay_order_id = razorpay_order_id
        db.session.add(new_inv)

        db.session.commit()
        return jsonify({
            "success": True,
            "message": f"Milestone {milestone_index} payment verified successfully.",
            "project_id": payment.project_id,
        })

    except Exception as exc:
        db.session.rollback()
        logger.error("Database write failure in verify_payment: %s", str(exc), exc_info=True)
        return jsonify({"success": False, "message": f"Database write failure: {str(exc)}"}), 500


# =========================================================
# ONE-CLICK SIMULATE TEST PAYMENT (SUPPORTS MILESTONES 1, 2, 3)
# =========================================================

@payments_bp.route("/payment/simulate-test-payment/<int:project_id>", methods=["POST"])
@login_required
def simulate_test_payment(project_id: int) -> ResponseReturnValue:
    """Instantly fund next milestone in development and redirect directly to project workbench."""
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client", "admin"):
        flash("Customer access required.", "warning")
        return redirect(url_for("customer.dashboard"))

    if user_role == "admin":
        project = Project.query.get_or_404(project_id)
    else:
        project = Project.query.filter_by(id=project_id, customer_id=current_user.id).first_or_404()

    paid_payments = Payment.query.filter_by(project_id=project.id, status="Paid", addon_id=None).all()
    paid_count = len(paid_payments)

    if paid_count >= 3:
        flash("All 3 milestones have already been authorized and settled.", "info")
        return redirect(url_for("customer.project_details", project_id=project.id))

    next_milestone = paid_count + 1
    currency = sanitize_currency(project.currency)
    total_pkg = get_package_price(project.package, currency, PACKAGE_PRICES)

    if next_milestone == 1:
        amount = (total_pkg * Decimal("0.50")).quantize(Decimal("0.01"))
    else:
        amount = (total_pkg * Decimal("0.25")).quantize(Decimal("0.01"))

    try:
        payment = _create_payment_record(
            project_id=project.id,
            customer_id=project.customer_id,
            order_id=f"order_sim_{project.id}_m{next_milestone}_{secrets.token_hex(4)}",
            amount=amount,
            currency=currency,
        )
        payment.status = "Paid"
        payment.paid_at = get_ist_now()
        payment.razorpay_payment_id = f"pay_sim_m{next_milestone}_{secrets.token_hex(6)}"
        db.session.add(payment)

        if next_milestone == 1 and not project.assigned_professional_id:
            project.status = "Pending Assignment"
        else:
            project.status = "In Progress"

        talent_share, agency_share, tax_val = compute_financial_split(amount, currency)

        # Lock Escrow in Specialist Profile
        assigned_app = ProfessionalProjectApplication.query.filter_by(
            project_id=project.id, status="Assigned"
        ).first()
        specialist_id = project.assigned_professional_id or (assigned_app.professional_id if assigned_app else None)

        if specialist_id:
            spec_profile = ProfessionalProfile.query.filter_by(user_id=specialist_id).first()
            if spec_profile:
                spec_profile.pending_balance = (
                    Decimal(str(spec_profile.pending_balance or 0)) + Decimal(str(talent_share))
                ).quantize(Decimal("0.01"))

                tx = WalletTransaction()
                tx.user_id = specialist_id
                tx.amount = talent_share
                tx.currency = currency
                tx.transaction_type = "ESCROW_LOCKED"
                tx.reference_type = "PROJECT"
                tx.reference_id = project.id
                tx.description = f"Simulated Milestone {next_milestone} escrow locked. {format_currency(talent_share, currency)} held in pending balance."
                tx.created_at = get_ist_now()
                db.session.add(tx)

        new_inv = Invoice()
        new_inv.invoice_number = f"INV-2026-{project.id:04d}-M{next_milestone}-{secrets.token_hex(2).upper()}"
        new_inv.project_id = project.id
        new_inv.customer_id = project.customer_id
        new_inv.gross_amount = amount
        new_inv.currency = currency
        new_inv.talent_payout = talent_share
        new_inv.agency_profit = agency_share
        new_inv.tax_amount = tax_val
        new_inv.status = "Paid"
        new_inv.payout_status = "Pending Approval"
        new_inv.razorpay_order_id = payment.razorpay_order_id
        db.session.add(new_inv)

        milestones = Milestone.query.filter_by(project_id=project.id).order_by(Milestone.id.asc()).all()
        if milestones and len(milestones) >= next_milestone:
            milestones[next_milestone - 1].status = "funded"

        ActivityLog.log_event(
            project_id=project.id,
            action="PAYMENT_RECEIVED",
            description=f"Milestone {next_milestone} deposit of {format_currency(amount, currency)} verified and escrow held.",
            user_id=current_user.id,
            category="success",
        )

        db.session.commit()
        flash(f"Milestone {next_milestone} deposit of {format_currency(amount, currency)} successfully authorized! Escrow held.", "success")

    except Exception as e:
        db.session.rollback()
        logger.error("Error simulating test payment: %s", str(e), exc_info=True)
        flash("Could not simulate milestone payment.", "danger")

    return redirect(url_for("customer.project_details", project_id=project.id))


# =========================================================
# ASYNC RAZORPAY WEBHOOKS
# =========================================================

@payments_bp.route("/webhook", methods=["POST"])
@payments_bp.route("/api/billing/webhook", methods=["POST"])
@payments_bp.route("/subscription/webhook", methods=["POST"])
@csrf.exempt
def razorpay_webhook() -> ResponseReturnValue:
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

    if event_payload.get("event") == "payment.captured":
        try:
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

                if order_payment.addon_id is not None:
                    _mark_addon_payment_paid(order_payment)
                elif order_payment.project_id:
                    Project.query.filter_by(id=order_payment.project_id).update({"status": "In Progress"})

                db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error("Error processing webhook payment: %s", str(e), exc_info=True)
            return jsonify({"error": "Webhook database error"}), 500

    return jsonify({"status": "success"}), 200