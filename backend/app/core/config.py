from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Hostel PG Management API"
    # Secure by default: development-only features need ENVIRONMENT=development.
    environment: str = "production"
    database_url: str


    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # Tenant invitations. No email/SMS provider exists yet, so the raw token
    # is never delivered. For local development only, DEV_EXPOSE_ACTIVATION_TOKENS=true
    # returns it in the owner create response, and only if ENVIRONMENT=development.
    activation_token_expire_hours: int = 72
    dev_expose_activation_tokens: bool = False

    @model_validator(mode="after")
    def forbid_token_exposure_outside_development(self) -> "Settings":
        if self.dev_expose_activation_tokens and self.environment != "development":
            raise ValueError(
                "DEV_EXPOSE_ACTIVATION_TOKENS may only be enabled when "
                "ENVIRONMENT=development."
            )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()