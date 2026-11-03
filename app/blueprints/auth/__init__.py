"""AgencyOS Authentication & Authorization Blueprint.

Manages registration, secure login, role-based redirection, 
password recovery, and email verification.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
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
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import csrf, db, limiter
from app.models import CustomerProfile, ProfessionalProfile, User, UserToken, get_ist_now
from app.services.email import EmailDeliveryError, send_email

auth_bp = Blueprint("auth", __name__)
logger = logging.getLogger(__name__)


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
@csrf.exempt
@limiter.limit("10 per hour")
def register() -> ResponseReturnValue:
    if request.method == "POST":
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

        if User.query.filter_by(email=email).first():
            flash("An account with this email already exists.", "error")
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
            expertise = ", ".join(item.strip() for item in request.form.getlist("expertise") if item.strip())
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

    return render_template("register.html")


# =========================================================
# LOGIN
# =========================================================

@auth_bp.route("/login", methods=["GET", "POST"])
@csrf.exempt
@limiter.limit("20 per minute")
def login() -> ResponseReturnValue:
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        login_role = data.get("login_role", "").strip().lower()

        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash("Invalid email or password.", "login_error")
            return redirect(url_for("auth.login"))

        # Role enforcement: Ensure the user matches the portal role
        user_db_role = user.role.lower()
        if user_db_role == "client":
            user_db_role = "customer"

        # Admins can bypass role tabs
        if user_db_role != login_role and user_db_role != "admin":
            flash(f"Account registered as {user_db_role.title()}. Please select the matching tab.", "login_error")
            return redirect(url_for("auth.login"))

        if current_app.config.get("EMAIL_VERIFICATION_REQUIRED", False) and not user.email_verified_at:
            flash("Please verify your email address first.", "login_error")
            return redirect(url_for("auth.login"))

        session.clear()
        login_user(user)
        session.permanent = True
        session["user_id"] = user.id
        session["role"] = user.role
        session["session_version"] = user.session_version

        if user.role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        elif user.role == "professional":
            return redirect(url_for("professional.professional_dashboard"))
        return redirect(url_for("customer.dashboard"))

    return render_template("login.html")


# =========================================================
# LOGOUT
# =========================================================

@auth_bp.route("/logout", methods=["GET", "POST"])
@login_required
def logout() -> ResponseReturnValue:
    logout_user()
    session.clear()
    return redirect(url_for("public.index"))

# Add this to app/blueprints/auth/__init__.py

@auth_bp.route("/verify-email/<token>")
def verify_email(token: str) -> ResponseReturnValue:
    user_token = _get_valid_token(token, "email_verification")
    if user_token is None:
        flash("This verification link is invalid or expired.", "error")
        return redirect(url_for("auth.login"))

    user = User.query.get(user_token.user_id)
    if user is None:
        flash("This verification link is invalid or expired.", "error")
        return redirect(url_for("auth.login"))

    # Mark token as used
    UserToken.query.filter_by(id=user_token.id, used_at=None).update(
        {"used_at": get_ist_now()}, synchronize_session=False
    )
    
    # Mark user as verified
    user.email_verified_at = get_ist_now()
    db.session.commit()

    flash("Your email address has been verified. You can now log in.", "success")
    return redirect(url_for("auth.login"))