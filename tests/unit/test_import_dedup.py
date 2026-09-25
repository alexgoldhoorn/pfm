"""Duplicate detection when re-importing cash bookings and dividends.

Covers the cases that let real duplicates through: the same deposit dated one
day apart by two sources (operation vs value date), a dividend stored as
1 x amount by one parser and shares x per-share by another, "Import anyway"
re-adding every duplicate booking, and the PDT paths that never checked.
"""

import pytest
from fastapi import status
from httpx import AsyncClient

from portf_manager import currency_utils
from portf_manager.database import Database


@pytest.fixture(autouse=True)
def no_gbx_lookup(monkeypatch):
    """Stub the live Yahoo GBX lookup; no test symbol is GBX."""
    monkeypatch.setattr(currency_utils, "is_gbx", lambda s: False)


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def _row(date, amount, pid, action="Deposit", currency="EUR"):
    return {
        "date": date,
        "action": action,
        "amount": amount,
        "currency": currency,
        "portfolio_id": pid,
    }


class TestMatchExistingBookings:
    def test_exact_match(self, db):
        pid = db.create_portfolio("Example Broker")
        bid = db.create_booking("2026-07-20", "Deposit", 200.0, "EUR", pid)
        matches = db.match_existing_bookings([_row("2026-07-20", 200.0, pid)])
        assert matches[0]["id"] == bid
        assert matches[0]["match"] == "exact"

    def test_one_day_shift_inside_import_span_matches(self, db):
        # The existing 21/07 deposit sits inside the period this import covers,
        # and the import has no 21/07 row of its own, so its 20/07 row is the
        # same deposit under a different date.
        pid = db.create_portfolio("Example Broker")
        bid = db.create_booking("2026-07-21", "Deposit", 1500.0, "EUR", pid)
        rows = [
            _row("2026-07-20", 1500.0, pid),
            _row("2026-07-28", 2000.0, pid),
        ]
        matches = db.match_existing_bookings(rows)
        assert matches[0]["id"] == bid
        assert matches[0]["match"] == "near"
        assert matches[1] is None

    def test_near_match_outside_import_span_is_not_a_duplicate(self, db):
        # A regular deposit from the previous statement, two days before this
        # one starts: a new deposit of the same amount, not a copy.
        pid = db.create_portfolio("Example Broker")
        db.create_booking("2026-06-10", "Deposit", 1500.0, "EUR", pid)
        rows = [
            _row("2026-06-12", 1500.0, pid),
            _row("2026-06-20", 1500.0, pid),
        ]
        assert db.match_existing_bookings(rows) == [None, None]

    def test_each_existing_booking_matches_at_most_once(self, db):
        # Two genuine same-day deposits: one already stored, both in the import.
        pid = db.create_portfolio("Example Broker")
        bid = db.create_booking("2026-04-27", "Deposit", 2000.0, "EUR", pid)
        rows = [_row("2026-04-27", 2000.0, pid), _row("2026-04-27", 2000.0, pid)]
        matches = db.match_existing_bookings(rows)
        assert matches[0]["id"] == bid
        assert matches[1] is None

    def test_exact_match_wins_over_near_match(self, db):
        pid = db.create_portfolio("Example Broker")
        near = db.create_booking("2026-07-19", "Deposit", 100.0, "EUR", pid)
        exact = db.create_booking("2026-07-20", "Deposit", 100.0, "EUR", pid)
        rows = [_row("2026-07-19", 100.0, pid), _row("2026-07-20", 100.0, pid)]
        matches = db.match_existing_bookings(rows)
        assert [m["id"] for m in matches] == [near, exact]
        assert [m["match"] for m in matches] == ["exact", "exact"]

    def test_other_portfolio_action_or_amount_never_match(self, db):
        pid = db.create_portfolio("Example Broker")
        other = db.create_portfolio("Other Broker")
        db.create_booking("2026-07-20", "Deposit", 200.0, "EUR", other)
        db.create_booking("2026-07-20", "Withdrawal", 200.0, "EUR", pid)
        db.create_booking("2026-07-20", "Deposit", 200.5, "EUR", pid)
        assert db.match_existing_bookings([_row("2026-07-20", 200.0, pid)]) == [None]

    def test_beyond_window_is_not_a_duplicate(self, db):
        pid = db.create_portfolio("Example Broker")
        db.create_booking("2026-07-01", "Deposit", 300.0, "EUR", pid)
        rows = [_row("2026-06-25", 50.0, pid), _row("2026-07-05", 300.0, pid)]
        assert db.match_existing_bookings(rows) == [None, None]


