"""
Pydantic schemas for authentication and user management.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.modules.auth.models import UserRole


# --- Request Schemas ---


class UserRegister(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    timezone: str = Field(default="UTC", max_length=50)
    country: str = Field(default="", max_length=100)
    language: str = Field(default="en", max_length=10)
    currency: str = Field(default="USD", max_length=10)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenRefresh(BaseModel):
    refresh_token: str


class UserUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    timezone: Optional[str] = Field(None, max_length=50)
    country: Optional[str] = Field(None, max_length=100)
    language: Optional[str] = Field(None, max_length=10)
    currency: Optional[str] = Field(None, max_length=10)


class UserRoleUpdate(BaseModel):
    role: UserRole


# --- Response Schemas ---


class UserResponse(BaseModel):
    id: UUID
    name: str
    email: str
    timezone: str
    country: str
    language: str
    currency: str
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class MessageResponse(BaseModel):
    message: str
