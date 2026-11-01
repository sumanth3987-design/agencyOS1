from app.models import ActivityLog, Project


def test_new_project_is_recorded_and_shown_in_workspace(
    app, client, factories, login, csrf_post
):
    customer_id = factories.customer("activity-create@example.com")
    login("activity-create@example.com", "customer")

    response = csrf_post(
        "/start-project",
        data={
            "service": "SEO",
            "package": "SEO Starter",
            "brief": "Improve organic search visibility.",
        },
    )

    assert response.status_code == 302
    with app.app_context():
        project = Project.query.filter_by(customer_id=customer_id).one()
        entry = ActivityLog.query.filter_by(
            project_id=project.id,
            action="PROJECT_CREATED",
        ).one()
        assert entry.user_id == customer_id
        assert entry.category == "info"

    workspace = client.get(f"/project/{project.id}")
    assert workspace.status_code == 200
    assert b"Project Activity Audit Trail" in workspace.data
    assert b"Project workspace created and awaiting SOW signature." in workspace.data


def test_sow_signature_is_recorded_with_signer(
    app, client, factories, login, csrf_post
):
    customer_id = factories.customer("activity-sow@example.com")
    project_id = factories.project(customer_id)
    login("activity-sow@example.com", "customer")

    response = csrf_post(
        f"/project/{project_id}/contract",
        data={
            "agreed_terms": "on",
            "signer_name": "Avery Client",
            "signer_title": "Founder",
        },
    )

    assert response.status_code == 302
    with app.app_context():
        entry = ActivityLog.query.filter_by(
            project_id=project_id,
            action="SOW_SIGNED",
        ).one()
        assert entry.user_id == customer_id
        assert entry.category == "success"
        assert "Avery Client (Founder)" in entry.description


def test_submission_revision_and_approval_are_recorded(
    app, client, factories, login, csrf_post
):
    customer_id = factories.customer("activity-review@example.com")
    project_id = factories.project(customer_id, status="In Progress")
    professional_id = factories.professional("activity-specialist@example.com")
    factories.profile(professional_id, verification_status="Verified")
    factories.application(project_id, professional_id, status="Assigned")

    login("activity-specialist@example.com", "professional")
    submitted = csrf_post(
        f"/professional/project/{project_id}/submit",
        data={
            "live_url": "https://example.com",
            "completion_notes": "First staging submission",
        },
    )
    assert submitted.status_code == 302

    login("activity-review@example.com", "customer")
    revision = csrf_post(
        f"/project/{project_id}/revision",
        data={"revision_comment": "Please adjust the mobile navigation."},
    )
    assert revision.status_code == 302

    approval = csrf_post(f"/project/{project_id}/approve", data={})
    assert approval.status_code == 302

    with app.app_context():
        entries = ActivityLog.query.filter_by(project_id=project_id).all()
        by_action = {entry.action: entry for entry in entries}
        assert by_action["DELIVERABLE_SUBMITTED"].user_id == professional_id
        assert by_action["DELIVERABLE_SUBMITTED"].category == "accent"
        assert "First staging submission" in by_action["DELIVERABLE_SUBMITTED"].description
        assert by_action["REVISION_REQUESTED"].user_id == customer_id
        assert "mobile navigation" in by_action["REVISION_REQUESTED"].description
        assert by_action["MILESTONE_APPROVED"].user_id == customer_id
        assert by_action["MILESTONE_APPROVED"].category == "success"
