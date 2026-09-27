"""add llm_result column to case_reviews

Revision ID: 7de8a268604a
Revises: 21ac63a4097a
Create Date: 2026-09-26 11:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7de8a268604a'
down_revision: Union[str, None] = '21ac63a4097a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('case_reviews') as batch_op:
        batch_op.add_column(sa.Column('llm_result', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('case_reviews') as batch_op:
        batch_op.drop_column('llm_result')
