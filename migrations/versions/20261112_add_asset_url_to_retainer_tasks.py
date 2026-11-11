"""add asset_url to retainer_tasks

Revision ID: 20261112_asset_url
Revises: 
Create Date: 2026-11-12 01:03:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20261112_asset_url'
down_revision = None  # Alembic will automatically resolve this if run via flask db upgrade
branch_labels = None
depends_on = None


def upgrade():
    # Use batch_alter_table to ensure compatibility with SQLite and PostgreSQL
    with op.batch_alter_table('retainer_tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('asset_url', sa.String(length=500), nullable=True))


def downgrade():
    with op.batch_alter_table('retainer_tasks', schema=None) as batch_op:
        batch_op.drop_column('asset_url')