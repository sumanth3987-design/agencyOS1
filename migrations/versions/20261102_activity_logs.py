"""add project activity audit logs

Revision ID: 20261102_activity_logs
Revises: 20261102_subscriptions
Create Date: 2026-11-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20261102_activity_logs"
down_revision = "20261102_subscriptions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "activity_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "category",
            sa.String(length=30),
            server_default="info",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["project.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_activity_logs_project_created_at",
        "activity_logs",
        ["project_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_activity_logs_user_id"),
        "activity_logs",
        ["user_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_activity_logs_user_id"), table_name="activity_logs")
    op.drop_index(
        "ix_activity_logs_project_created_at",
        table_name="activity_logs",
    )
    op.drop_table("activity_logs")
