import hashlib
import hmac
import json
from unittest.mock import patch

from app.models import Invoice, Payment, Project, ProjectAddOn


def _create_addon(db, project_id, customer_id, amount=12000):
    invoice = Invoice(
        invoice_number=f"ADDON-TEST-{project_id}-{amount}",
        project_id=project_id,
        customer_id=customer_id,
        gross_amount=amount,
        talent_payout=0,
        agency_profit=amount - round(amount - amount / 1.18),
        tax_amount=round(amount - amount / 1.18),
        status="Pending",
        payout_status="Pending Approval",
    )
    db.session.add(invoice)
    db.session.flush()
    addon = ProjectAddOn(
        project_id=project_id,
        invoice_id=invoice.id,
        title="Extra checkout integration",
        amount=amount,
    )
    db.session.add(addon)
    db.session.commit()
    return addon.id, invoice.id


def test_admin_issues_linked_scope_addon_invoice(
    client, db, factories, login, csrf_post
):
    customer_id = factories.customer("addon-client@example.com")
    project_id = factories.project(customer_id, status="In Progress")
    factories.admin("addon-admin@example.com")
    login("addon-admin@example.com", "admin")

    response = csrf_post(
        f"/admin/project/{project_id}/add-on",
        data={"title": "Extra checkout integration", "amount": "12000"},
    )

    assert response.status_code == 302
    addon = ProjectAddOn.query.filter_by(project_id=project_id).one()
    assert addon.title == "Extra checkout integration"
    assert addon.amount == 12000
    assert addon.status == "Pending Payment"
    assert addon.invoice.project_id == project_id
    assert addon.invoice.customer_id == customer_id
    assert addon.invoice.gross_amount == 12000
    assert addon.invoice.status == "Pending"
    assert addon.invoice.invoice_number.startswith(f"ADDON-{project_id}-")


def test_admin_rejects_invalid_addon_amount(client, db, factories, login, csrf_post):
    customer_id = factories.customer("addon-invalid-client@example.com")
    project_id = factories.project(customer_id, status="In Progress")
    factories.admin("addon-invalid-admin@example.com")
    login("addon-invalid-admin@example.com", "admin")

    response = csrf_post(
        f"/admin/project/{project_id}/add-on",
        data={"title": "Extra work", "amount": "0"},
    )

    assert response.status_code == 302
    assert ProjectAddOn.query.filter_by(project_id=project_id).count() == 0
    assert Invoice.query.filter_by(project_id=project_id).count() == 0


def test_non_admin_cannot_issue_addon(client, factories, login, csrf_post):
    customer_id = factories.customer("addon-not-admin@example.com")
    project_id = factories.project(customer_id, status="In Progress")
    login("addon-not-admin@example.com", "customer")

    response = csrf_post(
        f"/admin/project/{project_id}/add-on",
        data={"title": "Extra work", "amount": "12000"},
    )

    assert response.status_code == 302
    assert ProjectAddOn.query.count() == 0


def test_customer_workspace_shows_pending_and_paid_addons(
    client, db, factories, login
):
    customer_id = factories.customer("addon-workspace@example.com")
    project_id = factories.project(customer_id, status="In Progress")
    pending_id, _ = _create_addon(db, project_id, customer_id)
    paid_id, _ = _create_addon(db, project_id, customer_id, amount=6000)
    ProjectAddOn.query.filter_by(id=paid_id).one().status = "Paid"
    db.session.commit()
    login("addon-workspace@example.com", "customer")

    response = client.get(f"/project/{project_id}")

    assert response.status_code == 200
    assert b"Extra checkout integration" in response.data
    assert b"Authorize Add-On Payment" in response.data
    assert ProjectAddOn.query.filter_by(id=pending_id).one().status == "Pending Payment"


