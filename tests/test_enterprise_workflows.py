"""Enterprise Workflow Integration Tests for AgencyOS.

Covers:
1. Stripe Webhook idempotency and duplicate event suppression.
2. Immutable FinancialAuditLog recording upon checkout completion.
3. Multi-milestone payments for active projects in 'In Progress' state.
4. SOW PDF generation resilience with dictionary-based signatures.
5. Professional meritocracy performance payout calculations.
6. Autonomous deliverable review reminder anti-spam execution guards.
"""

from decimal import Decimal
import json
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import (
    ActivityLog,
    CustomerProfile,
    FinancialAuditLog,
    Invoice,
    Payment,
    PaymentWebhookEvent,
    Project,
    ProjectDelivery,
    StripeWebhookEvent,
    User,
    get_ist_now,
)
from app.services.payouts import calculate_meritocracy_rate, get_meritocracy_tier_info
from app.services.pdf_generator import extract_sow_signature, generate_sow_pdf
from app.services.reminders import send_review_urgency_nudge


# ===========================================================================
# 1. STRIPE WEBHOOK IDEMPOTENCY & FINANCIAL AUDIT TRAIL
# ===========================================================================

class TestStripeWebhookResilience:
    """Verifies that duplicate webhook deliveries are safely ignored and audit logs are recorded."""

    def test_stripe_webhook_idempotent_duplicate_suppression(self, app, client, factories):
        """Duplicate Stripe events must be acknowledged as 200 'ignored' without double crediting."""
        customer_id = factories.user("customer", "stripe_idem@example.com")
        project_id = factories.project(customer_id, status="Payment Pending", currency="USD")

        event_id = "evt_test_unique_idem_1001"
        fake_payload = {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_session_1001",
                    "client_reference_id": str(project_id),
                    "metadata": {"project_id": str(project_id)},
                    "payment_status": "paid",
                    "amount_total": 69900,
                    "currency": "usd",
                    "payment_intent": "pi_test_1001",
                }
            },
        }

        with patch("stripe.Webhook.construct_event", return_value=fake_payload):
            # First delivery
            res1 = client.post(
                "/payment/stripe/webhook",
                data=json.dumps(fake_payload),
                headers={"Stripe-Signature": "t=123,v1=mock_sig"},
                content_type="application/json",
            )
            assert res1.status_code == 200
            assert res1.get_json()["status"] == "success"

            with app.app_context():
                proj = db.session.get(Project, project_id)
                assert proj.status == "Pending Assignment"

                payment = Payment.query.filter_by(stripe_session_id="cs_test_session_1001").first()
                assert payment is not None
                assert payment.status == "Paid"
                assert payment.amount == Decimal("699.00")

                inv = Invoice.query.filter_by(project_id=project_id).first()
                assert inv is not None
                assert inv.status == "Paid"
                assert inv.paid_amount == Decimal("699.00")

                # Verify Immutable Financial Audit Log was created
                audit = FinancialAuditLog.query.filter_by(invoice_id=inv.id).first()
                assert audit is not None
                assert audit.action == "STRIPE_CHECKOUT_COMPLETED"
                assert audit.new_status == "Paid"
                assert audit.amount == Decimal("699.00")

                assert StripeWebhookEvent.is_processed(event_id) is True

            # Duplicate delivery (Replay attack or network retry)
            res2 = client.post(
                "/payment/stripe/webhook",
                data=json.dumps(fake_payload),
                headers={"Stripe-Signature": "t=123,v1=mock_sig"},
                content_type="application/json",
            )
            assert res2.status_code == 200
            assert res2.get_json()["status"] == "ignored"
            assert res2.get_json()["reason"] == "duplicate_event"

            with app.app_context():
                # Verify balance was not incremented twice
                inv = Invoice.query.filter_by(project_id=project_id).first()
                assert inv.paid_amount == Decimal("699.00")

    def test_stripe_subsequent_milestone_payment_in_progress(self, app, client, factories):
        """Projects in 'In Progress' status paying Gate 2 or Gate 3 must be marked Paid."""
        customer_id = factories.user("customer", "stripe_milestone@example.com")
        project_id = factories.project(customer_id, status="In Progress", currency="USD")

        event_id = "evt_test_gate2_2002"
        fake_payload = {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_gate2_2002",
                    "client_reference_id": str(project_id),
                    "metadata": {"project_id": str(project_id)},
                    "payment_status": "paid",
                    "amount_total": 35000,
                    "currency": "usd",
                    "payment_intent": "pi_gate2_2002",
                }
            },
        }

        with patch("stripe.Webhook.construct_event", return_value=fake_payload):
            res = client.post(
                "/payment/stripe/webhook",
                data=json.dumps(fake_payload),
                headers={"Stripe-Signature": "t=123,v1=mock_sig"},
                content_type="application/json",
            )
            assert res.status_code == 200
            assert res.get_json()["status"] == "success"

            with app.app_context():
                payment = Payment.query.filter_by(stripe_session_id="cs_test_gate2_2002").first()
                assert payment is not None
                assert payment.status == "Paid"