class TestDividendDuplicate:
    def test_same_cash_amount_split_differently_is_a_duplicate(self, db):
        pid = db.create_portfolio("Example Broker")
        aid = db.create_asset("EXMPL", "Example Corp", "stock")
        db.create_transaction(
            aid,
            "dividend",
            1.0,
            40.21,
            40.21,
            "2026-08-10",
            portfolio_id=pid,
        )
        dup = db.find_duplicate_transaction(
            asset_id=aid,
            transaction_type="dividend",
            quantity=146.0,
            price=40.21 / 146,
            transaction_date="2026-08-10",
            portfolio_id=pid,
        )
        assert dup is not None

    def test_different_dividend_amount_same_day_is_not(self, db):
        pid = db.create_portfolio("Example Broker")
        aid = db.create_asset("EXMPL", "Example Corp", "stock")
        db.create_transaction(
            aid, "dividend", 1.0, 40.21, 40.21, "2026-08-10", portfolio_id=pid
        )
        dup = db.find_duplicate_transaction(
            asset_id=aid,
            transaction_type="dividend",
            quantity=1.0,
            price=12.00,
            transaction_date="2026-08-10",
            portfolio_id=pid,
        )
        assert dup is None

    def test_buys_still_require_matching_quantity(self, db):
        # Buying 2 @ 50 and 1 @ 100 on one day are different trades.
        pid = db.create_portfolio("Example Broker")
        aid = db.create_asset("EXMPL", "Example Corp", "stock")
        db.create_transaction(
            aid, "buy", 2.0, 50.0, 100.0, "2026-08-10", portfolio_id=pid
        )
        dup = db.find_duplicate_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=1.0,
            price=100.0,
            transaction_date="2026-08-10",
            portfolio_id=pid,
        )
        assert dup is None


def _booking(date, amount, **extra):
    return {
        "broker": "Example Broker",
        "date": date,
        "action": "Deposit",
        "amount": amount,
        "currency": "EUR",
        **extra,
    }


