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


# ---- ranking and next_euro --------------------------------------------------


def _cand(asset_id, bucket, **extra):
    base = {
        "asset_id": asset_id,
        "symbol": f"S{asset_id}",
        "name": f"Name {asset_id}",
        "asset_type": bucket,
        "bucket": bucket,
        "portfolio_id": 1,
        "portfolio_name": "Example Broker",
        "value_eur": 0.0,
        "weight_pct": 0.0,
        "is_fund": bucket in ("etf", "mutual_fund"),
        "ter_pct": None,
        "overlap_group": None,
        "buy_below": None,
        "fair_value": None,
        "recommendation": None,
        "conviction": None,
        "blocked": None,
        "managed": False,
        "is_watchlist": False,
        "price_eur": 10.0,
        "price_native": 10.0,
        "currency": "EUR",
    }
    base.update(extra)
    return base


def test_fund_rank_prefers_lowest_ter_outside_overlap():
    big = _cand(
        1, "etf", ter_pct=0.3, value_eur=900, overlap_group={"largest_member_id": 1}
    )
    small = _cand(
        2, "etf", ter_pct=0.05, value_eur=100, overlap_group={"largest_member_id": 1}
    )
    other = _cand(3, "etf", ter_pct=0.2, value_eur=500)
    ranked = allocator.rank_fund_candidates([big, small, other])
    assert [c["asset_id"] for c, _ in ranked] == [3, 1]
    excl = allocator.fund_exclusions([big, small, other])
    assert excl[0][1] == "overlaps with S1, which is the bigger holding"


def test_fund_without_ter_ranks_last_with_hint():
    a = _cand(1, "etf", ter_pct=None, value_eur=900)
    b = _cand(2, "etf", ter_pct=0.5, value_eur=1)
    ranked = allocator.rank_fund_candidates([a, b])
    assert [c["asset_id"] for c, _ in ranked] == [2, 1]
    assert "TER unknown, enter it on the Fees tab" in ranked[1][1]


def test_stock_rank_price_under_target_first_then_recommendation():
    cheap = _cand(1, "stock", buy_below=12.0, weight_pct=2)
    buy = _cand(2, "stock", buy_below=5.0, recommendation="BUY", weight_pct=1)
    plain = _cand(3, "stock", conviction=3, weight_pct=0.5)
    ranked, needs, excl = allocator.rank_stock_candidates(
        [plain, buy, cheap], 10000, 5.0
    )
    assert [c["asset_id"] for c, _ in ranked] == [1, 2, 3]
    assert "at or below your buy-below of 12.00 EUR" in ranked[0][1][0]
    assert needs == [] and excl == []


def test_stock_over_cap_excluded_with_reason():
    big = _cand(1, "stock", value_eur=480, buy_below=20.0)
    ok = _cand(2, "stock", value_eur=0, buy_below=20.0)
    ranked, _, excl = allocator.rank_stock_candidates([big, ok], 10000, 5.0, 100.0)
    assert [c["asset_id"] for c, _ in ranked] == [2]
    assert excl[0][1] == "would exceed the 5 % single-stock cap"


def test_blocked_stock_never_proposed():
    blocked = _cand(
        1,
        "stock",
        buy_below=20.0,
        blocked={"reason": "sold at a loss", "safe_from": "x"},
    )
    ranked, _, excl = allocator.rank_stock_candidates([blocked], 10000, 5.0)
    assert ranked == [] and excl[0][0] is blocked


def test_unresearched_stock_goes_to_needs_research_and_unallocated():
    raw = _cand(1, "stock", weight_pct=1.5)
    settings = {
        "max_lines_per_bucket": 2,
        "min_line_eur": 100,
        "max_single_stock_pct": 5.0,
    }
    lines, left, needs, why = allocator.pick_lines(
        "stock", [raw], 500.0, settings, {"total_after": 10000}
    )
    assert lines == [] and left == 500.0
    assert needs == [{"symbol": "S1", "weight_pct": 1.5}]
    assert why == ["no researched candidate under the price target"]


