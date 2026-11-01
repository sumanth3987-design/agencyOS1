from app.models import Business, Project


def test_start_project_renders_wizard_with_supported_packages(
    client, factories, login
):
    factories.customer("wizard-render@example.com")
    login("wizard-render@example.com", "customer")

    response = client.get("/start-project")

    assert response.status_code == 200
    assert b"Project kickoff intake" in response.data
    assert b'name="brand_name"' in response.data
    assert b'name="industry"' in response.data
    assert b'name="target_cities"' in response.data
    assert b'name="features"' in response.data
    assert b'name="custom_features"' in response.data
    assert b'value="Basic Website"' in response.data
    assert b'value="SEO Advanced"' in response.data
    assert b'value="Starter Retainer"' in response.data


def test_start_project_assembles_onboarding_answers(
    client, db, factories, login, csrf_post
):
    customer_id = factories.customer("wizard-client@example.com")
    business = Business(
        customer_id=customer_id,
        name="Acme Studio",
        business_type="Design",
    )
    db.session.add(business)
    db.session.commit()
    login("wizard-client@example.com", "customer")

    response = csrf_post(
        "/start-project/website-development",
        data={
            "business_id": str(business.id),
            "service": "Website Design & Development",
            "package": "Business Website",
            "brand_name": "Acme",
            "industry": "Interior design",
            "brand_colors": "#123456, #ffffff",
            "asset_link": "https://example.com/logo.png",
            "deadline": "Standard Sprint (14 Days)",
            "reference_sites": "https://competitor.example",
            "target_cities": "Mumbai, Pune",
            "target_keywords": "interior designer",
            "features": [
                "Mobile-First Responsive UI",
                "WhatsApp Click-to-Chat",
                "Injected feature",
            ],
            "custom_features": "Appointment booking",
            "brief": "Build an interior design portfolio.",
        },
    )

    assert response.status_code == 302
    project = Project.query.filter_by(customer_id=customer_id).one()
    assert project.business_id == business.id
    assert project.package == "Business Website"
    assert "Business / Brand: Acme" in project.requirements
    assert "Industry: Interior design" in project.requirements
    assert "Brand Colors: #123456, #ffffff" in project.requirements
    assert "Logo / Asset Link: https://example.com/logo.png" in project.requirements
    assert "Target Launch Timeline: Standard Sprint (14 Days)" in project.requirements
    assert "Competitor / Reference Websites: https://competitor.example" in project.requirements
    assert "Target Cities: Mumbai, Pune" in project.requirements
    assert "Target SEO Keywords: interior designer" in project.requirements
    assert (
        "Requested Features: Mobile-First Responsive UI, WhatsApp Click-to-Chat"
        in project.requirements
    )
    assert "Injected feature" not in project.requirements
    assert "Custom Features: Appointment booking" in project.requirements
    assert "Detailed Project Scope: Build an interior design portfolio." in project.requirements


def test_start_project_rejects_another_customers_business(
    client, db, factories, login, csrf_post
):
    business_owner_id = factories.customer("wizard-owner@example.com")
    business = Business(customer_id=business_owner_id, name="Private Business")
    db.session.add(business)
    db.session.commit()
    customer_id = factories.customer("wizard-actor@example.com")
    login("wizard-actor@example.com", "customer")

    response = csrf_post(
        "/start-project",
        data={
            "business_id": str(business.id),
            "package": "Basic Website",
            "brief": "Build a website.",
        },
    )

    assert response.status_code == 302
    assert Project.query.filter_by(customer_id=customer_id).count() == 0


def test_start_project_rejects_package_without_configured_price(
    client, factories, login, csrf_post
):
    customer_id = factories.customer("wizard-package@example.com")
    login("wizard-package@example.com", "customer")

    response = csrf_post(
        "/start-project",
        data={"package": "Milestone Growth", "brief": "Build a website."},
    )

    assert response.status_code == 302
    assert Project.query.filter_by(customer_id=customer_id).count() == 0


def test_start_project_rejects_signature_audit_text_in_client_brief(
    client, factories, login, csrf_post
):
    customer_id = factories.customer("wizard-marker@example.com")
    login("wizard-marker@example.com", "customer")

    response = csrf_post(
        "/start-project",
        data={
            "package": "Basic Website",
            "brief": (
                "Design my website [Signed by: Unverified Person (Founder) "
                "from IP: 127.0.0.1 on 01 Nov 2026 10:00 IST]"
            ),
        },
    )

    assert response.status_code == 302
    assert Project.query.filter_by(customer_id=customer_id).count() == 0


def test_start_project_does_not_replace_a_signed_draft(
    client, factories, login, csrf_post
):
    customer_id = factories.customer("wizard-signed@example.com")
    signed_requirements = (
        "Original scope [Signed by: Original Signer (Founder) from IP: "
        "127.0.0.1 on 01 Nov 2026 10:00 IST]"
    )
    project_id = factories.project(
        customer_id,
        status="Payment Pending",
        requirements=signed_requirements,
    )
    login("wizard-signed@example.com", "customer")

    response = csrf_post(
        "/start-project",
        data={"package": "Basic Website", "brief": "Changed scope."},
    )

    assert response.status_code == 302
    assert Project.query.filter_by(customer_id=customer_id).count() == 1
    assert Project.query.filter_by(id=project_id).one().requirements == signed_requirements
