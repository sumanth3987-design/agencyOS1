import pytest

from app.models import AgencySetting, Business


VALID_SETTINGS = {
    "agency_name": "Northstar Studio",
    "primary_color": "#1a76c2",
    "logo_url": "https://assets.example.com/brand/logo.png",
    "whatsapp_support_number": "+14155550123",
}


def test_settings_are_admin_only_and_get_does_not_create_row(client, db, factories, login):
    customer_id = factories.customer("settings-client@example.com")
    response = client.get("/admin/settings")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    login("settings-client@example.com", "customer")
    response = client.get("/admin/settings")
    assert response.status_code == 302
    assert AgencySetting.query.count() == 0

    from app.models import User

    User.query.filter_by(id=customer_id).update({"role": "admin"})
    db.session.commit()
    # Login again so the session has the updated role.
    client.post("/logout", data={"csrf_token": _csrf_token(client)})
    login("settings-client@example.com", "admin")
    response = client.get("/admin/settings")

    assert response.status_code == 200
    assert b"AgencyOS" in response.data
    assert b"#6366f1" in response.data
    assert b'id="primary_color_picker"' in response.data
    assert b'id="primary_color_text"' in response.data
    assert b"colorText.addEventListener" in response.data
    assert AgencySetting.query.count() == 0


def test_settings_post_requires_csrf(client, factories, login):
    factories.admin("settings-csrf@example.com")
    login("settings-csrf@example.com", "admin")
    response = client.post("/admin/settings", data=VALID_SETTINGS)
    assert response.status_code == 400
    assert AgencySetting.query.count() == 0


def test_non_admin_cannot_post_settings(client, factories, login, csrf_post):
    factories.customer("settings-post-client@example.com")
    login("settings-post-client@example.com", "customer")

    response = csrf_post("/admin/settings", data=VALID_SETTINGS)

    assert response.status_code == 302
    assert AgencySetting.query.count() == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("agency_name", "   "),
        ("agency_name", "x" * 121),
        ("primary_color", "red"),
        ("primary_color", "#12345; background:url(javascript:alert(1))"),
        ("logo_url", "javascript:alert(1)"),
        ("logo_url", "ftp://example.com/logo.png"),
        ("logo_url", "https://user:pass@example.com/logo.png"),
        ("logo_url", "https:///logo.png"),
        ("whatsapp_support_number", "1234567"),
        ("whatsapp_support_number", "++14155550123"),
        ("whatsapp_support_number", "1234567890123456"),
    ],
)
def test_invalid_settings_are_rejected_without_partial_persistence(
    client, db, factories, login, csrf_post, field, value
):
    factories.admin(f"settings-invalid-{field}@example.com")
    login(f"settings-invalid-{field}@example.com", "admin")
    submitted = {**VALID_SETTINGS, field: value}

    response = csrf_post("/admin/settings", data=submitted)

    assert response.status_code == 400
    assert b"Settings were not saved" in response.data
    assert AgencySetting.query.count() == 0


def test_settings_save_persist_and_update_singleton_row(
    client, db, factories, login, csrf_post
):
    factories.admin("settings-save@example.com")
    login("settings-save@example.com", "admin")

    first = csrf_post(
        "/admin/settings",
        data={**VALID_SETTINGS, "agency_name": "  Northstar Studio  "},
    )
    assert first.status_code == 302
    settings = AgencySetting.query.one()
    assert settings.id == 1
    assert settings.agency_name == "Northstar Studio"
    assert settings.primary_color == "#1a76c2"
    assert settings.logo_url == VALID_SETTINGS["logo_url"]
    assert settings.whatsapp_support_number == "+14155550123"
    row_id = settings.id

    updated = {
        **VALID_SETTINGS,
        "agency_name": "Northstar Digital",
        "logo_url": "",
    }
    second = csrf_post("/admin/settings", data=updated)
    assert second.status_code == 302

    client.get("/admin/settings")
    persisted = AgencySetting.query.one()
    assert persisted.id == row_id
    assert AgencySetting.query.count() == 1
    assert persisted.agency_name == "Northstar Digital"
    assert persisted.logo_url is None


def test_failed_update_does_not_partially_change_saved_settings(
    client, db, factories, login, csrf_post
):
    factories.admin("settings-atomic@example.com")
    login("settings-atomic@example.com", "admin")
    csrf_post("/admin/settings", data=VALID_SETTINGS)

    invalid = {**VALID_SETTINGS, "agency_name": "Changed Name", "primary_color": "not-a-color"}
    response = csrf_post("/admin/settings", data=invalid)

    assert response.status_code == 400
    settings = AgencySetting.query.one()
    assert settings.agency_name == "Northstar Studio"
    assert settings.primary_color == "#1a76c2"


def test_admin_navigation_exposes_settings_link(client, factories, login):
    factories.admin("settings-nav@example.com")
    login("settings-nav@example.com", "admin")
    response = client.get("/admin")

    assert response.status_code == 200
    assert b'href="/admin/settings"' in response.data
    assert b"Agency Settings" in response.data


def test_portal_context_exposes_defaults_without_persisting(client, factories, login):
    factories.customer("settings-defaults@example.com")
    login("settings-defaults@example.com", "customer")
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert b"AgencyOS" in response.data
    assert b"#6366f1" in response.data
    assert AgencySetting.query.count() == 0


