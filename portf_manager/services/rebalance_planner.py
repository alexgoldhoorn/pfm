"""
Tax-aware rebalance planner — Steps A/B: current state + gap analysis.

This module answers "where do I stand versus target, and how big is each
gap" for ``POST /api/v1/rebalance/plan``. Trade generation itself — Step C
(FIFO-lot tax-estimated sell ranking), Step D (buy assignment) and Step E
(constraint enforcement: ``excluded_symbols``/``locked_symbols``/
``max_trades``/``max_sell_gain_eur``/``allow_sells``) — is Task 3's job.
Every strategy this module's ``build_plan`` returns has ``trades: []`` and a
zeroed summary (see ``_stub_plan``); only ``before`` and
``summary.max_abs_drift_pct_after`` are computed from real DB data, since
the latter needs only the current allocation, not any trades.

The drift it does report is only as good as the prices behind it, so a
holding with no price row (valued at €0) or one converted at a stale FX rate
is named in ``warnings`` rather than quietly understating its own asset type
and inflating everyone else's drift.
"""

from typing import Dict, List, Optional, Tuple

from portf_manager import market
from portf_manager.positions import compute_positions

# The three strategies the planner will eventually differentiate (Task 3).
# All three are always rendered, even though none of them differ yet.
STRATEGIES = ("tax_minimal", "closest_to_target", "balanced")

STUB_WARNING = (
    "Trade generation not yet implemented for this strategy — showing drift only."
)

# The top-level (whole-response) counterpart of STUB_WARNING. A client that
# only reads `warnings` must not see a clean response while every plan is a
# stub, so the notice is stated once at the top rather than repeated per plan.
STUB_PLAN_NOTICE = (
    "Trade generation not yet implemented — all strategies show drift only."
)


def _to_eur_converter(db, warnings: List[str]):
    """Same FX-conversion helper as ``get_rebalance_analysis``
    (``portf_server/routers/rebalance.py``) — duplicated rather than shared
    since it's ~10 lines and the two call sites don't need to stay in
    lockstep beyond the pattern itself.

    Unlike that endpoint's copy, a **stale** rate (``market.get_fx_eur``'s
    second return value: a cached or hard-coded fallback, not a live quote)
    appends a warning instead of being discarded — a silently mispriced
    holding understates its own asset type and inflates every other type's
    apparent drift, which is exactly the kind of wrong number a planner must
    not present as clean. One warning per currency, not per position.
    """
    fx_cache: Dict[str, float] = {}

    def to_eur(amount: float, currency: str) -> float:
        if currency == "EUR" or amount == 0:
            return amount
        if currency not in fx_cache:
            rate, stale = market.get_fx_eur(db, currency, max_age=1800)
            fx_cache[currency] = rate
            if stale:
                warnings.append(
                    f"Stale FX rate for {currency} — EUR values for "
                    f"{currency}-priced holdings are approximate."
                )
        return amount * fx_cache[currency]

    return to_eur


def _current_type_values(
    db, transactions: List[dict]
) -> Tuple[Dict[str, float], List[str]]:
    """Per-asset-type EUR value of currently open positions, plus any
    data-quality warnings raised while pricing them.

    Positions come from the shared ``compute_positions`` (handles stock
    splits) rather than an inline buy/sell loop — Step A explicitly calls for
    the shared helper, and ``get_rebalance_analysis`` was moved onto the same
    helper so the two endpoints can't disagree about what "current
    allocation" means. The per-type aggregation that follows mirrors that
    endpoint exactly.

    A held position with no price row is valued at 0 (as it always was) but
    now says so: the totals stay usable while naming what they understate.
    """
    positions, _realised = compute_positions(transactions)
    warnings: List[str] = []
    to_eur = _to_eur_converter(db, warnings)

    type_values: Dict[str, float] = {}
    for asset_id, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(asset_id)
        if not asset:
            continue
        atype = asset.get("asset_type", "other")
        price_data = db.get_latest_price(asset_id)
        if price_data:
            price = float(price_data["price"])
        else:
            price = 0.0
            symbol = asset.get("symbol") or f"asset {asset_id}"
            warnings.append(
                f"No price data for {symbol} ({atype}) — it is valued at €0, "
                f"so {atype} is understated and other types' drift overstated."
            )
        value_eur = to_eur(pos["quantity"] * price, asset.get("currency", "EUR"))
        type_values[atype] = type_values.get(atype, 0.0) + value_eur
    return type_values, warnings


def merge_targets(db, target_overrides: Optional[List[dict]]) -> Dict[str, float]:
    """Effective target-% set for this plan.

    Starts from the saved ``allocation_targets``; each entry in
    ``target_overrides`` (``[{"asset_type": ..., "target_pct": ...}]``)
    then replaces its matching ``asset_type`` — never additive, an override
    for "stock" replaces the stored stock target outright rather than
    stacking on top of it. Asset types not named in ``target_overrides``
    keep their stored value.
    """
    merged = {
        t["asset_type"]: float(t["target_pct"]) for t in db.get_allocation_targets()
    }
    if target_overrides:
        for o in target_overrides:
            merged[o["asset_type"]] = float(o["target_pct"])
    return merged


def validate_target_sum(targets: Dict[str, float]) -> None:
    """Raise ``ValueError`` unless the effective targets sum to ~100%.

    A DB-dependent check (it validates the *merged* set, not the request
    body alone), so it can't live in a Pydantic field validator — the route
    calls this after loading/merging targets and turns a ``ValueError`` into
    a 422.
    """
    total = sum(targets.values())
    if not (99.5 <= total <= 100.5):
        raise ValueError(
            f"Allocation targets must sum to ~100% (got {total:.1f}%). "
            "Adjust target_overrides or the saved allocation targets."
        )


