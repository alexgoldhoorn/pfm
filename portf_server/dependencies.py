"""
FastAPI Dependencies for Portfolio Management System

This module provides dependency injection for database and authentication management.
"""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from portf_manager.database import Database
from portf_manager.auth import AuthManager, UserSession, AuthenticationError
from .auth_middleware import APIKeyManager, require_api_key, optional_api_key

# Security scheme for JWT tokens
security = HTTPBearer()


def get_database() -> Database:
    """
    Dependency to get database instance.
    This will be overridden by the main app with the actual database instance.
    """
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not available"
    )


def get_auth_manager() -> AuthManager:
    """
    Dependency to get authentication manager instance.
    This will be overridden by the main app with the actual auth manager instance.
    """
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Authentication manager not available",
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    auth_manager: AuthManager = Depends(get_auth_manager),
) -> UserSession:
    """
    Dependency to get current authenticated user from token.

    Args:
        credentials: HTTP authorization credentials
        auth_manager: Authentication manager instance

    Returns:
        UserSession: Current user session

    Raises:
        HTTPException: If authentication fails
    """
    try:
        # For now, we'll use a simple token-based approach
        # In a production system, you'd want to use proper JWT validation
        token = credentials.credentials

        # Check if there's a current session with matching token
        current_session = auth_manager.current_session
        if not current_session or current_session.session_token != token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Check if session is expired
        if current_session.is_expired():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Refresh session activity
        current_session.refresh()

        return current_session

    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication error",
        )


def get_current_user_id(current_user: UserSession = Depends(get_current_user)) -> int:
    """
    Dependency to get current user ID.

    Args:
        current_user: Current user session

    Returns:
        int: User ID
    """
    return current_user.user_id


def get_api_key_manager() -> "APIKeyManager":
    """
    Dependency to get API key manager instance.
    This will be overridden by the main app with the actual API key manager instance.
    """
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="API key manager not available",
    )


def get_optional_current_user(
    auth_manager: AuthManager = Depends(get_auth_manager),
) -> UserSession:
    """
    Dependency to get current user without requiring authentication.
    Used for endpoints that work with or without authentication.

    Args:
        auth_manager: Authentication manager instance

    Returns:
        UserSession or None: Current user session if authenticated, None otherwise
    """
    try:
        current_user = auth_manager.get_current_user()
        return auth_manager.current_session if current_user else None
    except Exception:
        return None


async def require_api_key_dep(
    request: Request,
    api_key_manager: "APIKeyManager" = Depends(get_api_key_manager),
) -> dict:
    """Router-level API key authentication dependency.

    Applied via ``include_router(..., dependencies=[Depends(require_api_key_dep)])``
    so every endpoint in a data router is protected by default — no per-endpoint
    dependency to forget. Raises 401 when the ``X-API-Key`` header is missing or
    invalid.
    """
    return await require_api_key(api_key_manager)(request)


def get_api_key_auth(api_key_manager: "APIKeyManager" = Depends(get_api_key_manager)):
    """
    Dependency factory to create API key authentication dependency.

    Args:
        api_key_manager: API key manager instance

    Returns:
        API key authentication dependency
    """
    return require_api_key(api_key_manager)


def get_optional_api_key_auth(
    api_key_manager: "APIKeyManager" = Depends(get_api_key_manager),
):
    """
    Dependency factory to create optional API key authentication dependency.

    Args:
        api_key_manager: API key manager instance

    Returns:
        Optional API key authentication dependency
    """
    return optional_api_key(api_key_manager)
