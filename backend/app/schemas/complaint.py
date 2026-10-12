from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.stay import Page, UtcDatetime, _StrictRequest  # noqa: F401

ComplaintCategory = Literal[
    "maintenance", "plumbing", "electrical", "cleanliness",
    "noise", "safety", "internet", "other",
]
ComplaintStatus = Literal["open", "in_progress", "resolved", "closed"]
ComplaintVisibility = Literal["public", "private"]
Note = Annotated[str, Field(min_length=1, max_length=500)]


# ------------------------------------------------------------- requests


class ComplaintCreate(_StrictRequest):
    """Status is always 'open'; tenant and property come from the stay."""

    stay_id: UUID
    category: ComplaintCategory
    title: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(min_length=1, max_length=4000)]
    visibility: ComplaintVisibility


class TenantResolutionNote(_StrictRequest):
    note: Note | None = None


class OwnerStatusChange(_StrictRequest):
    """Owner transitions: open -> in_progress, in_progress -> resolved.
    Any other target (notably 'closed') is refused with 409."""

    status: ComplaintStatus
    note: Note | None = None


# ------------------------------------------------------------ responses


class ComplaintResponse(BaseModel):
    """Tenant view. The reporter's identity and stay are only shown to the
    reporter; other tenants see ``raised_by_me=false`` and nothing else."""

    id: UUID
    property_id: UUID
    property_name: str
    category: ComplaintCategory
    title: str
    description: str
    visibility: ComplaintVisibility
    status: ComplaintStatus
    raised_by_me: bool
    stay_id: UUID | None
    # True for the reporter while the owner is waiting on their decision.
    awaiting_my_confirmation: bool
    created_at: UtcDatetime
    updated_at: UtcDatetime


class OwnerComplaintResponse(BaseModel):
    id: UUID
    property_id: UUID
    property_name: str
    stay_id: UUID
    tenant_id: UUID
    tenant_full_name: str
    category: ComplaintCategory
    title: str
    description: str
    visibility: ComplaintVisibility
    status: ComplaintStatus
    awaiting_tenant_confirmation: bool
    created_at: UtcDatetime
    updated_at: UtcDatetime


class HistoryEntry(BaseModel):
    """Tenant view of an audit record: no identities except 'by me'."""

    id: UUID
    from_status: ComplaintStatus | None
    to_status: ComplaintStatus
    actor_type: Literal["tenant", "owner"]
    by_me: bool
    note: str | None
    created_at: UtcDatetime


class OwnerHistoryEntry(BaseModel):
    id: UUID
    from_status: ComplaintStatus | None
    to_status: ComplaintStatus
    actor_type: Literal["tenant", "owner"]
    actor_tenant_id: UUID | None
    actor_owner_id: UUID | None
    note: str | None
    created_at: UtcDatetime


class ComplaintSummary(BaseModel):
    """Counts over exactly the complaints the caller may see."""

    total: int
    # Always has all four statuses, zero-filled.
    by_status: dict[str, int]
    awaiting_confirmation: int
