"""AgencyOS Collaboration, Notification & Chat Stream Blueprint.

Manages real-time project messaging, file uploads, in-app bell notifications,
and AI-assisted SOW generator endpoints.
"""

import os
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from flask import Blueprint, flash, jsonify, redirect, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import ChatMessage, Invoice, Notification, PACKAGE_PRICES, Project, User, get_ist_now
from app.utils.currency import compute_financial_split, get_package_price, sanitize_currency

collaboration_bp = Blueprint("collaboration", __name__)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "pdf", "zip", "mp4", "txt", "docx", "fig"}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# =========================================================
# 1. REAL-TIME PROJECT CHAT & MESSAGING
# =========================================================

@collaboration_bp.route("/api/project/<int:project_id>/messages", methods=["GET"])
@login_required
def get_project_messages(project_id: int) -> ResponseReturnValue:
    project = Project.query.get_or_404(project_id)
    is_customer = project.customer_id == current_user.id
    is_admin = getattr(current_user, "role", "").lower() == "admin"
    is_professional = getattr(current_user, "role", "").lower() == "professional"

    if not (is_customer or is_admin or is_professional):
        return jsonify({"error": "Unauthorized"}), 403

    messages = ChatMessage.query.filter_by(project_id=project.id).order_by(ChatMessage.created_at.asc()).all()

    return jsonify({
        "success": True,
        "messages": [
            {
                "id": m.id,
                "sender_id": m.sender_id,
                "sender_name": m.sender.name if m.sender else "User",
                "sender_role": m.sender.role if m.sender else "",
                "message": m.message,
                "attachment_url": m.attachment_url,
                "created_at": m.created_at.strftime("%I:%M %p, %d %b") if m.created_at else ""
            }
            for m in messages
        ]
    })


@collaboration_bp.route(
    "/api/project/<int:project_id>/messages",
    methods=["POST"],
    endpoint="api_send_project_message",
)
@login_required
def send_project_message(project_id: int) -> ResponseReturnValue:
    project = Project.query.get_or_404(project_id)
    data = request.get_json(silent=True) or request.form
    text = (data.get("message") or "").strip()
    attachment_url = (data.get("attachment_url") or "").strip() or None

    if not text and not attachment_url:
        return jsonify({"error": "Message content or attachment required"}), 400

    msg = ChatMessage()
    msg.project_id = project.id
    msg.sender_id = current_user.id
    msg.message = text or "Sent an attachment"
    msg.attachment_url = attachment_url
    msg.created_at = get_ist_now()
    
    db.session.add(msg)

    # Send Notification to customer or admin
    recipient_id = project.customer_id if current_user.role != "customer" else None
    if recipient_id and recipient_id != current_user.id:
        notif = Notification()
        notif.user_id = recipient_id
        notif.title = f"New Message on {project.service}"
        notif.message = f"{current_user.name}: {text[:80]}"
        notif.link = f"/project/{project.id}"
        notif.created_at = get_ist_now()
        db.session.add(notif)

    db.session.commit()

    return jsonify({
        "success": True,
        "message": {
            "id": msg.id,
            "sender_name": current_user.name,
            "sender_role": current_user.role,
            "message": msg.message,
            "attachment_url": msg.attachment_url,
            "created_at": msg.created_at.strftime("%I:%M %p, %d %b") if msg.created_at else ""
        }
    })


# =========================================================
# 2. NOTIFICATIONS ENGINE
# =========================================================

@collaboration_bp.route("/api/notifications", methods=["GET"])
@login_required
def get_notifications() -> ResponseReturnValue:
    notifs = (
        Notification.query.filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(10)
        .all()
    )
    unread_count = Notification.query.filter_by(
        user_id=current_user.id, is_read=False
    ).count()

    def safe_link(value: str | None) -> str:
        if not value:
            return url_for("customer.dashboard")
        candidate = value.strip()
        parsed = urlsplit(candidate)
        if (
            parsed.scheme
            or parsed.netloc
            or not parsed.path.startswith("/")
            or parsed.path.startswith("//")
            or "\\" in candidate
            or any(ord(char) < 32 for char in candidate)
        ):
            return url_for("customer.dashboard")
        return candidate

    return jsonify({
        "success": True,
        "count": unread_count,
        "unread_count": unread_count,
        "notifications": [
            {
                "id": n.id,
                "title": n.title,
                "message": n.message,
                "link": safe_link(n.link),
                "is_read": n.is_read,
                "created_at": n.created_at.strftime("%d %b %H:%M") if n.created_at else ""
            }
            for n in notifs
        ]
    })


