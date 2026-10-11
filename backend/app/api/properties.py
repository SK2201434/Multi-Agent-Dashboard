
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner, get_db
from app.models.owner import Owner
from app.schemas.bed import BedResponse
from app.schemas.property import (
    PropertyCreate,
    PropertyCreateResponse,
    PropertyResponse,
    PropertyUpdate,
)
from app.schemas.room import RoomWithBedsResponse
from app.services import property_service


router = APIRouter(
    prefix="/api/v1/properties",
    tags=["Properties"],
)


@router.post(
    "",
    response_model=PropertyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_property(
    data: PropertyCreate,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    created = property_service.create_property(db, current_owner, data)

    rooms = []
    for item in created.rooms:
        room = RoomWithBedsResponse.model_validate(item.room)
        room.beds = [BedResponse.model_validate(bed) for bed in item.beds]
        rooms.append(room)

    response = PropertyCreateResponse.model_validate(created.property)
    response.rooms = rooms
    return response


@router.get("", response_model=list[PropertyResponse])
def list_properties(
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    return property_service.list_owner_properties(db, current_owner)


@router.get("/{property_id}", response_model=PropertyResponse)
def get_property(
    property_id: UUID,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    property_obj = property_service.get_owner_property(
        db, current_owner, property_id
    )

    if property_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found.",
        )

    return property_obj


@router.patch("/{property_id}", response_model=PropertyResponse)
def update_property(
    property_id: UUID,
    data: PropertyUpdate,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    property_obj = property_service.update_property(
        db,
        current_owner,
        property_id,
        data,
    )

    if property_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found.",
        )

    return property_obj



@router.delete("/{property_id}", response_model=PropertyResponse)
def delete_property(
    property_id: UUID,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    property_obj = property_service.soft_delete_property(
        db,
        current_owner,
        property_id,
    )

    if property_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found.",
        )

    return property_obj
