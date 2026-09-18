"""Unit tests for the pure/DB-light helpers in
``portf_manager/services/rebalance_planner.py``.

These go straight at the functions — no HTTP layer — because they encode
rules the API shape can't show: that an override *replaces* a stored target
rather than adding to it, where exactly the target-sum tolerance band ends,
which gaps ``compute_gaps`` drops, and — for trade generation — which of two
equally-overweight positions each strategy actually reaches for.

All data is invented (plain asset-type strings, made-up symbols, round
numbers). Everything is priced in EUR so no test can reach a live FX quote.
"""

import pytest

from portf_manager.services.rebalance_planner import (
    NO_CASH_WARNING,
    allocate_trade_taxes,
    build_holdings,
    build_plan,
    compute_after_drift,
    compute_gaps,
    fifo_gain_eur,
    merge_targets,
    rank_sell_candidates,
    truncate_trades,
    validate_target_sum,
)


class _FakeDB:
    """Minimal stand-in exposing only what ``merge_targets`` touches."""

    def __init__(self, targets):
        self._targets = targets

    def get_allocation_targets(self):
        return self._targets


class TestMergeTargets:
    def test_no_overrides_returns_stored_targets(self):
        db = _FakeDB(
            [
                {"asset_type": "stock", "target_pct": 60},
                {"asset_type": "etf", "target_pct": 40},
            ]
        )
        assert merge_targets(db, None) == {"stock": 60.0, "etf": 40.0}
        assert merge_targets(db, []) == {"stock": 60.0, "etf": 40.0}

    def test_override_replaces_and_never_adds(self):
        """The rule that matters: an override for "stock" is 50, not 60+50."""
        db = _FakeDB(
            [
                {"asset_type": "stock", "target_pct": 60},
                {"asset_type": "etf", "target_pct": 40},
            ]
        )
        merged = merge_targets(db, [{"asset_type": "stock", "target_pct": 50}])
        assert merged["stock"] == 50.0
        # Not summed...
        assert merged["stock"] != 110.0
        # ...and an asset type nobody overrode keeps its stored value.
        assert merged["etf"] == 40.0

    def test_override_can_introduce_a_new_asset_type(self):
        db = _FakeDB([{"asset_type": "stock", "target_pct": 100}])
        merged = merge_targets(db, [{"asset_type": "crypto", "target_pct": 5}])
        assert merged == {"stock": 100.0, "crypto": 5.0}

    def test_values_are_coerced_to_float(self):
        db = _FakeDB([{"asset_type": "stock", "target_pct": "60"}])
        merged = merge_targets(db, [{"asset_type": "etf", "target_pct": "40"}])
        assert merged == {"stock": 60.0, "etf": 40.0}


class TestValidateTargetSum:
    def test_exactly_100_passes(self):
        validate_target_sum({"stock": 60.0, "etf": 40.0})

    @pytest.mark.parametrize("total", [99.5, 100.5])
    def test_boundary_values_are_inclusive(self, total):
        """99.5 and 100.5 are inside the band, not outside it."""
        validate_target_sum({"stock": total})

    @pytest.mark.parametrize("total", [99.4, 100.6])
    def test_just_outside_the_band_raises(self, total):
        with pytest.raises(ValueError) as exc:
            validate_target_sum({"stock": total})
        assert "sum to ~100%" in str(exc.value)

    def test_empty_targets_raise(self):
        with pytest.raises(ValueError):
            validate_target_sum({})


