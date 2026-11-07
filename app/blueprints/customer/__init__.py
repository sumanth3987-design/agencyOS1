from datetime import datetime
from decimal import Decimal
import json
import logging
from typing import Any
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.models import (
    ActivityLog,
    Business,
    ChatMessage,
    CustomerProfile,
    Dispute,
    Invoice,
    Milestone,
    Notification,
    PACKAGE_PRICES,
    Payment,
    ProfessionalProfile,
    ProfessionalProjectApplication,
    Project,
    ProjectDelivery,
    RazorpaySubscription,
    Review,
    SubscriptionCharge,
    User,
    WalletTransaction,
    calculate_customer_profile_completion,
    get_ist_now,
    get_latest_project_delivery,
)
from app.services.pdf_generator import extract_sow_signature, generate_sow_pdf
from app.utils.currency import (
    SUPPORTED_CURRENCIES,
    compute_financial_split,
    format_currency,
    get_package_price,
    sanitize_currency,
)
from app.utils.project_uploads import save_project_upload

customer_bp = Blueprint("customer", __name__)
logger = logging.getLogger(__name__)


# =========================================================
# CLIENT DASHBOARD (MAIN WORKSPACE)
# =========================================================

@customer_bp.route("/dashboard")
@customer_bp.route("/customer/dashboard")
@login_required
def dashboard() -> Any:
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client"):
        if user_role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        return redirect(url_for("professional.professional_dashboard"))

    projects = (
        Project.query.filter_by(customer_id=current_user.id)
        .order_by(Project.created_at.desc())
        .all()
    )
    profile = CustomerProfile.query.filter_by(user_id=current_user.id).first()
    businesses = Business.query.filter_by(customer_id=current_user.id).all()

    project_ids = [p.id for p in projects]
    try:
        recent_messages = (
            ChatMessage.query.filter(ChatMessage.project_id.in_(project_ids))
            .order_by(ChatMessage.created_at.desc())
            .limit(6)
            .all()
            if project_ids
            else []
        )
    except Exception:
        recent_messages = []

    profile_completion = 85
    if profile:
        try:
            profile_completion = calculate_customer_profile_completion(profile)
        except Exception:
            profile_completion = 85

    return render_template(
        "dashboard.html",
        projects=projects,
        profile=profile,
        businesses=businesses,
        recent_messages=recent_messages,
        profile_completion=profile_completion,
    )


# =========================================================
# DASHBOARD QUICK MESSAGING
# =========================================================

