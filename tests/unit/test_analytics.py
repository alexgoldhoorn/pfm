"""Tests for the analytics router and service."""

import pytest
from datetime import date
from httpx import AsyncClient
from fastapi import status

from portf_manager.services.analytics_service import (
    irpf_savings_tax,
    dividend_income,
    money_weighted_irr,
    simple_return,
)


class TestAnalyticsService:
    def test_irpf_brackets(self):
        assert irpf_savings_tax(0) == 0
        assert irpf_savings_tax(6000) == round(6000 * 0.19, 2)
        # 10k = 6000*.19 + 4000*.21
        assert irpf_savings_tax(10000) == round(6000 * 0.19 + 4000 * 0.21, 2)
        assert irpf_savings_tax(-500) == 0

    def test_dividend_income_aggregation(self):
        txns = [
            {
                "transaction_type": "dividend",
                "transaction_date": "2025-03-01",
                "total_amount": 10,
                "symbol": "AAA",
            },
            {
                "transaction_type": "dividend",
                "transaction_date": "2025-06-01",
                "total_amount": 15,
                "symbol": "AAA",
            },
            {
                "transaction_type": "buy",
                "transaction_date": "2025-01-01",
                "total_amount": 1000,
                "symbol": "BBB",
            },
        ]
        result = dividend_income(txns)
        assert result["total"] == 25
        assert result["by_year"]["2025"] == 25
        assert result["by_symbol"]["AAA"] == 25

    def test_simple_return(self):
        assert simple_return(1000, 1200) == 20.0
        assert simple_return(0, 100) is None

    def test_irr_basic(self):
        # Invest 1000 one year ago, now worth 1100 → ~10% IRR
        flows = [
            (date(date.today().year - 1, date.today().month, date.today().day), -1000.0)
        ]
        irr = money_weighted_irr(flows, 1100.0)
        assert irr is not None
        assert 8 < irr < 12


