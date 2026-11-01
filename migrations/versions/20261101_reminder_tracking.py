"""track sent payment and deliverable review reminders

Revision ID: 20261101_reminders
Revises: 20261101_addons
Create Date: 2026-11-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20261101_reminders"
down_revision = "20261101_addons"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("payment_reminder_sent_at", sa.DateTime(), nullable=True)
        )

    with op.batch_alter_table("project_delivery", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("review_reminder_sent_at", sa.DateTime(), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("project_delivery", schema=None) as batch_op:
        batch_op.drop_column("review_reminder_sent_at")

    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("payment_reminder_sent_at")
