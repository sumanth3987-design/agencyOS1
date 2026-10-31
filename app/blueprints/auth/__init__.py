# Save this directly into your file: app/blueprints/auth/__init__.py

import logging
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urljoin, urlparse

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db, csrf, limiter
from app.services.email import send_email, EmailDeliveryError
from app.models import User, CustomerProfile, ProfessionalProfile, UserToken, get_ist_now

auth_bp = Blueprint("auth", __name__)
logger = logging.getLogger(__name__)

def _hash_token(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

def _validate_password_policy(password):
    """Return whether a password satisfies the shared AgencyOS policy."""
    return (
        isinstance(password, str)
        and 8 <= len(password) <= 128
        and any(char.isalpha() for char in password)
        and any(char.isdigit() for char in password)
    )

def _trusted_external_url(endpoint, **values):
    path = url_for(endpoint, _external=False, **values)
    base_url = (current_app.config.get("APP_BASE_URL") or "").strip()
    if base_url:
        parsed = urlparse(base_url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    return url_for(endpoint, _external=True, **values)

def _create_user_token(user, purpose, expires_at):
    raw_token = secrets.token_urlsafe(32)
    token = UserToken(
        user_id=user.id,
        purpose=purpose,
        token_hash=_hash_token(raw_token),
        expires_at=expires_at,
    )
    db.session.add(token)
    db.session.commit()
    return raw_token

def _get_valid_token(raw_token, purpose):
    if not raw_token:
        return None
    token = UserToken.query.filter_by(
        token_hash=_hash_token(raw_token),
        purpose=purpose,
    ).first()
    if token is None or token.used_at is not None:
        return None

    expires_at = token.expires_at
    if expires_at.tzinfo is not None:
        expires_at = expires_at.replace(tzinfo=None)

    now = get_ist_now()
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)

    if expires_at <= now:
        return None
    return token

def _issue_email_verification(user):
    expires_at = get_ist_now() + timedelta(
        hours=current_app.config.get("EMAIL_VERIFICATION_TOKEN_EXPIRY_HOURS", 24)
    )
    raw_token = _create_user_token(user, "email_verification", expires_at)
    verification_link = _trusted_external_url("auth.verify_email", token=raw_token)
    try:
        send_email(
            user.email,
            "Verify your AgencyOS email address",
            f"Verify your email address:\n\n{verification_link}",
        )
    except EmailDeliveryError:
        logger.error("Email verification delivery failed for user %s", user.id)

# =========================================================
# REGISTRATION (SUPPORTS FORM & SPA JSON)
# =========================================================

@auth_bp.route("/register", methods=["GET", "POST"])
@csrf.exempt
@limiter.limit("10 per hour")
def register():
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        name = data.get("name", "").strip()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        account_type = data.get("account_type", "customer").strip().lower()

        if account_type == "client":
            account_type = "customer"

        if not name or not email or not password:
            if request.is_json:
                return jsonify({"success": False, "message": "Please fill all required fields."}), 400
            flash("Please fill all required fields.", "error")
            return redirect(url_for("auth.register"))

        if not _validate_password_policy(password):
            msg = "Password must be 8-128 characters and include both letters and numbers."
            if request.is_json:
                return jsonify({"success": False, "message": msg}), 400
            flash(msg, "error")
            return redirect(url_for("auth.register"))

        if account_type not in ("customer", "professional"):
            if request.is_json:
                return jsonify({"success": False, "message": "Please select a valid account type."}), 400
            flash("Please select a valid account type.", "error")
            return redirect(url_for("auth.register"))

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            msg = "An account with this email already exists. Please log in."
            if request.is_json:
                return jsonify({"success": False, "message": msg}), 400
            flash(msg, "error")
            return redirect(url_for("auth.register"))

        # Create base User
        user = User(
            email=email,
            role=account_type,
            is_active=True
        )
        if hasattr(user, "full_name"):
            user.full_name = name
        if hasattr(user, "name"):
            user.name = name

        if hasattr(user, "set_password"):
            user.set_password(password)
        else:
            user.password = generate_password_hash(password)

        db.session.add(user)
        db.session.flush()

        if account_type == "professional":
            if request.is_json:
                expertise_values = data.get("expertise", [])
                if isinstance(expertise_values, list):
                    expertise = ", ".join(expertise_values)
                else:
                    expertise = str(expertise_values)
            else:
                expertise = ", ".join(item.strip() for item in request.form.getlist("expertise") if item.strip())

            profile = ProfessionalProfile(
                user_id=user.id,
                expertise=expertise or "General",
                experience=data.get("experience", "1+ year"),
                portfolio=data.get("portfolio", ""),
                about=data.get("about", ""),
                verification_status="Pending",
                profile_completed=False
            )
            db.session.add(profile)
        else:
            profile = CustomerProfile(
                user_id=user.id,
                business_name=data.get("business_name", name),
                phone=data.get("phone", "")
            )
            db.session.add(profile)

        db.session.commit()
        _issue_email_verification(user)

        if request.is_json:
            return jsonify({
                "success": True,
                "message": "Account created successfully. Please login.",
                "user_id": user.id
            }), 201

        flash("Account created successfully. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")

# =========================================================
# LOGIN (SUPPORTS BOTH BROWSER COOKIES & SPA SESSIONS)
# =========================================================

@auth_bp.route("/login", methods=["GET", "POST"])
@csrf.exempt
@limiter.limit("20 per minute")
def login():
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        login_role = data.get("login_role", "").strip().lower()

        if not email or not password:
            if request.is_json:
                return jsonify({"success": False, "message": "Email and password are required."}), 400
            flash("Email and password are required.", "login_error")
            return redirect(url_for("auth.login"))

        user = User.query.filter_by(email=email).first()

        password_valid = False
        if user:
            if hasattr(user, "check_password"):
                password_valid = user.check_password(password)
            elif hasattr(user, "password"):
                password_valid = check_password_hash(user.password, password)

        if not user or not password_valid:
            if request.is_json:
                return jsonify({"success": False, "message": "Invalid email or password."}), 401
            flash("Invalid email or password.", "login_error")
            return redirect(url_for("auth.login"))

        if (
            current_app.config.get("EMAIL_VERIFICATION_REQUIRED", False)
            and getattr(user, "email_verified_at", None) is None
        ):
            msg = "Please verify your email address before logging in."
            if request.is_json:
                return jsonify({"success": False, "message": msg}), 403
            flash(msg, "login_error")
            return redirect(url_for("auth.login"))

        # Setup session
        csrf_token = session.get("csrf_token")
        session.clear()
        if csrf_token is not None:
            session["csrf_token"] = csrf_token

        login_user(user)
        session.permanent = True
        session["user_id"] = user.id
        session["role"] = user.role
        session["session_version"] = getattr(user, "session_version", 0)

        if request.is_json:
            return jsonify({
                "success": True,
                "message": "Logged in successfully.",
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "role": user.role,
                    "full_name": getattr(user, "full_name", getattr(user, "name", user.email))
                }
            }), 200

        if user.role == "admin":
            return redirect(url_for("admin.dashboard") if "admin.dashboard" in current_app.view_functions else url_for("admin_dashboard"))
        elif user.role == "professional":
            return redirect(url_for("professional.dashboard") if "professional.dashboard" in current_app.view_functions else url_for("professional_dashboard"))
        else:
            return redirect(url_for("customer.dashboard") if "customer.dashboard" in current_app.view_functions else url_for("dashboard"))

    return render_template("login.html")

