"""reviews and ratings

Revision ID: c8d2e5f7a914
Revises: a9c4e7b2d150
Create Date: 2026-10-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8d2e5f7a914'
down_revision: Union[str, Sequence[str], None] = 'a9c4e7b2d150'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('reviews',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stay_id', sa.UUID(), nullable=False),
    sa.Column('rating', sa.SmallInteger(), nullable=False),
    sa.Column('comment', sa.Text(), nullable=False),
    sa.Column('owner_response', sa.Text(), nullable=True),
    sa.Column('owner_responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('owner_responded_by_owner_id', sa.UUID(), nullable=True),
    sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('rating BETWEEN 1 AND 5', name='ck_reviews_rating_range'),
    sa.CheckConstraint('length(trim(comment)) > 0', name='ck_reviews_comment_not_blank'),
    sa.CheckConstraint('(owner_response IS NULL) = (owner_responded_at IS NULL) AND (owner_response IS NULL) = (owner_responded_by_owner_id IS NULL) AND (owner_response IS NULL OR length(trim(owner_response)) > 0)', name='ck_reviews_owner_response_consistency'),
    sa.ForeignKeyConstraint(['owner_responded_by_owner_id'], ['owners.id'], ),
    sa.ForeignKeyConstraint(['stay_id'], ['stays.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stay_id')
    )
    op.create_index('ix_reviews_created_at', 'reviews', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema.

    Refuses to run while any review exists: reviews are user content and
    are never silently dropped.
    """
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT count(*) FROM reviews")).scalar()
    if count:
        raise RuntimeError(
            f"Refusing to downgrade: {count} review(s) exist. Export them "
            "deliberately before dropping the reviews table."
        )
    op.drop_index('ix_reviews_created_at', table_name='reviews')
    op.drop_table('reviews')