def test_client_portal_and_invoice_use_saved_branding(client, db, factories, login):
    customer_id = factories.customer("settings-brand-client@example.com")
    project_id = factories.project(customer_id, status="Payment Pending")
    db.session.add(AgencySetting(
        id=1,
        agency_name="Northstar Studio",
        primary_color="#1a76c2",
        logo_url="https://assets.example.com/northstar.png",
        whatsapp_support_number="+14155550123",
    ))
    db.session.commit()
    login("settings-brand-client@example.com", "customer")

    for path in (
        "/dashboard",
        "/start-project",
        "/invoices",
        f"/project/{project_id}",
        f"/project/{project_id}/invoice",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert b"Northstar Studio" in response.data, path
        assert b"--agency-primary: #1a76c2" in response.data, path

    workspace = client.get(f"/project/{project_id}")
    assert b'class="flex flex-col gap-3 border-b border-zinc-800/80 pb-6 sm:flex-row sm:items-center sm:justify-between"' in workspace.data
    assert b'class="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:justify-end"' in workspace.data
    assert b".agency-action { background-color: var(--agency-primary); color: var(--agency-on-primary); }" in workspace.data
    assert b'class="agency-action px-3.5 py-1.5 rounded-xl text-xs font-bold transition flex items-center gap-1.5"' in workspace.data
    assert b'aria-label="Notifications"' in workspace.data
    assert f'href="/project/{project_id}/invoice"'.encode() in workspace.data
    assert f'href="/project/{project_id}/contract"'.encode() in workspace.data
    assert b"wa.me/14155550123" in workspace.data
    assert b"wa.me/919876543210" not in workspace.data

    signed_project_id = factories.project(
        customer_id,
        requirements="Build a website [Signed by: Test Signer (Founder) from IP: 127.0.0.1 on 01 Nov 2026 10:00 IST]",
    )
    signed_workspace = client.get(f"/project/{signed_project_id}")
    assert f'href="/project/{signed_project_id}/sow-pdf"'.encode() in signed_workspace.data
    assert b"Download Signed SOW (PDF)" in signed_workspace.data

    invoice = client.get(f"/project/{project_id}/invoice")
    assert b"https://assets.example.com/northstar.png" in invoice.data
    assert b"AgencyOS Digital Solutions" not in invoice.data
    assert b"invoice-total-amount { background: transparent !important; color: #000000 !important;" in invoice.data
    assert b".invoice-issuer, .invoice-issuer * { color: #000000 !important; }" in invoice.data
    assert b"@media screen and (max-width: 800px)" in invoice.data
    assert b".invoice-table tbody td::before { content: attr(data-label)" in invoice.data
    assert b"this.nextElementSibling.classList.remove('hidden')" in invoice.data
    assert b".invoice-table thead { display: table-header-group !important; }" in invoice.data
    assert b".invoice-table th, .invoice-table td { display: table-cell !important;" in invoice.data
    assert b"@media screen and (max-width: 800px)" in invoice.data

    dashboard = client.get("/dashboard")
    assert b"this.nextElementSibling.classList.remove('hidden')" in dashboard.data
    assert b".agency-action { background-color: var(--agency-primary); color: var(--agency-on-primary); }" in dashboard.data
    assert b"class=\"agency-action px-5 py-2.5 rounded-xl" in dashboard.data
    assert b"bg-rose-500 text-white" in dashboard.data


def test_remaining_customer_pages_use_saved_branding(client, db, factories, login):
    customer_id = factories.customer("settings-remaining-pages@example.com")
    project_id = factories.project(customer_id)
    business = Business(customer_id=customer_id, name="Northstar Retail")
    db.session.add(business)
    db.session.add(AgencySetting(
        id=1,
        agency_name="Northstar Studio",
        primary_color="#1a76c2",
        logo_url="https://assets.example.com/northstar.png",
        whatsapp_support_number="+14155550123",
    ))
    db.session.commit()
    login("settings-remaining-pages@example.com", "customer")

    paths = (
        "/businesses",
        "/add-business",
        f"/business/{business.id}/edit",
        "/profile",
        f"/project/{project_id}/contract",
        f"/payment/{project_id}",
    )
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, path
        assert b"Northstar Studio" in response.data, path
        assert b"https://assets.example.com/northstar.png" in response.data, path
        assert b"--agency-primary: #1a76c2" in response.data, path
        assert b"AgencyOS" not in response.data, path
        assert b"indigo-" not in response.data, path
        assert b"this.nextElementSibling.classList.remove('hidden')" in response.data, path

    checkout = client.get(f"/payment/{project_id}")
    assert b"#payBtn { background-color: var(--agency-primary); color: var(--agency-on-primary); }" in checkout.data
    assert b"#payBtn:focus-visible { outline: 2px solid #ffffff;" in checkout.data
    assert b'theme: { color: "#1a76c2" }' in checkout.data
    assert b"@media (max-width: 480px)" in checkout.data


def test_specialist_portal_templates_use_saved_branding(client, db, factories, login):
    customer_id = factories.customer("settings-specialist-client@example.com")
    professional_id = factories.professional("settings-specialist@example.com")
    project_id = factories.project(customer_id, status="Assigned")
    factories.application(project_id, professional_id, status="Assigned")
    db.session.add(AgencySetting(
        id=1,
        agency_name="Northstar Studio",
        primary_color="#1a76c2",
        logo_url="https://assets.example.com/northstar.png",
        whatsapp_support_number="+14155550123",
    ))
    db.session.commit()
    login("settings-specialist@example.com", "professional")

    for path in (
        "/professional/dashboard",
        "/professional/my-projects",
        "/professional/projects",
        "/professional/profile",
        f"/professional/project/{project_id}/workspace",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert b"Northstar Studio" in response.data, path
        assert b"https://assets.example.com/northstar.png" in response.data, path
        assert b"--agency-primary: #1a76c2" in response.data, path


def _csrf_token(client):
    with client.session_transaction() as session:
        return session.get("csrf_token", "")
