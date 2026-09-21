"""Unit tests for the pure/DB-light helpers in
``portf_manager/services/rebalance_planner.py``.

These go straight at the functions — no HTTP layer — because they encode
rules the API shape can't show: that an override *replaces* a stored target
rather than adding to it, where exactly the target-sum tolerance band ends,
which gaps ``compute_gaps`` drops, and — for trade generation — which of two
equally-overweight positions each strategy actually reaches for.

All data is invented (plain asset-type strings, made-up symbols, round
numbers). Everything is priced in EUR so no test can reach a live FX quote —
except ``TestPerLotHistoricalFx``, which stubs both rate lookups outright.
"""

from datetime import date

import pytest

from portf_manager import market
from portf_manager.services.rebalance_planner import (
    NO_CASH_WARNING,
    allocate_buy_trades,
    allocate_trade_taxes,
    build_holdings,
    build_plan,
    build_sell_candidates,
    compute_after_drift,
    compute_before_state,
    compute_gaps,
    fifo_gain_eur,
    merge_targets,
    plan_sale,
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
        for symbol, atype, qty, buy_price, price in holdings:
            self.add_holding(symbol, atype, price)
            self.add_buy(symbol, qty, buy_price, "2024-03-01")

    # ── fixture builders ──
    def add_holding(self, symbol, asset_type, latest_price, currency="EUR"):
        """Register an asset with a latest price but no transactions yet."""
        asset_id = len(self._assets) + 1
        self._assets[asset_id] = {
            "id": asset_id,
            "symbol": symbol,
            "name": f"Example {symbol}",
            "asset_type": asset_type,
            "currency": currency,
        }
        self._prices[asset_id] = latest_price
        return asset_id

    def add_buy(self, symbol, quantity, price, transaction_date):
        """Add one more FIFO lot — the only way to get a multi-lot position."""
        asset = self.get_asset_by_symbol(symbol)
        self._transactions.append(
            {
                "id": len(self._transactions) + 1,
                "asset_id": asset["id"],
                "symbol": symbol,
                "portfolio_id": self.portfolio_id,
                "transaction_type": "buy",
                "quantity": quantity,
                "price": price,
                "total_amount": quantity * price,
                "fees": 0,
                "currency": asset["currency"],
                "transaction_date": transaction_date,
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


class TestUnspentCashBudgetWarning:
    """Review finding: ``allocate_buy_trades`` sizes each buy to Step B's gap
    (computed *before* ``cash_budget_eur`` is added to the pool), so a budget
    bigger than the total gap used to leave money unspent with no warning at
    all — a €50,000 budget against a €3,000 gap silently "lost" €47,000.
    """

    def test_warns_when_the_budget_is_not_fully_needed(self):
        db = _PlannerDB(THREE_TYPE_TARGETS, THREE_TYPE_HOLDINGS)
        result = build_plan(db, allow_sells=False, cash_budget_eur=50_000.0)
        for plan in result["plans"]:
            assert _sides(plan) == [("BUY", "EXST", 3000.0)]
            assert any(
                "€47,000.00 of the cash budget was not needed to reach "
                "target and was not spent." in w
                for w in plan["warnings"]
            ), plan["warnings"]

    def test_no_warning_when_the_budget_is_fully_spent(self):
        db = _PlannerDB(THREE_TYPE_TARGETS, THREE_TYPE_HOLDINGS)
        # The stock gap is exactly €3,000 — there is nothing left over.
        result = build_plan(db, allow_sells=False, cash_budget_eur=3000.0)
        for plan in result["plans"]:
            assert not any("was not needed" in w for w in plan["warnings"])

    def test_no_warning_when_there_is_no_cash_budget(self):
        db = _PlannerDB(THREE_TYPE_TARGETS, THREE_TYPE_HOLDINGS)
        # Sells alone fund the buys almost exactly — no budget, no warning.
        result = build_plan(db)
        for plan in result["plans"]:
            assert not any("was not needed" in w for w in plan["warnings"])


class TestWholeTypeLockedOrExcludedWarns:
    """Review finding: ``build_sell_candidates`` silently ``continue``s past a
    locked/excluded symbol with no record kept, so a type where *every* held
    symbol is filtered out produces zero sell candidates with nothing
    explaining why — the only downstream symptom is an unrelated "no cash to
    fund X" warning on some other, underweight, type.
    """

    def test_locking_every_symbol_in_an_overweight_type_warns(self):
        db = _PlannerDB({"stock": 50.0, "etf": 50.0}, TWO_ETF_HOLDINGS)
        result = build_plan(db, locked_symbols=["EXETFA", "EXETFZ"])
        for plan in result["plans"]:
            assert all(t["asset_type"] != "etf" for t in plan["trades"])
            assert (
                "etf is overweight but every held position in it is locked "
                "or excluded — nothing to sell." in plan["warnings"]
            )

    def test_excluding_every_symbol_in_an_overweight_type_warns(self):
        db = _PlannerDB({"stock": 50.0, "etf": 50.0}, TWO_ETF_HOLDINGS)
        result = build_plan(db, excluded_symbols=["EXETFA", "EXETFZ"])
        for plan in result["plans"]:
            assert all(t["asset_type"] != "etf" for t in plan["trades"])
            assert (
                "etf is overweight but every held position in it is locked "
                "or excluded — nothing to sell." in plan["warnings"]
            )

    def test_only_partially_locking_a_type_does_not_warn(self):
        """One eligible symbol (EXETFZ) remains, so this is not the
        whole-type case — already covered by TestExcludedAndLockedSymbols."""
        db = _PlannerDB({"stock": 50.0, "etf": 50.0}, TWO_ETF_HOLDINGS)
        result = build_plan(db, locked_symbols=["EXETFA"])
        for plan in result["plans"]:
            assert not any("nothing to sell" in w for w in plan["warnings"])


class TestSellReasonUsesRemainingGapNotOriginal:
    """Review finding: ``_sell_reason``'s quoted gap used to read the type's
    original Step B gap even for a *second* sell into an already-partially-
    reduced type, instead of what was actually still open when that specific
    trade was generated.
    """

    TARGETS = {"stock": 80.0, "etf": 20.0}

    def test_second_sell_into_the_same_type_quotes_the_remaining_gap(self):
        db = _PlannerDB(self.TARGETS, TWO_ETF_HOLDINGS)
        # etf is €4,000 overweight; both candidates tie on gap so
        # closest_to_target falls to the symbol tie-break (EXETFA first).
        plan = _plan_for(build_plan(db), "closest_to_target")
        sells = [t for t in plan["trades"] if t["side"] == "SELL"]
        assert [t["symbol"] for t in sells] == ["EXETFA", "EXETFZ"]

        # First sell: nothing sold yet, so the remaining gap still equals
        # the type's original €4,000 overweight.
        assert "€4,000 to cut" in sells[0]["reason"]

        # Second sell: the first trade already closed €3,000 of the €4,000
        # gap, so only €1,000 remained when this trade was generated — not
        # the stale original €4,000.
        assert "€1,000 to cut" in sells[1]["reason"]
        assert "€4,000 to cut" not in sells[1]["reason"]


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

    def test_compute_before_state_returns_the_same_holdings_build_holdings_would(self):
        """Task 5 perf fix regression guard: ``compute_before_state`` now
        hands back the ``holdings``/``transactions`` it already computed
        internally instead of ``build_plan`` re-fetching transactions and
        calling ``build_holdings`` a second time — this pins that what it
        returns is identical to a direct ``build_holdings(db, transactions)``
        call over the same transactions, not a different (and possibly
        cheaper-but-wrong) shortcut."""
        db = _PlannerDB(THREE_TYPE_TARGETS, THREE_TYPE_HOLDINGS)
        before, warnings, holdings, transactions = compute_before_state(db, None, None)
        expected_holdings, expected_warnings = build_holdings(
            db, db.get_all_transactions()
        )
        assert holdings == expected_holdings
        assert warnings == expected_warnings
        assert transactions == db.get_all_transactions()


class TestPureScoringHelpers:
    def _candidate(self, symbol, planned_gain_ratio, gap_eur):
        """``planned_gain_ratio`` — the gain per euro of the sale this
        candidate would actually produce, which is what ranking reads (never
        the whole position's average)."""
        return {
            "symbol": symbol,
            "planned_gain_ratio": planned_gain_ratio,
            "gap_eur": gap_eur,
        }

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


class TestRankingUsesTheTradeThatWouldActuallyHappen:
    """Regression: ``tax_minimal`` must rank on the FIFO gain of the quantity
    it would really sell, not on the whole position's average gain ratio.

    Fixture is the counterexample that exposed the bug. Both ETFs are worth
    €1,000, sit in the same €500-overweight type, and trade at €10:

    - ``EXETFZ`` holds two very unevenly-priced lots — 50 @ €9 bought first,
      then 50 @ €1. Averaged over the whole position that is a 50% gain, the
      *worse* of the two on paper.
    - ``EXETFA`` holds one flat lot of 100 @ €6 — a 40% gain averaged, the
      better-looking one.

    But only €500 is sold, and FIFO eats ``EXETFZ``'s expensive €9 lot first:
    that sale realises €50 (10%), against €200 (40%) for ``EXETFA``. Ranking
    on the position average picks ``EXETFA`` and lands the trade that is four
    times more taxed.
    """

    TARGETS = {"stock": 62.5, "etf": 37.5}

    def _db(self):
        # €2,000 of stock + €2,000 of etf = €4,000; etf's 37.5% target is
        # €1,500, so the etf overweight is exactly €500.
        db = _PlannerDB(self.TARGETS, [("EXST", "stock", 2000, 1.0, 1.0)])
        db.add_holding("EXETFA", "etf", 10.0)
        db.add_buy("EXETFA", 100, 6.0, "2023-01-10")
        db.add_holding("EXETFZ", "etf", 10.0)
        db.add_buy("EXETFZ", 50, 9.0, "2023-01-10")
        db.add_buy("EXETFZ", 50, 1.0, "2024-06-10")
        return db

    def _candidates(self, db):
        before, _, holdings, transactions = compute_before_state(db, None, None)
        gaps = compute_gaps(before["allocations"], 100.0)
        candidates, _ = build_sell_candidates(
            db,
            holdings,
            gaps,
            transactions=transactions,
        )
        return {c["symbol"]: c for c in candidates}

    def test_the_two_ratios_genuinely_disagree(self):
        """Pins the fixture itself: whole-position and actually-traded ratios
        rank these two candidates in *opposite* orders. Without this the rest
        of the class could pass against a fixture that proves nothing."""
        by_symbol = self._candidates(self._db())
        whole = {s: c["gain_eur"] / c["proceeds_eur"] for s, c in by_symbol.items()}
        planned = {s: c["planned_gain_ratio"] for s, c in by_symbol.items()}

        # On the whole position EXETFZ looks worse...
        assert whole["EXETFZ"] == pytest.approx(0.50)
        assert whole["EXETFA"] == pytest.approx(0.40)
        # ...but the €500 that actually gets sold tells the opposite story.
        assert planned["EXETFZ"] == pytest.approx(0.10)
        assert planned["EXETFA"] == pytest.approx(0.40)

    def test_tax_minimal_picks_the_genuinely_cheaper_trade(self):
        plan = _plan_for(build_plan(self._db()), "tax_minimal")
        sell = plan["trades"][0]
        assert (sell["symbol"], sell["amount_eur"]) == ("EXETFZ", 500.0)
        assert sell["estimated_gain_eur"] == 50.0

    def test_the_alternative_trade_really_is_more_taxed(self):
        """closest_to_target ties on gap and falls to the symbol tie-break,
        so it takes EXETFA — the same €500 sold, four times the gain."""
        result = build_plan(self._db())
        cheap = _plan_for(result, "tax_minimal")
        other = _plan_for(result, "closest_to_target")
        assert other["trades"][0]["symbol"] == "EXETFA"
        assert other["trades"][0]["estimated_gain_eur"] == 200.0
        assert cheap["summary"]["estimated_realized_gain_eur"] == 50.0
        assert cheap["summary"]["estimated_tax_delta_eur"] < (
            other["summary"]["estimated_tax_delta_eur"]
        )

    def test_the_reason_quotes_this_trade_s_own_gain_not_the_position_s(self):
        """The reason used to print the whole-position ratio, so a 10% trade
        could be described as a 50% one."""
        plan = _plan_for(build_plan(self._db()), "tax_minimal")
        sell = plan["trades"][0]
        actual_pct = sell["estimated_gain_eur"] / sell["amount_eur"] * 100
        assert actual_pct == pytest.approx(10.0)
        assert f"({actual_pct:.1f}%)" in sell["reason"]
        # The position average must not appear.
        assert "50.0%" not in sell["reason"]

    def test_balanced_also_reads_the_traded_ratio(self):
        """balanced blends the same tax signal, so it moves with the fix."""
        db = self._db()
        by_symbol = self._candidates(db)
        order = [
            c["symbol"]
            for c in rank_sell_candidates(list(by_symbol.values()), "balanced")
        ]
        # Both share one type, so the drift axis is degenerate and scores 0
        # for both; the traded-gain axis alone decides.
        assert order == ["EXETFZ", "EXETFA"]


class TestPerLotHistoricalFx:
    """The FX convention the brief called non-negotiable: proceeds at today's
    rate, each lot's cost basis at the rate on *its own* purchase date.

    Every other fixture here is EUR-only (deliberately, so no test can reach a
    live quote), which left this rule unpinned. This one stubs both rate
    lookups with distinct values per date, so a uniform-rate regression shows
    up as a different number rather than passing silently.
    """

    # USD→EUR: today, and on each of the two purchase dates.
    RATE_NOW = 0.90
    RATES_ON = {date(2023, 1, 10): 0.50, date(2024, 6, 10): 0.80}

    @pytest.fixture
    def db(self, monkeypatch):
        monkeypatch.setattr(
            market,
            "get_fx_eur",
            lambda db, currency, max_age=3600: (
                (self.RATE_NOW, False) if currency.upper() == "USD" else (1.0, False)
            ),
        )
        monkeypatch.setattr(
            market,
            "get_fx_eur_on",
            lambda db, currency, on_date: (
                (self.RATES_ON[on_date], False)
                if currency.upper() == "USD"
                else (1.0, False)
            ),
        )
        # €1,200 of EUR stock + a USD ETF worth €3,600 = €4,800; a 50/50
        # target makes the etf €1,200 overweight.
        fake = _PlannerDB(
            {"stock": 50.0, "etf": 50.0}, [("EXST", "stock", 1200, 1.0, 1.0)]
        )
        fake.add_holding("EXUSD", "etf", 20.0, currency="USD")
        fake.add_buy("EXUSD", 40, 10.0, "2023-01-10")
        fake.add_buy("EXUSD", 160, 10.0, "2024-06-10")
        return fake

    def _candidate(self, db):
        before, _, holdings, transactions = compute_before_state(db, None, None)
        gaps = compute_gaps(before["allocations"], 100.0)
        candidates, _ = build_sell_candidates(
            db,
            holdings,
            gaps,
            transactions=transactions,
        )
        return next(c for c in candidates if c["symbol"] == "EXUSD")

    def test_each_lot_is_converted_at_its_own_purchase_date_rate(self, db):
        candidate = self._candidate(db)
        # Proceeds: 200 units × $20 × today's 0.90.
        assert candidate["price_eur"] == pytest.approx(18.0)
        assert candidate["proceeds_eur"] == pytest.approx(3600.0)
        # Cost: 40 × ($10 × 0.50) + 160 × ($10 × 0.80) = 200 + 1280.
        assert candidate["cost_basis_eur"] == pytest.approx(1480.0)
        # A single uniform rate would have produced either of these instead.
        assert candidate["cost_basis_eur"] != pytest.approx(200 * 10 * 0.50)
        assert candidate["cost_basis_eur"] != pytest.approx(200 * 10 * 0.80)

    def test_a_partial_sale_spanning_both_lots_uses_both_rates(self, db):
        """€1,200 is 66.67 units: all 40 of the 0.50-rate lot, then 26.67 of
        the 0.80-rate one. Cost 40×5 + 26.67×8 = €413.33, gain €786.67."""
        plan = _plan_for(build_plan(db), "tax_minimal")
        sell = plan["trades"][0]
        assert sell["symbol"] == "EXUSD"
        assert sell["amount_eur"] == 1200.0
        assert sell["quantity"] == pytest.approx(66.666667, abs=1e-5)
        assert sell["estimated_gain_eur"] == pytest.approx(786.67, abs=0.01)
        # Had one rate been applied to both lots the answer would have been
        # €666.67 (all at 0.80) or €866.67 (all at 0.50).
        assert sell["estimated_gain_eur"] != pytest.approx(666.67, abs=0.01)
        assert sell["estimated_gain_eur"] != pytest.approx(866.67, abs=0.01)


class TestZeroValueTradesAreNeverEmitted:
    """``min_trade_eur`` may be 0, at which point a type sitting exactly on
    target survives ``compute_gaps`` with ``gap_eur == 0.0`` — and a
    ``>= min_trade_eur`` guard alone lets a €0, zero-quantity trade through.
    """

    def test_a_portfolio_exactly_on_target_produces_no_trades_at_min_zero(self):
        db = _PlannerDB(
            {"stock": 50.0, "etf": 50.0},
            [
                ("EXST", "stock", 1000, 1.0, 1.0),
                ("EXETF", "etf", 1000, 1.0, 1.0),
            ],
        )
        result = build_plan(db, min_trade_eur=0.0)
        # The zero gaps do survive Step B at this threshold...
        assert compute_gaps(result["before"]["allocations"], 0.0) == [
            {"asset_type": "etf", "gap_eur": 0.0, "side": "SELL"},
            {"asset_type": "stock", "gap_eur": 0.0, "side": "SELL"},
        ]
        # ...but nothing degenerate reaches the response.
        for plan in result["plans"]:
            assert plan["trades"] == []

    def test_allocate_buy_trades_refuses_a_zero_gap(self):
        holdings = [
            {
                "asset_id": 1,
                "symbol": "EXST",
                "asset_type": "stock",
                "currency": "EUR",
                "quantity": 100.0,
                "price": 10.0,
                "price_eur": 10.0,
                "value_eur": 1000.0,
            }
        ]
        trades, warnings = allocate_buy_trades(
            holdings,
            [{"asset_type": "stock", "gap_eur": 0.0, "side": "BUY"}],
            1000.0,
            min_trade_eur=0.0,
        )
        assert trades == []
        assert warnings == []

    def test_allocate_buy_trades_says_so_when_the_cash_is_gone(self):
        holdings = [
            {
                "asset_id": 1,
                "symbol": "EXST",
                "asset_type": "stock",
                "currency": "EUR",
                "quantity": 100.0,
                "price": 10.0,
                "price_eur": 10.0,
                "value_eur": 1000.0,
            }
        ]
        trades, warnings = allocate_buy_trades(
            holdings,
            [{"asset_type": "stock", "gap_eur": 500.0, "side": "BUY"}],
            0.0,
            min_trade_eur=0.0,
        )
        assert trades == []
        assert warnings == ["No cash left to fund stock — €500.00 still needed."]


class TestPlanSale:
    """The one implementation both ranking and allocation call."""

    CANDIDATE = {
        "proceeds_eur": 1000.0,
        "price_eur": 10.0,
        "lots": [
            {"quantity": 50.0, "unit_cost_eur": 9.0},
            {"quantity": 50.0, "unit_cost_eur": 1.0},
        ],
    }

    def test_a_partial_sale_is_priced_off_the_oldest_lot(self):
        sale = plan_sale(self.CANDIDATE, 500.0)
        assert sale["amount_eur"] == pytest.approx(500.0)
        assert sale["quantity"] == pytest.approx(50.0)
        assert sale["gain_eur"] == pytest.approx(50.0)
        assert sale["gain_ratio"] == pytest.approx(0.10)

    def test_a_gap_larger_than_the_position_sells_all_of_it(self):
        sale = plan_sale(self.CANDIDATE, 99_000.0)
        assert sale["amount_eur"] == pytest.approx(1000.0)
        assert sale["gain_ratio"] == pytest.approx(0.50)

    def test_a_zero_or_negative_gap_is_a_zero_sale_not_a_crash(self):
        for gap in (0.0, -250.0):
            sale = plan_sale(self.CANDIDATE, gap)
            assert sale == {
                "amount_eur": 0.0,
                "quantity": 0.0,
                "gain_eur": 0.0,
                "gain_ratio": 0.0,
            }
