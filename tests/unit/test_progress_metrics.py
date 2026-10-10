"""Progress metrics: contributions vs growth, calendar returns, inflation,
latent tax, savings rate and emergency fund."""

from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from portf_manager.database import Database
from portf_manager.services import inflation
from portf_manager.services.analytics_service import irpf_savings_tax
from portf_manager.services.progress import (
    calendar_returns,
    contributions_vs_growth,
    is_fund_like,
    latent_tax,
    savings_and_buffer,
    savings_tax,
    window_return,
)

_KEY = "test-key-progress-abc123"
HEADERS = {"X-API-Key": _KEY}
# Captured at import, before the autouse conftest stub replaces it per test.
_REAL_FETCH_HICP = inflation._fetch_hicp


def _tx(t: str, day: str, total: float) -> dict:
    return {"transaction_type": t, "transaction_date": day, "total_amount": total}


class TestContributionsVsGrowth:
    def test_net_contributions_and_growth(self):
        txs = [
            _tx("buy", "2025-01-10", 1000),
            _tx("buy", "2025-02-10", 500),
            _tx("sell", "2025-03-10", 300),
            _tx("dividend", "2025-03-15", 20),
        ]
        snaps = [
            {"snapshot_date": "2025-01-31", "total_value_eur": 1010},
            {"snapshot_date": "2025-02-28", "total_value_eur": 1550},
        ]
        out = contributions_vs_growth(txs, snaps, 1400.0, today=date(2025, 3, 20))
        assert out["net_contributions_eur"] == 1200.0
        assert out["growth_eur"] == 200.0
        assert out["months"] == [
            {"month": "2025-01", "net_contributions_eur": 1000.0, "value_eur": 1010.0},
            {"month": "2025-02", "net_contributions_eur": 1500.0, "value_eur": 1550.0},
            # The month in progress shows today's value.
            {"month": "2025-03", "net_contributions_eur": 1200.0, "value_eur": 1400.0},
        ]

    def test_no_trades(self):
        out = contributions_vs_growth([], [], 0.0)
        assert out == {"net_contributions_eur": 0.0, "growth_eur": 0.0, "months": []}


def _snaps(start: date, values: list[float]) -> list[dict]:
    return [
        {
            "snapshot_date": (start + timedelta(days=i)).isoformat(),
            "total_value_eur": v,
            "total_cost_eur": 1000,
        }
        for i, v in enumerate(values)
    ]


class TestCalendarReturns:
    def test_years_split_at_year_end(self):
        # 2024-12-30 → 2025-01-02: +1% in 2024 (one day), +2% in 2025.
        snaps = _snaps(date(2024, 12, 30), [100, 101, 101, 103.02])
        out = calendar_returns(snaps, {}, today=date(2025, 6, 1))
        y24, y25 = out["years"]
        assert (y24["year"], y24["return_pct"]) == (2024, 1.0)
        assert y24["start"] == "2024-12-30" and y24["partial"] is True
        assert y25["start"] == "2024-12-31" and y25["end"] == "2025-01-02"
        assert y25["return_pct"] == pytest.approx(2.0, abs=0.01)
        # The running year is partial even if it started on 1 January.
        assert y25["partial"] is True
        assert out["months"]["2025"]["01"] == pytest.approx(2.0, abs=0.01)

    def test_full_year_is_not_partial(self):
        snaps = _snaps(date(2023, 12, 31), [100] * 367)
        years = calendar_returns(snaps, {}, today=date(2025, 6, 1))["years"]
        assert years[0]["year"] == 2024 and years[0]["partial"] is False

    def test_benchmark_window_return(self):
        closes = [("2024-12-31", 100.0), ("2025-06-30", 110.0)]
        assert window_return(closes, "2024-12-31", "2025-07-01") == 10.0
        assert window_return(closes, "2024-01-01", "2025-07-01") is None


class TestInflation:
    def test_parses_ecb_csv(self, monkeypatch):
        text = "KEY,TIME_PERIOD,OBS_VALUE\nX,2024-12,100.0\nX,2025-01,100.5\n"

        class _Resp:
            def __init__(self):
                self.text = text

            def raise_for_status(self):
                return None

        monkeypatch.setattr(inflation.httpx, "get", lambda *a, **k: _Resp())
        assert _REAL_FETCH_HICP("2024-12") == {"2024-12": 100.0, "2025-01": 100.5}

    def test_inflation_uses_latest_published_month(self):
        index = {"2024-12": 100.0, "2025-08": 102.0}
        assert inflation.inflation_between(index, "2024-12", "2025-10") == (
            pytest.approx(0.02)
        )
        assert inflation.latest_month(index, "2025-10") == "2025-08"
        assert inflation.inflation_between(index, "2023-12", "2025-10") is None

    def test_fisher_relation(self):
        # 10% nominal with 4% inflation is 5.77% real, not 6%.
        assert inflation.real_return(0.10, 0.04) == pytest.approx(0.0577, abs=1e-4)


