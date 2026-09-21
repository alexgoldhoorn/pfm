"""
Tax-aware rebalance planner behind ``POST /api/v1/rebalance/plan``.

Five steps, in order:

- **A** current allocation per asset type (:func:`compute_before_state`),
- **B** the EUR gap to target per type (:func:`compute_gaps`),
- **C** one sell candidate per held symbol in an overweight type, priced with
  its own open FIFO lots and ranked per strategy
  (:func:`build_sell_candidates`, :func:`rank_sell_candidates`),
- **D** the resulting cash (plus any ``cash_budget_eur``) spent on the
  underweight types (:func:`allocate_buy_trades`),
- **E** the request's constraints — ``excluded_symbols``/``locked_symbols``
  at candidate generation, then ``max_sell_gain_eur`` while sells are
  selected, then ``max_trades`` over the combined list, with
  ``allow_sells=False`` skipping Step C entirely.

Everything it reports is an **estimate**: latest stored prices, today's FX
for proceeds, and a tax figure that models only the Spanish IRPF savings
base. A constraint that blocks a full rebalance produces a partial plan with
warnings, never an exception — and the drift it reports is only as good as
the prices behind it, so a holding with no price row (valued at €0) or one
converted at a stale FX rate is named in ``warnings`` rather than quietly
understating its own asset type and inflating everyone else's drift.
"""

from typing import Callable, Dict, Iterable, List, Optional, Tuple

from portf_manager import market
from portf_manager.positions import compute_positions
from portf_manager.services.analytics_service import (
    current_year_savings_base,
    irpf_savings_tax,
)

# The three strategy views every request returns. They share Steps A-D and
# differ only in how rank_sell_candidates orders the sell candidates.
STRATEGIES = ("tax_minimal", "closest_to_target", "balanced")


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


def build_holdings(db, transactions: List[dict]) -> Tuple[List[Dict], List[str]]:
    """Every currently-open position, priced in EUR, plus pricing warnings.

    The per-asset layer under :func:`_current_type_values`'s type totals —
    Steps C and D need to name individual symbols, so the loop that joins
    positions to assets and prices lives here and both callers share it
    rather than each walking the positions dict its own way.

    Positions come from the shared ``compute_positions`` (handles stock
    splits) rather than an inline buy/sell loop — Step A explicitly calls for
    the shared helper, and ``get_rebalance_analysis`` was moved onto the same
    helper so the two endpoints can't disagree about what "current
    allocation" means.

    A held position with no price row is valued at 0 (as it always was) but
    now says so: the totals stay usable while naming what they understate.
    Such a holding is still returned (with ``price_eur`` 0) — it is part of
    the allocation; trade generation is what has to refuse to act on it.

    Returns:
        ``(holdings, warnings)`` where each holding is
        ``{"asset_id", "symbol", "asset_type", "currency", "quantity",
        "price", "price_eur", "value_eur"}``, sorted by symbol for
        deterministic downstream ordering.
    """
    positions, _realised = compute_positions(transactions)
    warnings: List[str] = []
    to_eur = _to_eur_converter(db, warnings)

    holdings: List[Dict] = []
    for asset_id, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(asset_id)
        if not asset:
            continue
        atype = asset.get("asset_type", "other")
        symbol = asset.get("symbol") or f"asset {asset_id}"
        currency = asset.get("currency", "EUR") or "EUR"
        price_data = db.get_latest_price(asset_id)
        if price_data:
            price = float(price_data["price"])
        else:
            price = 0.0
            warnings.append(
                f"No price data for {symbol} ({atype}) — it is valued at €0, "
                f"so {atype} is understated and other types' drift overstated."
            )
        holdings.append(
            {
                "asset_id": asset_id,
                "symbol": symbol,
                "asset_type": atype,
                "currency": currency,
                "quantity": float(pos["quantity"]),
                "price": price,
                "price_eur": to_eur(price, currency),
                "value_eur": to_eur(pos["quantity"] * price, currency),
            }
        )
    holdings.sort(key=lambda h: str(h["symbol"]))
    return holdings, warnings


