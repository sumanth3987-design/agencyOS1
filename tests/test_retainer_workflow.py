"""Tests for the Care Plan Retainer & Task Attachments Engine."""

import pytest
from app.models import Project, RetainerTask, User
from app.extensions import db

class TestRetainerWorkflow:
    
    def test_client_drops_retainer_task_with_attachment(self, client, login, factories):
        """Test that a customer can drop a maintenance task and it queues correctly."""
        customer_id = factories.user("customer", "client@example.com")
        pro_id = factories.user("professional", "pro@example.com")
        
        # Create a project with active retainer hours
        project = Project(
            customer_id=customer_id,
            service="Website Maintenance",
            package="Starter Retainer",
            status="In Progress",
            assigned_professional_id=pro_id
        )
        db.session.add(project)
        db.session.commit()

        login("client@example.com", "customer")

        # Payload
        data = {
            "title": "Update Home Banner",
            "description": "Please replace the hero image with the new design."
        }
        
        # Post the task
        response = client.post(
            f"/project/{project.id}/drop-task",
            data=data,
            follow_redirects=True
        )
        
        assert response.status_code == 200
        
        # Check database
        task = RetainerTask.query.filter_by(project_id=project.id).first()
        assert task is not None
        assert task.title == "Update Home Banner"
        assert task.status == "Queued"

    def test_specialist_workspace_renders_retainer_tasks(self, client, login, factories):
        """Verify specialist sees queued tasks in their workspace."""
        customer_id = factories.user("customer", "client@example.com")
        pro_id = factories.user("professional", "pro@example.com")
        
        project = Project(
            customer_id=customer_id,
            service="Website Maintenance",
            package="Starter Retainer",
            status="In Progress",
            assigned_professional_id=pro_id
        )
        db.session.add(project)
        db.session.flush()
        
        task = RetainerTask(project_id=project.id, customer_id=customer_id, title="Test Task", description="Testing")
        db.session.add(task)
        db.session.commit()

        login("pro@example.com", "professional")
        
        response = client.get(f"/professional/project/{project.id}/workspace")
        assert response.status_code == 200
        assert b"Test Task" in response.data
        assert b"Claim Task" in response.data

    def test_task_claiming_process(self, client, login, factories):
        """Verify the state transition of a task when claimed."""
        customer_id = factories.user("customer", "client@example.com")
        pro_id = factories.user("professional", "pro@example.com")
        
        project = Project(customer_id=customer_id, package="Starter Retainer", status="In Progress", assigned_professional_id=pro_id)
        db.session.add(project)
        db.session.flush()
        
        task = RetainerTask(project_id=project.id, customer_id=customer_id, title="Claimable Task", description="Test")
        db.session.add(task)
        db.session.commit()

        login("pro@example.com", "professional")
        
        # Claim it
        response = client.post(
            f"/professional/project/{project.id}/task/{task.id}/claim",
            follow_redirects=True
        )
        
        assert response.status_code == 200
        task_db = db.session.get(RetainerTask, task.id)
        assert task_db.status == "In Progress"
        assert task_db.specialist_id == pro_id