def test_pick_lines_splits_drops_small_and_names_runner_up():
    settings = {
        "max_lines_per_bucket": 2,
        "min_line_eur": 100,
        "max_single_stock_pct": 5.0,
    }
    funds = [
        _cand(1, "etf", ter_pct=0.07, value_eur=10),
        _cand(2, "etf", ter_pct=0.22, value_eur=10),
        _cand(3, "etf", ter_pct=0.40, value_eur=10),
    ]
    ctx = {"total_after": 10000}
    lines, left, _, _ = allocator.pick_lines("etf", funds, 301.0, settings, ctx)
    assert [ln["amount_eur"] for ln in lines] == [150.5, 150.5] and left == 0
    assert lines[0]["runner_up"] == {
        "symbol": "S3",
        "why_not": "higher TER (0.40 % vs 0.07 %)",
    }
    lines, _, _, _ = allocator.pick_lines("etf", funds, 150.0, settings, ctx)
    assert len(lines) == 1 and lines[0]["amount_eur"] == 150.0


def test_crypto_split_by_weights():
    settings = {
        "max_lines_per_bucket": 2,
        "min_line_eur": 100,
        "max_single_stock_pct": 5.0,
    }
    coins = [
        _cand(1, "crypto", value_eur=750),
        _cand(2, "crypto", value_eur=250),
        _cand(3, "crypto", value_eur=0),
    ]
    lines, left, _, _ = allocator.pick_lines(
        "crypto", coins, 400.0, settings, {"total_after": 10000}
    )
    assert [ln["amount_eur"] for ln in lines] == [300.0, 100.0] and left == 0


@pytest.fixture
def stub_deployable(monkeypatch):
    def make(**over):
        dep = {
            "status": "ok",
            "liquid_eur": 5000.0,
            "tax_reserve": {"reserve_eur": 500.0},
            "buffer": {"target_eur": 3000.0},
            "deployable_eur": 1500.0,
            "warnings": [],
        }
        dep.update(over)
        monkeypatch.setattr(
            allocator.deployable_service, "deployable", lambda *a, **k: dep
        )

    return make


def _fx_on(currency, day):
    return 1.0


def test_next_euro_amount_above_deployable_warns(db, stub_deployable):
    stub_deployable()
    db.set_allocation_target("etf", 100)
    broker = db.get_or_create_portfolio("Example Broker")
    _buy(db, broker, "WLD1", "World Fund A", "etf", 10, 100, ter=0.2)
    res = allocator.next_euro(db, 2000.0, TODAY, _fx, _fx_on)
    assert res["status"] == "ok"
    assert "amount exceeds deployable cash by 500.00 EUR" in res["warnings"]
    assert sum(ln["amount_eur"] for ln in res["lines"]) == 2000.0
    assert res["lines"][0]["symbol"] == "WLD1" and "WLD1" in res["chat_summary"]


def test_next_euro_statuses(db, stub_deployable):
    stub_deployable(
        status="insufficient_data", deployable_eur=None, reason="few months"
    )
    res = allocator.next_euro(db, None, TODAY, _fx, _fx_on)
    assert res["status"] == "insufficient_data" and res["lines"] == []
    stub_deployable()
    res = allocator.next_euro(db, None, TODAY, _fx, _fx_on)
    assert res["status"] == "targets_incomplete" and res["reasons"]
    db.set_allocation_target("etf", 100)
    stub_deployable(deployable_eur=0.0)
    res = allocator.next_euro(db, None, TODAY, _fx, _fx_on)
    assert res["status"] == "nothing_to_deploy"


