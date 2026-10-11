
from math import asin, cos, radians, sin, sqrt
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.property import Property
from app.schemas.property import (
    NearbyPropertyResponse,
    PublicPropertyResponse,
)
from app.schemas.stay import PublicAvailabilityResponse, PublicRoomResponse
from app.services import availability_service


router = APIRouter(
    prefix="/api/v1/public/properties",
    tags=["Public Property Discovery"],
)


def calculate_distance_km(
    latitude_1: float,
    longitude_1: float,
    latitude_2: float,
    longitude_2: float,
) -> float:
    """Calculate the great-circle distance using the Haversine formula."""
    earth_radius_km = 6371.0

    lat1, lon1, lat2, lon2 = map(
        radians,
        (latitude_1, longitude_1, latitude_2, longitude_2),
    )

    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1

    a = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    )

    return 2 * earth_radius_km * asin(sqrt(min(1.0, a)))


@router.get("", response_model=list[PublicPropertyResponse])
def list_public_properties(
    db: Session = Depends(get_db),
):
    statement = (
        select(Property)
        .where(
            Property.is_active.is_(True),
            Property.deleted_at.is_(None),
        )
        .order_by(Property.name.asc())
    )

    return list(db.scalars(statement).all())


@router.get("/nearby", response_model=list[NearbyPropertyResponse])
def find_nearby_properties(
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    radius_km: float = Query(default=5.0, gt=0, le=50),
    db: Session = Depends(get_db),
):
    statement = select(Property).where(
        Property.is_active.is_(True),
        Property.deleted_at.is_(None),
        Property.latitude.is_not(None),
        Property.longitude.is_not(None),
    )

    properties = db.scalars(statement).all()
    results = []

    for property_obj in properties:
        distance = calculate_distance_km(
            latitude,
            longitude,
            property_obj.latitude,
            property_obj.longitude,
        )

        if distance <= radius_km:
            results.append(
                NearbyPropertyResponse(
                    **PublicPropertyResponse.model_validate(
                        property_obj
                    ).model_dump(),
                    distance_km=round(distance, 2),
                )
            )

    return sorted(results, key=lambda item: item.distance_km)


@router.get(
    "/{property_id}",
    response_model=PublicPropertyResponse,
)
def get_public_property(
    property_id: UUID,
    db: Session = Depends(get_db),
):
    statement = select(Property).where(
        Property.id == property_id,
        Property.is_active.is_(True),
        Property.deleted_at.is_(None),
    )

    property_obj = db.scalar(statement)

    if property_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found.",
        )

    return property_obj


@router.get(
    "/{property_id}/rooms",
    response_model=list[PublicRoomResponse],
    summary="List bookable rooms of a property",
    description=(
        "Public. Operational rooms with rent per bed and the number of "
        "bookable beds. No tenant or booking data is exposed."
    ),
)
def list_public_rooms(
    property_id: UUID,
    db: Session = Depends(get_db),
):
    return availability_service.list_rooms(db, property_id)


@router.get(
    "/{property_id}/availability",
    response_model=PublicAvailabilityResponse,
    summary="Bed availability for a date range",
    description=(
        "Public. Interval is [check_in, check_out); check_out is optional "
        "and, when omitted, means an open-ended stay. Pending requests do "
        "not reduce availability."
    ),
)
def get_public_availability(
    property_id: UUID,
    check_in: date,
    check_out: date | None = None,
    db: Session = Depends(get_db),
):
    if check_out is not None and check_out <= check_in:
        raise HTTPException(
            status_code=422,
            detail="check_out must be after check_in.",
        )
    return availability_service.availability(db, property_id, check_in, check_out)
