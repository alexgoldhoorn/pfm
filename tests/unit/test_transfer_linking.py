"""Tests for the transfer-linking service and its booking triggers."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from portf_manager.database import Database
from portf_manager.services import transfer_linking
from portf_manager.services.transfer_linking import (
    link_transfers,
    settle_after_bookings,
    settle_transfers,
)

from tests.unit.test_spending_api import HEADERS, _make_client


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "linking.db"))


@pytest.fixture
def accounts(db):
    return {
        "bank": db.create_portfolio("Example Bank", account_type="bank"),
        "bank2": db.create_portfolio("Second Bank", account_type="bank"),
        "broker": db.create_portfolio("Example Broker"),
    }


def test_booking_imported_after_bank_row_links_it(db, accounts):
    sid = db.create_spending_transaction(
        accounts["bank"], "2026-07-08", "To broker", -800.0
    )
    assert link_transfers(db) == 0
    bid = db.create_booking(
        date="2026-07-08",
        action="Deposit",
        amount=800.0,
        currency="EUR",
        portfolio_id=accounts["broker"],
    )
    assert settle_after_bookings(db) == {"linked": 1, "flagged": 0}
    row = db.get_spending_transaction(sid)
    assert row["transfer_source"] == "match"
    assert row["transfer_link_type"] == "booking"
    assert row["transfer_link_id"] == bid
    assert row["is_transfer"] == 1 and row["category"] == "Transfer"


def test_withdrawal_links_inflow(db, accounts):
    sid = db.create_spending_transaction(
        accounts["bank"], "2026-07-08", "From broker", 500.0
    )
    bid = db.create_booking(
        date="2026-07-09",
        action="Withdrawal",
        amount=500.0,
        currency="EUR",
        portfolio_id=accounts["broker"],
    )
    assert settle_transfers(db) == {"linked": 1, "flagged": 0}
    row = db.get_spending_transaction(sid)
    assert row["transfer_link_type"] == "booking"
    assert row["transfer_link_id"] == bid


def test_claimed_booking_not_claimed_again_across_calls(db, accounts):
    b1 = db.create_booking(
        date="2026-07-01",
        action="Deposit",
        amount=200.0,
        currency="EUR",
        portfolio_id=accounts["broker"],
    )
    b2 = db.create_booking(
        date="2026-07-02",
        action="Deposit",
        amount=200.0,
        currency="EUR",
        portfolio_id=accounts["broker"],
    )
    s1 = db.create_spending_transaction(accounts["bank"], "2026-07-01", "A", -200.0)
    settle_transfers(db)
    s2 = db.create_spending_transaction(accounts["bank"], "2026-07-02", "B", -200.0)
    settle_transfers(db)
    assert db.get_spending_transaction(s1)["transfer_link_id"] == b1
    assert db.get_spending_transaction(s2)["transfer_link_id"] == b2


def test_manual_flagged_row_upgrades_to_match(db, accounts):
    sid = db.create_spending_transaction(accounts["bank"], "2026-07-08", "Move", -800.0)
    db.update_spending_transaction(sid, is_transfer=True, transfer_source="manual")
    bid = db.create_booking(
        date="2026-07-08",
        action="Deposit",
        amount=800.0,
        currency="EUR",
        portfolio_id=accounts["broker"],
    )
    assert settle_after_bookings(db)["linked"] == 1
    row = db.get_spending_transaction(sid)
    assert row["transfer_source"] == "match"
    assert row["transfer_link_id"] == bid


def test_bank_to_bank_pair_links_both_sides(db, accounts):
    a = db.create_spending_transaction(accounts["bank"], "2026-07-08", "Out", -90.0)
    b = db.create_spending_transaction(accounts["bank2"], "2026-07-09", "In", 90.0)
    assert link_transfers(db, [a]) == 1
    ra, rb = db.get_spending_transaction(a), db.get_spending_transaction(b)
    assert (ra["transfer_link_id"], rb["transfer_link_id"]) == (b, a)
    assert ra["transfer_source"] == rb["transfer_source"] == "match"


def test_settle_after_bookings_swallows_errors(db, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(transfer_linking, "settle_transfers", boom)
    assert settle_after_bookings(db) == {"linked": 0, "flagged": 0}


# --- router triggers -------------------------------------------------------


def _bank_row(db, amount=-800.0, day="2026-07-08"):
    bank = db.create_portfolio("Example Bank", account_type="bank")
    return db.create_spending_transaction(bank, day, "To broker", amount)


def test_bookings_endpoint_links_bank_row(tmp_path):
    client, db = _make_client(tmp_path)
    sid = _bank_row(db)
    broker = db.create_portfolio("Example Broker")
    r = client.post(
        "/api/v1/bookings/",
        json={
            "portfolio_id": broker,
            "date": "2026-07-08",
            "action": "Deposit",
            "amount": 800.0,
        },
        headers=HEADERS,
    )
    assert r.status_code == 201
    assert db.get_spending_transaction(sid)["transfer_link_type"] == "booking"


def test_import_save_with_booking_links_bank_row(tmp_path):
    client, db = _make_client(tmp_path)
    sid = _bank_row(db)
    r = client.post(
        "/api/v1/import/save",
        json={
            "transactions": [],
            "bookings": [
                {
                    "broker": "Example Broker",
                    "date": "2026-07-08",
                    "action": "Deposit",
                    "amount": 800.0,
                    "currency": "EUR",
                }
            ],
        },
        headers=HEADERS,
    )
    assert r.status_code == 200 and r.json()["saved_bookings"] == 1
    assert db.get_spending_transaction(sid)["transfer_link_type"] == "booking"


def _pull_with_booking(monkeypatch):
    from portf_manager.parsers.pdt_xlsx_parser import PDTBooking, PDTParseResult
    from portf_server.routers import sync as sync_router

    result = PDTParseResult(
        transactions=[],
        dividends=[],
        bookings=[
            PDTBooking(
                broker="Example Broker",
                date=date(2026, 7, 8),
                action="Deposit",
                amount=800.0,
                currency="EUR",
            )
        ],
    )

    class FakeSync:
        def pull(self):
            return result

    monkeypatch.setattr(sync_router, "_get_sync", lambda _sid: FakeSync())
    return "/api/v1/sync/pdt-pull?spreadsheet_id=YOUR_SPREADSHEET_ID"


def test_pdt_pull_with_booking_links_bank_row(tmp_path, monkeypatch):
    client, db = _make_client(tmp_path)
    sid = _bank_row(db)
    r = client.post(_pull_with_booking(monkeypatch), headers=HEADERS)
    assert r.status_code == 200 and r.json()["imported_bookings"] == 1
    assert db.get_spending_transaction(sid)["transfer_link_type"] == "booking"


def test_booking_survives_a_failing_settle(tmp_path, monkeypatch):
    client, db = _make_client(tmp_path)
    _bank_row(db)
    broker = db.create_portfolio("Example Broker")

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(transfer_linking, "settle_transfers", boom)
    r = client.post(
        "/api/v1/bookings/",
        json={
            "portfolio_id": broker,
            "date": "2026-07-08",
            "action": "Deposit",
            "amount": 800.0,
        },
        headers=HEADERS,
    )
    assert r.status_code == 201
    assert len(db.get_all_bookings()) == 1


def test_rescan_reports_linked_and_flagged(tmp_path):
    client, db = _make_client(tmp_path)
    _bank_row(db)
    r = client.post("/api/v1/spending/rescan-transfers", headers=HEADERS)
    assert r.json() == {"transfers_linked": 0, "transfers_flagged": 0}


# --- CLI -------------------------------------------------------------------


def _cli_with(db_manager):
    from portf_manager.cli import PortfolioManagerCLI

    cli = PortfolioManagerCLI.__new__(PortfolioManagerCLI)
    cli.db_manager = db_manager
    return cli


def test_cli_booking_import_settles_with_a_real_database(db, accounts, monkeypatch):
    calls = []
    monkeypatch.setattr(
        transfer_linking, "settle_after_bookings", lambda d: calls.append(d)
    )
    bk = MagicMock(
        broker=None, date=date(2026, 7, 8), action="Deposit", amount=5.0, currency="EUR"
    )
    n = _cli_with(db)._import_pdt_bookings([bk], accounts["broker"])
    assert n == 1 and calls == [db]


def test_cli_booking_import_does_not_settle_over_http(monkeypatch):
    calls = []
    monkeypatch.setattr(
        transfer_linking, "settle_after_bookings", lambda d: calls.append(d)
    )
    http = MagicMock(spec=["create_booking", "get_portfolio_by_name"])
    http.create_booking.return_value = 1
    bk = MagicMock(
        broker=None, date=date(2026, 7, 8), action="Deposit", amount=5.0, currency="EUR"
    )
    n = _cli_with(http)._import_pdt_bookings([bk], 1)
    assert n == 1 and calls == []


# --- own-account patterns ---------------------------------------------------


def _row(db, pid, day, desc, amount):
    return db.create_spending_transaction(pid, day, desc, amount)


def test_pattern_flags_on_settle(db, accounts):
    db.set_setting("own_account_patterns", '["Sam Sample"]')
    sid = _row(db, accounts["bank"], "2026-07-08", "Transfer to Sam Sample", -300.0)
    assert settle_transfers(db, [sid]) == {"linked": 0, "flagged": 1}
    row = db.get_spending_transaction(sid)
    assert row["transfer_source"] == "pattern"
    assert row["category"] == "Transfer"
    assert row["is_transfer"] == 1
    assert row["transfer_link_id"] is None


def test_pair_match_wins_over_pattern(db, accounts):
    db.set_setting("own_account_patterns", '["Sam Sample"]')
    a = _row(db, accounts["bank"], "2026-07-08", "To Sam Sample", -300.0)
    b = _row(db, accounts["bank2"], "2026-07-09", "From Sam Sample", 300.0)
    assert settle_transfers(db) == {"linked": 1, "flagged": 0}
    for sid, other in ((a, b), (b, a)):
        row = db.get_spending_transaction(sid)
        assert row["transfer_source"] == "match"
        assert row["transfer_link_id"] == other


def test_later_leg_links_to_pattern_flagged_row(db, accounts):
    db.set_setting("own_account_patterns", '["Sam Sample"]')
    a = _row(db, accounts["bank"], "2026-07-08", "To Sam Sample", -300.0)
    settle_transfers(db)
    assert db.get_spending_transaction(a)["transfer_source"] == "pattern"
    b = _row(db, accounts["bank2"], "2026-07-09", "Incoming", 300.0)
    assert settle_transfers(db)["linked"] == 1
    assert db.get_spending_transaction(a)["transfer_source"] == "match"
    assert db.get_spending_transaction(a)["transfer_link_id"] == b


def test_pattern_leaves_linked_row_alone(db, accounts):
    a = _row(db, accounts["bank"], "2026-07-08", "To Sam Sample", -300.0)
    b = _row(db, accounts["bank2"], "2026-07-09", "Incoming", 300.0)
    assert link_transfers(db) == 1
    before = db.get_spending_transaction(a)
    assert transfer_linking.flag_own_account_rows(db, patterns=["Sam Sample"]) == 0
    assert db.get_spending_transaction(a) == before
    assert db.get_spending_transaction(b)["transfer_source"] == "match"


def test_pattern_overrides_existing_category(db, accounts):
    sid = _row(db, accounts["bank"], "2026-07-08", "Sam Sample", -50.0)
    db.update_spending_transaction(sid, category="Groceries")
    assert transfer_linking.flag_own_account_rows(db, patterns=["Sam Sample"]) == 1
    assert db.get_spending_transaction(sid)["category"] == "Transfer"


def test_pattern_skips_rejected_row(db, accounts):
    sid = _row(db, accounts["bank"], "2026-07-08", "Sam Sample", -50.0)
    db.update_spending_transaction(sid, transfer_source="rejected")
    assert transfer_linking.flag_own_account_rows(db, patterns=["Sam Sample"]) == 0
    row = db.get_spending_transaction(sid)
    assert row["transfer_source"] == "rejected" and row["is_transfer"] == 0


def test_removing_pattern_does_not_unflag(db, accounts):
    sid = _row(db, accounts["bank"], "2026-07-08", "Sam Sample", -50.0)
    assert transfer_linking.flag_own_account_rows(db, patterns=["Sam Sample"]) == 1
    assert transfer_linking.flag_own_account_rows(db, patterns=[]) == 0
    assert db.get_spending_transaction(sid)["transfer_source"] == "pattern"


def test_preview_lists_without_writing(db, accounts):
    sid = _row(db, accounts["bank"], "2026-07-08", "Sam Sample", -50.0)
    rows = transfer_linking.preview_own_account_pattern(db, "sam sample")
    assert [r["id"] for r in rows] == [sid]
    assert db.get_spending_transaction(sid)["is_transfer"] == 0
