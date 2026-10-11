
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class OwnerRegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class OwnerLoginRequest(BaseModel):
    email: EmailStr
    password: str


class OwnerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    full_name: str
    email: EmailStr
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
