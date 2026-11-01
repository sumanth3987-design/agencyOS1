import hashlib
import hmac
import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.models import (
    AgencySetting,
    Invoice,
    Payment,
    Project,
    RazorpaySubscription,
    RazorpaySubscriptionPlan,
    SubscriptionCharge,
    get_ist_now,
)


STARTER_RETAINER_AMOUNT = 14999


def _subscription_event(
    subscription_id="sub_starter_test",
    payment_id="pay_starter_test",
    amount=STARTER_RETAINER_AMOUNT * 100,
    status="captured",
):
    return {
        "event": "subscription.charged",
        "payload": {
            "subscription": {
                "entity": {
                    "id": subscription_id,
                    "plan_id": "plan_starter_test",
                    "status": "active",
                    "notes": {"project_id": "1", "customer_id": "1"},
                }
            },
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": amount,
                    "currency": "INR",
                    "status": status,
                    "order_id": f"order_{payment_id}",
                }
            },
        },
    }


def _post_signed_webhook(client, monkeypatch, payload, event_id="evt_starter_test"):
    secret = "test-subscription-webhook-secret"
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", secret)
    body = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-Razorpay-Signature": signature}
    if event_id:
        headers["X-Razorpay-Event-Id"] = event_id
    return client.post(
        "/subscription/webhook",
        data=body,
        content_type="application/json",
        headers=headers,
    )


def _create_local_subscription(db, project_id, customer_id):
    plan = RazorpaySubscriptionPlan(
        code="starter_seo_retainer",
        razorpay_plan_id="plan_starter_test",
        name="Starter SEO Retainer",
        amount=STARTER_RETAINER_AMOUNT,
        currency="INR",
    )
    db.session.add(plan)
    db.session.flush()
    subscription = RazorpaySubscription(
        project_id=project_id,
        customer_id=customer_id,
        plan_id=plan.id,
        razorpay_subscription_id="sub_starter_test",
        amount=STARTER_RETAINER_AMOUNT,
        currency="INR",
        status="active",
        start_at=datetime(2026, 12, 1, 9, tzinfo=ZoneInfo("Asia/Kolkata")),
    )
    db.session.add(subscription)
    db.session.commit()
    return subscription


