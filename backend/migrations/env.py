
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.session import Base
from app.models.owner import Owner  # Register the Owner model
from app.models.property import Property # Register the Property model
from app.models.room import Room # Register the Room model
from app.models.bed import Bed # Register the Bed model
from app.models.tenant import Tenant # Register the Tenant model
from app.models.stay import Stay # Register the Stay model
from app.models.property_tenant import PropertyTenant # Register the PropertyTenant model
from app.models.tenant_activation_token import TenantActivationToken # Register the TenantActivationToken model
from app.models.booking_request import BookingRequest # Register the BookingRequest model
from app.models.stay_bed_assignment import StayBedAssignment # Register the StayBedAssignment model
from app.models.bed_change_request import BedChangeRequest # Register the BedChangeRequest model

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = get_settings().database_url

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        {"sqlalchemy.url": get_settings().database_url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
