"""Tests for GET /api/v1/analytics/risk (flow-adjusted risk metrics)."""

from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from portf_manager.database import Database

_TEST_API_KEY = "test-key-risk-abc123"
HEADERS = {"X-API-Key": _TEST_API_KEY}
DAYS = 400


@pytest.fixture
def client_db(tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    db = Database(str(tmp_path / "risk.db"))
    km = APIKeyManager(db)
    km.create_api_key(key_name="test", description="test key", raw_key=_TEST_API_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km
    yield TestClient(app), db
    app.dependency_overrides.clear()


def _seed(db: Database) -> date:
    """Steady +0.1%/day growth, with a 10k buy in the middle."""
    start = date.today() - timedelta(days=DAYS - 1)
    value, cost = 1000.0, 1000.0
    for i in range(DAYS):
        if i == DAYS // 2:
            value += 10_000
            cost += 10_000
        elif i > 0:
            value *= 1.001 if i % 2 else 1.0005
        db.record_snapshot((start + timedelta(days=i)).isoformat(), value, cost)
    return start


def _bench_frame(start: date) -> pd.DataFrame:
    days = [start + timedelta(days=i) for i in range(DAYS)]
    closes = [100.0 * (1.0005**i) for i in range(DAYS)]
    return pd.DataFrame({"Close": closes}, index=pd.to_datetime(days))


def test_buy_does_not_count_as_return(client_db):
    client, db = client_db
    start = _seed(db)
    with patch(
        "portf_server.routers.analytics.yf.download",
        return_value=_bench_frame(start),
    ):
        r = client.get("/api/v1/analytics/risk", headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    # 399 returns alternating +0.1% / +0.05%, nothing from the 10k buy.
    expected_growth = (1.001**200) * (1.0005**198)
    assert d["period_return_pct"] == pytest.approx(
        (expected_growth - 1) * 100, abs=0.01
    )
    assert d["max_drawdown_pct"] == 0.0
    assert d["current_drawdown_pct"] == 0.0
    assert d["volatility_pct"] < 1
    assert d["sharpe_ratio"] > 10
    assert d["low_confidence"] is False
    assert d["benchmark_return_pct"] == pytest.approx(
        (1.0005 ** (DAYS - 1) - 1) * 100, abs=0.01
    )
    assert d["beta"] is not None
    assert "returns" not in d


def test_one_year_window(client_db):
    client, db = client_db
    start = _seed(db)
    with patch(
        "portf_server.routers.analytics.yf.download",
        return_value=_bench_frame(start),
    ):
        r = client.get("/api/v1/analytics/risk?window=1y", headers=HEADERS)
    d = r.json()
    assert d["window"] == "1y"
    assert d["history_days"] == 365
    assert d["start_date"] == (date.today() - timedelta(days=365)).isoformat()


def test_rejects_unknown_window(client_db):
    client, _ = client_db
    r = client.get("/api/v1/analytics/risk?window=5y", headers=HEADERS)
    assert r.status_code == 422


def test_insufficient_history(client_db):
    client, db = client_db
    db.record_snapshot(date.today().isoformat(), 100.0, 100.0)
    r = client.get("/api/v1/analytics/risk", headers=HEADERS)
    d = r.json()
    assert d["sharpe_ratio"] is None
    assert d["note"]
