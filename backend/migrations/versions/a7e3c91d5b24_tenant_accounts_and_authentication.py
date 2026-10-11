"""tenant accounts and authentication

Turns the owner-scoped tenants table into a global tenant identity with
credentials and an activation status, and adds the property association and
activation token tables.

The old tenants table had no credentials and was tied to a single owner, so
there is no safe automatic conversion of existing rows. The upgrade refuses
to run if the table holds data (the owner confirmed it is empty).

Revision ID: a7e3c91d5b24
Revises: c4a9d1f27b3e
Create Date: 2026-10-11 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7e3c91d5b24'
down_revision: Union[str, Sequence[str], None] = 'c4a9d1f27b3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    if not context.is_offline_mode():
        count = op.get_bind().execute(sa.text('SELECT count(*) FROM tenants')).scalar()
        if count:
            raise RuntimeError(
                f'tenants contains {count} row(s); they cannot be converted to '
                'global accounts automatically. Resolve them manually first.'
            )

    # Drop the owner scoping and fields that do not belong on a global
    # identity. Dropping owner_id also drops its FK and index.
    op.drop_column('tenants', 'owner_id')
    op.drop_column('tenants', 'is_active')
    op.drop_column('tenants', 'emergency_contact_name')
    op.drop_column('tenants', 'emergency_contact_phone')
    op.drop_column('tenants', 'address')
    op.drop_column('tenants', 'notes')

    # Email becomes the unique login identity; phone is optional contact data.
    op.alter_column('tenants', 'email', existing_type=sa.String(length=255), nullable=False)
    op.alter_column('tenants', 'phone', existing_type=sa.String(length=20), nullable=True)
    op.create_index(op.f('ix_tenants_email'), 'tenants', ['email'], unique=True)

    op.add_column('tenants', sa.Column('password_hash', sa.String(length=255), nullable=True))
    op.add_column('tenants', sa.Column('status', sa.String(length=20), nullable=False))
    op.add_column('tenants', sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tenants', sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        'ck_tenants_status', 'tenants',
        "status IN ('pending_activation', 'active')",
    )
    op.create_check_constraint(
        'ck_tenants_activation_consistency', 'tenants',
        "(status = 'pending_activation' AND password_hash IS NULL AND activated_at IS NULL) OR "
        "(status = 'active' AND password_hash IS NOT NULL AND activated_at IS NOT NULL)",
    )

    op.create_table('property_tenants',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('property_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by_owner_id', sa.UUID(), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('active', 'ended')", name='ck_property_tenants_status'),
    sa.CheckConstraint("(status = 'ended') = (ended_at IS NOT NULL)", name='ck_property_tenants_ended_at'),
    sa.ForeignKeyConstraint(['created_by_owner_id'], ['owners.id'], ),
    sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_property_tenants_property_id'), 'property_tenants', ['property_id'], unique=False)
    op.create_index(op.f('ix_property_tenants_tenant_id'), 'property_tenants', ['tenant_id'], unique=False)
    op.create_index('uq_property_tenants_active', 'property_tenants', ['tenant_id', 'property_id'], unique=True, postgresql_where=sa.text("status = 'active'"))

    op.create_table('tenant_activation_tokens',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tenant_activation_tokens_tenant_id'), 'tenant_activation_tokens', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_tenant_activation_tokens_token_hash'), 'tenant_activation_tokens', ['token_hash'], unique=True)


def downgrade() -> None:
    """Downgrade schema.

    Restores the old owner-scoped shape. owner_id cannot be reconstructed,
    so this only works while tenants is empty.
    """
    if not context.is_offline_mode():
        count = op.get_bind().execute(sa.text('SELECT count(*) FROM tenants')).scalar()
        if count:
            raise RuntimeError(
                f'tenants contains {count} row(s); downgrading would destroy '
                'tenant accounts and their property associations. Remove or '
                'export them manually first.'
            )

    op.drop_index(op.f('ix_tenant_activation_tokens_token_hash'), table_name='tenant_activation_tokens')
    op.drop_index(op.f('ix_tenant_activation_tokens_tenant_id'), table_name='tenant_activation_tokens')
    op.drop_table('tenant_activation_tokens')

    op.drop_index('uq_property_tenants_active', table_name='property_tenants', postgresql_where=sa.text("status = 'active'"))
    op.drop_index(op.f('ix_property_tenants_tenant_id'), table_name='property_tenants')
    op.drop_index(op.f('ix_property_tenants_property_id'), table_name='property_tenants')
    op.drop_table('property_tenants')

    op.drop_constraint('ck_tenants_activation_consistency', 'tenants', type_='check')
    op.drop_constraint('ck_tenants_status', 'tenants', type_='check')
    op.drop_column('tenants', 'password_changed_at')
    op.drop_column('tenants', 'activated_at')
    op.drop_column('tenants', 'status')
    op.drop_column('tenants', 'password_hash')

    op.drop_index(op.f('ix_tenants_email'), table_name='tenants')
    op.alter_column('tenants', 'phone', existing_type=sa.String(length=20), nullable=False)
    op.alter_column('tenants', 'email', existing_type=sa.String(length=255), nullable=True)

    op.add_column('tenants', sa.Column('notes', sa.Text(), nullable=True))
    op.add_column('tenants', sa.Column('address', sa.Text(), nullable=True))
    op.add_column('tenants', sa.Column('emergency_contact_phone', sa.String(length=20), nullable=True))
    op.add_column('tenants', sa.Column('emergency_contact_name', sa.String(length=150), nullable=True))
    op.add_column('tenants', sa.Column('is_active', sa.Boolean(), nullable=False))
    op.add_column('tenants', sa.Column('owner_id', sa.UUID(), nullable=False))
    op.create_foreign_key('tenants_owner_id_fkey', 'tenants', 'owners', ['owner_id'], ['id'])
    op.create_index(op.f('ix_tenants_owner_id'), 'tenants', ['owner_id'], unique=False)
