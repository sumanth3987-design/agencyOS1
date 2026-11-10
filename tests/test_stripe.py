import json
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest
import stripe

from app.models import Project, Payment, Invoice, StripeWebhookEvent
from app.utils.currency import calculate_tax_rate
from app.utils.stripe_config import (
    STRIPE_PRICE_MATRIX,
    SUPPORTED_STRIPE_CURRENCIES,
    get_stripe_price_id,
    build_checkout_session_params,
)


# ---------------------------------------------------------------------------
# Tax rate tests
# ---------------------------------------------------------------------------

class TestCalculateTaxRate:
    def test_inr_domestic_18_percent(self):
        assert calculate_tax_rate("IN", "INR") == Decimal("0.18")

    def test_usd_international_zero(self):
        assert calculate_tax_rate("US", "USD") == Decimal("0.00")

    def test_eur_international_zero(self):
        assert calculate_tax_rate("DE", "EUR") == Decimal("0.00")

    def test_gbp_international_zero(self):
        assert calculate_tax_rate("GB", "GBP") == Decimal("0.00")


# ---------------------------------------------------------------------------
# Stripe config tests
# ---------------------------------------------------------------------------

class TestStripeConfig:
    def test_price_matrix_has_all_currencies(self):
        for currency in ("USD", "EUR", "GBP"):
            assert currency in STRIPE_PRICE_MATRIX
            assert "Basic Website" in STRIPE_PRICE_MATRIX[currency]

    def test_supported_currencies(self):
        assert "USD" in SUPPORTED_STRIPE_CURRENCIES
        assert "EUR" in SUPPORTED_STRIPE_CURRENCIES
        assert "GBP" in SUPPORTED_STRIPE_CURRENCIES
        assert "INR" not in SUPPORTED_STRIPE_CURRENCIES

    def test_get_stripe_price_id_usd(self):
        price_id = get_stripe_price_id("USD", "Basic Website")
        assert price_id == "price_usd_basic_placeholder"

    def test_get_stripe_price_id_eur(self):
        price_id = get_stripe_price_id("EUR", "SEO Growth")
        assert price_id == "price_eur_seo_growth_placeholder"

    def test_get_stripe_price_id_invalid_currency(self):
        with pytest.raises(ValueError, match="Unsupported currency for Stripe"):
            get_stripe_price_id("CAD", "Basic Website")

    def test_get_stripe_price_id_invalid_package(self):
        with pytest.raises(ValueError, match="not found in Stripe price matrix"):
            get_stripe_price_id("USD", "Nonexistent Package")

    def test_build_checkout_session_params(self):
        params = build_checkout_session_params(
            project_id=42,
            package_name="Basic Website",
            currency="USD",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
        assert params["mode"] == "payment"
        assert params["payment_method_types"] == ["card"]
        assert len(params["line_items"]) == 1
        assert params["line_items"][0]["quantity"] == 1
        assert params["client_reference_id"] == "42"
        assert params["metadata"]["project_id"] == "42"
        assert params["metadata"]["package_name"] == "Basic Website"
        assert params["metadata"]["currency"] == "USD"


# ---------------------------------------------------------------------------
# Checkout session creation tests
# ---------------------------------------------------------------------------

class TestCreateStripeCheckout:
    def _make_stripe_session(self, session_id="cs_test_123",
                             url="https://checkout.stripe.com/s/cs_test_123"):
        return MagicMock(id=session_id, url=url)

    def test_anonymous_redirected(self, app, client, csrf_token):
        response = client.post(
            "/payment/stripe/checkout/1",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_non_customer_forbidden(self, app, client, login, factories, csrf_token):
        factories.user("professional", "stripe-pro@example.com")
        login("stripe-pro@example.com", "professional")

        response = client.post(
            "/payment/stripe/checkout/1",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 403

    def test_project_not_found(self, app, client, login, factories, csrf_token):
        factories.user("customer", "stripe-cust@example.com")
        login("stripe-cust@example.com", "customer")

        response = client.post(
            "/payment/stripe/checkout/99999",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 404

    def test_non_payable_state_rejected(
        self, app, client, login, factories, csrf_token
    ):
        customer_id = factories.user("customer", "stripe-nonpay@example.com")
        project_id = factories.project(customer_id, status="Assigned")
        login("stripe-nonpay@example.com", "customer")

        response = client.post(
            f"/payment/stripe/checkout/{project_id}",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["success"] is False
        assert data["message"] == "Project is not in a payable state."
    def test_already_paid_rejected(
        self, app, client, login, factories, csrf_token
    ):
        customer_id = factories.user("customer", "stripe-paid@example.com")
        project_id = factories.project(customer_id)
        payment = Payment(
            project_id=project_id,
            customer_id=customer_id,
            stripe_session_id="cs_paid_123",
            gateway_type="stripe",
            amount=Decimal("100.00"),
            currency="USD",
            status="Paid",
        )
        from app.extensions import db as _db
        _db.session.add(payment)
        _db.session.commit()
        login("stripe-paid@example.com", "customer")

        response = client.post(
            f"/payment/stripe/checkout/{project_id}",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["success"] is False
        assert data["message"] == "Project has already been paid."

    def test_success_creates_checkout_session(
        self, app, client, login, factories, csrf_token
    ):
        customer_id = factories.user("customer", "stripe-success@example.com")
        project_id = factories.project(
            customer_id,
            package="Basic Website",
            currency="USD",
            status="Payment Pending",
        )
        login("stripe-success@example.com", "customer")

        fake_session = self._make_stripe_session()

        with patch(
            "app.blueprints.payments.stripe.stripe.checkout.Session.create",
            return_value=fake_session,
        ):
            response = client.post(
                f"/payment/stripe/checkout/{project_id}",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert data["session_id"] == "cs_test_123"
        assert data["currency"] == "USD"
        assert data["url"] == fake_session.url

        with app.app_context():
            payment = Payment.query.filter_by(
                stripe_session_id="cs_test_123"
            ).first()
            assert payment is not None
            assert payment.status == "Created"
            assert payment.gateway_type == "stripe"
            assert payment.currency == "USD"

    def test_unsupported_currency_rejected(
        self, app, client, login, factories, csrf_token
    ):
        customer_id = factories.user("customer", "stripe-currency@example.com")
        project_id = factories.project(
            customer_id,
            package="Basic Website",
            currency="CAD",
            status="Payment Pending",
        )
        login("stripe-currency@example.com", "customer")

        response = client.post(
            f"/payment/stripe/checkout/{project_id}",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["success"] is False

    def test_reuses_existing_created_session(
        self, app, client, login, factories, csrf_token
    ):
        customer_id = factories.user("customer", "stripe-reuse@example.com")
        project_id = factories.project(
            customer_id,
            package="Basic Website",
            currency="USD",
            status="Payment Pending",
        )
        payment = Payment(
            project_id=project_id,
            customer_id=customer_id,
            stripe_session_id="cs_existing_123",
            gateway_type="stripe",
            amount=Decimal("349.50"),
            currency="USD",
            status="Created",
        )
        from app.extensions import db as _db
        _db.session.add(payment)
        _db.session.commit()
        login("stripe-reuse@example.com", "customer")

        with patch(
            "app.blueprints.payments.stripe.stripe.checkout.Session.create",
        ) as mock_create:
            response = client.post(
                f"/payment/stripe/checkout/{project_id}",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert data["session_id"] == "cs_existing_123"
        mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# Webhook handler tests
# ---------------------------------------------------------------------------

class TestStripeWebhook:
    def _make_stripe_event(
        self, event_id="evt_test_123", event_type="checkout.session.completed",
        project_id=1, amount_total=34950, currency="usd",
        payment_status="paid",
    ):
        return {
            "id": event_id,
            "type": event_type,
            "data": {
                "object": {
                    "id": "cs_test_123",
                    "metadata": {
                        "project_id": str(project_id),
                    },
                    "amount_total": amount_total,
                    "currency": currency,
                    "payment_status": payment_status,
                }
            },
        }

    def test_invalid_signature_rejected(self, app, client):
        response = client.post(
            "/payment/stripe/webhook",
            data="{}",
            content_type="application/json",
            headers={"Stripe-Signature": "invalid_sig"},
        )
        assert response.status_code == 400

    def test_missing_signature_rejected(self, app, client):
        response = client.post(
            "/payment/stripe/webhook",
            data="{}",
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_duplicate_event_ignored(
        self, app, client, factories
    ):
        customer_id = factories.user("customer", "webhook-dup@example.com")
        project_id = factories.project(customer_id, status="Payment Pending")

        from app.extensions import db as _db
        event = StripeWebhookEvent(
            event_id="evt_duplicate",
            event_type="checkout.session.completed",
            project_id=project_id,
        )
        _db.session.add(event)
        _db.session.commit()

        with patch(
            "app.blueprints.payments.stripe.stripe.Webhook.construct_event",
            return_value=self._make_stripe_event(event_id="evt_duplicate"),
        ):
            response = client.post(
                "/payment/stripe/webhook",
                data="{}",
                content_type="application/json",
                headers={"Stripe-Signature": "test_sig"},
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "ignored"
        assert data["reason"] == "duplicate_event"

    def test_webhook_marks_project_pending_assignment(
        self, app, client, factories
    ):
        customer_id = factories.user("customer", "webhook-paid@example.com")
        project_id = factories.project(
            customer_id,
            package="Basic Website",
            currency="USD",
            status="Payment Pending",
        )

        event = self._make_stripe_event(
            event_id="evt_paid_123",
            project_id=project_id,
            amount_total=34950,
            currency="usd",
            payment_status="paid",
        )

        with patch(
            "app.blueprints.payments.stripe.stripe.Webhook.construct_event",
            return_value=event,
        ), patch(
            "app.blueprints.payments.stripe.STRIPE_WEBHOOK_SECRET",
            "whsec_test_secret",
        ):
            response = client.post(
                "/payment/stripe/webhook",
                data=json.dumps(event),
                content_type="application/json",
                headers={"Stripe-Signature": "test_sig"},
            )

        assert response.status_code == 200

        with app.app_context():
            from app.models import Project as P
            project = P.query.get(project_id)
            # Confirms 50% deposit moves the project to pending assignment
            assert project.status == "Pending Assignment"

            payment = Payment.query.filter_by(
                stripe_session_id="cs_test_123"
            ).first()
            assert payment is not None
            assert payment.status == "Paid"

    def test_webhook_non_payable_state_ignored(
        self, app, client, factories
    ):
        customer_id = factories.user("customer", "webhook-nonpay@example.com")
        project_id = factories.project(customer_id, status="Assigned")

        event = self._make_stripe_event(
            event_id="evt_nonpay",
            project_id=project_id,
            payment_status="paid",
        )

        with patch(
            "app.blueprints.payments.stripe.stripe.Webhook.construct_event",
            return_value=event,
        ), patch(
            "app.blueprints.payments.stripe.STRIPE_WEBHOOK_SECRET",
            "whsec_test_secret",
        ):
            response = client.post(
                "/payment/stripe/webhook",
                data=json.dumps(event),
                content_type="application/json",
                headers={"Stripe-Signature": "test_sig"},
            )

        assert response.status_code == 200

        with app.app_context():
            from app.models import Project as P
            project = P.query.get(project_id)
            assert project.status == "Pending Assignment"

            assert StripeWebhookEvent.is_processed("evt_nonpay") is True