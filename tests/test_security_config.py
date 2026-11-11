"""Security, IDOR, and Authorization Test Suite for AgencyOS."""

import json
import os
import runpy
import secrets
from datetime import timedelta

import pytest

from app import create_app
from app.config import (
    InsecureSecretKeyError,
    resolve_app_base_url,
    resolve_flask_env,
    resolve_mail_port,
    resolve_rate_limit_in_memory_opt_in,
    resolve_rate_limit_storage_url,
    resolve_session_lifetime_hours,
    validate_production_secret_key,
    validate_secret_key,
)


def test_missing_env_defaults_to_production(monkeypatch):
    monkeypatch.delenv("FLASK_ENV", raising=False)
    assert resolve_flask_env() == "production"


def test_blank_env_defaults_to_production(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "   ")
    assert resolve_flask_env() == "production"


def test_environment_resolution_case_insensitive():
    assert resolve_flask_env("Development") == "development"
    assert resolve_flask_env("  PRODUCTION  ") == "production"


def test_unknown_environment_raises_value_error():
    with pytest.raises(ValueError):
        resolve_flask_env("staging")


def test_missing_secret_rejected():
    for value in (None, "", "   "):
        with pytest.raises(Exception):
            validate_production_secret_key(value)


def test_non_string_secret_rejected():
    with pytest.raises(Exception):
        validate_production_secret_key(123)


def test_known_insecure_secret_rejected():
    with pytest.raises(Exception):
        validate_production_secret_key("agencyos-development-secret-key")


def test_short_secret_rejected():
    with pytest.raises(Exception):
        validate_production_secret_key("short")


def test_weak_secret_rejected():
    with pytest.raises(Exception):
        validate_production_secret_key("a" * 40)


def test_strong_secret_accepted():
    validate_production_secret_key(secrets.token_urlsafe(32))


def test_production_debug_cannot_be_enabled_via_override():
    strong = secrets.token_urlsafe(32)
    app = create_app("production", {"SECRET_KEY": strong, "DEBUG": True,
                                 "RATELIMIT_STORAGE_URI": "memory://",
                                 "RATELIMIT_ALLOW_IN_MEMORY_STORAGE": True,
                                 "APP_BASE_URL": "https://example.com"})
    assert app.config["DEBUG"] is False


def test_unknown_config_name_raises_value_error():
    with pytest.raises(ValueError):
        create_app("does-not-exist")


def test_legacy_blocked_by_default(monkeypatch):
    if not os.path.isfile("app_legacy.py"):
        pytest.skip("app_legacy.py is not present in this checkout")
    monkeypatch.delenv("ALLOW_LEGACY_APP", raising=False)
    with pytest.raises(SystemExit):
        runpy.run_path("app_legacy.py", run_name="__main__")


def test_legacy_source_has_no_hardcoded_insecure_secret():
    if not os.path.isfile("app_legacy.py"):
        pytest.skip("app_legacy.py is not present in this checkout")
    source = open("app_legacy.py", encoding="utf-8", errors="replace").read()
    assert "agencyos-development-secret-key" not in source


def test_modular_app_does_not_execute_legacy():
    import sys
    sys.modules.pop("app_legacy", None)
    import app  # noqa: F401
    assert "app_legacy" not in sys.modules


def test_validate_secret_key_rejects_missing():
    for value in (None, "", "   "):
        with pytest.raises(InsecureSecretKeyError):
            validate_secret_key(value, production=False)


def test_validate_secret_key_rejects_non_string():
    with pytest.raises(InsecureSecretKeyError):
        validate_secret_key(123, production=False)


def test_validate_secret_key_rejects_short():
    with pytest.raises(InsecureSecretKeyError):
        validate_secret_key("short", production=False)


def test_validate_secret_key_accepts_strong():
    validate_secret_key(secrets.token_urlsafe(32), production=False)