@customer_bp.route("/dashboard/quick-message", methods=["POST"])
@login_required
def dashboard_quick_message() -> Any:
    text = request.form.get("message", "").strip()
    project_id_str = request.form.get("project_id", "").strip().rstrip("-/")

    if not text or not project_id_str:
        flash("Please enter a message and select a project.", "warning")
        return redirect(url_for("customer.dashboard"))

    try:
        pid = int(project_id_str)
        project = Project.query.get(pid)
        if not project or (getattr(current_user, "role", "").lower() != "admin" and project.customer_id != current_user.id):
            flash(f"Project #{pid} not accessible.", "warning")
            return redirect(url_for("customer.dashboard"))

        msg = ChatMessage()
        msg.project_id = pid
        msg.sender_id = current_user.id
        msg.message = text
        msg.created_at = get_ist_now()

        db.session.add(msg)
        db.session.commit()
        flash(f"Note posted to Project #{pid} workspace successfully.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error("Error in dashboard_quick_message: %s", str(e), exc_info=True)
        flash(f"Could not dispatch message: {str(e)}", "danger")

    return redirect(url_for("customer.dashboard"))


# =========================================================
# PROJECT DETAILS & WORKSPACE
# =========================================================

@customer_bp.route("/project/<project_id>")
@customer_bp.route("/project/<int:project_id>")
@login_required
def project_details(project_id: int | str) -> Any:
    clean_id = str(project_id).strip().rstrip("-/")
    try:
        pid = int(clean_id)
    except (ValueError, TypeError):
        flash(f"Invalid project reference: {project_id}", "warning")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get(pid)
    if not project:
        flash(f"Project #{pid} not found.", "warning")
        return redirect(url_for("customer.dashboard"))

    user_role = getattr(current_user, "role", "").lower()
    if user_role != "admin" and project.customer_id != current_user.id:
        flash("Unauthorized access to project.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        latest_delivery = get_latest_project_delivery(project.id)
    except Exception:
        latest_delivery = (
            ProjectDelivery.query.filter_by(project_id=project.id)
            .order_by(ProjectDelivery.submission_number.desc())
            .first()
        )

    deliveries = (
        ProjectDelivery.query.filter_by(project_id=project.id)
        .order_by(ProjectDelivery.submission_number.asc())
        .all()
    )
    activity_logs = (
        ActivityLog.query.filter_by(project_id=project.id)
        .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
        .all()
    )

    try:
        messages = (
            ChatMessage.query.filter_by(project_id=project.id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )
    except Exception:
        messages = []

    revision_count = ProjectDelivery.query.filter_by(
        project_id=project.id, status="Revision"
    ).count()
    revisions_remaining = max(0, 2 - revision_count)
    payment = (
        Payment.query.filter_by(project_id=project.id)
        .order_by(Payment.created_at.desc())
        .first()
    )

    retainer_subscription_status = None
    retainer_charges = []
    if project.package == "Starter Retainer":
        retainer_subscription = (
            RazorpaySubscription.query.filter_by(project_id=project.id)
            .order_by(RazorpaySubscription.id.desc())
            .first()
        )
        if retainer_subscription is not None:
            retainer_subscription_status = retainer_subscription.status.lower()
            retainer_charges = (
                SubscriptionCharge.query.join(RazorpaySubscription)
                .filter(RazorpaySubscription.project_id == project.id)
                .order_by(SubscriptionCharge.charged_at.desc())
                .all()
            )

    return render_template(
        "customer_project_details.html",
        project=project,
        payment=payment,
        currency=sanitize_currency(project.currency),
        latest_delivery=latest_delivery,
        deliveries=deliveries,
        activity_logs=activity_logs,
        messages=messages,
        revision_count=revision_count,
        revisions_remaining=revisions_remaining,
        sow_signed=extract_sow_signature(project.requirements) is not None,
        retainer_subscription_status=retainer_subscription_status,
        retainer_charges=retainer_charges,
    )


# =========================================================
# IN-PROJECT CHAT NOTE
# =========================================================

@customer_bp.route("/project/<project_id>/message", methods=["POST"])
@customer_bp.route("/project/<int:project_id>/message", methods=["POST"])
@login_required
def send_project_message(project_id: int | str) -> Any:
    clean_id = str(project_id).strip().rstrip("-/")
    try:
        pid = int(clean_id)
    except ValueError:
        flash("Invalid project reference.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(pid)
    user_role = getattr(current_user, "role", "").lower()
    if user_role != "admin" and project.customer_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("customer.dashboard"))

    text = request.form.get("message", "").strip()
    if not text:
        flash("Message cannot be empty.", "warning")
        return redirect(url_for("customer.project_details", project_id=pid))

    try:
        msg = ChatMessage()
        msg.project_id = pid
        msg.sender_id = current_user.id
        msg.message = text
        msg.created_at = get_ist_now()

        db.session.add(msg)
        db.session.commit()
        flash("Note dispatched to project discussion.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error("Error in send_project_message: %s", str(e), exc_info=True)
        flash(f"Could not dispatch message: {str(e)}", "danger")

    return redirect(url_for("customer.project_details", project_id=pid))


# =========================================================
# PRINTABLE GST TAX INVOICE
# =========================================================

@customer_bp.route("/project/<project_id>/invoice")
@customer_bp.route("/project/<int:project_id>/invoice")
@login_required
def view_invoice(project_id: int | str) -> Any:
    clean_id = str(project_id).strip().rstrip("-/")
    try:
        pid = int(clean_id)
    except ValueError:
        flash("Invalid project identifier.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(pid)

    user_role = getattr(current_user, "role", "").lower()
    if user_role != "admin" and project.customer_id != current_user.id:
        flash("Unauthorized access to invoice.", "danger")
        return redirect(url_for("customer.dashboard"))

    invoice_id = request.args.get("invoice_id", type=int)
    if invoice_id is not None:
        invoice_record = Invoice.query.filter_by(
            id=invoice_id,
            project_id=pid,
            customer_id=project.customer_id,
        ).first_or_404()
    else:
        invoice_record = (
            Invoice.query.filter_by(
                project_id=pid,
                customer_id=project.customer_id,
            )
            .order_by(Invoice.id.desc())
            .first()
        )

    customer = project.customer
    profile = customer.customer_profile if customer else None

    invoice_currency = sanitize_currency(
        invoice_record.currency if invoice_record else project.currency
    )

    gross_amount = (
        Decimal(str(invoice_record.gross_amount))
        if invoice_record
        else get_package_price(
            project.package,
            invoice_currency,
            PACKAGE_PRICES,
        )
    )

    talent_payout, agency_profit, tax_total = compute_financial_split(gross_amount, invoice_currency)
    base_amount = gross_amount - tax_total
    cgst_amount = (tax_total / Decimal("2")).quantize(Decimal("0.01"))
    sgst_amount = tax_total - cgst_amount

    is_retainer_invoice = bool(
        invoice_record
        and (
            SubscriptionCharge.query.filter_by(invoice_id=invoice_record.id).first()
            or (invoice_record.invoice_number and invoice_record.invoice_number.startswith("RET-"))
        )
    )

    return render_template(
        "invoice.html",
        project=project,
        invoice_record=invoice_record,
        is_retainer_invoice=is_retainer_invoice,
        customer=customer,
        profile=profile,
        invoice_currency=invoice_currency,
        gross_amount=gross_amount,
        base_amount=base_amount,
        tax_total=tax_total,
        cgst_amount=cgst_amount,
        sgst_amount=sgst_amount,
        invoice_number=(
            invoice_record.invoice_number
            if invoice_record
            else f"INV-2026-{project.id:04d}"
        ),
        invoice_date=(
            invoice_record.created_at.strftime("%d %b %Y")
            if invoice_record and invoice_record.created_at
            else (
                project.created_at.strftime("%d %b %Y")
                if getattr(project, "created_at", None)
                else "01 Nov 2026"
            )
        ),
    )


@customer_bp.route("/project/<project_id>/sow-pdf")
@customer_bp.route("/project/<int:project_id>/sow-pdf")
@login_required
def download_sow_pdf(project_id: int | str) -> Any:
    clean_id = str(project_id).strip().rstrip("-/")
    try:
        pid = int(clean_id)
    except ValueError:
        flash("Invalid project reference.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(pid)
    user_role = getattr(current_user, "role", "").lower()
    if user_role != "admin" and project.customer_id != current_user.id:
        flash("Unauthorized access to agreement.", "danger")
        return redirect(url_for("customer.dashboard"))

    signature = extract_sow_signature(project.requirements)
    if signature is None:
        flash("Sign the SOW before downloading the signed agreement.", "warning")
        return redirect(url_for("customer.project_contract", project_id=project.id))

    try:
        pdf_buffer = generate_sow_pdf(
            project, project.customer, project.business, signature
        )
        return send_file(
            pdf_buffer,
            as_attachment=True,
            download_name=f"AgencyOS_SOW_Project_{project.id}.pdf",
            mimetype="application/pdf",
        )
    except Exception as exc:
        logger.error("Failed to generate SOW PDF: %s", str(exc), exc_info=True)
        flash("Error generating PDF document. Please try again.", "danger")
        return redirect(url_for("customer.project_details", project_id=project.id))


# =========================================================
# CLIENT INVOICES / BILLING LEDGER
# =========================================================

@customer_bp.route("/invoices")
@customer_bp.route("/customer/invoices")
@login_required
def invoices_list() -> Any:
    user_role = getattr(current_user, "role", "").lower()
    if user_role == "admin":
        invoices = Invoice.query.order_by(Invoice.id.desc()).all()
    else:
        invoices = (
            Invoice.query.filter_by(customer_id=current_user.id)
            .order_by(Invoice.id.desc())
            .all()
        )

    return render_template("client_invoices.html", invoices=invoices)


# =========================================================
# DIGITAL SOW CONTRACT & E-SIGNATURE
# =========================================================

@customer_bp.route("/project/<project_id>/contract", methods=["GET", "POST"])
@customer_bp.route("/project/<int:project_id>/contract", methods=["GET", "POST"])
@login_required
def project_contract(project_id: int | str) -> Any:
    clean_id = str(project_id).strip().rstrip("-/")
    try:
        pid = int(clean_id)
    except ValueError:
        flash("Invalid project reference.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(pid)
    user_role = getattr(current_user, "role", "").lower()

    if user_role != "admin" and project.customer_id != current_user.id:
        flash("Unauthorized access to contract.", "danger")
        return redirect(url_for("customer.dashboard"))

    customer = project.customer
    business = project.business

    if request.method == "POST":
        if extract_sow_signature(project.requirements):
            flash("This SOW has already been digitally signed.", "warning")
            return redirect(url_for("customer.project_details", project_id=project.id))

        if request.form.get("agreed_terms") != "on":
            flash("You must accept the SOW terms before signing.", "warning")
            return redirect(url_for("customer.project_contract", project_id=project.id))

        signer_name = request.form.get("signer_name", "").strip()
        signer_title = request.form.get("signer_title", "").strip()
        if not signer_name or not signer_title:
            flash("Enter the signer's full name and title before signing.", "warning")
            return redirect(url_for("customer.project_contract", project_id=project.id))

        try:
            signature_entry = f" [Signed by: {signer_name} ({signer_title}) from IP: {request.remote_addr} on {get_ist_now().strftime('%d %b %Y %H:%M IST')}]"
            if not getattr(project, "requirements", ""):
                project.requirements = signature_entry
            else:
                project.requirements = project.requirements + signature_entry

            project.status = "Payment Pending"

            ActivityLog.log_event(
                project_id=project.id,
                action="SOW_SIGNED",
                description=f"Statement of Work digitally signed by {signer_name} ({signer_title}).",
                user_id=current_user.id,
                category="success",
            )
            db.session.commit()
            flash("SOW Agreement digitally signed. Please authorize Milestone 1 Advance (50%) to begin.", "success")
            return redirect(url_for("customer.project_details", project_id=project.id))

        except Exception as e:
            db.session.rollback()
            logger.error("Error signing SOW: %s", str(e), exc_info=True)
            flash("Failed to record digital signature. Please try again.", "danger")
            return redirect(url_for("customer.project_contract", project_id=project.id))

    return render_template(
        "contract.html",
        project=project,
        customer=customer,
        business=business,
    )


# =========================================================
# SEQUENTIAL 3-GATE DELIVERABLE APPROVAL
# =========================================================

@customer_bp.route("/project/<project_id>/approve", methods=["POST"])
@customer_bp.route("/project/<int:project_id>/approve", methods=["POST"])
@login_required
def customer_approve_project(project_id: int | str) -> Any:
    clean_id = str(project_id).strip().rstrip("-/")
    try:
        pid = int(clean_id)
    except ValueError:
        flash("Invalid project reference.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(pid)

    user_role = getattr(current_user, "role", "").lower()
    if user_role != "admin" and project.customer_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        latest_delivery = get_latest_project_delivery(project.id)
        if latest_delivery:
            latest_delivery.status = "Approved"
            latest_delivery.reviewed_at = get_ist_now()

        project.revision_comment = None

        paid_payments_count = Payment.query.filter_by(project_id=project.id, status="Paid", addon_id=None).count()

        assigned_app = ProfessionalProjectApplication.query.filter_by(
            project_id=project.id, status="Assigned"
        ).first()

        specialist_id = project.assigned_professional_id or (assigned_app.professional_id if assigned_app else None)
        specialist_profile = ProfessionalProfile.query.filter_by(user_id=specialist_id).first() if specialist_id else None

        currency = sanitize_currency(project.currency)
        gross = get_package_price(project.package, currency, PACKAGE_PRICES)
        talent_share, _, _ = compute_financial_split(gross, currency)
        total_talent_payout = Decimal(str(assigned_app.proposed_rate)) if (assigned_app and assigned_app.proposed_rate is not None) else talent_share

        milestones = Milestone.query.filter_by(project_id=project.id).order_by(Milestone.id.asc()).all()

        if paid_payments_count <= 1:
            project.status = "In Progress"
            milestone_ratio = Decimal("0.50")
            earned_amount = (total_talent_payout * milestone_ratio).quantize(Decimal("0.01"))

            if specialist_profile:
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
                tx.description = f"Project #{project.id} Step 1 (UI/UX) approved. {format_currency(earned_amount, currency)} released to available balance."
                tx.created_at = get_ist_now()
                db.session.add(tx)

                notif = Notification()
                notif.user_id = specialist_id
                notif.title = f"Step 1 (UI/UX) Approved! {format_currency(earned_amount, currency)} Unlocked 💰"
                notif.message = f"Client approved UI/UX prototype for Project #{project.id}."
                notif.link = "/professional/dashboard"
                notif.created_at = get_ist_now()
                db.session.add(notif)

            if milestones and len(milestones) >= 1:
                milestones[0].status = "released"
                milestones[0].released_at = get_ist_now()

            flash_msg = "Step 1 (UI/UX Prototype) approved! Gate 2 (25% Staging Escrow) is now unlocked for payment."

        elif paid_payments_count == 2:
            project.status = "In Progress"
            milestone_ratio = Decimal("0.25")
            earned_amount = (total_talent_payout * milestone_ratio).quantize(Decimal("0.01"))

            if specialist_profile:
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
                tx.description = f"Project #{project.id} Step 2 (Staging) approved. {format_currency(earned_amount, currency)} released to available balance."
                tx.created_at = get_ist_now()
                db.session.add(tx)

                notif = Notification()
                notif.user_id = specialist_id
                notif.title = f"Step 2 (Staging) Approved! {format_currency(earned_amount, currency)} Unlocked 💰"
                notif.message = f"Client approved the staging build for Project #{project.id}."
                notif.link = "/professional/dashboard"
                notif.created_at = get_ist_now()
                db.session.add(notif)

            if milestones and len(milestones) >= 2:
                milestones[1].status = "released"
                milestones[1].released_at = get_ist_now()

            flash_msg = "Step 2 (Staging Build) approved! Gate 3 (25% Final Production & Handover) is now unlocked for payment."

        else:
            project.status = "Completed"

            if specialist_profile:
                curr_pend = Decimal(str(specialist_profile.pending_balance or 0))
                curr_avail = Decimal(str(specialist_profile.available_balance or 0))
                remaining_to_release = curr_pend if curr_pend > Decimal("0.00") else (total_talent_payout * Decimal("0.25")).quantize(Decimal("0.01"))
                
                specialist_profile.available_balance = curr_avail + remaining_to_release
                specialist_profile.pending_balance = Decimal("0.00")

                tx = WalletTransaction()
                tx.user_id = specialist_id
                tx.amount = remaining_to_release
                tx.currency = currency
                tx.transaction_type = "ESCROW_RELEASED"
                tx.reference_type = "PROJECT"
                tx.reference_id = project.id
                tx.description = f"Project #{project.id} final handover approved."
                tx.created_at = get_ist_now()
                db.session.add(tx)

                notif = Notification()
                notif.user_id = specialist_id
                notif.title = f"🎉 Project Completed! {format_currency(remaining_to_release, currency)} Final Release"
                notif.message = f"Client approved final handover for Project #{project.id}."
                notif.link = "/professional/dashboard"
                notif.created_at = get_ist_now()
                db.session.add(notif)

            for m in milestones:
                if m.status != "released":
                    m.status = "released"
                    m.released_at = get_ist_now()

            flash_msg = "Final milestone approved! Project marked Completed and all remaining escrow funds released."

        ActivityLog.log_event(
            project_id=project.id,
            action="MILESTONE_APPROVED",
            description=f"Client approved milestone deliverable. Project status: {project.status}.",
            user_id=current_user.id,
            category="success",
        )

        db.session.commit()
        flash(flash_msg, "success")

    except Exception as e:
        db.session.rollback()
        logger.error("Error approving project milestone: %s", str(e), exc_info=True)
        flash("An error occurred during milestone clearance. Please try again.", "danger")

    return redirect(url_for("customer.project_details", project_id=pid))


# =========================================================
# REVISION REQUEST GATE
# =========================================================

@customer_bp.route("/project/<project_id>/revision", methods=["POST"])
@customer_bp.route("/project/<int:project_id>/revision", methods=["POST"])
@login_required
def customer_request_revision(project_id: int | str) -> Any:
    clean_id = str(project_id).strip().rstrip("-/")
    try:
        pid = int(clean_id)
    except ValueError:
        flash("Invalid project reference.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(pid)

    user_role = getattr(current_user, "role", "").lower()
    if user_role != "admin" and project.customer_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("customer.dashboard"))

    revision_count = ProjectDelivery.query.filter_by(
        project_id=project.id, status="Revision"
    ).count()
    if revision_count >= 2:
        flash("Maximum 2 complimentary revisions reached. Order a Paid Scope Add-on for extra modifications.", "warning")
        return redirect(url_for("customer.project_details", project_id=pid))

    comment = request.form.get("revision_comment", "").strip()
    if not comment:
        flash("Please provide details for the revision request.", "warning")
        return redirect(url_for("customer.project_details", project_id=pid))

    try:
        latest_delivery = get_latest_project_delivery(project.id)
        if latest_delivery:
            latest_delivery.status = "Revision"
            latest_delivery.review_comment = comment
            latest_delivery.reviewed_at = get_ist_now()

        project.status = "Revision"
        project.revision_comment = comment
        ActivityLog.log_event(
            project_id=project.id,
            action="REVISION_REQUESTED",
            description=f"Client requested a revision: {comment}",
            user_id=current_user.id,
            category="warning",
        )
        db.session.commit()
        flash("Revision feedback logged. Your specialist has been notified.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error("Error requesting revision: %s", str(e), exc_info=True)
        flash("Could not log revision feedback. Please try again.", "danger")

    return redirect(url_for("customer.project_details", project_id=pid))


# =========================================================
# CLIENT REVIEW & TESTIMONIAL SUBMISSION
# =========================================================

@customer_bp.route("/project/<project_id>/review", methods=["POST"])
@customer_bp.route("/project/<int:project_id>/review", methods=["POST"])
@login_required
def submit_project_review(project_id: int | str) -> Any:
    clean_id = str(project_id).strip().rstrip("-/")
    try:
        pid = int(clean_id)
    except ValueError:
        flash("Invalid project reference.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(pid)
    if project.customer_id != current_user.id and getattr(current_user, "role", "").lower() != "admin":
        flash("Unauthorized action.", "danger")
        return redirect(url_for("customer.dashboard"))

    rating = request.form.get("rating", 5, type=int)
    tags = ", ".join(request.form.getlist("tags"))
    feedback = request.form.get("feedback", "").strip()

    if not 1 <= rating <= 5:
        flash("Choose a rating from 1 to 5 stars.", "warning")
        return redirect(url_for("customer.project_details", project_id=project.id))

    if not feedback:
        flash("Please provide review feedback.", "warning")
        return redirect(url_for("customer.project_details", project_id=project.id))

    try:
        existing_review = Review.query.filter_by(project_id=project.id).first()
        if existing_review:
            existing_review.rating = rating
            existing_review.tags = tags
            existing_review.feedback = feedback
        else:
            new_review = Review()
            new_review.project_id = project.id
            new_review.customer_id = current_user.id
            new_review.rating = rating
            new_review.tags = tags
            new_review.feedback = feedback
            new_review.is_public = True
            db.session.add(new_review)
        db.session.commit()
        flash("Review logged successfully. Thank you for your feedback.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error("Error submitting review: %s", str(e), exc_info=True)
        flash(f"Could not record review: {str(e)}", "danger")

    return redirect(url_for("customer.project_details", project_id=project.id))


# =========================================================
# BUSINESS ENTITY MANAGEMENT
# =========================================================

@customer_bp.route("/businesses")
@customer_bp.route("/business")
@login_required
def businesses() -> Any:
    user_businesses = Business.query.filter_by(customer_id=current_user.id).all()
    return render_template("businesses.html", businesses=user_businesses)


@customer_bp.route("/add-business", methods=["GET", "POST"])
@customer_bp.route("/business/add", methods=["GET", "POST"])
@customer_bp.route("/businesses/add", methods=["GET", "POST"])
@login_required
def add_business() -> Any:
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        b_type = request.form.get("business_type", "").strip()
        location = request.form.get("location", "").strip()
        website = request.form.get("website", "").strip()

        if not name:
            flash("Business name is required.", "danger")
            return render_template("add_business.html")

        try:
            b = Business()
            b.customer_id = current_user.id
            b.name = name
            b.business_type = b_type
            b.location = location
            b.website = website
            db.session.add(b)
            db.session.commit()
            flash("Business entity created successfully.", "success")
            return redirect(url_for("customer.businesses"))
        except Exception as e:
            db.session.rollback()
            logger.error("Error creating business: %s", str(e), exc_info=True)
            flash("Failed to save business entity. Please try again.", "danger")

    return render_template("add_business.html")


@customer_bp.route("/business/<int:business_id>/edit", methods=["GET", "POST"])
@customer_bp.route("/businesses/<int:business_id>/edit", methods=["GET", "POST"])
@login_required
def edit_business(business_id: int) -> Any:
    business = Business.query.filter_by(id=business_id, customer_id=current_user.id).first_or_404()

    if request.method == "POST":
        name = request.form.get("name", business.name).strip()
        if not name:
            flash("Business name cannot be empty.", "danger")
            return render_template("edit_business.html", business=business)

        try:
            business.name = name
            business.business_type = request.form.get("business_type", business.business_type).strip()
            business.location = request.form.get("location", business.location).strip()
            business.website = request.form.get("website", business.website).strip()
            db.session.commit()
            flash("Business entity updated successfully.", "success")
            return redirect(url_for("customer.businesses"))
        except Exception as e:
            db.session.rollback()
            logger.error("Error updating business: %s", str(e), exc_info=True)
            flash("Failed to update business entity.", "danger")

    return render_template("edit_business.html", business=business)


@customer_bp.route("/business/<int:business_id>/delete", methods=["POST"])
@customer_bp.route("/businesses/<int:business_id>/delete", methods=["POST"])
@login_required
def delete_business(business_id: int) -> Any:
    business = Business.query.filter_by(id=business_id, customer_id=current_user.id).first_or_404()
    try:
        Project.query.filter_by(business_id=business.id).update({"business_id": None})
        db.session.delete(business)
        db.session.commit()
        flash(f"Business entity '{business.name}' deleted.", "success")
    except Exception as e:
        db.session.rollback()
        logger.error("Error deleting business: %s", str(e), exc_info=True)
        flash("Could not delete business entity.", "danger")

    return redirect(url_for("customer.businesses"))


# =========================================================
# CUSTOMER PROFILE & SETTINGS
# =========================================================

@customer_bp.route("/customer/profile", methods=["GET", "POST"])
@customer_bp.route("/profile", methods=["GET", "POST"])
@customer_bp.route("/customer_profile", methods=["GET", "POST"])
@login_required
def customer_profile() -> Any:
    profile = CustomerProfile.query.filter_by(user_id=current_user.id).first()
    
    if not profile:
        try:
            profile = CustomerProfile(user_id=current_user.id)
            db.session.add(profile)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            profile = CustomerProfile.query.filter_by(user_id=current_user.id).first()
        except Exception as e:
            db.session.rollback()
            logger.error("Error auto-creating customer profile: %s", str(e))

    if profile is None:
        flash("Unable to load or initialize customer profile coordinates.", "danger")
        return redirect(url_for("customer.dashboard"))

    if request.method == "POST":
        try:
            profile.phone = request.form.get("phone", "").strip()
            profile.secondary_phone = request.form.get("secondary_phone", "").strip()
            profile.preferred_channel = request.form.get("preferred_channel", "WhatsApp").strip()
            profile.available_hours = request.form.get("available_hours", "").strip()
            profile.billing_email = request.form.get("billing_email", "").strip().lower()

            profile.business_name = request.form.get("business_name", "").strip()
            profile.business_type = request.form.get("business_type", "").strip()
            profile.website = request.form.get("website", "").strip()
            profile.gstin = request.form.get("gstin", "").strip().upper()
            profile.billing_address = request.form.get("billing_address", "").strip()
            profile.pincode = request.form.get("pincode", "").strip()
            profile.location = request.form.get("location", "").strip()

            profile.signatory_name = request.form.get("signatory_name", "").strip()
            profile.signatory_title = request.form.get("signatory_title", "").strip()

            profile.profile_completed = True
            db.session.commit()
            flash("Organization profile and direct communication coordinates saved.", "success")
            return redirect(url_for("customer.customer_profile"))
        except Exception as e:
            db.session.rollback()
            logger.error("Error updating customer profile: %s", str(e), exc_info=True)
            flash("Failed to update profile coordinates.", "danger")

    return render_template("customer_profile.html", profile=profile)


# =========================================================
# START PROJECT
# =========================================================

@customer_bp.route("/start-project/<service_slug>", methods=["GET", "POST"])
@customer_bp.route("/start-project", methods=["GET", "POST"])
@customer_bp.route("/project/start", methods=["GET", "POST"])
@login_required
def start_project(service_slug: str = "website-development") -> Any:
    businesses = Business.query.filter_by(customer_id=current_user.id).all()

    if request.method == "POST":
        service = (
            request.form.get("service")
            or request.form.get("service_name")
            or request.form.get("service_type")
            or request.form.get("title")
            or service_slug.replace("-", " ").title()
            or "Website Design & Development"
        ).strip()

        package = (
            request.form.get("package")
            or "Basic Website"
        ).strip()

        currency = sanitize_currency(
            request.form.get("currency") or "INR"
        )

        brief = (
            request.form.get("brief")
            or request.form.get("requirements")
            or request.form.get("description")
            or "Standard project kickoff and milestone deliverables."
        ).strip()

        raw_skills = request.form.get("required_skills", "").strip() or request.form.get("skills", "").strip()
        if not raw_skills:
            lower_service = service.lower()
            if "seo" in lower_service:
                raw_skills = "SEO, Technical SEO, Keyword Research, Content Strategy"
            elif "app" in lower_service or "custom" in lower_service:
                raw_skills = "Python, Flask, React, PostgreSQL, API Integration"
            else:
                raw_skills = "HTML, CSS, JavaScript, Responsive UI, WordPress"

        if package not in PACKAGE_PRICES:
            package = "Basic Website"

        if package == "Starter Retainer" and currency != "INR":
            flash("Starter Retainer billing is currently available in INR only.", "warning")
            return redirect(url_for("customer.start_project", service_slug=service_slug))

        business_id_value = request.form.get("business_id", "").strip()
        target_business_id = None
        if business_id_value:
            try:
                target_business_id = int(business_id_value)
            except ValueError:
                target_business_id = None

        if target_business_id is None and businesses:
            target_business_id = businesses[0].id

        sections = [
            ("Business / Brand", request.form.get("brand_name", "").strip()),
            ("Industry", request.form.get("industry", "").strip()),
            ("Brand Colors", request.form.get("brand_colors", "").strip()),
            ("Logo / Asset Link", request.form.get("asset_link", "").strip()),
            ("Target Launch Timeline", request.form.get("deadline", "").strip()),
            ("Competitor / Reference Websites", request.form.get("reference_sites", "").strip()),
            ("Target Audience", request.form.get("target_audience", "").strip()),
            ("Target Cities", request.form.get("target_cities", "").strip()),
            ("Target SEO Keywords", request.form.get("target_keywords", "").strip()),
            ("Requested Features", ", ".join(request.form.getlist("features"))),
            ("Custom Features", request.form.get("custom_features", "").strip()),
            ("Scope Narrative", brief),
        ]
        reqs = "\n".join(f"{label}: {value}" for label, value in sections if value) or brief

        try:
            attachment_url = save_project_upload(
                request.files.get("srs_document"),
                {"pdf", "doc", "docx", "png", "jpg", "jpeg", "zip"},
            )
        except Exception:
            attachment_url = None

        if attachment_url:
            reqs += f"\n\nAttached Scope Document: {attachment_url}"

        try:
            existing_draft = (
                Project.query.filter_by(
                    customer_id=current_user.id, status="Payment Pending"
                )
                .order_by(Project.created_at.desc())
                .first()
            )

            if existing_draft and not extract_sow_signature(existing_draft.requirements):
                existing_draft.service = service
                existing_draft.package = package
                existing_draft.currency = currency
                existing_draft.requirements = reqs
                existing_draft.required_skills = raw_skills
                existing_draft.business_id = target_business_id

                if attachment_url:
                    ActivityLog.log_event(
                        project_id=existing_draft.id,
                        action="DOCUMENT_UPLOADED",
                        description="Client uploaded an updated project scope document.",
                        user_id=current_user.id,
                        category="info",
                    )
                db.session.commit()
                flash(f"Updated Project #{existing_draft.id}. Review and sign the SOW Agreement.", "info")
                return redirect(url_for("customer.project_contract", project_id=existing_draft.id))

            project = Project()
            project.customer_id = current_user.id
            project.business_id = target_business_id
            project.service = service
            project.package = package
            project.currency = currency
            project.requirements = reqs
            project.required_skills = raw_skills
            project.status = "Payment Pending"

            db.session.add(project)
            db.session.flush()

            pkg_gross = get_package_price(package, currency, PACKAGE_PRICES)
            m1_val = int(round(pkg_gross * Decimal("0.50")))
            m2_val = int(round(pkg_gross * Decimal("0.25")))
            m3_val = int(round(pkg_gross * Decimal("0.25")))

            m1 = Milestone()
            m1.project_id = project.id
            m1.title = "Gate 1: UI/UX Prototype & Specifications (50%)"
            m1.amount = m1_val
            m1.status = "pending"

            m2 = Milestone()
            m2.project_id = project.id
            m2.title = "Gate 2: Functional Staging Build (25%)"
            m2.amount = m2_val
            m2.status = "pending"

            m3 = Milestone()
            m3.project_id = project.id
            m3.title = "Gate 3: Production Deployment & IP Handover (25%)"
            m3.amount = m3_val
            m3.status = "pending"

            db.session.add_all([m1, m2, m3])

            ActivityLog.log_event(
                project_id=project.id,
                action="PROJECT_CREATED",
                description=f"Project #{project.id} created with required skills: {raw_skills}. Awaiting SOW signature.",
                user_id=current_user.id,
                category="info",
            )
            
            if attachment_url:
                ActivityLog.log_event(
                    project_id=project.id,
                    action="DOCUMENT_UPLOADED",
                    description="Client uploaded a project scope document.",
                    user_id=current_user.id,
                    category="info",
                )
                
            db.session.commit()

            flash("Milestone project created! Please review and sign the SOW Agreement.", "info")
            return redirect(url_for("customer.project_contract", project_id=project.id))

        except Exception as e:
            db.session.rollback()
            logger.error("Error creating project in start_project: %s", str(e), exc_info=True)
            flash("Could not initialize your project workspace. Please try again.", "danger")
            return redirect(url_for("customer.start_project", service_slug=service_slug))

    return render_template("start_project.html", service_slug=service_slug, businesses=businesses)


# =========================================================
# CANCEL UNPAID DRAFT PROJECT
# =========================================================

@customer_bp.route("/project/<project_id>/cancel", methods=["POST"])
@customer_bp.route("/project/<int:project_id>/cancel", methods=["POST"])
@login_required
def cancel_project(project_id: int | str) -> Any:
    clean_id = str(project_id).strip().rstrip("-/")
    try:
        pid = int(clean_id)
    except ValueError:
        flash("Invalid project reference.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.filter_by(id=pid, customer_id=current_user.id).first_or_404()

    if project.status not in ("Payment Pending", "Pending Assignment"):
        flash("Cannot cancel an active or paid project.", "warning")
        return redirect(url_for("customer.dashboard"))

    try:
        db.session.execute(db.delete(Payment).where(Payment.project_id == project.id))
        db.session.execute(db.delete(Milestone).where(Milestone.project_id == project.id))
        db.session.delete(project)
        
        db.session.commit()
        flash(f"Draft Project #{pid} cancelled.", "info")
    except Exception as e:
        db.session.rollback()
        logger.error("Error cancelling project: %s", str(e), exc_info=True)
        flash("Could not cancel draft project.", "danger")

    return redirect(url_for("customer.dashboard"))


@customer_bp.route("/project/<project_id>/raise-dispute", methods=["POST"])
@customer_bp.route("/project/<int:project_id>/raise-dispute", methods=["POST"])
@login_required
def raise_dispute(project_id: int | str) -> Any:
    clean_id = str(project_id).strip().rstrip("-/")
    try:
        pid = int(clean_id)
    except ValueError:
        flash("Invalid project reference.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get_or_404(pid)
    if project.customer_id != current_user.id and getattr(current_user, "role", "").lower() != "admin":
        flash("Unauthorized action.", "danger")
        return redirect(url_for("customer.dashboard"))

    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Please provide a reason for raising this dispute.", "warning")
        return redirect(url_for("customer.project_details", project_id=project.id))

    try:
        project.status = "Disputed"
        dispute = Dispute(
            project_id=project.id,
            raised_by_user_id=current_user.id,
            reason=reason,
            status="OPEN"
        )
        db.session.add(dispute)

        ActivityLog.log_event(
            project_id=project.id,
            action="DISPUTE_RAISED",
            description=f"Escrow dispute raised by client: {reason}",
            user_id=current_user.id,
            category="danger",
        )

        for admin in User.query.filter_by(role="admin").all():
            notif = Notification(
                user_id=admin.id,
                title=f"⚠️ Escrow Dispute Raised: Project #{project.id}",
                message=f"{current_user.name} raised a dispute on Project #{project.id}. Admin arbitration required.",
                link=url_for("admin.admin_disputes")
            )
            db.session.add(notif)

        db.session.commit()
        flash("Escrow dispute logged. Agency administration has frozen funds and stepped in for arbitration.", "warning")
    except Exception as e:
        db.session.rollback()
        logger.error("Error raising dispute: %s", str(e), exc_info=True)
        flash("Error submitting dispute. Please try again.", "danger")

    return redirect(url_for("customer.project_details", project_id=project.id))


@customer_bp.route("/api/project/<int:project_id>/messages")
@login_required
def api_get_messages(project_id: int) -> Any:
    project = Project.query.get_or_404(project_id)
    if project.customer_id != current_user.id and getattr(current_user, "role", "").lower() != "admin":
        return jsonify({"error": "Unauthorized"}), 403
    
    messages = ChatMessage.query.filter_by(project_id=project.id).order_by(ChatMessage.created_at.asc()).all()
    data = [{
        "id": m.id,
        "sender_name": m.sender.name if m.sender else "Agency Team",
        "message": m.message,
        "time": m.created_at.strftime("%d %b %I:%M %p"),
        "is_me": m.sender_id == current_user.id
    } for m in messages]
    return jsonify({"success": True, "messages": data})
