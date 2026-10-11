"""stay management: reservations, booking and bed-change requests

Revision ID: f5a1c8d3e962
Revises: d2b8f6a40c71
Create Date: 2026-10-11 12:00:00.000000

Reshapes the (unused) legacy ``stays`` table in place, preserving any rows:

* move_in_date / move_out_date  -> planned_check_in / planned_check_out
* bed_id moves to ``stay_bed_assignments`` (one history row per legacy stay)
* legacy open stays become 'checked_in', closed ones 'checked_out'

Adds ``booking_requests``, ``bed_change_requests`` and
``stay_bed_assignments`` and the PostgreSQL exclusion constraints that make
double-booking impossible. Requires the ``btree_gist`` extension (CREATE
EXTENSION needs sufficient privileges; the extension is not dropped on
downgrade).

The downgrade refuses to run if it would destroy Module 6 history.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5a1c8d3e962'
down_revision: Union[str, Sequence[str], None] = 'd2b8f6a40c71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # ---------------------------------------------------------- booking_requests
    op.create_table(
        'booking_requests',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('bed_id', sa.UUID(), nullable=False),
        sa.Column('property_id', sa.UUID(), nullable=False),
        sa.Column('planned_check_in', sa.Date(), nullable=False),
        sa.Column('planned_check_out', sa.Date(), nullable=True),
        sa.Column('quoted_monthly_rent', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('tenant_message', sa.Text(), nullable=True),
        _ts('reviewed_at'),
        sa.Column('reviewed_by_owner_id', sa.UUID(), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired')", name='ck_booking_requests_status'),
        sa.CheckConstraint('planned_check_out IS NULL OR planned_check_out > planned_check_in', name='ck_booking_requests_planned_dates'),
        sa.CheckConstraint('quoted_monthly_rent >= 0', name='ck_booking_requests_quoted_rent_non_negative'),
        sa.CheckConstraint("status NOT IN ('approved', 'rejected') OR (reviewed_at IS NOT NULL AND reviewed_by_owner_id IS NOT NULL)", name='ck_booking_requests_review_consistency'),
        sa.ForeignKeyConstraint(['bed_id'], ['beds.id']),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id']),
        sa.ForeignKeyConstraint(['reviewed_by_owner_id'], ['owners.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_booking_requests_tenant_id'), 'booking_requests', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_booking_requests_bed_id'), 'booking_requests', ['bed_id'], unique=False)
    op.create_index('ix_booking_requests_property_status', 'booking_requests', ['property_id', 'status', 'created_at'], unique=False)
    op.create_index(
        'uq_booking_requests_pending_tenant_bed_start', 'booking_requests',
        ['tenant_id', 'bed_id', 'planned_check_in'], unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    # -------------------------------------------------------------------- stays
    op.add_column('stays', sa.Column('property_id', sa.UUID(), nullable=True))
    op.add_column('stays', sa.Column('booking_request_id', sa.UUID(), nullable=True))
    op.add_column('stays', sa.Column('status', sa.String(length=20), nullable=True))
    op.add_column('stays', sa.Column('planned_check_in', sa.Date(), nullable=True))
    op.add_column('stays', sa.Column('planned_check_out', sa.Date(), nullable=True))
    op.add_column('stays', _ts('actual_check_in_at'))
    op.add_column('stays', _ts('actual_check_out_at'))
    op.add_column('stays', _ts('closed_at'))
    op.add_column('stays', sa.Column('close_reason', sa.Text(), nullable=True))
    for owner_col in ('created_by_owner_id', 'checked_in_by_owner_id',
                      'checked_out_by_owner_id', 'closed_by_owner_id'):
        op.add_column('stays', sa.Column(owner_col, sa.UUID(), nullable=True))

    # Legacy rows: an open stay was occupied, a closed one was completed.
    op.execute("""
        UPDATE stays s SET
            property_id = r.property_id,
            status = CASE WHEN s.move_out_date IS NULL
                          THEN 'checked_in' ELSE 'checked_out' END,
            planned_check_in = s.move_in_date,
            planned_check_out = CASE WHEN s.move_out_date > s.move_in_date
                                     THEN s.move_out_date END,
            actual_check_in_at = s.move_in_date::timestamp AT TIME ZONE 'UTC',
            actual_check_out_at = CASE WHEN s.move_out_date IS NOT NULL
                THEN s.move_out_date::timestamp AT TIME ZONE 'UTC' END
        FROM beds b JOIN rooms r ON r.id = b.room_id
        WHERE b.id = s.bed_id
    """)

    # ---------------------------------------------------- bed_change_requests
    op.create_table(
        'bed_change_requests',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('stay_id', sa.UUID(), nullable=False),
        sa.Column('property_id', sa.UUID(), nullable=False),
        sa.Column('from_bed_id', sa.UUID(), nullable=False),
        sa.Column('to_bed_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        _ts('reviewed_at'),
        sa.Column('reviewed_by_owner_id', sa.UUID(), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        _ts('effective_at'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'cancelled')", name='ck_bed_change_requests_status'),
        sa.CheckConstraint('from_bed_id <> to_bed_id', name='ck_bed_change_requests_distinct_beds'),
        sa.CheckConstraint("(status = 'approved') = (effective_at IS NOT NULL)", name='ck_bed_change_requests_effective_at'),
        sa.CheckConstraint("status NOT IN ('approved', 'rejected') OR (reviewed_at IS NOT NULL AND reviewed_by_owner_id IS NOT NULL)", name='ck_bed_change_requests_review_consistency'),
        sa.ForeignKeyConstraint(['from_bed_id'], ['beds.id']),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id']),
        sa.ForeignKeyConstraint(['reviewed_by_owner_id'], ['owners.id']),
        sa.ForeignKeyConstraint(['stay_id'], ['stays.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['to_bed_id'], ['beds.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_bed_change_requests_tenant_id'), 'bed_change_requests', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_bed_change_requests_stay_id'), 'bed_change_requests', ['stay_id'], unique=False)
    op.create_index('ix_bed_change_requests_property_status', 'bed_change_requests', ['property_id', 'status', 'created_at'], unique=False)
    op.create_index(
        'uq_bed_change_requests_pending_stay', 'bed_change_requests',
        ['stay_id'], unique=True, postgresql_where=sa.text("status = 'pending'"),
    )

    # ------------------------------------------------------ stay_bed_assignments
    op.create_table(
        'stay_bed_assignments',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('stay_id', sa.UUID(), nullable=False),
        sa.Column('bed_id', sa.UUID(), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=True),
        sa.Column('assigned_at', sa.DateTime(timezone=True), nullable=False),
        _ts('occupied_from'),
        _ts('released_at'),
        sa.Column('bed_change_request_id', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('period_end IS NULL OR period_end > period_start', name='ck_stay_bed_assignments_period'),
        sa.CheckConstraint('released_at IS NULL OR released_at >= assigned_at', name='ck_stay_bed_assignments_released_after_assigned'),
        sa.CheckConstraint('occupied_from IS NULL OR occupied_from >= assigned_at', name='ck_stay_bed_assignments_occupied_after_assigned'),
        sa.CheckConstraint('released_at IS NULL OR occupied_from IS NULL OR released_at >= occupied_from', name='ck_stay_bed_assignments_released_after_occupied'),
        sa.ForeignKeyConstraint(['bed_change_request_id'], ['bed_change_requests.id']),
        sa.ForeignKeyConstraint(['bed_id'], ['beds.id']),
        sa.ForeignKeyConstraint(['stay_id'], ['stays.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_stay_bed_assignments_stay_id'), 'stay_bed_assignments', ['stay_id'], unique=False)
    op.create_index('ix_stay_bed_assignments_bed_current', 'stay_bed_assignments', ['bed_id', 'released_at'], unique=False)
    op.create_index(
        'uq_stay_bed_assignments_current_stay', 'stay_bed_assignments',
        ['stay_id'], unique=True, postgresql_where=sa.text('released_at IS NULL'),
    )
    op.create_index(
        'uq_stay_bed_assignments_bed_occupied', 'stay_bed_assignments',
        ['bed_id'], unique=True,
        postgresql_where=sa.text('released_at IS NULL AND occupied_from IS NOT NULL'),
    )
    op.execute("""
        ALTER TABLE stay_bed_assignments
        ADD CONSTRAINT ex_stay_bed_assignments_bed_no_overlap
        EXCLUDE USING gist (
            bed_id WITH =,
            daterange(period_start, period_end, '[)') WITH &&
        ) WHERE (released_at IS NULL)
    """)
    op.execute("""
        ALTER TABLE stay_bed_assignments
        ADD CONSTRAINT ex_stay_bed_assignments_stay_timeline
        EXCLUDE USING gist (
            stay_id WITH =,
            tstzrange(assigned_at, released_at, '[)') WITH &&
        )
    """)

    # One assignment per legacy stay, carrying its bed over.
    op.execute("""
        INSERT INTO stay_bed_assignments
            (id, stay_id, bed_id, period_start, period_end,
             assigned_at, occupied_from, released_at)
        SELECT gen_random_uuid(), id, bed_id, planned_check_in,
               planned_check_out, actual_check_in_at, actual_check_in_at,
               actual_check_out_at
        FROM stays
    """)

    # ------------------------------------------- finish reshaping the stays table
    op.drop_index('uq_stays_bed_active', table_name='stays',
                  postgresql_where=sa.text('move_out_date IS NULL'))
    op.drop_index(op.f('ix_stays_bed_id'), table_name='stays')
    op.drop_constraint('ck_stays_valid_dates', 'stays', type_='check')
    op.drop_constraint('stays_bed_id_fkey', 'stays', type_='foreignkey')
    op.drop_column('stays', 'bed_id')
    op.drop_column('stays', 'move_in_date')
    op.drop_column('stays', 'move_out_date')

    op.alter_column('stays', 'property_id', nullable=False)
    op.alter_column('stays', 'status', nullable=False)
    op.alter_column('stays', 'planned_check_in', nullable=False)

    op.create_foreign_key('stays_property_id_fkey', 'stays', 'properties', ['property_id'], ['id'])
    op.create_foreign_key('stays_booking_request_id_fkey', 'stays', 'booking_requests', ['booking_request_id'], ['id'])
    for owner_col in ('created_by_owner_id', 'checked_in_by_owner_id',
                      'checked_out_by_owner_id', 'closed_by_owner_id'):
        op.create_foreign_key(f'stays_{owner_col}_fkey', 'stays', 'owners', [owner_col], ['id'])
    op.create_unique_constraint('stays_booking_request_id_key', 'stays', ['booking_request_id'])
    op.create_index(op.f('ix_stays_property_id'), 'stays', ['property_id'], unique=False)
    op.create_index(op.f('ix_stays_status'), 'stays', ['status'], unique=False)
    op.create_index('uq_stays_tenant_checked_in', 'stays', ['tenant_id'], unique=True,
                    postgresql_where=sa.text("status = 'checked_in'"))

    op.create_check_constraint(
        'ck_stays_status', 'stays',
        "status IN ('reserved', 'checked_in', 'checked_out', 'cancelled', 'no_show')")
    op.create_check_constraint(
        'ck_stays_planned_dates', 'stays',
        'planned_check_out IS NULL OR planned_check_out > planned_check_in')
    op.create_check_constraint(
        'ck_stays_lifecycle_consistency', 'stays',
        "(status = 'reserved' AND actual_check_in_at IS NULL "
        "AND actual_check_out_at IS NULL AND closed_at IS NULL) OR "
        "(status = 'checked_in' AND actual_check_in_at IS NOT NULL "
        "AND actual_check_out_at IS NULL AND closed_at IS NULL) OR "
        "(status = 'checked_out' AND actual_check_in_at IS NOT NULL "
        "AND actual_check_out_at IS NOT NULL "
        "AND actual_check_out_at >= actual_check_in_at "
        "AND closed_at IS NULL) OR "
        "(status IN ('cancelled', 'no_show') "
        "AND actual_check_in_at IS NULL "
        "AND actual_check_out_at IS NULL AND closed_at IS NOT NULL)")
    op.execute("""
        ALTER TABLE stays
        ADD CONSTRAINT ex_stays_tenant_no_overlap
        EXCLUDE USING gist (
            tenant_id WITH =,
            daterange(planned_check_in, planned_check_out, '[)') WITH &&
        ) WHERE (status IN ('reserved', 'checked_in'))
    """)


def downgrade() -> None:
    bind = op.get_bind()

    # Never silently destroy reservation, request or assignment history.
    blockers = bind.execute(sa.text("""
        SELECT
          (SELECT count(*) FROM booking_requests),
          (SELECT count(*) FROM bed_change_requests),
          (SELECT count(*) FROM stays
             WHERE status IN ('reserved', 'cancelled', 'no_show')),
          (SELECT count(*) FROM (
             SELECT stay_id FROM stay_bed_assignments
             GROUP BY stay_id HAVING count(*) > 1) multi)
    """)).one()
    if any(blockers):
        raise RuntimeError(
            "Refusing to downgrade: Module 6 data exists that the previous "
            f"schema cannot hold (booking_requests={blockers[0]}, "
            f"bed_change_requests={blockers[1]}, "
            f"reserved/cancelled/no_show stays={blockers[2]}, "
            f"stays with bed history={blockers[3]}). Export or remove it "
            "deliberately first."
        )

    op.execute('ALTER TABLE stays DROP CONSTRAINT ex_stays_tenant_no_overlap')
    op.drop_index('uq_stays_tenant_checked_in', table_name='stays',
                  postgresql_where=sa.text("status = 'checked_in'"))
    for name in ('ck_stays_lifecycle_consistency', 'ck_stays_planned_dates', 'ck_stays_status'):
        op.drop_constraint(name, 'stays', type_='check')
    op.drop_index(op.f('ix_stays_status'), table_name='stays')
    op.drop_index(op.f('ix_stays_property_id'), table_name='stays')

    op.add_column('stays', sa.Column('bed_id', sa.UUID(), nullable=True))
    op.add_column('stays', sa.Column('move_in_date', sa.Date(), nullable=True))
    op.add_column('stays', sa.Column('move_out_date', sa.Date(), nullable=True))
    op.execute("""
        UPDATE stays s SET
            bed_id = a.bed_id,
            move_in_date = s.planned_check_in,
            move_out_date = (s.actual_check_out_at AT TIME ZONE 'UTC')::date
        FROM stay_bed_assignments a WHERE a.stay_id = s.id
    """)
    op.alter_column('stays', 'bed_id', nullable=False)
    op.alter_column('stays', 'move_in_date', nullable=False)

    op.drop_table('stay_bed_assignments')
    op.drop_table('bed_change_requests')

    op.drop_constraint('stays_booking_request_id_key', 'stays', type_='unique')
    for owner_col in ('created_by_owner_id', 'checked_in_by_owner_id',
                      'checked_out_by_owner_id', 'closed_by_owner_id'):
        op.drop_constraint(f'stays_{owner_col}_fkey', 'stays', type_='foreignkey')
    op.drop_constraint('stays_booking_request_id_fkey', 'stays', type_='foreignkey')
    op.drop_constraint('stays_property_id_fkey', 'stays', type_='foreignkey')
    for col in ('closed_by_owner_id', 'checked_out_by_owner_id',
                'checked_in_by_owner_id', 'created_by_owner_id', 'close_reason',
                'closed_at', 'actual_check_out_at', 'actual_check_in_at',
                'planned_check_out', 'planned_check_in', 'status',
                'booking_request_id', 'property_id'):
        op.drop_column('stays', col)

    op.create_foreign_key('stays_bed_id_fkey', 'stays', 'beds', ['bed_id'], ['id'])
    op.create_check_constraint(
        'ck_stays_valid_dates', 'stays',
        'move_out_date IS NULL OR move_out_date >= move_in_date')
    op.create_index(op.f('ix_stays_bed_id'), 'stays', ['bed_id'], unique=False)
    op.create_index('uq_stays_bed_active', 'stays', ['bed_id'], unique=True,
                    postgresql_where=sa.text('move_out_date IS NULL'))

    op.drop_table('booking_requests')
