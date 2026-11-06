"""Flask Application Factory & Blueprint Registry for AgencyOS."""

import logging
import os
import re
import sqlite3
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

from app.config import (
    config_by_name,
    resolve_app_base_url,
    resolve_rate_limit_in_memory_opt_in,
    resolve_rate_limit_storage_url,
    validate_production_razorpay_credentials,
    validate_production_secret_key,
    validate_secret_key,
)
from app.extensions import csrf, db, limiter, login_manager, migrate
from app.models import User
from app.utils.currency import format_currency


def _build_bare_endpoint_map(view_functions) -> dict[str, str]:
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
        raise RuntimeError("Ambiguous bare endpoint names across blueprints: " + ", ".join(sorted(ambiguous)))
    return bare_map


def configure_logging(app: Flask) -> None:
    # Skip file logging in testing mode to prevent Windows [WinError 32] file lock crashes
    if app.config.get("TESTING"):
        app.logger.setLevel(logging.INFO)
        return

    if not os.path.exists("logs"):
        os.mkdir("logs")
    file_handler = RotatingFileHandler("logs/agencyos.log", maxBytes=1024000, backupCount=10)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]")
    )
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info("AgencyOS startup")


def _auto_sync_sqlite_columns(app: Flask) -> None:
    """Inspects and automatically adds missing columns to existing SQLite tables."""
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite"):
        return

    path = uri.replace("sqlite:///", "")
    if not os.path.isabs(path):
        candidate = os.path.join(app.instance_path, os.path.basename(path))
        if os.path.exists(candidate):
            path = candidate
        else:
            path = os.path.abspath(path)

    if not os.path.exists(path):
        candidate = os.path.join(app.instance_path, "agencyos.db")
        if os.path.exists(candidate):
            path = candidate
        else:
            return

    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()

        def cols(tbl: str) -> list[str]:
            try:
                cur.execute(f"PRAGMA table_info({tbl});")
                return [r[1] for r in cur.fetchall()]
            except Exception:
                return []

        # 1. Update user table
        u_cols = cols("user")
        if u_cols:
            if "is_verified" not in u_cols:
                cur.execute("ALTER TABLE user ADD COLUMN is_verified BOOLEAN DEFAULT 0;")
            if "verification_status" not in u_cols:
                cur.execute("ALTER TABLE user ADD COLUMN verification_status VARCHAR(30) DEFAULT 'Pending';")
            if "verified_at" not in u_cols:
                cur.execute("ALTER TABLE user ADD COLUMN verified_at DATETIME;")
            if "rejection_reason" not in u_cols:
                cur.execute("ALTER TABLE user ADD COLUMN rejection_reason TEXT;")
            cur.execute("UPDATE user SET is_verified = 1, verification_status = 'Verified' WHERE role = 'admin';")

        # 2. Update project table
        p_cols = cols("project")
        if p_cols:
            if "required_skills" not in p_cols:
                cur.execute("ALTER TABLE project ADD COLUMN required_skills TEXT;")
            if "assigned_professional_id" not in p_cols:
                cur.execute("ALTER TABLE project ADD COLUMN assigned_professional_id INTEGER;")

        # 3. Update professional_profile table
        pp_cols = cols("professional_profile")
        if pp_cols:
            if "pending_balance" not in pp_cols:
                cur.execute("ALTER TABLE professional_profile ADD COLUMN pending_balance NUMERIC(12, 2) DEFAULT 0.00;")
            if "available_balance" not in pp_cols:
                cur.execute("ALTER TABLE professional_profile ADD COLUMN available_balance NUMERIC(12, 2) DEFAULT 0.00;")
            if "bank_beneficiary_name" not in pp_cols:
                cur.execute("ALTER TABLE professional_profile ADD COLUMN bank_beneficiary_name VARCHAR(120);")

        # 4. Update professional_project_application table
        ppa_cols = cols("professional_project_application")
        if ppa_cols:
            if "proposal" not in ppa_cols:
                cur.execute("ALTER TABLE professional_project_application ADD COLUMN proposal TEXT;")
            if "proposed_rate" not in ppa_cols:
                cur.execute("ALTER TABLE professional_project_application ADD COLUMN proposed_rate NUMERIC(12, 2);")
            if "estimated_days" not in ppa_cols:
                cur.execute("ALTER TABLE professional_project_application ADD COLUMN estimated_days INTEGER;")

        conn.commit()
        conn.close()
    except Exception as exc:
        app.logger.warning(f"Auto SQLite sync note: {exc}")


