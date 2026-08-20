"""store full benchmark payload for historical run inspection

Revision ID: 7c1a51f4b902
Revises: 0f37c30a0822
Create Date: 2026-10-21 17:35:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '7c1a51f4b902'
down_revision = '0f37c30a0822'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('benchmark_runs') as batch:
        batch.add_column(sa.Column('payload', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('benchmark_runs') as batch:
        batch.drop_column('payload')