@collaboration_bp.route("/api/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notification_id: int) -> ResponseReturnValue:
    notif = Notification.query.filter_by(id=notification_id, user_id=current_user.id).first()
    if not notif:
        return jsonify({"success": False, "error": "Notification not found."}), 404
    try:
        notif.is_read = True
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "error": "Could not update notification."}), 500
    return jsonify({"success": True})


@collaboration_bp.route("/api/notifications/clear-all", methods=["POST"])
@login_required
def clear_all_notifications() -> ResponseReturnValue:
    try:
        updated_count = Notification.query.filter_by(
            user_id=current_user.id, is_read=False
        ).update({Notification.is_read: True}, synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "error": "Could not clear notifications."}), 500
    return jsonify({"success": True, "updated_count": updated_count, "count": 0})


# =========================================================
# 3. DIRECT FILE UPLOADS (Assets, ZIPs, Design Files)
# =========================================================

@collaboration_bp.route("/api/upload", methods=["POST"])
@login_required
def upload_file() -> ResponseReturnValue:
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    filename = file.filename
    
    if not file or not filename:
        return jsonify({"error": "No file selected"}), 400

    if allowed_file(filename):
        original_name = secure_filename(filename)
        unique_name = f"{uuid.uuid4().hex[:12]}_{original_name}"

        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        upload_dir = os.path.join(base_dir, "static", "uploads")
        os.makedirs(upload_dir, exist_ok=True)

        dest_path = os.path.join(upload_dir, unique_name)
        file.save(dest_path)

        file_url = f"/static/uploads/{unique_name}"
        return jsonify({
            "success": True,
            "filename": original_name,
            "url": file_url,
            "size_bytes": os.path.getsize(dest_path)
        })

    return jsonify({"error": "File type not permitted. Allowed: images, zip, pdf, mp4, docx"}), 400


# =========================================================
# 4. INVOICING FALLBACK & REDIRECT
# =========================================================

@collaboration_bp.route("/invoice/<int:project_id>")
@login_required
def collaboration_view_invoice(project_id: int) -> ResponseReturnValue:
    """Fallback route to ensure legacy invoice links direct to the canonical customer endpoint."""
    return redirect(url_for("customer.view_invoice", project_id=project_id))


@collaboration_bp.route("/admin/invoices")
@login_required
def admin_invoices() -> ResponseReturnValue:
    """Fallback directory route to unified transactions ledger."""
    if getattr(current_user, "role", "").lower() != "admin":
        return redirect(url_for("customer.dashboard"))
    return redirect(url_for("admin.admin_transactions"))


# =========================================================
# 5. AI PROJECT BRIEF & SCOPE GENERATOR
# =========================================================

@collaboration_bp.route("/api/ai/generate-scope", methods=["POST"])
@login_required
def ai_generate_scope() -> ResponseReturnValue:
    if getattr(current_user, "role", "").lower() != "admin":
        return jsonify({"success": False, "message": "Admin authorization required."}), 403
    data = request.get_json(silent=True) or {}
    business_name = data.get("business_name", "Local Business")
    service = data.get("service", "Website Design & Development")
    industry = data.get("industry", "Retail & Services")
    goal = data.get("goal", "Increase sales and customer inquiries")

    if "seo" in service.lower():
        scope = f"""# AI-Generated SEO Strategy for {business_name}
**Industry:** {industry} | **Core Goal:** {goal}

### 1. Technical Audit Deliverables:
- Core Web Vitals optimization (LCP < 2.5s, CLS < 0.1).
- Schema.org Structured Data (LocalBusiness, FAQPage, Product).
- XML Sitemap & Robots.txt indexing in Google Search Console.

### 2. High-Intent Keyword Clusters:
- Primary: "{business_name} services near me"
- Secondary: "Top rated {industry.lower()}", "pricing & quotes"

### 3. Execution Plan:
- 10 High-Authority contextual backlinks.
- Google Business Profile optimization with geotagged media."""
    else:
        scope = f"""# AI Scope of Work for {business_name}
**Project:** {service} | **Target Goal:** {goal}

### 1. Recommended Architecture (5-7 Pages):
- **Homepage:** High-impact hero section with conversion CTA and trust badges.
- **Services / Catalog:** Structured listing with pricing and feature breakdowns.
- **About Us:** Founder story and business certifications.
- **Customer Reviews:** Case studies with verified testimonials.
- **Contact:** Interactive form with direct WhatsApp click-to-chat.

### 2. Technical Deliverable Checklist for Professional:
- 100% Mobile Responsive design tested on iOS and Android.
- Lighthouse Speed Score > 90.
- Integrated Razorpay checkout flow."""

    return jsonify({
        "success": True,
        "scope": scope
    })