def _current_type_values(
    db, transactions: List[dict]
) -> Tuple[Dict[str, float], List[str]]:
    """Per-asset-type EUR value of currently open positions, plus any
    data-quality warnings raised while pricing them.

    A thin aggregation over :func:`build_holdings` so the type totals behind
    ``before`` and the per-symbol rows behind the trades can't be built from
    two different walks of the same data.
    """
    holdings, warnings = build_holdings(db, transactions)
    type_values: Dict[str, float] = {}
    for h in holdings:
        atype = h["asset_type"]
        type_values[atype] = type_values.get(atype, 0.0) + h["value_eur"]
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
    small to ever become a trade candidate. This is also where
    ``min_trade_eur`` is enforced for a whole type: Steps C and D consume
    this list as-is rather than re-applying the threshold to a type's gap.
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


# ── Step C: sell candidates ──────────────────────────────────────────────────

NO_CASH_WARNING = (
    "allow_sells is false and no cash_budget_eur was given — nothing to buy with."
)


def fifo_gain_eur(lots: List[Dict], quantity: float, price_eur: float) -> float:
    """Estimated EUR gain of selling *quantity* units at *price_eur*, FIFO.

    ``lots`` are :func:`build_sell_candidates`' pre-converted open lots —
    ``{"quantity", "unit_cost_eur"}`` in purchase order, each unit cost
    already including its share of the purchase fee and already converted at
    that lot's own purchase-date FX rate. The oldest lots are consumed first,
    which is what Spanish IRPF requires and therefore what a partial sale
    would really realise; a proportional share of the whole position's gain
    would be a different (and wrong) number.

    Proceeds are gross: a proposed trade has no known sale fee, so unlike
    ``TaxCalculator`` (which nets real ones out) nothing is subtracted.
    """
    remaining = quantity
    cost = 0.0
    for lot in lots:
        if remaining <= 0:
            break
        take = min(remaining, float(lot["quantity"]))
        cost += take * float(lot["unit_cost_eur"])
        remaining -= take
    return quantity * price_eur - cost


def plan_sale(candidate: Dict, gap_eur: float) -> Dict:
    """What selling *candidate* into a ``gap_eur`` hole would actually realise.

    The single implementation of "how much of this position gets sold, and
    what does that cost in tax" — :func:`build_sell_candidates` calls it with
    the type's **initial** gap to derive the number
    :func:`rank_sell_candidates` orders on, and
    :func:`allocate_sell_trades` calls it again with whatever is **left** of
    that gap to build the trade. Ranking and execution therefore describe the
    same transaction.

    That shared call is the point. Ranking on the *whole position's* gain
    ratio, as an earlier version did, is a different number from the one the
    trade realises whenever the lots are unevenly priced: a position that is
    cheap on average can be expensive to sell a slice of, because FIFO eats
    its oldest (often most expensive) lot first. ``tax_minimal`` ranked on the
    average and could pick the more heavily taxed of two trades.

    Returns:
        ``{"amount_eur", "quantity", "gain_eur", "gain_ratio"}`` for a sale of
        ``min(proceeds_eur, gap_eur)`` worth. ``gain_ratio`` is gain per euro
        sold, and is 0.0 for a degenerate (zero-value) sale.
    """
    amount_eur = min(candidate["proceeds_eur"], max(gap_eur, 0.0))
    quantity = amount_eur / candidate["price_eur"] if candidate["price_eur"] else 0.0
    gain_eur = fifo_gain_eur(candidate["lots"], quantity, candidate["price_eur"])
    return {
        "amount_eur": amount_eur,
        "quantity": quantity,
        "gain_eur": gain_eur,
        "gain_ratio": (gain_eur / amount_eur) if amount_eur > 0 else 0.0,
    }