# =========================================================
# LOGOUT
# =========================================================

@auth_bp.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    logout_user()
    session.clear()

    if request.is_json or request.headers.get("Accept") == "application/json":
        return jsonify({"success": True, "message": "Logged out successfully."}), 200

    return redirect(url_for("public.index"))

# =========================================================
# FORGOT & RESET PASSWORD
# =========================================================

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def forgot_password():
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        email = data.get("email", "").strip().lower()
        users = User.query.filter_by(email=email).all()

        for user in users:
            expires_at = get_ist_now() + timedelta(
                minutes=current_app.config.get("PASSWORD_RESET_TOKEN_EXPIRY_MINUTES", 30)
            )
            raw_token = _create_user_token(user, "password_reset", expires_at)
            reset_link = _trusted_external_url("auth.reset_password", token=raw_token)
            try:
                send_email(
                    user.email,
                    "Reset your AgencyOS password",
                    f"Reset your password:\n\n{reset_link}",
                )
            except EmailDeliveryError:
                logger.error("Password reset delivery failed for user %s", user.id)

        msg = "If an account with that email exists, a password reset link has been sent."
        if request.is_json:
            return jsonify({"success": True, "message": msg}), 200

        flash(msg, "info")
        return redirect(url_for("auth.login"))

    return render_template("forgot_password.html")

@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def reset_password(token):
    user_token = _get_valid_token(token, "password_reset")
    if user_token is None:
        flash("This password reset link is invalid or expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    user = User.query.get(user_token.user_id)
    if user is None:
        flash("This password reset link is invalid or expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        password = data.get("password", "")
        confirm_password = data.get("confirm_password", "")

        if not _validate_password_policy(password) or password != confirm_password:
            msg = "Passwords do not match or fail security requirements."
            if request.is_json:
                return jsonify({"success": False, "message": msg}), 400
            flash(msg, "error")
            return redirect(url_for("auth.reset_password", token=token))

        consumed = UserToken.query.filter_by(id=user_token.id, used_at=None).update(
            {"used_at": get_ist_now()}, synchronize_session=False
        )
        if consumed != 1:
            db.session.rollback()
            flash("This password reset link is invalid or expired.", "error")
            return redirect(url_for("auth.forgot_password"))

        if hasattr(user, "set_password"):
            user.set_password(password)
        else:
            user.password = generate_password_hash(password)

        user.session_version = (user.session_version or 0) + 1
        UserToken.query.filter_by(user_id=user.id, purpose="password_reset", used_at=None).update(
            {"used_at": get_ist_now()}, synchronize_session=False
        )
        db.session.commit()

        if request.is_json:
            return jsonify({"success": True, "message": "Password reset successful. Please log in."}), 200

        flash("Your password has been reset. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html", token=token)

@auth_bp.route("/verify-email/<token>")
def verify_email(token):
    user_token = _get_valid_token(token, "email_verification")
    if user_token is None:
        flash("This verification link is invalid or expired.", "error")
        return redirect(url_for("auth.login"))

    user = User.query.get(user_token.user_id)
    if user is None:
        flash("This verification link is invalid or expired.", "error")
        return redirect(url_for("auth.login"))

    UserToken.query.filter_by(id=user_token.id, used_at=None).update(
        {"used_at": get_ist_now()}, synchronize_session=False
    )
    user.email_verified_at = get_ist_now()
    db.session.commit()

    flash("Your email address has been verified. You can now log in.", "success")
    return redirect(url_for("auth.login"))