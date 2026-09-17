"""Tests for the shared position helper (chronological, split-aware)."""

from portf_manager.positions import compute_positions


def _tx(i, date, ttype, qty, total):
    return {
        "id": i,
        "transaction_date": date,
        "transaction_type": ttype,
        "quantity": qty,
        "total_amount": total,
        "asset_id": 1,
    }


def test_average_cost_reduces_on_partial_sell_chronologically():
    # Buy 10@100 then 10@200 (avg 150), sell 10. Remaining 10 @ avg 150 = 1500.
    txs = [
        _tx(1, "2026-01-01", "buy", 10, 1000),
        _tx(2, "2026-01-02", "buy", 10, 2000),
        _tx(3, "2026-01-03", "sell", 10, 2500),
    ]
    pos, realised = compute_positions(txs)
    assert pos[1]["quantity"] == 10
    assert round(pos[1]["cost"], 2) == 1500.0  # not 3000 (the old DESC bug)
    assert round(realised, 2) == 1000.0  # 2500 proceeds - 1500 avg cost


def test_order_independent_of_input_ordering():
    # Same data fed newest-first must give the same result (helper sorts).
    txs = [
        _tx(3, "2026-01-03", "sell", 10, 2500),
        _tx(2, "2026-01-02", "buy", 10, 2000),
        _tx(1, "2026-01-01", "buy", 10, 1000),
    ]
    pos, _ = compute_positions(txs)
    assert round(pos[1]["cost"], 2) == 1500.0


def test_same_day_ties_use_time_of_day_when_present():
    # A same-day buy that happened EARLIER but was imported with a HIGHER id
    # than the sell (the real Coinbase shape: its CSV export lists rows
    # newest-first, so sequential import assigns ids in reverse chronological
    # order for same-day trades). Date-only ties break on id and would
    # process the sell first, understating realised P&L. A full ISO
    # timestamp in transaction_date sorts correctly regardless of id order.
    txs = [
        _tx(10, "2026-01-01T20:54:56", "sell", 5, 60),  # lower id, later time
        _tx(11, "2026-01-01T20:38:06", "buy", 5, 50),  # higher id, earlier time
    ]
    pos, realised = compute_positions(txs)
    assert pos[1]["quantity"] == 0
    assert round(realised, 2) == 10.0  # 60 proceeds - 50 cost, not silently 0


def test_stock_split_scales_quantity_keeps_cost():
    # Buy 10@100 (cost 1000), 2-for-1 split → 20 shares, cost unchanged.
    txs = [
        _tx(1, "2026-01-01", "buy", 10, 1000),
        _tx(2, "2026-02-01", "split", 2, 0),
    ]
    pos, _ = compute_positions(txs)
    assert pos[1]["quantity"] == 20
    assert pos[1]["cost"] == 1000  # average cost/share halved from 100 to 50


def test_reverse_split():
    txs = [
        _tx(1, "2026-01-01", "buy", 100, 1000),
        _tx(2, "2026-02-01", "split", 0.1, 0),  # 1-for-10 reverse
    ]
    pos, _ = compute_positions(txs)
    assert pos[1]["quantity"] == 10
    assert pos[1]["cost"] == 1000


def test_split_then_sell_uses_post_split_shares():
    # Buy 10@100, 2:1 split → 20 @ avg 50, sell 20 for 1500 → realised 500.
    txs = [
        _tx(1, "2026-01-01", "buy", 10, 1000),
        _tx(2, "2026-02-01", "split", 2, 0),
        _tx(3, "2026-03-01", "sell", 20, 1500),
    ]
    pos, realised = compute_positions(txs)
    assert pos[1]["quantity"] == 0
    assert round(realised, 2) == 500.0
