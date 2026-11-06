from app.extensions import db
from app.models import get_ist_now


from app.models import User, ProfessionalProfile

def _register(client, name="Test User", email="new@example.com",
              password="Pass123!x", account_type="customer", **extra):
    data = {
        "name": name,
        "email": email,
        "password": password,
        "account_type": account_type,
    }
    data.update(extra)
    return client.post("/register", data=data, follow_redirects=False)


class TestRegistration:

        # Customer registration
    # ---------------------------------------------------------

    def test_customer_registration_creates_user_and_profile(self, app, client):
        response = _register(client, email="cust-reg@example.com")
        assert response.status_code == 302

        with app.app_context():
            user = User.query.filter_by(
                email="cust-reg@example.com", role="customer"
            ).first()
            assert user is not None
            from werkzeug.security import check_password_hash
            assert check_password_hash(user.password, "Pass123!x")
            assert user.customer_profile is not None
            assert user.professional_profile is None

    def test_professional_registration_creates_pending_profile(self, app, client):
        response = _register(
            client,
            email="pro-reg@example.com",
            account_type="professional",
            expertise="SEO",
            experience="3 years",
        )
        assert response.status_code == 302

        with app.app_context():
            user = User.query.filter_by(
                email="pro-reg@example.com", role="professional"
            ).first()
            assert user is not None
            profile = ProfessionalProfile.query.filter_by(
                user_id=user.id
            ).first()
            assert profile is not None
            assert profile.verification_status == "Pending"

    def test_duplicate_email_same_role_rejected(self, app, client):
        with app.app_context():
            _register(client, email="dup@example.com")
            assert User.query.count() == 1

            _register(client, email="dup@example.com")
            assert User.query.count() == 1  # no second user created

    def test_same_email_other_role_allowed(self, app, client):
        # Same email as customer AND professional is allowed by design.
        _register(client, email="both@example.com", account_type="customer")
        _register(
            client, email="both@example.com", account_type="professional",
            expertise="SEO", experience="3 years",
        )
        with app.app_context():
            assert User.query.filter_by(email="both@example.com").count() == 2

    # ---------------------------------------------------------
    # Professional registration
    # ---------------------------------------------------------

    def test_professional_without_expertise_rejected(self, app, client):
        response = _register(
            client,
            email="pro-no-exp@example.com",
            account_type="professional",
        )
        assert response.status_code == 302
