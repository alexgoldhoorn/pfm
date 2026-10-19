"""Allocator candidates, target checks, bucket split and traspasos."""

import json
from datetime import date, datetime

import pytest

from portf_manager.database import Database
from portf_manager.services import allocator
from portf_manager.services.allocator import (
    build_candidates,
    check_targets,
    investable_targets,
    plan_traspasos,
    split_buckets,
)
from portf_manager.services.deployable import load_settings
from portf_manager.services.rebalance_planner import compute_before_state
from portf_manager.services.wash_sale import WashSaleUnavailable

TODAY = date(2026, 10, 19)


def _fx(currency):
    return 1.0


def _before(rows):
    return {
        "allocations": [
            {"asset_type": t, "current_pct": pct} for t, pct in rows.items()
        ]
    }


def test_check_targets_sum_99_is_incomplete():
    reasons = check_targets({"stock": 60, "etf": 39}, _before({"stock": 60}))
    assert len(reasons) == 1 and "99.0" in reasons[0]
    assert check_targets({"stock": 60, "etf": 40}, _before({"stock": 60})) == []


def test_check_targets_missing_held_type_over_1pct():
    before = _before({"stock": 60, "crypto": 1.5, "bond": 0.5})
    reasons = check_targets({"stock": 100}, before)
    assert len(reasons) == 1 and "crypto" in reasons[0]
    # an explicit zero is a target
    assert check_targets({"stock": 98, "crypto": 2, "bond": 0}, before) == []


def test_investable_targets_renormalise_without_cash():
    cands = [{"bucket": "stock"}, {"bucket": "etf"}]
    result = investable_targets({"stock": 50, "etf": 30, "cash": 20}, cands)
    assert result == {"stock": pytest.approx(62.5), "etf": pytest.approx(37.5)}
    # a bucket with no candidate is dropped too
    assert investable_targets({"stock": 50, "bond": 50}, cands) == {"stock": 100}


def test_split_water_fills_proportionally_and_caps():
    rows = split_buckets(
        1000,
        {"stock": 60, "etf": 20, "crypto": 20},
        {"stock": 900, "etf": 300, "crypto": 800},
        100,
    )
    by = {r["asset_type"]: r for r in rows}
    # gaps 900 / 300 / -200: crypto is overweight and gets nothing
    assert by["stock"]["gap_eur"] == 900 and by["etf"]["gap_eur"] == 300
    assert by["stock"]["allocated_eur"] == 750
    assert by["etf"]["allocated_eur"] == 250
    assert by["crypto"]["allocated_eur"] == 0
    assert by["stock"]["before_pct"] == pytest.approx(900 / 2000 * 100)
    assert by["stock"]["after_pct"] == pytest.approx(1650 / 3000 * 100)
    # a gap smaller than the amount's share caps the allocation
    capped = split_buckets(
        1000, {"stock": 90, "etf": 10}, {"stock": 0, "etf": 1050}, 100
    )
    assert {r["asset_type"]: r["allocated_eur"] for r in capped} == {
        "stock": 1000,
        "etf": 0,
    }


def test_split_remainder_goes_by_target_when_gaps_run_out():
    # targets sum to 60 only: gaps total less than the amount
    rows = split_buckets(
        1000, {"stock": 40, "etf": 20}, {"stock": 100, "etf": 100}, 100
    )
    alloc = {r["asset_type"]: r["allocated_eur"] for r in rows}
    # gaps 380 / 140 are filled, the other 480 follows the 2:1 targets
    assert alloc == {"stock": 700, "etf": 300}


def test_split_all_overweight_follows_targets():
    rows = split_buckets(100, {"stock": 40, "etf": 20}, {"stock": 900, "etf": 500}, 10)
    alloc = {r["asset_type"]: r["allocated_eur"] for r in rows}
    assert alloc == {"stock": 66.67, "etf": 33.33}
    assert sum(alloc.values()) == pytest.approx(100)


def test_split_drops_lines_under_min_and_reallocates():
    rows = split_buckets(
        1000, {"stock": 80, "etf": 20}, {"stock": 2000, "etf": 675}, 100
    )
    by = {r["asset_type"]: r for r in rows}
    assert by["etf"]["gap_eur"] == 60
    assert by["etf"]["allocated_eur"] == 0
    assert by["stock"]["allocated_eur"] == 1000


def test_split_sums_exactly_after_rounding():
    rows = split_buckets(
        100.01,
        {"a": 100 / 3, "b": 100 / 3, "c": 100 / 3},
        {"a": 10, "b": 20, "c": 30},
        1,
    )
    assert round(sum(r["allocated_eur"] for r in rows), 2) == 100.01


