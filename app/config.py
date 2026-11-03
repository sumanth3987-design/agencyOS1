"""Configuration Management & Secret Key Validators for AgencyOS."""

import os
from datetime import timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".env",
    )
)


def resolve_flask_env(env=None):
    if env is None:
        env = os.getenv("FLASK_ENV")
    if env is None or str(env).strip() == "":
        return "production"
    value = str(env).strip().lower()
    if value == "development":
        return "development"
    if value == "production":
        return "production"
    raise ValueError(f"Unknown FLASK_ENV value: {env!r}. Expected 'development' or 'production'.")


def resolve_session_lifetime_hours(value=None):
    if value is None:
        value = os.getenv("SESSION_LIFETIME_HOURS", "12")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"SESSION_LIFETIME_HOURS must be a positive integer, got {value!r}.")
    text = str(value).strip()
    if not text:
        raise ValueError(f"SESSION_LIFETIME_HOURS must be a positive integer, got {value!r}.")
    try:
        hours = int(text, 10)
    except ValueError as exc:
        raise ValueError(f"SESSION_LIFETIME_HOURS must be a positive integer, got {value!r}.") from exc
    if hours <= 0:
        raise ValueError(f"SESSION_LIFETIME_HOURS must be greater than zero, got {value!r}.")
    return hours


def resolve_app_base_url(value=None, *, production=False):
    if value is None:
        value = os.getenv("APP_BASE_URL", "")
    url = str(value).strip()
    if not url:
        if production:
            raise ValueError("APP_BASE_URL is required in production.")
        return ""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"APP_BASE_URL must use http:// or https://, got {value!r}.")
    if not parsed.netloc or parsed.hostname is None:
        raise ValueError(f"APP_BASE_URL must include a valid host, got {value!r}.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("APP_BASE_URL must not include credentials.")
    if parsed.path not in ("", "/"):
        raise ValueError("APP_BASE_URL must not include a path.")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("APP_BASE_URL must not include params, query, or fragment.")
    if production and parsed.scheme != "https":
        raise ValueError("APP_BASE_URL must use HTTPS in production.")
    return f"{parsed.scheme}://{parsed.netloc}"


def resolve_rate_limit_storage_url(value=None):
    if value is None:
        value = os.getenv("RATELIMIT_STORAGE_URL", "memory://")
    if not isinstance(value, str):
        raise ValueError(f"RATELIMIT_STORAGE_URL must be a string, got {value!r}.")
    url = value.strip()
    if not url:
        raise ValueError("RATELIMIT_STORAGE_URL must not be empty.")
    parsed = urlparse(url)
    if parsed.scheme not in {"memory", "redis", "rediss"}:
        raise ValueError(f"RATELIMIT_STORAGE_URL must use memory://, redis://, or rediss://, got {value!r}.")
    if parsed.scheme in {"redis", "rediss"} and not parsed.hostname:
        raise ValueError(f"RATELIMIT_STORAGE_URL must include a Redis host, got {value!r}.")
    return url


def resolve_rate_limit_in_memory_opt_in(value=None):
    if value is None:
        value = os.getenv("RATELIMIT_ALLOW_IN_MEMORY_STORAGE", "false")
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        raise ValueError(f"RATELIMIT_ALLOW_IN_MEMORY_STORAGE must be a boolean value, got {value!r}.")
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"RATELIMIT_ALLOW_IN_MEMORY_STORAGE must be true or false, got {value!r}.")


_INSECURE_FALLBACKS = {
    "agencyos-development-secret-key",
    "agencyos-secure-fallback-key-change-in-prod",
    "placeholder_secret",
}


class InsecureSecretKeyError(ValueError, RuntimeError):
    """Raised when a production secret key is missing or insecure."""


def validate_secret_key(secret_key, production=False):
    if not isinstance(secret_key, str):
        raise InsecureSecretKeyError("SECRET_KEY must be a non-empty string of at least 32 characters.")
    if secret_key.strip() == "":
        raise InsecureSecretKeyError("SECRET_KEY is required but was not set in the environment.")
    if len(secret_key) < 32:
        raise InsecureSecretKeyError("SECRET_KEY is too short; it must be at least 32 characters long.")
    if production:
        if secret_key in _INSECURE_FALLBACKS:
            raise InsecureSecretKeyError(
                "SECRET_KEY is set to a known insecure fallback value. Set a strong, unique SECRET_KEY."
            )
        if len(set(secret_key)) < 8:
            raise InsecureSecretKeyError("SECRET_KEY is too weak; use a cryptographically random value.")


def validate_production_secret_key(secret_key):
    validate_secret_key(secret_key, production=True)


class InsecureRazorpayCredentialsError(ValueError, RuntimeError):
    """Raised when production Razorpay credentials are missing or placeholders."""