def compute_before_state(
    db, portfolio_id: Optional[int], target_overrides: Optional[List[dict]]
) -> Tuple[Dict, List[str]]:
    """Step A (+ the drift half of Step B): current allocation vs. target.

    Returns ``({"total_value_eur": float, "allocations": [...]}, warnings)``
    — the ``allocations`` shape (``asset_type``/``current_value_eur``/
    ``current_pct``/``target_pct``/``drift_pct``/``drift_eur``) is exactly
    ``get_rebalance_analysis``'s, and both now build positions with
    ``compute_positions``, so the planner and the analysis endpoint can't
    disagree about "current allocation". ``warnings`` names any holding
    whose EUR value is unreliable (no price row, stale FX) — see
    ``_current_type_values``.

    Raises:
        ValueError: the effective target set doesn't sum to ~100%
            (``validate_target_sum``).
    """
    if portfolio_id is not None:
        transactions = db.get_transactions_by_portfolio(portfolio_id)
    else:
        transactions = db.get_all_transactions()

    type_values, warnings = _current_type_values(db, transactions)
    total_eur = sum(type_values.values())

    targets = merge_targets(db, target_overrides)
    validate_target_sum(targets)

    all_types = set(type_values) | set(targets)
    allocations = []
    for atype in sorted(all_types):
        current_val = type_values.get(atype, 0.0)
        current_pct = (current_val / total_eur * 100) if total_eur else 0.0
        target_pct = targets.get(atype, 0.0)
        target_val = (target_pct / 100) * total_eur
        drift_eur = target_val - current_val
        drift_pct = current_pct - target_pct

        allocations.append(
            {
                "asset_type": atype,
                "current_value_eur": round(current_val, 2),
                "current_pct": round(current_pct, 1),
                "target_pct": target_pct,
                "drift_pct": round(drift_pct, 1),
                "drift_eur": round(drift_eur, 2),
            }
        )

    return (
        {
            "total_value_eur": round(total_eur, 2),
            "allocations": allocations,
        },
        warnings,
    )


def compute_gaps(allocations: List[dict], min_trade_eur: float) -> List[dict]:
    """Step B: per-asset-type buy/sell gaps versus target.

    ``gap_eur`` is exactly ``drift_eur`` from ``compute_before_state``:
    positive means underweight (needs a buy), negative means overweight
    (needs a sell). Gaps smaller than ``min_trade_eur`` are dropped — too
    small to ever become a trade candidate. This list isn't part of the API
    response yet (there are no trades to generate from it in this task);
    Task 3's trade assignment is its first real consumer.
    """
    gaps = []
    for a in allocations:
        gap_eur = a["drift_eur"]
        if abs(gap_eur) < min_trade_eur:
            continue
        gaps.append(
            {
                "asset_type": a["asset_type"],
                "gap_eur": gap_eur,
                "side": "BUY" if gap_eur > 0 else "SELL",
            }
        )
    return gaps


def _stub_plan(
    strategy: str,
    max_abs_drift_pct_after: float,
    data_warnings: Optional[List[str]] = None,
) -> Dict:
    """A strategy's placeholder plan: no trades, zeroed summary, warnings.

    ``max_abs_drift_pct_after`` is the exception — trades don't move drift
    since there are none, so "after" is simply "before"'s own worst drift.
    ``data_warnings`` (missing prices, stale FX) are repeated on every plan
    because a client rendering one strategy's card in isolation still needs
    to know the drift it shows is built on approximate values.
    """
    return {
        "strategy": strategy,
        "summary": {
            "trade_count": 0,
            "buy_total_eur": 0.0,
            "sell_total_eur": 0.0,
            "estimated_realized_gain_eur": 0.0,
            "estimated_tax_delta_eur": 0.0,
            "max_abs_drift_pct_after": max_abs_drift_pct_after,
        },
        "trades": [],
        "warnings": [STUB_WARNING] + list(data_warnings or []),
    }


def build_plan(
    db,
    *,
    portfolio_id: Optional[int] = None,
    min_trade_eur: float = 100.0,
    target_overrides: Optional[List[dict]] = None,
) -> Dict:
    """Steps A + B: current state, gaps, and one stub plan per strategy.

    Every strategy in ``STRATEGIES`` is always present, each with
    ``trades: []`` (see ``_stub_plan``) — Task 3 replaces the stub with
    real trade assignment per strategy.

    Returns ``{"before": ..., "plans": [...], "warnings": [...]}``. The
    top-level ``warnings`` always leads with ``STUB_PLAN_NOTICE`` (so a
    client reading only that list can't mistake a stubbed response for a
    finished one) followed by any pricing/FX warnings from Step A.

    Raises:
        ValueError: the effective target set doesn't sum to ~100%
            (propagated from ``compute_before_state``).
    """
    before, data_warnings = compute_before_state(db, portfolio_id, target_overrides)
    # Step B. Its result is deliberately unused here: nothing consumes a gap
    # without trade generation (Task 3, its first real consumer). The call
    # stays so the function is exercised end-to-end on real data rather than
    # only by its unit tests — it does NOT mean min_trade_eur affects the
    # response yet; it currently does not.
    compute_gaps(before["allocations"], min_trade_eur)

    max_abs_drift = max(
        (abs(a["drift_pct"]) for a in before["allocations"]), default=0.0
    )

    plans = [
        _stub_plan(strategy, max_abs_drift, data_warnings) for strategy in STRATEGIES
    ]

    return {
        "before": before,
        "plans": plans,
        "warnings": [STUB_PLAN_NOTICE] + data_warnings,
    }
