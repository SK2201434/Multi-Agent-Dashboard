import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Review(Base):
    """A tenant's rating of the property for one actual stay.

    The tenant and the property are NOT stored here: they are always the
    stay's, so a review can never disagree with the stay it belongs to.
    ``stay_id`` is unique: one review per stay, enforced by the database.
    The owner's response lives in dedicated columns and never changes
    ``rating`` or ``comment``.
    """

    __tablename__ = "reviews"

    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_reviews_rating_range"),
        CheckConstraint("length(trim(comment)) > 0", name="ck_reviews_comment_not_blank"),
        CheckConstraint(
            "(owner_response IS NULL) = (owner_responded_at IS NULL) AND "
            "(owner_response IS NULL) = (owner_responded_by_owner_id IS NULL) AND "
            "(owner_response IS NULL OR length(trim(owner_response)) > 0)",
            name="ck_reviews_owner_response_consistency",
        ),
        Index("ix_reviews_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stay_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stays.id"), nullable=False, unique=True
    )
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    owner_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    owner_responded_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=True
    )
    # Set only when the tenant edits rating/comment after posting.
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