def test_addon_checkout_uses_database_amount_not_query_parameter(
    client, db, factories, login
):
    customer_id = factories.customer("addon-checkout@example.com")
    project_id = factories.project(customer_id, status="In Progress")
    addon_id, _ = _create_addon(db, project_id, customer_id, amount=12000)
    login("addon-checkout@example.com", "customer")

    response = client.get(
        f"/payment/{project_id}?addon_id={addon_id}&amount=1"
    )

    assert response.status_code == 200
    assert b"Extra checkout integration" in response.data
    assert b"12000" in response.data
    assert b">1<" not in response.data


def test_create_addon_order_uses_saved_amount_and_reuses_order(
    app, client, db, factories, login, csrf_token
):
    customer_id = factories.customer("addon-order@example.com")
    project_id = factories.project(customer_id, status="In Progress")
    addon_id, _ = _create_addon(db, project_id, customer_id, amount=12000)
    login("addon-order@example.com", "customer")

    order = {"id": "order_addon_123"}
    with patch(
        "app.blueprints.payments.razorpay_client.order.create",
        return_value=order,
    ) as create_order:
        response = client.post(
            f"/payment/create-order/{project_id}",
            json={"addon_id": addon_id},
            headers={"X-CSRFToken": csrf_token},
        )
        assert response.status_code == 200
        assert response.get_json()["amount"] == 1_200_000
        assert Payment.query.filter_by(addon_id=addon_id).count() == 1
        create_order.assert_called_once()
        assert create_order.call_args.args[0]["amount"] == 1_200_000

        duplicate_response = client.post(
            f"/payment/create-order/{project_id}",
            json={"addon_id": addon_id},
            headers={"X-CSRFToken": csrf_token},
        )

    assert duplicate_response.status_code == 200
    assert duplicate_response.get_json()["order_id"] == "order_addon_123"
    assert create_order.call_count == 1


def test_verified_addon_payment_marks_invoice_and_addon_paid_only(
    app, client, db, factories, login, csrf_token
):
    customer_id = factories.customer("addon-verify@example.com")
    project_id = factories.project(customer_id, status="Revision")
    addon_id, invoice_id = _create_addon(db, project_id, customer_id, amount=12000)
    payment = Payment(
        project_id=project_id,
        customer_id=customer_id,
        addon_id=addon_id,
        razorpay_order_id="order_addon_verify",
        amount=12000,
        status="Created",
    )
    db.session.add(payment)
    db.session.commit()
    login("addon-verify@example.com", "customer")

    with patch(
        "app.blueprints.payments.razorpay_client.utility.verify_payment_signature",
        return_value=True,
    ), patch(
        "app.blueprints.payments.razorpay_client.order.fetch",
        return_value={
            "id": "order_addon_verify",
            "amount": 1_200_000,
            "currency": "INR",
        },
    ), patch(
        "app.blueprints.payments.razorpay_client.payment.fetch",
        return_value={
            "order_id": "order_addon_verify",
            "amount": 1_200_000,
            "currency": "INR",
            "status": "captured",
        },
    ):
        response = client.post(
            "/payment/verify",
            json={
                "razorpay_order_id": "order_addon_verify",
                "razorpay_payment_id": "pay_addon_verify",
                "razorpay_signature": "valid_signature",
            },
            headers={"X-CSRFToken": csrf_token},
        )

    assert response.status_code == 200
    assert response.get_json()["addon_id"] == addon_id
    assert ProjectAddOn.query.filter_by(id=addon_id).one().status == "Paid"
    assert Invoice.query.filter_by(id=invoice_id).one().status == "Paid"
    assert Payment.query.filter_by(id=payment.id).one().status == "Paid"
    assert Project.query.filter_by(id=project_id).one().status == "Revision"