def test_chat_summary_mentions_every_line():
    result = {
        "status": "ok",
        "amount_eur": 1234.56,
        "deployable": {"liquid_eur": 5000, "deployable_eur": 1234.56},
        "buckets": [
            {
                "asset_type": "etf",
                "target_pct": 100,
                "before_pct": 10,
                "after_pct": 20,
                "gap_eur": 0,
                "allocated_eur": 1234.56,
            }
        ],
        "lines": [
            {
                "symbol": sym,
                "name": "N",
                "portfolio_name": "P",
                "amount_eur": 617.28,
                "reasons": ["r"],
                "runner_up": None,
            }
            for sym in ("AAA", "BBB")
        ],
        "traspasos": [],
        "blocked": [{"symbol": "CCC", "reason": "sold at a loss"}],
        "needs_research": [{"symbol": "DDD", "weight_pct": 1.0}],
        "warnings": ["w"],
    }
    text = allocator.chat_summary(result)
    for sym in ("AAA", "BBB", "CCC", "DDD"):
        assert sym in text
    assert "1,234.56 EUR" in text and "Traspasos" not in text


_STOCK_SETTINGS = {
    "max_lines_per_bucket": 2,
    "min_line_eur": 100,
    "max_single_stock_pct": 5.0,
}


def test_single_stock_cap_clamps_line_and_leaves_rest_unallocated():
    one = _cand(1, "stock", buy_below=20.0)
    lines, left, _, why = allocator.pick_lines(
        "stock", [one], 1000.0, _STOCK_SETTINGS, {"total_after": 10000}
    )
    assert [ln["amount_eur"] for ln in lines] == [500.0] and left == 500.0
    assert why == ["would exceed the 5 % single-stock cap"]


def test_single_stock_cap_spills_to_next_candidate():
    a = _cand(1, "stock", buy_below=20.0, value_eur=400)
    b = _cand(2, "stock", buy_below=5.0, value_eur=0, weight_pct=1)
    lines, left, _, _ = allocator.pick_lines(
        "stock", [a, b], 400.0, _STOCK_SETTINGS, {"total_after": 10000}
    )
    assert left == 0
    assert {ln["symbol"]: ln["amount_eur"] for ln in lines} == {
        "S1": 100.0,
        "S2": 300.0,
    }


def test_unallocated_reason_names_blocked_stocks():
    blocked = _cand(
        1,
        "stock",
        buy_below=20.0,
        blocked={"reason": "sold at a loss", "safe_from": "x"},
    )
    lines, left, _, why = allocator.pick_lines(
        "stock", [blocked], 300.0, _STOCK_SETTINGS, {"total_after": 10000}
    )
    assert lines == [] and left == 300.0 and why == ["S1: sold at a loss"]


def test_next_euro_conserves_money_across_buckets(db, stub_deployable):
    stub_deployable()
    db.set_allocation_target("etf", 60)
    db.set_allocation_target("stock", 40)
    broker = db.get_or_create_portfolio("Example Broker")
    _buy(db, broker, "WLD1", "World Fund A", "etf", 10, 100, ter=0.2)
    _buy(db, broker, "STK1", "Stock One", "stock", 10, 100)
    res = allocator.next_euro(db, 1001.0, TODAY, _fx, _fx_on)
    placed = sum(ln["amount_eur"] for ln in res["lines"])
    assert round(placed + res["unallocated_eur"], 2) == 1001.0
    assert res["unallocated_eur"] > 0
    assert any("stock" in w and "unallocated" in w for w in res["warnings"])


def test_next_euro_explicit_amount_with_insufficient_data_warns(db, stub_deployable):
    stub_deployable(
        status="insufficient_data", deployable_eur=None, reason="few months"
    )
    db.set_allocation_target("etf", 100)
    broker = db.get_or_create_portfolio("Example Broker")
    _buy(db, broker, "WLD1", "World Fund A", "etf", 10, 100, ter=0.2)
    res = allocator.next_euro(db, 500.0, TODAY, _fx, _fx_on)
    assert "deployable unknown: few months" in res["warnings"]
    assert not any("exceeds" in w for w in res["warnings"])


