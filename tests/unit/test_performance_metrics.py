"""Performance metrics: EUR conversion, total return, TWR, benchmark, €STR, beta."""

from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from portf_manager.database import Database
from portf_manager.services import risk_free
from portf_manager.services.analytics_service import (
    weekly_from_closes,
    weekly_from_daily,
)
from portf_manager.services.performance import (
    compute_performance,
    lifetime_twr,
    portfolio_totals,
    to_eur_closes,
    to_eur_transactions,
)

_KEY = "test-key-perf-abc123"
# Captured at import, before the autouse conftest stub replaces it per test.
_REAL_FETCH_ESTR = risk_free._fetch_estr
HEADERS = {"X-API-Key": _KEY}


def _tx(t: str, day: str, qty: float, total: float, cur: str = "USD") -> dict:
    return {
        "asset_id": 1,
        "transaction_type": t,
        "transaction_date": day,
        "quantity": qty,
        "total_amount": total,
        "currency": cur,
    }


class TestCurrency:
    def test_cost_stays_at_purchase_rate_and_fx_move_is_return(self):
        # $1000 bought at 0.9 €/$. The price doesn't move; the dollar falls to
        # 0.8 — a €100 loss for a euro investor, which the old code (cost at
        # today's rate) hid by moving the cost down with the value.
        txs = [_tx("buy", "2025-01-02", 10, 1000)]
        eur = to_eur_transactions(txs, lambda c, d: 0.9 if c == "USD" else 1.0)
        value, cost = portfolio_totals(
            txs, eur, lambda aid: 100.0, lambda aid: "USD", lambda c: 0.8
        )
        assert cost == pytest.approx(900.0)
        assert value == pytest.approx(800.0)

    def test_position_without_price_is_valued_at_cost(self):
        txs = [_tx("buy", "2025-01-02", 10, 1000, "EUR")]
        value, cost = portfolio_totals(
            txs, txs, lambda aid: None, lambda aid: "EUR", lambda c: 1.0
        )
        assert value == cost == 1000.0

    def test_benchmark_closes_converted_each_day(self):
        closes = [("2025-01-02", 100.0), ("2025-01-03", 100.0)]
        rates = {"2025-01-02": 0.9, "2025-01-03": 0.8}
        out = to_eur_closes(closes, "USD", lambda c, d: rates[d])
        assert out == [("2025-01-02", 90.0), ("2025-01-03", 80.0)]
        assert to_eur_closes(closes, "EUR", lambda c, d: 0.0) == closes


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(str(tmp_path / "perf.db"))


def _seed_usd_position(db: Database) -> int:
    pid = db.get_or_create_portfolio("Example Broker")
    aid = db.create_asset("EXMP", "Example Corp", "stock", currency="USD")
    db.create_transaction(
        aid, "buy", 10, 100, 1000, "2024-01-02", portfolio_id=pid, currency="USD"
    )
    db.create_transaction(
        aid, "sell", 5, 120, 600, "2024-06-03", portfolio_id=pid, currency="USD"
    )
    db.create_transaction(
        aid, "dividend", 0, 0, 20, "2024-07-01", portfolio_id=pid, currency="USD"
    )
    db.create_price(aid, 130.0, "2024-12-30")
    return aid


class TestComputePerformance:
    def test_total_gain_includes_income_and_uses_purchase_rates(self, db):
        _seed_usd_position(db)
        rates = {"2024-01-02": 0.9, "2024-06-03": 0.9, "2024-07-01": 0.9}
        perf = compute_performance(
            db, lambda c: 0.8 if c == "USD" else 1.0, lambda c, d: rates[d]
        )
        # Bought €900, sold half (cost €450) for €540, 5 left worth 5×130×0.8.
        assert perf["capital_invested_eur"] == 900.0
        assert perf["invested_eur"] == 450.0
        assert perf["current_value_eur"] == 520.0
        assert perf["realised_pnl_eur"] == 90.0
        assert perf["income_eur"] == 18.0
        assert perf["total_gain_eur"] == pytest.approx(520 - 450 + 90 + 18)
        assert perf["total_return_pct"] == pytest.approx(178 / 900 * 100, abs=0.01)
        assert perf["inception_date"] == "2024-01-02"
        # Purchases are money in (negative); sale and dividend money out.
        flows = [round(a, 2) for _, a in sorted(perf["cash_flows"])]
        assert flows == [-900.0, 540.0, 18.0]
        assert perf["money_weighted_irr_pct"] is not None

    def test_empty_database(self, db):
        perf = compute_performance(db, lambda c: 1.0, lambda c, d: 1.0)
        assert perf["total_return_pct"] is None
        assert perf["money_weighted_irr_pct"] is None
        assert perf["inception_date"] is None