def create_app(config_name: str = "default", config_overrides: dict[str, Any] | None = None) -> Flask:
    if config_name not in config_by_name:
        raise ValueError(f"Unknown configuration name: {config_name!r}. Expected one of: {', '.join(sorted(config_by_name))}.")

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

    storage_url = resolve_rate_limit_storage_url(app.config.get("RATELIMIT_STORAGE_URI"))
    app.config["RATELIMIT_STORAGE_URI"] = storage_url
    allow_in_memory = resolve_rate_limit_in_memory_opt_in(app.config.get("RATELIMIT_ALLOW_IN_MEMORY_STORAGE", False))
    app.config["RATELIMIT_ALLOW_IN_MEMORY_STORAGE"] = allow_in_memory

    if config_name == "production" and storage_url.startswith("memory://"):
        if not allow_in_memory:
            raise RuntimeError("RATELIMIT_STORAGE_URL must use redis:// or rediss:// in production.")
        import warnings
        warnings.warn("Rate limiting is using in-memory storage in production.", RuntimeWarning, stacklevel=2)
    elif config_name == "production" and storage_url.startswith(("redis://", "rediss://")):
        try:
            import redis  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("Redis client is required for production rate-limit storage.") from exc

    app.config["APP_BASE_URL"] = resolve_app_base_url(
        app.config.get("APP_BASE_URL"),
        production=(config_name == "production"),
    )

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    # Automatic Table & Column Synchronization
    with app.app_context():
        os.makedirs(app.instance_path, exist_ok=True)
        db.create_all()
        _auto_sync_sqlite_columns(app)

    configure_logging(app)

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
                return redirect(url_for("auth.login"))

    @app.errorhandler(429)
    def handle_rate_limit_exceeded(error):
        if request.path.startswith("/payment") or request.path.startswith("/api/"):
            return jsonify({"success": False, "message": "Too many requests. Please try again later."}), 429
        return "Too many requests. Please try again later.", 429

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        app.logger.error("Server Error: %s", error)
        return jsonify({"error": "An internal server error occurred", "code": "SERVER_ERROR"}), 500

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @app.cli.command("send-reminders")
    def send_reminders_command():
        """Send due payment and deliverable review reminders."""
        from click import ClickException
        from app.services.reminders import run_due_reminders

        summary = run_due_reminders()
        print(f"Reminder run complete: {summary['payment_sent']} payment, {summary['review_sent']} review, {summary['failed']} failed.")
        if summary["failed"]:
            raise ClickException("Some reminders could not be sent; inspect application logs.")

    # Register Blueprints
    from app.blueprints.admin import admin_bp
    from app.blueprints.api import api_bp
    from app.blueprints.auth import auth_bp
    from app.blueprints.collaboration import collaboration_bp
    from app.blueprints.customer import customer_bp
    from app.blueprints.payments import payments_bp
    from app.blueprints.payments.stripe import stripe_bp
    from app.blueprints.payments.wire import wire_bp
    from app.blueprints.professional import professional_bp
    from app.routes.public import public_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(professional_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(collaboration_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(wire_bp)
    app.register_blueprint(stripe_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(public_bp)

    bare_map = _build_bare_endpoint_map(app.view_functions)

    def resolve_bare_endpoint(
        error: Exception, endpoint: str, values: dict[str, Any]
    ) -> str:
        if "." in endpoint:
            raise error
        qualified = bare_map.get(endpoint)
        if not qualified:
            raise error
        return url_for(qualified, **values)

    app.url_build_error_handlers.append(resolve_bare_endpoint)

    @app.context_processor
    def inject_agency_settings():
        from app.models import AgencySetting

        defaults = {
            "agency_name": "AgencyOS",
            "primary_color": "#09090b",
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
                        defaults["logo_url"] = logo_url
                except ValueError:
                    pass
            if re.fullmatch(r"\+?\d{8,15}", phone):
                defaults["whatsapp_support_number"] = phone

        channels = (
            int(defaults["primary_color"][index : index + 2], 16) / 255
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

    from app.cli import create_admin_command
    app.cli.add_command(create_admin_command)

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
        index_file = os.path.join(dist_dir, "index.html")
        if os.path.exists(index_file):
            return send_from_directory(dist_dir, "index.html")
        try:
            return render_template("404.html"), 404
        except Exception:
            return jsonify({"error": "Page not found", "code": "NOT_FOUND"}), 404

    return app