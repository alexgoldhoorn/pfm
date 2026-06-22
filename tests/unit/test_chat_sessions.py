"""Tests for chat session REST endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestChatSessionEndpoints:
    async def test_list_sessions_empty(
        self, async_test_client: AsyncClient, auth_headers
    ):
        response = await async_test_client.get(
            "/api/v1/llm/chat/sessions", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json() == []

    async def test_create_session(self, async_test_client: AsyncClient, auth_headers):
        response = await async_test_client.post(
            "/api/v1/llm/chat/sessions",
            json={"name": "Test Thread"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Test Thread"
        assert "id" in data

    async def test_list_sessions_after_create(
        self, async_test_client: AsyncClient, auth_headers
    ):
        await async_test_client.post(
            "/api/v1/llm/chat/sessions",
            json={"name": "Alpha"},
            headers=auth_headers,
        )
        response = await async_test_client.get(
            "/api/v1/llm/chat/sessions", headers=auth_headers
        )
        assert response.status_code == 200
        sessions = response.json()
        assert any(s["name"] == "Alpha" for s in sessions)

    async def test_delete_session(self, async_test_client: AsyncClient, auth_headers):
        create_resp = await async_test_client.post(
            "/api/v1/llm/chat/sessions",
            json={"name": "ToDelete"},
            headers=auth_headers,
        )
        session_id = create_resp.json()["id"]
        del_resp = await async_test_client.delete(
            f"/api/v1/llm/chat/sessions/{session_id}", headers=auth_headers
        )
        assert del_resp.status_code == 204

        list_resp = await async_test_client.get(
            "/api/v1/llm/chat/sessions", headers=auth_headers
        )
        assert not any(s["id"] == session_id for s in list_resp.json())

    async def test_delete_nonexistent_session_returns_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        response = await async_test_client.delete(
            "/api/v1/llm/chat/sessions/nonexistent-id", headers=auth_headers
        )
        assert response.status_code == 404

    async def test_get_session_messages_empty(
        self, async_test_client: AsyncClient, auth_headers
    ):
        create_resp = await async_test_client.post(
            "/api/v1/llm/chat/sessions",
            json={"name": "Empty"},
            headers=auth_headers,
        )
        session_id = create_resp.json()["id"]
        msg_resp = await async_test_client.get(
            f"/api/v1/llm/chat/sessions/{session_id}/messages", headers=auth_headers
        )
        assert msg_resp.status_code == 200
        assert msg_resp.json() == {"messages": []}
