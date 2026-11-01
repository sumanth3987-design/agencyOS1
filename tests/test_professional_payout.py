from app.models import Invoice, Notification


def _create_approved_paid_invoice(factories, db, project_status="Completed"):
    customer_id = factories.user("customer", "redeem-client@example.com")
    project_id = factories.project(customer_id, status=project_status)
    professional_id = factories.user("professional", "redeem-specialist@example.com")
    factories.application(project_id, professional_id, status="Assigned")
    invoice = Invoice(
        invoice_number=f"INV-REDEEM-{project_id}",
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
    return invoice


def test_specialist_can_request_approved_payout_once(
    client, csrf_post, db, factories, login
):
    invoice = _create_approved_paid_invoice(factories, db)
    admin_id = factories.user("admin", "redeem-admin@example.com")
    login("redeem-specialist@example.com", "professional")

    dashboard_response = client.get("/professional/dashboard")
    assert dashboard_response.status_code == 200
    assert b"Redeem to UPI" in dashboard_response.data

    response = csrf_post(
        f"/professional/invoice/{invoice.id}/redeem",
        data={},
        follow_redirects=False,
    )

    db.session.refresh(invoice)
    assert response.status_code == 302
    assert invoice.payout_status == "Requested"
    notification = Notification.query.filter_by(user_id=admin_id).one()
    assert "Project #{}".format(invoice.project_id) in notification.message
    assert notification.link == "/admin/transactions"

    repeated_response = csrf_post(
        f"/professional/invoice/{invoice.id}/redeem",
        data={},
        follow_redirects=False,
    )
    assert repeated_response.status_code == 302
    assert Notification.query.filter_by(user_id=admin_id).count() == 1
    dashboard_response = client.get("/professional/dashboard")
    assert b"Redemption Processing" in dashboard_response.data


def test_payout_cannot_be_requested_before_client_approval(
    client, csrf_post, db, factories, login
):
    invoice = _create_approved_paid_invoice(
        factories, db, project_status="Submitted"
    )
    login("redeem-specialist@example.com", "professional")

    response = csrf_post(
        f"/professional/invoice/{invoice.id}/redeem",
        data={},
        follow_redirects=False,
    )

    db.session.refresh(invoice)
    assert response.status_code == 302
    assert invoice.payout_status == "Pending Approval"
    assert Notification.query.count() == 0


def test_another_specialist_cannot_redeem_invoice(
    client, csrf_post, db, factories, login
):
    invoice = _create_approved_paid_invoice(factories, db)
    factories.user("professional", "other-specialist@example.com")
    login("other-specialist@example.com", "professional")

    csrf_post(
        f"/professional/invoice/{invoice.id}/redeem",
        data={},
        follow_redirects=False,
    )

    db.session.refresh(invoice)
    assert invoice.payout_status == "Pending Approval"
    assert Notification.query.count() == 0
