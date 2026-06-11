"""Tests for registration gating and login rate-limiting (Phase 2 hardening)."""

import uuid

import pytest
from fastapi import status
from httpx import AsyncClient

from portf_server.settings import get_settings


def _user_payload():
    suffix = uuid.uuid4().hex[:8]
    return {
        "username": f"gate_{suffix}",
        "email": f"gate_{suffix}@example.com",
        "password": "secret12345",
    }


class TestRegistrationGate:
    @pytest.mark.asyncio
    async def test_register_blocked_when_disabled(self, async_test_client: AsyncClient):
        """With allow_registration False, /register returns 403."""
        # test_app enables registration by default; flip it off for this test.
        settings = get_settings()
        prev = settings.allow_registration
        settings.allow_registration = False
        try:
            resp = await async_test_client.post(
                "/api/v1/auth/register", json=_user_payload()
            )
            assert resp.status_code == status.HTTP_403_FORBIDDEN
        finally:
            settings.allow_registration = prev

    @pytest.mark.asyncio
    async def test_register_allowed_when_enabled(self, async_test_client: AsyncClient):
        """With allow_registration True (test default), /register succeeds."""
        resp = await async_test_client.post(
            "/api/v1/auth/register", json=_user_payload()
        )
        assert resp.status_code == status.HTTP_201_CREATED


class TestLoginRateLimit:
    @pytest.mark.asyncio
    async def test_repeated_bad_logins_are_throttled(
        self, async_test_client: AsyncClient
    ):
        """After the attempt budget, /login-key returns 429 instead of 401."""
        creds = {"username": f"rl_{uuid.uuid4().hex[:8]}", "password": "nope"}
        seen_429 = False
        # Budget is 5/min; the 6th attempt within the window must be throttled.
        for _ in range(7):
            resp = await async_test_client.post("/api/v1/auth/login-key", json=creds)
            if resp.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                seen_429 = True
                break
            assert resp.status_code == status.HTTP_401_UNAUTHORIZED
        assert seen_429, "Expected a 429 after exceeding the login attempt budget"
