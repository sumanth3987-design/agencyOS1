"""Client notification center API and rendering coverage."""

from app.extensions import db
from app.models import Notification


def _notification(user_id, **kwargs):
    values = {
        "title": "Milestone update",
        "message": "A new project milestone is ready to review.",
        "link": "/project/123",
    }
    values.update(kwargs)
    notification = Notification(user_id=user_id, **values)
    db.session.add(notification)
    db.session.commit()
    return notification.id


def test_notification_feed_reports_unread_count_and_safe_links(
    app, client, login, factories
):
    user_id = factories.customer("notification-feed@example.com")
    login("notification-feed@example.com", "customer")
    for index in range(11):
        _notification(user_id, title=f"Update {index}")
    _notification(user_id, is_read=True, link="https://attacker.example/")

    response = client.get("/api/notifications")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["unread_count"] == 11
    assert payload["count"] == 11
    assert len(payload["notifications"]) == 10
    assert all(item["link"].startswith("/") for item in payload["notifications"])
    assert all("attacker.example" not in item["link"] for item in payload["notifications"])


def test_mark_read_is_owner_scoped_and_missing_ids_are_explicit(
    app, client, login, factories, csrf_post
):
    owner_id = factories.customer("notification-owner@example.com")
    other_id = factories.customer("notification-other@example.com")
    owned_notification_id = _notification(owner_id)
    other_notification_id = _notification(other_id)
    login("notification-owner@example.com", "customer")

    forbidden = csrf_post(f"/api/notifications/{other_notification_id}/read")
    missing = csrf_post("/api/notifications/999999/read")
    marked = csrf_post(f"/api/notifications/{owned_notification_id}/read")

    assert forbidden.status_code == 404
    assert forbidden.get_json() == {
        "success": False,
        "error": "Notification not found.",
    }
    assert missing.status_code == 404
    assert marked.status_code == 200
    with app.app_context():
        assert db.session.get(Notification, owned_notification_id).is_read is True
        assert db.session.get(Notification, other_notification_id).is_read is False


def test_clear_all_marks_only_current_users_notifications_read(
    app, client, login, factories, csrf_token
):
    owner_id = factories.customer("notification-clear@example.com")
    other_id = factories.customer("notification-clear-other@example.com")
    first_id = _notification(owner_id)
    second_id = _notification(owner_id)
    already_read_id = _notification(owner_id, is_read=True)
    other_id_value = _notification(other_id)
    login("notification-clear@example.com", "customer")

    response = client.post(
        "/api/notifications/clear-all",
        headers={"X-CSRFToken": csrf_token},
    )

    assert response.status_code == 200
    assert response.get_json() == {"success": True, "updated_count": 2, "count": 0}
    with app.app_context():
        assert db.session.get(Notification, first_id).is_read is True
        assert db.session.get(Notification, second_id).is_read is True
        assert db.session.get(Notification, already_read_id) is not None
        assert db.session.get(Notification, other_id_value).is_read is False
        assert Notification.query.filter_by(user_id=owner_id).count() == 3


def test_notification_mutations_require_csrf(client, login, factories):
    factories.customer("notification-csrf@example.com")
    login("notification-csrf@example.com", "customer")

    response = client.post("/api/notifications/clear-all")

    assert response.status_code == 400


def test_notification_center_is_rendered_in_both_client_workspaces(
    client, login, factories
):
    customer_id = factories.customer("notification-render@example.com")
    project_id = factories.project(customer_id)
    login("notification-render@example.com", "customer")

    dashboard = client.get("/dashboard")
    project_page = client.get(f"/project/{project_id}")

    assert dashboard.status_code == 200
    assert project_page.status_code == 200
    for response in (dashboard, project_page):
        assert b'aria-label="Notifications"' in response.data
        assert b'aria-haspopup="dialog"' in response.data
        assert b"Clear all" in response.data
        assert b"/api/notifications/clear-all" in response.data
