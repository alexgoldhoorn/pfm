"""Unit tests for the Coinbase CSV parser (synthetic data)."""

from portf_manager.parsers.coinbase_csv_parser import parse_coinbase_csv

# Real Coinbase export shape: 2 preamble lines, then the CSV header, then rows.
HEADER = (
    "Timestamp,Transaction Type,Asset,Quantity Transacted,"
    "Price Currency,Price at Transaction,"
    "Total (inclusive of fees and/or spread),Notes"
)
CSV = (
    "Transactions\nuser@example.com\n"
    + HEADER
    + "\n"
    + "\n".join(
        [
            "2025-08-25 20:34:04 UTC,Buy,BTC,0.001,EUR,60000,60.50,bought",
            "2025-08-20 10:00:00 UTC,Deposit,EUR,500,EUR,,500,sepa in",
            "2025-08-22 11:00:00 UTC,Receive,BTC,0.002,EUR,,120,received btc",
            "2025-08-26 09:00:00 UTC,Withdrawal,EUR,200,EUR,,200,withdrew",
        ]
    )
)


def test_fiat_deposit_and_withdrawal_become_bookings():
    r = parse_coinbase_csv(CSV)
    actions = {(b["action"], b["amount"], b["currency"]) for b in r.bookings}
    assert ("Deposit", 500.0, "EUR") in actions
    assert ("Withdrawal", 200.0, "EUR") in actions
    assert len(r.bookings) == 2
    # The booking date is the calendar date of the row.
    dep = next(b for b in r.bookings if b["action"] == "Deposit")
    assert dep["date"] == "2025-08-20"


def test_trade_still_imported_and_crypto_transfer_skipped():
    r = parse_coinbase_csv(CSV)
    # The Buy is still an importable trade.
    assert [t.symbol for t in r.importable] == ["BTC"]
    # The crypto Receive is skipped, NOT a booking.
    assert any(t == "Receive" for t, _reason in r.skipped)
    assert all(b["currency"] == "EUR" for b in r.bookings)  # no crypto bookings