def validate_production_razorpay_credentials(*args, **kwargs):
    key_id = kwargs.get("key_id", kwargs.get("razorpay_key_id"))
    key_secret = kwargs.get("key_secret", kwargs.get("razorpay_key_secret"))

    if key_id is None and key_secret is None and args:
        if len(args) == 1 and isinstance(args[0], dict):
            config = args[0]
            key_id = config.get("RAZORPAY_KEY_ID", config.get("razorpay_key_id"))
            key_secret = config.get("RAZORPAY_KEY_SECRET", config.get("razorpay_key_secret"))
        elif len(args) == 1 and hasattr(args[0], "get"):
            config = args[0]
            key_id = config.get("RAZORPAY_KEY_ID", config.get("razorpay_key_id"))
            key_secret = config.get("RAZORPAY_KEY_SECRET", config.get("razorpay_key_secret"))
        elif len(args) >= 2:
            key_id = args[0]
            key_secret = args[1]

    if not isinstance(key_id, str) or key_id.strip() == "":
        raise InsecureRazorpayCredentialsError("RAZORPAY_KEY_ID is required in production.")
    if not isinstance(key_secret, str) or key_secret.strip() == "":
        raise InsecureRazorpayCredentialsError("RAZORPAY_KEY_SECRET is required in production.")
    if key_id.strip() == "rzp_test_placeholder":
        raise InsecureRazorpayCredentialsError("RAZORPAY_KEY_ID is set to a placeholder value.")
    if key_secret.strip() == "placeholder_secret":
        raise InsecureRazorpayCredentialsError("RAZORPAY_KEY_SECRET is set to a placeholder value.")


validate_razorpay_credentials = validate_production_razorpay_credentials
validate_production_razorpay_config = validate_production_razorpay_credentials
validate_razorpay_config = validate_production_razorpay_credentials


def resolve_mail_port(value=None):
    if value is None:
        value = os.getenv("MAIL_PORT", "587")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"MAIL_PORT must be an integer between 1 and 65535, got {value!r}.")
    text = str(value).strip()
    if not text:
        raise ValueError(f"MAIL_PORT must be an integer between 1 and 65535, got {value!r}.")
    try:
        port = int(text, 10)
    except ValueError as exc:
        raise ValueError(f"MAIL_PORT must be an integer between 1 and 65535, got {value!r}.") from exc
    if port < 1 or port > 65535:
        raise ValueError(f"MAIL_PORT must be an integer between 1 and 65535, got {value!r}.")
    return port


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///agencyos.db")
    if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    WIRE_BANK_NAME = os.getenv("WIRE_BANK_NAME", "HDFC Bank / Global Business Account")
    WIRE_ACCOUNT_NAME = os.getenv("WIRE_ACCOUNT_NAME", "Agency OS Technologies")
    WIRE_ACCOUNT_NUMBER = os.getenv("WIRE_ACCOUNT_NUMBER", "XXXX-XXXX-XXXX")
    WIRE_SWIFT_BIC = os.getenv("WIRE_SWIFT_BIC", "HDFCINBBXXX")
    WIRE_IBAN = os.getenv("WIRE_IBAN", "IN37HDFC0000...")

    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    PERMANENT_SESSION_LIFETIME = timedelta(hours=resolve_session_lifetime_hours())
    SESSION_REFRESH_EACH_REQUEST = True

    RATELIMIT_ENABLED = resolve_flask_env() == "production"
    RATELIMIT_STORAGE_URI = None
    RATELIMIT_ALLOW_IN_MEMORY_STORAGE = None
    RATELIMIT_IN_MEMORY_FALLBACK_ENABLED = False

    RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_placeholder")
    RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "placeholder_secret")

    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    MAIL_BACKEND = os.getenv("MAIL_BACKEND", "console").strip().lower()
    MAIL_SERVER = os.getenv("MAIL_SERVER", "").strip()
    MAIL_PORT = resolve_mail_port()
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").strip().lower() in {"1", "true", "yes", "on"}
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "").strip()
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "no-reply@agencyos.local").strip()
    MAIL_SUPPRESS_SEND = os.getenv("MAIL_SUPPRESS_SEND", "false").strip().lower() in {"1", "true", "yes", "on"}

    PASSWORD_RESET_TOKEN_EXPIRY_MINUTES = 30
    EMAIL_VERIFICATION_TOKEN_EXPIRY_HOURS = 24
    EMAIL_VERIFICATION_REQUIRED = os.getenv("EMAIL_VERIFICATION_REQUIRED", "false").strip().lower() in {"1", "true", "yes", "on"}
    APP_BASE_URL = resolve_app_base_url(os.getenv("APP_BASE_URL", ""))
    IST = ZoneInfo("Asia/Kolkata")


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}

validate_secret_key(os.getenv("SECRET_KEY"), production=False)