def build_sell_candidates(
    db,
    holdings: List[Dict],
    gaps: List[Dict],
    *,
    transactions: List[dict],
    excluded_symbols: Iterable[str] = (),
    locked_symbols: Iterable[str] = (),
    drift_by_type: Optional[Dict[str, float]] = None,
) -> Tuple[List[Dict], List[str]]:
    """Step C: one sell candidate per held symbol in an overweight type.

    A candidate is a **whole open position**, scored by its full-sale
    numbers; how much of it is actually sold is decided later by
    :func:`allocate_sell_trades`. Lot-level candidates are out of scope for
    the MVP (§14 of the plan doc).

    ``excluded_symbols`` and ``locked_symbols`` are applied here, at
    generation time rather than as a post-hoc filter, so an excluded symbol
    never reaches scoring and can't influence ``balanced``'s min-max
    normalisation.

    FX follows the same convention as ``/analytics/tax-report``: proceeds at
    the **current** rate (the sale would happen now), each lot's cost basis
    at the rate on **its own purchase date**.

    Returns:
        ``(candidates, warnings)``. Each candidate carries the open position
        (``quantity``/``price_eur``/``proceeds_eur``/``cost_basis_eur``/
        ``gain_eur``, all describing a sale of *all* of it), its type's
        ``gap_eur``/``drift_pct``, the EUR-converted ``lots``
        :func:`fifo_gain_eur` consumes, and ``planned_gain_ratio`` — the gain
        per euro of the sale that would really be made, which is the only one
        of these :func:`rank_sell_candidates` may order on.
    """
    from portf_manager.tax_calculator import TaxCalculator

    overweight = {g["asset_type"]: g["gap_eur"] for g in gaps if g["side"] == "SELL"}
    if not overweight:
        return [], []

    excluded = {s.strip().upper() for s in excluded_symbols if s and s.strip()}
    locked = {s.strip().upper() for s in locked_symbols if s and s.strip()}
    drift_by_type = drift_by_type or {}

    warnings: List[str] = []
    calc = TaxCalculator(db)
    fx_on_cache: Dict[Tuple[str, object], float] = {}

    def rate_on(currency: str, on_date) -> float:
        key = (currency, on_date)
        if key not in fx_on_cache:
            rate, stale = market.get_fx_eur_on(db, currency, on_date)
            fx_on_cache[key] = rate
            if stale:
                warnings.append(
                    f"No historical FX rate for {currency} on {on_date} — that "
                    "lot's cost basis (and so its estimated gain) uses today's "
                    "rate instead."
                )
        return fx_on_cache[key]

    candidates: List[Dict] = []
    for h in holdings:
        atype = h["asset_type"]
        if atype not in overweight:
            continue
        symbol = str(h["symbol"])
        upper = symbol.upper()
        if upper in excluded or upper in locked:
            continue
        if h["price_eur"] <= 0 or h["value_eur"] <= 0:
            warnings.append(
                f"{symbol} has no usable price — not offered as a sell candidate."
            )
            continue

        open_lots = calc.get_open_lots(symbol, transactions=transactions)
        if not open_lots:
            warnings.append(
                f"No open FIFO lots found for {symbol} — its cost basis is "
                "unknown, so it is not offered as a sell candidate."
            )
            continue

        currency = h["currency"]
        lots = []
        open_quantity = 0.0
        cost_basis_eur = 0.0
        for lot in open_lots:
            qty = float(lot.remaining_quantity)
            unit_cost = float(lot.price + lot.fee_per_share) * rate_on(
                currency, lot.purchase_date
            )
            lots.append({"quantity": qty, "unit_cost_eur": unit_cost})
            open_quantity += qty
            cost_basis_eur += qty * unit_cost

        proceeds_eur = open_quantity * h["price_eur"]
        if proceeds_eur <= 0:
            continue
        # The lots say a different number of shares is open than the position
        # math does — a sell with no matching buy history, or two asset rows
        # sharing a symbol. The candidate is still offered (capped at what the
        # lots can actually cover, so no gain is ever estimated against a lot
        # that isn't there), but the mismatch is named.
        if abs(open_quantity - h["quantity"]) > max(h["quantity"], 1.0) * 0.01:
            warnings.append(
                f"{symbol}: FIFO lots hold {open_quantity:,.4f} units but the "
                f"position is {h['quantity']:,.4f} — only the lot-backed part "
                "can be planned, and its gain estimate may be incomplete."
            )

        candidate = {
            "symbol": symbol,
            "asset_id": h["asset_id"],
            "asset_type": atype,
            # The whole open position, for context and display. Note these
            # describe selling *all* of it, which is not what the plan does —
            # ranking and the trade both use planned_* below.
            "quantity": open_quantity,
            "price_eur": h["price_eur"],
            "proceeds_eur": proceeds_eur,
            "cost_basis_eur": cost_basis_eur,
            "gain_eur": proceeds_eur - cost_basis_eur,
            "gap_eur": overweight[atype],
            "drift_pct": drift_by_type.get(atype, 0.0),
            "lots": lots,
        }
        # Gain per euro of the sale this candidate would actually produce if
        # it were picked first — the number rank_sell_candidates orders on,
        # from the same plan_sale() allocate_sell_trades builds the trade with.
        candidate["planned_gain_ratio"] = plan_sale(candidate, abs(overweight[atype]))[
            "gain_ratio"
        ]
        candidates.append(candidate)

    return candidates, warnings


