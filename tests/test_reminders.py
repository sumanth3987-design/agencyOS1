from datetime import datetime

from app.models import Project, ProjectDelivery
from app.services.email import EmailDeliveryError
from app.services.reminders import (
    send_payment_reminder,
    send_review_reminder,
)


def _set_mail_base_url(app):
    app.config["APP_BASE_URL"] = "https://agencyos.example"
    app.config["MAIL_BACKEND"] = "console"
    app.config["TEST_EMAILS"] = []


def test_payment_reminder_sends_once_after_24_hours(app, db, factories):
    _set_mail_base_url(app)
    customer_id = factories.customer("payment-reminder@example.com")
    project_id = factories.project(
        customer_id,
        status="Payment Pending",
        created_at=datetime(2026, 1, 1, 10),
    )
    now = datetime(2026, 1, 2, 11)

    assert send_payment_reminder(project_id, now=now) is True
    assert send_payment_reminder(project_id, now=now) is False

    project = db.session.get(Project, project_id)
    assert project.payment_reminder_sent_at == now
    email = app.config["TEST_EMAILS"][0]
    assert email["to"] == "payment-reminder@example.com"
    assert "https://agencyos.example/payment/" in email["body"]


def test_payment_reminder_waits_until_24_hours(app, db, factories):
    _set_mail_base_url(app)
    customer_id = factories.customer("payment-wait@example.com")
    project_id = factories.project(
        customer_id,
        status="Payment Pending",
        created_at=datetime(2026, 1, 1, 10),
    )

    assert send_payment_reminder(
        project_id, now=datetime(2026, 1, 2, 9, 59)
    ) is False
    assert app.config["TEST_EMAILS"] == []


def test_review_reminder_targets_latest_submitted_delivery(
    app, db, factories
):
    _set_mail_base_url(app)
    customer_id = factories.customer("review-reminder@example.com")
    project_id = factories.project(customer_id, status="Submitted")
    professional_id = factories.professional("review-specialist@example.com")
    delivery_id = factories.delivery(
        project_id,
        professional_id,
        submitted_at=datetime(2026, 1, 1, 10),
    )

    assert send_review_reminder(
        project_id, now=datetime(2026, 1, 3, 10)
    ) is True

    delivery = db.session.get(ProjectDelivery, delivery_id)
    assert delivery.review_reminder_sent_at == datetime(2026, 1, 3, 10)
    email = app.config["TEST_EMAILS"][0]
    assert email["to"] == "review-reminder@example.com"
    assert "https://agencyos.example/project/" in email["body"]


def test_failed_email_does_not_mark_reminder_sent(
    app, db, factories, monkeypatch
):
    _set_mail_base_url(app)
    customer_id = factories.customer("failed-reminder@example.com")
    project_id = factories.project(
        customer_id,
        status="Payment Pending",
        created_at=datetime(2026, 1, 1, 10),
    )

    def fail_delivery(*args, **kwargs):
        raise EmailDeliveryError("SMTP unavailable")

    monkeypatch.setattr("app.services.reminders.send_email", fail_delivery)

    assert send_payment_reminder(
        project_id, now=datetime(2026, 1, 2, 11)
    ) is False
    assert db.session.get(Project, project_id).payment_reminder_sent_at is None


def test_admin_can_manually_send_reminder_before_due(
    app, client, db, factories, login, csrf_post
):
    _set_mail_base_url(app)
    customer_id = factories.customer("manual-reminder@example.com")
    project_id = factories.project(customer_id, status="Payment Pending")
    factories.admin("reminder-admin@example.com")
    login("reminder-admin@example.com", "admin")

    page = client.get("/admin/project-management")
    assert page.status_code == 200
    assert b"Remind Client" in page.data

    response = csrf_post(
        f"/admin/project/{project_id}/send-reminder",
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/project-management")
    assert len(app.config["TEST_EMAILS"]) == 1


def test_admin_can_manually_send_review_reminder(
    app, factories, login, csrf_post
):
    _set_mail_base_url(app)
    customer_id = factories.customer("manual-review@example.com")
    project_id = factories.project(customer_id, status="Submitted")
    professional_id = factories.professional("manual-review-specialist@example.com")
    factories.delivery(
        project_id,
        professional_id,
        submitted_at=datetime(2026, 1, 1, 10),
    )
    factories.admin("manual-review-admin@example.com")
    login("manual-review-admin@example.com", "admin")

    response = csrf_post(f"/admin/project/{project_id}/send-reminder")

    assert response.status_code == 302
    assert app.config["TEST_EMAILS"][0]["to"] == "manual-review@example.com"
    assert "Review requested" in app.config["TEST_EMAILS"][0]["subject"]


def test_non_admin_cannot_manually_send_reminder(
    app, factories, login, csrf_post
):
    _set_mail_base_url(app)
    customer_id = factories.customer("client-reminder@example.com")
    project_id = factories.project(customer_id, status="Payment Pending")
    login("client-reminder@example.com", "customer")

    response = csrf_post(f"/admin/project/{project_id}/send-reminder")

    assert response.status_code == 302
    assert app.config["TEST_EMAILS"] == []


def test_scheduled_reminder_command_sends_only_due_reminders(app, factories):
    _set_mail_base_url(app)
    customer_id = factories.customer("scheduled-reminder@example.com")
    factories.project(
        customer_id,
        status="Payment Pending",
        created_at=datetime(2026, 1, 1, 10),
    )
    review_customer_id = factories.customer("scheduled-review@example.com")
    review_project_id = factories.project(
        review_customer_id,
        status="Submitted",
    )
    professional_id = factories.professional("scheduled-specialist@example.com")
    factories.delivery(
        review_project_id,
        professional_id,
        submitted_at=datetime(2026, 1, 1, 10),
    )

    result = app.test_cli_runner().invoke(args=["send-reminders"])

    assert result.exit_code == 0
    assert "1 payment, 1 review, 0 failed" in result.output
    assert len(app.config["TEST_EMAILS"]) == 2
