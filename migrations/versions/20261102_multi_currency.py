"""add project currencies and precise multi-currency amounts

Revision ID: 20261102_multi_currency
Revises: 20261102_activity_logs
Create Date: 2026-11-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20261102_multi_currency"
down_revision = "20261102_activity_logs"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(
            sa.Column(
                "currency",
                sa.String(length=3),
                server_default="INR",
                nullable=False,
            )
        )

    with op.batch_alter_table("payment") as batch_op:
        batch_op.alter_column(
            "amount",
            existing_type=sa.Integer(),
            type_=sa.Numeric(12, 2),
            existing_nullable=False,
        )

    with op.batch_alter_table("invoice") as batch_op:
        batch_op.alter_column(
            "gross_amount",
            existing_type=sa.Integer(),
            type_=sa.Numeric(12, 2),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "talent_payout",
            existing_type=sa.Integer(),
            type_=sa.Numeric(12, 2),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "agency_profit",
            existing_type=sa.Integer(),
            type_=sa.Numeric(12, 2),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "tax_amount",
            existing_type=sa.Integer(),
            type_=sa.Numeric(12, 2),
            existing_nullable=False,
        )
        batch_op.add_column(
            sa.Column(
                "currency",
                sa.String(length=3),
                server_default="INR",
                nullable=False,
            )
        )

    with op.batch_alter_table("project_add_ons") as batch_op:
        batch_op.alter_column(
            "amount",
            existing_type=sa.Integer(),
            type_=sa.Numeric(12, 2),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table("project_add_ons") as batch_op:
        batch_op.alter_column(
            "amount",
            existing_type=sa.Numeric(12, 2),
            type_=sa.Integer(),
            existing_nullable=False,
        )

    with op.batch_alter_table("invoice") as batch_op:
        batch_op.drop_column("currency")
        batch_op.alter_column(
            "tax_amount",
            existing_type=sa.Numeric(12, 2),
            type_=sa.Integer(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "agency_profit",
            existing_type=sa.Numeric(12, 2),
            type_=sa.Integer(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "talent_payout",
            existing_type=sa.Numeric(12, 2),
            type_=sa.Integer(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "gross_amount",
            existing_type=sa.Numeric(12, 2),
            type_=sa.Integer(),
            existing_nullable=False,
        )

    with op.batch_alter_table("payment") as batch_op:
        batch_op.alter_column(
            "amount",
            existing_type=sa.Numeric(12, 2),
            type_=sa.Integer(),
            existing_nullable=False,
        )

    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_column("currency")