class TestComputeGaps:
    """``gap_eur`` is ``drift_eur`` verbatim: positive = underweight (BUY),
    negative = overweight (SELL); ``abs(gap) < min_trade_eur`` is dropped.
    """

    ALLOCATIONS = [
        # Underweight by €500 → a BUY.
        {"asset_type": "stock", "drift_eur": 500.0, "drift_pct": -5.0},
        # Overweight by €500 → a SELL.
        {"asset_type": "etf", "drift_eur": -500.0, "drift_pct": 5.0},
        # Below any sane min_trade_eur → dropped.
        {"asset_type": "crypto", "drift_eur": 12.5, "drift_pct": 0.1},
    ]

    def test_sides_and_amounts(self):
        gaps = compute_gaps(self.ALLOCATIONS, min_trade_eur=100.0)
        assert gaps == [
            {"asset_type": "stock", "gap_eur": 500.0, "side": "BUY"},
            {"asset_type": "etf", "gap_eur": -500.0, "side": "SELL"},
        ]

    def test_small_gap_included_when_threshold_is_lower(self):
        gaps = compute_gaps(self.ALLOCATIONS, min_trade_eur=10.0)
        assert [g["asset_type"] for g in gaps] == ["stock", "etf", "crypto"]
        assert gaps[-1]["side"] == "BUY"

    def test_threshold_is_on_absolute_value_so_it_drops_sells_too(self):
        gaps = compute_gaps(self.ALLOCATIONS, min_trade_eur=600.0)
        assert gaps == []

    def test_gap_exactly_at_the_threshold_is_kept(self):
        """The check is ``abs(gap) < min_trade_eur``, so equality survives."""
        gaps = compute_gaps(
            [{"asset_type": "stock", "drift_eur": 100.0}], min_trade_eur=100.0
        )
        assert len(gaps) == 1

    def test_zero_min_trade_keeps_every_drifted_type(self):
        gaps = compute_gaps(self.ALLOCATIONS, min_trade_eur=0.0)
        assert len(gaps) == 3

    def test_empty_allocations(self):
        assert compute_gaps([], min_trade_eur=100.0) == []


# ── Trade generation (Steps C/D/E) ───────────────────────────────────────────


class _PlannerDB:
    """A whole fake portfolio: targets, assets, one buy lot each, latest prices.

    Only the handful of ``Database`` methods the planner (and, through it,
    ``TaxCalculator`` and ``current_year_savings_base``) actually calls.
    """

    def __init__(self, targets, holdings, portfolio_id=1):
        """Args:
        targets: ``{asset_type: target_pct}``.
        holdings: ``[(symbol, asset_type, quantity, buy_price, latest_price)]``
            — one ``buy`` transaction per symbol, so its whole open position
            is a single FIFO lot at ``buy_price``.
        """
        self._targets = targets
        self.portfolio_id = portfolio_id
        self._assets = {}
        self._prices = {}
        self._transactions = []
        for index, (symbol, atype, qty, buy_price, price) in enumerate(holdings, 1):
            self._assets[index] = {
                "id": index,
                "symbol": symbol,
                "name": f"Example {symbol}",
                "asset_type": atype,
                "currency": "EUR",
            }
            self._prices[index] = price
            self._transactions.append(
                {
                    "id": index,
                    "asset_id": index,
                    "symbol": symbol,
                    "portfolio_id": portfolio_id,
                    "transaction_type": "buy",
                    "quantity": qty,
                    "price": buy_price,
                    "total_amount": qty * buy_price,
                    "fees": 0,
                    "currency": "EUR",
                    "transaction_date": "2024-03-01",
                }
            )

    def get_allocation_targets(self):
        return [{"asset_type": t, "target_pct": p} for t, p in self._targets.items()]

    def get_all_transactions(self, limit=None, user_id=None, portfolio_id=None):
        return list(self._transactions)

    def get_transactions_by_portfolio(self, portfolio_id):
        return [t for t in self._transactions if t["portfolio_id"] == portfolio_id]

    def get_asset(self, asset_id):
        return self._assets.get(asset_id)

    def get_asset_by_symbol(self, symbol):
        for a in self._assets.values():
            if a["symbol"].upper() == symbol.upper():
                return a
        return None

    def get_latest_price(self, asset_id, price_type="close"):
        price = self._prices.get(asset_id)
        return {"price": price} if price is not None else None

    def get_portfolio(self, portfolio_id):
        return {"name": "Example Broker"}


def _sides(plan):
    return [(t["side"], t["symbol"], t["amount_eur"]) for t in plan["trades"]]


def _plan_for(result, strategy):
    return next(p for p in result["plans"] if p["strategy"] == strategy)


# Two equally-overweight ETFs whose gain ratios and alphabetical order pull in
# opposite directions: EXETFA is the expensive-to-sell one (50% gain) but sorts
# first, EXETFZ is the cheap one (10% gain) but sorts last. A strategy that
# ignored tax would reach for EXETFA.
TWO_ETF_HOLDINGS = [
    ("EXST", "stock", 400, 10.0, 10.0),  # €4,000, no gain
    ("EXETFA", "etf", 300, 5.0, 10.0),  # €3,000, +€1,500 (50%)
    ("EXETFZ", "etf", 300, 9.0, 10.0),  # €3,000, +€300 (10%)
]

