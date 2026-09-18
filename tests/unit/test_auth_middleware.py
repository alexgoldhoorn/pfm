import asyncio

from starlette.requests import Request

from portf_server.auth_middleware import APIKeyBearer


class _FakeAPIKeyManager:
    def __init__(self):
        self.calls = 0

    def validate_api_key(self, key):
        self.calls += 1
        if key == "valid-key":
            return {"id": 1, "key_name": "test"}
        return None


def _request_with_headers(headers):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
    }
    return Request(scope)


def test_api_key_bearer_uses_cached_request_state():
    manager = _FakeAPIKeyManager()
    bearer = APIKeyBearer(manager)
    request = _request_with_headers([(b"x-api-key", b"valid-key")])
    request.state.api_key_info = {"id": 99, "key_name": "cached"}

    key_info = asyncio.run(bearer(request))

    assert key_info == {"id": 99, "key_name": "cached"}
    assert manager.calls == 0


def test_api_key_bearer_caches_first_successful_validation():
    manager = _FakeAPIKeyManager()
    bearer = APIKeyBearer(manager)
    request = _request_with_headers([(b"x-api-key", b"valid-key")])

    first = asyncio.run(bearer(request))
    second = asyncio.run(bearer(request))

    assert first == {"id": 1, "key_name": "test"}
    assert second == first
    assert request.state.api_key_info == first
    assert manager.calls == 1