class TestAnalyticsRouter:
    @pytest.mark.asyncio
    async def test_dividends_endpoint(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/dividends", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        d = resp.json()
        assert "total" in d and "by_year" in d and "yield_on_cost" in d

    @pytest.mark.asyncio
    async def test_tax_estimate_endpoint(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/tax-estimate", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        d = resp.json()
        assert "estimated_tax_eur" in d and "harvest_candidates" in d

    @pytest.mark.asyncio
    async def test_networth_history_endpoint(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/networth-history", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        assert "snapshots" in resp.json()

    @pytest.mark.asyncio
    async def test_snapshot_endpoint(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.post(
            "/api/v1/analytics/snapshot", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        assert "total_value_eur" in resp.json()


class TestPeriodReturn:
    def test_period_start_date(self):
        from datetime import date
        from portf_manager.services.analytics_service import period_start_date

        ref = date(2026, 6, 15)
        assert period_start_date("ytd", ref) == date(2026, 1, 1)
        assert period_start_date("1y", ref) == date(2025, 6, 15)
        assert period_start_date("all", ref) is None

    def test_period_return(self):
        from portf_manager.services.analytics_service import period_return

        snaps = [
            {"snapshot_date": "2026-01-01", "total_value_eur": 100000},
            {"snapshot_date": "2026-06-01", "total_value_eur": 130000},
        ]
        assert period_return(snaps, 130000, "ytd") == 30.0
        assert period_return(snaps, 130000, "all") == 30.0
        # No snapshot inside a 1-day window far in the future → None
        assert period_return([], 130000, "ytd") is None


class TestPublicView:
    @pytest.mark.asyncio
    async def test_public_disabled_by_default(self, async_test_client):
        # No PORTF_PUBLIC_VIEW env in tests → 404
        import os

        os.environ.pop("PORTF_PUBLIC_VIEW", None)
        resp = await async_test_client.get("/api/v1/public/summary")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_public_enabled_no_amounts(self, async_test_client, monkeypatch):
        monkeypatch.setenv("PORTF_PUBLIC_VIEW", "true")
        resp = await async_test_client.get("/api/v1/public/summary")
        assert resp.status_code == 200
        d = resp.json()
        assert "allocation_by_type_pct" in d
        assert "total_return_pct" in d
        # Must NOT expose any absolute monetary fields
        keys = " ".join(d.keys()).lower()
        assert "eur" not in keys and "value" not in keys and "cost" not in keys


class TestNewMetrics:
    def test_compute_cagr_basic(self):
        from datetime import date
        from portf_manager.services.analytics_service import compute_cagr

        # 1000 invested 2 years ago, now worth 1210, 0 realised → 10% CAGR
        inception = date(
            date.today().year - 2, date.today().month, min(date.today().day, 28)
        )
        result = compute_cagr(1000.0, 1210.0, 0.0, inception)
        assert result is not None
        assert abs(result - 10.0) < 0.5

    def test_compute_cagr_none_cases(self):
        from datetime import date
        from portf_manager.services.analytics_service import compute_cagr

        recent = date(date.today().year, 1, 1)  # less than 1 year
        assert compute_cagr(1000.0, 1200.0, 0.0, recent) is None
        assert compute_cagr(0.0, 1200.0, 0.0, date(2020, 1, 1)) is None
        assert compute_cagr(1000.0, 1200.0, 0.0, None) is None

    def test_sortino_ratio_basic(self):
        from portf_manager.services.analytics_service import sortino_ratio

        # Mix of positive and negative daily returns
        rets = [0.01, -0.02, 0.015, -0.005, 0.008, -0.003, 0.012, -0.007, 0.01, -0.001]
        result = sortino_ratio(rets)
        assert result is not None
        assert isinstance(result, float)

    def test_sortino_ratio_none_when_no_downside(self):
        from portf_manager.services.analytics_service import sortino_ratio

        # All positive → fewer than 2 downside observations
        assert sortino_ratio([0.01, 0.02, 0.005]) is None
        # Only 1 negative → stdev undefined
        assert sortino_ratio([0.01, -0.01, 0.02, 0.005]) is None
        assert sortino_ratio([]) is None

    def test_calmar_ratio_basic(self):
        from portf_manager.services.analytics_service import calmar_ratio

        # cagr=10%, drawdown=-15% → calmar = 10/15 ≈ 0.67
        result = calmar_ratio(10.0, -15.0)
        assert result is not None
        assert abs(result - 0.67) < 0.01

    def test_calmar_ratio_none_cases(self):
        from portf_manager.services.analytics_service import calmar_ratio

        assert calmar_ratio(None, -15.0) is None
        assert calmar_ratio(10.0, None) is None
        assert calmar_ratio(10.0, 0.0) is None  # no drawdown recorded
        assert calmar_ratio(10.0, 2.0) is None  # positive drawdown (impossible, guard)

    def test_compute_beta_alpha(self):
        from portf_manager.services.analytics_service import compute_beta_alpha

        # Portfolio returns ≈ 1.2× benchmark → beta ≈ 1.2
        bench = [
            0.01,
            -0.02,
            0.015,
            -0.005,
            0.008,
            -0.003,
            0.012,
            -0.007,
            0.01,
            -0.001,
            0.005,
            -0.008,
            0.02,
            -0.01,
            0.003,
        ]
        port = [r * 1.2 for r in bench]
        beta, alpha = compute_beta_alpha(port, bench, 0.10, 0.08)
        assert beta is not None
        assert abs(beta - 1.2) < 0.05
        # alpha = (0.10 - 1.2 * 0.08) * 100 = 0.4%
        assert alpha is not None
        assert abs(alpha - 0.4) < 0.2

    def test_compute_beta_alpha_none_cases(self):
        from portf_manager.services.analytics_service import compute_beta_alpha

        assert compute_beta_alpha([], [], None, None) == (None, None)
        short = [0.01] * 5
        assert compute_beta_alpha(short, short, None, None) == (None, None)
        # alpha None when cagrs not available
        bench = [0.01] * 15
        beta, alpha = compute_beta_alpha(bench, bench, None, None)
        assert beta is not None
        assert alpha is None

    def test_period_start_date_3y_5y(self):
        from datetime import date
        from portf_manager.services.analytics_service import period_start_date

        ref = date(2026, 6, 19)
        assert period_start_date("3y", ref) == date(2023, 6, 19)
        assert period_start_date("5y", ref) == date(2021, 6, 19)

    def test_period_start_date_5y_day_capped(self):
        from datetime import date
        from portf_manager.services.analytics_service import period_start_date

        # Jan 31 → day capped at 28 for Feb compatibility
        ref = date(2026, 1, 31)
        assert period_start_date("5y", ref) == date(2021, 1, 28)


class TestTaxRatesAndReport:
    def test_tax_rates_module(self):
        from portf_manager.services.tax_rates import progressive_tax

        assert progressive_tax(0) == 0
        assert progressive_tax(10000) == round(6000 * 0.19 + 4000 * 0.21, 2)
        # Unknown jurisdiction falls back to default (ES)
        assert progressive_tax(10000, "ZZ") == progressive_tax(10000, "ES")

    @pytest.mark.asyncio
    async def test_tax_report_shape(self, async_test_client, auth_headers):
        resp = await async_test_client.get(
            "/api/v1/analytics/tax-report?year=2026", headers=auth_headers
        )
        assert resp.status_code == 200
        d = resp.json()
        assert "realised_lots" in d
        assert "dividend_withholding_eur" in d
        assert "realised_gain_total" in d
