# Save this directly into your file: app/__init__.py

import os
import logging
import re
from logging.handlers import RotatingFileHandler
from flask import Flask, url_for, request, jsonify, flash, session, redirect, send_from_directory
from werkzeug.routing import BuildError
from types import SimpleNamespace
from urllib.parse import urlsplit
from app.config import (
    config_by_name,
    resolve_app_base_url,
    resolve_rate_limit_in_memory_opt_in,
    resolve_rate_limit_storage_url,
    validate_production_secret_key,
    validate_production_razorpay_credentials,
    validate_secret_key,
)
from app.extensions import db, login_manager, csrf, limiter, migrate
from app.utils.currency import format_currency


def _build_bare_endpoint_map(view_functions):
    """Map bare endpoint names to qualified blueprint endpoints."""
    bare_map = {}
    ambiguous = set()

    for endpoint in view_functions:
        if "." not in endpoint:
            continue

        bare = endpoint.split(".", 1)[1]

        if bare in bare_map and bare_map[bare] != endpoint:
            ambiguous.add(bare)

        bare_map[bare] = endpoint

    if ambiguous:
        raise RuntimeError(
            "Ambiguous bare endpoint names across blueprints: "
            + ", ".join(sorted(ambiguous))
        )

    return bare_map


def configure_logging(app):
    if not os.path.exists("logs"):
        os.mkdir("logs")
    file_handler = RotatingFileHandler(
        "logs/agencyos.log", maxBytes=1024000, backupCount=10
    )
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]"
        )
    )
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info("AgencyOS startup")


