"""add persistent agency branding settings

Revision ID: 20261101_agency_settings
Revises: 20261101_reminders
Create Date: 2026-11-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20261101_agency_settings"
down_revision = "20261101_reminders"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agency_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "agency_name",
            sa.String(length=120),
            server_default="AgencyOS",
            nullable=False,
        ),
        sa.Column(
            "primary_color",
            sa.String(length=7),
            server_default="#6366f1",
            nullable=False,
        ),
        sa.Column("logo_url", sa.String(length=500), nullable=True),
        sa.Column(
            "whatsapp_support_number",
            sa.String(length=16),
            server_default="+919876543210",
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table("agency_settings")
