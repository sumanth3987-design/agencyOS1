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
from app.utils.project_uploads import save_project_upload

customer_bp = Blueprint("customer", __name__)


# =========================================================
# CLIENT DASHBOARD (MAIN WORKSPACE)
# =========================================================

@customer_bp.route("/dashboard")
@login_required
def dashboard():
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("customer", "client"):
        if user_role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        return redirect(url_for("professional.professional_dashboard"))

    projects = Project.query.filter_by(customer_id=current_user.id).order_by(Project.created_at.desc()).all()
    profile = CustomerProfile.query.filter_by(user_id=current_user.id).first()
    businesses = Business.query.filter_by(customer_id=current_user.id).all()
    
    project_ids = [p.id for p in projects]
    try:
        recent_messages = ChatMessage.query.filter(ChatMessage.project_id.in_(project_ids)).order_by(ChatMessage.created_at.desc()).limit(6).all() if project_ids else []
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
        profile_completion=profile_completion
    )


# =========================================================
# DASHBOARD QUICK MESSAGING
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
# PROJECT DETAILS & WORKSPACE
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

    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("admin",) and project.customer_id != current_user.id:
        flash("Unauthorized access to project.", "danger")
        return redirect(url_for("customer.dashboard"))

    try:
        latest_delivery = get_latest_project_delivery(project.id)
    except Exception:
        latest_delivery = ProjectDelivery.query.filter_by(project_id=project.id).order_by(ProjectDelivery.submission_number.desc()).first()

    deliveries = ProjectDelivery.query.filter_by(project_id=project.id).order_by(ProjectDelivery.submission_number.asc()).all()
    
    try:
        messages = ChatMessage.query.filter_by(project_id=project.id).order_by(ChatMessage.created_at.asc()).all()
    except Exception:
        messages = []

    revision_count = ProjectDelivery.query.filter_by(project_id=project.id, status="Revision").count()
    revisions_remaining = max(0, 2 - revision_count)
    payment = Payment.query.filter_by(project_id=project.id).order_by(Payment.created_at.desc()).first()

    return render_template(
        "customer_project_details.html",
        project=project,
        payment=payment,
        latest_delivery=latest_delivery,
        deliveries=deliveries,
        messages=messages,
        revision_count=revision_count,
        revisions_remaining=revisions_remaining
    )


# =========================================================
# IN-PROJECT CHAT NOTE
# =========================================================

@customer_bp.route("/project/<project_id>/message", methods=["POST"])
@customer_bp.route("/project/<int:project_id>/message", methods=["POST"])
@login_required
def send_project_message(project_id):
    clean_id = str(project_id).strip().rstrip("-/")
    pid = int(clean_id)

    project = Project.query.get_or_404(pid)
    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("admin",) and project.customer_id != current_user.id:
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
# PRINTABLE GST TAX INVOICE (SAC 998311)
# =========================================================

@customer_bp.route("/project/<project_id>/invoice")
@customer_bp.route("/project/<int:project_id>/invoice")
@login_required
def view_invoice(project_id):
    clean_id = str(project_id).strip().rstrip("-/")
    pid = int(clean_id)
    project = Project.query.get_or_404(pid)

    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("admin",) and project.customer_id != current_user.id:
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
        invoice_date=project.created_at.strftime("%d %b %Y") if getattr(project, "created_at", None) else "01 Nov 2026"
    )


# =========================================================
# CLIENT INVOICES / BILLING LEDGER
# =========================================================

@customer_bp.route("/invoices")
@login_required
def invoices_list():
    user_role = getattr(current_user, "role", "").lower()
    if user_role == "admin":
        invoices = Invoice.query.order_by(Invoice.id.desc()).all()
    else:
        invoices = Invoice.query.filter_by(customer_id=current_user.id).order_by(Invoice.id.desc()).all()

    return render_template("client_invoices.html", invoices=invoices)


# =========================================================
# DIGITAL SOW CONTRACT & E-SIGNATURE
# =========================================================

@customer_bp.route("/project/<int:project_id>/contract", methods=["GET", "POST"])
@login_required
def project_contract(project_id):
    project = Project.query.get_or_404(project_id)
    user_role = getattr(current_user, "role", "").lower()

    if user_role not in ("admin",) and project.customer_id != current_user.id:
        flash("Unauthorized access to contract.", "danger")
        return redirect(url_for("customer.dashboard"))

    customer = project.customer
    business = project.business

    if request.method == "POST":
        signer_name = request.form.get("signer_name", "").strip()
        signer_title = request.form.get("signer_title", "").strip()

        # Record signature audit log
        signature_entry = f" [Signed by: {signer_name} ({signer_title}) from IP: {request.remote_addr} on {get_ist_now().strftime('%d %b %Y %H:%M IST')}]"
        if not getattr(project, "requirements", ""):
            project.requirements = signature_entry
        else:
            project.requirements = project.requirements + signature_entry

        db.session.commit()
        flash("SOW Agreement digitally signed! Please authorize the advance deposit.", "success")
        return redirect(url_for("payments.payment_page", project_id=project.id))

    return render_template(
        "contract.html",
        project=project,
        customer=customer,
        business=business
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

    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("admin",) and project.customer_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("customer.dashboard"))

    latest_delivery = get_latest_project_delivery(project.id)
    if latest_delivery:
        latest_delivery.status = "Approved"
        latest_delivery.reviewed_at = get_ist_now()

    project.status = "Completed"
    project.revision_comment = None

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

    user_role = getattr(current_user, "role", "").lower()
    if user_role not in ("admin",) and project.customer_id != current_user.id:
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
# CLIENT REVIEW & TESTIMONIAL SUBMISSION
# =========================================================

