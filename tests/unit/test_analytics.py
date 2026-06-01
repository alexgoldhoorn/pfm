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