class TestLatentTax:
    def test_tax_on_top_of_this_years_base(self):
        positions = [
            {"value_eur": 15000, "cost_eur": 10000, "fund": True},
            {"value_eur": 3000, "cost_eur": 4000, "fund": False},
        ]
        out = latent_tax(positions, realised_ytd=2000, income_ytd=500)
        assert out["unrealised_gain_eur"] == 4000
        assert out["fund_unrealised_gain_eur"] == 5000
        expected = irpf_savings_tax(6500) - irpf_savings_tax(2500)
        assert out["latent_tax_eur"] == pytest.approx(expected, abs=0.01)
        assert out["after_tax_value_eur"] == pytest.approx(18000 - expected, abs=0.01)

    def test_losses_offset_a_quarter_of_income(self):
        assert savings_tax(-1000, 2000) == irpf_savings_tax(1500)
        assert savings_tax(-100, 2000) == irpf_savings_tax(1900)

    def test_fund_detection(self):
        assert is_fund_like({"asset_type": "mutual_fund"})
        assert is_fund_like({"asset_type": "stock", "name": "Global Index Fund"})
        assert is_fund_like({"asset_type": "stock", "exchange": "Funds"})
        assert not is_fund_like({"asset_type": "etf", "name": "World Index ETF"})
        assert not is_fund_like({"asset_type": "stock", "name": "Example Corp"})


class TestSavingsAndBuffer:
    def test_complete_months_with_imports_only(self):
        rows = [
            {"date": "2025-04-03", "amount_eur": 3000},
            {"date": "2025-04-20", "amount_eur": -2000},
            {"date": "2025-05-03", "amount_eur": 3000},
            {"date": "2025-05-20", "amount_eur": -2600},
            # Month in progress: ignored.
            {"date": "2025-06-02", "amount_eur": -5000},
        ]
        out = savings_and_buffer(rows, 13800.0, today=date(2025, 6, 10))
        assert out["months_used"] == ["2025-04", "2025-05"]
        assert out["savings_rate_pct"] == pytest.approx(23.3, abs=0.05)
        assert out["avg_monthly_spend_eur"] == 2300.0
        assert out["emergency_months"] == 6.0

    def test_nothing_imported(self):
        out = savings_and_buffer([], None, today=date(2025, 6, 10))
        assert out["savings_rate_pct"] is None
        assert out["emergency_months"] is None
        assert out["months_used"] == []


@pytest.fixture
def client_db(tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    db = Database(str(tmp_path / "progress.db"))
    km = APIKeyManager(db)
    km.create_api_key(key_name="test", description="test key", raw_key=_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km
    yield TestClient(app), db
    app.dependency_overrides.clear()


class TestProgressEndpoint:
    def test_end_to_end_with_inflation(self, client_db, monkeypatch):
        client, db = client_db
        today = date.today()
        start = today - timedelta(days=400)
        pid = db.get_or_create_portfolio("Example Broker")
        aid = db.create_asset("EXEU", "Example Fund", "etf", currency="EUR")
        db.create_transaction(
            aid,
            "buy",
            10,
            100,
            1000,
            start.isoformat(),
            portfolio_id=pid,
            currency="EUR",
        )
        db.create_price(aid, 120.0, today.isoformat())
        for i in range(401):
            day = (start + timedelta(days=i)).isoformat()
            db.record_snapshot(day, 1000 + 200 * i / 400, 1000)

        # Flat 2%/yr prices: index rises 2% every 12 months.
        index = {}
        y, m = start.year - 1, 12
        for k in range(30):
            index[f"{y:04d}-{m:02d}"] = 100 * 1.02 ** (k / 12)
            m += 1
            if m > 12:
                y, m = y + 1, 1
        monkeypatch.setattr(inflation, "_fetch_hicp", lambda start_month: index)
        bench = pd.DataFrame(
            {"Close": [100.0, 110.0]},
            index=pd.to_datetime([start - timedelta(days=1), today]),
        )
        from portf_server.routers import analytics

        with patch.object(analytics.yf, "download", return_value=bench):
            r = client.get("/api/v1/analytics/progress", headers=HEADERS)
        assert r.status_code == 200
        d = r.json()
        assert d["contributions"]["net_contributions_eur"] == 1000.0
        assert d["contributions"]["growth_eur"] == 200.0
        assert [y["year"] for y in d["calendar_years"]] == sorted(
            {start.year, today.year}
        )
        assert all(y["inflation_pct"] is not None for y in d["calendar_years"])
        # A partial first year is deflated over its own months, not 12.
        first = d["calendar_years"][0]
        months_in_first = 12 - int(first["start"][5:7]) + 1
        assert first["inflation_pct"] == pytest.approx(
            (1.02 ** (months_in_first / 12) - 1) * 100, abs=0.05
        )
        assert d["real"]["inflation_annual_pct"] == pytest.approx(2.0, abs=0.3)
        assert d["real"]["real_irr_pct"] < d["real"]["irr_pct"]
        assert d["latent_tax"]["unrealised_gain_eur"] == 200.0
        assert d["latent_tax"]["latent_tax_eur"] == pytest.approx(38.0, abs=0.01)
        # No bank accounts: savings block present but empty, not zero.
        assert d["savings"]["savings_rate_pct"] is None
        assert d["savings"]["cash_eur"] is None
        assert d["notes"] == []

    def test_inflation_unavailable_is_said(self, client_db):
        client, db = client_db
        aid = db.create_asset("EXEU", "Example Fund", "etf", currency="EUR")
        db.create_transaction(aid, "buy", 1, 100, 100, "2025-01-02", currency="EUR")
        r = client.get("/api/v1/analytics/progress", headers=HEADERS)
        d = r.json()
        assert d["real"]["real_irr_pct"] is None
        assert any("Inflation" in n for n in d["notes"])