# ---- final review fixes -----------------------------------------------------

_BLOCK = {"reason": "sold at a loss", "safe_from": "2026-12-01", "loss_eur": 50}
_PICK_SETTINGS = {
    "max_lines_per_bucket": 2,
    "min_line_eur": 100,
    "max_single_stock_pct": 5.0,
}


@pytest.mark.parametrize("bucket", ["etf", "mutual_fund", "bond"])
def test_blocked_candidate_is_never_proposed(bucket):
    bad = _cand(1, bucket, ter_pct=0.01, blocked=_BLOCK, weight_pct=0.1)
    good = _cand(2, bucket, ter_pct=0.5, weight_pct=5)
    ctx = {"total_after": 10000}
    lines, left, _, why = allocator.pick_lines(
        bucket, [bad, good], 400.0, _PICK_SETTINGS, ctx
    )
    assert [ln["symbol"] for ln in lines] == ["S2"] and left == 0
    lines, left, _, why = allocator.pick_lines(
        bucket, [bad], 400.0, _PICK_SETTINGS, ctx
    )
    assert lines == [] and left == 400.0
    assert any("S1" in w and "sold at a loss" in w for w in why)


def test_blocked_other_bucket_is_never_proposed():
    bad = _cand(1, "other", blocked=_BLOCK)
    lines, left, _, _ = allocator.pick_lines(
        "other", [bad], 300.0, _PICK_SETTINGS, {"total_after": 10000}
    )
    assert lines == [] and left == 300.0


def test_traspaso_never_to_a_blocked_fund():
    cands = [
        _fund(1, "mutual_fund", 800, 0.5),
        _fund(2, "index", 100, 0.1, blocked=_BLOCK),
    ]
    traspasos, after = plan_traspasos(
        cands,
        {"mutual_fund": 20, "index": 80},
        {"mutual_fund": 800, "index": 100},
        5,
        {},
        {},
    )
    assert traspasos == [] and after == {"mutual_fund": 800, "index": 100}
    # a blocked fund may still be the source: nothing is sold
    cands = [
        _fund(1, "mutual_fund", 800, 0.5, blocked=_BLOCK),
        _fund(2, "index", 100, 0.1),
    ]
    traspasos, _ = plan_traspasos(
        cands,
        {"mutual_fund": 20, "index": 80},
        {"mutual_fund": 800, "index": 100},
        5,
        {},
        {},
    )
    assert [t["to"]["symbol"] for t in traspasos] == ["F2"]


def test_traspaso_under_min_line_is_not_proposed():
    cands = [_fund(1, "mutual_fund", 520, 0.5), _fund(2, "index", 480, 0.1)]
    args = (cands, {"mutual_fund": 47, "index": 53}, {"mutual_fund": 520, "index": 480})
    # 5 EUR move would be proposed without a minimum, but threshold is 0
    traspasos, _ = plan_traspasos(*args, 0.0, {}, {})
    assert [t["amount_eur"] for t in traspasos] == [50.0]
    traspasos, after = plan_traspasos(*args, 0.0, {}, {}, min_amount=100.0)
    assert traspasos == [] and after == {"mutual_fund": 520, "index": 480}


def test_buy_below_compares_in_the_stocks_own_currency():
    # 90 USD is 81 EUR: under 85 in EUR terms, above 85 USD
    usd = _cand(
        1,
        "stock",
        buy_below=85.0,
        price_native=90.0,
        price_eur=81.0,
        currency="USD",
        weight_pct=1,
    )
    eur = _cand(2, "stock", buy_below=12.0, weight_pct=2)
    ranked, _, _ = allocator.rank_stock_candidates([usd, eur], 10000, 5.0)
    assert [c["asset_id"] for c, _ in ranked] == [2, 1]
    assert not any("buy-below" in r for r in ranked[1][1])
    cheap = {**usd, "price_native": 80.0, "price_eur": 72.0}
    ranked, _, _ = allocator.rank_stock_candidates([cheap], 10000, 5.0)
    assert "price 80.00 USD at or below your buy-below of 85.00 USD" in ranked[0][1][0]
    assert allocator._why_not("stock", eur, usd) == "price above your buy-below"


