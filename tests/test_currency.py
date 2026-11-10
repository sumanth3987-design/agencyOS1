from decimal import Decimal
import json
from unittest.mock import patch

import pytest

from app.models import Invoice, Payment, Project
from app.utils.currency import (
    INTERNATIONAL_PACKAGE_PRICES,
    format_currency,
    get_package_price,
    to_minor_units,
)


@pytest.mark.parametrize(
    ("currency", "package", "expected"),
    [
        ("USD", "Basic Website", 699),
        ("USD", "Advanced Website", 2499),
        ("EUR", "SEO Growth", 850),
        ("GBP", "Business Website", 999),
    ],
)
def test_international_package_prices(currency, package, expected):
    assert get_package_price(package, currency, {}) == Decimal(expected)
    assert package in INTERNATIONAL_PACKAGE_PRICES[currency]


def test_currency_helpers_validate_minor_units_and_formatting():
    assert to_minor_units(Decimal("349.50")) == 34950
    assert format_currency(Decimal("2499"), "USD") == "$2,499.00"
    with pytest.raises(ValueError):
        to_minor_units(Decimal("1.001"))
    with pytest.raises(ValueError):
        get_package_price("Basic Website", "CAD", {})
    with pytest.raises(ValueError):
        get_package_price("Starter Retainer", "USD", {})
    with pytest.raises(ValueError):
        format_currency(100, "CAD")


def test_project_checkout_uses_saved_foreign_currency(
    app, client, factories, login, csrf_token
):
    customer_id = factories.customer("currency-checkout@example.com")
    project_id = factories.project(
        customer_id,
        package="Basic Website",
        currency="USD",
        status="Payment Pending",
    )
    login("currency-checkout@example.com", "customer")

    with patch(
        "app.blueprints.payments.razorpay_client.order.create",
        return_value={"id": "order_usd_123"},
    ) as create_order:
        response = client.post(
            f"/payment/create-order/{project_id}",
            json={"milestone": 1},
            headers={"X-CSRFToken": csrf_token},
        )

    assert response.status_code == 200
    payload = json.loads(response.data)
    assert payload["currency"] == "USD"
    assert payload["amount"] == 34950
    assert create_order.call_args.args[0]["currency"] == "USD"
    assert create_order.call_args.args[0]["amount"] == 34950
    payment = Payment.query.filter_by(razorpay_order_id="order_usd_123").one()
    assert payment.amount == Decimal("349.50")
    assert payment.currency == "USD"


def test_foreign_payment_verification_checks_and_records_currency(
    app, client, db, factories, login, csrf_token
):
    customer_id = factories.customer("currency-verify@example.com")
    project_id = factories.project(
        customer_id,
        package="Basic Website",
        currency="USD",
        status="Payment Pending",
    )
    payment = Payment(
        project_id=project_id,
        customer_id=customer_id,
        razorpay_order_id="order_usd_verify",
        amount=Decimal("349.50"),
        currency="USD",
        status="Created",
    )
    db.session.add(payment)
    db.session.commit()
    login("currency-verify@example.com", "customer")

    with patch(
        "app.blueprints.payments.razorpay_client.utility.verify_payment_signature",
        return_value=True,
    ), patch(
        "app.blueprints.payments.razorpay_client.order.fetch",
        return_value={
            "id": "order_usd_verify",
            "amount": 34950,
            "currency": "USD",
        },
    ), patch(
        "app.blueprints.payments.razorpay_client.payment.fetch",
        return_value={
            "order_id": "order_usd_verify",
            "amount": 34950,
            "currency": "USD",
            "status": "captured",
        },
    ):
        response = client.post(
            "/payment/verify",
            json={
                "razorpay_order_id": "order_usd_verify",
                "razorpay_payment_id": "pay_usd_verify",
                "razorpay_signature": "valid_signature",
            },
            headers={"X-CSRFToken": csrf_token},
        )

    assert response.status_code == 200
    assert payment.status == "Paid"
    invoice = Invoice.query.filter_by(project_id=project_id).one()
    assert invoice.currency == "USD"

    invoice_response = client.get(f"/project/{project_id}/invoice")
    assert invoice_response.status_code == 200
    assert b"$349.50" in invoice_response.data
    assert b"Tax estimate (18%)" in invoice_response.data
    assert b"Rule 46 of CGST Rules" not in invoice_response.data


def test_foreign_payment_verification_rejects_mismatched_currency(
    app, client, db, factories, login, csrf_token
):
    customer_id = factories.customer("currency-mismatch@example.com")
    project_id = factories.project(
        customer_id,
        package="Basic Website",
        currency="USD",
        status="Payment Pending",
    )
    db.session.add(Payment(
        project_id=project_id,
        customer_id=customer_id,
        razorpay_order_id="order_usd_mismatch",
        amount=Decimal("349.50"),
        currency="USD",
        status="Created",
    ))
    db.session.commit()
    login("currency-mismatch@example.com", "customer")

    with patch(
        "app.blueprints.payments.razorpay_client.utility.verify_payment_signature",
        return_value=True,
    ), patch(
        "app.blueprints.payments.razorpay_client.order.fetch",
        return_value={
            "id": "order_usd_mismatch",
            "amount": 34950,
            "currency": "EUR",
        },
    ), patch(
        "app.blueprints.payments.razorpay_client.payment.fetch",
        return_value={
            "order_id": "order_usd_mismatch",
            "amount": 34950,
            "currency": "EUR",
            "status": "captured",
        },
    ):
        response = client.post(
            "/payment/verify",
            json={
                "razorpay_order_id": "order_usd_mismatch",
                "razorpay_payment_id": "pay_usd_mismatch",
                "razorpay_signature": "valid_signature",
            },
            headers={"X-CSRFToken": csrf_token},
        )

    assert response.status_code == 400
    assert Payment.query.filter_by(
        razorpay_order_id="order_usd_mismatch"
    ).one().status == "Created"