# ===========================================================================
# 2. STATEMENT OF WORK (SOW) PDF GENERATOR RESILIENCE
# ===========================================================================

class TestSowPdfGeneration:
    """Verifies that SOW compilation works across diverse inputs and dictionary signatures."""

    def test_sow_pdf_compilation_with_dict_signature(self, app, factories):
        """Verifies SOW PDF compiles successfully with dictionary signature metadata."""
        with app.app_context():
            customer_id = factories.user("customer", "sow_client@example.com")
            customer_user = db.session.get(User, customer_id)
            project_id = factories.project(customer_id, service="Custom Web Application", package="Advanced Website")
            project = db.session.get(Project, project_id)

            dict_sig = {
                "signer_name": "Eleanor Vance",
                "signer_title": "Chief Technology Officer",
                "ip_address": "198.51.100.42",
                "signed_at": "12 Nov 2026 14:30 IST",
            }

            pdf_buffer = generate_sow_pdf(project, customer_user, None, dict_sig)
            assert pdf_buffer is not None
            pdf_bytes = pdf_buffer.getvalue()

            # Verify standard PDF file header
            assert pdf_bytes.startswith(b"%PDF-")
            assert len(pdf_bytes) > 2000

    def test_sow_signature_extraction_regex(self):
        """Verifies regex extraction of electronic signature audit text."""
        raw_requirements = (
            "Build an enterprise freight management portal with automated load matching.\n"
            " [Signed by: Marcus Aurelius (Managing Partner) from IP: 203.0.113.195 on 12 Nov 2026 10:15 IST]"
        )
        sig = extract_sow_signature(raw_requirements)
        assert sig is not None
        assert sig.signer_name == "Marcus Aurelius"
        assert sig.signer_title == "Managing Partner"
        assert sig.ip_address == "203.0.113.195"
        assert sig.signed_at == "12 Nov 2026 10:15 IST"


# ===========================================================================
# 3. PROFESSIONAL MERITOCRACY COMMISSIONS
# ===========================================================================

class TestProfessionalMeritocracy:
    """Verifies dynamic meritocracy commission tier calculations."""

    def test_default_meritocracy_tier(self, app, factories):
        """Standard specialist starts at 45% commission tier."""
        with app.app_context():
            prof_id = factories.user("professional", "standard_pro@example.com")
            factories.profile(prof_id, verification_status="Pending")
            user = db.session.get(User, prof_id)

            rate = calculate_meritocracy_rate(user)
            assert rate == Decimal("0.45")

            info = get_meritocracy_tier_info(user)
            assert info["rate_percentage"] == 45
            assert "Standard Partner" in info["tier_name"]


# ===========================================================================
# 4. REVIEW URGENCY NUDGE ANTI-SPAM GUARD
# ===========================================================================

class TestReminderEngineAntiSpam:
    """Verifies that 48h deliverable review urgency nudges do not spam clients repeatedly."""

    def test_urgency_nudge_fires_once_and_blocks_consequent_runs(self, app, factories):
        """Urgency nudge must record an ActivityLog entry and not fire twice for the same delivery."""
        from datetime import datetime, timedelta
        from app.models import IST

        with app.app_context():
            customer_id = factories.user("customer", "nudge_client@example.com")
            profile = CustomerProfile(user_id=customer_id, phone="919876543210")
            db.session.add(profile)
            project_id = factories.project(customer_id, status="Submitted")
            prof_id = factories.user("professional", "delivery_pro@example.com")

            now_ist = datetime.now(IST)
            submitted_50h_ago = now_ist - timedelta(hours=50)

            delivery = ProjectDelivery(
                project_id=project_id,
                professional_id=prof_id,
                submission_number=1,
                completion_notes="Initial staging build ready for review.",
                status="Submitted",
                submitted_at=submitted_50h_ago,
            )
            db.session.add(delivery)
            db.session.commit()

            # First run: in the 48h-72h window -> Dispatches and returns True
            with patch("app.services.reminders.notify_review_nudge", return_value=True):
                first_run = send_review_urgency_nudge(project_id, now=now_ist)
                assert first_run is True

                log_entry = ActivityLog.query.filter_by(
                    project_id=project_id,
                    action=f"REVIEW_URGENCY_NUDGE_DELIVERY_{delivery.id}",
                ).first()
                assert log_entry is not None

            # Immediate second run (e.g. cron executing 10 minutes later) -> Must be blocked
            with patch("app.services.reminders.notify_review_nudge", return_value=True) as mock_nudge:
                second_run = send_review_urgency_nudge(project_id, now=now_ist)
                assert second_run is False
                mock_nudge.assert_not_called()