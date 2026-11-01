import re

from app.models import Project


def _signed_requirements(brief="Build a website"):
    return (
        f"{brief} [Signed by: Test Signer (Founder) from IP: "
        "127.0.0.1 on 01 Nov 2026 10:00 IST]"
    )


def test_unsigned_project_cannot_download_signed_sow(client, factories, login):
    customer_id = factories.customer("sow-client@example.com")
    project_id = factories.project(customer_id)
    login("sow-client@example.com", "customer")

    response = client.get(f"/project/{project_id}/sow-pdf")

    assert response.status_code == 302
    assert f"/project/{project_id}/contract" in response.headers["Location"]


def test_contract_requires_explicit_acceptance_before_signing(
    client, factories, login, csrf_post
):
    customer_id = factories.customer("sow-unchecked@example.com")
    project_id = factories.project(customer_id)
    login("sow-unchecked@example.com", "customer")

    response = csrf_post(
        f"/project/{project_id}/contract",
        data={"signer_name": "Test Signer", "signer_title": "Founder"},
    )

    assert response.status_code == 302
    assert f"/project/{project_id}/contract" in response.headers["Location"]

    assert (
        Project.query.filter_by(id=project_id).one().requirements == "Build a website"
    )


def test_customer_can_download_owned_signed_sow(client, factories, login):
    customer_id = factories.customer("sow-owner@example.com")
    project_id = factories.project(
        customer_id, requirements=_signed_requirements("Create <b>safe</b> scope")
    )
    login("sow-owner@example.com", "customer")

    response = client.get(f"/project/{project_id}/sow-pdf")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.headers["Content-Disposition"] == (
        f"attachment; filename=AgencyOS_SOW_Project_{project_id}.pdf"
    )
    assert response.data.startswith(b"%PDF-")
    assert len(re.findall(rb"/Type\s*/Page\b", response.data)) == 2


def test_customer_cannot_download_another_customers_signed_sow(
    client, factories, login
):
    owner_id = factories.customer("sow-other-owner@example.com")
    project_id = factories.project(
        owner_id, requirements=_signed_requirements()
    )
    factories.customer("sow-other-customer@example.com")
    login("sow-other-customer@example.com", "customer")

    response = client.get(f"/project/{project_id}/sow-pdf")

    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]
