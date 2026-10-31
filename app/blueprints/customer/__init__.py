# Save this directly into your file: app/blueprints/customer/__init__.py

import os
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app.extensions import db

# 1. IMPORT MODELS & REPOSITORY HELPERS
from app.models import (
    User, CustomerProfile, Business, Project, Payment,
    ProfessionalProjectApplication, ProjectDelivery, ChatMessage, Invoice, Milestone,
    get_ist_now, PACKAGE_PRICES, calculate_customer_profile_completion, get_latest_project_delivery
)

customer_bp = Blueprint("customer", __name__)


# =========================================================
# CLIENT DASHBOARD (MAIN WORKSPACE)
# =========================================================
@customer_bp.route("/dashboard")
@login_required
def dashboard():
    if current_user.role != "customer":
        if current_user.role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        return redirect(url_for("professional.professional_dashboard"))

    projects = Project.query.filter_by(customer_id=current_user.id).order_by(Project.created_at.desc()).all()
    profile = CustomerProfile.query.filter_by(user_id=current_user.id).first()
    businesses = Business.query.filter_by(customer_id=current_user.id).all()
    
    # Load recent discussion notes across customer's projects
    project_ids = [p.id for p in projects]
    try:
        recent_messages = ChatMessage.query.filter(ChatMessage.project_id.in_(project_ids)).order_by(ChatMessage.created_at.desc()).limit(6).all() if project_ids else []
    except Exception:
        recent_messages = []

    profile_completion = 0
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
        profile_completion=profile_completion
    )

# =========================================================
# DASHBOARD QUICK MESSAGING (DIRECT TO AGENCY LEAD)
# =========================================================

@customer_bp.route("/dashboard/quick-message", methods=["POST"])
@login_required
def dashboard_quick_message():
    text = request.form.get("message", "").strip()
    project_id_str = request.form.get("project_id", "").strip().rstrip("-/")

    if not text or not project_id_str:
        flash("Please enter a message and select a project.", "warning")
        return redirect(url_for("customer.dashboard"))

    try:
        pid = int(project_id_str)
        project = Project.query.get(pid)
        if not project:
            flash(f"Project #{pid} not found.", "warning")
            return redirect(url_for("customer.dashboard"))

        msg = ChatMessage(
            project_id=pid,
            sender_id=current_user.id,
            message=text,
            created_at=get_ist_now()
        )
        db.session.add(msg)
        db.session.commit()
        flash(f"Note posted to Project #{pid} team successfully!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not post message: {str(e)}", "danger")

    return redirect(url_for("customer.dashboard"))

# =========================================================
# PROJECT DETAILS (HANDLES /project/5 AND /project/5-)
# =========================================================

@customer_bp.route("/project/<project_id>")
@customer_bp.route("/project/<int:project_id>")
@login_required
def project_details(project_id):
    clean_id = str(project_id).strip().rstrip("-/")
    try:
        pid = int(clean_id)
    except (ValueError, TypeError):
        flash(f"Invalid project reference: {project_id}", "warning")
        return redirect(url_for("customer.dashboard"))

    project = Project.query.get(pid)
    if not project:
        flash(f"Project #{pid} not found in database.", "warning")
        return redirect(url_for("customer.dashboard"))

    # Security check: Ensure customers only view their own projects
    if current_user.role == "customer" and project.customer_id != current_user.id:
        flash("Unauthorized access to project.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        latest_delivery = get_latest_project_delivery(project.id)
    except Exception:
        latest_delivery = ProjectDelivery.query.filter_by(project_id=project.id).order_by(ProjectDelivery.submission_number.desc()).first()

    deliveries = ProjectDelivery.query.filter_by(project_id=project.id).order_by(ProjectDelivery.submission_number.asc()).all()
    
    # Safe retrieval of chat messages
    try:
        messages = ChatMessage.query.filter_by(project_id=project.id).order_by(ChatMessage.created_at.asc()).all()
    except Exception:
        messages = []

    revision_count = ProjectDelivery.query.filter_by(project_id=project.id, status="Revision").count()
    revisions_remaining = max(0, 2 - revision_count)

    return render_template(
        "customer_project_details.html",
        project=project,
        latest_delivery=latest_delivery,
        deliveries=deliveries,
        messages=messages,
        revision_count=revision_count,
        revisions_remaining=revisions_remaining
    )


# =========================================================
# IN-PROJECT DISCUSSION NOTE / MESSAGE POST
# =========================================================

@customer_bp.route("/project/<project_id>/message", methods=["POST"])
@customer_bp.route("/project/<int:project_id>/message", methods=["POST"])
@login_required
def send_project_message(project_id):
    clean_id = str(project_id).strip().rstrip("-/")
    pid = int(clean_id)

    project = Project.query.get_or_404(pid)
    if current_user.role == "customer" and project.customer_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("customer.dashboard"))

    text = request.form.get("message", "").strip()
    if not text:
        flash("Message cannot be empty.", "warning")
        return redirect(url_for("customer.project_details", project_id=pid))

    try:
        msg = ChatMessage(
            project_id=pid,
            sender_id=current_user.id,
            message=text,
            created_at=get_ist_now()
        )
        db.session.add(msg)
        db.session.commit()
        flash("Note posted to project discussion!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not post message: {str(e)}", "danger")

    return redirect(url_for("customer.project_details", project_id=pid))


