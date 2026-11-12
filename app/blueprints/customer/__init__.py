"""AgencyOS Customer & Client Portal Blueprint."""

from datetime import datetime
from decimal import Decimal
import json
import logging
import os
import secrets
from typing import Any
from flask import (
    Blueprint, flash, jsonify, redirect, render_template, 
    request, send_file, url_for, abort
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import (
    ActivityLog, Business, ChatMessage, CustomerProfile, Dispute,
    Invoice, Milestone, Notification, PACKAGE_PRICES, Payment,
    ProfessionalProfile, ProfessionalProjectApplication, Project,
    ProjectAddOn, ProjectDelivery, RazorpaySubscription, RetainerTask,
    Review, SubscriptionCharge, User, WalletTransaction,
    calculate_customer_profile_completion, get_ist_now, get_latest_project_delivery,
)
from app.services.pdf_generator import (
    extract_sow_signature, generate_acceptance_certificate_pdf, generate_sow_pdf,
)
from app.services.whatsapp import notify_project_completed, notify_revision_requested
from app.utils.currency import (
    compute_financial_split, format_currency, get_package_price, sanitize_currency,
)
from app.utils.project_uploads import save_project_upload

customer_bp = Blueprint("customer", __name__)
logger = logging.getLogger(__name__)

ALLOWED_UPLOAD_EXTENSIONS = {"pdf", "doc", "docx", "png", "jpg", "jpeg", "zip"}
MAX_FILE_SIZE = 10 * 1024 * 1024

ADDON_CATALOG_PRICES = {
    "Payment Gateway Integration": {"INR": Decimal("12000.00"), "USD": Decimal("150.00")},
    "Extra 3 Static Pages": {"INR": Decimal("6000.00"), "USD": Decimal("80.00")},
    "Speed & Core Web Vitals Optimization": {"INR": Decimal("8000.00"), "USD": Decimal("100.00")},
    "3-Hour Retainer Boost Pack": {"INR": Decimal("5999.00"), "USD": Decimal("75.00")},
}

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

    # Eager loading: eliminate N+1 queries across project relations
    projects = (
        Project.query.options(
            joinedload(Project.business),
            selectinload(Project.milestones),
            selectinload(Project.payments),
        )
        .filter_by(customer_id=current_user.id)
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
        project = db.session.get(Project, pid)
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
# PROJECT DETAILS & WORKSPACE (WITH RETAINER TASKS)
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

    project = db.session.get(Project, pid)
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

    # Load micro-tasks for the retainer hours bank
    retainer_tasks = (
        RetainerTask.query.filter_by(project_id=project.id)
        .order_by(RetainerTask.created_at.desc())
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
        retainer_tasks=retainer_tasks,
    )


# =========================================================
# CLIENT DROP RETAINER TASK (HOURS BANK EXECUTION)
# =========================================================

@customer_bp.route("/project/<project_id>/drop-task", methods=["POST"])
@customer_bp.route("/project/<int:project_id>/drop-task", methods=["POST"])
@login_required
def drop_retainer_task(project_id: int | str) -> Any:
    clean_id = str(project_id).strip().rstrip("-/")
    try:
        pid = int(clean_id)
    except ValueError:
        flash("Invalid project reference.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = db.session.get(Project, pid)
    if not project or (project.customer_id != current_user.id and getattr(current_user, "role", "").lower() != "admin"):
        flash("Unauthorized action.", "danger")
        return redirect(url_for("customer.dashboard"))

    if (Decimal(str(project.hours_remaining or 0))) <= Decimal("0.00"):
        flash(
            "Monthly Hours Exhausted: You have utilized all 5 hours for this cycle. "
            "Please purchase a 3-Hour Retainer Boost Pack or await cycle renewal.",
            "warning",
        )
        return redirect(url_for("customer.project_details", project_id=project.id))

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()

    if not title or not description:
        flash("Please provide both a title and detailed instructions for the task.", "warning")
        return redirect(url_for("customer.project_details", project_id=project.id))

    asset_url = None
    upload_file = request.files.get("asset_file")
    if upload_file and upload_file.filename:
        try:
            asset_url = save_project_upload(upload_file, ALLOWED_UPLOAD_EXTENSIONS)
        except ValueError as exc:
            flash(f"File upload blocked: {str(exc)}", "danger")
            return redirect(url_for("customer.project_details", project_id=project.id))
        except Exception as exc:
            logger.error("Asset file upload failed: %s", str(exc), exc_info=True)
            flash("Could not upload the attachment. The file might be corrupted.", "danger")
            return redirect(url_for("customer.project_details", project_id=project.id))

    try:
        task = RetainerTask(
            project_id=project.id,
            customer_id=current_user.id,
            title=title,
            description=description,
            specialist_id=project.assigned_professional_id,
            status="Queued",
            asset_url=asset_url,
        )
        db.session.add(task)

        # Notify assigned specialist
        if project.assigned_professional_id:
            notif = Notification()
            notif.user_id = project.assigned_professional_id
            notif.title = f"New Retainer Task: {title}"
            notif.message = f"Client requested maintenance tweak for Project #{project.id}. Open workspace to claim and log time."
            notif.link = f"/professional/project/{project.id}/workspace"
            notif.created_at = get_ist_now()
            db.session.add(notif)

        ActivityLog.log_event(
            project_id=project.id,
            action="TASK_SUBMITTED",
            description=f"Client dropped maintenance task: {title}" + (" (with attachment)" if asset_url else ""),
            user_id=current_user.id,
            category="info",
        )

        db.session.commit()
        flash(f"Maintenance task '{title}' queued! Your specialist has been notified.", "success")

    except Exception as e:
        db.session.rollback()
        logger.error("Error creating retainer task for project %s: %s", project.id, str(e), exc_info=True)
        flash("Could not queue maintenance task. Please try again.", "danger")

    return redirect(url_for("customer.project_details", project_id=project.id))


# =========================================================
# SELF-SERVICE SCOPE EXPANSION / ADD-ON REQUEST ROUTE
# =========================================================

@customer_bp.route("/project/<project_id>/request-addon", methods=["POST"])
@customer_bp.route("/project/<int:project_id>/request-addon", methods=["POST"])
@login_required
def request_scope_addon(project_id: int | str) -> Any:
    clean_id = str(project_id).strip().rstrip("-/")
    try:
        pid = int(clean_id)
    except ValueError:
        flash("Invalid project reference.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = db.session.get(Project, pid)
    if not project or (project.customer_id != current_user.id and getattr(current_user, "role", "").lower() != "admin"):
        flash("Unauthorized action.", "danger")
        return redirect(url_for("customer.dashboard"))

    addon_package = request.form.get("addon_package", "").strip()
    currency = sanitize_currency(project.currency)

    if addon_package in ADDON_CATALOG_PRICES:
        price_map = ADDON_CATALOG_PRICES[addon_package]
        gross_amount = price_map.get(currency, price_map["INR"])
        title = addon_package
    else:
        flash("Selected add-on package is invalid.", "warning")
        return redirect(url_for("customer.project_details", project_id=project.id))

    try:
        _, agency_profit, tax_amount = compute_financial_split(gross_amount, currency)

        invoice = Invoice()
        invoice.invoice_number = f"ADDON-{project.id}-{secrets.token_hex(3).upper()}"
        invoice.project_id = project.id
        invoice.customer_id = project.customer_id
        invoice.gross_amount = gross_amount
        invoice.talent_payout = Decimal("0.00")
        invoice.agency_profit = agency_profit
        invoice.tax_amount = tax_amount
        invoice.currency = currency
        invoice.status = "Pending"
        invoice.payout_status = "Pending Approval"

        db.session.add(invoice)
        db.session.flush()

        addon = ProjectAddOn()
        addon.project_id = project.id
        addon.invoice_id = invoice.id
        addon.title = title
        addon.amount = gross_amount
        addon.status = "Pending Payment"

        db.session.add(addon)

        ActivityLog.log_event(
            project_id=project.id,
            action="ADDON_ORDERED",
            description=f"Client ordered scope expansion: {title} ({format_currency(gross_amount, currency)}).",
            user_id=current_user.id,
            category="info",
        )

        db.session.commit()
        flash(f"Scope expansion '{title}' created. Please complete payment authorization to attach to sprint.", "info")
        return redirect(url_for("payments.payment_page", project_id=project.id, addon_id=addon.id))

    except Exception as e:
        db.session.rollback()
        logger.error("Error creating client scope add-on for project %s: %s", project.id, str(e), exc_info=True)
        flash("Could not initialize scope add-on. Please try again.", "danger")
        return redirect(url_for("customer.project_details", project_id=project.id))


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

    project = db.session.get(Project, pid)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("customer.dashboard"))

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

    project = db.session.get(Project, pid)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("customer.dashboard"))

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

    customer = getattr(project, "customer", None) or db.session.get(User, project.customer_id)
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


# =========================================================
# DIGITAL SOW PDF DOWNLOAD
# =========================================================

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

    project = db.session.get(Project, pid)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("customer.dashboard"))

    user_role = getattr(current_user, "role", "").lower()
    if user_role != "admin" and project.customer_id != current_user.id:
        flash("Unauthorized access to agreement.", "danger")
        return redirect(url_for("customer.dashboard"))

    signature = extract_sow_signature(project.requirements)
    if signature is None:
        flash("Sign the SOW before downloading the signed agreement.", "warning")
        return redirect(url_for("customer.project_contract", project_id=project.id))

    try:
        customer_user = getattr(project, "customer", None) or db.session.get(User, project.customer_id)
        pdf_buffer = generate_sow_pdf(
            project, customer_user, project.business, signature
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
# ANTI-CHARGEBACK HANDOVER & ACCEPTANCE CERTIFICATE PDF
# =========================================================

@customer_bp.route("/project/<project_id>/certificate-pdf")
@customer_bp.route("/project/<int:project_id>/certificate-pdf")
@login_required
def download_acceptance_certificate(project_id: int | str) -> Any:
    clean_id = str(project_id).strip().rstrip("-/")
    try:
        pid = int(clean_id)
    except ValueError:
        flash("Invalid project reference.", "danger")
        return redirect(url_for("customer.dashboard"))

    project = db.session.get(Project, pid)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("customer.dashboard"))

    user_role = getattr(current_user, "role", "").lower()
    if user_role != "admin" and project.customer_id != current_user.id:
        flash("Unauthorized access to certificate.", "danger")
        return redirect(url_for("customer.dashboard"))

    if project.status != "Completed":
        flash("The Handover & Acceptance Certificate unlocks upon 100% final milestone sign-off.", "warning")
        return redirect(url_for("customer.project_details", project_id=project.id))

    try:
        latest_delivery = get_latest_project_delivery(project.id)
        customer_user = getattr(project, "customer", None) or db.session.get(User, project.customer_id)
        pdf_buffer = generate_acceptance_certificate_pdf(
            project, customer_user, project.business, latest_delivery
        )
        return send_file(
            pdf_buffer,
            as_attachment=True,
            download_name=f"AgencyOS_Acceptance_Certificate_Project_{project.id}.pdf",
            mimetype="application/pdf",
        )
    except Exception as exc:
        logger.error("Failed to generate Acceptance Certificate PDF: %s", str(exc), exc_info=True)
        flash("Error generating Acceptance Certificate. Please try again.", "danger")
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
        invoices = Invoice.query.options(joinedload(Invoice.project)).order_by(Invoice.id.desc()).all()
    else:
        invoices = (
            Invoice.query.options(joinedload(Invoice.project))
            .filter_by(customer_id=current_user.id)
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

    project = db.session.get(Project, pid)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("customer.dashboard"))

    user_role = getattr(current_user, "role", "").lower()

    if user_role != "admin" and project.customer_id != current_user.id:
        flash("Unauthorized access to contract.", "danger")
        return redirect(url_for("customer.dashboard"))

    customer = getattr(project, "customer", None) or db.session.get(User, project.customer_id)
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

            # Auto-compile SOW PDF
            try:
                sig_obj = extract_sow_signature(project.requirements)
                if sig_obj:
                    pdf_stream = generate_sow_pdf(project, customer, business, sig_obj)
                    sow_dir = os.path.join("static", "uploads", "sow")
                    os.makedirs(sow_dir, exist_ok=True)
                    sow_filename = f"SOW_Project_{project.id}_{secrets.token_hex(4)}.pdf"
                    sow_filepath = os.path.join(sow_dir, sow_filename)
                    with open(sow_filepath, "wb") as f:
                        f.write(pdf_stream.getvalue())
                    project.sow_pdf_path = sow_filepath
            except Exception as pdf_err:
                logger.warning("Auto SOW generation non-fatal notice: %s", str(pdf_err))

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
# SEQUENTIAL 3-GATE DELIVERABLE APPROVAL (TOTAL ESCROW LOCKOUT)
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

    project = db.session.get(Project, pid)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("customer.dashboard"))

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
            milestone_value = (total_talent_payout * milestone_ratio).quantize(Decimal("0.01"))

            if milestones and len(milestones) >= 1:
                milestones[0].status = "completed"
                milestones[0].released_at = get_ist_now()

            if specialist_id:
                tx = WalletTransaction()
                tx.user_id = specialist_id
                tx.amount = milestone_value
                tx.currency = currency
                tx.transaction_type = "ESCROW_LOCKED"
                tx.reference_type = "PROJECT"
                tx.reference_id = project.id
                tx.description = f"Project #{project.id} Step 1 (UI/UX) approved. {format_currency(milestone_value, currency)} secured in escrow (withheld until full project handover)."
                tx.created_at = get_ist_now()
                db.session.add(tx)

            if specialist_profile:
                curr_pend = Decimal(str(specialist_profile.pending_balance or 0))
                specialist_profile.pending_balance = curr_pend + milestone_value
                curr_avail = Decimal(str(specialist_profile.available_balance or 0))
                specialist_profile.available_balance = curr_avail + milestone_value
                db.session.add(specialist_profile)

                notif = Notification()
                notif.user_id = specialist_id
                notif.title = f"Step 1 (UI/UX) Approved! {format_currency(milestone_value, currency)} Escrow Held 🛡️"
                notif.message = f"Client approved UI/UX prototype for Project #{project.id}. Escrow funds will unlock upon final live cutover & handover."
                notif.link = "/professional/dashboard"
                notif.created_at = get_ist_now()
                db.session.add(notif)

            ActivityLog.log_event(
                project_id=project.id,
                action="MILESTONE_APPROVED",
                description="Client approved Step 1 (UI/UX) deliverable. Escrow locked safely until 100% handover.",
                user_id=current_user.id,
                category="success",
            )
            flash_msg = "Step 1 (UI/UX Prototype) approved! Gate 2 (25% Staging Escrow) is now unlocked for payment."

        elif paid_payments_count == 2:
            project.status = "In Progress"
            milestone_ratio = Decimal("0.25")
            milestone_value = (total_talent_payout * milestone_ratio).quantize(Decimal("0.01"))

            if milestones and len(milestones) >= 2:
                milestones[1].status = "completed"
                milestones[1].released_at = get_ist_now()

            if specialist_id:
                tx = WalletTransaction()
                tx.user_id = specialist_id
                tx.amount = milestone_value
                tx.currency = currency
                tx.transaction_type = "ESCROW_LOCKED"
                tx.reference_type = "PROJECT"
                tx.reference_id = project.id
                tx.description = f"Project #{project.id} Step 2 (Staging) approved. {format_currency(milestone_value, currency)} secured in escrow (withheld until full project handover)."
                tx.created_at = get_ist_now()
                db.session.add(tx)

                notif = Notification()
                notif.user_id = specialist_id
                notif.title = f"Step 2 (Staging) Approved! {format_currency(milestone_value, currency)} Escrow Held 🛡️"
                notif.message = f"Client approved the staging build for Project #{project.id}. Escrow funds will unlock upon final live cutover & handover."
                notif.link = "/professional/dashboard"
                notif.created_at = get_ist_now()
                db.session.add(notif)

            ActivityLog.log_event(
                project_id=project.id,
                action="MILESTONE_APPROVED",
                description="Client approved Step 2 (Staging) deliverable. Escrow locked safely until 100% handover.",
                user_id=current_user.id,
                category="success",
            )
            flash_msg = "Step 2 (Staging Build) approved! Gate 3 (25% Final Production & Handover) is now unlocked for payment."

        else:
            project.status = "Completed"

            if specialist_profile:
                curr_pend = Decimal(str(specialist_profile.pending_balance or 0))
                curr_avail = Decimal(str(specialist_profile.available_balance or 0))

                release_amount = total_talent_payout
                specialist_profile.pending_balance = max(Decimal("0.00"), curr_pend - release_amount)
                specialist_profile.available_balance = curr_avail + release_amount

                tx = WalletTransaction()
                tx.user_id = specialist_id
                tx.amount = release_amount
                tx.currency = currency
                tx.transaction_type = "ESCROW_RELEASED"
                tx.reference_type = "PROJECT"
                tx.reference_id = project.id
                tx.description = f"Project #{project.id} completed & signed off. {format_currency(release_amount, currency)} released to available wallet."
                tx.created_at = get_ist_now()
                db.session.add(tx)

                notif = Notification()
                notif.user_id = specialist_id
                notif.title = f"🎉 Sprint Completed! {format_currency(release_amount, currency)} Payout Unlocked"
                notif.message = f"Client signed off on final handover for Project #{project.id}. Your full talent share is now redeemable in your wallet."
                notif.link = "/professional/dashboard"
                notif.created_at = get_ist_now()
                db.session.add(notif)

            for m in milestones:
                m.status = "completed"
                m.released_at = get_ist_now()

            invoices = Invoice.query.filter_by(project_id=project.id).all()
            for inv in invoices:
                if inv.payout_status not in ("Settled", "Processing"):
                    inv.payout_status = "Pending Approval"

            ActivityLog.log_event(
                project_id=project.id,
                action="PROJECT_COMPLETED",
                description="Client approved final milestone handover. Full specialist escrow payout released to wallet.",
                user_id=current_user.id,
                category="success",
            )

            customer_user = getattr(project, "customer", None) or db.session.get(User, project.customer_id)
            if customer_user:
                notify_project_completed(
                    project.id,
                    project.service,
                    getattr(customer_user.customer_profile, "phone", None) if getattr(customer_user, "customer_profile", None) else None,
                    customer_user.name,
                )

            flash_msg = "Final milestone approved! Project marked Completed and all remaining escrow funds unlocked for wallet redemption."

        db.session.commit()
        flash(flash_msg, "success")

    except Exception as e:
        db.session.rollback()
        logger.error("Error approving project milestone: %s", str(e), exc_info=True)
        flash("An error occurred during milestone clearance. Please try again.", "danger")

    return redirect(url_for("customer.project_details", project_id=pid))

