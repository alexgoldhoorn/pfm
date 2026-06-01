"""
User Authentication System for Portfolio Manager

This module provides user authentication, registration, and session management
functionality for the portfolio management application.
"""

import hashlib
import secrets
import os
from datetime import datetime, timedelta
from typing import Optional, Dict
import json
from pathlib import Path


class AuthenticationError(Exception):
    """Custom exception for authentication-related errors."""


class UserSession:
    """Represents a user session with authentication state."""

    def __init__(
        self, user_id: int, username: str, email: str, session_token: str = None
    ):
        self.user_id = user_id
        self.username = username
        self.email = email
        self.session_token = session_token or self._generate_session_token()
        self.created_at = datetime.now()
        self.last_activity = datetime.now()

    def _generate_session_token(self) -> str:
        """Generate a secure session token."""
        return secrets.token_urlsafe(32)

    def is_expired(self, max_age_hours: int = 24) -> bool:
        """Check if the session has expired."""
        return datetime.now() - self.last_activity > timedelta(hours=max_age_hours)

    def refresh(self):
        """Refresh the session's last activity timestamp."""
        self.last_activity = datetime.now()

    def to_dict(self) -> Dict:
        """Convert session to dictionary for serialization."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email,
            "session_token": self.session_token,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "UserSession":
        """Create session from dictionary."""
        session = cls(
            user_id=data["user_id"],
            username=data["username"],
            email=data["email"],
            session_token=data["session_token"],
        )
        session.created_at = datetime.fromisoformat(data["created_at"])
        session.last_activity = datetime.fromisoformat(data["last_activity"])
        return session


class AuthManager:
    """Manages user authentication and session state."""

    def __init__(self, db_manager, session_file: str = ".portf_session"):
        self.db_manager = db_manager
        self.session_file = Path.home() / session_file
        self.current_session: Optional[UserSession] = None
        self._load_session()

    def _hash_password(self, password: str, salt: str = None) -> tuple[str, str]:
        """Hash password with salt."""
        if salt is None:
            salt = secrets.token_hex(32)

        # Use PBKDF2 with SHA256
        password_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100000,  # 100k iterations
        )
        return password_hash.hex(), salt

    def _verify_password(self, password: str, password_hash: str, salt: str) -> bool:
        """Verify password against hash."""
        computed_hash, _ = self._hash_password(password, salt)
        return computed_hash == password_hash

    def register_user(
        self, username: str, email: str, password: str, full_name: str = None
    ) -> int:
        """Register a new user."""
        # Check if user already exists
        if self.db_manager.get_user_by_username(username):
            raise AuthenticationError(f"Username '{username}' already exists")

        if self.db_manager.get_user_by_email(email):
            raise AuthenticationError(f"Email '{email}' already registered")

        # Hash password
        password_hash, salt = self._hash_password(password)

        # Create user
        user_id = self.db_manager.create_user(
            username=username,
            email=email,
            password_hash=password_hash,
            salt=salt,
            full_name=full_name,
        )

        return user_id

    def login(self, username: str, password: str) -> UserSession:
        """Authenticate user and create session."""
        # Get user by username or email
        user = self.db_manager.get_user_by_username(username)
        if not user:
            user = self.db_manager.get_user_by_email(username)

        if not user:
            raise AuthenticationError("Invalid username or password")

        # Check if user is active
        if not user.get("is_active", True):
            raise AuthenticationError("User account is disabled")

        # Verify password
        if not self._verify_password(password, user["password_hash"], user["salt"]):
            raise AuthenticationError("Invalid username or password")

        # Update last login
        self.db_manager.update_user_last_login(user["id"])

        # Create session
        session = UserSession(
            user_id=user["id"], username=user["username"], email=user["email"]
        )

        self.current_session = session
        self._save_session()

        return session

    def logout(self):
        """Logout current user and clear session."""
        if self.current_session:
            self.current_session = None
            self._clear_session()

    def get_current_user(self) -> Optional[Dict]:
        """Get current authenticated user."""
        if not self.current_session:
            return None

        if self.current_session.is_expired():
            self.logout()
            return None

        self.current_session.refresh()
        self._save_session()

        return self.db_manager.get_user(self.current_session.user_id)

    def is_authenticated(self) -> bool:
        """Check if user is authenticated."""
        return self.get_current_user() is not None

    def require_authentication(self) -> UserSession:
        """Ensure user is authenticated, raise exception if not."""
        if not self.is_authenticated():
            raise AuthenticationError("Authentication required")
        return self.current_session

    def change_password(self, current_password: str, new_password: str):
        """Change user's password."""
        user = self.get_current_user()
        if not user:
            raise AuthenticationError("Not authenticated")

        # Verify current password
        if not self._verify_password(
            current_password, user["password_hash"], user["salt"]
        ):
            raise AuthenticationError("Current password is incorrect")

        # Hash new password
        password_hash, salt = self._hash_password(new_password)

        # Update password
        self.db_manager.update_user_password(user["id"], password_hash, salt)

    def _save_session(self):
        """Save current session to file."""
        if not self.current_session:
            return

        try:
            with open(self.session_file, "w") as f:
                json.dump(self.current_session.to_dict(), f)

            # Set restrictive permissions
            os.chmod(self.session_file, 0o600)
        except Exception:
            # If we can't save session, continue without persistence
            pass

    def _load_session(self):
        """Load session from file."""
        try:
            if self.session_file.exists():
                with open(self.session_file, "r") as f:
                    session_data = json.load(f)

                session = UserSession.from_dict(session_data)

                # Check if session is expired
                if not session.is_expired():
                    self.current_session = session
                else:
                    self._clear_session()
        except Exception:
            # If we can't load session, start fresh
            self._clear_session()

    def _clear_session(self):
        """Clear session file."""
        try:
            if self.session_file.exists():
                self.session_file.unlink()
        except Exception:
            pass


def prompt_for_credentials(prompt_text: str = "Login") -> tuple[str, str]:
    """Prompt user for username and password."""
    import getpass

    print(f"\n🔐 {prompt_text}")
    username = input("Username or email: ").strip()
    password = getpass.getpass("Password: ")

    return username, password


def prompt_for_registration() -> Dict[str, str]:
    """Prompt user for registration details."""
    import getpass

    print("\n📝 User Registration")
    username = input("Username: ").strip()
    email = input("Email: ").strip()
    full_name = input("Full name (optional): ").strip() or None

    password = getpass.getpass("Password: ")
    confirm_password = getpass.getpass("Confirm password: ")

    if password != confirm_password:
        raise AuthenticationError("Passwords do not match")

    return {
        "username": username,
        "email": email,
        "full_name": full_name,
        "password": password,
    }
