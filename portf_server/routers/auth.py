"""
Authentication Router for Portfolio Management API

Handles user registration, login, logout, and user management.
"""

import os

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer
from pydantic import BaseModel

from portf_manager.auth import AuthManager, AuthenticationError
from portf_manager.database import Database

from ..dependencies import get_database, get_auth_manager, get_current_user
from ..schemas.auth import (
    UserRegistrationRequest,
    UserLoginRequest,
    UserResponse,
    LoginResponse,
    ChangePasswordRequest,
    MessageResponse,
)

router = APIRouter()
security = HTTPBearer()


class LoginKeyResponse(BaseModel):
    """Response for password login that returns the API key for data calls."""

    api_key: str
    username: str


@router.post("/login-key", response_model=LoginKeyResponse)
async def login_for_api_key(
    login_data: UserLoginRequest,
    auth_manager: AuthManager = Depends(get_auth_manager),
):
    """Validate username/password and return the server API key.

    The web client uses ``X-API-Key`` for all data endpoints. This bridges a
    username/password login to that scheme: on valid credentials it returns the
    configured ``SERVER_API_KEY`` so the browser can use it for subsequent calls.
    """
    try:
        auth_manager.login(login_data.username, login_data.password)
    except AuthenticationError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    api_key = os.getenv("SERVER_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server API key is not configured",
        )
    return LoginKeyResponse(api_key=api_key, username=login_data.username)


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register_user(
    user_data: UserRegistrationRequest,
    auth_manager: AuthManager = Depends(get_auth_manager),
    db: Database = Depends(get_database),
):
    """
    Register a new user.

    Args:
        user_data: User registration data
        auth_manager: Authentication manager
        db: Database instance

    Returns:
        UserResponse: Created user information

    Raises:
        HTTPException: If registration fails
    """
    try:
        user_id = auth_manager.register_user(
            username=user_data.username,
            email=user_data.email,
            password=user_data.password,
            full_name=user_data.full_name,
        )

        # Get the created user
        user_dict = db.get_user(user_id)
        if not user_dict:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve created user",
            )

        return UserResponse(**user_dict)

    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed",
        )


@router.post("/login", response_model=LoginResponse)
async def login_user(
    login_data: UserLoginRequest,
    auth_manager: AuthManager = Depends(get_auth_manager),
    db: Database = Depends(get_database),
):
    """
    Login user and create session.

    Args:
        login_data: Login credentials
        auth_manager: Authentication manager
        db: Database instance

    Returns:
        LoginResponse: Login response with token and user info

    Raises:
        HTTPException: If login fails
    """
    try:
        session = auth_manager.login(login_data.username, login_data.password)

        # Get full user data
        user_dict = db.get_user(session.user_id)
        if not user_dict:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve user data",
            )

        return LoginResponse(
            access_token=session.session_token,
            token_type="bearer",
            user=UserResponse(**user_dict),
        )

    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed",
        )


@router.post("/logout", response_model=MessageResponse)
async def logout_user(
    auth_manager: AuthManager = Depends(get_auth_manager),
    current_user=Depends(get_current_user),
):
    """
    Logout current user and clear session.

    Args:
        auth_manager: Authentication manager
        current_user: Current authenticated user

    Returns:
        MessageResponse: Logout confirmation
    """
    try:
        auth_manager.logout()
        return MessageResponse(message="Successfully logged out")

    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Logout failed",
        )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user=Depends(get_current_user),
    db: Database = Depends(get_database),
):
    """
    Get current user information.

    Args:
        current_user: Current authenticated user
        db: Database instance

    Returns:
        UserResponse: Current user information
    """
    try:
        user_dict = db.get_user(current_user.user_id)
        if not user_dict:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        return UserResponse(**user_dict)

    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user information",
        )


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    password_data: ChangePasswordRequest,
    auth_manager: AuthManager = Depends(get_auth_manager),
    current_user=Depends(get_current_user),
):
    """
    Change user password.

    Args:
        password_data: Password change data
        auth_manager: Authentication manager
        current_user: Current authenticated user

    Returns:
        MessageResponse: Password change confirmation

    Raises:
        HTTPException: If password change fails
    """
    try:
        auth_manager.change_password(
            password_data.current_password,
            password_data.new_password,
        )

        return MessageResponse(message="Password changed successfully")

    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Password change failed",
        )
