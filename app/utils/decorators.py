"""AgencyOS Role-Based Access Control, Session Hardening, and IDOR Prevention Decorators."""

from functools import wraps
from typing import Any, Callable, cast

from flask import abort, current_app, flash, jsonify, redirect, request, session, url_for
from flask_login import current_user

from app.extensions import db
from app.models import Invoice, Project, User


def _get_request_user() -> User | None:
    """Resolve and verify current user from Flask-Login session or session ID."""
    user_obj = None
    if current_user and getattr(current_user, "is_authenticated", False):
        user_obj = getattr(current_user, "_get_current_object", lambda: current_user)()
    else:
        user_id = session.get("user_id") or session.get("_user_id")
        if user_id:
            try:
                user_obj = db.session.get(User, int(user_id))
            except Exception:
                user_obj = User.query.get(int(user_id))

    if user_obj:
        user = cast(User, user_obj)
        # Verify session version against revoked sessions
        stored_version = session.get("session_version", 0)
        user_version = getattr(user, "session_version", 0)
        if stored_version != user_version and "session_version" in session:
            current_app.logger.warning(f"Revoked session detected for user {user.id}")
            return None
        return user
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
            if user_role not in normalized_allowed and user_role != "admin":
                current_app.logger.warning(
                    f"Forbidden portal access: User {user.id} ({user_role}) attempted to access {request.path}"
                )
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
        is_api_request = (
            request.path.startswith("/api/")
            or request.is_json
            or request.headers.get("Accept") == "application/json"
        )

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


def verify_project_access(project_id: int, user: User | None = None) -> Project:
    """Guards against IDOR by ensuring the user owns or is assigned to the specified project.

    Returns the Project model instance or aborts with 404 (preventing resource enumeration).
    """
    user = user or _get_request_user()
    if not user:
        abort(401)

    project = db.session.get(Project, project_id)
    if not project:
        abort(404)

    user_role = getattr(user, "role", "").lower()
    if user_role == "admin":
        return project

    if user_role in ("customer", "client") and project.customer_id == user.id:
        return project

    if user_role == "professional" and (
        project.assigned_professional_id == user.id
        or any(app.professional_id == user.id for app in project.applications)
    ):
        return project

    current_app.logger.warning(
        f"IDOR violation attempt: User {user.id} ({user_role}) accessed Project {project_id}"
    )
    abort(404)


def verify_invoice_access(invoice_id: int, user: User | None = None) -> Invoice:
    """Guards against IDOR by ensuring the client owns the specified invoice.

    Returns the Invoice model instance or aborts with 404.
    """
    user = user or _get_request_user()
    if not user:
        abort(401)

    invoice = db.session.get(Invoice, invoice_id)
    if not invoice:
        abort(404)

    user_role = getattr(user, "role", "").lower()
    if user_role == "admin":
        return invoice

    if user_role in ("customer", "client") and invoice.customer_id == user.id:
        return invoice

    current_app.logger.warning(
        f"IDOR violation attempt: User {user.id} ({user_role}) accessed Invoice {invoice_id}"
    )
    abort(404)