def test_production_config_has_secure_cookies():
    strong = secrets.token_urlsafe(32)
    app = create_app("production", {"SECRET_KEY": strong,
                                    "RATELIMIT_STORAGE_URI": "memory://",
                                    "RATELIMIT_ALLOW_IN_MEMORY_STORAGE": True,
                                    "APP_BASE_URL": "https://example.com"})
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["REMEMBER_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_development_config_allows_insecure_cookies():
    strong = secrets.token_urlsafe(32)
    app = create_app("development", {"SECRET_KEY": strong})
    assert app.config["SESSION_COOKIE_SECURE"] is False
    assert app.config["REMEMBER_COOKIE_SECURE"] is False
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_create_app_rejects_invalid_secret_override_development():
    for value in (None, "", "   ", 123, "short"):
        with pytest.raises(InsecureSecretKeyError):
            create_app("development", {"SECRET_KEY": value})


def test_create_app_accepts_valid_development_secret():
    strong = secrets.token_urlsafe(32)
    app = create_app("development", {"SECRET_KEY": strong})
    assert app is not None


def test_create_app_default_config_validates_secret():
    strong = secrets.token_urlsafe(32)
    app = create_app("default", {"SECRET_KEY": strong})
    assert app is not None


def test_create_app_rejects_short_default_secret():
    with pytest.raises(InsecureSecretKeyError):
        create_app("default", {"SECRET_KEY": "short"})


def test_create_app_production_rejects_insecure_fallback():
    with pytest.raises(InsecureSecretKeyError):
        create_app("production", {"SECRET_KEY": "agencyos-development-secret-key"})


def test_create_app_production_accepts_strong_secret():
    strong = secrets.token_urlsafe(32)
    app = create_app("production", {"SECRET_KEY": strong,
                                    "RATELIMIT_STORAGE_URI": "memory://",
                                    "RATELIMIT_ALLOW_IN_MEMORY_STORAGE": True,
                                    "APP_BASE_URL": "https://example.com"})
    assert app is not None


def test_session_lifetime_defaults_to_twelve_hours(monkeypatch):
    monkeypatch.delenv("SESSION_LIFETIME_HOURS", raising=False)
    assert resolve_session_lifetime_hours() == 12
    strong = secrets.token_urlsafe(32)
    app = create_app("development", {"SECRET_KEY": strong})
    assert app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(hours=12)


def test_session_lifetime_override_is_respected(monkeypatch):
    monkeypatch.setenv("SESSION_LIFETIME_HOURS", "24")
    assert resolve_session_lifetime_hours() == 24


def test_session_lifetime_rejects_invalid_values():
    for value in ("", "   ", "abc", "0", "-1", 0, -3, 1.5, True):
        with pytest.raises(ValueError):
            resolve_session_lifetime_hours(value)


def test_session_refresh_each_request_enabled():
    strong = secrets.token_urlsafe(32)
    for config_name in ("development", "production"):
        app = create_app(config_name, {"SECRET_KEY": strong,
                                       "RATELIMIT_STORAGE_URI": "memory://",
                                       "RATELIMIT_ALLOW_IN_MEMORY_STORAGE": True,
                                     "APP_BASE_URL": "https://example.com"})
        assert app.config["SESSION_REFRESH_EACH_REQUEST"] is True


def test_rate_limit_storage_url_defaults_to_memory():
    assert resolve_rate_limit_storage_url() == "memory://"


def test_rate_limit_storage_url_accepts_redis():
    url = "redis://redis-host:6379/0"
    assert resolve_rate_limit_storage_url(url) == url


def test_rate_limit_storage_url_rejects_invalid_values():
    for value in ("", "   ", "http://example.com", "redis://", "redis:///0", 123):
        with pytest.raises(ValueError):
            resolve_rate_limit_storage_url(value)


def test_rate_limit_in_memory_opt_in_parses_values():
    assert resolve_rate_limit_in_memory_opt_in("true") is True
    assert resolve_rate_limit_in_memory_opt_in("false") is False
    for value in ("", "maybe", 123):
        with pytest.raises(ValueError):
            resolve_rate_limit_in_memory_opt_in(value)


def test_production_rejects_memory_storage_by_default():
    strong = secrets.token_urlsafe(32)
    with pytest.raises(RuntimeError):
        create_app("production", {"SECRET_KEY": strong, "APP_BASE_URL": "https://example.com",
                                 "RATELIMIT_ALLOW_IN_MEMORY_STORAGE": False})


def test_production_allows_explicit_in_memory_storage():
    strong = secrets.token_urlsafe(32)
    app = create_app(
        "production",
        {
            "SECRET_KEY": strong,
            "RATELIMIT_STORAGE_URI": "memory://",
            "RATELIMIT_ALLOW_IN_MEMORY_STORAGE": True,
            "APP_BASE_URL": "https://example.com",
        },
    )
    assert app.config["RATELIMIT_STORAGE_URI"] == "memory://"


def test_production_accepts_redis_storage_url():
    strong = secrets.token_urlsafe(32)
    url = "redis://redis-host:6379/0"
    app = create_app(
        "production",
        {"SECRET_KEY": strong, "RATELIMIT_STORAGE_URI": url, "APP_BASE_URL": "https://example.com"},
    )
    assert app.config["RATELIMIT_STORAGE_URI"] == url


def test_production_rejects_invalid_in_memory_opt_in():
    strong = secrets.token_urlsafe(32)
    with pytest.raises(ValueError):
        create_app(
            "production",
            {
                "SECRET_KEY": strong,
                "RATELIMIT_STORAGE_URI": "memory://",
                "RATELIMIT_ALLOW_IN_MEMORY_STORAGE": "maybe",
            },
        )


class TestSafeErrorHandlers:

    def test_not_found_browser_response(self, app, client):
        response = client.get("/definitely-not-a-route", follow_redirects=False)
        assert response.status_code == 404
        assert response.data == b"Page not found."

    def test_not_found_json_response_for_api(self, app, client):
        response = client.get("/api/definitely-not-a-route", follow_redirects=False)
        assert response.status_code == 404
        assert response.get_json() == {
            "success": False,
            "message": "Not found.",
        }

    def test_not_found_json_response_for_payment(self, app, client):
        response = client.get("/payment/definitely-not-a-route", follow_redirects=False)
        assert response.status_code == 404
        assert response.get_json() == {
            "success": False,
            "message": "Not found.",
        }

    def test_internal_server_error_browser_response(self, app, client):
        def boom():
            raise RuntimeError("secret internal detail")

        app.add_url_rule("/__test_internal_error", view_func=boom)

        original_testing = app.config.get("TESTING", False)
        original_propagate = app.config.get("PROPAGATE_EXCEPTIONS", None)
        app.config["TESTING"] = False
        app.config["PROPAGATE_EXCEPTIONS"] = False
        try:
            response = client.get("/__test_internal_error", follow_redirects=False)
        finally:
            app.config["TESTING"] = original_testing
            app.config["PROPAGATE_EXCEPTIONS"] = original_propagate
        assert response.status_code == 500
        assert response.data == b"Internal server error."
        assert b"secret internal detail" not in response.data

    def test_internal_server_error_json_response(self, app, client):
        def boom():
            raise RuntimeError("secret internal detail")

        app.add_url_rule("/api/__test_internal_error", view_func=boom)

        original_testing = app.config.get("TESTING", False)
        original_propagate = app.config.get("PROPAGATE_EXCEPTIONS", None)
        app.config["TESTING"] = False
        app.config["PROPAGATE_EXCEPTIONS"] = False
        try:
            response = client.get("/api/__test_internal_error", follow_redirects=False)
        finally:
            app.config["TESTING"] = original_testing
            app.config["PROPAGATE_EXCEPTIONS"] = original_propagate
        assert response.status_code == 500
        assert response.get_json() == {
            "success": False,
            "message": "Internal server error.",
        }
        assert b"secret internal detail" not in response.data


def test_app_base_url_normalizes_origin():
    assert resolve_app_base_url("https://Example.com:8443/") == "https://Example.com:8443"


def test_app_base_url_rejects_credentials_and_paths():
    for value in (
        "https://user:pass@example.com",
        "https://example.com/reset",
        "ftp://example.com",
        "not-a-url",
    ):
        with pytest.raises(ValueError):
            resolve_app_base_url(value)


def test_app_base_url_production_requires_https():
    with pytest.raises(ValueError):
        resolve_app_base_url("http://example.com", production=True)
    with pytest.raises(ValueError):
        resolve_app_base_url("", production=True)


def test_production_requires_app_base_url():
    import os
    import secrets

    strong = secrets.token_urlsafe(32)
    with pytest.raises(ValueError):
        create_app(
            "production",
            {
                "SECRET_KEY": strong,
                "RAZORPAY_KEY_ID": "rzp_test_example",
                "RAZORPAY_KEY_SECRET": "example_secret_value",
                "RATELIMIT_STORAGE_URI": "redis://redis-host:6379/0",
                "APP_BASE_URL": "",
            },
        )


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    yield


def test_mail_port_defaults_to_587():
    assert resolve_mail_port() == 587


def test_mail_port_accepts_valid_values():
    assert resolve_mail_port("25") == 25
    assert resolve_mail_port(465) == 465


def test_mail_port_rejects_invalid_values():
    for value in ("abc", "", "   ", "0", 70000):
        with pytest.raises(ValueError):
            resolve_mail_port(value)


def test_mail_port_rejects_boolean():
    with pytest.raises(ValueError):
        resolve_mail_port(True)


# =========================================================================
# NEW IDOR & SECURITY AUDIT TESTS
# =========================================================================

def test_idor_project_message_access_blocked(app, client, login, factories):
    """A customer cannot query discussion messages for another customer's project (403)."""
    owner_id = factories.customer("owner-secure@example.com")
    foreign_project_id = factories.project(owner_id, service="Secret Project")

    factories.customer("intruder-secure@example.com")
    login("intruder-secure@example.com", "customer")

    response = client.get(f"/api/project/{foreign_project_id}/messages")
    assert response.status_code == 403
    data = json.loads(response.data)
    assert data["success"] is False


def test_admin_api_scope_generator_requires_admin(app, client, login, factories, csrf_token):
    """Non-admin users cannot access the Gemini AI SOW proxy endpoint (403)."""
    factories.customer("regular-user-ai@example.com")
    login("regular-user-ai@example.com", "customer")

    response = client.post(
        "/api/ai/generate-scope",
        data={"client_name": "Test", "notes": "Build a SaaS"},
        headers={"X-CSRFToken": csrf_token},
    )
    assert response.status_code == 403
    data = json.loads(response.data)
    assert data["success"] is False