def _normalize(value: float, low: float, high: float) -> float:
    """Min-max position of *value* in ``[low, high]``; 0.0 when the range is
    degenerate (every candidate scored the same)."""
    if high - low <= 0:
        return 0.0
    return (value - low) / (high - low)


def rank_sell_candidates(candidates: List[Dict], strategy: str) -> List[Dict]:
    """Order sell candidates for *strategy*; lowest score sells first.

    Every gain figure here is ``planned_gain_ratio`` — the gain per euro of
    the sale this candidate would **actually** produce (see :func:`plan_sale`),
    not of its whole position. Those two differ whenever a position's lots are
    unevenly priced, and ranking on the position average made ``tax_minimal``
    able to choose the more heavily taxed of two trades.

    - ``tax_minimal`` — ascending ``planned_gain_ratio``: realised losses and
      small gains go first, whatever the drift.
    - ``closest_to_target`` — descending ``abs(gap_eur)`` of the candidate's
      asset type: whichever type is furthest from target is reduced first.
      Per-symbol gain plays no part, so this one is unaffected by the above.
    - ``balanced`` — **an explicit design choice, not an obvious formula.**
      Both signals are min-max normalised across *all* candidates in this run:

          tax_score   = (ratio - min_ratio) / (max_ratio - min_ratio)
          drift_score = 1 - (abs(gap) - min_gap) / (max_gap - min_gap)
          score       = 0.5 * tax_score + 0.5 * drift_score

      so 0 is "best" on either axis (lowest gain ratio / largest gap), the two
      weigh equally, and candidates sort ascending by the combined score. A
      degenerate range (all candidates equal on that axis) scores 0 for every
      candidate, which neutralises that axis instead of dividing by zero.

    Ties break on symbol so the same input always produces the same order.
    """
    if strategy == "tax_minimal":
        return sorted(candidates, key=lambda c: (c["planned_gain_ratio"], c["symbol"]))
    if strategy == "closest_to_target":
        return sorted(candidates, key=lambda c: (-abs(c["gap_eur"]), c["symbol"]))

    ratios = [c["planned_gain_ratio"] for c in candidates]
    gaps = [abs(c["gap_eur"]) for c in candidates]
    if not ratios:
        return []
    lo_r, hi_r = min(ratios), max(ratios)
    lo_g, hi_g = min(gaps), max(gaps)

    def score(c: Dict) -> float:
        tax_score = _normalize(c["planned_gain_ratio"], lo_r, hi_r)
        drift_score = 1.0 - _normalize(abs(c["gap_eur"]), lo_g, hi_g)
        return 0.5 * tax_score + 0.5 * drift_score

    return sorted(candidates, key=lambda c: (score(c), c["symbol"]))


def _sell_reason(candidate: Dict, strategy: str, sale: Dict) -> str:
    """Why this SELL is in the plan, in the strategy's own terms.

    The gain percentage quoted is *this trade's* (``sale``), not the whole
    position's, so the sentence can never contradict the row's own
    ``estimated_gain_eur``/``amount_eur``.
    """
    head = f"{candidate['asset_type']} overweight by {abs(candidate['drift_pct']):.1f}%"
    ratio_pct = sale["gain_ratio"] * 100
    if strategy == "tax_minimal":
        return f"{head}; lowest gain per € sold ({ratio_pct:.1f}%)"
    if strategy == "closest_to_target":
        return f"{head}; largest gap (€{abs(candidate['gap_eur']):,.0f} to cut)"
    return (
        f"{head}; balanced score (gain {ratio_pct:.1f}% "
        f"vs €{abs(candidate['gap_eur']):,.0f} gap)"
    )


