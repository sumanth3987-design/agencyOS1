# Save this directly into your file: app/routes/public.py

import os
from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, current_app, send_from_directory
from app.extensions import db, csrf
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
public_bp = Blueprint("public", __name__)

# =========================================================
# ROOT LANDING / APP SERVING
# =========================================================

@public_bp.route("/")
def index():
    dist_index = os.path.abspath(os.path.join(current_app.root_path, "..", "static", "dist", "index.html"))
    if os.path.exists(dist_index):
        return send_from_directory(os.path.dirname(dist_index), "index.html")
    return render_template("index.html")

# =========================================================
# LEGAL & COMPLIANCE PAGES (REQUIRED FOR RAZORPAY APPROVAL)
# =========================================================

@public_bp.route("/privacy")
def privacy():
    return render_template("legal/privacy.html")

@public_bp.route("/terms")
def terms():
    return render_template("legal/terms.html")

@public_bp.route("/refund-policy")
@public_bp.route("/refund")
def refund_policy():
    return render_template("legal/refund.html")

@public_bp.route("/contact")
def contact():
    return render_template("legal/contact.html")

# =========================================================
# FREE AUDIT / CLIENT LEAD CAPTURE
# =========================================================

@public_bp.route("/api/free-audit", methods=["POST"])
@csrf.exempt
def free_audit():
    if request.is_json:
        data = request.get_json(silent=True) or {}
        business_url = data.get("business_url", "").strip()
        phone = data.get("phone", "").strip()
        email = data.get("email", "").strip()
    else:
        business_url = request.form.get("business_url", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()

    if not business_url:
        if request.is_json:
            return jsonify({"success": False, "message": "Website URL or business name is required."}), 400
        flash("Please enter a valid website URL or business name.", "error")
        return redirect(url_for("public.index") + "#audit")

    current_app.logger.info(f"Audit requested for {business_url} (Phone: {phone}, Email: {email})")

    if request.is_json:
        return jsonify({
            "success": True,
            "message": "Audit requested successfully! Our team will contact you shortly."
        }), 200

    flash("Audit requested successfully! Our team is analyzing your site and will reach out shortly.", "success")
    return redirect(url_for("public.index") + "#audit")