"""
API Key Authentication Middleware for Portfolio Management API

This module provides API key authentication functionality including:
- API key validation middleware
- Key generation and management utilities
- Database operations for API keys
"""

import hashlib
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from fastapi import HTTPException, Request, status
from fastapi.security import HTTPBearer

from portf_manager.database import Database


class APIKeyError(Exception):
    """Base exception for API key related errors."""


class APIKeyManager:
    """Manager class for API key operations."""

    def __init__(self, database: Database):
        """
        Initialize API key manager.

        Args:
            database: Database instance
        """
        self.database = database

    def generate_api_key(self) -> str:
        """
        Generate a new API key.

        Returns:
            str: Generated API key
        """
        # Generate a secure random API key (64 characters)
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(64))

    def _hash_api_key(self, api_key: str) -> str:
        """
        Hash an API key for secure storage.

        Args:
            api_key: Raw API key

        Returns:
            str: Hashed API key
        """
        return hashlib.sha256(api_key.encode()).hexdigest()

    def _get_key_prefix(self, api_key: str) -> str:
        """
        Get the prefix of an API key for identification.

        Args:
            api_key: Raw API key

        Returns:
            str: Key prefix (first 8 characters)
        """
        return api_key[:8]

    def create_api_key(
        self,
        key_name: str,
        description: Optional[str] = None,
        expires_days: Optional[int] = None,
        raw_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new API key in the database.

        Args:
            key_name: Name for the API key
            description: Optional description
            expires_days: Optional expiration in days
            raw_key: Use this specific key value instead of generating one (for env-var seeding)

        Returns:
            dict: API key info including the raw key (only returned once)

        Raises:
            APIKeyError: If key creation fails
        """
        try:
            # Use provided key or generate a new one
            api_key = raw_key if raw_key else self.generate_api_key()
            key_hash = self._hash_api_key(api_key)
            key_prefix = self._get_key_prefix(api_key)

            # Calculate expiration if provided
            expires_at = None
            if expires_days:
                expires_at = datetime.now() + timedelta(days=expires_days)

            # Insert into database
            query = """
                INSERT INTO api_keys (key_name, key_hash, key_prefix, description, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """
            params = (key_name, key_hash, key_prefix, description, expires_at)

            with self.database.get_connection() as conn:
                cursor = conn.execute(query, params)
                key_id = cursor.lastrowid
                conn.commit()

            return {
                "id": key_id,
                "key_name": key_name,
                "api_key": api_key,  # Only returned once during creation
                "key_prefix": key_prefix,
                "description": description,
                "expires_at": expires_at,
                "created_at": datetime.now(),
                "is_active": True,
            }

        except Exception as e:
            raise APIKeyError(f"Failed to create API key: {str(e)}")

    def validate_api_key(self, api_key: str) -> Optional[Dict[str, Any]]:
        """
        Validate an API key and return key information.

        Args:
            api_key: Raw API key to validate

        Returns:
            dict or None: API key information if valid, None otherwise
        """
        try:
            key_hash = self._hash_api_key(api_key)

            query = """
                SELECT id, key_name, key_prefix, description, is_active,
                       last_used, created_at, expires_at
                FROM api_keys
                WHERE key_hash = ? AND is_active = 1
            """

            with self.database.get_connection() as conn:
                cursor = conn.execute(query, (key_hash,))
                row = cursor.fetchone()

                if not row:
                    return None

                key_info = {
                    "id": row[0],
                    "key_name": row[1],
                    "key_prefix": row[2],
                    "description": row[3],
                    "is_active": bool(row[4]),
                    "last_used": row[5],
                    "created_at": row[6],
                    "expires_at": row[7],
                }

                # Check if key is expired
                if key_info["expires_at"]:
                    expires_at = datetime.fromisoformat(key_info["expires_at"])
                    if datetime.now() > expires_at:
                        return None

                # Update last_used timestamp
                self._update_last_used(key_info["id"])

                return key_info

        except Exception:
            return None

    def _update_last_used(self, key_id: int):
        """
        Update the last_used timestamp for an API key.

        Args:
            key_id: API key ID
        """
        try:
            query = "UPDATE api_keys SET last_used = ? WHERE id = ?"
            with self.database.get_connection() as conn:
                conn.execute(query, (datetime.now(), key_id))
                conn.commit()
        except Exception:
            # Log but don't fail the request if timestamp update fails
            pass

    def list_api_keys(self) -> list:
        """
        List all API keys (without the actual keys).

        Returns:
            list: List of API key information
        """
        query = """
            SELECT id, key_name, key_prefix, description, is_active,
                   last_used, created_at, expires_at
            FROM api_keys
            ORDER BY created_at DESC
        """

        with self.database.get_connection() as conn:
            cursor = conn.execute(query)
            rows = cursor.fetchall()

            return [
                {
                    "id": row[0],
                    "key_name": row[1],
                    "key_prefix": row[2],
                    "description": row[3],
                    "is_active": bool(row[4]),
                    "last_used": row[5],
                    "created_at": row[6],
                    "expires_at": row[7],
                }
                for row in rows
            ]

    def deactivate_api_key(self, key_id: int) -> bool:
        """
        Deactivate an API key.

        Args:
            key_id: API key ID to deactivate

        Returns:
            bool: True if successful
        """
        try:
            query = "UPDATE api_keys SET is_active = 0 WHERE id = ?"
            with self.database.get_connection() as conn:
                cursor = conn.execute(query, (key_id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception:
            return False

    def delete_api_key(self, key_id: int) -> bool:
        """
        Permanently delete an API key.

        Args:
            key_id: API key ID to delete

        Returns:
            bool: True if successful
        """
        try:
            query = "DELETE FROM api_keys WHERE id = ?"
            with self.database.get_connection() as conn:
                cursor = conn.execute(query, (key_id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception:
            return False


class APIKeyBearer(HTTPBearer):
    """Custom HTTPBearer that validates API keys."""

    def __init__(self, api_key_manager: APIKeyManager):
        """
        Initialize API key bearer.

        Args:
            api_key_manager: API key manager instance
        """
        super().__init__(auto_error=False)
        self.api_key_manager = api_key_manager

    async def __call__(self, request: Request) -> Optional[Dict[str, Any]]:
        """
        Validate API key from request headers.

        Args:
            request: FastAPI request object

        Returns:
            dict or None: API key info if valid, None otherwise
        """
        # Check for X-API-Key header first
        api_key = request.headers.get("X-API-Key")

        if api_key:
            key_info = self.api_key_manager.validate_api_key(api_key)
            if key_info:
                return key_info

        # Fallback to Authorization header
        credentials = await super().__call__(request)
        if credentials:
            key_info = self.api_key_manager.validate_api_key(credentials.credentials)
            if key_info:
                return key_info

        return None


def require_api_key(api_key_manager: APIKeyManager):
    """
    Dependency factory for requiring API key authentication.

    Args:
        api_key_manager: API key manager instance

    Returns:
        Dependency function
    """
    bearer = APIKeyBearer(api_key_manager)

    async def _require_api_key(request: Request) -> Dict[str, Any]:
        """
        Require valid API key for endpoint access.

        Args:
            request: FastAPI request object

        Returns:
            dict: API key information

        Raises:
            HTTPException: If API key is invalid or missing
        """
        key_info = await bearer(request)

        if not key_info:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Valid API key required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return key_info

    return _require_api_key


def optional_api_key(api_key_manager: APIKeyManager):
    """
    Dependency factory for optional API key authentication.

    Args:
        api_key_manager: API key manager instance

    Returns:
        Dependency function
    """
    bearer = APIKeyBearer(api_key_manager)

    async def _optional_api_key(request: Request) -> Optional[Dict[str, Any]]:
        """
        Optional API key validation for endpoint access.

        Args:
            request: FastAPI request object

        Returns:
            dict or None: API key information if provided and valid
        """
        return await bearer(request)

    return _optional_api_key
