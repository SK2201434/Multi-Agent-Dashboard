# Import every model so Base.metadata is fully populated wherever the
# application or Alembic loads the models package.
from app.models.owner import Owner  # noqa: F401
from app.models.property import Property  # noqa: F401
from app.models.room import Room  # noqa: F401
from app.models.bed import Bed  # noqa: F401
from app.models.tenant import Tenant  # noqa: F401
from app.models.booking_request import BookingRequest  # noqa: F401
from app.models.stay import Stay  # noqa: F401
from app.models.stay_bed_assignment import StayBedAssignment  # noqa: F401
from app.models.bed_change_request import BedChangeRequest  # noqa: F401
from app.models.property_tenant import PropertyTenant  # noqa: F401
from app.models.tenant_activation_token import TenantActivationToken  # noqa: F401
