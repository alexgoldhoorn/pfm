"""
Authentication Schemas for Portfolio Management API

Pydantic models for authentication-related requests and responses.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class UserRegistrationRequest(BaseModel):
    """Schema for user registration request."""

    username: str = Field(
        ..., min_length=3, max_length=50, description="Unique username"
    )
    email: EmailStr = Field(..., description="User email address")
    password: str = Field(
        ..., min_length=8, description="User password (min 8 characters)"
    )
    full_name: Optional[str] = Field(
        None, max_length=100, description="User's full name"
    )


class UserLoginRequest(BaseModel):
    """Schema for user login request."""

    username: str = Field(..., description="Username or email address")
    password: str = Field(..., description="User password")


class UserResponse(BaseModel):
    """Schema for user information response."""

    id: int
    username: str
    email: str
    full_name: Optional[str] = None
    is_active: bool
    last_login: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LoginResponse(BaseModel):
    """Schema for login response."""

    access_token: str = Field(..., description="Authentication token")
    token_type: str = Field(default="bearer", description="Token type")
    user: UserResponse


class ChangePasswordRequest(BaseModel):
    """Schema for change password request."""

    current_password: str = Field(..., description="Current password")
    new_password: str = Field(
        ..., min_length=8, description="New password (min 8 characters)"
    )


class MessageResponse(BaseModel):
    """Schema for simple message responses."""

    message: str = Field(..., description="Response message")