def test_addon_verification_rejects_mismatched_gateway_amount(
    app, client, db, factories, login, csrf_token
):
    customer_id = factories.customer("addon-mismatch@example.com")
    project_id = factories.project(customer_id, status="In Progress")
    addon_id, invoice_id = _create_addon(db, project_id, customer_id)
    payment = Payment(
        project_id=project_id,
        customer_id=customer_id,
        addon_id=addon_id,
        razorpay_order_id="order_addon_mismatch",
        amount=12000,
        status="Created",
    )
    db.session.add(payment)
    db.session.commit()
    login("addon-mismatch@example.com", "customer")

    with patch(
        "app.blueprints.payments.razorpay_client.utility.verify_payment_signature",
        return_value=True,
    ), patch(
        "app.blueprints.payments.razorpay_client.order.fetch",
        return_value={
            "id": "order_addon_mismatch",
            "amount": 1_199_999,
            "currency": "INR",
        },
    ), patch(
        "app.blueprints.payments.razorpay_client.payment.fetch",
        return_value={
            "order_id": "order_addon_mismatch",
            "amount": 1_199_999,
            "currency": "INR",
            "status": "captured",
        },
    ):
        response = client.post(
            "/payment/verify",
            json={
                "razorpay_order_id": "order_addon_mismatch",
                "razorpay_payment_id": "pay_addon_mismatch",
                "razorpay_signature": "valid_signature",
            },
            headers={"X-CSRFToken": csrf_token},
        )

    assert response.status_code == 400
    assert Payment.query.filter_by(id=payment.id).one().status == "Created"
    assert ProjectAddOn.query.filter_by(id=addon_id).one().status == "Pending Payment"
    assert Invoice.query.filter_by(id=invoice_id).one().status == "Pending"


def test_webhook_marks_linked_addon_paid(app, client, db, factories, monkeypatch):
    customer_id = factories.customer("addon-webhook@example.com")
    project_id = factories.project(customer_id, status="In Progress")
    addon_id, invoice_id = _create_addon(db, project_id, customer_id)
    payment = Payment(
        project_id=project_id,
        customer_id=customer_id,
        addon_id=addon_id,
        razorpay_order_id="order_addon_webhook",
        amount=12000,
        status="Created",
    )
    db.session.add(payment)
    db.session.commit()
    secret = "test-webhook-secret"
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", secret)
    payload = json.dumps({
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_addon_webhook",
                    "order_id": "order_addon_webhook",
                    "amount": 1_200_000,
                    "currency": "INR",
                }
            }
        },
    }).encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhook",
        data=payload,
        content_type="application/json",
        headers={"X-Razorpay-Signature": signature},
    )

    assert response.status_code == 200
    assert ProjectAddOn.query.filter_by(id=addon_id).one().status == "Paid"
    assert Invoice.query.filter_by(id=invoice_id).one().status == "Paid"


def test_webhook_rejects_addon_amount_mismatch(
    app, client, db, factories, monkeypatch
):
    customer_id = factories.customer("addon-webhook-mismatch@example.com")
    project_id = factories.project(customer_id, status="In Progress")
    addon_id, invoice_id = _create_addon(db, project_id, customer_id)
    payment = Payment(
        project_id=project_id,
        customer_id=customer_id,
        addon_id=addon_id,
        razorpay_order_id="order_addon_webhook_mismatch",
        amount=12000,
        status="Created",
    )
    db.session.add(payment)
    db.session.commit()
    secret = "test-webhook-secret"
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", secret)
    payload = json.dumps({
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_addon_webhook_mismatch",
                    "order_id": "order_addon_webhook_mismatch",
                    "amount": 1_199_999,
                    "currency": "INR",
                }
            }
        },
    }).encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhook",
        data=payload,
        content_type="application/json",
        headers={"X-Razorpay-Signature": signature},
    )

    assert response.status_code == 400
    assert Payment.query.filter_by(id=payment.id).one().status == "Created"
    assert ProjectAddOn.query.filter_by(id=addon_id).one().status == "Pending Payment"
    assert Invoice.query.filter_by(id=invoice_id).one().status == "Pending"
