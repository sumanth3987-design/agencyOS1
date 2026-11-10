"""Unit & Integration Tests for AgencyOS Enterprise SaaS Features."""

from decimal import Decimal
import json
import pytest

from app.extensions import db
from app.models import (
    ContractSignature,
    CustomerProfile,
    Milestone,
    Payment,
    ProfessionalProfile,
    Project,
    ProjectDelivery,
    ProjectTimeLog,
    User,
    get_ist_now,
)
from app.services.payouts import calculate_meritocracy_rate


def test_contract_e_signature_flow(app):
    """Verify that ContractSignature models store SHA-256 hashes and canvas data."""
    with app.app_context():
        customer = User(name="Enterprise Client", email="enterprise@client.com", role="customer")
        customer.set_password("SecurePassword123!")
        db.session.add(customer)
        db.session.commit()

        project = Project(
            customer_id=customer.id,
            service="Website Design & Development",
            package="Advanced Website",
            currency="USD",
            requirements="Custom multi-tenant architecture and Stripe integration.",
            status="Payment Pending",
        )
        db.session.add(project)
        db.session.commit()

        sig = ContractSignature(
            project_id=project.id,
            signer_name="Eleanor Vance",
            signer_title="Chief Technology Officer",
            signature_hash="E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855",
            signature_data="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
            ip_address="198.51.100.42",
        )
        db.session.add(sig)
        db.session.commit()

        loaded_sig = ContractSignature.query.filter_by(project_id=project.id).first()
        assert loaded_sig is not None
        assert loaded_sig.signer_name == "Eleanor Vance"
        assert loaded_sig.signer_title == "Chief Technology Officer"
        assert loaded_sig.ip_address == "198.51.100.42"
        assert loaded_sig.signature_data.startswith("data:image/png;base64")


def test_project_time_tracking_model(app):
    """Verify that ProjectTimeLog logs hours against milestones and calculates billable value."""
    with app.app_context():
        customer = User(name="Billing Client", email="billing@client.com", role="customer")
        customer.set_password("SecurePass123!")
        specialist = User(name="Senior Engineer", email="engineer@specialist.com", role="professional")
        specialist.set_password("SecurePass123!")
        db.session.add_all([customer, specialist])
        db.session.commit()

        project = Project(
            customer_id=customer.id,
            service="Custom Web Application",
            package="Custom Web Application",
            currency="USD",
            requirements="Enterprise backend engineering.",
            status="In Progress",
            assigned_professional_id=specialist.id,
        )
        db.session.add(project)
        db.session.flush()

        milestone = Milestone(project_id=project.id, title="API Architecture", amount=3000, status="in_progress")
        db.session.add(milestone)
        db.session.commit()

        log1 = ProjectTimeLog(
            project_id=project.id,
            milestone_id=milestone.id,
            professional_id=specialist.id,
            hours=Decimal("4.50"),
            hourly_rate=Decimal("120.00"),
            description="Engineered Stripe subscription webhook listeners.",
            is_billed=False,
        )
        log2 = ProjectTimeLog(
            project_id=project.id,
            milestone_id=milestone.id,
            professional_id=specialist.id,
            hours=Decimal("3.00"),
            hourly_rate=Decimal("120.00"),
            description="Database schema optimization and indexing.",
            is_billed=False,
        )
        db.session.add_all([log1, log2])
        db.session.commit()

        logs = ProjectTimeLog.query.filter_by(project_id=project.id).all()
        assert len(logs) == 2
        total_hours = sum(Decimal(str(l.hours)) for l in logs)
        assert total_hours == Decimal("7.50")
        total_billable = sum(Decimal(str(l.hours)) * Decimal(str(l.hourly_rate)) for l in logs)
        assert total_billable == Decimal("900.00")


def test_stripe_subscription_invoice_payment_succeeded(client, app, monkeypatch):
    """Verify Stripe webhook creates digital invoices on invoice.payment_succeeded."""
    with app.app_context():
        customer = User(name="Retainer Client", email="retainer@client.com", role="customer")
        customer.set_password("SecurePass123!")
        db.session.add(customer)
        db.session.commit()

        # Create a matching project so the invoice foreign key constraint is satisfied in PostgreSQL
        project = Project(
            customer_id=customer.id,
            service="Monthly Retainer",
            package="Standard Retainer",
            currency="USD",
            requirements="Monthly retainer subscription.",
            status="Active",
        )
        db.session.add(project)
        db.session.commit()

    webhook_payload = {
        "id": "evt_test_sub_success_001",
        "type": "invoice.payment_succeeded",
        "data": {
            "object": {
                "id": "in_test_stripe_12345",
                "subscription": "sub_test_monthly_retainer",
                "amount_paid": 69900,  # $699.00
                "currency": "usd",
                "customer_email": "retainer@client.com",
            }
        }
    }

    # Bypass Stripe cryptographic signature in local test environment
    monkeypatch.setattr(
        "stripe.Webhook.construct_event",
        lambda payload, sig, secret: webhook_payload,
    )

    response = client.post(
        "/payment/stripe/webhook",
        data=json.dumps(webhook_payload),
        content_type="application/json",
        headers={"Stripe-Signature": "t=123,v1=test_signature"},
    )
    assert response.status_code == 200
    assert response.get_json()["status"] == "recurring_payment_recorded"


def test_deliverable_revision_cycle_limit(app):
    """Assert that deliverables track revision counters accurately."""
    with app.app_context():
        customer = User(name="Client", email="c@test.com", role="customer")
        specialist = User(name="Pro", email="p@test.com", role="professional")
        customer.set_password("pass123")
        specialist.set_password("pass123")
        db.session.add_all([customer, specialist])
        db.session.commit()

        project = Project(
            customer_id=customer.id,
            service="Website Design & Development",
            package="Basic Website",
            currency="INR",
            requirements="Sprint with 2-revision guarantee.",
            status="Revision",
            assigned_professional_id=specialist.id,
        )
        db.session.add(project)
        db.session.flush()

        d1 = ProjectDelivery(
            project_id=project.id,
            professional_id=specialist.id,
            submission_number=1,
            completion_notes="Initial build.",
            status="Revision",
            review_comment="Adjust brand colors to match hex.",
        )
        d2 = ProjectDelivery(
            project_id=project.id,
            professional_id=specialist.id,
            submission_number=2,
            completion_notes="Second build with updated hex.",
            status="Revision",
            review_comment="Minor margin tweaks.",
        )
        db.session.add_all([d1, d2])
        db.session.commit()

        rev_count = ProjectDelivery.query.filter_by(project_id=project.id, status="Revision").count()
        assert rev_count == 2