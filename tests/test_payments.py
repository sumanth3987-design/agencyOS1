"""Payment tests: order creation and payment verification.

Razorpay client calls are mocked; no real payments are made.
"""
import json
import pytest
from unittest.mock import patch, MagicMock

import razorpay

from app.models import Project, Payment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_razorpay_order(order_id="order_test_123", amount=499900,
                         currency="INR", status="created"):
    return {
        "id": order_id,
        "amount": amount,
        "currency": currency,
        "status": status,
        "receipt": "agencyos_project_1",
    }


def _make_razorpay_payment(payment_id="pay_test_456", order_id="order_test_123",
                           amount=499900, currency="INR", status="captured"):
    return {
        "id": payment_id,
        "order_id": order_id,
        "amount": amount,
        "currency": currency,
        "status": status,
    }


# ---------------------------------------------------------------------------
# TestCreateOrder
# ---------------------------------------------------------------------------

class TestCreateOrder:
    """Test payment order creation."""

    def test_anonymous_redirected(self, app, client, csrf_token):
        """Anonymous users must be redirected to login for order creation."""
        response = client.post(
            "/payment/create-order/1", data={"csrf_token": csrf_token}, follow_redirects=False
        )
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_non_customer_forbidden(self, app, client, login, factories, csrf_token):
        """A professional cannot create a payment order (403)."""
        factories.user("professional", "pro@example.com")
        login("pro@example.com", "professional")

        response = client.post(
            "/payment/create-order/1", data={"csrf_token": csrf_token}, follow_redirects=False
        )
        assert response.status_code == 403

    def test_foreign_project_not_found(self, app, client, login, factories, csrf_token):
        """A customer cannot create an order for another customer's project (404)."""
        owner_id = factories.user("customer", "owner@example.com")
        project_id = factories.project(owner_id)

        factories.user("customer", "actor@example.com")
        login("actor@example.com", "customer")

        response = client.post(
            f"/payment/create-order/{project_id}",
            data={"csrf_token": csrf_token}, follow_redirects=False,
        )
        assert response.status_code == 404
        with app.app_context():
            assert Payment.query.count() == 0

    def test_fully_paid_project_rejected(self, app, client, login, factories, csrf_token):
        """A project that already has 3 Paid payments cannot create a new order."""
        customer_id = factories.user("customer", "paid@example.com")
        project_id = factories.project(customer_id)
        # Mock 3 completed milestone payments
        factories.payment(project_id, customer_id, razorpay_order_id="o1", status="Paid")
        factories.payment(project_id, customer_id, razorpay_order_id="o2", status="Paid")
        factories.payment(project_id, customer_id, razorpay_order_id="o3", status="Paid")
        login("paid@example.com", "customer")

        response = client.post(
            f"/payment/create-order/{project_id}",
            data={"csrf_token": csrf_token}, follow_redirects=False,
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["success"] is False
        assert data["message"] == "All milestone payments for this project have already been completed."

    def test_payable_assigned_project_succeeds(
        self, app, client, login, factories, csrf_token
    ):
        """A project already assigned is payable for the next milestone."""
        customer_id = factories.user("customer", "assigned@example.com")
        project_id = factories.project(customer_id, status="Assigned")
        login("assigned@example.com", "customer")

        fake_order = _make_razorpay_order(order_id="order_assigned")

        with patch(
            "app.blueprints.payments.razorpay_client.order.create",
            return_value=fake_order,
        ):
            response = client.post(
                f"/payment/create-order/{project_id}",
                data={"csrf_token": csrf_token}, follow_redirects=False,
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert data["order_id"] == "order_assigned"

    def test_payable_in_progress_project_succeeds(
        self, app, client, login, factories, csrf_token
    ):
        """A project in progress is payable for the next milestone."""
        customer_id = factories.user("customer", "inprogress@example.com")
        project_id = factories.project(customer_id, status="In Progress")
        login("inprogress@example.com", "customer")

        fake_order = _make_razorpay_order(order_id="order_inprogress")

        with patch(
            "app.blueprints.payments.razorpay_client.order.create",
            return_value=fake_order,
        ):
            response = client.post(
                f"/payment/create-order/{project_id}",
                data={"csrf_token": csrf_token}, follow_redirects=False,
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert data["order_id"] == "order_inprogress"

    def test_non_payable_completed_project_rejected(
        self, app, client, login, factories, csrf_token
    ):
        """A completed project is not payable."""
        customer_id = factories.user("customer", "completed@example.com")
        project_id = factories.project(customer_id, status="Completed")
        login("completed@example.com", "customer")

        with patch(
            "app.blueprints.payments.razorpay_client.order.create",
        ) as mock_create:
            response = client.post(
                f"/payment/create-order/{project_id}",
                data={"csrf_token": csrf_token}, follow_redirects=False,
            )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["success"] is False
        assert data["message"] == "Project is completed and no longer payable."
        mock_create.assert_not_called()

    def test_payable_pending_assignment_creates_order(
        self, app, client, login, factories, csrf_token
    ):
        """A Pending Assignment project can still create a payment order."""
        customer_id = factories.user("customer", "pendingassignment@example.com")
        project_id = factories.project(customer_id, status="Pending Assignment")
        login("pendingassignment@example.com", "customer")

        fake_order = _make_razorpay_order(order_id="order_pending_assignment")

        with patch(
            "app.blueprints.payments.razorpay_client.order.create",
            return_value=fake_order,
        ):
            response = client.post(
                f"/payment/create-order/{project_id}",
                data={"csrf_token": csrf_token}, follow_redirects=False,
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert data["order_id"] == fake_order["id"]

    def test_payable_pending_assignment_reuses_open_order(
        self, app, client, login, factories, csrf_token
    ):
        """A Pending Assignment project reuses an existing open order."""
        customer_id = factories.user("customer", "reuse@example.com")
        project_id = factories.project(customer_id, status="Pending Assignment")
        factories.payment(project_id, customer_id, status="Created")
        login("reuse@example.com", "customer")

        with patch(
            "app.blueprints.payments.razorpay_client.order.create",
        ) as mock_create:
            response = client.post(
                f"/payment/create-order/{project_id}",
                data={"csrf_token": csrf_token}, follow_redirects=False,
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert data["order_id"] == "order_test_1"
        mock_create.assert_not_called()

    def test_success_creates_created_payment_row(self, app, client, login, factories, csrf_token):
        """Successful order creation creates a Payment row with status 'Created'."""
        customer_id = factories.user("customer", "success@example.com")
        project_id = factories.project(customer_id)
        login("success@example.com", "customer")

        fake_order = _make_razorpay_order()

        with patch(
            "app.blueprints.payments.razorpay_client.order.create",
            return_value=fake_order,
        ):
            response = client.post(
                f"/payment/create-order/{project_id}",
                data={"csrf_token": csrf_token}, follow_redirects=False,
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert data["order_id"] == fake_order["id"]

        with app.app_context():
            payment = Payment.query.filter_by(project_id=project_id).first()
            assert payment is not None
            assert payment.status == "Created"
            assert payment.razorpay_order_id == fake_order["id"]
            assert payment.amount == 4999
            assert payment.currency == "INR"


    def test_service_unavailable_returns_503(self, app, client, login, factories, csrf_token, monkeypatch):
        """Order creation returns 503 when Razorpay client is unavailable."""
        customer_id = factories.user("customer", "svcunavail@example.com")
        project_id = factories.project(customer_id)
        login("svcunavail@example.com", "customer")

        import app.blueprints.payments as payments_mod

        monkeypatch.setattr(payments_mod, "razorpay_client", None)

        response = client.post(
            f"/payment/create-order/{project_id}",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# TestVerifyPayment
# ---------------------------------------------------------------------------

class TestVerifyPayment:
    """Test payment verification."""

    def test_anonymous_redirected(self, app, client, csrf_token):
        """Anonymous users must be redirected to login for verification."""
        response = client.post(
            "/payment/verify",
            data=json.dumps({}),
            content_type="application/json",
            headers={"X-CSRFToken": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_non_customer_forbidden(self, app, client, login, factories, csrf_token):
        """A professional cannot verify a payment (403)."""
        factories.user("professional", "pro2@example.com")
        login("pro2@example.com", "professional")

        response = client.post(
            "/payment/verify",
            data=json.dumps({
                "razorpay_order_id": "order_x",
                "razorpay_payment_id": "pay_x",
                "razorpay_signature": "sig_x",
            }),
            content_type="application/json",
            headers={"X-CSRFToken": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 403

    def test_missing_fields_rejected(self, app, client, login, factories, csrf_token):
        """Missing verification fields return 400."""
        factories.user("customer", "missing@example.com")
        login("missing@example.com", "customer")

        response = client.post(
            "/payment/verify",
            data=json.dumps({}),
            content_type="application/json",
            headers={"X-CSRFToken": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["success"] is False

    def test_other_customers_order_returns_404(self, app, client, login, factories, csrf_token):
        """A customer cannot verify another customer's payment (404)."""
        owner_id = factories.user("customer", "owner2@example.com")
        project_id = factories.project(owner_id)
        factories.payment(project_id, owner_id, razorpay_order_id="order_other")

        factories.user("customer", "actor2@example.com")
        login("actor2@example.com", "customer")

        response = client.post(
            "/payment/verify",
            data=json.dumps({
                "razorpay_order_id": "order_other",
                "razorpay_payment_id": "pay_other",
                "razorpay_signature": "sig_other",
            }),
            content_type="application/json",
            headers={"X-CSRFToken": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 404

    def test_invalid_signature_rejected_payment_stays_created(
        self, app, client, login, factories, csrf_token
    ):
        """Invalid Razorpay signature rejects verification; payment stays 'Created'."""
        customer_id = factories.user("customer", "sigfail@example.com")
        project_id = factories.project(customer_id)
        factories.payment(project_id, customer_id, razorpay_order_id="order_sig")
        login("sigfail@example.com", "customer")

        with patch(
            "app.blueprints.payments.razorpay_client.utility."
            "verify_payment_signature",
            side_effect=razorpay.errors.SignatureVerificationError("bad signature"),
        ):
            response = client.post(
                "/payment/verify",
                data=json.dumps({
                    "razorpay_order_id": "order_sig",
                    "razorpay_payment_id": "pay_sig",
                    "razorpay_signature": "bad_sig",
                }),
                content_type="application/json",
                headers={"X-CSRFToken": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["success"] is False

        with app.app_context():
            payment = Payment.query.filter_by(razorpay_order_id="order_sig").first()
            assert payment.status == "Created"

    def test_amount_mismatch_rejected(self, app, client, login, factories, csrf_token):
        """Razorpay order amount mismatch rejects verification."""
        customer_id = factories.user("customer", "amt@example.com")
        project_id = factories.project(customer_id)
        factories.payment(project_id, customer_id, razorpay_order_id="order_amt", amount=4999)
        login("amt@example.com", "customer")

        with patch(
            "app.blueprints.payments.razorpay_client.utility."
            "verify_payment_signature",
            return_value=True,
        ), patch(
            "app.blueprints.payments.razorpay_client.order.fetch",
            return_value=_make_razorpay_order(order_id="order_amt", amount=999900),
        ), patch(
            "app.blueprints.payments.razorpay_client.payment.fetch",
            return_value=_make_razorpay_payment(order_id="order_amt", amount=999900),
        ):
            response = client.post(
                "/payment/verify",
                data=json.dumps({
                    "razorpay_order_id": "order_amt",
                    "razorpay_payment_id": "pay_amt",
                    "razorpay_signature": "sig_amt",
                }),
                content_type="application/json",
                headers={"X-CSRFToken": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["success"] is False

    def test_currency_mismatch_rejected(self, app, client, login, factories, csrf_token):
        """Non-INR currency rejects verification."""
        customer_id = factories.user("customer", "cur@example.com")
        project_id = factories.project(customer_id)
        factories.payment(project_id, customer_id, razorpay_order_id="order_cur")
        login("cur@example.com", "customer")

        with patch(
            "app.blueprints.payments.razorpay_client.utility."
            "verify_payment_signature",
            return_value=True,
        ), patch(
            "app.blueprints.payments.razorpay_client.order.fetch",
            return_value=_make_razorpay_order(order_id="order_cur", currency="USD"),
        ), patch(
            "app.blueprints.payments.razorpay_client.payment.fetch",
            return_value=_make_razorpay_payment(order_id="order_cur", currency="USD"),
        ):
            response = client.post(
                "/payment/verify",
                data=json.dumps({
                    "razorpay_order_id": "order_cur",
                    "razorpay_payment_id": "pay_cur",
                    "razorpay_signature": "sig_cur",
                }),
                content_type="application/json",
                headers={"X-CSRFToken": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 400

    def test_uncaptured_payment_rejected(self, app, client, login, factories, csrf_token):
        """An uncaptured Razorpay payment rejects verification."""
        customer_id = factories.user("customer", "uncap@example.com")
        project_id = factories.project(customer_id)
        factories.payment(project_id, customer_id, razorpay_order_id="order_uncap")
        login("uncap@example.com", "customer")

        with patch(
            "app.blueprints.payments.razorpay_client.utility."
            "verify_payment_signature",
            return_value=True,
        ), patch(
            "app.blueprints.payments.razorpay_client.order.fetch",
            return_value=_make_razorpay_order(order_id="order_uncap"),
        ), patch(
            "app.blueprints.payments.razorpay_client.payment.fetch",
            return_value=_make_razorpay_payment(order_id="order_uncap", status="authorized"),
        ):
            response = client.post(
                "/payment/verify",
                data=json.dumps({
                    "razorpay_order_id": "order_uncap",
                    "razorpay_payment_id": "pay_uncap",
                    "razorpay_signature": "sig_uncap",
                }),
                content_type="application/json",
                headers={"X-CSRFToken": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 400

    def test_payment_order_id_mismatch_rejected(self, app, client, login, factories, csrf_token):
        """Razorpay payment's order_id must match the local order ID."""
        customer_id = factories.user("customer", "mismatch@example.com")
        project_id = factories.project(customer_id)
        factories.payment(project_id, customer_id, razorpay_order_id="order_local")
        login("mismatch@example.com", "customer")

        with patch(
            "app.blueprints.payments.razorpay_client.utility."
            "verify_payment_signature",
            return_value=True,
        ), patch(
            "app.blueprints.payments.razorpay_client.order.fetch",
            return_value=_make_razorpay_order(order_id="order_local"),
        ), patch(
            "app.blueprints.payments.razorpay_client.payment.fetch",
            return_value=_make_razorpay_payment(order_id="order_remote"),
        ):
            response = client.post(
                "/payment/verify",
                data=json.dumps({
                    "razorpay_order_id": "order_local",
                    "razorpay_payment_id": "pay_mismatch",
                    "razorpay_signature": "sig_mismatch",
                }),
                content_type="application/json",
                headers={"X-CSRFToken": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 400

    def test_unknown_order_returns_404(self, app, client, login, factories, csrf_token):
        """Verifying an order with no local payment record returns 404."""
        factories.user("customer", "unknown@example.com")
        login("unknown@example.com", "customer")

        response = client.post(
            "/payment/verify",
            data=json.dumps({
                "razorpay_order_id": "order_does_not_exist",
                "razorpay_payment_id": "pay_unknown",
                "razorpay_signature": "sig_unknown",
            }),
            content_type="application/json",
            headers={"X-CSRFToken": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 404

    def test_full_success_marks_paid_and_project_in_progress(
        self, app, client, login, factories, csrf_token
    ):
        """Successful verification marks payment 'Paid' and project 'In Progress'."""
        customer_id = factories.user("customer", "fullsuccess@example.com")
        project_id = factories.project(customer_id)
        factories.payment(project_id, customer_id, razorpay_order_id="order_full")
        login("fullsuccess@example.com", "customer")

        with patch(
            "app.blueprints.payments.razorpay_client.utility."
            "verify_payment_signature",
            return_value=True,
        ), patch(
            "app.blueprints.payments.razorpay_client.order.fetch",
            return_value=_make_razorpay_order(order_id="order_full", amount=499900),
        ), patch(
            "app.blueprints.payments.razorpay_client.payment.fetch",
            return_value=_make_razorpay_payment(order_id="order_full", amount=499900, status="captured"),
        ):
            response = client.post(
                "/payment/verify",
                data=json.dumps({
                    "razorpay_order_id": "order_full",
                    "razorpay_payment_id": "pay_full",
                    "razorpay_signature": "sig_full",
                }),
                content_type="application/json",
                headers={"X-CSRFToken": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True

        with app.app_context():
            from app.models import ActivityLog

            payment = Payment.query.filter_by(razorpay_order_id="order_full").first()
            assert payment.status == "Paid"
            assert payment.razorpay_payment_id == "pay_full"
            project = Project.query.get(project_id)
            assert project.status == "In Progress"
            audit_entry = ActivityLog.query.filter_by(
                project_id=project_id,
                action="PAYMENT_RECEIVED",
            ).one()
            assert audit_entry.user_id == customer_id
            assert audit_entry.category == "success"
            assert "₹4,999.00" in audit_entry.description

    def test_gateway_fetch_failure_returns_500(
        self, app, client, login, factories, csrf_token
    ):
        """Razorpay fetch failure returns 500 without mutating state."""
        customer_id = factories.user("customer", "gwfail@example.com")
        project_id = factories.project(customer_id)
        factories.payment(project_id, customer_id, razorpay_order_id="order_gw")
        login("gwfail@example.com", "customer")

        with patch(
            "app.blueprints.payments.razorpay_client.utility."
            "verify_payment_signature",
            return_value=True,
        ), patch(
            "app.blueprints.payments.razorpay_client.order.fetch",
            side_effect=razorpay.errors.ServerError("network down"),
        ):
            response = client.post(
                "/payment/verify",
                data=json.dumps({
                    "razorpay_order_id": "order_gw",
                    "razorpay_payment_id": "pay_gw",
                    "razorpay_signature": "sig_gw",
                }),
                content_type="application/json",
                headers={"X-CSRFToken": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 500

    def test_repeated_verification_is_idempotent(
        self, app, client, login, factories, csrf_token
    ):
        """Repeated verification of an already-paid payment returns success
        without re-calling Razorpay or mutating state."""
        customer_id = factories.user("customer", "idem@example.com")
        project_id = factories.project(customer_id)
        factories.payment(project_id, customer_id, razorpay_order_id="order_idem", status="Paid")
        login("idem@example.com", "customer")

        with patch(
            "app.blueprints.payments.razorpay_client.utility."
            "verify_payment_signature",
        ) as mock_sig, patch(
            "app.blueprints.payments.razorpay_client.order.fetch",
        ) as mock_order_fetch:
            response = client.post(
                "/payment/verify",
                data=json.dumps({
                    "razorpay_order_id": "order_idem",
                    "razorpay_payment_id": "pay_idem",
                    "razorpay_signature": "sig_idem",
                }),
                content_type="application/json",
                headers={"X-CSRFToken": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        mock_sig.assert_not_called()
        mock_order_fetch.assert_not_called()

        with app.app_context():
            payment = Payment.query.filter_by(razorpay_order_id="order_idem").first()
            assert payment.status == "Paid"

    def test_service_unavailable_returns_503(self, app, client, login, factories, csrf_token, monkeypatch):
        """Verification returns 503 when Razorpay client is unavailable."""
        customer_id = factories.user("customer", "verifyunavail@example.com")
        project_id = factories.project(customer_id)
        factories.payment(project_id, customer_id, razorpay_order_id="order_verify_unavail")
        login("verifyunavail@example.com", "customer")

        import app.blueprints.payments as payments_mod

        monkeypatch.setattr(payments_mod, "razorpay_client", None)

        response = client.post(
            "/payment/verify",
            data=json.dumps({
                "razorpay_order_id": "order_verify_unavail",
                "razorpay_payment_id": "pay_verify_unavail",
                "razorpay_signature": "sig",
            }),
            content_type="application/json",
            headers={"X-CSRFToken": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 503

    def test_duplicate_razorpay_payment_id_rejected(self, app, client, login, factories, csrf_token):
        """The same Razorpay payment ID cannot be used for two local payments."""
        customer_id = factories.user("customer", "duppay@example.com")
        project_id_1 = factories.project(customer_id)
        project_id_2 = factories.project(customer_id)
        factories.payment(project_id_1, customer_id, razorpay_order_id="order_dup_first", status="Created", amount=4999)
        factories.payment(project_id_2, customer_id, razorpay_order_id="order_dup_second", status="Created", amount=4999)
        login("duppay@example.com", "customer")

        with patch(
            "app.blueprints.payments.razorpay_client.utility.verify_payment_signature",
            return_value=True,
        ), patch(
            "app.blueprints.payments.razorpay_client.order.fetch",
            return_value=_make_razorpay_order(order_id="order_dup_first", amount=499900),
        ), patch(
            "app.blueprints.payments.razorpay_client.payment.fetch",
            return_value=_make_razorpay_payment(
                payment_id="pay_dup_shared", order_id="order_dup_first", amount=499900, status="captured"
            ),
        ):
            first = client.post(
                "/payment/verify",
                data=json.dumps({
                    "razorpay_order_id": "order_dup_first",
                    "razorpay_payment_id": "pay_dup_shared",
                    "razorpay_signature": "sig_dup",
                }),
                content_type="application/json",
                headers={"X-CSRFToken": csrf_token},
                follow_redirects=False,
            )
        assert first.status_code == 200

        with patch(
            "app.blueprints.payments.razorpay_client.utility.verify_payment_signature",
            return_value=True,
        ), patch(
            "app.blueprints.payments.razorpay_client.order.fetch",
            return_value=_make_razorpay_order(order_id="order_dup_second", amount=499900),
        ), patch(
            "app.blueprints.payments.razorpay_client.payment.fetch",
            return_value=_make_razorpay_payment(
                payment_id="pay_dup_shared", order_id="order_dup_second", amount=499900, status="captured"
            ),
        ):
            second = client.post(
                "/payment/verify",
                data=json.dumps({
                    "razorpay_order_id": "order_dup_second",
                    "razorpay_payment_id": "pay_dup_shared",
                    "razorpay_signature": "sig_dup",
                }),
                content_type="application/json",
                headers={"X-CSRFToken": csrf_token},
                follow_redirects=False,
            )
        assert second.status_code == 400
        data = json.loads(second.data)
        assert data["success"] is False

    def test_duplicate_razorpay_order_id_database_rejection(
        self, app, db, client, login, factories, csrf_token
    ):
        """Database rejects insertion of duplicate razorpay_order_id."""
        customer_id = factories.user("customer", "orderdup@example.com")
        project_id = factories.project(customer_id)
        # Create first payment with unique order ID
        factories.payment(
            project_id, customer_id,
            razorpay_order_id="order_unique_1",
            status="Created", amount=4999
        )

        with app.app_context():
            # Attempt to insert a second payment with same order_id
            # This should fail at database level due to unique constraint
            from sqlalchemy.exc import IntegrityError
            dup = Payment(
                project_id=project_id,
                customer_id=customer_id,
                razorpay_order_id="order_unique_1",
                amount=4999,
                currency="INR",
                status="Created"
            )
            db.session.add(dup)
            with pytest.raises(IntegrityError):
                db.session.commit()

    def test_duplicate_razorpay_payment_id_database_rejection(
        self, app, db, factories
    ):
        """Database rejects insertion of duplicate non-NULL razorpay_payment_id."""
        customer_id = factories.user("customer", "payiddb@example.com")
        project_id = factories.project(customer_id)
        factories.payment(
            project_id, customer_id,
            razorpay_order_id="order_payid_first",
            razorpay_payment_id="pay_shared",
            status="Created", amount=4999,
        )

        with app.app_context():
            from sqlalchemy.exc import IntegrityError

            dup = Payment(
                project_id=project_id,
                customer_id=customer_id,
                razorpay_order_id="order_payid_second",
                razorpay_payment_id="pay_shared",
                amount=4999,
                currency="INR",
                status="Created",
            )
            db.session.add(dup)
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_duplicate_razorpay_payment_id_allows_nulls(
        self, app, db, factories
    ):
        """Multiple NULL razorpay_payment_id values are allowed."""
        customer_id = factories.user("customer", "payidnull@example.com")
        project_id_1 = factories.project(customer_id)
        project_id_2 = factories.project(customer_id)
        factories.payment(
            project_id_1, customer_id,
            razorpay_order_id="order_null_first",
            razorpay_payment_id=None,
            status="Created", amount=4999,
        )

        with app.app_context():
            second = Payment(
                project_id=project_id_2,
                customer_id=customer_id,
                razorpay_order_id="order_null_second",
                razorpay_payment_id=None,
                amount=4999,
                currency="INR",
                status="Created",
            )
            db.session.add(second)
            db.session.commit()
            db.session.rollback()
            assert Payment.query.filter_by(
                customer_id=customer_id
            ).count() == 2

    def test_unique_created_payment_per_project_customer_database_rejection(
        self, app, db, factories
    ):
        """Database rejects two 'Created' payments for same (project_id, customer_id)."""
        customer_id = factories.user("customer", "partialdb@example.com")
        project_id = factories.project(customer_id)
        factories.payment(
            project_id, customer_id,
            razorpay_order_id="order_created_first",
            status="Created", amount=4999,
        )

        with app.app_context():
            from sqlalchemy.exc import IntegrityError

            dup = Payment(
                project_id=project_id,
                customer_id=customer_id,
                razorpay_order_id="order_created_second",
                amount=4999,
                currency="INR",
                status="Created",
            )
            db.session.add(dup)
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_multiple_non_created_payments_allowed(
        self, app, db, factories
    ):
        """Partial index only restricts status='Created'."""
        customer_id = factories.user("customer", "multipaid@example.com")
        project_id = factories.project(customer_id)
        factories.payment(
            project_id, customer_id,
            razorpay_order_id="order_paid_first",
            status="Paid", amount=4999,
        )

        with app.app_context():
            second = Payment(
                project_id=project_id,
                customer_id=customer_id,
                razorpay_order_id="order_paid_second",
                amount=4999,
                currency="INR",
                status="Paid",
            )
            db.session.add(second)
            db.session.commit()
            db.session.rollback()
            assert Payment.query.filter_by(
                project_id=project_id, customer_id=customer_id
            ).count() == 2
import json as _json


class TestDatabaseUniquenessAndRecovery:
    """Deterministic database-level uniqueness conflict and recovery tests."""

    def test_rollback_and_session_recovery_after_failed_write(self, app, db, factories):
        """A failed write can be rolled back and the session remains usable."""
        customer_id = factories.user("customer", "rollback@example.com")
        project_id = factories.project(customer_id)
        factories.payment(
            project_id, customer_id,
            razorpay_order_id="order_rollback",
            status="Created", amount=4999,
        )

        with app.app_context():
            from sqlalchemy.exc import IntegrityError

            dup = Payment(
                project_id=project_id,
                customer_id=customer_id,
                razorpay_order_id="order_rollback",
                amount=4999,
                currency="INR",
                status="Created",
            )
            db.session.add(dup)
            with pytest.raises(IntegrityError):
                db.session.commit()

            db.session.rollback()

            # Session is recovered and queries work.
            assert Payment.query.count() >= 1

    def test_verification_uniqueness_conflict_returns_safe_message(
        self, app, client, db, login, factories, csrf_token
    ):
        """Verification hitting a duplicate payment ID returns a safe 400."""
        customer_id = factories.user("customer", "verifyconflict@example.com")
        project_id_1 = factories.project(customer_id)
        project_id_2 = factories.project(customer_id)
        factories.payment(
            project_id_1, customer_id,
            razorpay_order_id="order_conflict_first",
            status="Created", amount=4999,
        )
        factories.payment(
            project_id_2, customer_id,
            razorpay_order_id="order_conflict_second",
            status="Created", amount=4999,
        )
        # Pre-assign the shared payment id to the second row so the
        # application-level duplicate check triggers during verification.
        with app.app_context():
            second = Payment.query.filter_by(
                razorpay_order_id="order_conflict_second"
            ).first()
            second.razorpay_payment_id = "pay_conflict_shared"
            db.session.commit()

        login("verifyconflict@example.com", "customer")

        with patch(
            "app.blueprints.payments.razorpay_client.utility.verify_payment_signature",
            return_value=True,
        ), patch(
            "app.blueprints.payments.razorpay_client.order.fetch",
            return_value=_make_razorpay_order(order_id="order_conflict_first", amount=499900),
        ), patch(
            "app.blueprints.payments.razorpay_client.payment.fetch",
            return_value=_make_razorpay_payment(
                payment_id="pay_conflict_shared",
                order_id="order_conflict_first",
                amount=499900,
                status="captured",
            ),
        ):
            response = client.post(
                "/payment/verify",
                data=_json.dumps({
                    "razorpay_order_id": "order_conflict_first",
                    "razorpay_payment_id": "pay_conflict_shared",
                    "razorpay_signature": "sig_conflict",
                }),
                content_type="application/json",
                headers={"X-CSRFToken": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 400
        data = _json.loads(response.data)
        assert data["success"] is False

    def test_simulated_race_order_creation_recovers(self, app, client, login, factories, csrf_token, monkeypatch):
        """Simulated race: IntegrityError during insert is handled gracefully."""
        customer_id = factories.user("customer", "racesim@example.com")
        project_id = factories.project(customer_id)
        login("racesim@example.com", "customer")

        fake_order = _make_razorpay_order(order_id="order_racesim")

        from app.extensions import db as _db
        original_commit = _db.session.commit
        state = {"raised": False}

        def flaky_commit():
            if not state["raised"]:
                state["raised"] = True
                from sqlalchemy.exc import IntegrityError

                raise IntegrityError("race", None, None)
            return original_commit()

        monkeypatch.setattr(_db.session, "commit", flaky_commit)

        with patch(
            "app.blueprints.payments.razorpay_client.order.create",
            return_value=fake_order,
        ):
            response = client.post(
                f"/payment/create-order/{project_id}",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

        # Either recovery success or safe failure is acceptable; must not 500.
        assert response.status_code in (200, 400)


# ---------------------------------------------------------------------------
# Genuine multi-connection concurrency tests (SQLite file-backed)
# ---------------------------------------------------------------------------
import os
import tempfile
import threading
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db as _db
from app.models import User, Project, Payment
from werkzeug.security import generate_password_hash


class TestGenuineConcurrency:
    """Genuine multi-connection SQLite concurrency tests.

    These tests use a temporary file-backed SQLite database and a separate
    application/session context per thread. They do not touch the application
    database or any repository scratch files.
    """

    def _make_file_app(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        if os.path.exists(path):
            os.remove(path)
        app = create_app(
            "default",
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///"
                + path.replace(os.sep, "/"),
                "WTF_CSRF_ENABLED": True,
                "SECRET_KEY": "concurrency-test-secret-0123456789abcdef",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "SQLALCHEMY_ENGINE_OPTIONS": {
                    "connect_args": {"timeout": 30}
                },
            },
        )
        with app.app_context():
            _db.create_all()
        return app, path

    def _cleanup(self, app, path):
        try:
            with app.app_context():
                _db.session.remove()
                _db.drop_all()
                _db.engine.dispose()
        except Exception:
            pass
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(path + suffix)
            except FileNotFoundError:
                pass

    def test_concurrent_duplicate_created_payment_one_succeeds(self):
        """Two connections inserting the same open-payment key: one wins."""
        app, path = self._make_file_app()
        try:
            with app.app_context():
                user = User(
                    name="Concurrency Customer",
                    email="concurrency@example.com",
                    password=generate_password_hash("Password123"),
                    role="customer",
                )
                _db.session.add(user)
                _db.session.commit()
                project = Project(
                    customer_id=user.id,
                    service="Website",
                    package="Basic",
                    requirements="Concurrency test",
                    status="Payment Pending",
                )
                _db.session.add(project)
                _db.session.commit()
                user_id = user.id
                project_id = project.id

            results = []
            barrier = threading.Barrier(2, timeout=10)

            def worker(order_id):
                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    results.append("barrier_error")
                    return
                try:
                    with app.app_context():
                        payment = Payment(
                            project_id=project_id,
                            customer_id=user_id,
                            razorpay_order_id=order_id,
                            amount=4999,
                            currency="INR",
                            status="Created",
                        )
                        _db.session.add(payment)
                        _db.session.commit()
                        _db.session.remove()
                        results.append("ok")
                except IntegrityError:
                    try:
                        _db.session.rollback()
                        _db.session.remove()
                    except Exception:
                        pass
                    results.append("conflict")
                except Exception as exc:  # pragma: no cover - diagnostic only
                    try:
                        _db.session.rollback()
                        _db.session.remove()
                    except Exception:
                        pass
                    results.append(f"error:{type(exc).__name__}:{exc}")

            threads = [
                threading.Thread(
                    target=worker, args=("order_concurrent_first",), daemon=True
                ),
                threading.Thread(
                    target=worker, args=("order_concurrent_second",), daemon=True
                ),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

            assert all(not thread.is_alive() for thread in threads)
            assert not any(result.startswith("error:") for result in results)
            assert sorted(results) == ["conflict", "ok"]

            with app.app_context():
                count = Payment.query.filter_by(
                    project_id=project_id,
                    customer_id=user_id,
                    status="Created",
                ).count()
                assert count == 1
                _db.session.remove()
        finally:
            self._cleanup(app, path)

    def test_concurrent_claim_same_payment_one_wins(self):
        """Two connections claiming the same Created payment: one update wins."""
        app, path = self._make_file_app()
        try:
            with app.app_context():
                user = User(
                    name="Claim Customer",
                    email="claim@example.com",
                    password=generate_password_hash("Password123"),
                    role="customer",
                )
                _db.session.add(user)
                _db.session.commit()
                project = Project(
                    customer_id=user.id,
                    service="Website",
                    package="Basic",
                    requirements="Claim test",
                    status="Payment Pending",
                )
                _db.session.add(project)
                _db.session.commit()
                payment = Payment(
                    project_id=project.id,
                    customer_id=user.id,
                    razorpay_order_id="order_claim_concurrent",
                    amount=4999,
                    currency="INR",
                    status="Created",
                )
                _db.session.add(payment)
                _db.session.commit()
                payment_id = payment.id

            results = []
            barrier = threading.Barrier(2, timeout=10)

            def worker():
                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    results.append("barrier_error")
                    return
                try:
                    with app.app_context():
                        updated = Payment.query.filter_by(
                            id=payment_id, status="Created"
                        ).update(
                            {
                                "status": "Paid",
                                "razorpay_payment_id": "pay_claim_shared",
                                "paid_at": datetime.utcnow(),
                            },
                            synchronize_session=False,
                        )
                        _db.session.commit()
                        _db.session.remove()
                        results.append(updated)
                except Exception as exc:  # pragma: no cover - diagnostic only
                    try:
                        _db.session.rollback()
                        _db.session.remove()
                    except Exception:
                        pass
                    results.append(f"error:{type(exc).__name__}:{exc}")

            threads = [
                threading.Thread(target=worker, daemon=True)
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

            assert all(not thread.is_alive() for thread in threads)
            assert not any(
                isinstance(result, str) and result.startswith("error:")
                for result in results
            )
            assert sorted(results) == [0, 1]

            with app.app_context():
                final_payment = Payment.query.get(payment_id)
                assert final_payment is not None
                assert final_payment.status == "Paid"
                assert final_payment.razorpay_payment_id == "pay_claim_shared"
                _db.session.remove()
        finally:
            self._cleanup(app, path)
class TestRateLimitingPayments:

    def test_payment_order_creation_throttled(self, app, client, csrf_token):
        for _ in range(10):
            response = client.post(
                "/payment/create-order/1",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )
            assert response.status_code in (200, 302, 400, 403, 404)

        response = client.post(
            "/payment/create-order/1",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 429

    def test_payment_verification_throttled(self, app, client, csrf_token):
        for _ in range(20):
            response = client.post(
                "/payment/verify",
                data=json.dumps({
                    "razorpay_order_id": "order_rate",
                    "razorpay_payment_id": "pay_rate",
                    "razorpay_signature": "sig_rate",
                }),
                content_type="application/json",
                headers={"X-CSRFToken": csrf_token},
                follow_redirects=False,
            )
            assert response.status_code in (200, 302, 400, 403, 404)

        response = client.post(
            "/payment/verify",
            data=json.dumps({
                "razorpay_order_id": "order_rate",
                "razorpay_payment_id": "pay_rate",
                "razorpay_signature": "sig_rate",
            }),
            content_type="application/json",
            headers={"X-CSRFToken": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 429
class TestPaymentConflictRecovery:

    def test_create_order_reuses_matching_created_payment_without_razorpay(
        self, app, client, login, factories, csrf_token
    ):
        customer_id = factories.user("customer", "reuse-order@example.com")
        project_id = factories.project(customer_id)
        factories.payment(
            project_id,
            customer_id,
            razorpay_order_id="order_reuse_existing",
            status="Created",
            amount=4999,
        )
        login("reuse-order@example.com", "customer")

        with patch(
            "app.blueprints.payments.razorpay_client.order.create",
            side_effect=AssertionError("Razorpay should not be called"),
        ) as mock_create:
            response = client.post(
                f"/payment/create-order/{project_id}",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert data["order_id"] == "order_reuse_existing"
        mock_create.assert_not_called()

    def test_create_order_recovers_matching_payment_after_integrity_error(
        self, app, client, login, factories, csrf_token, monkeypatch
    ):
        customer_id = factories.user("customer", "recover-race@example.com")
        project_id = factories.project(customer_id)
        login("recover-race@example.com", "customer")

        fake_order = _make_razorpay_order(order_id="order_recover_race")

        from app.extensions import db as _db

        original_commit = _db.session.commit
        state = {"raised": False}

        def flaky_commit():
            if not state["raised"]:
                state["raised"] = True
                from sqlalchemy.exc import IntegrityError

                raise IntegrityError("simulated race", None, None)
            return original_commit()

        monkeypatch.setattr(_db.session, "commit", flaky_commit)

        with patch(
            "app.blueprints.payments.razorpay_client.order.create",
            return_value=fake_order,
        ):
            response = client.post(
                f"/payment/create-order/{project_id}",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code in (200, 400)
        assert response.status_code != 500

        with app.app_context():
            payments = Payment.query.filter_by(project_id=project_id).all()
            assert len(payments) <= 1

    def test_create_order_rejects_conflicting_razorpay_order_id(
        self, app, client, login, factories, csrf_token
    ):
        owner_id = factories.user("customer", "conflict-owner@example.com")
        owner_project_id = factories.project(owner_id)
        factories.payment(
            owner_project_id,
            owner_id,
            razorpay_order_id="order_conflict_shared",
            status="Created",
            amount=4999,
        )

        actor_id = factories.user("customer", "conflict-actor@example.com")
        actor_project_id = factories.project(actor_id)
        login("conflict-actor@example.com", "customer")

        fake_order = _make_razorpay_order(order_id="order_conflict_shared")

        with patch(
            "app.blueprints.payments.razorpay_client.order.create",
            return_value=fake_order,
        ):
            response = client.post(
                f"/payment/create-order/{actor_project_id}",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["success"] is False
        assert data["message"] == "Payment order already exists."

        with app.app_context():
            assert Payment.query.filter_by(
                razorpay_order_id="order_conflict_shared"
            ).count() == 1

    def test_create_order_logs_orphan_after_persistence_failure(
        self, app, client, login, factories, csrf_token, monkeypatch, caplog
    ):
        customer_id = factories.user("customer", "orphan-log@example.com")
        project_id = factories.project(customer_id)
        login("orphan-log@example.com", "customer")

        fake_order = _make_razorpay_order(order_id="order_orphan_log")

        from app.extensions import db as _db
        from sqlalchemy.exc import IntegrityError

        monkeypatch.setattr(
            _db.session,
            "commit",
            lambda: (_ for _ in ()).throw(
                IntegrityError("forced failure", None, None)
            ),
        )

        with patch(
            "app.blueprints.payments.razorpay_client.order.create",
            return_value=fake_order,
        ), caplog.at_level("ERROR"):
            response = client.post(
                f"/payment/create-order/{project_id}",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["success"] is False
        assert data["message"] == "Unable to create payment order."

        assert "Razorpay order could not be reconciled" in caplog.text
        orphan_record = next(
            record for record in caplog.records
            if "Razorpay order could not be reconciled" in record.getMessage()
        )
        assert getattr(orphan_record, "razorpay_order_id", None) == "order_orphan_log"
        assert getattr(orphan_record, "project_id", None) == project_id

        with app.app_context():
            assert Payment.query.filter_by(
                razorpay_order_id="order_orphan_log"
            ).count() == 0

    def test_create_order_retry_reuses_created_payment(
        self, app, client, login, factories, csrf_token
    ):
        customer_id = factories.user("customer", "retry-order@example.com")
        project_id = factories.project(customer_id)
        login("retry-order@example.com", "customer")

        fake_order = _make_razorpay_order(order_id="order_retry_once")

        with patch(
            "app.blueprints.payments.razorpay_client.order.create",
            return_value=fake_order,
        ) as mock_create:
            first_response = client.post(
                f"/payment/create-order/{project_id}",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )
            second_response = client.post(
                f"/payment/create-order/{project_id}",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

        assert first_response.status_code == 200
        assert second_response.status_code == 200
        first_data = json.loads(first_response.data)
        second_data = json.loads(second_response.data)
        assert first_data["order_id"] == "order_retry_once"
        assert second_data["order_id"] == "order_retry_once"
        assert mock_create.call_count == 1

        with app.app_context():
            assert Payment.query.filter_by(project_id=project_id).count() == 1

    def test_verify_payment_replay_does_not_transition_twice(
        self, app, client, login, factories, csrf_token
    ):
        customer_id = factories.user("customer", "verify-replay@example.com")
        project_id = factories.project(customer_id, status="Payment Pending")
        factories.payment(
            project_id,
            customer_id,
            razorpay_order_id="order_verify_replay",
            razorpay_payment_id="pay_verify_replay",
            status="Paid",
            amount=4999,
        )
        login("verify-replay@example.com", "customer")

        with patch(
            "app.blueprints.payments.razorpay_client.utility.verify_payment_signature",
            return_value=True,
        ) as mock_signature, patch(
            "app.blueprints.payments.razorpay_client.order.fetch"
        ) as mock_order_fetch, patch(
            "app.blueprints.payments.razorpay_client.payment.fetch"
        ) as mock_payment_fetch:
            response = client.post(
                "/payment/verify",
                data=json.dumps({
                    "razorpay_order_id": "order_verify_replay",
                    "razorpay_payment_id": "pay_verify_replay",
                    "razorpay_signature": "sig_verify_replay",
                }),
                content_type="application/json",
                headers={"X-CSRFToken": csrf_token},
                follow_redirects=False,
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert data["message"] == "Payment already verified."
        mock_signature.assert_not_called()
        mock_order_fetch.assert_not_called()
        mock_payment_fetch.assert_not_called()

        with app.app_context():
            from app.models import Project

            project = Project.query.get(project_id)
            assert project.status == "Payment Pending"