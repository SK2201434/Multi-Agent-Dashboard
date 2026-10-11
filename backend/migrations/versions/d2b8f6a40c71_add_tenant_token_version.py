"""add tenant token version

Adds tenants.token_version, embedded in tenant JWTs and incremented on
password change so stale tokens are rejected exactly, not by timestamp.

Revision ID: d2b8f6a40c71
Revises: a7e3c91d5b24
Create Date: 2026-10-11 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2b8f6a40c71'
down_revision: Union[str, Sequence[str], None] = 'a7e3c91d5b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The server default makes this safe for any existing rows.
    op.add_column('tenants', sa.Column('token_version', sa.Integer(), server_default=sa.text('0'), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tenants', 'token_version')
