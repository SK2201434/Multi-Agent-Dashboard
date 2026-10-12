from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.stay import Page, UtcDatetime, _StrictRequest  # noqa: F401

# strict: 4.5, "4" and true are rejected rather than coerced.
Rating = Annotated[int, Field(strict=True, ge=1, le=5)]
Comment = Annotated[str, Field(min_length=1, max_length=2000)]


# ------------------------------------------------------------- requests


class ReviewCreate(_StrictRequest):
    stay_id: UUID
    rating: Rating
    comment: Comment


class ReviewUpdate(_StrictRequest):
    rating: Rating | None = None
    comment: Comment | None = None

    @model_validator(mode="after")
    def at_least_one(self) -> "ReviewUpdate":
        if self.rating is None and self.comment is None:
            raise ValueError("Provide a rating, a comment, or both.")
        return self


class OwnerResponseUpsert(_StrictRequest):
    """The only thing an owner can write: a response. Rating and text are
    not accepted here (unknown fields are rejected)."""

    response: Annotated[str, Field(min_length=1, max_length=2000)]


# ------------------------------------------------------------ responses


class ReviewResponse(BaseModel):
    id: UUID
    stay_id: UUID
    property_id: UUID
    property_name: str
    rating: int
    comment: str
    owner_response: str | None
    owner_responded_at: UtcDatetime | None
    edited_at: UtcDatetime | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class OwnerReviewResponse(ReviewResponse):
    tenant_id: UUID
    tenant_full_name: str
    stay_check_in: date | None
    stay_check_out: date | None


class RatingSummary(BaseModel):
    property_id: UUID
    total_reviews: int
    # Mean of all ratings rounded to 2 decimals; null when there are none.
    average_rating: Decimal | None
    # Always has keys "1".."5", zero-filled.
    rating_counts: dict[str, int]
    responded_reviews: int
