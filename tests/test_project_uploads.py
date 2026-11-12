from io import BytesIO
import os
import zipfile

from app.models import Project, ProjectDelivery


def test_customer_can_attach_srs_to_project(
    app, client, db, factories, login, csrf_post, tmp_path, monkeypatch
):
    customer_id = factories.user("customer", "upload-client@example.com")
    login("upload-client@example.com", "customer")
    monkeypatch.setattr(app, "static_folder", str(tmp_path))
    pdf_contents = b"%PDF-1.4\nproject requirements\n%%EOF"

    response = csrf_post(
        "/start-project",
        data={
            "brief": "Build a customer portal",
            "srs_document": (BytesIO(pdf_contents), "../requirements.pdf"),
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    project = Project.query.filter_by(customer_id=customer_id).one()
    assert "Attached Client Document: /static/uploads/" in project.requirements
    stored_name = project.requirements.rsplit("/", 1)[-1]
    with open(os.path.join(tmp_path, "uploads", stored_name), "rb") as saved_file:
        assert saved_file.read() == pdf_contents


def test_assigned_specialist_can_attach_deliverable(
    app, client, db, factories, login, csrf_post, tmp_path, monkeypatch
):
    customer_id = factories.user("customer", "delivery-client@example.com")
    project_id = factories.project(customer_id, status="In Progress")
    professional_id = factories.user("professional", "delivery-specialist@example.com")
    factories.application(project_id, professional_id, status="Assigned")
    login("delivery-specialist@example.com", "professional")
    monkeypatch.setattr(app, "static_folder", str(tmp_path))
    archive_stream = BytesIO()
    with zipfile.ZipFile(archive_stream, "w") as archive:
        archive.writestr("deliverable.txt", "deliverable archive")
    deliverable_archive = archive_stream.getvalue()

    response = csrf_post(
        f"/professional/project/{project_id}/submit",
        data={
            "preview_url": "https://preview.example.com",
            "completion_notes": "Delivery notes",
            "deliverable_file": (BytesIO(deliverable_archive), "assets.zip"),
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    delivery = ProjectDelivery.query.filter_by(project_id=project_id).one()
    assert "Attached Deliverable File: /static/uploads/" in delivery.completion_notes
    stored_name = delivery.completion_notes.rsplit("/", 1)[-1]
    with open(os.path.join(tmp_path, "uploads", stored_name), "rb") as saved_file:
        assert saved_file.read() == deliverable_archive


def test_upload_rejects_disallowed_file_types(
    client, db, factories, login, csrf_post, tmp_path, monkeypatch, app
):
    customer_id = factories.user("customer", "invalid-upload@example.com")
    login("invalid-upload@example.com", "customer")
    monkeypatch.setattr(app, "static_folder", str(tmp_path))

    response = csrf_post(
        "/start-project",
        data={
            "brief": "Build a customer portal",
            "srs_document": (BytesIO(b"not an allowed file"), "payload.exe"),
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert Project.query.filter_by(customer_id=customer_id).count() == 0
    assert not os.path.exists(os.path.join(tmp_path, "uploads"))


def test_upload_rejects_files_over_10_mb(
    app, client, db, factories, login, csrf_post, tmp_path, monkeypatch
):
    customer_id = factories.user("customer", "large-upload@example.com")
    login("large-upload@example.com", "customer")
    monkeypatch.setattr(app, "static_folder", str(tmp_path))

    response = csrf_post(
        "/start-project",
        data={
            "brief": "Build a customer portal",
            "srs_document": (
                BytesIO(b"x" * (10 * 1024 * 1024 + 1)),
                "requirements.pdf",
            ),
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert Project.query.filter_by(customer_id=customer_id).count() == 0
    
    # FIX: Safely check for the directory instead of blind os.listdir
    upload_dir = os.path.join(tmp_path, "uploads")
    if os.path.exists(upload_dir):
        assert os.listdir(upload_dir) == []


def test_landing_page_renders_business_faqs(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"Common Questions" in response.data
    assert b"How does the 2-Revision Guarantee work?" in response.data