def allocate_sell_trades(
    ranked: List[Dict],
    *,
    strategy: str,
    min_trade_eur: float,
    max_sell_gain_eur: Optional[float] = None,
) -> Tuple[List[Dict], List[str]]:
    """Turn ranked candidates into SELL trades, sized to each type's gap.

    A candidate is a whole position but a trade is not: selling all of a
    €50k holding to fix a €1k overweight would overshoot into the opposite
    drift, so each trade is capped at whatever is left of its asset type's
    overweight gap and its gain is computed FIFO over exactly that quantity
    (:func:`fifo_gain_eur`). Once a type's remaining gap drops below
    ``min_trade_eur`` no further candidate in it is sold.

    ``max_sell_gain_eur`` (Step E rule 4) is applied while the list is being
    built, before buys are assigned, because the buy side spends whatever
    proceeds survive this cap. The first trade that would push the running
    realised gain over the cap stops the loop — nothing further is sold, and
    the warning says how many candidates were left.

    Returns:
        ``(trades, warnings)`` — trades in the order they were selected.
    """
    remaining_gap: Dict[str, float] = {}
    for c in ranked:
        remaining_gap.setdefault(c["asset_type"], abs(c["gap_eur"]))

    trades: List[Dict] = []
    warnings: List[str] = []
    running_gain = 0.0

    for index, c in enumerate(ranked):
        need = remaining_gap[c["asset_type"]]
        # The `> 0` floors are not redundant with min_trade_eur: that is
        # allowed to be 0, and then a type sitting exactly on target survives
        # compute_gaps and would otherwise emit a zero-value, zero-quantity
        # trade.
        if need <= 0 or need < min_trade_eur:
            continue
        sale = plan_sale(c, need)
        amount_eur = sale["amount_eur"]
        if amount_eur <= 0 or amount_eur < min_trade_eur:
            continue
        quantity = sale["quantity"]
        gain_eur = sale["gain_eur"]

        if (
            max_sell_gain_eur is not None
            and running_gain + gain_eur > max_sell_gain_eur
        ):
            left = len(ranked) - index
            warnings.append(
                f"Sell gain cap (€{max_sell_gain_eur:,.2f}) reached; {left} "
                f"further overweight position(s) not sold."
            )
            break

        running_gain += gain_eur
        remaining_gap[c["asset_type"]] = need - amount_eur
        trades.append(
            {
                "symbol": c["symbol"],
                "asset_id": c["asset_id"],
                "asset_type": c["asset_type"],
                "side": "SELL",
                "quantity": round(quantity, 6),
                "price_eur": round(c["price_eur"], 4),
                "amount_eur": round(amount_eur, 2),
                "estimated_gain_eur": round(gain_eur, 2),
                # Filled in by allocate_trade_taxes once the sell list (and so
                # the progressive-bracket total) is final.
                "estimated_tax_eur": 0.0,
                "reason": _sell_reason(c, strategy, sale),
            }
        )

    return trades, warnings


# ── Step D: buy assignment ───────────────────────────────────────────────────


