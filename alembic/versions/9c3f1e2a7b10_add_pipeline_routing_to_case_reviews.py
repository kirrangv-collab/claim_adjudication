"""add multi-agent pipeline routing and trace columns to case_reviews

Revision ID: 9c3f1e2a7b10
Revises: 7de8a268604a
Create Date: 2026-10-21 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c3f1e2a7b10'
down_revision: Union[str, None] = '7de8a268604a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('case_reviews') as batch_op:
        batch_op.add_column(sa.Column('route', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('audit_brief', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('pipeline_trace', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('final_decision', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('decision_source', sa.String(length=10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('case_reviews') as batch_op:
        batch_op.drop_column('decision_source')
        batch_op.drop_column('final_decision')
        batch_op.drop_column('pipeline_trace')
        batch_op.drop_column('audit_brief')
        batch_op.drop_column('route')
