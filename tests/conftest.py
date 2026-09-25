"""Pytest configuration and shared fixtures.

Every test gets its own empty HOME, so nothing touches the developer's real
files (the CLI keeps its login session in ``~/.portf_session``).

Every test runs behind a network guard: a real outbound HTTP call (requests,
httpx, or yfinance's curl_cffi) fails the test, even when the code under test
swallows the error. Tests that genuinely need the network carry
``@pytest.mark.network`` and are opted in with ``PFM_LIVE_NETWORK_TESTS=1``.
"""

import logging
import sys
from pathlib import Path

import pytest

# Make the project root importable when pytest is run from elsewhere
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.fixtures.test_fixtures import *  # noqa: E402,F401,F403

logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)


def pytest_collection_modifyitems(config, items):
    """Mark tests unit/integration/e2e by directory."""
    for item in items:
        path = Path(str(item.fspath)).parts
        for kind in ("unit", "integration", "e2e"):
            if kind in path:
                item.add_marker(getattr(pytest.mark, kind))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """Point HOME at an empty temp dir for the duration of the test."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def _no_llm_retry_sleep(monkeypatch):
    """LLM retries keep their attempts but don't wait between them."""
    monkeypatch.setenv("PORTF_LLM_RETRY_DELAY", "0")


class RealNetworkCall(ConnectionError):
    """Raised in place of a real outbound HTTP request during tests."""


def _is_loopback(url: str) -> bool:
    host = str(url).split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
    return host in {"127.0.0.1", "localhost", "[::1]"}


@pytest.fixture(autouse=True)
def _no_real_network(request, monkeypatch):
    """Fail the test if it makes a real outbound HTTP call.

    Patched at the HTTP-library level, so in-process ASGI clients still work.
    Loopback is allowed only for tests that start their own server
    (integration/e2e); in unit tests a localhost call means an unmocked
    service probe, e.g. LLM auto-detection pinging Ollama.
    """
    if request.node.get_closest_marker("network"):
        yield
        return

    allow_loopback = bool(
        request.node.get_closest_marker("integration")
        or request.node.get_closest_marker("e2e")
    )
    calls: list[str] = []

    def guard(url: str, original, *args, **kwargs):
        if allow_loopback and _is_loopback(url):
            return original(*args, **kwargs)
        calls.append(str(url))
        raise RealNetworkCall(f"real network call blocked in tests: {url}")

    import requests.adapters

    orig_send = requests.adapters.HTTPAdapter.send
    monkeypatch.setattr(
        requests.adapters.HTTPAdapter,
        "send",
        lambda self, req, *a, **k: guard(req.url, orig_send, self, req, *a, **k),
    )

    import httpx

    orig_hx = httpx.HTTPTransport.handle_request
    monkeypatch.setattr(
        httpx.HTTPTransport,
        "handle_request",
        lambda self, req: guard(req.url, orig_hx, self, req),
    )
    orig_ahx = httpx.AsyncHTTPTransport.handle_async_request

    async def async_guard(self, req):
        return await guard(req.url, orig_ahx, self, req)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_guard)

    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        curl_requests = None
    if curl_requests is not None:
        orig_curl = curl_requests.Session.request
        monkeypatch.setattr(
            curl_requests.Session,
            "request",
            lambda self, method, url, *a, **k: guard(
                url, orig_curl, self, method, url, *a, **k
            ),
        )

    yield
    if calls:
        pytest.fail(
            "Test made real network call(s); mock them or mark the test "
            f"@pytest.mark.network: {sorted(set(calls))}",
            pytrace=False,
        )
