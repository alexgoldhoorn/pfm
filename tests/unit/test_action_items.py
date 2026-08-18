"""Unit tests for the cross-cutting Action Items aggregator."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from portf_manager.services.action_items import (
    check_data_quality,
    check_goals_off_track,
    check_price_alerts,
    check_price_update_failures,
    check_stale_imports,
    check_stale_research,
    get_action_items,
)


def _portfolio_with_transaction(db, name="TestBroker", days_ago=10):
    """One portfolio with a single buy transaction dated `days_ago` days back."""
    pid = db.get_or_create_portfolio(name, base_currency="EUR")
    aid = db.create_asset(f"{name}SYM", f"{name} Asset", "stock", currency="EUR")
    tx_date = (date.today() - timedelta(days=days_ago)).isoformat()
    db.create_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=10.0,
        price=100.0,
        total_amount=1000.0,
        transaction_date=tx_date,
        portfolio_id=pid,
        currency="EUR",
    )
    return pid, aid


class TestStaleImports:
    def test_flags_portfolio_with_no_recent_activity(self, test_database):
        pid, _ = _portfolio_with_transaction(test_database, days_ago=90)
        items = check_stale_imports(test_database)
        assert any(i["context"]["portfolio_id"] == pid for i in items)

    def test_does_not_flag_recent_activity(self, test_database):
        pid, _ = _portfolio_with_transaction(test_database, days_ago=5)
        items = check_stale_imports(test_database)
        assert not any(i["context"]["portfolio_id"] == pid for i in items)

    def test_ignores_portfolio_with_no_transactions_ever(self, test_database):
        pid = test_database.get_or_create_portfolio("Empty", base_currency="EUR")
        items = check_stale_imports(test_database)
        assert not any(i["context"]["portfolio_id"] == pid for i in items)

    def test_context_has_explicit_upload_from_date(self, test_database):
        pid, _ = _portfolio_with_transaction(test_database, days_ago=90)
        item = next(
            i
            for i in check_stale_imports(test_database)
            if i["context"]["portfolio_id"] == pid
        )
        expected_since = (date.today() - timedelta(days=89)).isoformat()
        assert item["context"]["since_date"] == expected_since
        assert expected_since in item["detail"]
        assert item["context"]["account_type"] == "brokerage"

    def test_flags_stale_bank_account_from_spending_transactions(self, test_database):
        pid = test_database.get_or_create_portfolio(
            "TestBank", base_currency="EUR", account_type="bank"
        )
        tx_date = (date.today() - timedelta(days=90)).isoformat()
        test_database.create_spending_transaction(
            portfolio_id=pid, date=tx_date, description="Groceries", amount=-10.0
        )
        items = check_stale_imports(test_database)
        item = next(i for i in items if i["context"]["portfolio_id"] == pid)
        assert item["context"]["account_type"] == "bank"
        assert (
            item["context"]["since_date"]
            == (date.today() - timedelta(days=89)).isoformat()
        )

    def test_does_not_flag_recent_bank_activity(self, test_database):
        pid = test_database.get_or_create_portfolio(
            "RecentBank", base_currency="EUR", account_type="bank"
        )
        tx_date = (date.today() - timedelta(days=5)).isoformat()
        test_database.create_spending_transaction(
            portfolio_id=pid, date=tx_date, description="Coffee", amount=-3.0
        )
        items = check_stale_imports(test_database)
        assert not any(i["context"]["portfolio_id"] == pid for i in items)


class TestDataQuality:
    def test_flags_suspicious_zero_price(self, test_database):
        pid = test_database.get_or_create_portfolio("Broker", base_currency="EUR")
        aid = test_database.create_asset("ZP", "Zero Price Co", "stock", currency="EUR")
        test_database.create_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=10.0,
            price=0.0,
            total_amount=0.0,
            transaction_date=date.today().isoformat(),
            portfolio_id=pid,
            currency="EUR",
        )
        items = check_data_quality(test_database)
        assert any(i["id"] == "dq:suspicious" for i in items)

    def test_empty_when_clean(self, test_database):
        _portfolio_with_transaction(test_database, days_ago=5)
        assert check_data_quality(test_database) == []


class TestPriceUpdateFailures:
    def test_flags_latest_run_with_errors(self, test_database):
        test_database.record_price_update_run(
            started_at="2026-07-15T20:00:00",
            duration_seconds=12.0,
            updated_count=5,
            skipped_count=0,
            error_count=2,
            error_symbols=["AAPL", "MSFT"],
            source="cron",
        )
        items = check_price_update_failures(test_database)
        assert len(items) == 1
        assert items[0]["context"]["symbols"] == ["AAPL", "MSFT"]

    def test_detail_shows_asset_name_alongside_symbol(self, test_database):
        """Users don't recognise raw tickers/ISINs — the name must be shown too."""
        test_database.create_asset("AAPL", "Apple Inc.", "stock", currency="USD")
        test_database.record_price_update_run(
            started_at="2026-07-15T20:00:00",
            duration_seconds=12.0,
            updated_count=5,
            skipped_count=0,
            error_count=1,
            error_symbols=["AAPL"],
            source="cron",
        )
        items = check_price_update_failures(test_database)
        assert "Apple Inc. (AAPL)" in items[0]["detail"]

    def test_no_item_when_latest_run_clean(self, test_database):
        test_database.record_price_update_run(
            started_at="2026-07-15T20:00:00",
            duration_seconds=12.0,
            updated_count=5,
            skipped_count=0,
            error_count=0,
            source="cron",
        )
        assert check_price_update_failures(test_database) == []

    def test_no_item_when_no_runs(self, test_database):
        assert check_price_update_failures(test_database) == []