# One underweight type and two overweight ones with *different* gaps: etf is
# €2,000 over (but expensive to sell), crypto only €1,000 over (and nearly
# free to sell). This is where tax_minimal and closest_to_target disagree.
THREE_TYPE_HOLDINGS = [
    ("EXST", "stock", 300, 10.0, 10.0),  # €3,000
    ("EXETF", "etf", 500, 5.0, 10.0),  # €5,000, +€2,500 (50%)
    ("EXCRY", "crypto", 200, 9.5, 10.0),  # €2,000, +€100 (5%)
]
THREE_TYPE_TARGETS = {"stock": 60.0, "etf": 30.0, "crypto": 10.0}


class TestBuyOnlyPlan:
    """Plan case 1 — ``allow_sells=False``."""

    def test_cash_budget_funds_buys_and_nothing_is_sold(self):
        db = _PlannerDB(THREE_TYPE_TARGETS, THREE_TYPE_HOLDINGS)
        result = build_plan(
            db, allow_sells=False, cash_budget_eur=2000.0, min_trade_eur=100.0
        )
        for plan in result["plans"]:
            assert _sides(plan) == [("BUY", "EXST", 2000.0)]
            assert plan["summary"]["sell_total_eur"] == 0.0
            assert plan["summary"]["estimated_realized_gain_eur"] == 0.0
            assert plan["summary"]["estimated_tax_delta_eur"] == 0.0
            # The stock gap is €3,000 and only €2,000 was available.
            assert any(
                "Insufficient cash to fully fund stock" in w for w in plan["warnings"]
            )

    def test_no_sells_and_no_budget_says_so(self):
        db = _PlannerDB(THREE_TYPE_TARGETS, THREE_TYPE_HOLDINGS)
        result = build_plan(db, allow_sells=False, cash_budget_eur=None)
        for plan in result["plans"]:
            assert plan["trades"] == []
            assert NO_CASH_WARNING in plan["warnings"]

    def test_budget_larger_than_the_gap_buys_only_the_gap(self):
        db = _PlannerDB(THREE_TYPE_TARGETS, THREE_TYPE_HOLDINGS)
        plan = _plan_for(
            build_plan(db, allow_sells=False, cash_budget_eur=99_000.0), "balanced"
        )
        assert _sides(plan) == [("BUY", "EXST", 3000.0)]


class TestTaxMinimalRanking:
    """Plan case 2 — the low-gain position is sold first."""

    def test_tax_minimal_prefers_the_low_gain_position(self):
        db = _PlannerDB({"stock": 50.0, "etf": 50.0}, TWO_ETF_HOLDINGS)
        plan = _plan_for(build_plan(db), "tax_minimal")
        assert _sides(plan) == [
            ("SELL", "EXETFZ", 1000.0),
            ("BUY", "EXST", 1000.0),
        ]
        # 100 units off a €9 lot sold at €10.
        assert plan["trades"][0]["estimated_gain_eur"] == 100.0

    def test_closest_to_target_breaks_an_equal_gap_tie_on_symbol(self):
        """Both ETFs share one type, so they share its gap — the tie-break is
        the symbol, which here means the *expensive* lot. That contrast is the
        point: tax_minimal's pick is a tax decision, not an ordering accident.
        """
        db = _PlannerDB({"stock": 50.0, "etf": 50.0}, TWO_ETF_HOLDINGS)
        plan = _plan_for(build_plan(db), "closest_to_target")
        assert _sides(plan) == [
            ("SELL", "EXETFA", 1000.0),
            ("BUY", "EXST", 1000.0),
        ]
        # 100 units off a €5 lot sold at €10 — five times tax_minimal's gain.
        assert plan["trades"][0]["estimated_gain_eur"] == 500.0

    def test_tax_minimal_realises_less_gain_than_closest_to_target(self):
        db = _PlannerDB({"stock": 50.0, "etf": 50.0}, TWO_ETF_HOLDINGS)
        result = build_plan(db)
        cheap = _plan_for(result, "tax_minimal")["summary"]
        costly = _plan_for(result, "closest_to_target")["summary"]
        assert (
            cheap["estimated_realized_gain_eur"] < costly["estimated_realized_gain_eur"]
        )
        assert cheap["estimated_tax_delta_eur"] < costly["estimated_tax_delta_eur"]
        # Same rebalance either way — only the tax bill differs.
        assert cheap["max_abs_drift_pct_after"] == costly["max_abs_drift_pct_after"]


