"""add Razorpay recurring subscription tracking

Revision ID: 20261102_subscriptions
Revises: 20261101_agency_settings
Create Date: 2026-11-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20261102_subscriptions"
down_revision = "20261101_agency_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "razorpay_subscription_plan",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("razorpay_plan_id", sa.String(length=100), nullable=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=10),
            server_default="INR",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.UniqueConstraint("razorpay_plan_id"),
    )
    op.create_table(
        "razorpay_subscription",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("razorpay_subscription_id", sa.String(length=100), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=10),
            server_default="INR",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="created",
            nullable=False,
        ),
        sa.Column("start_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["user.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["razorpay_subscription_plan.id"]
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["project.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("razorpay_subscription_id"),
    )
    op.create_index(
        "ix_razorpay_subscription_project_id",
        "razorpay_subscription",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_razorpay_subscription_customer_id",
        "razorpay_subscription",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "uq_razorpay_subscription_project_open",
        "razorpay_subscription",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('initializing', 'created', 'authenticated', 'active', "
            "'pending', 'halted', 'paused', 'resumed')"
        ),
        sqlite_where=sa.text(
            "status IN ('initializing', 'created', 'authenticated', 'active', "
            "'pending', 'halted', 'paused', 'resumed')"
        ),
    )
    op.create_table(
        "subscription_charge",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("razorpay_payment_id", sa.String(length=100), nullable=False),
        sa.Column("event_id", sa.String(length=100), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=10),
            server_default="INR",
            nullable=False,
        ),
        sa.Column(
            "charged_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoice.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"], ["payment.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["razorpay_subscription.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint("invoice_id"),
        sa.UniqueConstraint("payment_id"),
        sa.UniqueConstraint("razorpay_payment_id"),
    )
    op.create_index(
        "ix_subscription_charge_subscription_id",
        "subscription_charge",
        ["subscription_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_subscription_charge_subscription_id",
        table_name="subscription_charge",
    )
    op.drop_table("subscription_charge")
    op.drop_index(
        "uq_razorpay_subscription_project_open",
        table_name="razorpay_subscription",
    )
    op.drop_index(
        "ix_razorpay_subscription_customer_id",
        table_name="razorpay_subscription",
    )
    op.drop_index(
        "ix_razorpay_subscription_project_id",
        table_name="razorpay_subscription",
    )
    op.drop_table("razorpay_subscription")
    op.drop_table("razorpay_subscription_plan")
