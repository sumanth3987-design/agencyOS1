"""Merge conflicting migrations

Revision ID: 88ab792d685f
Revises: 20261103_retainer_tasks, 20261112_asset_url
Create Date: 2026-11-12 01:09:38.168010

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '88ab792d685f'
down_revision = ('20261103_retainer_tasks', '20261112_asset_url')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
