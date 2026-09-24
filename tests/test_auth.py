"""Tests for AuthManager: registration, login, sessions and password changes."""

import json
from datetime import datetime, timedelta

import pytest

from portf_manager.auth import AuthenticationError, AuthManager, UserSession
from portf_manager.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "auth.db"))


@pytest.fixture
def auth(db):
    manager = AuthManager(db)
    manager.register_user("alice", "alice@example.com", "s3cret-pass", "Alice")
    return manager


def test_register_rejects_duplicate_username_and_email(auth):
    with pytest.raises(AuthenticationError, match="Username"):
        auth.register_user("alice", "other@example.com", "x")
    with pytest.raises(AuthenticationError, match="Email"):
        auth.register_user("bob", "alice@example.com", "x")


def test_password_is_stored_hashed_and_salted(auth, db):
    user = db.get_user_by_username("alice")
    assert "s3cret-pass" not in (user["password_hash"], user["salt"])
    auth.register_user("bob", "bob@example.com", "s3cret-pass")
    assert db.get_user_by_username("bob")["password_hash"] != user["password_hash"]


def test_login_by_username_or_email(auth):
    assert auth.login("alice", "s3cret-pass").username == "alice"
    assert auth.login("alice@example.com", "s3cret-pass").username == "alice"
    assert auth.get_current_user()["email"] == "alice@example.com"


@pytest.mark.parametrize("user,password", [("alice", "wrong"), ("nobody", "x")])
def test_login_failures_share_one_message(auth, user, password):
    # Same message for unknown user and bad password: no username probing
    with pytest.raises(AuthenticationError, match="Invalid username or password"):
        auth.login(user, password)
    assert auth.get_current_user() is None


def test_disabled_account_cannot_log_in(auth, db, monkeypatch):
    user = db.get_user_by_username("alice")
    monkeypatch.setattr(db, "get_user_by_username", lambda u: {**user, "is_active": 0})
    with pytest.raises(AuthenticationError, match="disabled"):
        auth.login("alice", "s3cret-pass")


def test_session_survives_restart_and_logout_clears_it(auth, db, tmp_path):
    auth.login("alice", "s3cret-pass")
    assert (AuthManager(db).get_current_user() or {}).get("username") == "alice"

    auth.logout()
    assert not auth.session_file.exists()
    assert AuthManager(db).get_current_user() is None


def test_session_file_is_private(auth):
    auth.login("alice", "s3cret-pass")
    assert auth.session_file.stat().st_mode & 0o777 == 0o600


def test_expired_session_is_discarded_on_load(auth, db):
    auth.login("alice", "s3cret-pass")
    data = json.loads(auth.session_file.read_text())
    data["last_activity"] = (datetime.now() - timedelta(hours=25)).isoformat()
    auth.session_file.write_text(json.dumps(data))

    assert AuthManager(db).get_current_user() is None
    assert not auth.session_file.exists()


def test_corrupt_session_file_starts_logged_out(auth, db):
    auth.session_file.write_text("{not json")
    assert AuthManager(db).get_current_user() is None


def test_session_expiry_boundary():
    session = UserSession(1, "alice", "alice@example.com")
    session.last_activity = datetime.now() - timedelta(hours=23, minutes=59)
    assert not session.is_expired()
    session.last_activity = datetime.now() - timedelta(hours=24, minutes=1)
    assert session.is_expired()


def test_change_password(auth):
    auth.login("alice", "s3cret-pass")
    with pytest.raises(AuthenticationError, match="incorrect"):
        auth.change_password("wrong", "new-pass-456")

    auth.change_password("s3cret-pass", "new-pass-456")
    auth.logout()
    assert auth.login("alice", "new-pass-456").username == "alice"
    with pytest.raises(AuthenticationError):
        auth.login("alice", "s3cret-pass")


def test_change_password_requires_login(auth):
    with pytest.raises(AuthenticationError, match="Not authenticated"):
        auth.change_password("s3cret-pass", "x")
