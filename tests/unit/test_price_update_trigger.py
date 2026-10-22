"""The trigger-price-update endpoint forwards symbols and source to the updater.

The CLI in server mode delegates its whole run to this endpoint (the updater
needs the real Database, which the HTTP client is not), so the endpoint must
carry the CLI's two inputs: an optional symbol filter and the run's source
label.
"""

import threading
import time

from fastapi.testclient import TestClient

from portf_manager.database import Database

_TEST_API_KEY = "test-key-trigger-abc123"
HEADERS = {"X-API-Key": _TEST_API_KEY}
TRIGGER = "/api/v1/analytics/trigger-price-update"
STATUS = "/api/v1/analytics/price-update-status"


def _make_client(tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    db = Database(str(tmp_path / "api_test.db"))
    km = APIKeyManager(db)
    km.create_api_key(key_name="test", description="test key", raw_key=_TEST_API_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km
    return TestClient(app)


def _capture_updater(monkeypatch):
    """Replace run_price_update with a recorder; returns (calls, done_event)."""
    calls = []
    done = threading.Event()

    def fake(db, symbols=None, source="api"):
        calls.append({"symbols": symbols, "source": source})
        done.set()
        return {}

    monkeypatch.setattr("portf_manager.services.price_updater.run_price_update", fake)
    return calls, done


def _wait_idle(client):
    """The run happens on a background thread; wait until the state is clear
    so the next test's trigger is not refused with 409."""
    for _ in range(50):
        if not client.get(STATUS, headers=HEADERS).json()["running"]:
            return
        time.sleep(0.01)
    raise AssertionError("price update still marked running")


def test_trigger_defaults_to_every_asset_with_api_source(tmp_path, monkeypatch):
    client = _make_client(tmp_path)
    calls, done = _capture_updater(monkeypatch)

    resp = client.post(TRIGGER, headers=HEADERS)

    assert resp.status_code == 200 and resp.json()["status"] == "started"
    assert done.wait(5)
    _wait_idle(client)
    assert calls == [{"symbols": None, "source": "api"}]


def test_trigger_forwards_symbols_and_source(tmp_path, monkeypatch):
    client = _make_client(tmp_path)
    calls, done = _capture_updater(monkeypatch)

    resp = client.post(
        TRIGGER + "?symbols=EXA&symbols=OTH&source=manual", headers=HEADERS
    )

    assert resp.status_code == 200
    assert done.wait(5)
    _wait_idle(client)
    assert calls == [{"symbols": ["EXA", "OTH"], "source": "manual"}]


def test_trigger_rejects_an_unknown_source(tmp_path, monkeypatch):
    client = _make_client(tmp_path)
    calls, _ = _capture_updater(monkeypatch)

    resp = client.post(TRIGGER + "?source=bogus", headers=HEADERS)

    assert resp.status_code == 422
    assert calls == []