class TestImportSaveBookings:
    @pytest.mark.asyncio
    async def test_import_anyway_does_not_re_add_duplicate_bookings(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        # "Import anyway" is chosen for transactions; it must not also copy
        # every duplicate booking in the same file.
        pid = test_database.get_or_create_portfolio("Example Broker")
        test_database.create_booking("2026-07-20", "Deposit", 200.0, "EUR", pid)
        payload = {
            "transactions": [],
            "bookings": [_booking("2026-07-20", 200.0), _booking("2026-07-28", 50.0)],
            "duplicate_action": "add",
        }
        response = await async_test_client.post(
            "/api/v1/import/save", json=payload, headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["saved_bookings"] == 1
        assert data["duplicates_skipped"] == 1
        amounts = sorted(b["amount"] for b in test_database.get_all_bookings(pid))
        assert amounts == [50.0, 200.0]

    @pytest.mark.asyncio
    async def test_force_on_a_booking_adds_it(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("Example Broker")
        test_database.create_booking("2026-07-20", "Deposit", 200.0, "EUR", pid)
        payload = {
            "transactions": [],
            "bookings": [_booking("2026-07-20", 200.0, force=True)],
        }
        response = await async_test_client.post(
            "/api/v1/import/save", json=payload, headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["saved_bookings"] == 1
        assert len(test_database.get_all_bookings(pid)) == 2

    @pytest.mark.asyncio
    async def test_shifted_date_is_skipped(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("Example Broker")
        test_database.create_booking("2026-07-21", "Deposit", 1500.0, "EUR", pid)
        payload = {
            "transactions": [],
            "bookings": [
                _booking("2026-07-20", 1500.0),
                _booking("2026-07-28", 2000.0),
            ],
        }
        response = await async_test_client.post(
            "/api/v1/import/save", json=payload, headers=auth_headers
        )
        data = response.json()
        assert data["saved_bookings"] == 1
        assert data["duplicates_skipped"] == 1
        dates = sorted(b["date"] for b in test_database.get_all_bookings(pid))
        assert dates == ["2026-07-21", "2026-07-28"]

    @pytest.mark.asyncio
    async def test_check_duplicates_explains_near_match(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("Example Broker")
        test_database.create_booking("2026-07-21", "Deposit", 1500.0, "EUR", pid)
        payload = {
            "transactions": [],
            "bookings": [
                _booking("2026-07-20", 1500.0),
                _booking("2026-07-28", 2000.0),
            ],
        }
        response = await async_test_client.post(
            "/api/v1/import/check-duplicates", json=payload, headers=auth_headers
        )
        bookings = response.json()["bookings"]
        assert bookings[0]["is_duplicate"] is True
        assert "2026-07-21" in bookings[0]["duplicate_reason"]
        assert bookings[1]["is_duplicate"] is False
        assert bookings[1]["duplicate_reason"] is None


class TestImportSaveTransactions:
    @pytest.mark.asyncio
    async def test_two_identical_trades_in_one_file_are_both_saved(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        # An existing asset, so saving triggers no new-asset lookups.
        test_database.create_asset("EXMPL", "Example Corp", "stock", currency="EUR")
        tx = {
            "symbol": "EXMPL",
            "name": "Example Corp",
            "asset_type": "stock",
            "tx_type": "buy",
            "date": "2026-08-10",
            "quantity": 1.0,
            "price": 50.0,
            "currency": "EUR",
            "fees": 0.0,
            "broker": "Example Broker",
        }
        payload = {"transactions": [tx, dict(tx)]}
        response = await async_test_client.post(
            "/api/v1/import/save", json=payload, headers=auth_headers
        )
        assert response.json()["saved"] == 2
        # Re-importing the same file adds nothing.
        response = await async_test_client.post(
            "/api/v1/import/save", json=payload, headers=auth_headers
        )
        data = response.json()
        assert data["saved"] == 0
        assert data["duplicates_skipped"] == 2


class TestSheetsPullDedup:
    @pytest.mark.asyncio
    async def test_second_pull_adds_nothing(
        self, async_test_client: AsyncClient, auth_headers, test_database, monkeypatch
    ):
        from datetime import date

        from portf_manager.parsers.pdt_xlsx_parser import (
            PDTBooking,
            PDTDividend,
            PDTParseResult,
            PDTTransaction,
        )
        from portf_server.routers import sync as sync_router

        result = PDTParseResult(
            transactions=[
                PDTTransaction(
                    broker="Example Broker",
                    name="Example Corp",
                    pdt_type="Stock market",
                    search="EXMPL",
                    exchange="XAMS",
                    date=date(2026, 8, 10),
                    action="Buy",
                    amount=2.0,
                    price=50.0,
                    price_currency="EUR",
                )
            ],
            dividends=[
                PDTDividend(
                    broker="Example Broker",
                    name="Example Corp",
                    pdt_type="Stock market",
                    search="EXMPL",
                    exchange="XAMS",
                    date=date(2026, 8, 20),
                    action="Cash",
                    amount=1.5,
                    amount_currency="EUR",
                )
            ],
            bookings=[
                PDTBooking(
                    broker="Example Broker",
                    date=date(2026, 8, 1),
                    action="Deposit",
                    amount=100.0,
                    currency="EUR",
                ),
                PDTBooking(
                    broker="Example Broker",
                    date=date(2026, 8, 2),
                    action="Deposit",
                    amount=100.0,
                    currency="EUR",
                ),
            ],
        )

        class FakeSync:
            def pull(self):
                return result

        monkeypatch.setattr(sync_router, "_get_sync", lambda _sid: FakeSync())
        test_database.create_asset("EXMPL", "Example Corp", "stock", currency="EUR")
        url = "/api/v1/sync/pdt-pull?spreadsheet_id=YOUR_SPREADSHEET_ID"

        first = (await async_test_client.post(url, headers=auth_headers)).json()
        assert (
            first["imported_transactions"],
            first["imported_dividends"],
            first["imported_bookings"],
        ) == (1, 1, 2)

        second = (await async_test_client.post(url, headers=auth_headers)).json()
        assert (
            second["imported_transactions"],
            second["imported_dividends"],
            second["imported_bookings"],
        ) == (0, 0, 0)
        assert second["duplicates_skipped"] == 4
        pid = test_database.get_portfolio_by_name("Example Broker")["id"]
        assert len(test_database.get_all_bookings(pid)) == 2