class TestClosestToTargetReducesDriftMore:
    """Plan case 3 — with only one trade to spend, drift-first beats tax-first
    at closing drift (and that is exactly the trade-off the two strategies
    exist to show)."""

    def test_max_drift_after_is_lower_for_closest_to_target(self):
        db = _PlannerDB(THREE_TYPE_TARGETS, THREE_TYPE_HOLDINGS)
        result = build_plan(db, max_trades=1)

        tax_plan = _plan_for(result, "tax_minimal")
        drift_plan = _plan_for(result, "closest_to_target")

        # tax_minimal spends its one trade on the cheap-but-small crypto
        # overweight; closest_to_target spends it on the big etf one.
        assert _sides(tax_plan) == [("SELL", "EXCRY", 1000.0)]
        assert _sides(drift_plan) == [("SELL", "EXETF", 2000.0)]

        before_worst = max(abs(a["drift_pct"]) for a in result["before"]["allocations"])
        tax_after = tax_plan["summary"]["max_abs_drift_pct_after"]
        drift_after = drift_plan["summary"]["max_abs_drift_pct_after"]
        assert drift_after < tax_after < before_worst


class TestExcludedAndLockedSymbols:
    """Plan case 4."""

    def test_locked_symbol_is_never_sold_but_can_still_be_bought(self):
        db = _PlannerDB({"stock": 50.0, "etf": 50.0}, TWO_ETF_HOLDINGS)
        # EXETFA is what closest_to_target would otherwise sell; EXST is the
        # buy target.
        plan = _plan_for(
            build_plan(db, locked_symbols=["EXETFA", "EXST"]), "closest_to_target"
        )
        assert _sides(plan) == [
            ("SELL", "EXETFZ", 1000.0),
            ("BUY", "EXST", 1000.0),
        ]

    def test_excluded_symbol_appears_on_neither_side(self):
        db = _PlannerDB({"stock": 50.0, "etf": 50.0}, TWO_ETF_HOLDINGS)
        result = build_plan(db, excluded_symbols=["exetfa"])
        for plan in result["plans"]:
            assert all(t["symbol"] != "EXETFA" for t in plan["trades"])
            assert _sides(plan) == [
                ("SELL", "EXETFZ", 1000.0),
                ("BUY", "EXST", 1000.0),
            ]

    def test_excluding_the_only_buy_target_warns_and_skips_that_gap(self):
        db = _PlannerDB({"stock": 50.0, "etf": 50.0}, TWO_ETF_HOLDINGS)
        plan = _plan_for(build_plan(db, excluded_symbols=["EXST"]), "tax_minimal")
        assert [t["side"] for t in plan["trades"]] == ["SELL"]
        assert "No eligible held symbol to buy for stock — skipped." in plan["warnings"]


class TestMaxTradesTruncation:
    """Plan case 5."""

    def test_truncation_is_deterministic_and_warns(self):
        first = build_plan(
            _PlannerDB(THREE_TYPE_TARGETS, THREE_TYPE_HOLDINGS), max_trades=2
        )
        second = build_plan(
            _PlannerDB(THREE_TYPE_TARGETS, THREE_TYPE_HOLDINGS), max_trades=2
        )

        for strategy in ("tax_minimal", "closest_to_target", "balanced"):
            a = _plan_for(first, strategy)
            b = _plan_for(second, strategy)
            assert _sides(a) == _sides(b)
            assert a["summary"] == b["summary"]
            assert len(a["trades"]) == 2
            assert any("Plan truncated to 2 trades" in w for w in a["warnings"]), a[
                "warnings"
            ]

    def test_sells_survive_truncation_before_buys(self):
        """Sells fund buys, so a truncated plan keeps the funding side."""
        plan = _plan_for(
            build_plan(
                _PlannerDB(THREE_TYPE_TARGETS, THREE_TYPE_HOLDINGS), max_trades=2
            ),
            "closest_to_target",
        )
        assert [t["side"] for t in plan["trades"]] == ["SELL", "SELL"]

    def test_no_warning_when_nothing_is_truncated(self):
        plan = _plan_for(
            build_plan(
                _PlannerDB(THREE_TYPE_TARGETS, THREE_TYPE_HOLDINGS), max_trades=12
            ),
            "balanced",
        )
        assert not any("truncated" in w for w in plan["warnings"])


