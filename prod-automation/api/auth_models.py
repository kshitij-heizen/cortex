from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    avatarUrl: Optional[str] = None


class AuthResponse(BaseModel):
    token: str
    user: UserResponse


class UserDocument(BaseModel):
    """Internal representation of a user stored in MongoDB."""

    email: str
    name: str
    password_hash: str
    avatar_url: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
