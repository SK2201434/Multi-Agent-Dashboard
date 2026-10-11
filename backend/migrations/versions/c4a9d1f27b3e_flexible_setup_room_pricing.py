"""flexible property setup and per-room bed pricing

Adds properties.total_floors, rooms.floor_number and
rooms.monthly_rent_per_bed; removes the per-bed rent from beds.

Existing rooms keep a NULL monthly_rent_per_bed until the owner sets a
price. No prices are invented or back-filled.

Revision ID: c4a9d1f27b3e
Revises: b7d0824f06ad
Create Date: 2026-10-11 16:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4a9d1f27b3e'
down_revision: Union[str, Sequence[str], None] = 'b7d0824f06ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Properties: optional floor count (includes the ground floor).
    op.add_column('properties', sa.Column('total_floors', sa.Integer(), nullable=True))
    op.create_check_constraint(
        'ck_properties_total_floors_range',
        'properties',
        'total_floors IS NULL OR (total_floors >= 1 AND total_floors <= 200)',
    )

    # Rooms: optional floor and owner-defined per-bed rent (nullable for
    # legacy rows; the API requires a price for every new room).
    op.add_column('rooms', sa.Column('floor_number', sa.Integer(), nullable=True))
    op.add_column('rooms', sa.Column('monthly_rent_per_bed', sa.Numeric(precision=10, scale=2), nullable=True))
    op.create_check_constraint(
        'ck_rooms_floor_number_range',
        'rooms',
        'floor_number IS NULL OR (floor_number >= 0 AND floor_number <= 199)',
    )
    op.create_check_constraint(
        'ck_rooms_monthly_rent_per_bed_positive',
        'rooms',
        'monthly_rent_per_bed IS NULL OR monthly_rent_per_bed > 0',
    )

    # Beds: the table already exists (created by revision bc0758e70c15), so
    # it is not recreated. Remove the obsolete per-bed rent.
    op.drop_constraint('ck_beds_monthly_rent_non_negative', 'beds', type_='check')
    op.drop_column('beds', 'monthly_rent')


def downgrade() -> None:
    """Downgrade schema.

    The original beds.monthly_rent was NOT NULL and its values are lost on
    upgrade, so restored rows get 0 as a placeholder (the old constraint
    allowed 0). Those values are NOT real historical rents.
    """
    op.add_column(
        'beds',
        sa.Column('monthly_rent', sa.Numeric(precision=10, scale=2), nullable=False, server_default='0'),
    )
    op.alter_column('beds', 'monthly_rent', server_default=None)
    op.create_check_constraint(
        'ck_beds_monthly_rent_non_negative', 'beds', 'monthly_rent >= 0'
    )

    op.drop_constraint('ck_rooms_monthly_rent_per_bed_positive', 'rooms', type_='check')
    op.drop_constraint('ck_rooms_floor_number_range', 'rooms', type_='check')
    op.drop_column('rooms', 'monthly_rent_per_bed')
    op.drop_column('rooms', 'floor_number')

    op.drop_constraint('ck_properties_total_floors_range', 'properties', type_='check')
    op.drop_column('properties', 'total_floors')