def allocate_buy_trades(
    holdings: List[Dict],
    gaps: List[Dict],
    available_cash_eur: float,
    *,
    min_trade_eur: float,
    excluded_symbols: Iterable[str] = (),
) -> Tuple[List[Dict], List[str]]:
    """Step D: spend *available_cash_eur* on the underweight types.

    Identical for all three strategies — the plan makes only sell ranking
    strategy-specific. Types are funded in descending gap order (largest
    underweight first) from one shared cash pool, and within a type the buy
    goes to the largest existing holding, which keeps symbol churn down (and
    is why no new symbol is ever introduced — explicitly post-MVP).

    A type whose gap the remaining cash only partly covers is still traded,
    for as much as the cash allows, with a warning — a partial fill moves
    toward target, and silently skipping it would not. A remainder below
    ``min_trade_eur`` is not worth a trade and is reported instead.
    """
    excluded = {s.strip().upper() for s in excluded_symbols if s and s.strip()}
    buy_gaps = sorted(
        (g for g in gaps if g["side"] == "BUY"),
        key=lambda g: (-g["gap_eur"], g["asset_type"]),
    )

    trades: List[Dict] = []
    warnings: List[str] = []
    cash = max(available_cash_eur, 0.0)

    for gap in buy_gaps:
        atype = gap["asset_type"]
        eligible = [
            h
            for h in holdings
            if h["asset_type"] == atype
            and str(h["symbol"]).upper() not in excluded
            and h["price_eur"] > 0
        ]
        if not eligible:
            warnings.append(f"No eligible held symbol to buy for {atype} — skipped.")
            continue
        target = sorted(eligible, key=lambda h: (-h["value_eur"], str(h["symbol"])))[0]

        needed = gap["gap_eur"]
        # min_trade_eur is allowed to be 0, so a type sitting exactly on
        # target survives compute_gaps; nothing to fund, and a €0 trade is not
        # the answer.
        if needed <= 0:
            continue
        amount_eur = min(needed, cash)
        if amount_eur <= 0:
            warnings.append(
                f"No cash left to fund {atype} — €{needed:,.2f} still needed."
            )
            continue
        if amount_eur < min_trade_eur:
            warnings.append(
                f"Insufficient cash to fund {atype} — €{needed:,.2f} needed, "
                f"€{cash:,.2f} available (below the €{min_trade_eur:,.2f} "
                "minimum trade)."
            )
            continue
        if amount_eur < needed - 0.005:
            warnings.append(
                f"Insufficient cash to fully fund {atype} — bought "
                f"€{amount_eur:,.2f} of €{needed:,.2f} needed."
            )

        cash -= amount_eur
        trades.append(
            {
                "symbol": target["symbol"],
                "asset_id": target["asset_id"],
                "asset_type": atype,
                "side": "BUY",
                "quantity": round(amount_eur / target["price_eur"], 6),
                "price_eur": round(target["price_eur"], 4),
                "amount_eur": round(amount_eur, 2),
                "estimated_gain_eur": None,
                "estimated_tax_eur": None,
                "reason": (
                    f"Fund {atype} underweight (€{needed:,.0f} needed) via "
                    f"largest existing holding {target['symbol']}"
                ),
            }
        )

    return trades, warnings


# ── Step E: constraints, summary, after-state ────────────────────────────────


def truncate_trades(
    trades: List[Dict], max_trades: Optional[int]
) -> Tuple[List[Dict], List[str]]:
    """Step E rule 3: keep the first *max_trades* of the combined list.

    The list arrives sells-first (in their ranked order) then buys (in gap
    order), so what survives a truncation is the funding side rather than
    unfunded purchases.
    """
    if max_trades is None or len(trades) <= max_trades:
        return list(trades), []
    dropped = len(trades) - max_trades
    return (
        trades[:max_trades],
        [f"Plan truncated to {max_trades} trades; {dropped} more were generated."],
    )


def allocate_trade_taxes(sell_trades: List[Dict], tax_delta_eur: float) -> None:
    """Spread a plan's aggregate ``estimated_tax_delta_eur`` over its sells.

    **Display only.** The real liability is progressive and lives on the
    year's total savings base, so no single trade "costs" a fixed amount of
    tax in isolation — the last euro of gain is taxed at a higher marginal
    rate than the first, and which trade is "last" is arbitrary. Each sell is
    therefore attributed its share of the aggregate, weighted by its own gain,
    purely so a UI can show a per-row figure that adds up to the plan total.

    Trades are mutated in place. When the plan's total gain is zero or
    negative there is nothing to apportion and every sell gets ``0.0``.
    """
    total_gain = sum(t["estimated_gain_eur"] or 0.0 for t in sell_trades)
    for t in sell_trades:
        if total_gain > 0:
            share = (t["estimated_gain_eur"] or 0.0) / total_gain
            t["estimated_tax_eur"] = round(share * tax_delta_eur, 2)
        else:
            t["estimated_tax_eur"] = 0.0


