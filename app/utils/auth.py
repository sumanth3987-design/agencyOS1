from functools import wraps
from flask import session, jsonify, request, redirect, url_for, flash
from flask_login import current_user
from app.models import User

def _get_active_auth_user():
    """Retrieve authenticated user from Flask-Login or standard session."""
    if current_user and getattr(current_user, "is_authenticated", False):
        return current_user
    user_id = session.get("user_id")
    if user_id:
        return User.query.get(user_id)
    return None

def roles_required(*allowed_roles):
    """Universal RBAC decorator protecting JSON APIs and web views."""
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
            user = _get_active_auth_user()
            is_api = request.path.startswith("/api/") or request.is_json or request.headers.get("Accept") == "application/json"

            if not user or not getattr(user, "is_active", True):
                if is_api:
                    return jsonify({"error": "Authentication required", "code": "UNAUTHORIZED"}), 401
                flash("Please log in to access this page.", "warning")
                return redirect(url_for("auth.login"))

            user_role = getattr(user, "role", "").lower()
            if user_role not in normalized_allowed:
                if is_api:
                    return jsonify({
                        "error": "You do not have permission to access this resource",
                        "code": "ACCESS_DENIED"
                    }), 403
                flash("You do not have permission to view this page.", "danger")
                
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

# Alias for backward compatibility
role_required = roles_required