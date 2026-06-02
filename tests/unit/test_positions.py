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