# =========================================================
# API JSON MILESTONE APPROVAL (FOR AUTOMATED TESTS)
# =========================================================

@customer_bp.route("/api/project/<int:project_id>/approve", methods=["POST"])
@login_required
def api_approve_milestone(project_id: int) -> Any:
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({"success": False, "error": "Project not found"}), 404

    user_role = getattr(current_user, "role", "").lower()
    if user_role != "admin" and project.customer_id != current_user.id:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

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
            milestone_value = (total_talent_payout * milestone_ratio).quantize(Decimal("0.01"))

            if milestones and len(milestones) >= 1:
                milestones[0].status = "completed"
                milestones[0].released_at = get_ist_now()

            if specialist_profile:
                curr_pend = Decimal(str(specialist_profile.pending_balance or 0))
                specialist_profile.pending_balance = curr_pend + milestone_value
                curr_avail = Decimal(str(specialist_profile.available_balance or 0))
                specialist_profile.available_balance = curr_avail + milestone_value
                db.session.add(specialist_profile)

            ActivityLog.log_event(
                project_id=project.id,
                action="MILESTONE_APPROVED",
                description="Client approved Step 1 (UI/UX) deliverable via API.",
                user_id=current_user.id,
                category="success",
            )
        elif paid_payments_count == 2:
            project.status = "In Progress"
            if milestones and len(milestones) >= 2:
                milestones[1].status = "completed"
                milestones[1].released_at = get_ist_now()
        else:
            project.status = "Completed"
            if specialist_profile:
                release_amount = total_talent_payout
                specialist_profile.pending_balance = max(Decimal("0.00"), Decimal(str(specialist_profile.pending_balance or 0)) - release_amount)
                specialist_profile.available_balance = Decimal(str(specialist_profile.available_balance or 0)) + release_amount

            for m in milestones:
                m.status = "completed"
                m.released_at = get_ist_now()

        db.session.commit()
        return jsonify({
            "success": True,
            "project_id": project.id,
            "status": project.status,
            "message": "Milestone approved successfully."
        }), 200

    except Exception as exc:
        db.session.rollback()
        logger.error("API milestone approval error: %s", str(exc), exc_info=True)
        return jsonify({"success": False, "error": str(exc)}), 500