def _daily_snaps(start: date, n: int, growth: float = 1.0005) -> list[dict]:
    return [
        {
            "snapshot_date": (start + timedelta(days=i)).isoformat(),
            "total_value_eur": 1000 * growth**i,
            "total_cost_eur": 1000,
        }
        for i in range(n)
    ]


class TestLifetimeTwr:
    def test_annualised_after_a_year(self):
        start = date(2024, 1, 1)
        total, annual = lifetime_twr(_daily_snaps(start, 400), {}, start)
        assert total == pytest.approx((1.0005**399 - 1) * 100, abs=0.01)
        assert annual == pytest.approx(
            ((1.0005**399) ** (365.25 / 399) - 1) * 100, abs=0.01
        )

    def test_not_annualised_under_a_year(self):
        start = date(2024, 1, 1)
        total, annual = lifetime_twr(_daily_snaps(start, 100), {}, start)
        assert total is not None and annual is None

    def test_none_when_history_starts_after_inception(self):
        start = date(2024, 1, 1)
        snaps = _daily_snaps(start + timedelta(days=60), 400)
        assert lifetime_twr(snaps, {}, start) == (None, None)


class TestWeeklyReturns:
    def test_daily_compound_into_iso_weeks_and_partial_weeks_drop(self):
        # 2025-01-06 is a Monday: one full week, then a 2-day stub.
        days = [(date(2025, 1, 6) + timedelta(days=i)).isoformat() for i in range(9)]
        weekly = weekly_from_daily([(d, 0.01) for d in days])
        assert weekly == {"2025-W02": pytest.approx(1.01**7 - 1)}

    def test_closes_last_to_last(self):
        closes = [
            ("2025-01-03", 100.0),
            ("2025-01-10", 110.0),
            ("2025-01-17", 99.0),
        ]
        assert weekly_from_closes(closes) == {
            "2025-W02": pytest.approx(0.10),
            "2025-W03": pytest.approx(-0.10),
        }


class TestRiskFree:
    def test_parses_ecb_csv(self, monkeypatch):
        csv_text = "KEY,TIME_PERIOD,OBS_VALUE\nX,2025-01-02,2.0\nX,2025-01-03,2.2\n"

        class _Resp:
            text = csv_text

            def raise_for_status(self):
                return None

        monkeypatch.setattr(risk_free.httpx, "get", lambda *a, **k: _Resp())
        assert _REAL_FETCH_ESTR("2025-01-02", "2025-01-03") == [2.0, 2.2]

    def test_average_of_fixings(self, monkeypatch, db):
        monkeypatch.setattr(risk_free, "_fetch_estr", lambda s, e: [2.0, 2.2])
        rate, source = risk_free.risk_free_rate(db, date(2025, 1, 2), date(2025, 1, 3))
        assert rate == pytest.approx(0.021)
        assert source == "ecb_estr"

    def test_falls_back_to_setting_then_default(self, db):
        rate, source = risk_free.risk_free_rate(db, date(2025, 1, 2), date(2025, 2, 1))
        assert (rate, source) == (0.02, "default")
        db.set_setting(risk_free.SETTING_KEY, "3.5")
        rate, source = risk_free.risk_free_rate(db, date(2025, 1, 2), date(2025, 2, 2))
        assert rate == pytest.approx(0.035)
        assert source == "setting"


@pytest.fixture
def client_db(tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    database = Database(str(tmp_path / "perf_api.db"))
    km = APIKeyManager(database)
    km.create_api_key(key_name="test", description="test key", raw_key=_KEY)
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_api_key_manager] = lambda: km
    yield TestClient(app), database
    app.dependency_overrides.clear()


