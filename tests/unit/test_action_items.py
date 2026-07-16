"""Unit tests for the cross-cutting Action Items aggregator."""

from datetime import date, timedelta

from portf_manager.services.action_items import (
    check_data_quality,
    check_price_update_failures,
    check_stale_imports,
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
