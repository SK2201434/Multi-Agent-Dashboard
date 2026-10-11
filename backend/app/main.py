from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import engine
################### APIS ###################
from app.api.auth import router as auth_router
from app.api.properties import router as properties_router
from app.api.public_properties import router as public_properties_router
from app.api.rooms import router as rooms_router
from app.api.beds import router as beds_router
from app.api.tenant_auth import router as tenant_auth_router
from app.api.property_tenants import router as property_tenants_router
from app.services.errors import AppError

settings = get_settings()

app = FastAPI(title=settings.app_name)


@app.exception_handler(AppError)
def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message},
    )


@app.exception_handler(RequestValidationError)
def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # Same shape as FastAPI's default, minus the echoed request values, so
    # passwords and activation tokens are never reflected in error bodies.
    errors = [
        {key: value for key, value in error.items() if key != "input"}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(errors)},
    )


@app.get("/health")
def health_check():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return {
            "status": "healthy",
            "database": "connected",
        }
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unhealthy",
                "database": "unavailable",
            },
        )

app.include_router(auth_router)
app.include_router(properties_router)
app.include_router(public_properties_router)
app.include_router(rooms_router)
app.include_router(beds_router)
app.include_router(tenant_auth_router)
app.include_router(property_tenants_router)
