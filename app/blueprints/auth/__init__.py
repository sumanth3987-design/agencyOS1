"""AgencyOS Authentication & Authorization Blueprint with Standardized JSON error boundaries."""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import csrf, db, limiter
from app.models import CustomerProfile, ProfessionalProfile, User, UserToken, get_ist_now
from app.services.email import EmailDeliveryError, send_email

auth_bp = Blueprint("auth", __name__)
logger = logging.getLogger(__name__)


# =========================================================
# SESSION HARDENING & REVOCATION HOOK
# =========================================================

@auth_bp.before_app_request
def validate_session_version():
    """Revokes session if user's session_version has incremented (e.g. password reset)."""
    if current_user.is_authenticated:
        try:
            expected_version = getattr(current_user, "session_version", 0) or 0
            current_version = session.get("session_version", 0) or 0
            if current_version != expected_version:
                logout_user()
                session.clear()
                flash("Your session has expired. Please log in again.", "warning")
                return redirect(url_for("auth.login"))
        except Exception as e:
            logger.error(f"Session version validation error: {e}")

def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _validate_password_policy(password: str) -> bool:
    return (
        isinstance(password, str)
        and 8 <= len(password) <= 128
        and any(char.isalpha() for char in password)
        and any(char.isdigit() for char in password)
    )


def _trusted_external_url(endpoint: str, **values) -> str:
    path = url_for(endpoint, _external=False, **values)
    base_url = (current_app.config.get("APP_BASE_URL") or "").strip()
    if base_url:
        parsed = urlparse(base_url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    return url_for(endpoint, _external=True, **values)


def _create_user_token(user: User, purpose: str, expires_at: datetime) -> str:
    raw_token = secrets.token_urlsafe(32)
    token = UserToken()
    token.user_id = user.id
    token.purpose = purpose
    token.token_hash = _hash_token(raw_token)
    token.expires_at = expires_at
    db.session.add(token)
    db.session.commit()
    return raw_token


def _get_valid_token(raw_token: str, purpose: str) -> UserToken | None:
    if not raw_token:
        return None
    token = UserToken.query.filter_by(
        token_hash=_hash_token(raw_token),
        purpose=purpose,
    ).first()
    if token is None or token.used_at is not None:
        return None

    now = get_ist_now()
    expires_at = token.expires_at
    
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=now.tzinfo)

    if expires_at <= now:
        return None
    return token


def _issue_email_verification(user: User) -> None:
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
# REGISTRATION
# =========================================================

@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour")
@csrf.exempt
def register() -> Any:
    if request.method == "POST":
        try:
            data = request.get_json(silent=True) or request.form
            name = data.get("name", "").strip()
            email = data.get("email", "").strip().lower()
            password = data.get("password", "")
            account_type = data.get("account_type", "customer").strip().lower()

            if account_type == "client":
                account_type = "customer"

            if not name or not email or not password:
                flash("Please fill all required fields.", "error")
                return redirect(url_for("auth.register"))

            if not _validate_password_policy(password):
                flash("Password must be 8-128 chars with letters and numbers.", "error")
                return redirect(url_for("auth.register"))

            if account_type not in ("customer", "professional"):
                flash("Please select a valid account type.", "error")
                return redirect(url_for("auth.register"))

            if User.query.filter_by(email=email, role=account_type).first():
                flash("Registration could not be completed.", "error")
                return redirect(url_for("auth.register"))

            user = User()
            user.email = email
            user.role = account_type
            user.name = name
            user.full_name = name
            user.set_password(password)
            db.session.add(user)
            db.session.flush()

            if account_type == "professional":
                expertise_list = request.form.getlist("expertise") if request.form else data.get("expertise", [])
                if isinstance(expertise_list, str):
                    expertise_list = [expertise_list]
                
                expertise = ", ".join(item.strip() for item in expertise_list if item.strip())
                profile = ProfessionalProfile()
                profile.user_id = user.id
                profile.expertise = expertise or "General"
                profile.experience = data.get("experience", "1+ year")
                profile.portfolio = data.get("portfolio", "")
                profile.verification_status = "Pending"
                db.session.add(profile)
            else:
                profile = CustomerProfile()
                profile.user_id = user.id
                profile.business_name = data.get("business_name", name)
                profile.phone = data.get("phone", "")
                db.session.add(profile)

            db.session.commit()
            _issue_email_verification(user)
            
            flash("Account created. Please verify your email.", "success")
            return redirect(url_for("auth.login"))

        except IntegrityError:
            db.session.rollback()
            flash("Registration could not be completed.", "error")
            return redirect(url_for("auth.register"))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error during registration: {str(e)}")
            flash("An unexpected error occurred. Please try again.", "error")
            return redirect(url_for("auth.register"))

    return render_template("register.html")


# =========================================================
# LOGIN
# =========================================================

