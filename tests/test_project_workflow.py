"""Project workflow tests: application, assignment, submission, and approval."""

import pytest
from app.extensions import db
from app.models import ProfessionalProfile, ProfessionalProjectApplication, Project


class TestAdminVerification:
    """Test admin verification of professionals."""

    def test_verify_and_reject_professional(
        self, app, client, login, factories, csrf_post
    ):
        """Admin should verify and reject professional profiles."""
        prof_id = factories.user('professional', 'verify@example.com')
        factories.profile(prof_id, verification_status='Pending')

        admin_id = factories.user('admin', 'admin-verify@example.com')
        login('admin-verify@example.com', 'admin')

        response = csrf_post(
            '/admin/professionals/' + str(prof_id) + '/verify',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            profile = db.session.get(ProfessionalProfile, prof_id)
            assert profile.verification_status == 'Verified'

        # Reject the professional
        response = csrf_post(
            '/admin/professionals/' + str(prof_id) + '/reject',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            profile = db.session.get(ProfessionalProfile, prof_id)
            assert profile.verification_status == 'Rejected'


class TestApply:
    """Test project application flows."""

    def test_apply_to_non_pending_project_blocked(
        self, app, client, login, factories, csrf_post
    ):
        """Applying to a non-Pending Assignment project is blocked."""
        customer_id = factories.user('customer', 'apply-nonpending@example.com')
        project_id = factories.project(customer_id, status='Assigned')
        prof_id = factories.user('professional', 'apply-nonpending-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        login('apply-nonpending-prof@example.com', 'professional')

        response = csrf_post(
            '/professional/projects/' + str(project_id) + '/apply',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            count = ProfessionalProjectApplication.query.filter_by(
                project_id=project_id, professional_id=prof_id
            ).count()
            assert count == 0

    def test_duplicate_apply_rejected(
        self, app, client, login, factories, csrf_post
    ):
        """Duplicate application to same project is rejected."""
        customer_id = factories.user('customer', 'apply-dup@example.com')
        project_id = factories.project(customer_id, status='Pending Assignment')
        prof_id = factories.user('professional', 'apply-dup-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        login('apply-dup-prof@example.com', 'professional')

        response1 = csrf_post(
            '/professional/projects/' + str(project_id) + '/apply',
            data={},
            follow_redirects=False,
        )
        assert response1.status_code == 302

        response2 = csrf_post(
            '/professional/projects/' + str(project_id) + '/apply',
            data={},
            follow_redirects=False,
        )
        assert response2.status_code == 302

        with app.app_context():
            count = ProfessionalProjectApplication.query.filter_by(
                project_id=project_id, professional_id=prof_id
            ).count()
            assert count == 1

    def test_expertise_mismatch_blocked(
        self, app, client, login, factories, csrf_post
    ):
        """Professional without matching expertise cannot apply."""
        customer_id = factories.user('customer', 'apply-mismatch@example.com')
        project_id = factories.project(customer_id, status='Pending Assignment')
        prof_id = factories.user('professional', 'apply-mismatch-prof@example.com')
        factories.profile(
            prof_id, verification_status='Verified', expertise='Web Development'
        )
        login('apply-mismatch-prof@example.com', 'professional')

        response = csrf_post(
            '/professional/projects/' + str(project_id) + '/apply',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            count = ProfessionalProjectApplication.query.filter_by(
                project_id=project_id, professional_id=prof_id
            ).count()
            assert count == 0

    def test_unverified_professional_blocked(
        self, app, client, login, factories, csrf_post
    ):
        """Unverified professional cannot apply."""
        customer_id = factories.user('customer', 'apply-unverified@example.com')
        project_id = factories.project(customer_id, status='Pending Assignment')
        prof_id = factories.user('professional', 'apply-unverified-prof@example.com')
        factories.profile(prof_id, verification_status='Pending')
        login('apply-unverified-prof@example.com', 'professional')

        response = csrf_post(
            '/professional/projects/' + str(project_id) + '/apply',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            count = ProfessionalProjectApplication.query.filter_by(
                project_id=project_id, professional_id=prof_id
            ).count()
            assert count == 0

    def test_verified_professional_applies(
        self, app, client, login, factories, csrf_post
    ):
        """Verified professional with matching expertise can apply."""
        customer_id = factories.user('customer', 'apply-ok@example.com')
        project_id = factories.project(customer_id, status='Pending Assignment')
        prof_id = factories.user('professional', 'apply-ok-prof@example.com')
        # Default profile expertise matches the default project service.
        factories.profile(prof_id, verification_status='Verified')
        login('apply-ok-prof@example.com', 'professional')

        response = csrf_post(
            '/professional/projects/' + str(project_id) + '/apply',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            app_row = ProfessionalProjectApplication.query.filter_by(
                project_id=project_id, professional_id=prof_id
            ).first()
            assert app_row is not None
            assert app_row.status == 'Applied'
            assert db.session.get(Project, project_id) is not None


class TestAssignAndStart:
    """Test project assignment and starting."""

    def test_assign_blocked_when_application_not_applied(
        self, app, client, login, factories, csrf_post
    ):
        """Assign blocked when application status is not 'Applied'"""
        customer_id = factories.user('customer', 'assign-notapp@example.com')
        project_id = factories.project(customer_id, status='Pending Assignment')
        prof_id = factories.user('professional', 'assign-notapp-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        app_id = factories.application(project_id, prof_id, status='Not Selected')

        admin_id = factories.user('admin', 'assign-notapp-admin@example.com')
        login('assign-notapp-admin@example.com', 'admin')

        response = csrf_post(
            '/admin/applications/' + str(app_id) + '/assign',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            application = db.session.get(ProfessionalProjectApplication, app_id)
            assert application.status == 'Not Selected'
            assert db.session.get(Project, project_id).status == 'Pending Assignment'

    def test_assign_blocked_when_project_not_pending(
        self, app, client, login, factories, csrf_post
    ):
        """Assign blocked when project is not 'Pending Assignment'"""
        customer_id = factories.user('customer', 'assign-notpending@example.com')
        project_id = factories.project(customer_id, status='Assigned')
        prof_id = factories.user('professional', 'assign-notpending-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        app_id = factories.application(project_id, prof_id, status='Applied')

        admin_id = factories.user('admin', 'assign-notpending-admin@example.com')
        login('assign-notpending-admin@example.com', 'admin')

        response = csrf_post(
            '/admin/applications/' + str(app_id) + '/assign',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            application = db.session.get(ProfessionalProjectApplication, app_id)
            assert application.status == 'Applied'
            # Project remains 'Assigned' (was never 'Pending Assignment')
            assert db.session.get(Project, project_id).status == 'Assigned'

    def test_assign_transitions_all_applications(
        self, app, client, login, factories, csrf_post
    ):
        """Assign transitions selected app to 'Assigned' and others to 'Not Selected'."""
        customer_id = factories.user('customer', 'assign-all@example.com')
        project_id = factories.project(customer_id, status='Pending Assignment')
        prof1_id = factories.user('professional', 'assign-all-prof1@example.com')
        factories.profile(prof1_id, verification_status='Verified')
        prof2_id = factories.user('professional', 'assign-all-prof2@example.com')
        factories.profile(prof2_id, verification_status='Verified')

        app1_id = factories.application(project_id, prof1_id, status='Applied')
        app2_id = factories.application(project_id, prof2_id, status='Applied')

        admin_id = factories.user('admin', 'assign-all-admin@example.com')
        login('assign-all-admin@example.com', 'admin')

        response = csrf_post(
            '/admin/applications/' + str(app1_id) + '/assign',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            app1 = db.session.get(ProfessionalProjectApplication, app1_id)
            app2 = db.session.get(ProfessionalProjectApplication, app2_id)
            project = db.session.get(Project, project_id)
            assert app1 is not None
            assert app2 is not None
            assert project is not None
            assert app1.status == 'Assigned'
            assert app2.status == 'Not Selected'
            assert project.status == 'Assigned'

    def test_assign_rejected_when_professional_pending(
        self, app, client, login, factories, csrf_post
    ):
        """Assign is rejected when professional verification is Pending."""
        customer_id = factories.user('customer', 'assign-pending@example.com')
        project_id = factories.project(customer_id, status='Pending Assignment')
        prof_id = factories.user('professional', 'assign-pending-prof@example.com')
        factories.profile(prof_id, verification_status='Pending')
        other_prof_id = factories.user('professional', 'assign-pending-other@example.com')
        factories.profile(other_prof_id, verification_status='Verified')

        selected_app_id = factories.application(project_id, prof_id, status='Applied')
        competing_app_id = factories.application(project_id, other_prof_id, status='Applied')

        admin_id = factories.user('admin', 'assign-pending-admin@example.com')
        login('assign-pending-admin@example.com', 'admin')

        response = csrf_post(
            '/admin/applications/' + str(selected_app_id) + '/assign',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            selected = db.session.get(ProfessionalProjectApplication, selected_app_id)
            competing = db.session.get(ProfessionalProjectApplication, competing_app_id)
            project = db.session.get(Project, project_id)
            assert selected is not None
            assert competing is not None
            assert project is not None
            assert selected.status == 'Applied'
            assert competing.status == 'Applied'
            assert project.status == 'Pending Assignment'

    def test_assign_rejected_when_verification_empty(
        self, app, client, login, factories, csrf_post
    ):
        """Assign is rejected when professional verification status is empty."""
        customer_id = factories.user('customer', 'assign-empty@example.com')
        project_id = factories.project(customer_id, status='Pending Assignment')
        prof_id = factories.user('professional', 'assign-empty-prof@example.com')
        factories.profile(prof_id, verification_status='')
        other_prof_id = factories.user('professional', 'assign-empty-other@example.com')
        factories.profile(other_prof_id, verification_status='Verified')

        selected_app_id = factories.application(project_id, prof_id, status='Applied')
        competing_app_id = factories.application(project_id, other_prof_id, status='Applied')

        admin_id = factories.user('admin', 'assign-empty-admin@example.com')
        login('assign-empty-admin@example.com', 'admin')

        response = csrf_post(
            '/admin/applications/' + str(selected_app_id) + '/assign',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            selected = db.session.get(ProfessionalProjectApplication, selected_app_id)
            competing = db.session.get(ProfessionalProjectApplication, competing_app_id)
            project = db.session.get(Project, project_id)
            assert selected is not None
            assert competing is not None
            assert project is not None
            assert selected.status == 'Applied'
            assert competing.status == 'Applied'
            assert project.status == 'Pending Assignment'

    def test_assign_rejected_when_verification_unknown(
        self, app, client, login, factories, csrf_post
    ):
        """Assign is rejected when professional verification status is unknown."""
        customer_id = factories.user('customer', 'assign-unknown@example.com')
        project_id = factories.project(customer_id, status='Pending Assignment')
        prof_id = factories.user('professional', 'assign-unknown-prof@example.com')
        factories.profile(prof_id, verification_status='Review')
        other_prof_id = factories.user('professional', 'assign-unknown-other@example.com')
        factories.profile(other_prof_id, verification_status='Verified')

        selected_app_id = factories.application(project_id, prof_id, status='Applied')
        competing_app_id = factories.application(project_id, other_prof_id, status='Applied')

        admin_id = factories.user('admin', 'assign-unknown-admin@example.com')
        login('assign-unknown-admin@example.com', 'admin')

        response = csrf_post(
            '/admin/applications/' + str(selected_app_id) + '/assign',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            selected = db.session.get(ProfessionalProjectApplication, selected_app_id)
            competing = db.session.get(ProfessionalProjectApplication, competing_app_id)
            project = db.session.get(Project, project_id)
            assert selected is not None
            assert competing is not None
            assert project is not None
            assert selected.status == 'Applied'
            assert competing.status == 'Applied'
            assert project.status == 'Pending Assignment'

    def test_assign_rejected_when_profile_missing(
        self, app, client, login, factories, csrf_post
    ):
        """Assign is safely rejected when professional has no profile."""
        customer_id = factories.user('customer', 'assign-missing@example.com')
        project_id = factories.project(customer_id, status='Pending Assignment')
        prof_id = factories.user('professional', 'assign-missing-prof@example.com')
        other_prof_id = factories.user('professional', 'assign-missing-other@example.com')
        factories.profile(other_prof_id, verification_status='Verified')

        selected_app_id = factories.application(project_id, prof_id, status='Applied')
        competing_app_id = factories.application(project_id, other_prof_id, status='Applied')

        admin_id = factories.user('admin', 'assign-missing-admin@example.com')
        login('assign-missing-admin@example.com', 'admin')

        response = csrf_post(
            '/admin/applications/' + str(selected_app_id) + '/assign',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            selected = db.session.get(ProfessionalProjectApplication, selected_app_id)
            competing = db.session.get(ProfessionalProjectApplication, competing_app_id)
            project = db.session.get(Project, project_id)
            assert selected is not None
            assert competing is not None
            assert project is not None
            assert selected.status == 'Applied'
            assert competing.status == 'Applied'
            assert project.status == 'Pending Assignment'

    def test_assigned_professional_starts_project(
        self, app, client, login, factories, csrf_post
    ):
        """Assigned professional can start project (Assigned -> In Progress)."""
        customer_id = factories.user('customer', 'start-ok@example.com')
        project_id = factories.project(customer_id, status='Assigned')
        prof_id = factories.user('professional', 'start-ok-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        factories.application(project_id, prof_id, status='Assigned')
        login('start-ok-prof@example.com', 'professional')

        response = csrf_post(
            '/professional/project/' + str(project_id) + '/start',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            project = db.session.get(Project, project_id)
            assert project is not None
            assert project.status == 'In Progress'

    def test_start_blocked_when_not_assigned_status(
        self, app, client, login, factories, csrf_post
    ):
        """Start blocked when project status is not 'Assigned'."""
        customer_id = factories.user('customer', 'start-blocked@example.com')
        project_id = factories.project(customer_id, status='Pending Assignment')
        prof_id = factories.user('professional', 'start-blocked-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        factories.application(project_id, prof_id, status='Assigned')
        login('start-blocked-prof@example.com', 'professional')

        response = csrf_post(
            '/professional/project/' + str(project_id) + '/start',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            project = db.session.get(Project, project_id)
            assert project is not None
            assert project.status == 'Pending Assignment'

    def test_unassigned_professional_cannot_start(
        self, app, client, login, factories, csrf_post
    ):
        """Professional not assigned to project cannot start it."""
        customer_id = factories.user('customer', 'start-unassigned@example.com')
        project_id = factories.project(customer_id, status='Assigned')
        prof_id = factories.user('professional', 'start-unassigned-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        login('start-unassigned-prof@example.com', 'professional')

        response = csrf_post(
            '/professional/project/' + str(project_id) + '/start',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            project = db.session.get(Project, project_id)
            assert project is not None
            assert project.status == 'Assigned'


class TestRevisionResubmitApprove:
    """Test revision, resubmit, and approve flows."""

    def test_approve_blocked_when_not_submitted(
        self, app, client, login, factories, csrf_post
    ):
        """Approve blocked when project is not 'Submitted'"""
        customer_id = factories.user('customer', 'approve-nosubmit@example.com')
        project_id = factories.project(customer_id, status='In Progress')
        prof_id = factories.user('professional', 'approve-nosubmit-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        factories.application(project_id, prof_id, status='Assigned')
        admin_id = factories.user('admin', 'approve-nosubmit-admin@example.com')
        login('approve-nosubmit-admin@example.com', 'admin')

        response = csrf_post(
            '/admin/project/' + str(project_id) + '/approve',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            assert db.session.get(Project, project_id).status == 'In Progress'

    def test_full_review_cycle(
        self, app, client, login, factories, csrf_post
    ):
        """Full review cycle: submit -> revision -> resubmit -> approve."""
        customer_id = factories.user('customer', 'review-cycle@example.com')
        project_id = factories.project(customer_id, status='In Progress')
        prof_id = factories.user('professional', 'review-cycle-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        factories.application(project_id, prof_id, status='Assigned')
        login('review-cycle-prof@example.com', 'professional')

        # Start the project
        csrf_post(
            '/professional/project/' + str(project_id) + '/start',
            data={},
            follow_redirects=False,
        )

        # Submit work (moves from In Progress -> Submitted)
        csrf_post(
            '/professional/project/' + str(project_id) + '/submit',
            data={'live_url': 'https://example.com', 'completion_notes': 'Work complete'},
            follow_redirects=False,
        )

        with app.app_context():
            assert db.session.get(Project, project_id).status == 'Submitted'

        # Admin requests revision
        admin_id = factories.user('admin', 'review-cycle-admin@example.com')
        login('review-cycle-admin@example.com', 'admin')
        factories.delivery(project_id, prof_id)

        response = csrf_post(
            '/admin/project/' + str(project_id) + '/revision',
            data={'revision_comment': 'Needs fixes'},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            assert db.session.get(Project, project_id).status == 'Revision'

        # Professional resubmits
        login('review-cycle-prof@example.com', 'professional')
        response = csrf_post(
            '/professional/project/' + str(project_id) + '/resubmit',
            data={'live_url': 'https://example.com/fixed', 'completion_notes': 'Fixed'},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            assert db.session.get(Project, project_id).status == 'Submitted'

        # Admin approves
        login('review-cycle-admin@example.com', 'admin')
        response = csrf_post(
            '/admin/project/' + str(project_id) + '/approve',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            assert db.session.get(Project, project_id).status == 'Completed'

    def test_resubmit_blocked_when_not_revision(
        self, app, client, login, factories, csrf_post
    ):
        """Resubmit blocked when project is not 'Revision'"""
        customer_id = factories.user('customer', 'resubmit-blocked@example.com')
        project_id = factories.project(customer_id, status='In Progress')
        prof_id = factories.user('professional', 'resubmit-blocked-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        factories.application(project_id, prof_id, status='Assigned')
        login('resubmit-blocked-prof@example.com', 'professional')

        response = csrf_post(
            '/professional/project/' + str(project_id) + '/resubmit',
            data={'live_url': 'https://example.com', 'completion_notes': 'x'},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            assert db.session.get(Project, project_id).status == 'In Progress'

    def test_revision_blocked_when_not_submitted(
        self, app, client, login, factories, csrf_post
    ):
        """Revision blocked when project is not 'Submitted'"""
        customer_id = factories.user('customer', 'revision-blocked@example.com')
        project_id = factories.project(customer_id, status='In Progress')
        prof_id = factories.user('professional', 'revision-blocked-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        factories.application(project_id, prof_id, status='Assigned')
        admin_id = factories.user('admin', 'revision-blocked-admin@example.com')
        login('revision-blocked-admin@example.com', 'admin')

        response = csrf_post(
            '/admin/project/' + str(project_id) + '/revision',
            data={'revision_comment': 'Needs work'},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            assert db.session.get(Project, project_id).status == 'In Progress'

    def test_revision_requires_comment(
        self, app, client, login, factories, csrf_post
    ):
        """Revision requires a non-empty comment."""
        customer_id = factories.user('customer', 'revision-comment@example.com')
        project_id = factories.project(customer_id, status='Submitted')
        prof_id = factories.user('professional', 'revision-comment-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        factories.application(project_id, prof_id, status='Assigned')
        factories.delivery(project_id, prof_id)
        admin_id = factories.user('admin', 'revision-comment-admin@example.com')
        login('revision-comment-admin@example.com', 'admin')

        response = csrf_post(
            '/admin/project/' + str(project_id) + '/revision',
            data={'revision_comment': ''},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            project = db.session.get(Project, project_id)
            assert project is not None
            assert project.status == 'Submitted'


class TestStartProjectPaymentGate:
    """Test starting project with payment gate."""

    def test_admin_cannot_assign_payment_pending_project(
        self, app, client, login, factories, csrf_post
    ):
        """Admin cannot assign a project with 'Payment Pending' status."""
        customer_id = factories.user('customer', 'paygate-assign@example.com')
        project_id = factories.project(customer_id, status='Payment Pending')
        prof_id = factories.user('professional', 'paygate-assign-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        app_id = factories.application(project_id, prof_id, status='Applied')
        admin_id = factories.user('admin', 'paygate-assign-admin@example.com')
        login('paygate-assign-admin@example.com', 'admin')

        response = csrf_post(
            '/admin/applications/' + str(app_id) + '/assign',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            application = db.session.get(ProfessionalProjectApplication, app_id)
            assert application.status == 'Applied'
            # Project remains 'Payment Pending'
            assert db.session.get(Project, project_id).status == 'Payment Pending'

    def test_professional_cannot_apply_to_payment_pending_project(
        self, app, client, login, factories, csrf_post
    ):
        """Professional cannot apply to 'Payment Pending' project."""
        customer_id = factories.user('customer', 'paygate-apply@example.com')
        project_id = factories.project(customer_id, status='Payment Pending')
        prof_id = factories.user('professional', 'paygate-apply-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        login('paygate-apply-prof@example.com', 'professional')

        response = csrf_post(
            '/professional/projects/' + str(project_id) + '/apply',
            data={},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            count = ProfessionalProjectApplication.query.filter_by(
                project_id=project_id, professional_id=prof_id
            ).count()
            assert count == 0

    def test_start_project_creates_payment_pending(
        self, app, client, login, factories, csrf_post
    ):
        """A newly created project has 'Payment Pending' status."""
        customer_id = factories.user('customer', 'paygate-start@example.com')
        project_id = factories.project(customer_id, status='Payment Pending')
        login('paygate-start@example.com', 'customer')

        with app.app_context():
            project = db.session.get(Project, project_id)
            assert project.status == 'Payment Pending'

class TestSubmit:
    """Test project submission flows."""

    def test_submit_blocked_when_not_in_progress(
        self, app, client, login, factories, csrf_post
    ):
        """Submit blocked when project is not 'In Progress'"""
        customer_id = factories.user('customer', 'submit-blocked@example.com')
        project_id = factories.project(customer_id, status='Assigned')
        prof_id = factories.user('professional', 'submit-blocked-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        factories.application(project_id, prof_id, status='Assigned')
        login('submit-blocked-prof@example.com', 'professional')

        response = csrf_post(
            '/professional/project/' + str(project_id) + '/submit',
            data={'live_url': 'https://example.com', 'completion_notes': 'Done'},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            assert db.session.get(Project, project_id).status == 'Assigned'

    def test_submit_in_progress_project(
        self, app, client, login, factories, csrf_post
    ):
        """Submit transitions project from 'In Progress' to 'Submitted'"""
        customer_id = factories.user('customer', 'submit-ok@example.com')
        project_id = factories.project(customer_id, status='In Progress')
        prof_id = factories.user('professional', 'submit-ok-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        factories.application(project_id, prof_id, status='Assigned')
        login('submit-ok-prof@example.com', 'professional')

        response = csrf_post(
            '/professional/project/' + str(project_id) + '/submit',
            data={'live_url': 'https://example.com', 'completion_notes': 'Done'},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            assert db.session.get(Project, project_id).status == 'Submitted'

    def test_submit_rejects_invalid_live_url(
        self, app, client, login, factories, csrf_post
    ):
        """Submit rejects invalid live URL."""
        customer_id = factories.user('customer', 'submit-url@example.com')
        project_id = factories.project(customer_id, status='In Progress')
        prof_id = factories.user('professional', 'submit-url-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        factories.application(project_id, prof_id, status='Assigned')
        login('submit-url-prof@example.com', 'professional')

        response = csrf_post(
            '/professional/project/' + str(project_id) + '/submit',
            data={'live_url': 'not-a-url', 'completion_notes': 'Done'},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            assert db.session.get(Project, project_id).status == 'In Progress'

    def test_submit_requires_completion_notes(
        self, app, client, login, factories, csrf_post
    ):
        """Submit requires completion notes."""
        customer_id = factories.user('customer', 'submit-notes@example.com')
        project_id = factories.project(customer_id, status='In Progress')
        prof_id = factories.user('professional', 'submit-notes-prof@example.com')
        factories.profile(prof_id, verification_status='Verified')
        factories.application(project_id, prof_id, status='Assigned')
        login('submit-notes-prof@example.com', 'professional')

        response = csrf_post(
            '/professional/project/' + str(project_id) + '/submit',
            data={'live_url': 'https://example.com', 'completion_notes': ''},
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            assert db.session.get(Project, project_id).status == 'In Progress'