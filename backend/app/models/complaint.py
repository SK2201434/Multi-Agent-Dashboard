import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

COMPLAINT_CATEGORIES = (
    "maintenance", "plumbing", "electrical", "cleanliness",
    "noise", "safety", "internet", "other",
)
COMPLAINT_STATUSES = ("open", "in_progress", "resolved", "closed")
COMPLAINT_VISIBILITIES = ("public", "private")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Complaint(Base):
    """A tenant's complaint about the property they currently live in.

    The tenant and the property are NOT stored here: they are the stay's.
    title, description, category, visibility and stay are immutable (a
    database trigger enforces it); only ``status`` moves, and every move
    is recorded in ComplaintStatusHistory in the same transaction.
    """

    __tablename__ = "complaints"

    __table_args__ = (
        CheckConstraint(_in("category", COMPLAINT_CATEGORIES), name="ck_complaints_category"),
        CheckConstraint(_in("status", COMPLAINT_STATUSES), name="ck_complaints_status"),
        CheckConstraint(_in("visibility", COMPLAINT_VISIBILITIES), name="ck_complaints_visibility"),
        CheckConstraint("length(trim(title)) > 0", name="ck_complaints_title_not_blank"),
        CheckConstraint("length(trim(description)) > 0", name="ck_complaints_description_not_blank"),
        Index("ix_complaints_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stay_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stays.id"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ComplaintStatusHistory(Base):
    """Append-only audit trail (a trigger rejects UPDATE and DELETE).

    The CHECK below is the lifecycle itself: only these transitions exist,
    each by its own actor. Owners can never produce a 'closed' row.
    """

    __tablename__ = "complaint_status_history"

    __table_args__ = (
        CheckConstraint(
            "(from_status IS NULL AND to_status = 'open' AND actor_type = 'tenant') OR "
            "(from_status = 'open' AND to_status = 'in_progress' AND actor_type = 'owner') OR "
            "(from_status = 'in_progress' AND to_status = 'resolved' AND actor_type = 'owner') OR "
            "(from_status = 'resolved' AND to_status = 'closed' AND actor_type = 'tenant') OR "
            "(from_status = 'resolved' AND to_status = 'in_progress' AND actor_type = 'tenant')",
            name="ck_complaint_history_transition",
        ),
        CheckConstraint(
            "(actor_type = 'tenant' AND actor_tenant_id IS NOT NULL AND actor_owner_id IS NULL) OR "
            "(actor_type = 'owner' AND actor_owner_id IS NOT NULL AND actor_tenant_id IS NULL)",
            name="ck_complaint_history_actor",
        ),
        # Exactly one initial 'open' record per complaint.
        Index(
            "uq_complaint_history_initial", "complaint_id", unique=True,
            postgresql_where=text("from_status IS NULL"),
        ),
        Index("ix_complaint_history_complaint_created", "complaint_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    complaint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("complaints.id"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(10), nullable=False)
    actor_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True
    )
    actor_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