def test_sell_rated_and_expensive_hold_stocks_are_excluded():
    sell = _cand(1, "stock", recommendation="sell", buy_below=20.0)
    hold_high = _cand(2, "stock", recommendation="HOLD", buy_below=5.0)
    hold_low = _cand(3, "stock", recommendation="HOLD", buy_below=20.0)
    ranked, _, excl = allocator.rank_stock_candidates(
        [sell, hold_high, hold_low], 10000, 5.0
    )
    assert [c["asset_id"] for c, _ in ranked] == [3]
    assert {c["asset_id"]: r for c, r in excl} == {
        1: "your research says SELL",
        2: "HOLD and above your buy-below",
    }


def test_fund_typed_stock_is_ranked_fund_style_and_exempt_from_caps():
    # a fund typed "stock": 6 % of the portfolio, no research, would break
    # both the single-stock cap and needs_research if treated as a stock
    fund = _cand(1, "stock", is_fund=True, ter_pct=0.2, value_eur=600)
    stock = _cand(2, "stock", buy_below=20.0, weight_pct=1)
    plain = _cand(3, "stock")
    lines, left, needs, _ = allocator.pick_lines(
        "stock", [fund, stock, plain], 400.0, _PICK_SETTINGS, {"total_after": 10000}
    )
    assert {ln["symbol"] for ln in lines} == {"S1", "S2"}
    assert left == 0
    assert [n["symbol"] for n in needs] == ["S3"]
    # the fund-style line is not clipped by the 5 % cap
    only_fund, left, needs, _ = allocator.pick_lines(
        "stock", [fund], 1000.0, _PICK_SETTINGS, {"total_after": 10000}
    )
    assert [ln["amount_eur"] for ln in only_fund] == [1000.0] and left == 0
    assert needs == []


def test_bucket_table_before_pct_is_measured_before_the_traspasos(db, stub_deployable):
    stub_deployable()
    db.set_allocation_target("mutual_fund", 20)
    db.set_allocation_target("index", 80)
    broker = db.get_or_create_portfolio("Example Broker")
    _buy(db, broker, "MFA", "Fund A", "mutual_fund", 8, 100, ter=0.5)
    _buy(db, broker, "IDXA", "Index Fund A", "index", 1, 100, ter=0.1)
    res = allocator.next_euro(db, 100.0, TODAY, _fx, _fx_on)
    assert [t["amount_eur"] for t in res["traspasos"]] == [620.0]
    rows = {b["asset_type"]: b for b in res["buckets"]}
    # now: 800 of 900 is mutual_fund; after the switch and 100 new: 180 of 1000
    assert rows["mutual_fund"]["before_pct"] == pytest.approx(800 / 900 * 100)
    assert rows["mutual_fund"]["after_pct"] == pytest.approx(18.0)
    assert rows["index"]["before_pct"] == pytest.approx(100 / 900 * 100)


def test_wash_unavailable_warns_funds_are_not_checked(db, monkeypatch):
    db.set_allocation_target("etf", 100)
    broker = db.get_or_create_portfolio("Example Broker")
    _buy(db, broker, "ETFX", "Example Fund ETF", "etf", 1, 100, ter=0.1)

    def boom(*a, **k):
        raise WashSaleUnavailable("tax report failed")

    monkeypatch.setattr(allocator, "blocked_assets", boom)
    holdings, txns = _state(db)
    _, warnings = build_candidates(db, holdings, txns, load_settings(db), TODAY, _fx)
    assert "stocks are not proposed" in warnings[0]
    assert "funds and ETFs are proposed without checking" in warnings[0]