def test_payment_page_renders_foreign_currency(
    client, factories, login
):
    customer_id = factories.customer("currency-page@example.com")
    project_id = factories.project(
        customer_id,
        package="Basic Website",
        currency="USD",
        status="Payment Pending",
    )
    login("currency-page@example.com", "customer")

    response = client.get(f"/payment/{project_id}")

    assert response.status_code == 200
    assert b"$349.50" in response.data
    assert b"Total Payable (USD)" in response.data


def test_project_intake_persists_selected_currency(
    client, factories, login, csrf_post
):
    customer_id = factories.customer("currency-intake@example.com")
    login("currency-intake@example.com", "customer")

    response = csrf_post(
        "/start-project",
        data={
            "package": "Business Website",
            "currency": "GBP",
            "brief": "Build an international business site.",
        },
    )

    assert response.status_code == 302
    project = Project.query.filter_by(customer_id=customer_id).one()
    assert project.package == "Business Website"
    assert project.currency == "GBP"


def test_project_intake_keeps_retainer_inr_only(
    client, factories, login, csrf_post
):
    customer_id = factories.customer("currency-retainer@example.com")
    login("currency-retainer@example.com", "customer")

    response = csrf_post(
        "/start-project",
        data={
            "package": "Starter Retainer",
            "currency": "USD",
            "brief": "Monthly SEO retainer.",
        },
    )

    assert response.status_code == 302
    assert not Project.query.filter_by(customer_id=customer_id).count()


def test_international_scope_addon_uses_project_currency(
    app, client, db, factories, login, csrf_post, csrf_token
):
    customer_id = factories.customer("currency-addon@example.com")
    project_id = factories.project(
        customer_id,
        package="Business Website",
        currency="EUR",
        status="In Progress",
    )
    factories.admin("currency-addon-admin@example.com")
    login("currency-addon-admin@example.com", "admin")

    response = csrf_post(
        f"/admin/project/{project_id}/add-on",
        data={"title": "Extra integration", "amount": "89.50"},
    )

    assert response.status_code == 302
    addon = Project.query.get(project_id).add_ons[0]
    assert addon.amount == Decimal("89.50")
    assert addon.invoice.currency == "EUR"

    login("currency-addon@example.com", "customer")
    with patch(
        "app.blueprints.payments.razorpay_client.order.create",
        return_value={"id": "order_eur_addon"},
    ) as create_order:
        order_response = client.post(
            f"/payment/create-order/{project_id}",
            json={"addon_id": addon.id},
            headers={"X-CSRFToken": csrf_token},
        )

    assert order_response.status_code == 200
    assert order_response.get_json()["currency"] == "EUR"
    assert order_response.get_json()["amount"] == 8950
    assert create_order.call_args.args[0]["currency"] == "EUR"
    assert create_order.call_args.args[0]["amount"] == 8950


def test_admin_ledger_groups_amounts_by_currency(
    client, factories, login
):
    customer_id = factories.customer("currency-ledger@example.com")
    project_id = factories.project(customer_id, currency="USD")
    factories.payment(
        project_id,
        customer_id,
        amount=Decimal("349.50"),
        currency="USD",
        status="Paid",
    )
    factories.admin("currency-ledger-admin@example.com")
    login("currency-ledger-admin@example.com", "admin")

    response = client.get("/admin/transactions")

    assert response.status_code == 200
    assert b"$349.50" in response.data
    assert b"Verified totals grouped by currency" in response.data


def test_specialist_dashboard_groups_allocations_by_currency(
    client, factories, login
):
    customer_id = factories.customer("currency-work@example.com")
    professional_id = factories.professional("currency-specialist@example.com")
    project_id = factories.project(
        customer_id,
        package="Basic Website",
        currency="USD",
        status="Completed",
    )
    gbp_project_id = factories.project(
        customer_id,
        package="SEO Starter",
        currency="GBP",
        status="Pending Assignment",
    )
    
    # Needs a paid record to be visible on the new strictly-secured marketplace
    factories.payment(
        gbp_project_id,
        customer_id,
        amount=Decimal("390.00"),
        currency="GBP",
        status="Paid"
    )
    
    factories.application(project_id, professional_id, status="Assigned")
    login("currency-specialist@example.com", "professional")

    response = client.get("/professional/dashboard")

    assert response.status_code == 200
    assert b"$314.55" in response.data
    assert "£175.50".encode("utf-8") in response.data
    assert b"Payout allocations grouped by project currency" in response.data