class TestMaxSellGainCap:
    """Plan case 6."""

    TARGETS = {"stock": 80.0, "etf": 20.0}

    def test_cap_stops_further_sells_and_warns(self):
        db = _PlannerDB(self.TARGETS, TWO_ETF_HOLDINGS)
        uncapped = _plan_for(build_plan(db), "tax_minimal")
        # Without a cap both ETFs are sold to close the €4,000 etf overweight.
        assert [t["symbol"] for t in uncapped["trades"] if t["side"] == "SELL"] == [
            "EXETFZ",
            "EXETFA",
        ]
        assert uncapped["summary"]["estimated_realized_gain_eur"] == 800.0

        capped = _plan_for(build_plan(db, max_sell_gain_eur=400.0), "tax_minimal")
        sells = [t for t in capped["trades"] if t["side"] == "SELL"]
        assert [t["symbol"] for t in sells] == ["EXETFZ"]
        assert capped["summary"]["estimated_realized_gain_eur"] == 300.0
        assert any("Sell gain cap (€400.00) reached" in w for w in capped["warnings"])

    def test_no_sell_pushes_the_running_total_over_the_cap(self):
        db = _PlannerDB(self.TARGETS, TWO_ETF_HOLDINGS)
        for cap in (0.0, 100.0, 300.0, 799.0, 800.0):
            plan = _plan_for(build_plan(db, max_sell_gain_eur=cap), "tax_minimal")
            running = 0.0
            for trade in plan["trades"]:
                if trade["side"] != "SELL":
                    continue
                running += trade["estimated_gain_eur"]
                assert running <= cap + 1e-9, (cap, plan["trades"])


class TestPlanEdgeCases:
    def test_portfolio_already_on_target_produces_no_trades(self):
        """Nothing overweight is an empty sell list, not an error."""
        db = _PlannerDB({"stock": 100.0}, [("EXST", "stock", 100, 10.0, 10.0)])
        result = build_plan(db, allow_sells=True)
        for plan in result["plans"]:
            assert plan["trades"] == []
            assert plan["summary"]["max_abs_drift_pct_after"] == 0.0

    def test_holdings_and_before_state_agree_per_type(self):
        """The per-symbol rows the trades are built from must add up to the
        type totals ``before`` reports — they come from one walk of the data
        (``build_holdings``) and this is the guard that keeps it that way."""
        db = _PlannerDB(THREE_TYPE_TARGETS, THREE_TYPE_HOLDINGS)
        holdings, _ = build_holdings(db, db.get_all_transactions())
        by_type = {}
        for h in holdings:
            by_type[h["asset_type"]] = (
                by_type.get(h["asset_type"], 0.0) + h["value_eur"]
            )

        result = build_plan(db)
        for a in result["before"]["allocations"]:
            assert round(by_type.get(a["asset_type"], 0.0), 2) == a["current_value_eur"]

    def test_unpriced_holding_is_never_traded(self):
        db = _PlannerDB({"stock": 50.0, "etf": 50.0}, TWO_ETF_HOLDINGS)
        db._prices[2] = None  # EXETFA has no price row
        plan = _plan_for(build_plan(db), "closest_to_target")
        assert all(t["symbol"] != "EXETFA" for t in plan["trades"])
        assert any("No price data for EXETFA" in w for w in plan["warnings"])