def _frame(days: list[date], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Close": closes}, index=pd.to_datetime(days))


class TestEndpoints:
    def test_snapshot_cost_uses_transaction_date_fx(self, client_db, monkeypatch):
        from portf_server.routers import analytics

        client, database = client_db
        _seed_usd_position(database)
        monkeypatch.setattr(analytics, "_fx", lambda c: 0.8 if c == "USD" else 1.0)
        monkeypatch.setattr(analytics, "_fx_on", lambda db, c, d: 0.9)
        r = client.post("/api/v1/analytics/snapshot", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["total_cost_eur"] == 450.0
        assert r.json()["total_value_eur"] == 520.0

    def test_usd_benchmark_is_measured_in_eur(self, client_db, monkeypatch):
        from portf_server.routers import analytics

        client, database = client_db
        start = date.today() - timedelta(days=30)
        aid = database.create_asset("EXEU", "Example Fund", "etf", currency="EUR")
        database.create_transaction(
            aid, "buy", 10, 100, 1000, start.isoformat(), currency="EUR"
        )
        for snap in _daily_snaps(start, 31, 1.0):
            database.record_snapshot(
                snap["snapshot_date"], snap["total_value_eur"], snap["total_cost_eur"]
            )
        # Index +10% in USD while the dollar falls 10%: ~-1% in EUR.
        days = [start, date.today()]
        rates = {start.isoformat(): 1.0, date.today().isoformat(): 0.9}
        monkeypatch.setattr(
            analytics,
            "_fx_on",
            lambda db, c, d: rates.get(str(d)[:10], 1.0) if c == "USD" else 1.0,
        )
        with patch.object(
            analytics.yf, "download", return_value=_frame(days, [100.0, 110.0])
        ):
            r = client.get(
                "/api/v1/analytics/performance?benchmark=%5EGSPC&period=all",
                headers=HEADERS,
            )
        d = r.json()
        assert d["benchmark_return_pct"] == pytest.approx(-1.0, abs=0.01)
        assert d["benchmark_total_return"] is False
        assert d["period_return_pct"] == 0.0
        assert d["twr_since_inception_pct"] == 0.0

    def test_risk_reports_rf_and_weekly_beta(self, client_db):
        client, database = client_db
        start = date.today() - timedelta(days=399)
        value = 1000.0
        rets = []
        for i in range(400):
            if i:
                r = 0.006 if i % 3 else -0.009
                rets.append(r)
                value *= 1 + r
            day = (start + timedelta(days=i)).isoformat()
            database.record_snapshot(day, value, 1000.0)
        days = [start + timedelta(days=i) for i in range(400)]
        closes = [100.0 * (1.0004**i) * (1.01 if i % 3 else 0.99) for i in range(400)]
        from portf_server.routers import analytics

        with patch.object(analytics.yf, "download", return_value=_frame(days, closes)):
            r = client.get("/api/v1/analytics/risk", headers=HEADERS)
        d = r.json()
        assert d["risk_free_source"] == "default"
        assert d["risk_free_rate_pct"] == 2.0
        assert d["beta_observations"] >= 50
        assert d["beta"] is not None
        assert d["benchmark"] == "VWCE.DE"
        # Sharpe = (mean × periods/yr − rf) / volatility, rf = 2%.
        ppy = d["periods_per_year"]
        mean = sum(rets) / len(rets)
        expected = (mean * ppy - 0.02) / (d["volatility_pct"] / 100)
        assert d["sharpe_ratio"] == pytest.approx(expected, abs=0.02)


class TestBackfill:
    def test_uses_stored_prices_and_historical_fx(self, db, monkeypatch):
        from portf_server.routers import analytics

        today = date.today()
        d0 = today - timedelta(days=2)
        aid = db.create_asset("EXMP", "Example Corp", "stock", currency="USD")
        db.create_transaction(aid, "buy", 10, 100, 1000, d0.isoformat(), currency="USD")
        db.create_price(aid, 110.0, (d0 + timedelta(days=1)).isoformat())

        class _NoYahoo:
            def __init__(self, *_a, **_k):
                raise RuntimeError("no network in tests")

        monkeypatch.setattr(analytics.yf, "Ticker", _NoYahoo)
        rates = {d0.isoformat(): 0.9}
        monkeypatch.setattr(
            analytics, "_fx_on", lambda _db, c, d: rates.get(str(d)[:10], 0.8)
        )
        analytics._run_backfill(db, force=True)
        snaps = {s["snapshot_date"]: s for s in db.get_snapshots()}
        # Day 0: no price yet → at cost (€900). Day 1: 10 × 110 × 0.8.
        assert snaps[d0.isoformat()]["total_value_eur"] == 900.0
        day1 = snaps[(d0 + timedelta(days=1)).isoformat()]
        assert day1["total_value_eur"] == 880.0
        assert day1["total_cost_eur"] == 900.0
