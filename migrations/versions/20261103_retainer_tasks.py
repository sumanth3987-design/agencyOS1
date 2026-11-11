"""add retainer hours bank, micro tasks, and statutory TDS fields

Revision ID: 20261103_retainer_tasks
Revises: 20261102_multi_currency
Create Date: 2026-11-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20261103_retainer_tasks"
down_revision = "20261102_multi_currency"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    # 1. Create Retainer Tasks table only if it does not already exist
    if "retainer_tasks" not in existing_tables:
        op.create_table(
            "retainer_tasks",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("specialist_id", sa.Integer(), nullable=True),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=30),
                server_default="Queued",
                nullable=False,
            ),
            sa.Column(
                "hours_spent",
                sa.Numeric(precision=5, scale=2),
                server_default="0.00",
                nullable=False,
            ),
            sa.Column("completion_notes", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["customer_id"], ["user.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["project_id"], ["project.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["specialist_id"], ["user.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_retainer_tasks_project_id",
            "retainer_tasks",
            ["project_id"],
            unique=False,
        )
        op.create_index(
            "ix_retainer_tasks_customer_id",
            "retainer_tasks",
            ["customer_id"],
            unique=False,
        )
        op.create_index(
            "ix_retainer_tasks_specialist_id",
            "retainer_tasks",
            ["specialist_id"],
            unique=False,
        )

    # 2. Add Monthly Hours Bank to Project
    project_cols = [c["name"] for c in inspector.get_columns("project")]
    with op.batch_alter_table("project", schema=None) as batch_op:
        if "hours_allocated" not in project_cols:
            batch_op.add_column(
                sa.Column(
                    "hours_allocated",
                    sa.Integer(),
                    server_default="0",
                    nullable=False,
                )
            )
        if "hours_remaining" not in project_cols:
            batch_op.add_column(
                sa.Column(
                    "hours_remaining",
                    sa.Numeric(precision=5, scale=2),
                    server_default="0.00",
                    nullable=False,
                )
            )

    # 3. Add Monthly Hours Bank to RazorpaySubscription
    if "razorpay_subscription" in existing_tables:
        sub_cols = [c["name"] for c in inspector.get_columns("razorpay_subscription")]
        with op.batch_alter_table("razorpay_subscription", schema=None) as batch_op:
            if "hours_allocated" not in sub_cols:
                batch_op.add_column(
                    sa.Column(
                        "hours_allocated",
                        sa.Integer(),
                        server_default="5",
                        nullable=False,
                    )
                )
            if "hours_remaining" not in sub_cols:
                batch_op.add_column(
                    sa.Column(
                        "hours_remaining",
                        sa.Numeric(precision=5, scale=2),
                        server_default="5.00",
                        nullable=False,
                    )
                )

    # 4. Add Statutory Section 194-O TDS fields to WithdrawalRequest
    if "withdrawal_requests" in existing_tables:
        wth_cols = [c["name"] for c in inspector.get_columns("withdrawal_requests")]
        with op.batch_alter_table("withdrawal_requests", schema=None) as batch_op:
            if "pan_number" not in wth_cols:
                batch_op.add_column(
                    sa.Column("pan_number", sa.String(length=20), nullable=True)
                )
            if "tds_rate" not in wth_cols:
                batch_op.add_column(
                    sa.Column(
                        "tds_rate",
                        sa.Numeric(precision=4, scale=2),
                        server_default="0.01",
                        nullable=False,
                    )
                )
            if "tds_amount" not in wth_cols:
                batch_op.add_column(
                    sa.Column(
                        "tds_amount",
                        sa.Numeric(precision=12, scale=2),
                        server_default="0.00",
                        nullable=False,
                    )
                )
            if "net_amount" not in wth_cols:
                batch_op.add_column(
                    sa.Column(
                        "net_amount",
                        sa.Numeric(precision=12, scale=2),
                        server_default="0.00",
                        nullable=False,
                    )
                )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "withdrawal_requests" in existing_tables:
        wth_cols = [c["name"] for c in inspector.get_columns("withdrawal_requests")]
        with op.batch_alter_table("withdrawal_requests", schema=None) as batch_op:
            if "net_amount" in wth_cols:
                batch_op.drop_column("net_amount")
            if "tds_amount" in wth_cols:
                batch_op.drop_column("tds_amount")
            if "tds_rate" in wth_cols:
                batch_op.drop_column("tds_rate")
            if "pan_number" in wth_cols:
                batch_op.drop_column("pan_number")

    if "razorpay_subscription" in existing_tables:
        sub_cols = [c["name"] for c in inspector.get_columns("razorpay_subscription")]
        with op.batch_alter_table("razorpay_subscription", schema=None) as batch_op:
            if "hours_remaining" in sub_cols:
                batch_op.drop_column("hours_remaining")
            if "hours_allocated" in sub_cols:
                batch_op.drop_column("hours_allocated")

    if "project" in existing_tables:
        project_cols = [c["name"] for c in inspector.get_columns("project")]
        with op.batch_alter_table("project", schema=None) as batch_op:
            if "hours_remaining" in project_cols:
                batch_op.drop_column("hours_remaining")
            if "hours_allocated" in project_cols:
                batch_op.drop_column("hours_allocated")

    if "retainer_tasks" in existing_tables:
        op.drop_table("retainer_tasks")