"""add project scope add-ons and invoice-linked payments

Revision ID: 20261101_addons
Revises: d78f13f1b865
Create Date: 2026-11-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20261101_addons"
down_revision = "d78f13f1b865"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_add_ons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="Pending Payment",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoice.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id"),
    )
    op.create_index(
        op.f("ix_project_add_ons_project_id"),
        "project_add_ons",
        ["project_id"],
        unique=False,
    )

    with op.batch_alter_table("payment", schema=None) as batch_op:
        batch_op.add_column(sa.Column("addon_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_payment_addon_id_project_add_ons",
            "project_add_ons",
            ["addon_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.drop_index("uq_payment_project_customer_created")
        batch_op.create_index(
            "uq_payment_project_customer_created",
            ["project_id", "customer_id"],
            unique=True,
            postgresql_where=sa.text(
                "status = 'Created' AND addon_id IS NULL"
            ),
            sqlite_where=sa.text(
                "status = 'Created' AND addon_id IS NULL"
            ),
        )
        batch_op.create_index(
            "uq_payment_addon_created",
            ["addon_id"],
            unique=True,
            postgresql_where=sa.text(
                "status = 'Created' AND addon_id IS NOT NULL"
            ),
            sqlite_where=sa.text(
                "status = 'Created' AND addon_id IS NOT NULL"
            ),
        )


def downgrade():
    with op.batch_alter_table("payment", schema=None) as batch_op:
        batch_op.drop_index("uq_payment_addon_created")
        batch_op.drop_index("uq_payment_project_customer_created")
        batch_op.create_index(
            "uq_payment_project_customer_created",
            ["project_id", "customer_id"],
            unique=True,
            postgresql_where=sa.text("status = 'Created'"),
            sqlite_where=sa.text("status = 'Created'"),
        )
        batch_op.drop_constraint(
            "fk_payment_addon_id_project_add_ons", type_="foreignkey"
        )
        batch_op.drop_column("addon_id")

    op.drop_index(op.f("ix_project_add_ons_project_id"), table_name="project_add_ons")
    op.drop_table("project_add_ons")