def _fund(asset_id, bucket, value, ter, **extra):
    base = {
        "asset_id": asset_id,
        "symbol": f"F{asset_id}",
        "name": f"Fund {asset_id}",
        "asset_type": bucket,
        "bucket": bucket,
        "portfolio_id": 1,
        "portfolio_name": "Example Broker",
        "value_eur": value,
        "is_fund": True,
        "ter_pct": ter,
        "overlap_group": None,
        "managed": False,
        "is_watchlist": False,
    }
    base.update(extra)
    return base


def test_traspaso_only_between_funds_and_not_from_managed():
    cands = [
        _fund(1, "mutual_fund", 800, 0.5, managed=True),
        _fund(2, "etf", 800, 0.9),
        {**_fund(3, "stock", 100, None), "is_fund": False},
        _fund(4, "index", 100, 0.1),
    ]
    cands[0]["asset_id"] = None
    traspasos, after = plan_traspasos(
        cands,
        {"mutual_fund": 20, "etf": 20, "stock": 20, "index": 40},
        {"mutual_fund": 800, "etf": 800, "stock": 100, "index": 100},
        5,
        {},
        {},
    )
    # the only overweight sources are a managed fund and an ETF: no traspaso
    assert traspasos == []
    assert after == {"mutual_fund": 800, "etf": 800, "stock": 100, "index": 100}


def test_traspaso_amount_and_gain():
    cands = [
        _fund(1, "mutual_fund", 600, 0.30),
        _fund(2, "mutual_fund", 200, 0.85),
        _fund(3, "index", 100, 0.20),
        _fund(4, "index", 100, 0.05),
    ]
    before = {"mutual_fund": 800, "index": 200}
    traspasos, after = plan_traspasos(
        cands,
        {"mutual_fund": 50, "index": 50},
        before,
        5,
        {2: [{"quantity": 10.0, "unit_cost_eur": 10.0}]},
        {2: 20.0},
    )
    assert len(traspasos) == 1
    t = traspasos[0]
    # excess 300, room 300, source holds only 200: highest TER fund, capped
    assert t["from"]["asset_id"] == 2 and t["to"]["asset_id"] == 4
    assert t["amount_eur"] == 200
    # 10 units at 20 EUR over cost 10 EUR: 100 EUR gain
    assert t["gain_eur"] == 100
    assert "untaxed" in t["reason"] and "100" in t["reason"]
    assert after == {"mutual_fund": 600, "index": 400}


def test_traspaso_below_threshold_does_nothing():
    cands = [_fund(1, "mutual_fund", 540, 0.5), _fund(2, "index", 460, 0.1)]
    traspasos, after = plan_traspasos(
        cands,
        {"mutual_fund": 50, "index": 50},
        {"mutual_fund": 540, "index": 460},
        5,
        {},
        {},
    )
    assert traspasos == []


def test_traspaso_prefers_smaller_overlap_member():
    group = {"kind": "consolidation_candidate", "largest_member_id": 1}
    cands = [
        _fund(1, "mutual_fund", 500, 0.9, overlap_group=group),
        _fund(2, "mutual_fund", 300, 0.2, overlap_group=group),
        _fund(3, "index", 100, 0.1),
    ]
    traspasos, _ = plan_traspasos(
        cands,
        {"mutual_fund": 40, "index": 60},
        {"mutual_fund": 800, "index": 100},
        5,
        {},
        {},
    )
    assert traspasos[0]["from"]["asset_id"] == 2
    assert "overlap" in traspasos[0]["reason"]
    assert traspasos[0]["gain_eur"] is None


# ---- build_candidates (database) -------------------------------------------


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "alloc.db"))


def _buy(db, pid, symbol, name, atype, qty, price, ter=None, exchange=None):
    aid = db.create_asset(symbol, name, atype, exchange=exchange, currency="EUR")
    db.create_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=qty,
        price=price,
        total_amount=qty * price,
        transaction_date="2026-01-02",
        portfolio_id=pid,
        currency="EUR",
    )
    db.insert_price_record(symbol, price, datetime(2026, 10, 15, 9, 0))
    if atype in ("etf", "mutual_fund", "index"):
        db.upsert_fund_profile(
            asset_id=aid,
            benchmark_key=None,
            source="manual",
            asset_class=json.dumps({"equity": 1.0}),
            regions=json.dumps({"north_america": 1.0}),
            sectors="{}",
            currency_hedged=0,
            hedge_currency=None,
            as_of="2026-09-01",
            ongoing_charges_pct=ter,
        )
    return aid


def _state(db):
    before, _, holdings, txns = compute_before_state(db, None, None)
    return holdings, txns


@pytest.fixture(autouse=True)
def no_wash(monkeypatch):
    monkeypatch.setattr(allocator, "blocked_assets", lambda *a, **k: {})
    # exposure looks up sector and country of direct holdings over the network
    monkeypatch.setattr(
        "portf_manager.services.exposure._resolve_sector_country",
        lambda db, asset: ("Unknown", "Unknown"),
    )