def compute_after_drift(allocations: List[Dict], trades: List[Dict]) -> float:
    """Worst absolute drift (in %) once *trades* are applied to *allocations*.

    Each trade moves its amount into or out of its asset type; the new total
    is the sum of the new type values, so unspent sale proceeds leave the
    invested total (they become cash, which no target covers) and an injected
    ``cash_budget_eur`` enlarges it. A type with a target but no holdings is
    still measured — its whole target percentage is drift.
    """
    values = {a["asset_type"]: float(a["current_value_eur"]) for a in allocations}
    targets = {a["asset_type"]: float(a["target_pct"]) for a in allocations}
    for t in trades:
        atype = t["asset_type"]
        signed = t["amount_eur"] if t["side"] == "BUY" else -t["amount_eur"]
        values[atype] = values.get(atype, 0.0) + signed
    total = sum(values.values())
    worst = 0.0
    for atype in set(values) | set(targets):
        pct = (values.get(atype, 0.0) / total * 100) if total > 0 else 0.0
        worst = max(worst, abs(pct - targets.get(atype, 0.0)))
    return round(worst, 1)


def _summarize(trades: List[Dict], tax_delta_eur: float, after_drift: float) -> Dict:
    sells = [t for t in trades if t["side"] == "SELL"]
    buys = [t for t in trades if t["side"] == "BUY"]
    return {
        "trade_count": len(trades),
        "buy_total_eur": round(sum(t["amount_eur"] for t in buys), 2),
        "sell_total_eur": round(sum(t["amount_eur"] for t in sells), 2),
        "estimated_realized_gain_eur": round(
            sum(t["estimated_gain_eur"] or 0.0 for t in sells), 2
        ),
        "estimated_tax_delta_eur": round(tax_delta_eur, 2),
        "max_abs_drift_pct_after": after_drift,
    }


def build_strategy_plan(
    strategy: str,
    *,
    before: Dict,
    gaps: List[Dict],
    holdings: List[Dict],
    candidates: List[Dict],
    options: Dict,
    tax_delta_for: Callable[[float], float],
    data_warnings: List[str],
) -> Dict:
    """One strategy's plan: ranked sells, funded buys, constraints, summary.

    ``tax_delta_for`` maps a plan's total realised gain to its IRPF delta
    against the year's existing savings base — a callable rather than a
    number so the (expensive) baseline is computed once per request and only
    when a strategy actually sells something.
    """
    plan_warnings: List[str] = []
    min_trade_eur = options["min_trade_eur"]
    cash_budget = options.get("cash_budget_eur") or 0.0

    sell_trades: List[Dict] = []
    if options["allow_sells"]:
        ranked = rank_sell_candidates(candidates, strategy)
        sell_trades, sell_warnings = allocate_sell_trades(
            ranked,
            strategy=strategy,
            min_trade_eur=min_trade_eur,
            max_sell_gain_eur=options.get("max_sell_gain_eur"),
        )
        plan_warnings.extend(sell_warnings)

    available_cash = sum(t["amount_eur"] for t in sell_trades) + cash_budget
    if not options["allow_sells"] and cash_budget <= 0:
        # Step E rule 5: nothing funds a buy, so there is no plan to build —
        # say so rather than returning a silently empty trade list.
        plan_warnings.append(NO_CASH_WARNING)
        buy_trades: List[Dict] = []
    else:
        buy_trades, buy_warnings = allocate_buy_trades(
            holdings,
            gaps,
            available_cash,
            min_trade_eur=min_trade_eur,
            excluded_symbols=options["excluded_symbols"],
        )
        plan_warnings.extend(buy_warnings)

    trades, truncation_warnings = truncate_trades(
        sell_trades + buy_trades, options["max_trades"]
    )
    plan_warnings.extend(truncation_warnings)

    # Tax is computed on what survived truncation, not on what was proposed.
    surviving_sells = [t for t in trades if t["side"] == "SELL"]
    total_gain = sum(t["estimated_gain_eur"] or 0.0 for t in surviving_sells)
    tax_delta = tax_delta_for(total_gain) if surviving_sells else 0.0
    allocate_trade_taxes(surviving_sells, tax_delta)

    return {
        "strategy": strategy,
        "summary": _summarize(
            trades, tax_delta, compute_after_drift(before["allocations"], trades)
        ),
        "trades": trades,
        "warnings": plan_warnings + list(data_warnings),
    }


