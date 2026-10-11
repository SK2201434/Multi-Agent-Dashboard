from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import engine
################### APIS ###################
from app.api.auth import router as auth_router
from app.api.properties import router as properties_router
from app.api.public_properties import router as public_properties_router
from app.api.rooms import router as rooms_router

settings = get_settings()

app = FastAPI(title=settings.app_name)


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