class TestStaleResearch:
    def test_flags_held_asset_with_no_research(self, test_database):
        _pid, aid = _portfolio_with_transaction(test_database, days_ago=5)
        items = check_stale_research(test_database)
        assert len(items) == 1
        assert "TestBroker Asset (TestBrokerSYM)" in items[0]["detail"]

    def test_flags_held_asset_with_old_research(self, test_database):
        _pid, aid = _portfolio_with_transaction(test_database, days_ago=5)
        note_id = test_database.create_research_note(
            asset_id=aid, symbol="TestBrokerSYM", thesis="x", conviction=3
        )
        old_date = (date.today() - timedelta(days=120)).isoformat()
        with test_database.get_connection() as conn:
            conn.execute(
                "UPDATE research_notes SET created_at = ? WHERE id = ?",
                (old_date, note_id),
            )
            conn.commit()
        items = check_stale_research(test_database)
        assert len(items) == 1

    def test_no_item_when_research_is_fresh(self, test_database):
        _pid, aid = _portfolio_with_transaction(test_database, days_ago=5)
        test_database.create_research_note(
            asset_id=aid, symbol="TestBrokerSYM", thesis="x", conviction=3
        )
        assert check_stale_research(test_database) == []

    def test_no_item_when_nothing_held(self, test_database):
        assert check_stale_research(test_database) == []


class TestGoalsOffTrack:
    def test_flags_off_track_goal(self, test_database):
        test_database.create_goal(
            name="Retire early",
            target_amount_eur=10_000_000,
            target_date="2027-01-01",
            monthly_contribution_eur=100,
            expected_return_pct=1,
        )
        items = check_goals_off_track(test_database)
        assert len(items) == 1
        assert items[0]["context"]["goal_id"]

    def test_no_item_when_no_goals(self, test_database):
        assert check_goals_off_track(test_database) == []


class TestPriceAlerts:
    def test_no_items_when_nothing_configured(self, test_database):
        assert check_price_alerts(test_database) == []


class TestGetActionItems:
    def test_aggregates_and_sorts_by_severity(self, test_database):
        test_database.record_price_update_run(
            started_at="2026-07-15T20:00:00",
            duration_seconds=1.0,
            updated_count=0,
            skipped_count=0,
            error_count=1,
            error_symbols=["AAPL"],
            source="cron",
        )
        _portfolio_with_transaction(test_database, days_ago=90)
        items = get_action_items(test_database)
        order = {"high": 0, "medium": 1, "low": 2}
        severities = [i["severity"] for i in items]
        assert severities == sorted(severities, key=lambda s: order[s])

    def test_empty_db_returns_empty_list(self, test_database):
        assert get_action_items(test_database) == []

    def test_one_failing_check_does_not_take_down_others(self, test_database):
        test_database.record_price_update_run(
            started_at="2026-07-15T20:00:00",
            duration_seconds=1.0,
            updated_count=0,
            skipped_count=0,
            error_count=1,
            error_symbols=["AAPL"],
            source="cron",
        )

        def _raise(db):
            raise RuntimeError("boom")

        with patch(
            "portf_manager.services.action_items.check_data_quality",
            new=_raise,
        ):
            items = get_action_items(test_database)
        assert items
        assert any(i["id"].startswith("errors:price-update:") for i in items)


class TestActionItemsEndpoint:
    @pytest.mark.asyncio
    async def test_endpoint_returns_items_and_timestamp(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        _portfolio_with_transaction(test_database, days_ago=90)
        resp = await async_test_client.get(
            "/api/v1/action-items/", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data and "generated_at" in data
