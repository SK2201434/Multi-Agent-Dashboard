
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner, get_db
from app.models.owner import Owner
from app.schemas.auth import (
    OwnerLoginRequest,
    OwnerRegisterRequest,
    OwnerResponse,
    TokenResponse,
)
from app.services.auth_service import (
    authenticate_owner,
    create_access_token,
    register_owner,
)


router = APIRouter(
    prefix="/api/v1/auth",
    tags=["Authentication"],
)


@router.post(
    "/register",
    response_model=OwnerResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: OwnerRegisterRequest,
    db: Session = Depends(get_db),
):
    try:
        owner = register_owner(
            db=db,
            full_name=payload.full_name,
            email=str(payload.email),
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        ) from exc

    return OwnerResponse(
        id=str(owner.id),
        full_name=owner.full_name,
        email=owner.email,
        is_active=owner.is_active,
    )


@router.post("/login", response_model=TokenResponse)
def login(
    payload: OwnerLoginRequest,
    db: Session = Depends(get_db),
):
    owner = authenticate_owner(
        db=db,
        email=str(payload.email),
        password=payload.password,
    )

    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenResponse(
        access_token=create_access_token(str(owner.id))
    )


@router.get("/me", response_model=OwnerResponse)
def get_me(
    owner: Owner = Depends(get_current_owner),
):
    return OwnerResponse(
        id=str(owner.id),
        full_name=owner.full_name,
        email=owner.email,
        is_active=owner.is_active,
    )
