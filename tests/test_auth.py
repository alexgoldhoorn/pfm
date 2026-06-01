#!/usr/bin/env python3
"""
Test script for the user authentication system.
"""

import sys
import os
import pathlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "portf_manager"))

from portf_manager.database import Database  # noqa: E402
from portf_manager.auth import AuthManager, AuthenticationError  # noqa: E402

_TEST_DB = "test_auth.db"


def test_auth_system():
    """Test the authentication system."""
    # Clean up any leftover DB from a previous run
    pathlib.Path(_TEST_DB).unlink(missing_ok=True)

    print("🧪 Testing Portfolio Manager Authentication System")
    print("=" * 60)

    # Initialize database and auth manager
    db = Database(_TEST_DB)
    auth = AuthManager(db)

    try:
        # Test user registration
        print("\n1. Testing User Registration")
        print("-" * 30)

        user_id = auth.register_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            full_name="Test User",
        )
        print(f"✅ User registered successfully with ID: {user_id}")

        # Test duplicate registration (should fail)
        try:
            auth.register_user(
                username="testuser", email="test2@example.com", password="testpass123"
            )
            print("❌ Duplicate username should have failed!")
        except AuthenticationError as e:
            print(f"✅ Duplicate username properly rejected: {e}")

        # Test login
        print("\n2. Testing User Login")
        print("-" * 30)

        session = auth.login("testuser", "testpass123")
        print(f"✅ Login successful! Session token: {session.session_token[:10]}...")

        # Test current user
        current_user = auth.get_current_user()
        print(f"✅ Current user: {current_user['username']} ({current_user['email']})")

        # Test wrong password
        try:
            auth.login("testuser", "wrongpass")
            print("❌ Wrong password should have failed!")
        except AuthenticationError as e:
            print(f"✅ Wrong password properly rejected: {e}")

        # Test logout
        print("\n3. Testing User Logout")
        print("-" * 30)

        auth.logout()
        current_user = auth.get_current_user()
        if current_user is None:
            print("✅ Logout successful - no current user")
        else:
            print("❌ Logout failed - user still logged in")

        # Test session persistence
        print("\n4. Testing Session Persistence")
        print("-" * 30)

        # Login again
        session = auth.login("testuser", "testpass123")
        print("✅ Logged in again")

        # Create new auth manager (simulates app restart)
        auth2 = AuthManager(db)
        current_user = auth2.get_current_user()

        if current_user and current_user["username"] == "testuser":
            print("✅ Session persistence works!")
        else:
            print("❌ Session persistence failed")

        # Test password change
        print("\n5. Testing Password Change")
        print("-" * 30)

        auth2.change_password("testpass123", "newpass456")
        print("✅ Password changed successfully")

        # Test login with new password
        auth2.logout()
        session = auth2.login("testuser", "newpass456")
        print("✅ Login with new password successful")

        # Test login with old password (should fail)
        auth2.logout()
        try:
            auth2.login("testuser", "testpass123")
            print("❌ Old password should have failed!")
        except AuthenticationError as e:
            print(f"✅ Old password properly rejected: {e}")

    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback

        traceback.print_exc()

    finally:
        # Cleanup
        print("\n6. Cleanup")
        print("-" * 30)
        try:
            os.remove("test_auth.db")
            print("✅ Test database cleaned up")
        except Exception:
            pass

        try:
            session_file = pathlib.Path.home() / ".portf_session"
            if session_file.exists():
                session_file.unlink()
                print("✅ Session file cleaned up")
        except Exception:
            pass

    print("\n🎉 Authentication system test completed!")


if __name__ == "__main__":
    test_auth_system()