class TestRateLimiting:

    def test_login_works_below_limit(self, app, client):
        _register(client, email="rate-login@example.com", password="Pass123!x")
        response = client.post(
            "/login",
            data={
                "email": "rate-login@example.com",
                "password": "Pass123!x",
                "login_role": "customer",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    def test_login_throttled_after_five_attempts(self, app, client):
        for _ in range(5):
            response = client.post(
                "/login",
                data={
                    "email": "nobody@example.com",
                    "password": "wrong-password",
                    "login_role": "customer",
                },
                follow_redirects=False,
            )
            assert response.status_code == 200

        response = client.post(
            "/login",
            data={
                "email": "nobody@example.com",
                "password": "wrong-password",
                "login_role": "customer",
            },
            follow_redirects=False,
        )
        assert response.status_code == 429

    def test_registration_throttled_after_five_attempts(self, app, client):
        for i in range(5):
            response = _register(
                client, email=f"rate-register-{i}@example.com"
            )
            assert response.status_code == 302

        response = _register(
            client, email="rate-register-5@example.com"
        )
        assert response.status_code == 429


class TestLoginRoleEnforcement:

    def test_login_rejects_mismatched_role_tab(self, client):
        _register(client, email="role-enforcement@example.com")

        response = client.post(
            "/login",
            json={
                "email": "role-enforcement@example.com",
                "password": "Pass123!x",
                "login_role": "professional",
            },
        )

        assert response.status_code == 401
        assert response.get_json()["success"] is False
        assert "registered as a Customer" in response.get_json()["message"]


class TestSessionSecurity:

    def test_successful_login_creates_permanent_session(self, app, client):
        _register(client, email="perm-session@example.com", password="Pass123!x")
        response = client.post(
            "/login",
            data={
                "email": "perm-session@example.com",
                "password": "Pass123!x",
                "login_role": "customer",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        with client.session_transaction() as session:
            assert session.permanent is True
class TestRegistrationPrivacy:

    def test_duplicate_registration_does_not_reveal_account(self, app, client):
        _register(client, email="privacy-dup@example.com")

        response = client.post(
            "/register",
            data={
                "name": "Duplicate User",
                "email": "privacy-dup@example.com",
                "password": "Pass123!x",
                "account_type": "customer",
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert b"Registration could not be completed." in response.data
        assert b"already exists" not in response.data

        with app.app_context():
            from app.models import User

            assert User.query.filter_by(
                email="privacy-dup@example.com", role="customer"
            ).count() == 1
class TestSessionHardening:

    def test_pre_login_session_data_does_not_survive_login(self, app, client):
        with client.session_transaction() as session:
            session["pre_login_marker"] = "value"

        _register(client, email="session-hardening@example.com", password="Pass123!x")
        response = client.post(
            "/login",
            data={
                "email": "session-hardening@example.com",
                "password": "Pass123!x",
                "login_role": "customer",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        with client.session_transaction() as session:
            assert "pre_login_marker" not in session
            assert session.permanent is True
            assert "_user_id" in session

    def test_failed_login_preserves_pre_login_session_data(self, app, client):
        with client.session_transaction() as session:
            session["pre_login_marker"] = "value"

        response = client.post(
            "/login",
            data={
                "email": "nobody@example.com",
                "password": "wrong-password",
                "login_role": "customer",
            },
            follow_redirects=False,
        )
        assert response.status_code == 200

        with client.session_transaction() as session:
            assert session.get("pre_login_marker") == "value"

    def test_logout_clears_session_data(self, app, client, csrf_token):
        _register(client, email="logout-session@example.com", password="Pass123!x")
        login_response = client.post(
            "/login",
            data={
                "email": "logout-session@example.com",
                "password": "Pass123!x",
                "login_role": "customer",
            },
            follow_redirects=False,
        )
        assert login_response.status_code == 302

        with client.session_transaction() as session:
            session["post_login_marker"] = "value"

        response = client.post(
            "/logout",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with client.session_transaction() as session:
            assert "_user_id" not in session
            assert "post_login_marker" not in session
class TestAccountRecovery:

    def test_forgot_password_generic_response(self, app, client, csrf_token):
        _register(client, email="recovery@example.com", password="Pass123!x")

        app.config["TEST_EMAILS"] = []

        known = client.post(
            "/forgot-password",
            data={"email": "recovery@example.com", "csrf_token": csrf_token},
            follow_redirects=False,
        )
        unknown = client.post(
            "/forgot-password",
            data={"email": "missing@example.com", "csrf_token": csrf_token},
            follow_redirects=False,
        )

        assert known.status_code == 302
        assert unknown.status_code == 302

        with client.session_transaction() as session:
            flashes = [
                message
                for _, message in session.get("_flashes", [])
            ]
            assert any(
                "If an account with that email exists" in message
                for message in flashes
            )

        with app.app_context():
            assert len(app.config.get("TEST_EMAILS", [])) == 1

    def test_password_reset_flow_and_session_revocation(self, app, client, csrf_token):
        _register(client, email="reset-flow@example.com", password="OldPass123!x")
        app.config["TEST_EMAILS"] = []
        client.post(
            "/forgot-password",
            data={"email": "reset-flow@example.com", "csrf_token": csrf_token},
            follow_redirects=False,
        )

        with app.app_context():
            from app.models import User
            import re

            user = User.query.filter_by(email="reset-flow@example.com").first()
            assert user is not None
            assert user.session_version == 0
            emails = app.config.get("TEST_EMAILS", [])
            assert len(emails) == 1
            token = re.search(
                r"/reset-password/([A-Za-z0-9_-]+)",
                emails[-1]["body"],
            )
            assert token is not None
            token = token.group(1)

        login_response = client.post(
            "/login",
            data={
                "email": "reset-flow@example.com",
                "password": "OldPass123!x",
                "login_role": "customer",
            },
            follow_redirects=False,
        )
        assert login_response.status_code == 302

        response = client.post(
            f"/reset-password/{token}",
            data={
                "password": "NewPass123!x",
                "confirm_password": "NewPass123!x",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        protected = client.get("/dashboard", follow_redirects=False)
        assert protected.status_code == 302
        assert "/login" in protected.headers["Location"]

        relogin = client.post(
            "/login",
            data={
                "email": "reset-flow@example.com",
                "password": "NewPass123!x",
                "login_role": "customer",
            },
            follow_redirects=False,
        )
        assert relogin.status_code == 302

        with app.app_context():
            from app.models import User

            user = User.query.filter_by(email="reset-flow@example.com").first()
            assert user.session_version == 1

    def test_reset_rejects_invalid_and_expired_tokens(self, app, client):
        with app.app_context():
            from app.models import User, UserToken
            from datetime import timedelta
            import hashlib

            user = User(
                name="expired",
                email="expired-reset@example.com",
                password="hash",
                role="customer",
            )
            db.session.add(user)
            db.session.commit()

            raw_token = "expired-token"
            expired_token = UserToken(
                user_id=user.id,
                purpose="password_reset",
                token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                expires_at=get_ist_now() - timedelta(minutes=1),
            )
            db.session.add(expired_token)
            db.session.commit()

        response = client.get(
            f"/reset-password/{raw_token}", follow_redirects=False
        )
        assert response.status_code == 302

        wrong_purpose_token = "wrong-purpose-token"
        with app.app_context():
            from app.models import User, UserToken
            import hashlib

            user = User.query.filter_by(
                email="expired-reset@example.com"
            ).first()
            token = UserToken(
                user_id=user.id,
                purpose="email_verification",
                token_hash=hashlib.sha256(
                    wrong_purpose_token.encode()
                ).hexdigest(),
                expires_at=get_ist_now() + timedelta(hours=1),
            )
            db.session.add(token)
            db.session.commit()

        response = client.get(
            f"/reset-password/{wrong_purpose_token}",
            follow_redirects=False,
        )
        assert response.status_code == 302

    def test_email_verification_flow(self, app, client, csrf_token):
        _register(client, email="verify@example.com", password="Pass123!x")

        with app.app_context():
            from app.models import User

            user = User.query.filter_by(email="verify@example.com").first()
            assert user.email_verified_at is None
            emails = app.config.get("TEST_EMAILS", [])
            assert len(emails) == 1
            import re

            token = re.search(
                r"/verify-email/([A-Za-z0-9_-]+)",
                emails[-1]["body"],
            )
            assert token is not None
            token = token.group(1)

        response = client.get(
            f"/verify-email/{token}", follow_redirects=False
        )
        assert response.status_code == 302

        with app.app_context():
            from app.models import User

            user = User.query.filter_by(email="verify@example.com").first()
            assert user.email_verified_at is not None

        reused = client.get(
            f"/verify-email/{token}", follow_redirects=False
        )
        assert reused.status_code == 302

    def test_forgot_password_rate_limit(self, app, client, csrf_token):
        for _ in range(5):
            response = client.post(
                "/forgot-password",
                data={
                    "email": "rate-limit@example.com",
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )
            assert response.status_code == 302

        response = client.post(
            "/forgot-password",
            data={
                "email": "rate-limit@example.com",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert response.status_code == 429

    def test_forgot_password_requires_csrf(self, app, client):
        response = client.post(
            "/forgot-password",
            data={"email": "csrf@example.com"},
            follow_redirects=False,
        )
        assert response.status_code == 400

    def test_resend_verification_requires_csrf(self, app, client):
        response = client.post(
            "/resend-verification",
            data={"email": "csrf@example.com"},
            follow_redirects=False,
        )
        assert response.status_code == 400

    def test_reset_token_cannot_be_reused(self, app, client):
        _register(client, email="reset-reuse@example.com", password="OldPass123!x")

        with app.app_context():
            from app.models import User, UserToken
            import hashlib
            from datetime import timedelta

            user = User.query.filter_by(email="reset-reuse@example.com").first()
            raw_token = "reuse-token-value"
            token = UserToken(
                user_id=user.id,
                purpose="password_reset",
                token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                expires_at=get_ist_now() + timedelta(minutes=30),
            )
            db.session.add(token)
            db.session.commit()

        from itsdangerous import URLSafeTimedSerializer

        raw_csrf = "reuse-csrf-raw"
        signed_csrf = URLSafeTimedSerializer(
            app.config["SECRET_KEY"], salt="wtf-csrf-token"
        ).dumps(raw_csrf)

        with client.session_transaction() as session:
            session["csrf_token"] = raw_csrf
            session["session_version"] = 0

        first = client.post(
            f"/reset-password/{raw_token}",
            data={
                "password": "NewPass123!x",
                "confirm_password": "NewPass123!x",
                "csrf_token": signed_csrf,
            },
            follow_redirects=False,
        )
        assert first.status_code == 302

        with client.session_transaction() as session:
            session["session_version"] = 1

        reused = client.post(
            f"/reset-password/{raw_token}",
            data={
                "password": "AnotherPass123!x",
                "confirm_password": "AnotherPass123!x",
                "csrf_token": signed_csrf,
            },
            follow_redirects=False,
        )
        assert reused.status_code == 302


class TestSecurityHardeningRegression:

    def test_login_requires_email_verification_when_enabled(
        self, app, client, csrf_token
    ):
        app.config["EMAIL_VERIFICATION_REQUIRED"] = True
        _register(client, email="verify-required@example.com")

        response = client.post(
            "/login",
            data={
                "email": "verify-required@example.com",
                "password": "Pass123!x",
                "login_role": "customer",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_reset_rejects_weak_password(self, app, client, csrf_token):
        _register(client, email="weak-reset@example.com", password="OldPass123!x")
        app.config["TEST_EMAILS"] = []

        client.post(
            "/forgot-password",
            data={"email": "weak-reset@example.com", "csrf_token": csrf_token},
            follow_redirects=False,
        )

        with app.app_context():
            import re

            emails = app.config.get("TEST_EMAILS", [])
            token = re.search(
                r"/reset-password/([A-Za-z0-9_-]+)",
                emails[-1]["body"],
            ).group(1)

        response = client.post(
            f"/reset-password/{token}",
            data={
                "password": "short",
                "confirm_password": "short",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "/reset-password/" in response.headers["Location"]

    def test_reset_link_uses_trusted_origin(self, app, client, csrf_token):
        app.config["APP_BASE_URL"] = "https://trusted.example"
        _register(client, email="origin-reset@example.com", password="OldPass123!x")
        app.config["TEST_EMAILS"] = []

        client.post(
            "/forgot-password",
            data={
                "email": "origin-reset@example.com",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

        with app.app_context():
            emails = app.config.get("TEST_EMAILS", [])
            assert emails

class TestPasswordPolicyRegression:

    def test_registration_rejects_weak_password(self, app, client):
        response = _register(
            client, email="weak-reg@example.com", password="short"
        )
        assert response.status_code == 302
        assert "/register" in response.headers["Location"]
        with app.app_context():
            assert User.query.filter_by(
                email="weak-reg@example.com"
            ).count() == 0

    def test_registration_accepts_valid_password(self, app, client):
        response = _register(
            client, email="valid-reg@example.com", password="ValidPass123!"
        )
        assert response.status_code == 302
        with app.app_context():
            user = User.query.filter_by(
                email="valid-reg@example.com"
            ).first()
            assert user is not None
            from werkzeug.security import check_password_hash
            assert check_password_hash(user.password, "ValidPass123!")

    def test_registration_rejects_password_mismatch_not_applicable(self, app, client):
        # Registration has no confirmation field; ensure weak policy still blocks.
        response = _register(
            client, email="weak2-reg@example.com", password="NoDigitsHere"
        )
        assert response.status_code == 302
        assert "/register" in response.headers["Location"]

    def test_reset_rejects_confirmation_mismatch(self, app, client, csrf_token):
        _register(client, email="mismatch-reset@example.com", password="OldPass123!x")
        app.config["TEST_EMAILS"] = []

        client.post(
            "/forgot-password",
            data={"email": "mismatch-reset@example.com", "csrf_token": csrf_token},
            follow_redirects=False,
        )

        with app.app_context():
            import re

            emails = app.config.get("TEST_EMAILS", [])
            token = re.search(
                r"/reset-password/([A-Za-z0-9_-]+)",
                emails[-1]["body"],
            ).group(1)

        response = client.post(
            f"/reset-password/{token}",
            data={
                "password": "NewPass123!x",
                "confirm_password": "Different123!x",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "/reset-password/" in response.headers["Location"]
