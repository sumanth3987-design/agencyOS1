"""Unit & Integration Tests for AgencyOS Competitive Features & Patches."""

from decimal import Decimal
import re
import pytest

from app.extensions import db
from app.models import (
    ActivityLog,
    CustomerProfile,
    Milestone,
    Payment,
    ProfessionalProfile,
    ProfessionalProjectApplication,
    Project,
    User,
    WalletTransaction,
    get_ist_now,
)
from app.services.payouts import calculate_meritocracy_rate, disburse_payout
from app.services.pdf_generator import SowSignature, extract_sow_signature, generate_sow_pdf
from app.services.whatsapp import _format_phone, send_whatsapp_alert
from app.utils.currency import compute_financial_split, format_currency, get_package_price


def _get_csrf_token(client) -> str:
    """Extract active CSRF token from the authenticated client session."""
    response = client.get("/dashboard")
    html = response.get_data(as_text=True)

    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html) or re.search(r'value="([^"]+)"\s+name="csrf_token"', html)
    if match:
        return match.group(1)

    match = re.search(r'data-csrf-token="([^"]+)"', html)
    if match:
        return match.group(1)

    res_logout = client.get("/logout")
    html_logout = res_logout.get_data(as_text=True)
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html_logout)
    return match.group(1) if match else ""


def test_wire_transfer_calculation_patch(client, app):
    """Test that selecting a wire transfer uses dynamic package pricing and does not throw AttributeError."""
    with app.app_context():
        customer = User(name="Wire Client", email="wire@example.com", role="customer")
        customer.set_password("SecurePass123!")
        db.session.add(customer)
        db.session.commit()

        project = Project(
            customer_id=customer.id,
            service="Website Design & Development",
            package="Basic Website",
            currency="INR",
            requirements="Full-stack responsive business website with SEO optimization.",
            status="Payment Pending",
        )
        db.session.add(project)
        db.session.commit()
        project_id = project.id

    client.post(
        "/login",
        data={"email": "wire@example.com", "password": "SecurePass123!", "login_role": "customer"},
    )

    csrf_token = _get_csrf_token(client)

    response = client.post(
        f"/payment/wire/select/{project_id}",
        headers={"X-CSRFToken": csrf_token},
        data={"csrf_token": csrf_token},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "instructions" in data
    # Basic Website is ₹48,000; 50% advance = ₹24,000
    assert Decimal(data["instructions"]["amount_due"]) == Decimal("24000.00")

    with app.app_context():
        payment = Payment.query.filter_by(project_id=project_id, gateway_type="wire").first()
        assert payment is not None
        assert payment.amount == Decimal("24000.00")
        assert payment.status == "awaiting_wire"


def test_specialist_meritocracy_rates(app):
    """Test dynamic meritocracy rate calculation based on specialist ratings and verification."""
    with app.app_context():
        specialist = User(name="Top Specialist", email="top@example.com", role="professional")
        specialist.set_password("SecurePass123!")
        db.session.add(specialist)
        db.session.commit()

        profile = ProfessionalProfile(
            user_id=specialist.id,
            verification_status="Verified",
        )
        db.session.add(profile)
        db.session.commit()

        rate = calculate_meritocracy_rate(specialist)
        assert rate == Decimal("0.45")


def test_whatsapp_phone_normalization():
    """Verify phone normalization adheres to international E.164 standards."""
    assert _format_phone("9876543210") == "919876543210"
    assert _format_phone("+919876543210") == "919876543210"
    assert _format_phone("919876543210") == "919876543210"
    assert _format_phone(None) is None
    assert _format_phone("") is None


def test_sow_pdf_generation_pipeline(app):
    """Verify ReportLab compiles SOW PDF with legal signature coordinates."""
    with app.app_context():
        customer = User(name="Legal Client", email="legal@example.com", role="customer")
        customer.set_password("SecurePass123!")
        db.session.add(customer)
        db.session.commit()

        reqs = "Custom SaaS platform build [Signed by: Legal Client (Director) from IP: 127.0.0.1 on 01 Jan 2026 10:00 IST]"
        project = Project(
            customer_id=customer.id,
            service="Custom Web Application",
            package="Custom Web Application",
            currency="INR",
            requirements=reqs,
            status="In Progress",
        )
        db.session.add(project)
        db.session.commit()

        sig = extract_sow_signature(project.requirements)
        assert sig is not None
        assert sig.signer_name == "Legal Client"
        assert sig.signer_title == "Director"

        pdf_buf = generate_sow_pdf(project, customer, None, sig)
        assert pdf_buf is not None
        content = pdf_buf.getvalue()
        assert content.startswith(b"%PDF")
        assert len(content) > 1000


def test_api_milestone_approval_escrow_release(client, app):
    """Test that milestone approval via API correctly releases escrow to specialist balance."""
    with app.app_context():
        customer = User(name="API Client", email="apiclient@example.com", role="customer")
        customer.set_password("SecurePass123!")
        specialist = User(name="API Pro", email="apipro@example.com", role="professional")
        specialist.set_password("SecurePass123!")
        db.session.add_all([customer, specialist])
        db.session.commit()

        specialist_id = int(specialist.id)

        profile = ProfessionalProfile(
            user_id=specialist_id,
            pending_balance=Decimal("21600.00"),
            available_balance=Decimal("0.00"),
            verification_status="Verified",
        )
        db.session.add(profile)

        project = Project(
            customer_id=customer.id,
            service="Website Design & Development",
            package="Basic Website",
            currency="INR",
            requirements="Full-stack sprint with milestone gated architecture.",
            status="In Progress",
            assigned_professional_id=specialist_id,
        )
        db.session.add(project)
        db.session.flush()

        app_rec = ProfessionalProjectApplication(
            project_id=project.id,
            professional_id=specialist_id,
            status="Assigned",
            proposed_rate=Decimal("21600.00"),
        )
        db.session.add(app_rec)

        m1 = Milestone(project_id=project.id, title="Gate 1: Discovery (50%)", amount=12000, status="pending")
        m2 = Milestone(project_id=project.id, title="Gate 2: Staging (25%)", amount=6000, status="pending")
        m3 = Milestone(project_id=project.id, title="Gate 3: Launch (25%)", amount=6000, status="pending")
        db.session.add_all([m1, m2, m3])
        db.session.commit()

        milestone_id = int(m1.id)
        client_email = str(customer.email)

    client.post(
        "/login",
        data={"email": client_email, "password": "SecurePass123!", "login_role": "customer"},
    )

    csrf_token = _get_csrf_token(client)

    response = client.post(
        f"/api/milestones/{milestone_id}/approve",
        headers={"X-CSRFToken": csrf_token},
    )
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data["success"] is True

    with app.app_context():
        spec_profile = ProfessionalProfile.query.filter_by(user_id=specialist_id).first()
        assert spec_profile is not None
        # Gate 1 approval: 50% of ₹21,600 = ₹10,800 released to available balance
        assert spec_profile.available_balance == Decimal("10800.00")
        assert spec_profile.pending_balance == Decimal("10800.00")

        tx = WalletTransaction.query.filter_by(user_id=specialist_id, transaction_type="ESCROW_RELEASED").first()
        assert tx is not None
        assert tx.amount == Decimal("10800.00")