def test_subscription_creation_is_owned_retainer_only_and_idempotent(
    client, db, factories, login, csrf_token, monkeypatch
):
    customer_id = factories.customer("subscription-client@example.com")
    project_id = factories.project(
        customer_id,
        package="Starter Retainer",
        service="SEO Retainer & Growth",
    )
    login("subscription-client@example.com", "customer")

    calls = {"plans": 0, "subscriptions": 0, "subscription_data": None}

    def create_plan(data):
        calls["plans"] += 1
        assert data["period"] == "monthly"
        assert data["interval"] == 1
        assert data["item"]["amount"] == STARTER_RETAINER_AMOUNT * 100
        return {"id": "plan_starter_created"}

    def create_subscription(data):
        calls["subscriptions"] += 1
        calls["subscription_data"] = data
        return {"id": "sub_starter_created", "status": "created"}

    import app.blueprints.payments as payments_mod

    monkeypatch.setattr(
        payments_mod,
        "razorpay_client",
        SimpleNamespace(
            plan=SimpleNamespace(create=create_plan),
            subscription=SimpleNamespace(create=create_subscription),
        ),
    )
    db.session.add(AgencySetting(
        id=1,
        agency_name="Northstar Studio",
        primary_color="#1a76c2",
    ))
    db.session.commit()

    headers = {"X-CSRFToken": csrf_token}
    response = client.post(
        f"/subscription/create/{project_id}",
        json={},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["subscription_id"] == "sub_starter_created"
    assert data["amount"] == STARTER_RETAINER_AMOUNT * 100
    assert data["agency_name"] == "Northstar Studio"
    assert data["primary_color"] == "#1a76c2"
    assert calls["plans"] == 1
    assert calls["subscriptions"] == 1
    assert calls["subscription_data"]["total_count"] == 12
    local_subscription = RazorpaySubscription.query.one()
    assert calls["subscription_data"]["notes"] == {
        "project_id": str(project_id),
        "customer_id": str(customer_id),
        "local_subscription_id": str(local_subscription.id),
    }
    scheduled = datetime.fromtimestamp(
        calls["subscription_data"]["start_at"],
        tz=ZoneInfo("Asia/Kolkata"),
    )
    assert (scheduled.day, scheduled.hour, scheduled.minute) == (1, 9, 0)
    expected_month = (get_ist_now().month % 12) + 1
    assert scheduled.month == expected_month

    repeated = client.post(
        f"/subscription/create/{project_id}",
        json={},
        headers=headers,
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["subscription_id"] == "sub_starter_created"
    assert calls["plans"] == 1
    assert calls["subscriptions"] == 1
    assert RazorpaySubscription.query.count() == 1
    assert RazorpaySubscriptionPlan.query.one().razorpay_plan_id == "plan_starter_created"


def test_subscription_creation_rejects_non_retainer_and_foreign_projects(
    client, factories, login, csrf_token, monkeypatch
):
    owner_id = factories.customer("subscription-owner@example.com")
    project_id = factories.project(owner_id, package="Basic Website")
    factories.customer("subscription-other@example.com")
    login("subscription-other@example.com", "customer")

    import app.blueprints.payments as payments_mod

    def unexpected_plan_create(_data):
        raise AssertionError("Plan creation must not run for invalid access.")

    monkeypatch.setattr(
        payments_mod,
        "razorpay_client",
        SimpleNamespace(
            plan=SimpleNamespace(create=unexpected_plan_create),
            subscription=SimpleNamespace(create=unexpected_plan_create),
        ),
    )
    headers = {"X-CSRFToken": csrf_token}

    unauthorized = client.post(
        f"/subscription/create/{project_id}",
        json={},
        headers=headers,
    )
    assert unauthorized.status_code == 404

    login("subscription-owner@example.com", "customer")
    not_retainer = client.post(
        f"/subscription/create/{project_id}",
        json={},
        headers=headers,
    )
    assert not_retainer.status_code == 400


def test_starter_retainer_cannot_use_one_time_project_checkout(
    client, factories, login, csrf_token
):
    customer_id = factories.customer("subscription-one-time@example.com")
    project_id = factories.project(
        customer_id,
        package="Starter Retainer",
    )
    login("subscription-one-time@example.com", "customer")

    checkout_page = client.get(f"/payment/{project_id}")
    assert checkout_page.status_code == 302
    assert f"/project/{project_id}" in checkout_page.headers["Location"]

    order_response = client.post(
        f"/payment/create-order/{project_id}",
        json={},
        headers={"X-CSRFToken": csrf_token},
    )
    assert order_response.status_code == 400
    assert Payment.query.filter_by(project_id=project_id).count() == 0


def test_subscription_creation_requires_csrf(
    client, factories, login
):
    customer_id = factories.customer("subscription-csrf@example.com")
    project_id = factories.project(customer_id, package="Starter Retainer")
    login("subscription-csrf@example.com", "customer")

    response = client.post(f"/subscription/create/{project_id}", json={})

    assert response.status_code == 400
    assert RazorpaySubscription.query.count() == 0


def test_subscription_api_timeout_cannot_create_duplicate_remote_subscription(
    client, db, factories, login, csrf_token, monkeypatch
):
    customer_id = factories.customer("subscription-timeout@example.com")
    project_id = factories.project(
        customer_id,
        package="Starter Retainer",
    )
    login("subscription-timeout@example.com", "customer")
    calls = {"subscriptions": 0}

    def create_plan(_data):
        return {"id": "plan_starter_timeout"}

    def create_subscription(_data):
        calls["subscriptions"] += 1
        raise TimeoutError("gateway response timed out")

    import app.blueprints.payments as payments_mod

    monkeypatch.setattr(
        payments_mod,
        "razorpay_client",
        SimpleNamespace(
            plan=SimpleNamespace(create=create_plan),
            subscription=SimpleNamespace(create=create_subscription),
        ),
    )
    headers = {"X-CSRFToken": csrf_token}

    failed = client.post(
        f"/subscription/create/{project_id}",
        json={},
        headers=headers,
    )
    assert failed.status_code == 502
    pending = RazorpaySubscription.query.one()
    assert pending.status == "initializing"
    assert pending.razorpay_subscription_id is None

    retry = client.post(
        f"/subscription/create/{project_id}",
        json={},
        headers=headers,
    )
    assert retry.status_code == 409
    assert calls["subscriptions"] == 1
    assert RazorpaySubscription.query.count() == 1


def test_project_workspace_only_shows_starter_retainer_checkout(
    client, factories, login
):
    customer_id = factories.customer("subscription-ui@example.com")
    retainer_project_id = factories.project(
        customer_id,
        package="Starter Retainer",
        service="SEO Retainer & Growth",
    )
    standard_project_id = factories.project(
        customer_id,
        package="SEO Starter",
        service="Search Engine Optimization",
    )
    login("subscription-ui@example.com", "customer")

    retainer_workspace = client.get(f"/project/{retainer_project_id}")
    standard_workspace = client.get(f"/project/{standard_project_id}")

    assert retainer_workspace.status_code == 200
    assert b"Set up / continue mandate" in retainer_workspace.data
    assert b"/subscription/create/" in retainer_workspace.data
    assert b"X-CSRFToken" in retainer_workspace.data
    assert "₹14,999".encode() in retainer_workspace.data
    assert b"No mandate has been created yet." in retainer_workspace.data
    assert b"No confirmed captured charges are recorded yet." in retainer_workspace.data
    assert standard_workspace.status_code == 200
    assert b"Set up / continue mandate" not in standard_workspace.data


def test_project_workspace_renders_saved_mandate_and_captured_charge_states(
    client, db, factories, login, monkeypatch
):
    customer_id = factories.customer("subscription-workspace-state@example.com")
    project_id = factories.project(
        customer_id,
        package="Starter Retainer",
        service="SEO Retainer & Growth",
    )
    subscription = _create_local_subscription(db, project_id, customer_id)
    subscription.status = "authenticated"
    db.session.commit()
    login("subscription-workspace-state@example.com", "customer")

    authorized_workspace = client.get(f"/project/{project_id}")
    assert authorized_workspace.status_code == 200
    assert b"Your recurring mandate is authorized and awaiting activation." in authorized_workspace.data
    assert b"Authorization does not confirm a charge." in authorized_workspace.data
    assert b'id="starter-retainer-checkout"' in authorized_workspace.data
    assert b"disabled>" in authorized_workspace.data
    assert b"No confirmed captured charges are recorded yet." in authorized_workspace.data

    charged = _post_signed_webhook(
        client,
        monkeypatch,
        _subscription_event(),
        event_id="evt_workspace_charge_state",
    )
    assert charged.status_code == 200

    charged_workspace = client.get(f"/project/{project_id}")
    assert charged_workspace.status_code == 200
    assert b"Captured" in charged_workspace.data
    assert "₹14,999".encode() in charged_workspace.data
    assert b"No confirmed captured charges are recorded yet." not in charged_workspace.data


def test_subscription_charge_creates_payment_and_invoice_once(
    client, db, factories, login, monkeypatch
):
    customer_id = factories.customer("subscription-charge@example.com")
    project_id = factories.project(
        customer_id,
        package="Starter Retainer",
        service="SEO Retainer & Growth",
    )
    subscription = _create_local_subscription(db, project_id, customer_id)

    response = _post_signed_webhook(
        client,
        monkeypatch,
        _subscription_event(),
    )

    assert response.status_code == 200
    result = response.get_json()
    payment = Payment.query.filter_by(razorpay_payment_id="pay_starter_test").one()
    invoice = Invoice.query.filter_by(id=result["invoice_id"]).one()
    project = db.session.get(Project, project_id)
    charge = SubscriptionCharge.query.filter_by(
        razorpay_payment_id="pay_starter_test"
    ).one()
    assert project is not None
    assert project.invoice_records == [invoice]
    assert payment.project_id == project_id
    assert payment.customer_id == customer_id
    assert payment.amount == STARTER_RETAINER_AMOUNT
    assert payment.currency == "INR"
    assert payment.status == "Paid"
    assert payment.razorpay_order_id == "order_pay_starter_test"
    assert payment.gateway_payment_id == "pay_starter_test"
    assert invoice.gross_amount == STARTER_RETAINER_AMOUNT
    assert invoice.status == "Paid"
    assert invoice.invoice_number.startswith(f"RET-{project_id:04d}-")
    assert charge.subscription_id == subscription.id
    assert charge.payment_id == payment.id
    assert charge.invoice_id == invoice.id

    duplicate_event = _post_signed_webhook(
        client,
        monkeypatch,
        _subscription_event(),
        event_id="evt_starter_redelivery",
    )
    assert duplicate_event.status_code == 200
    assert duplicate_event.get_json()["status"] == "duplicate"
    assert Payment.query.filter_by(razorpay_payment_id="pay_starter_test").count() == 1
    assert Invoice.query.filter_by(invoice_number=invoice.invoice_number).count() == 1
    assert SubscriptionCharge.query.count() == 1


def test_subscription_authenticated_webhook_updates_status_without_invoicing(
    client, db, factories, login, csrf_token, monkeypatch
):
    customer_id = factories.customer("subscription-authenticated@example.com")
    project_id = factories.project(
        customer_id,
        package="Starter Retainer",
    )
    subscription = _create_local_subscription(db, project_id, customer_id)
    subscription.status = "created"
    db.session.commit()
    payload = {
        "event": "subscription.authenticated",
        "payload": {
            "subscription": {
                "entity": {
                    "id": subscription.razorpay_subscription_id,
                    "plan_id": subscription.plan.razorpay_plan_id,
                    "status": "authenticated",
                }
            }
        },
    }

    response = _post_signed_webhook(
        client,
        monkeypatch,
        payload,
        event_id="evt_subscription_authenticated",
    )

    assert response.status_code == 200
    assert RazorpaySubscription.query.one().status == "authenticated"
    assert Payment.query.count() == 0
    assert Invoice.query.count() == 0
    assert SubscriptionCharge.query.count() == 0

    login("subscription-authenticated@example.com", "customer")
    checkout_response = client.post(
        f"/subscription/create/{project_id}",
        json={},
        headers={"X-CSRFToken": csrf_token},
    )
    assert checkout_response.status_code == 200
    assert checkout_response.get_json()["status"] == "authenticated"


def test_subscription_webhook_rejects_invalid_signature_and_amount(
    client, db, factories, monkeypatch
):
    customer_id = factories.customer("subscription-invalid-charge@example.com")
    project_id = factories.project(customer_id, package="Starter Retainer")
    _create_local_subscription(db, project_id, customer_id)
    monkeypatch.setenv(
        "RAZORPAY_WEBHOOK_SECRET",
        "test-subscription-webhook-secret",
    )

    invalid_signature = client.post(
        "/subscription/webhook",
        data=json.dumps(_subscription_event()).encode(),
        content_type="application/json",
        headers={"X-Razorpay-Signature": "invalid"},
    )
    assert invalid_signature.status_code == 403

    mismatch = _post_signed_webhook(
        client,
        monkeypatch,
        _subscription_event(amount=1_499_899),
        event_id="evt_subscription_amount_mismatch",
    )
    assert mismatch.status_code == 400
    assert Payment.query.count() == 0
    assert Invoice.query.count() == 0
    assert SubscriptionCharge.query.count() == 0


def test_subscription_invoice_list_links_to_the_selected_renewal_invoice(
    client, db, factories, login, monkeypatch
):
    customer_id = factories.customer("subscription-invoice-view@example.com")
    project_id = factories.project(
        customer_id,
        package="Starter Retainer",
        service="SEO Retainer & Growth",
    )
    _create_local_subscription(db, project_id, customer_id)
    _post_signed_webhook(client, monkeypatch, _subscription_event())
    invoice = Invoice.query.one()
    login("subscription-invoice-view@example.com", "customer")

    invoices_page = client.get("/invoices")
    assert (
        f"/project/{project_id}/invoice?invoice_id={invoice.id}".encode()
        in invoices_page.data
    )
    rendered_invoice = client.get(
        f"/project/{project_id}/invoice?invoice_id={invoice.id}"
    )
    assert rendered_invoice.status_code == 200
    assert invoice.invoice_number.encode() in rendered_invoice.data
    assert b"Monthly SEO Retainer Renewal" in rendered_invoice.data
    assert b"14999" in rendered_invoice.data
