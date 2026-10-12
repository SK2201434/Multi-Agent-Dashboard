"""complaints and issue tracking

Revision ID: d4e7a9b1c360
Revises: c8d2e5f7a914
Create Date: 2026-10-12 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e7a9b1c360'
down_revision: Union[str, Sequence[str], None] = 'c8d2e5f7a914'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('complaints',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stay_id', sa.UUID(), nullable=False),
    sa.Column('category', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('visibility', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("category IN ('maintenance', 'plumbing', 'electrical', 'cleanliness', 'noise', 'safety', 'internet', 'other')", name='ck_complaints_category'),
    sa.CheckConstraint("status IN ('open', 'in_progress', 'resolved', 'closed')", name='ck_complaints_status'),
    sa.CheckConstraint("visibility IN ('public', 'private')", name='ck_complaints_visibility'),
    sa.CheckConstraint('length(trim(title)) > 0', name='ck_complaints_title_not_blank'),
    sa.CheckConstraint('length(trim(description)) > 0', name='ck_complaints_description_not_blank'),
    sa.ForeignKeyConstraint(['stay_id'], ['stays.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_complaints_stay_id'), 'complaints', ['stay_id'], unique=False)
    op.create_index('ix_complaints_status_created', 'complaints', ['status', 'created_at'], unique=False)

    op.create_table('complaint_status_history',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('complaint_id', sa.UUID(), nullable=False),
    sa.Column('from_status', sa.String(length=20), nullable=True),
    sa.Column('to_status', sa.String(length=20), nullable=False),
    sa.Column('actor_type', sa.String(length=10), nullable=False),
    sa.Column('actor_tenant_id', sa.UUID(), nullable=True),
    sa.Column('actor_owner_id', sa.UUID(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(from_status IS NULL AND to_status = 'open' AND actor_type = 'tenant') OR (from_status = 'open' AND to_status = 'in_progress' AND actor_type = 'owner') OR (from_status = 'in_progress' AND to_status = 'resolved' AND actor_type = 'owner') OR (from_status = 'resolved' AND to_status = 'closed' AND actor_type = 'tenant') OR (from_status = 'resolved' AND to_status = 'in_progress' AND actor_type = 'tenant')", name='ck_complaint_history_transition'),
    sa.CheckConstraint("(actor_type = 'tenant' AND actor_tenant_id IS NOT NULL AND actor_owner_id IS NULL) OR (actor_type = 'owner' AND actor_owner_id IS NOT NULL AND actor_tenant_id IS NULL)", name='ck_complaint_history_actor'),
    sa.ForeignKeyConstraint(['actor_owner_id'], ['owners.id'], ),
    sa.ForeignKeyConstraint(['actor_tenant_id'], ['tenants.id'], ),
    sa.ForeignKeyConstraint(['complaint_id'], ['complaints.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_complaint_history_complaint_created', 'complaint_status_history', ['complaint_id', 'created_at'], unique=False)
    op.create_index('uq_complaint_history_initial', 'complaint_status_history', ['complaint_id'], unique=True, postgresql_where=sa.text('from_status IS NULL'))

    # Immutability enforced by the database, not only by the API.
    op.execute("""
        CREATE FUNCTION complaints_protect_immutable() RETURNS trigger AS $$
        BEGIN
            IF NEW.stay_id <> OLD.stay_id OR NEW.category <> OLD.category
               OR NEW.title <> OLD.title OR NEW.description <> OLD.description
               OR NEW.visibility <> OLD.visibility OR NEW.created_at <> OLD.created_at THEN
                RAISE EXCEPTION 'complaint stay, category, title, description and visibility are immutable';
            END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_complaints_immutable BEFORE UPDATE ON complaints
        FOR EACH ROW EXECUTE FUNCTION complaints_protect_immutable()
    """)
    op.execute("""
        CREATE FUNCTION complaint_history_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'complaint status history is append-only';
        END $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_complaint_history_append_only
        BEFORE UPDATE OR DELETE ON complaint_status_history
        FOR EACH ROW EXECUTE FUNCTION complaint_history_append_only()
    """)


def downgrade() -> None:
    """Downgrade schema.

    Refuses to run while any complaint exists: complaints and their audit
    history are never silently dropped.
    """
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT count(*) FROM complaints")).scalar()
    if count:
        raise RuntimeError(
            f"Refusing to downgrade: {count} complaint(s) exist. Export them "
            "and their status history deliberately before dropping these tables."
        )
    op.execute("DROP TRIGGER trg_complaint_history_append_only ON complaint_status_history")
    op.execute("DROP FUNCTION complaint_history_append_only()")
    op.execute("DROP TRIGGER trg_complaints_immutable ON complaints")
    op.execute("DROP FUNCTION complaints_protect_immutable()")
    op.drop_index('uq_complaint_history_initial', table_name='complaint_status_history', postgresql_where=sa.text('from_status IS NULL'))
    op.drop_index('ix_complaint_history_complaint_created', table_name='complaint_status_history')
    op.drop_table('complaint_status_history')
    op.drop_index('ix_complaints_status_created', table_name='complaints')
    op.drop_index(op.f('ix_complaints_stay_id'), table_name='complaints')
    op.drop_table('complaints')