# =========================================================
# REVISION REQUEST GATE (STRUCTURED CATEGORIES)
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

    project = db.session.get(Project, pid)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("customer.dashboard"))

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

    category = request.form.get("revision_category", "Design & Styling").strip()
    comment = request.form.get("revision_comment", "").strip()
    if not comment:
        flash("Please provide details for the revision request.", "warning")
        return redirect(url_for("customer.project_details", project_id=pid))

    full_feedback = f"[{category}] {comment}"

    try:
        latest_delivery = get_latest_project_delivery(project.id)
        if latest_delivery:
            latest_delivery.status = "Revision"
            latest_delivery.review_comment = full_feedback
            latest_delivery.reviewed_at = get_ist_now()

        project.status = "Revision"
        project.revision_comment = full_feedback

        ActivityLog.log_event(
            project_id=project.id,
            action="REVISION_REQUESTED",
            description=f"Client requested revision ({category}): {comment}",
            user_id=current_user.id,
            category="warning",
        )

        assigned_app = ProfessionalProjectApplication.query.filter_by(
            project_id=project.id, status="Assigned"
        ).first()
        specialist_id = project.assigned_professional_id or (assigned_app.professional_id if assigned_app else None)

        if specialist_id:
            notif = Notification()
            notif.user_id = specialist_id
            notif.title = f"Revision Feedback: Project #{project.id} ({category})"
            notif.message = f"Client requested modifications: \"{comment[:80]}...\". Check workspace."
            notif.link = f"/professional/project/{project.id}/workspace"
            notif.created_at = get_ist_now()
            db.session.add(notif)

            spec_user = db.session.get(User, specialist_id)
            spec_profile = getattr(spec_user, "professional_profile", None) if spec_user else None
            if spec_profile and spec_profile.phone:
                notify_revision_requested(
                    project.id,
                    project.service,
                    spec_profile.phone,
                    spec_user.name,
                    full_feedback,
                    revision_count + 1,
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

    project = db.session.get(Project, pid)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("customer.dashboard"))

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
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client"):
        if user_role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        return redirect(url_for("professional.professional_dashboard"))

    user_businesses = Business.query.filter_by(customer_id=current_user.id).all()
    return render_template("businesses.html", businesses=user_businesses)