@customer_bp.route("/project/<int:project_id>/review", methods=["POST"])
@login_required
def submit_project_review(project_id):
    project = Project.query.get_or_404(project_id)
    if project.customer_id != current_user.id and getattr(current_user, "role", "").lower() != "admin":
        flash("Unauthorized action.", "danger")
        return redirect(url_for("customer.dashboard"))

    rating = request.form.get("rating", 5, type=int)
    tags = ", ".join(request.form.getlist("tags"))
    feedback = request.form.get("feedback", "").strip()

    if not feedback:
        flash("Please provide a short review or feedback.", "warning")
        return redirect(url_for("customer.project_details", project_id=project.id))

    try:
        from app.models import Review
        existing_review = Review.query.filter_by(project_id=project.id).first()
        if existing_review:
            existing_review.rating = rating
            existing_review.tags = tags
            existing_review.feedback = feedback
        else:
            db.session.add(Review(
                project_id=project.id,
                customer_id=current_user.id,
                rating=rating,
                tags=tags,
                feedback=feedback,
                is_public=True
            ))
        db.session.commit()
        flash("Thank you for your review! Your feedback helps us maintain top delivery standards.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not submit review: {str(e)}", "danger")

    return redirect(url_for("customer.project_details", project_id=project.id))


# =========================================================
# BUSINESSES MANAGEMENT (ADD, EDIT, DELETE)
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


@customer_bp.route("/business/<int:business_id>/edit", methods=["GET", "POST"])
@login_required
def edit_business(business_id):
    business = Business.query.filter_by(id=business_id, customer_id=current_user.id).first_or_404()

    if request.method == "POST":
        business.name = request.form.get("name", business.name).strip()
        business.business_type = request.form.get("business_type", business.business_type).strip()
        business.location = request.form.get("location", business.location).strip()
        business.website = request.form.get("website", business.website).strip()
        db.session.commit()
        flash("Business profile updated successfully!", "success")
        return redirect(url_for("customer.businesses"))

    return render_template("edit_business.html", business=business)


@customer_bp.route("/business/<int:business_id>/delete", methods=["POST"])
@login_required
def delete_business(business_id):
    business = Business.query.filter_by(id=business_id, customer_id=current_user.id).first_or_404()
    db.session.delete(business)
    db.session.commit()
    flash(f"Business '{business.name}' removed.", "success")
    return redirect(url_for("customer.businesses"))


# =========================================================
# CUSTOMER PROFILE & START PROJECT
# =========================================================

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

# In app/blueprints/customer/__init__.py

# =========================================================
# START PROJECT (WITH DUPLICATE DRAFT PREVENTION)
# =========================================================

@customer_bp.route("/start-project/<service_slug>", methods=["GET", "POST"])
@customer_bp.route("/start-project", methods=["GET", "POST"])
@login_required
def start_project(service_slug="website-development"):
    businesses = Business.query.filter_by(customer_id=current_user.id).all()
    if request.method == "POST":
        service = request.form.get("service", "Website Design & Development")
        package = request.form.get("package", "Milestone Growth")
        reqs = request.form.get("brief", "").strip() or request.form.get("requirements", "").strip()
        b_id = request.form.get("business_id", type=int)
        target_business_id = b_id if b_id else (businesses[0].id if businesses else None)

        try:
            attachment_url = save_project_upload(
                request.files.get("srs_document"),
                {"pdf", "doc", "docx", "png", "jpg", "jpeg", "zip"},
            )
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("customer.start_project", service_slug=service_slug))

        if attachment_url:
            reqs += f"\n\nAttached Client Document: {attachment_url}"

        # 1. SMART RE-USE: Check if an unpaid draft project already exists for this client
        existing_draft = Project.query.filter_by(
            customer_id=current_user.id,
            status="Payment Pending"
        ).order_by(Project.created_at.desc()).first()

        if existing_draft:
            # Re-use existing draft instead of generating duplicate cards
            existing_draft.service = service
            existing_draft.package = package
            existing_draft.requirements = reqs
            existing_draft.business_id = target_business_id
            db.session.commit()
            flash(f"Updated Project #{existing_draft.id}. Please review SOW and proceed to checkout.", "info")
            return redirect(url_for("customer.project_contract", project_id=existing_draft.id))

        # 2. Otherwise create a new project
        project = Project(
            customer_id=current_user.id,
            business_id=target_business_id,
            service=service,
            package=package,
            requirements=reqs,
            status="Payment Pending"
        )
        db.session.add(project)
        db.session.commit()

        flash("Milestone project created! Please review and digitally sign the SOW Agreement.", "info")
        return redirect(url_for("customer.project_contract", project_id=project.id))

    return render_template("start_project.html", service_slug=service_slug, businesses=businesses)


# =========================================================
# CANCEL UNPAID DRAFT PROJECT
# =========================================================

@customer_bp.route("/project/<int:project_id>/cancel", methods=["POST"])
@login_required
def cancel_project(project_id):
    project = Project.query.filter_by(id=project_id, customer_id=current_user.id).first_or_404()
    
    # Only allow cancelling unpaid drafts
    if project.status != "Payment Pending":
        flash("Cannot cancel an active or paid project.", "warning")
        return redirect(url_for("customer.dashboard"))

    Payment.query.filter_by(project_id=project.id).delete()
    db.session.delete(project)
    db.session.commit()

    flash(f"Draft Project #{project_id} has been cancelled.", "info")
    return redirect(url_for("customer.dashboard"))