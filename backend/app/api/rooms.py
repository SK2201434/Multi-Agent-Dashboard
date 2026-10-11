
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner, get_db
from app.models.owner import Owner
from app.schemas.room import RoomCreateRequest, RoomResponse, RoomUpdate
from app.services import room_service


router = APIRouter(
    prefix="/api/v1/properties/{property_id}/rooms",
    tags=["Rooms"],
)

@router.post(
    "",
    response_model=RoomResponse | list[RoomResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_room(
    property_id: UUID,
    data: RoomCreateRequest,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    is_bulk_request = isinstance(data, list)

    try:
        rooms = room_service.create_rooms(
            db=db,
            owner=current_owner,
            property_id=property_id,
            data=data,
        )
    except ValueError as exc:
        message = str(exc)

        if message == "Property not found.":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=message,
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        ) from exc

    # Preserve a single-room response for a single-room request.
    if not is_bulk_request:
        return rooms[0]

    return rooms

@router.get("", response_model=list[RoomResponse])
def list_rooms(
    property_id: UUID,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    try:
        return room_service.list_property_rooms(
            db, current_owner, property_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get("/{room_id}", response_model=RoomResponse)
def get_room(
    property_id: UUID,
    room_id: UUID,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    room = room_service.get_property_room(
        db, current_owner, property_id, room_id
    )

    if room is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found.",
        )

    return room


@router.patch("/{room_id}", response_model=RoomResponse)
def update_room(
    property_id: UUID,
    room_id: UUID,
    data: RoomUpdate,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    try:
        room = room_service.update_room(
            db, current_owner, property_id, room_id, data
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if room is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found.",
        )

    return room


@router.delete("/{room_id}", response_model=RoomResponse)
def deactivate_room(
    property_id: UUID,
    room_id: UUID,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    room = room_service.deactivate_room(
        db, current_owner, property_id, room_id
    )

    if room is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found.",
        )

    return room