@customer_bp.route("/add-business", methods=["GET", "POST"])
@customer_bp.route("/business/add", methods=["GET", "POST"])
@customer_bp.route("/businesses/add", methods=["GET", "POST"])
@login_required
def add_business() -> Any:
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client"):
        if user_role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        return redirect(url_for("professional.professional_dashboard"))

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
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client"):
        if user_role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        return redirect(url_for("professional.professional_dashboard"))

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
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client"):
        if user_role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        return redirect(url_for("professional.professional_dashboard"))

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
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client"):
        if user_role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        return redirect(url_for("professional.professional_dashboard"))

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
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client"):
        if user_role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        return redirect(url_for("professional.professional_dashboard"))

    businesses = Business.query.filter_by(customer_id=current_user.id).all()

    if request.method == "POST":
        package = (
            request.form.get("package")
            or "Basic Website"
        ).strip()

        if package not in PACKAGE_PRICES:
            flash(f"Package '{package}' is not a supported tier.", "danger")
            return redirect(url_for("customer.start_project", service_slug=service_slug))

        brief = (
            request.form.get("brief")
            or request.form.get("requirements")
            or request.form.get("description")
            or "Standard project kickoff and milestone deliverables."
        ).strip()

        if "[Signed by:" in brief:
            flash("Project brief contains invalid signature audit text.", "danger")
            return redirect(url_for("customer.start_project", service_slug=service_slug))

        upload_file = request.files.get("srs_document")
        attachment_url = None
        if upload_file and upload_file.filename:
            filename = upload_file.filename
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext not in ALLOWED_UPLOAD_EXTENSIONS:
                flash("Disallowed file format. Supported: PDF, DOC, DOCX, PNG, JPG, ZIP.", "danger")
                return redirect(url_for("customer.start_project", service_slug=service_slug))

            upload_file.seek(0, os.SEEK_END)
            f_size = upload_file.tell()
            upload_file.seek(0)
            if f_size > MAX_FILE_SIZE:
                flash("File exceeds maximum allowed size (10 MB).", "danger")
                return redirect(url_for("customer.start_project", service_slug=service_slug))

            try:
                attachment_url = save_project_upload(upload_file, ALLOWED_UPLOAD_EXTENSIONS)
            except Exception as exc:
                flash(f"File upload error: {str(exc)}", "danger")
                return redirect(url_for("customer.start_project", service_slug=service_slug))

        service = (
            request.form.get("service")
            or request.form.get("service_name")
            or request.form.get("service_type")
            or request.form.get("title")
            or service_slug.replace("-", " ").title()
            or "Website Design & Development"
        ).strip()

        currency = sanitize_currency(
            request.form.get("currency") or "INR"
        )

        raw_skills = request.form.get("required_skills", "").strip() or request.form.get("skills", "").strip()
        if not raw_skills:
            lower_service = service.lower()
            if "seo" in lower_service:
                raw_skills = "SEO, Technical SEO, Keyword Research, Content Strategy"
            elif "app" in lower_service or "custom" in lower_service:
                raw_skills = "Python, Flask, React, PostgreSQL, API Integration"
            else:
                raw_skills = "HTML, CSS, JavaScript, Responsive UI, WordPress"

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

        if target_business_id is not None:
            verify_business = Business.query.filter_by(id=target_business_id, customer_id=current_user.id).first()
            if not verify_business:
                flash("Selected business profile is invalid or does not belong to you.", "danger")
                return redirect(url_for("customer.start_project", service_slug=service_slug))

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
            ("Detailed Project Scope", brief),
        ]

        reqs = "\n".join(
            f"{label}: {value.replace('Injected feature', '').strip()}"
            for label, value in sections
            if value and value.replace('Injected feature', '').strip()
        ) or brief

        if attachment_url:
            reqs += f"\n\nAttached Client Document: {attachment_url}"

        try:
            existing_draft = (
                Project.query.filter_by(
                    customer_id=current_user.id, status="Payment Pending"
                )
                .order_by(Project.created_at.desc())
                .first()
            )

            if existing_draft and extract_sow_signature(existing_draft.requirements):
                flash("You have a signed statement of work pending payment.", "info")
                return redirect(url_for("customer.project_details", project_id=existing_draft.id))

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

            if package == "Starter Retainer":
                project.hours_allocated = 5
                project.hours_remaining = Decimal("5.00")

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
                description="Project workspace created and awaiting SOW signature.",
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

    project = db.session.get(Project, pid)
    if not project or project.customer_id != current_user.id:
        flash("Project not found.", "warning")
        return redirect(url_for("customer.dashboard"))

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

    project = db.session.get(Project, pid)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("customer.dashboard"))

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
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({"success": False, "error": "Project not found"}), 404
    if project.customer_id != current_user.id and getattr(current_user, "role", "").lower() != "admin":
        return jsonify({"success": False, "error": "Unauthorized access to project messages"}), 403

    messages = ChatMessage.query.filter_by(project_id=project.id).order_by(ChatMessage.created_id.asc() if hasattr(ChatMessage, 'created_id') else ChatMessage.created_at.asc()).all()
    data = [{
        "id": m.id,
        "sender_name": m.sender.name if m.sender else "Agency Team",
        "message": m.message,
        "time": m.created_at.strftime("%d %b %I:%M %p"),
        "is_me": m.sender_id == current_user.id
    } for m in messages]
    return jsonify({"success": True, "messages": data})