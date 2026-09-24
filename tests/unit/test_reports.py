"""Tests for PDF report generation — tax report + general portfolio report."""

import pytest
from httpx import AsyncClient
from fastapi import status

from portf_manager.services.pdf_reports import (
    build_portfolio_report_pdf,
    build_tax_report_pdf,
)


class TestTaxReportPdfBuilder:
    def test_builds_pdf_with_lots(self):
        data = {
            "year": 2026,
            "realised_lots": [
                {
                    "symbol": "EXAM",
                    "name": "Example Corp",
                    "purchase_date": "2025-01-15",
                    "sell_date": "2026-03-01",
                    "quantity": 10.0,
                    "proceeds_eur": 1200.0,
                    "cost_basis_eur": 1000.0,
                    "gain_loss_eur": 200.0,
                }
            ],
            "realised_gain_total": 200.0,
            "lot_count": 1,
            "dividends_gross_eur": 50.0,
            "dividend_withholding_eur": 7.5,
            "interest_withholding_eur": 0.0,
        }
        pdf_bytes = build_tax_report_pdf(
            data, {"savings_base_eur": 250.0, "estimated_tax_eur": 47.5}
        )
        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 500

    def test_builds_pdf_with_no_lots(self):
        data = {
            "year": 2026,
            "realised_lots": [],
            "realised_gain_total": 0.0,
            "lot_count": 0,
            "dividends_gross_eur": 0.0,
            "dividend_withholding_eur": 0.0,
            "interest_withholding_eur": 0.0,
        }
        pdf_bytes = build_tax_report_pdf(data, None)
        assert pdf_bytes.startswith(b"%PDF")


class TestPortfolioReportPdfBuilder:
    def test_builds_pdf_with_all_sections(self):
        bundle = {
            "networth": {
                "brokerage_eur": 1000.0,
                "bank_accounts_eur": 500.0,
                "manual_assets_eur": 0.0,
                "manual_liabilities_eur": 0.0,
                "deposits_eur": 0.0,
                "net_worth_eur": 1500.0,
            },
            "holdings": [
                {
                    "symbol": "EXAM",
                    "name": "Example Corp",
                    "asset_type": "stock",
                    "quantity": 5.0,
                    "total_value_eur": 1000.0,
                    "pnl_pct": 12.5,
                }
            ],
            "performance": {
                "invested_eur": 900.0,
                "current_value_eur": 1000.0,
                "realised_pnl_eur": 0.0,
                "total_return_pct": 11.1,
                "money_weighted_irr_pct": 10.0,
                "cagr_pct": 9.0,
                "benchmark": "^GSPC",
                "benchmark_return_pct": 8.0,
            },
            "risk": {
                "max_drawdown_pct": -5.0,
                "volatility_pct": 12.0,
                "sharpe_ratio": 1.1,
                "sortino_ratio": 1.4,
                "calmar_ratio": 2.0,
                "beta": 0.9,
                "alpha_pct": 1.2,
            },
            "diversification": {
                "coverage": {"classified_pct": 80.0, "unprofiled": []},
                "by_asset_class": {"Equity": 80.0, "Bond": 20.0},
                "by_region_equity": {"North America": 60.0, "Europe": 40.0},
                "by_currency_exposure": {"EUR": 50.0, "USD": 50.0},
                "by_sector": {"Technology": 30.0},
            },
            "health": {
                "scores": {
                    "diversification": {"score": 7, "reason": "Reasonably spread."}
                },
                "summary": "Solid overall.",
                "recommendations": [
                    {
                        "category": "Fees",
                        "action": "Consolidate brokers",
                        "rationale": "Lower drag.",
                    }
                ],
            },
        }
        pdf_bytes = build_portfolio_report_pdf(
            ["networth", "performance", "diversification", "health"], bundle
        )
        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 500

    def test_builds_pdf_with_missing_health(self):
        bundle = {
            "networth": {
                "brokerage_eur": 0.0,
                "bank_accounts_eur": 0.0,
                "manual_assets_eur": 0.0,
                "manual_liabilities_eur": 0.0,
                "deposits_eur": 0.0,
                "net_worth_eur": 0.0,
            },
            "holdings": [],
        }
        pdf_bytes = build_portfolio_report_pdf(["networth"], bundle)
        assert pdf_bytes.startswith(b"%PDF")


class TestReportsRouter:
    @pytest.mark.asyncio
    async def test_portfolio_report_default_sections(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/reports/portfolio", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")
        assert "portfolio_report.pdf" in resp.headers["content-disposition"]

    @pytest.mark.asyncio
    async def test_portfolio_report_single_section(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/reports/portfolio?sections=networth", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.content.startswith(b"%PDF")

    @pytest.mark.asyncio
    async def test_portfolio_report_rejects_unknown_section(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/reports/portfolio?sections=bogus", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