def create_app(config_name="default", config_overrides=None):
    if config_name not in config_by_name:
        raise ValueError(
            "Unknown configuration name: %r. Expected one of: %s."
            % (config_name, ", ".join(sorted(config_by_name)))
        )

    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.jinja_env.filters["format_currency"] = format_currency
    app.config.from_object(config_by_name[config_name])
    if config_overrides is not None:
        app.config.update(config_overrides)

    if config_name == "production":
        app.config["DEBUG"] = False
        validate_production_secret_key(app.config.get("SECRET_KEY"))
        validate_production_razorpay_credentials(
            app.config.get("RAZORPAY_KEY_ID"),
            app.config.get("RAZORPAY_KEY_SECRET"),
        )
    else:
        validate_secret_key(app.config.get("SECRET_KEY"), production=False)

    storage_url = resolve_rate_limit_storage_url(
        app.config.get("RATELIMIT_STORAGE_URI")
    )
    app.config["RATELIMIT_STORAGE_URI"] = storage_url
    allow_in_memory = resolve_rate_limit_in_memory_opt_in(
        app.config.get("RATELIMIT_ALLOW_IN_MEMORY_STORAGE", False)
    )
    app.config["RATELIMIT_ALLOW_IN_MEMORY_STORAGE"] = allow_in_memory

    if config_name == "production" and storage_url.startswith("memory://"):
        if not allow_in_memory:
            raise RuntimeError(
                "RATELIMIT_STORAGE_URL must use redis:// or rediss:// in "
                "production unless RATELIMIT_ALLOW_IN_MEMORY_STORAGE=true."
            )
        import warnings
        warnings.warn(
            "Rate limiting is using in-memory storage in production.",
            RuntimeWarning,
            stacklevel=2,
        )
    elif config_name == "production" and storage_url.startswith(("redis://", "rediss://")):
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError(
                "Redis client is required for production rate-limit storage."
            ) from exc

    app.config["APP_BASE_URL"] = resolve_app_base_url(
        app.config.get("APP_BASE_URL"),
        production=(config_name == "production"),
    )

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    # Auto-create any newly added database tables
    with app.app_context():
        db.create_all()

    configure_logging(app)

    # Health check endpoint for monitoring uptime
    @app.route("/healthz", methods=["GET"])
    def health_check():
        return jsonify({"status": "healthy", "service": "AgencyOS"}), 200

    @app.before_request
    def enforce_session_version():
        from flask_login import current_user, logout_user
        if current_user.is_authenticated:
            expected_version = getattr(current_user, "session_version", 0)
            if session.get("session_version") != expected_version:
                logout_user()
                session.clear()
                flash("Your session has expired. Please log in again.", "warning")
                return redirect(url_for("login"))

    @app.errorhandler(429)
    def handle_rate_limit_exceeded(error):
        if request.path.startswith("/payment") or request.path.startswith("/api/"):
            return jsonify({
                "success": False,
                "message": "Too many requests. Please try again later.",
            }), 429
        return "Too many requests. Please try again later.", 429

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        app.logger.error(f"Server Error: {error}")
        return jsonify({
            "error": "An internal server error occurred",
            "code": "SERVER_ERROR"
        }), 500

    from app.models import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    @app.cli.command("send-reminders")
    def send_reminders_command():
        """Send due payment and deliverable review reminders."""
        from click import ClickException
        from app.services.reminders import run_due_reminders

        summary = run_due_reminders()
        print(
            "Reminder run complete: "
            f"{summary['payment_sent']} payment, "
            f"{summary['review_sent']} review, "
            f"{summary['failed']} failed."
        )
        if summary["failed"]:
            raise ClickException("Some reminders could not be sent; inspect application logs.")

    # Register Blueprints
    from app.blueprints.auth import auth_bp
    from app.blueprints.customer import customer_bp
    from app.blueprints.professional import professional_bp
    from app.blueprints.admin import admin_bp
    from app.blueprints.collaboration import collaboration_bp
    from app.blueprints.payments import payments_bp
    from app.blueprints.api import api_bp
    from app.routes.public import public_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(professional_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(collaboration_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(public_bp)

    # Legacy bare-endpoint resolver for url_for("dashboard")-style calls
    bare_map = _build_bare_endpoint_map(app.view_functions)

    def resolve_bare_endpoint(error, endpoint, values):
        if "." in endpoint:
            return None
        qualified = bare_map.get(endpoint)
        if not qualified:
            return None
        try:
            return url_for(qualified, **values)
        except BuildError:
            return None

    app.url_build_error_handlers.append(resolve_bare_endpoint)

    @app.context_processor
    def inject_agency_settings():
        from app.models import AgencySetting

        defaults = {
            "agency_name": "AgencyOS",
            "primary_color": "#6366f1",
            "logo_url": None,
            "whatsapp_support_number": "+919876543210",
        }
        settings = db.session.get(AgencySetting, 1)
        if settings is not None:
            name = (settings.agency_name or "").strip()
            color = settings.primary_color or ""
            logo_url = settings.logo_url or ""
            phone = settings.whatsapp_support_number or ""
            if name and len(name) <= 120:
                defaults["agency_name"] = name
            if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                defaults["primary_color"] = color
            if logo_url:
                try:
                    parsed = urlsplit(logo_url)
                    host = parsed.hostname
                    if (
                        parsed.scheme.lower() in ("http", "https")
                        and parsed.netloc
                        and host
                        and parsed.username is None
                        and parsed.password is None
                        and not any(char.isspace() or ord(char) < 32 for char in logo_url)
                    ):
                        parsed.port
                        defaults["logo_url"] = logo_url
                except ValueError:
                    pass
            if re.fullmatch(r"\+?\d{8,15}", phone):
                defaults["whatsapp_support_number"] = phone

        channels = (
            int(defaults["primary_color"][index:index + 2], 16) / 255
            for index in (1, 3, 5)
        )
        linear_channels = tuple(
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        )
        luminance = (
            0.2126 * linear_channels[0]
            + 0.7152 * linear_channels[1]
            + 0.0722 * linear_channels[2]
        )
        defaults["primary_contrast"] = "#09090b" if luminance > 0.179 else "#ffffff"
        return {"agency_settings": SimpleNamespace(**defaults)}

    # Register CLI commands
    from app.cli import create_admin_command
    app.cli.add_command(create_admin_command)

    # ------------------------------------------------------------
    # Vite Frontend Build Assets & Single Page Fallback
    # ------------------------------------------------------------
    dist_dir = os.path.abspath(os.path.join(app.root_path, "..", "static", "dist"))
    assets_dir = os.path.join(dist_dir, "assets")

    @app.route("/assets/<path:filename>")
    def serve_vite_assets(filename):
        if os.path.exists(os.path.join(assets_dir, filename)):
            return send_from_directory(assets_dir, filename)
        return jsonify({"error": "Asset not found"}), 404

    @app.errorhandler(404)
    def not_found_error(error):
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"error": "Resource not found", "code": "NOT_FOUND"}), 404
        # SPA client-side route fallback if dist exists
        index_file = os.path.join(dist_dir, "index.html")
        if os.path.exists(index_file):
            return send_from_directory(dist_dir, "index.html")
        return jsonify({"error": "Page not found", "code": "NOT_FOUND"}), 404

    return app