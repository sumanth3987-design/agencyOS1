from datetime import datetime, timedelta
import re

from app.models import (
    Milestone,
    ProfessionalProfile,
    Project,
    ProjectDelivery,
    ProfessionalProjectApplication,
    Review,
)


def _create_assigned_project(db, factories, customer_id, professional_id, status):
    project_id = factories.project(customer_id, status=status)
    factories.application(project_id, professional_id, status="Assigned")
    return project_id


def test_admin_roster_shows_evidence_based_metrics_and_badges(
    app, client, db, factories, login
):
    customer_id = factories.customer("meritocracy-client@example.com")
    professional_id = factories.professional("meritocracy-pro@example.com")
    profile_id = factories.profile(
        professional_id,
        verification_status="Verified",
    )
    project_id = _create_assigned_project(
        db, factories, customer_id, professional_id, "Completed"
    )
    submitted_at = datetime.now()
    db.session.add_all(
        (
            ProjectDelivery(
                project_id=project_id,
                professional_id=professional_id,
                submission_number=1,
                completion_notes="Delivered early",
                status="Approved",
                submitted_at=submitted_at,
                reviewed_at=submitted_at,
            ),
            Milestone(
                project_id=project_id,
                title="Final delivery deadline",
                amount=1000,
                due_date=submitted_at + timedelta(days=1),
            ),
            Review(
                project_id=project_id,
                customer_id=customer_id,
                rating=5,
                feedback="Excellent work",
            ),
        )
    )
    db.session.commit()

    profile = db.session.get(ProfessionalProfile, profile_id)
    metrics = profile.get_performance_metrics()
    badge_names = {badge["name"] for badge in profile.get_badges(metrics)}
    assert metrics == {
        "completed_sprints": 1,
        "active_sprints": 0,
        "completion_rate": 100,
        "on_time_rate": 100,
        "average_rating": 5,
        "revision_count": 0,
        "early_sprints": 1,
    }
    assert {
        "Top Rated 🌟",
        "Speed Demon ⚡",
        "Reliable 🎯",
        "Verified Pro 🛡️",
    } == badge_names

    admin_id = factories.admin("meritocracy-admin@example.com")
    login("meritocracy-admin@example.com", "admin")
    response = client.get("/admin/professionals")
    rendered = re.sub(r"\s+", " ", response.data.decode("utf-8"))

    assert response.status_code == 200
    assert "Top Rated" in rendered
    assert "Speed Demon" in rendered
    assert "Reliable" in rendered
    assert "1 completed" in rendered
    assert "On-time: 100%" in rendered
    assert "Rating: 5.0/5" in rendered
    assert "Completion: 100%" in rendered


def test_missing_deadlines_and_reviews_are_not_invented_and_revision_blocks_top_rated(
    db, factories
):
    customer_id = factories.customer("meritocracy-no-data@example.com")
    professional_id = factories.professional("meritocracy-no-data-pro@example.com")
    profile_id = factories.profile(
        professional_id,
        verification_status="Verified",
    )
    completed_id = _create_assigned_project(
        db, factories, customer_id, professional_id, "Completed"
    )
    active_id = _create_assigned_project(
        db, factories, customer_id, professional_id, "In Progress"
    )
    db.session.add_all(
        (
            ProjectDelivery(
                project_id=completed_id,
                professional_id=professional_id,
                submission_number=1,
                completion_notes="Revision required",
                status="Revision",
                submitted_at=datetime.now(),
            ),
            ProjectDelivery(
                project_id=active_id,
                professional_id=professional_id,
                submission_number=1,
                completion_notes="Work in progress",
                status="Submitted",
                submitted_at=datetime.now(),
            ),
        )
    )
    db.session.commit()

    profile = db.session.get(ProfessionalProfile, profile_id)
    metrics = profile.get_performance_metrics()
    badge_names = {badge["name"] for badge in profile.get_badges(metrics)}
    assert metrics["completed_sprints"] == 1
    assert metrics["active_sprints"] == 1
    assert metrics["completion_rate"] == 50
    assert metrics["on_time_rate"] is None
    assert metrics["average_rating"] is None
    assert metrics["revision_count"] == 1
    assert "Top Rated 🌟" not in badge_names
    assert "Speed Demon ⚡" not in badge_names
    assert "Reliable 🎯" not in badge_names

    assert db.session.get(Project, active_id).status == "In Progress"


def test_review_score_outside_five_star_range_is_rejected(
    app, client, factories, login, csrf_post
):
    customer_id = factories.customer("meritocracy-rating-client@example.com")
    project_id = factories.project(customer_id, status="Completed")
    login("meritocracy-rating-client@example.com", "customer")

    response = csrf_post(
        f"/project/{project_id}/review",
        data={"rating": "99", "feedback": "Invalid score"},
    )

    assert response.status_code == 302
    with app.app_context():
        assert Review.query.filter_by(project_id=project_id).count() == 0
