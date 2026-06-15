"""Unit tests for the /analytics/dq/* data-quality endpoints."""

import pytest
from httpx import AsyncClient


# ── helpers ──────────────────────────────────────────────────────────────────


def _setup_portfolio_with_data(db):
    """Return (portfolio_id, asset_id) after inserting one portfolio,
    one asset, one deposit booking, and one buy transaction."""
    pid = db.get_or_create_portfolio("TestBroker", base_currency="EUR")
    aid = db.create_asset("VWCE", "Vanguard FTSE All-World ETF", "etf", currency="EUR")
    # Deposit 10 000 EUR
    db.create_booking("2025-01-01", "Deposit", 10000.0, "EUR", portfolio_id=pid)
    # Buy 100 units @ 80 EUR = 8 000 EUR total
    db.create_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=100.0,
        price=80.0,
        total_amount=8000.0,
        transaction_date="2025-01-05",
        portfolio_id=pid,
        currency="EUR",
    )
    return pid, aid


# ── reconciliation ────────────────────────────────────────────────────────────


class TestDQReconciliation:
    @pytest.mark.asyncio
    async def test_reconciliation_returns_portfolios(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        _setup_portfolio_with_data(test_database)
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/reconciliation", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "portfolios" in data
        assert len(data["portfolios"]) >= 1

    @pytest.mark.asyncio
    async def test_reconciliation_implied_cash_formula(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid, _ = _setup_portfolio_with_data(test_database)
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/reconciliation", headers=auth_headers
        )
        data = resp.json()
        p = next(x for x in data["portfolios"] if x["portfolio_id"] == pid)
        # deposit 10000 - buy 8000 = implied_cash 2000
        assert p["net_bookings"] == pytest.approx(10000.0)
        assert p["buy_costs"] == pytest.approx(8000.0)
        assert p["implied_cash"] == pytest.approx(2000.0)

    @pytest.mark.asyncio
    async def test_reconciliation_includes_sell_proceeds(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid, aid = _setup_portfolio_with_data(test_database)
        # Sell 50 units @ 90 = 4500 EUR
        test_database.create_transaction(
            asset_id=aid,
            transaction_type="sell",
            quantity=50.0,
            price=90.0,
            total_amount=4500.0,
            transaction_date="2025-06-01",
            portfolio_id=pid,
            currency="EUR",
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/reconciliation", headers=auth_headers
        )
        data = resp.json()
        p = next(x for x in data["portfolios"] if x["portfolio_id"] == pid)
        # 10000 - 8000 + 4500 = 6500
        assert p["implied_cash"] == pytest.approx(6500.0)

    @pytest.mark.asyncio
    async def test_reconciliation_includes_dividends_and_interest(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid, aid = _setup_portfolio_with_data(test_database)
        test_database.create_transaction(
            asset_id=aid,
            transaction_type="dividend",
            quantity=0.0,
            price=0.0,
            total_amount=150.0,
            transaction_date="2025-03-15",
            portfolio_id=pid,
            currency="EUR",
        )
        test_database.create_transaction(
            asset_id=aid,
            transaction_type="interest",
            quantity=0.0,
            price=0.0,
            total_amount=50.0,
            transaction_date="2025-04-01",
            portfolio_id=pid,
            currency="EUR",
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/reconciliation", headers=auth_headers
        )
        data = resp.json()
        p = next(x for x in data["portfolios"] if x["portfolio_id"] == pid)
        # 10000 - 8000 + 150 + 50 = 2200
        assert p["implied_cash"] == pytest.approx(2200.0)
        assert p["dividend_income"] == pytest.approx(150.0)
        assert p["interest_income"] == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_reconciliation_empty_portfolio(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        test_database.get_or_create_portfolio("Empty", base_currency="EUR")
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/reconciliation", headers=auth_headers
        )
        assert resp.status_code == 200


# ── duplicates ────────────────────────────────────────────────────────────────


class TestDQDuplicates:
    @pytest.mark.asyncio
    async def test_no_duplicates_when_empty(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/duplicates", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["duplicates"] == []

    @pytest.mark.asyncio
    async def test_detects_exact_same_day_duplicate(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("Broker", base_currency="EUR")
        aid = test_database.create_asset("SPY", "S&P 500 ETF", "etf", currency="USD")
        for _ in range(2):
            test_database.create_transaction(
                asset_id=aid,
                transaction_type="buy",
                quantity=10.0,
                price=500.0,
                total_amount=5000.0,
                transaction_date="2025-03-15",
                portfolio_id=pid,
                currency="USD",
            )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/duplicates", headers=auth_headers
        )
        dups = resp.json()["duplicates"]
        assert len(dups) == 1
        assert dups[0]["label"] == "likely"
        assert dups[0]["key"].startswith("dup:")

    @pytest.mark.asyncio
    async def test_detects_within_3_day_window(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("Broker2", base_currency="EUR")
        aid = test_database.create_asset("QQQ", "Nasdaq ETF", "etf", currency="USD")
        test_database.create_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=5.0,
            price=400.0,
            total_amount=2000.0,
            transaction_date="2025-04-01",
            portfolio_id=pid,
            currency="USD",
        )
        test_database.create_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=5.0,
            price=402.0,
            total_amount=2010.0,
            transaction_date="2025-04-03",
            portfolio_id=pid,
            currency="USD",
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/duplicates", headers=auth_headers
        )
        dups = resp.json()["duplicates"]
        assert len(dups) == 1
        assert dups[0]["label"] == "possible"

    @pytest.mark.asyncio
    async def test_no_duplicate_outside_3_day_window(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("Broker3", base_currency="EUR")
        aid = test_database.create_asset("GLD", "Gold ETF", "etf", currency="USD")
        test_database.create_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=10.0,
            price=180.0,
            total_amount=1800.0,
            transaction_date="2025-05-01",
            portfolio_id=pid,
            currency="USD",
        )
        test_database.create_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=10.0,
            price=180.0,
            total_amount=1800.0,
            transaction_date="2025-05-10",
            portfolio_id=pid,
            currency="USD",
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/duplicates", headers=auth_headers
        )
        assert resp.json()["duplicates"] == []

    @pytest.mark.asyncio
    async def test_no_duplicate_different_quantity(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("Broker4", base_currency="EUR")
        aid = test_database.create_asset("MSFT", "Microsoft", "stock", currency="USD")
        test_database.create_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=5.0,
            price=300.0,
            total_amount=1500.0,
            transaction_date="2025-06-01",
            portfolio_id=pid,
            currency="USD",
        )
        test_database.create_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=10.0,
            price=300.0,
            total_amount=3000.0,
            transaction_date="2025-06-01",
            portfolio_id=pid,
            currency="USD",
        )
        # quantities differ by >5% (5 vs 10 = 100% diff)
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/duplicates", headers=auth_headers
        )
        assert resp.json()["duplicates"] == []
