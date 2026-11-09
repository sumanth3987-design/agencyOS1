"""AgencyOS Role-Based Access Control and Authorization Decorators."""

from functools import wraps
from typing import Any, Callable, cast

from flask import flash, jsonify, redirect, request, session, url_for
from flask_login import current_user

from app.models import User


def _get_request_user() -> User | None:
    """Resolve current user from Flask-Login session or session ID."""
    if current_user and getattr(current_user, "is_authenticated", False):
        user_obj = getattr(current_user, "_get_current_object", lambda: current_user)()
        return cast(User, user_obj)
    user_id = session.get("user_id")
    if user_id:
        return cast(User | None, User.query.get(int(user_id)))
    return None


def role_required(*allowed_roles: str) -> Callable:
    """Enforce role checking supporting both traditional Flask views and JSON API requests."""
    normalized_allowed = set()
    for r in allowed_roles:
        normalized_allowed.add(r.lower())
        if r.lower() == "client":
            normalized_allowed.add("customer")
        elif r.lower() == "customer":
            normalized_allowed.add("client")

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(*args: Any, **kwargs: Any) -> Any:
            user = _get_request_user()

            is_api_request = (
                request.path.startswith("/api/")
                or request.is_json
                or request.headers.get("Accept") == "application/json"
            )

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

                try:
                    if user_role == "admin":
                        return redirect(url_for("admin.admin_dashboard"))
                    elif user_role == "professional":
                        return redirect(url_for("professional.professional_dashboard"))
                    return redirect(url_for("customer.dashboard"))
                except Exception:
                    return redirect(url_for("public.index"))

            return f(*args, **kwargs)
        return decorated_function
    return decorator


roles_required = role_required


def verified_professional_required(f: Callable) -> Callable:
    """Ensure the authenticated professional has an approved verification status."""
    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
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
                return jsonify({
                    "error": "Professional profile verification pending",
                    "code": "VERIFICATION_REQUIRED",
                }), 403
            flash("Your profile must be verified by an administrator before accessing sprint applications.", "warning")
            return redirect(url_for("professional.professional_dashboard"))

        return f(*args, **kwargs)
    return decorated_function