def test_build_candidates_collapses_managed_portfolio(db):
    db.set_allocation_target("mutual_fund", 100)
    managed = db.get_or_create_portfolio("Example Managed")
    plain = db.get_or_create_portfolio("Example Broker")
    _buy(db, managed, "MF1", "Managed Fund One", "mutual_fund", 10, 100, ter=0.2)
    _buy(db, managed, "MF2", "Managed Fund Two", "mutual_fund", 10, 300, ter=0.4)
    _buy(db, plain, "OWN", "Own Fund", "mutual_fund", 5, 100, ter=0.1)
    holdings, txns = _state(db)
    settings = {**load_settings(db), "managed_portfolio_ids": [managed]}
    cands, warnings = build_candidates(db, holdings, txns, settings, TODAY, _fx)
    assert warnings == []
    assert len(cands) == 2
    m = next(c for c in cands if c["managed"])
    assert m["symbol"] == "contribution:Example Managed"
    assert m["asset_id"] is None and m["portfolio_id"] == managed
    assert m["bucket"] == "mutual_fund" and m["is_fund"] is True
    assert m["value_eur"] == 4000
    # value-weighted TER: (1000 * 0.2 + 3000 * 0.4) / 4000
    assert m["ter_pct"] == pytest.approx(0.35)
    own = next(c for c in cands if not c["managed"])
    assert own["ter_pct"] == 0.1 and own["portfolio_name"] == "Example Broker"
    assert own["weight_pct"] == pytest.approx(500 / 4500 * 100)


def test_build_candidates_marks_overlap_blocked_and_watchlist(db, monkeypatch):
    db.set_allocation_target("etf", 100)
    broker = db.get_or_create_portfolio("Example Broker")
    a = _buy(db, broker, "WLD1", "World Fund A", "etf", 10, 100, ter=0.2)
    b = _buy(db, broker, "WLD2", "World Fund B", "etf", 2, 100, ter=0.3)
    for aid in (a, b):
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE fund_profiles SET benchmark_key = 'msci_world' "
                "WHERE asset_id = ?",
                (aid,),
            )
            conn.commit()
    stock = _buy(db, broker, "LOSS", "Example Corp", "stock", 10, 50)
    db.upsert_price_target(stock, buy_below=45.0, fair_value=60.0)
    db.create_research_note(
        asset_id=stock, symbol="LOSS", conviction="high", buy_below=40.0
    )
    # a priced, unheld watchlist stock
    wl = db.create_asset("WISH", "Wish Corp", "stock", currency="EUR")
    db.insert_price_record("WISH", 20.0, datetime(2026, 10, 15, 9, 0))
    db.add_watchlist("WISH", "Wish Corp", "stock", buy_below=18.0)
    db.add_watchlist("NOPE", "Unknown", "stock")
    monkeypatch.setattr(
        allocator,
        "blocked_assets",
        lambda *a, **k: {
            stock: {
                "symbol": "LOSS",
                "sold_on": "2026-09-01",
                "loss_eur": 100.0,
                "safe_from": "2026-11-01",
            }
        },
    )
    holdings, txns = _state(db)
    cands, warnings = build_candidates(
        db, holdings, txns, load_settings(db), TODAY, _fx
    )
    by = {c["symbol"]: c for c in cands}
    assert set(by) == {"WLD1", "WLD2", "LOSS", "WISH"}
    assert by["WLD1"]["overlap_group"] == {
        "kind": "consolidation_candidate",
        "largest_member_id": a,
    }
    assert by["WLD2"]["overlap_group"]["largest_member_id"] == a
    assert by["LOSS"]["blocked"]["safe_from"] == "2026-11-01"
    assert by["LOSS"]["buy_below"] == 45.0 and by["LOSS"]["fair_value"] == 60.0
    assert by["LOSS"]["conviction"] == "high"
    assert by["WLD1"]["blocked"] is None
    wish = by["WISH"]
    assert wish["is_watchlist"] and wish["value_eur"] == 0
    assert wish["asset_id"] == wl and wish["buy_below"] == 18.0
    assert wish["portfolio_name"] == "Example Broker"


def test_build_candidates_wash_unavailable_blocks_stocks_only(db, monkeypatch):
    db.set_allocation_target("etf", 100)
    broker = db.get_or_create_portfolio("Example Broker")
    _buy(db, broker, "STK", "Example Corp", "stock", 1, 100)
    _buy(db, broker, "ETFX", "Example Fund ETF", "etf", 1, 100, ter=0.1)

    def boom(*a, **k):
        raise WashSaleUnavailable("tax report failed")

    monkeypatch.setattr(allocator, "blocked_assets", boom)
    holdings, txns = _state(db)
    cands, warnings = build_candidates(
        db, holdings, txns, load_settings(db), TODAY, _fx
    )
    by = {c["symbol"]: c for c in cands}
    assert by["STK"]["blocked"] == {"reason": "antiaplicación check unavailable"}
    assert by["ETFX"]["blocked"] is None
    assert len(warnings) == 1
