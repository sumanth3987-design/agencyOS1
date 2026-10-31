import logging
import os

import razorpay

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf import CSRFProtect
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

logger = logging.getLogger(__name__)

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"

csrf = CSRFProtect()
migrate = Migrate()

# Shared rate limiter. Storage is configured through RATELIMIT_STORAGE_URL.
# memory:// is suitable for local development and tests only; production
# multi-worker deployments should use shared Redis storage.
limiter = Limiter(
    key_func=get_remote_address,
)


def get_razorpay_client():
    """Create a Razorpay client from environment/config values.

    Returns ``None`` when initialization fails so callers can respond
    in a controlled way instead of crashing.
    """
    key_id = os.getenv("RAZORPAY_KEY_ID", "rzp_test_placeholder")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "placeholder_secret")
    try:
        return razorpay.Client(auth=(key_id, key_secret))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Razorpay client initialization failed: %s",
            type(exc).__name__,
        )
        return None