# =========================================================
# PAYMENT CONFIRMATION / ADVANCE UNLOCK
# =========================================================

@customer_bp.route("/payment/<project_id>/confirm", methods=["GET", "POST"])
@customer_bp.route("/payment/<int:project_id>/confirm", methods=["GET", "POST"])
@login_required
def confirm_payment(project_id):
    clean_id = str(project_id).strip().rstrip("-/")
    pid = int(clean_id)
    project = Project.query.get_or_404(pid)

    if current_user.role == "customer" and project.customer_id != current_user.id:
        flash("Unauthorized access.", "danger")
        return redirect(url_for("customer.dashboard"))

    # 1. Update Project Status to Active/In Progress
    project.status = "In Progress"

    # 2. Update Payment Record to Paid
    payment = Payment.query.filter_by(project_id=project.id).first()
    pkg_amount = PACKAGE_PRICES.get(project.package, 48000)

    if not payment:
        payment = Payment(
            project_id=project.id,
            customer_id=project.customer_id,
            razorpay_order_id=f"order_{project.id}_{int(datetime.now().timestamp())}",
            razorpay_payment_id=f"pay_{project.id}_{int(datetime.now().timestamp())}",
            amount=pkg_amount,
            status="Paid",
            paid_at=get_ist_now()
        )
        db.session.add(payment)
    else:
        payment.status = "Paid"
        payment.paid_at = get_ist_now()
        if not payment.razorpay_payment_id:
            payment.razorpay_payment_id = f"pay_{project.id}_{int(datetime.now().timestamp())}"

    # 3. Create or Update GST Tax Invoice
    invoice = Invoice.query.filter_by(project_id=project.id).first()
    if not invoice:
        invoice = Invoice(
            invoice_number=f"INV-2026-{project.id:04d}",
            project_id=project.id,
            customer_id=project.customer_id,
            gross_amount=pkg_amount,
            talent_payout=int(pkg_amount * 0.45),
            agency_profit=int(pkg_amount * 0.55),
            tax_amount=int(pkg_amount - (pkg_amount / 1.18)),
            status="Paid",
            payout_status="Pending Approval"
        )
        db.session.add(invoice)
    else:
        invoice.status = "Paid"

    # 4. Fund linked milestones
    try:
        Milestone.query.filter_by(project_id=project.id, status="pending").update({"status": "funded"}, synchronize_session=False)
    except Exception:
        pass

    db.session.commit()
    flash("Advance payment authorized! Milestone 1 unlocked and active.", "success")
    return redirect(url_for("customer.project_details", project_id=pid))


# =========================================================
# PRINTABLE GST TAX INVOICE (SAC 998311)
# =========================================================

@customer_bp.route("/project/<project_id>/invoice")
@customer_bp.route("/project/<int:project_id>/invoice")
@login_required
def view_invoice(project_id):
    clean_id = str(project_id).strip().rstrip("-/")
    pid = int(clean_id)
    project = Project.query.get_or_404(pid)

    if current_user.role == "customer" and project.customer_id != current_user.id:
        flash("Unauthorized access to invoice.", "danger")
        return redirect(url_for("customer.dashboard"))

    customer = project.customer
    profile = customer.customer_profile if customer else None

    gross_amount = PACKAGE_PRICES.get(project.package, 48000)
    base_amount = round(gross_amount / 1.18, 2)
    tax_total = round(gross_amount - base_amount, 2)
    cgst_amount = round(tax_total / 2, 2)
    sgst_amount = round(tax_total / 2, 2)

    return render_template(
        "invoice.html",
        project=project,
        customer=customer,
        profile=profile,
        gross_amount=gross_amount,
        base_amount=base_amount,
        tax_total=tax_total,
        cgst_amount=cgst_amount,
        sgst_amount=sgst_amount,
        invoice_number=f"INV-2026-{project.id:04d}",
        invoice_date=project.created_at.strftime("%d %b %Y") if getattr(project, "created_at", None) else "31 Oct 2026"
    )


# =========================================================
# DELIVERABLE APPROVAL & REVISION
# =========================================================