class TestPureScoringHelpers:
    def _candidate(self, symbol, gain_ratio, gap_eur):
        return {"symbol": symbol, "gain_ratio": gain_ratio, "gap_eur": gap_eur}

    def test_balanced_blends_both_signals(self):
        """Neither extreme wins: the middle candidate, decent on both axes,
        outranks the one that is best on a single axis and worst on the other.
        """
        candidates = [
            # Cheapest to sell, but its type barely drifted.
            self._candidate("A", 0.00, -100.0),
            # Middling on both.
            self._candidate("B", 0.20, -800.0),
            # Biggest gap, but the most expensive gain.
            self._candidate("C", 1.00, -1000.0),
        ]
        order = [c["symbol"] for c in rank_sell_candidates(candidates, "balanced")]
        assert order[0] == "B"

    def test_balanced_survives_an_all_equal_axis(self):
        candidates = [
            self._candidate("A", 0.10, -500.0),
            self._candidate("B", 0.10, -500.0),
        ]
        order = [c["symbol"] for c in rank_sell_candidates(candidates, "balanced")]
        assert order == ["A", "B"]

    def test_tax_minimal_puts_losses_first(self):
        candidates = [
            self._candidate("GAINER", 0.40, -500.0),
            self._candidate("LOSER", -0.15, -500.0),
        ]
        order = [c["symbol"] for c in rank_sell_candidates(candidates, "tax_minimal")]
        assert order == ["LOSER", "GAINER"]

    def test_empty_candidate_list(self):
        for strategy in ("tax_minimal", "closest_to_target", "balanced"):
            assert rank_sell_candidates([], strategy) == []


class TestFifoGainEur:
    LOTS = [
        {"quantity": 10.0, "unit_cost_eur": 100.0},
        {"quantity": 10.0, "unit_cost_eur": 120.0},
    ]

    def test_partial_sale_consumes_the_oldest_lot_first(self):
        # 15 @ 150 = 2250 proceeds; cost 10*100 + 5*120 = 1600.
        assert fifo_gain_eur(self.LOTS, 15.0, 150.0) == pytest.approx(650.0)

    def test_a_smaller_sale_never_touches_the_second_lot(self):
        assert fifo_gain_eur(self.LOTS, 4.0, 150.0) == pytest.approx(200.0)

    def test_a_loss_is_negative(self):
        assert fifo_gain_eur(self.LOTS, 10.0, 80.0) == pytest.approx(-200.0)


class TestTruncateAndTaxAllocation:
    def test_truncate_keeps_order_and_reports_the_drop(self):
        trades = [{"i": i} for i in range(5)]
        kept, warnings = truncate_trades(trades, 2)
        assert kept == trades[:2]
        assert warnings == ["Plan truncated to 2 trades; 3 more were generated."]

    def test_truncate_is_a_no_op_under_the_limit(self):
        trades = [{"i": 0}]
        assert truncate_trades(trades, 12) == (trades, [])

    def test_tax_is_apportioned_by_each_trade_s_share_of_the_gain(self):
        sells = [
            {"estimated_gain_eur": 300.0, "estimated_tax_eur": 0.0},
            {"estimated_gain_eur": 100.0, "estimated_tax_eur": 0.0},
        ]
        allocate_trade_taxes(sells, 80.0)
        assert [t["estimated_tax_eur"] for t in sells] == [60.0, 20.0]

    def test_no_tax_is_apportioned_when_the_plan_realises_no_gain(self):
        sells = [{"estimated_gain_eur": -50.0, "estimated_tax_eur": 1.0}]
        allocate_trade_taxes(sells, -9.5)
        assert sells[0]["estimated_tax_eur"] == 0.0


class TestComputeAfterDrift:
    ALLOCATIONS = [
        {"asset_type": "stock", "current_value_eur": 4000.0, "target_pct": 60.0},
        {"asset_type": "etf", "current_value_eur": 6000.0, "target_pct": 40.0},
    ]

    def test_a_full_rebalance_lands_on_target(self):
        trades = [
            {"asset_type": "etf", "side": "SELL", "amount_eur": 2000.0},
            {"asset_type": "stock", "side": "BUY", "amount_eur": 2000.0},
        ]
        assert compute_after_drift(self.ALLOCATIONS, trades) == 0.0

    def test_no_trades_leaves_the_current_drift(self):
        assert compute_after_drift(self.ALLOCATIONS, []) == 20.0

    def test_unspent_proceeds_leave_the_invested_total(self):
        """Selling without reinvesting shrinks the base the percentages are
        measured against — the remaining type's weight goes *up*."""
        trades = [{"asset_type": "etf", "side": "SELL", "amount_eur": 2000.0}]
        # stock 4000 / etf 4000 of 8000 → 50/50 vs a 60/40 target.
        assert compute_after_drift(self.ALLOCATIONS, trades) == 10.0
