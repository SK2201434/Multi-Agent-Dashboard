from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner, get_db
from app.models.owner import Owner
from app.schemas.bed import (
    BedCreateRequest,
    BedResponse,
    BedUpdate,
)
from app.services import bed_service


router = APIRouter(
    prefix="/api/v1/properties/{property_id}/rooms/{room_id}/beds",
    tags=["Beds"],
)


@router.post(
    "",
    response_model=BedResponse | list[BedResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_bed(
    property_id: UUID,
    room_id: UUID,
    data: BedCreateRequest,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    is_bulk_request = isinstance(data, list)

    beds = bed_service.create_beds(
        db, current_owner, property_id, room_id, data
    )

    # Preserve a single-bed response for a single-bed request.
    return beds if is_bulk_request else beds[0]


@router.get("", response_model=list[BedResponse])
def list_beds(
    property_id: UUID,
    room_id: UUID,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    return bed_service.list_room_beds(db, current_owner, property_id, room_id)


@router.get("/{bed_id}", response_model=BedResponse)
def get_bed(
    property_id: UUID,
    room_id: UUID,
    bed_id: UUID,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    return bed_service.get_room_bed(
        db, current_owner, property_id, room_id, bed_id
    )


@router.patch("/{bed_id}", response_model=BedResponse)
def update_bed(
    property_id: UUID,
    room_id: UUID,
    bed_id: UUID,
    data: BedUpdate,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    return bed_service.update_bed(
        db, current_owner, property_id, room_id, bed_id, data
    )


@router.delete("/{bed_id}", response_model=BedResponse)
def deactivate_bed(
    property_id: UUID,
    room_id: UUID,
    bed_id: UUID,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    return bed_service.deactivate_bed(
        db, current_owner, property_id, room_id, bed_id
    )
