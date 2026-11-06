from functools import wraps
from flask import flash, redirect, url_for, jsonify, request, session
from flask_login import current_user
from app.models import User

def _get_request_user():
    """Resolve current user from Flask-Login session or session ID."""
    if current_user and getattr(current_user, "is_authenticated", False):
        return current_user
    user_id = session.get("user_id")
    if user_id:
        return User.query.get(user_id)
    return None

def role_required(*allowed_roles):
    """Enforce role checking supporting both traditional Flask views and JSON API requests."""
    # Normalize aliases ('client' <-> 'customer')
    normalized_allowed = set()
    for r in allowed_roles:
        normalized_allowed.add(r.lower())
        if r.lower() == "client":
            normalized_allowed.add("customer")
        elif r.lower() == "customer":
            normalized_allowed.add("client")

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = _get_request_user()

            is_api_request = request.path.startswith("/api/") or request.is_json or request.headers.get("Accept") == "application/json"

            if not user or not getattr(user, "is_active", True):
                if is_api_request:
                    return jsonify({"error": "Authentication required", "code": "UNAUTHORIZED"}), 401
                flash("Please log in to access this page.", "warning")
                return redirect(url_for("auth.login"))

            user_role = getattr(user, "role", "").lower()
            if user_role not in normalized_allowed:
                if is_api_request:
                    return jsonify({"error": "Forbidden: insufficient permissions", "code": "FORBIDDEN"}), 403
                flash("You do not have permission to access this resource.", "danger")
                
                # Standardized redirects matching application structure
                try:
                    if user_role == "admin":
                        return redirect(url_for("admin.dashboard"))
                    elif user_role == "professional":
                        return redirect(url_for("professional.dashboard"))
                    return redirect(url_for("customer.dashboard"))
                except Exception:
                    # Fallback if standard route is temporarily unavailable
                    return redirect(url_for("public.index"))

            return f(*args, **kwargs)
        return decorated_function
    return decorator

# Alias to maintain compatibility with both naming conventions
roles_required = role_required

def verified_professional_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = _get_request_user()
        is_api_request = request.path.startswith("/api/") or request.is_json

        if not user or not getattr(user, "is_active", True):
            if is_api_request:
                return jsonify({"error": "Authentication required", "code": "UNAUTHORIZED"}), 401
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login"))

        profile = getattr(user, "professional_profile", None)
        if not profile or getattr(profile, "verification_status", "Pending") != "Verified":
            if is_api_request:
                return jsonify({"error": "Professional profile verification pending", "code": "VERIFICATION_REQUIRED"}), 403
            flash("Your profile must be verified by an administrator before accessing project applications.", "warning")
            return redirect(url_for("professional.dashboard"))

        return f(*args, **kwargs)
    return decorated_function