def build_plan(
    db,
    *,
    portfolio_id: Optional[int] = None,
    min_trade_eur: float = 100.0,
    target_overrides: Optional[List[dict]] = None,
    cash_budget_eur: Optional[float] = None,
    allow_sells: bool = True,
    max_trades: int = 12,
    max_sell_gain_eur: Optional[float] = None,
    excluded_symbols: Optional[List[str]] = None,
    locked_symbols: Optional[List[str]] = None,
) -> Dict:
    """Steps A-E: current state, gaps, and one real trade plan per strategy.

    Every strategy in ``STRATEGIES`` is always present. They share Step A/B
    (current state, gaps), the sell-candidate set built in Step C and the
    Step D buy routing, and differ only in the order
    :func:`rank_sell_candidates` puts those candidates in — which is what
    makes one plan cheaper in tax and another closer to target.

    Constraints that block a full rebalance produce a **partial plan with
    warnings** (per §11/§13 of the plan doc), never an exception and never an
    all-or-nothing result.

    Returns ``{"before": ..., "plans": [...], "warnings": [...]}``; the
    top-level ``warnings`` carries the data-quality issues (missing price,
    stale FX, a position with no FIFO lots) behind every plan, which each
    plan repeats so a client rendering one strategy's card in isolation still
    sees them.

    Raises:
        ValueError: the effective target set doesn't sum to ~100%
            (propagated from ``compute_before_state``).
    """
    before, data_warnings = compute_before_state(db, portfolio_id, target_overrides)
    gaps = compute_gaps(before["allocations"], min_trade_eur)

    transactions = (
        db.get_transactions_by_portfolio(portfolio_id)
        if portfolio_id is not None
        else db.get_all_transactions()
    )
    # Second pass over the same data: compute_before_state already ran
    # build_holdings internally for its type totals, and its signature is
    # Step A's tested contract, so the per-symbol rows are rebuilt here rather
    # than threaded through it. The warnings are therefore identical to
    # data_warnings and are dropped instead of repeated. Collapsing the two
    # passes is the plan doc's §11 performance item (Task 5).
    holdings, _duplicate_warnings = build_holdings(db, transactions)

    drift_by_type = {a["asset_type"]: a["drift_pct"] for a in before["allocations"]}

    candidates: List[Dict] = []
    candidate_warnings: List[str] = []
    if allow_sells:
        candidates, candidate_warnings = build_sell_candidates(
            db,
            holdings,
            gaps,
            transactions=transactions,
            excluded_symbols=excluded_symbols or [],
            locked_symbols=locked_symbols or [],
            drift_by_type=drift_by_type,
        )

    shared_warnings = data_warnings + candidate_warnings

    baseline: List[Optional[float]] = [None]

    def tax_delta_for(total_gain: float) -> float:
        """IRPF delta of adding *total_gain* to this year's savings base.

        The baseline is a full FIFO pass over the year, so it is computed at
        most once per request and only if some strategy actually sells.
        """
        if baseline[0] is None:
            baseline[0] = current_year_savings_base(db)
        base = baseline[0]
        return irpf_savings_tax(base + total_gain) - irpf_savings_tax(base)

    options = {
        "min_trade_eur": min_trade_eur,
        "max_trades": max_trades,
        "allow_sells": allow_sells,
        "cash_budget_eur": cash_budget_eur,
        "max_sell_gain_eur": max_sell_gain_eur,
        "excluded_symbols": excluded_symbols or [],
        "locked_symbols": locked_symbols or [],
    }

    plans = [
        build_strategy_plan(
            strategy,
            before=before,
            gaps=gaps,
            holdings=holdings,
            candidates=candidates,
            options=options,
            tax_delta_for=tax_delta_for,
            data_warnings=shared_warnings,
        )
        for strategy in STRATEGIES
    ]

    return {
        "before": before,
        "plans": plans,
        "warnings": shared_warnings,
    }