@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
@csrf.exempt
def login() -> Any:
    if request.method == "POST":
        try:
            data = request.get_json(silent=True) or request.form
            email = data.get("email", "").strip().lower()
            password = data.get("password", "")
            login_role = data.get("login_role", "").strip().lower()

            user = User.query.filter_by(email=email).first()
            if not user or not user.check_password(password):
                flash("Invalid email or password.", "login_error")
                return render_template("login.html"), 200

            user_db_role = (user.role or "customer").lower()
            if user_db_role == "client":
                user_db_role = "customer"

            if login_role:
                if login_role == "client":
                    login_role = "customer"
                if user_db_role != login_role and user_db_role != "admin":
                    role_display = "a Customer" if user_db_role == "customer" else user_db_role.title()
                    return jsonify({
                        "success": False,
                        "message": f"Account registered as {role_display}. Please select the matching tab."
                    }), 401

            if current_app.config.get("EMAIL_VERIFICATION_REQUIRED", False) and not user.email_verified_at:
                flash("Please verify your email address first.", "login_error")
                return redirect(url_for("auth.login"))

            active_csrf = session.get("csrf_token")
            session.clear()
            if active_csrf:
                session["csrf_token"] = active_csrf

            login_user(user)
            session.permanent = True
            session["user_id"] = user.id
            session["role"] = user.role
            session["session_version"] = getattr(user, "session_version", 0) or 0
            session.modified = True

            next_url = request.args.get("next")
            if next_url and next_url.startswith("/"):
                return redirect(next_url)

            if user.role == "admin":
                return redirect(url_for("admin.admin_dashboard"))
            elif user.role == "professional":
                return redirect(url_for("professional.professional_dashboard"))
            return redirect(url_for("customer.dashboard"))

        except Exception as e:
            logger.error(f"Login error: {str(e)}")
            flash("An unexpected error occurred during login.", "login_error")
            return render_template("login.html"), 200

    return render_template("login.html")


# =========================================================
# LOGOUT
# =========================================================

@auth_bp.route("/logout", methods=["GET", "POST"])
def logout() -> Any:
    if request.method == "GET":
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        from flask_wtf.csrf import generate_csrf
        return f'<html><body><h1>Confirm Logout</h1><form method="POST"><input type="hidden" name="csrf_token" value="{generate_csrf()}"><button type="submit">Confirm Logout</button></form></body></html>', 200

    logout_user()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("public.index"))


# =========================================================
# PASSWORD RECOVERY & EMAIL VERIFICATION
# =========================================================

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def forgot_password() -> Any:
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            expires_at = get_ist_now() + timedelta(hours=2)
            raw_token = _create_user_token(user, "password_reset", expires_at)
            reset_url = _trusted_external_url("auth.reset_password", token=raw_token)
            try:
                send_email(
                    user.email,
                    "Reset your AgencyOS password",
                    f"Reset your password:\n\n{reset_url}",
                )
            except EmailDeliveryError:
                logger.error("Password reset email delivery failed for user %s", user.id)

        flash("If an account with that email exists, a password reset link has been dispatched.", "info")
        return redirect(url_for("auth.login"))

    return render_template("login.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str) -> Any:
    user_token = _get_valid_token(token, "password_reset")
    if user_token is None:
        flash("This reset token is invalid or expired.", "error")
        return redirect(url_for("auth.login"))

    user = db.session.get(User, user_token.user_id)
    if user is None:
        flash("User not found.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        password = request.form.get("password", "")
        password_confirm = request.form.get("confirm_password") or request.form.get("password_confirm", "")

        if password != password_confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("auth.reset_password", token=token))

        if not _validate_password_policy(password):
            flash("Password must be 8-128 chars with letters and numbers.", "error")
            return redirect(url_for("auth.reset_password", token=token))

        user.set_password(password)
        user.session_version = (user.session_version or 0) + 1
        user_token.used_at = get_ist_now()
        db.session.commit()

        flash("Password reset successfully. You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("login.html", token=token)


@auth_bp.route("/verify-email/<token>")
@limiter.limit("5 per minute")
def verify_email(token: str) -> Any:
    try:
        user_token = _get_valid_token(token, "email_verification")
        if user_token is None:
            flash("This verification link is invalid or expired.", "error")
            return redirect(url_for("auth.login"))

        user = db.session.get(User, user_token.user_id)
        if user is None:
            flash("This verification link is invalid or expired.", "error")
            return redirect(url_for("auth.login"))

        user_token.used_at = get_ist_now()
        user.email_verified_at = get_ist_now()
        db.session.commit()

        flash("Your email address has been verified. You can now log in.", "success")
        return redirect(url_for("auth.login"))
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Email verification error: {str(e)}")
        flash("An error occurred during verification. Please try again.", "error")
        return redirect(url_for("auth.login"))


@auth_bp.route("/resend-verification", methods=["POST"])
@limiter.limit("5 per hour")
def resend_verification() -> Any:
    email = request.form.get("email", "").strip().lower()
    user = User.query.filter_by(email=email).first()
    if user and not user.email_verified_at:
        _issue_email_verification(user)
    flash("If an unverified account exists, a new verification link has been sent.", "info")
    return redirect(url_for("auth.login"))