@customer_bp.route("/project/<project_id>/approve", methods=["POST"])
@customer_bp.route("/project/<int:project_id>/approve", methods=["POST"])
@login_required
def customer_approve_project(project_id):
    clean_id = str(project_id).strip().rstrip("-/")
    pid = int(clean_id)
    project = Project.query.get_or_404(pid)

    if current_user.role == "customer" and project.customer_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("customer.dashboard"))

    latest_delivery = get_latest_project_delivery(project.id)
    if latest_delivery:
        latest_delivery.status = "Approved"
        latest_delivery.reviewed_at = get_ist_now()

    project.status = "Completed"
    project.revision_comment = None

    # Release milestones
    try:
        Milestone.query.filter_by(project_id=project.id).update({
            "status": "released",
            "released_at": get_ist_now()
        }, synchronize_session=False)
    except Exception:
        pass

    db.session.commit()

    flash("Milestone deliverable approved! Project marked as Completed.", "success")
    return redirect(url_for("customer.project_details", project_id=pid))


@customer_bp.route("/project/<project_id>/revision", methods=["POST"])
@customer_bp.route("/project/<int:project_id>/revision", methods=["POST"])
@login_required
def customer_request_revision(project_id):
    clean_id = str(project_id).strip().rstrip("-/")
    pid = int(clean_id)
    project = Project.query.get_or_404(pid)

    if current_user.role == "customer" and project.customer_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("customer.dashboard"))

    revision_count = ProjectDelivery.query.filter_by(project_id=project.id, status="Revision").count()
    if revision_count >= 2:
        flash("Maximum 2 complimentary revisions reached. Please order a Paid Scope Add-on.", "warning")
        return redirect(url_for("customer.project_details", project_id=pid))

    comment = request.form.get("revision_comment", "").strip()
    latest_delivery = get_latest_project_delivery(project.id)
    if latest_delivery:
        latest_delivery.status = "Revision"
        latest_delivery.review_comment = comment
        latest_delivery.reviewed_at = get_ist_now()

    project.status = "Revision"
    project.revision_comment = comment
    db.session.commit()

    flash("Revision feedback submitted. The specialist will update the work.", "success")
    return redirect(url_for("customer.project_details", project_id=pid))


# =========================================================
# PROFILES, BUSINESSES & START PROJECT
# =========================================================

@customer_bp.route("/businesses")
@login_required
def businesses():
    user_businesses = Business.query.filter_by(customer_id=current_user.id).all()
    return render_template("businesses.html", businesses=user_businesses)


@customer_bp.route("/add-business", methods=["GET", "POST"])
@login_required
def add_business():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        b_type = request.form.get("business_type", "").strip()
        location = request.form.get("location", "").strip()
        website = request.form.get("website", "").strip()

        if name:
            b = Business(customer_id=current_user.id, name=name, business_type=b_type, location=location, website=website)
            db.session.add(b)
            db.session.commit()
            flash("Business profile added successfully!", "success")
            return redirect(url_for("customer.businesses"))

    return render_template("add_business.html")


@customer_bp.route("/customer/profile", methods=["GET", "POST"])
@customer_bp.route("/profile", methods=["GET", "POST"])
@customer_bp.route("/customer_profile", methods=["GET", "POST"])
@login_required
def customer_profile():
    profile = CustomerProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        profile = CustomerProfile(user_id=current_user.id)
        db.session.add(profile)
        db.session.commit()

    if request.method == "POST":
        profile.phone = request.form.get("phone", "").strip()
        profile.location = request.form.get("location", "").strip()
        profile.business_name = request.form.get("business_name", "").strip()
        profile.website = request.form.get("website", "").strip()
        db.session.commit()
        flash("Profile updated successfully!", "success")
        return redirect(url_for("customer.customer_profile"))

    return render_template("customer_profile.html", profile=profile)


@customer_bp.route("/start-project/<service_slug>", methods=["GET", "POST"])
@customer_bp.route("/start-project", methods=["GET", "POST"])
@login_required
def start_project(service_slug="website-development"):
    businesses = Business.query.filter_by(customer_id=current_user.id).all()
    if request.method == "POST":
        service = request.form.get("service", "Website Design & Development")
        package = request.form.get("package", "Milestone Growth")
        reqs = request.form.get("brief", "").strip() or request.form.get("requirements", "").strip()
        b = Business.query.filter_by(customer_id=current_user.id).first()

        project = Project(
            customer_id=current_user.id,
            business_id=b.id if b else None,
            service=service,
            package=package,
            requirements=reqs,
            status="Payment Pending"
        )
        db.session.add(project)
        db.session.commit()

        flash("Milestone project created! Please authorize advance deposit.", "success")
        return redirect(url_for("payments.payment_page", project_id=project.id))

    return render_template("start_project.html", service_slug=service_slug, businesses=businesses)