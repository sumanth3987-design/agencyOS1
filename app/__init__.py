import os
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, url_for, request, jsonify, flash, session, redirect, send_from_directory
from werkzeug.routing import BuildError
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


def _build_bare_endpoint_map(view_functions):
    """Map bare endpoint names to qualified blueprint endpoints.

    Templates and blueprint code call url_for("dashboard") in the style
    of the legacy monolithic app.py, while blueprint endpoints are
    qualified (e.g. "customer.dashboard"). A bare name that resolves to
    more than one blueprint function would be ambiguous and is rejected
    at startup. App-level endpoints such as "static" are already bare
    and are skipped.
    """

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
            "Unknown configuration name: %r. "
            "Expected one of: %s."
            % (config_name, ", ".join(sorted(config_by_name)))
        )

    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object(config_by_name[config_name])
    if config_overrides is not None:
        app.config.update(config_overrides)

    # Production is always non-debug, even if an override tries to enable it.
    if config_name == "production":
        app.config["DEBUG"] = False
        # Fail safely in production when the secret is missing or insecure.
        # Validated after overrides are applied.
        validate_production_secret_key(app.config.get("SECRET_KEY"))
    else:
        # Development and default configurations require a usable SECRET_KEY
        # after overrides are applied, without production-strength checks.
        validate_secret_key(app.config.get("SECRET_KEY"), production=False)

    # Fail safely in production when Razorpay credentials are missing
    # or still set to development placeholders.
    if config_name == "production":
        validate_production_razorpay_credentials(
            app.config.get("RAZORPAY_KEY_ID"),
            app.config.get("RAZORPAY_KEY_SECRET"),
        )

    # Validate and normalize rate-limit storage. Production uses Redis by
    # default; in-memory storage requires an explicit opt-in.
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
            "Rate limiting is using in-memory storage in production. "
            "This is only safe for a single worker.",
            RuntimeWarning,
            stacklevel=2,
        )
    elif config_name == "production" and storage_url.startswith(
        ("redis://", "rediss://")
    ):
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError(
                "Redis client is required for production rate-limit storage."
            ) from exc

    # Validate and normalize the trusted public origin used for externally
    # visible links. Production requires a valid HTTPS origin and must not
    # fall back to request-derived URLs.
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

    configure_logging(app)

    # Health check endpoint for monitoring uptime
    @app.route("/healthz", methods=["GET"])
    def health_check():
        return jsonify({"status": "healthy", "service": "AgencyOS"}), 200

    @app.before_request
    def enforce_session_version():
        from flask import session
        from flask_login import current_user, logout_user

        if current_user.is_authenticated:
            expected_version = getattr(
                current_user, "session_version", 0
            )
            if session.get("session_version") != expected_version:
                logout_user()
                session.clear()
                flash(
                    "Your session has expired. Please log in again.",
                    "warning",
                )
                return redirect(url_for("login"))

    @app.errorhandler(429)
    def handle_rate_limit_exceeded(error):
        if request.path.startswith("/payment"):
            return jsonify({
                "success": False,
                "message": "Too many requests. Please try again later.",
            }), 429
        return "Too many requests. Please try again later.", 429

    @app.errorhandler(404)
    def not_found_error(error):
        return jsonify({"error": "Resource not found", "code": "NOT_FOUND"}), 404

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        app.logger.error(f"Server Error: {error}")
        return jsonify(
            {"error": "An internal server error occurred", "code": "SERVER_ERROR"}
        ), 500

    from app.models import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Register Blueprints
    from app.blueprints.auth import auth_bp
    from app.blueprints.customer import customer_bp
    from app.blueprints.professional import professional_bp
    from app.blueprints.admin import admin_bp
    from app.blueprints.payments import payments_bp
    from app.blueprints.api import api_bp
    from app.routes.public import public_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(professional_bp)
    # Route rules in these blueprints already carry their full paths
    # (/admin/..., /payment/..., /api/...), so no url_prefix here.
    app.register_blueprint(admin_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(public_bp)

    # ------------------------------------------------------------
    # Legacy bare-endpoint resolver
    # ------------------------------------------------------------
    # url_for("dashboard")-style calls must resolve to the single
    # qualified blueprint endpoint. Unknown endpoints re-raise the
    # original BuildError unchanged.
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

    # Register CLI commands
    from app.cli import create_admin_command
    app.cli.add_command(create_admin_command)

    # ------------------------------------------------------------
    # Frontend serving (Vite build output)
    # ------------------------------------------------------------
    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def serve_frontend(path):
        dist_dir = os.path.join(app.root_path, '..', 'static', 'dist')
        if path != "" and os.path.exists(os.path.join(dist_dir, path)):
            return send_from_directory(dist_dir, path)
        if os.path.exists(os.path.join(dist_dir, 'index.html')):
            return send_from_directory(dist_dir, 'index.html')
        return jsonify({'message': 'AgencyOS API Running. Build frontend with npm run build'}), 200

    return app
