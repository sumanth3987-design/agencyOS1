from app.models import Invoice


def _create_paid_project_invoice(factories, db):
    customer_id = factories.user("customer", "payout-client@example.com")
    project_id = factories.project(customer_id)
    professional_id = factories.user("professional", "payout-specialist@example.com")
    factories.application(project_id, professional_id, status="Assigned")
    invoice = Invoice(
        invoice_number=f"INV-PAYOUT-{project_id}",
        project_id=project_id,
        customer_id=customer_id,
        gross_amount=100000,
        talent_payout=45000,
        agency_profit=55000,
        tax_amount=0,
        status="Paid",
    )
    db.session.add(invoice)
    db.session.commit()
    factories.payment(
        project_id,
        customer_id,
        razorpay_order_id=f"order-payout-{project_id}",
        amount=100000,
        status="Paid",
    )
    return invoice


def test_admin_can_release_paid_payout(
    client, csrf_post, db, factories, login
):
    invoice = _create_paid_project_invoice(factories, db)
    factories.user("admin", "payout-admin@example.com")
    login("payout-admin@example.com", "admin")

    ledger_response = client.get("/admin/transactions")
    assert ledger_response.status_code == 200
    assert b"Talent Payout (45%)" in ledger_response.data
    assert b"Release" in ledger_response.data

    response = csrf_post(
        f"/admin/invoice/{invoice.id}/release-payout",
        data={"utr_number": "UPI-REFERENCE-123"},
        follow_redirects=False,
    )

    db.session.refresh(invoice)
    assert response.status_code == 302
    assert invoice.payout_status == "Settled"
    assert invoice.payout_utr == "UPI-REFERENCE-123"
    assert invoice.payout_paid_at is not None


def test_unpaid_invoice_cannot_be_released(
    client, csrf_post, db, factories, login
):
    invoice = _create_paid_project_invoice(factories, db)
    invoice.status = "Created"
    db.session.commit()
    factories.user("admin", "payout-unpaid-admin@example.com")
    login("payout-unpaid-admin@example.com", "admin")

    response = csrf_post(
        f"/admin/invoice/{invoice.id}/release-payout",
        data={"utr_number": "UPI-REFERENCE-123"},
        follow_redirects=False,
    )

    db.session.refresh(invoice)
    assert response.status_code == 302
    assert invoice.payout_status == "Pending Approval"
    assert invoice.payout_utr is None
    assert invoice.payout_paid_at is None
