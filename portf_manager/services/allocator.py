"""Candidates, target checks, the bucket split and traspasos of the allocator.

Pure functions where possible: :func:`check_targets`,
:func:`investable_targets`, :func:`split_buckets` and :func:`plan_traspasos`
take plain dicts, so the arithmetic is testable without a database.
:func:`build_candidates` is the one that reads the database.
"""

from datetime import date
from typing import Callable, Dict, List, Optional, Tuple

from portf_manager.services.exposure import compute_exposure, find_fund_overlaps
from portf_manager.services.rebalance_planner import (
    build_sell_candidates,
    fifo_gain_eur,
)
from portf_manager.services.wash_sale import (
    WashSaleUnavailable,
    blocked_assets,
    is_fund,
)

# A held type above this share of the portfolio must have a target row.
HELD_TYPE_MIN_PCT = 1.0
# Targets must sum to 100 within this tolerance.
TARGET_SUM_TOLERANCE = 0.01


def _portfolio_by_asset(transactions: List[dict]) -> Dict[int, int]:
    """Portfolio holding most of each asset's quantity, from the transactions."""
    net: Dict[int, Dict[int, float]] = {}
    for tx in transactions:
        pid = tx.get("portfolio_id")
        if pid is None:
            continue
        qty = float(tx.get("quantity") or 0)
        kind = (tx.get("transaction_type") or "").lower()
        sign = -1.0 if kind == "sell" else 1.0 if kind == "buy" else 0.0
        per = net.setdefault(tx["asset_id"], {})
        per[pid] = per.get(pid, 0.0) + sign * qty
    result = {}
    for asset_id, per in net.items():
        result[asset_id] = max(per, key=lambda p: (per[p], -p))
    return result


def _ter(db, asset_id: int) -> Optional[float]:
    """Ongoing charges of a fund in %, or None when unknown."""
    profile = db.get_fund_profile(asset_id)
    value = (profile or {}).get("ongoing_charges_pct")
    return float(value) if value is not None else None


def _blocked_entry(info: dict) -> dict:
    """Candidate ``blocked`` dict from a wash-sale record."""
    return {
        "reason": (
            f"sold at a loss on {info['sold_on']}; "
            f"can be bought again from {info['safe_from']}"
        ),
        "sold_on": info["sold_on"],
        "safe_from": info["safe_from"],
        "loss_eur": info["loss_eur"],
    }


def build_candidates(
    db,
    holdings: List[dict],
    transactions: List[dict],
    settings: dict,
    today: date,
    fx: Callable[[str], float],
    fx_on: Optional[Callable] = None,
) -> Tuple[List[dict], List[str]]:
    """Every asset the next euro could go into, tagged for ranking.

    Held assets with a price, watchlist entries that resolve to a priced asset
    (value 0), and one collapsed candidate per managed portfolio.

    Args:
        db: Database.
        holdings: ``compute_before_state`` holdings.
        transactions: The transactions those holdings came from.
        settings: Allocator settings (``managed_portfolio_ids``).
        today: Reference date.
        fx: ``fx(currency) -> rate to EUR``.
        fx_on: Optional historical FX for the antiaplicación check.

    Returns:
        ``(candidates, warnings)``. Candidates are dicts with ``asset_id``,
        ``symbol``, ``name``, ``asset_type``, ``bucket``, ``portfolio_id``,
        ``portfolio_name``, ``value_eur``, ``weight_pct``, ``is_fund``,
        ``ter_pct``, ``overlap_group``, ``buy_below``, ``fair_value``,
        ``recommendation``, ``conviction``, ``blocked``, ``managed``,
        ``is_watchlist``, ``price_eur``.
    """
    warnings: List[str] = []
    portfolio_names = {p["id"]: p["name"] for p in db.get_all_portfolios()}
    portfolio_of = _portfolio_by_asset(transactions)
    managed_ids = set(settings.get("managed_portfolio_ids") or [])
    priced = [h for h in holdings if h["value_eur"] > 0 and h["price_eur"] > 0]
    total = sum(h["value_eur"] for h in priced)

    # Overlap groups: only consolidation candidates matter here.
    exposure = compute_exposure(db, fx=fx, today=today)
    overlap: Dict[int, dict] = {}
    for group in find_fund_overlaps(exposure["funds"], exposure["total_value_eur"]):
        if group["kind"] != "consolidation_candidate":
            continue
        largest = max(group["members"], key=lambda m: m["value_eur"])
        for member in group["members"]:
            overlap[member["asset_id"]] = {
                "kind": group["kind"],
                "largest_member_id": largest["asset_id"],
            }

    # Antiaplicación: when it cannot be computed, stocks are held back.
    blocked: Dict[int, dict] = {}
    wash_unavailable = False
    try:
        blocked = blocked_assets(db, today, fx_on=fx_on)
    except WashSaleUnavailable as exc:
        wash_unavailable = True
        warnings.append(
            f"Antiaplicación check unavailable ({exc}); stocks are not proposed."
        )

    research = {
        (n.get("symbol") or "").upper(): n for n in db.get_latest_research_notes()
    }

    def research_fields(asset_id: int, symbol: str) -> dict:
        note = research.get(symbol.upper()) or {}
        target = db.get_price_target(asset_id) or {}
        report = db.get_research_report(asset_id) or {}
        buy_below = target.get("buy_below")
        if buy_below is None:
            buy_below = note.get("buy_below")
        fair_value = target.get("fair_value")
        if fair_value is None:
            fair_value = note.get("fair_value")
        return {
            "buy_below": buy_below,
            "fair_value": fair_value,
            "recommendation": report.get("recommendation"),
            "conviction": note.get("conviction"),
        }

    def block_state(asset: dict) -> Optional[dict]:
        if wash_unavailable and asset.get("asset_type") == "stock":
            return {"reason": "antiaplicación check unavailable"}
        info = blocked.get(asset["id"])
        return _blocked_entry(info) if info else None

    candidates: List[dict] = []
    managed_rows: Dict[int, List[dict]] = {}
    held_ids = set()
    for h in priced:
        held_ids.add(h["asset_id"])
        pid = portfolio_of.get(h["asset_id"])
        if pid in managed_ids:
            managed_rows.setdefault(pid, []).append(h)
            continue
        asset = db.get_asset(h["asset_id"]) or {}
        fund = is_fund(asset)
        candidates.append(
            {
                "asset_id": h["asset_id"],
                "symbol": h["symbol"],
                "name": asset.get("name") or h["symbol"],
                "asset_type": h["asset_type"],
                "bucket": h["asset_type"],
                "portfolio_id": pid,
                "portfolio_name": portfolio_names.get(pid),
                "value_eur": round(h["value_eur"], 2),
                "weight_pct": h["value_eur"] / total * 100 if total else 0.0,
                "is_fund": fund,
                "ter_pct": _ter(db, h["asset_id"]) if fund else None,
                "overlap_group": overlap.get(h["asset_id"]),
                "blocked": block_state({**asset, "id": h["asset_id"]}),
                "managed": False,
                "is_watchlist": False,
                "price_eur": h["price_eur"],
                **research_fields(h["asset_id"], h["symbol"]),
            }
        )

    # A managed portfolio is one contribution line in its largest bucket.
    for pid, rows in managed_rows.items():
        by_type: Dict[str, float] = {}
        for h in rows:
            by_type[h["asset_type"]] = by_type.get(h["asset_type"], 0.0) + (
                h["value_eur"]
            )
        bucket = max(by_type, key=lambda t: by_type[t])
        weighted = [(h["value_eur"], _ter(db, h["asset_id"])) for h in rows]
        known = [(v, t) for v, t in weighted if t is not None]
        known_value = sum(v for v, _ in known)
        ter = sum(v * t for v, t in known) / known_value if known_value else None
        value = sum(h["value_eur"] for h in rows)
        name = portfolio_names.get(pid, str(pid))
        candidates.append(
            {
                "asset_id": None,
                "symbol": f"contribution:{name}",
                "name": f"Contribution to {name}",
                "asset_type": bucket,
                "bucket": bucket,
                "portfolio_id": pid,
                "portfolio_name": name,
                "value_eur": round(value, 2),
                "weight_pct": value / total * 100 if total else 0.0,
                "is_fund": True,
                "ter_pct": ter,
                "overlap_group": None,
                "buy_below": None,
                "fair_value": None,
                "recommendation": None,
                "conviction": None,
                "blocked": None,
                "managed": True,
                "is_watchlist": False,
                "price_eur": None,
            }
        )

    # Bucket -> portfolio holding the most of it (unmanaged first).
    bucket_value: Dict[Tuple[str, int], float] = {}
    for c in candidates:
        if c["portfolio_id"] is not None:
            key = (c["bucket"], c["portfolio_id"])
            bucket_value[key] = bucket_value.get(key, 0.0) + c["value_eur"]

    def home_portfolio(bucket: str) -> Tuple[Optional[int], Optional[str]]:
        options = [(v, pid) for (b, pid), v in bucket_value.items() if b == bucket]
        if not options:
            return None, None
        plain = [o for o in options if o[1] not in managed_ids]
        pid = max(plain or options)[1]
        return pid, portfolio_names.get(pid)

    for entry in db.get_watchlist():
        asset = db.get_asset_by_symbol(entry["symbol"])
        if not asset or asset["id"] in held_ids:
            continue
        price_row = db.get_latest_price(asset["id"])
        price = float(price_row["price"]) if price_row else 0.0
        if price <= 0:
            continue
        bucket = asset.get("asset_type") or entry.get("asset_type") or "other"
        pid, pname = home_portfolio(bucket)
        fund = is_fund(asset)
        fields = research_fields(asset["id"], asset["symbol"])
        if fields["buy_below"] is None:
            fields["buy_below"] = entry.get("buy_below")
        candidates.append(
            {
                "asset_id": asset["id"],
                "symbol": asset["symbol"],
                "name": asset.get("name") or asset["symbol"],
                "asset_type": bucket,
                "bucket": bucket,
                "portfolio_id": pid,
                "portfolio_name": pname,
                "value_eur": 0.0,
                "weight_pct": 0.0,
                "is_fund": fund,
                "ter_pct": _ter(db, asset["id"]) if fund else None,
                "overlap_group": None,
                "blocked": block_state(asset),
                "managed": False,
                "is_watchlist": True,
                "price_eur": price * fx(asset.get("currency") or "EUR"),
                **fields,
            }
        )

    candidates.sort(key=lambda c: (c["bucket"], -c["value_eur"], c["symbol"]))
    return candidates, warnings


def check_targets(targets: Dict[str, float], before: dict) -> List[str]:
    """Reasons the targets cannot drive an allocation; empty means fine.

    Args:
        targets: ``{asset_type: target_pct}``.
        before: ``compute_before_state``'s ``before`` dict.

    Returns:
        Human-readable reasons: targets not summing to 100, or a held type
        above 1 % of the portfolio with no target row (an explicit 0 is fine).
    """
    reasons: List[str] = []
    total = sum(targets.values())
    if abs(total - 100.0) > TARGET_SUM_TOLERANCE:
        reasons.append(f"Allocation targets sum to {total:.1f}%, not 100%.")
    for row in before.get("allocations", []):
        if row["current_pct"] > HELD_TYPE_MIN_PCT and row["asset_type"] not in targets:
            reasons.append(
                f"{row['asset_type']} is {row['current_pct']:.1f}% of the "
                "portfolio but has no target."
            )
    return reasons


def investable_targets(targets: Dict[str, float], candidates: List[dict]) -> dict:
    """Targets of buckets that can receive money, renormalised to 100.

    Drops ``cash`` and any bucket without a candidate.
    """
    buckets = {c["bucket"] for c in candidates}
    kept = {
        b: pct for b, pct in targets.items() if b != "cash" and b in buckets and pct > 0
    }
    total = sum(kept.values())
    if total <= 0:
        return {}
    return {b: pct / total * 100 for b, pct in kept.items()}


def _water_fill(
    amount: float,
    targets: Dict[str, float],
    gaps: Dict[str, float],
    eligible: List[str],
) -> Dict[str, float]:
    """Proportional-to-gap split capped at each gap; remainder by target."""
    alloc = {b: 0.0 for b in targets}
    positive = [b for b in eligible if gaps[b] > 0]
    if not positive:
        weight = sum(targets[b] for b in eligible)
        for b in eligible:
            alloc[b] = amount * targets[b] / weight
        return alloc
    gap_sum = sum(gaps[b] for b in positive)
    if amount <= gap_sum:
        for b in positive:
            alloc[b] = amount * gaps[b] / gap_sum
        return alloc
    weight = sum(targets[b] for b in positive)
    for b in positive:
        alloc[b] = gaps[b] + (amount - gap_sum) * targets[b] / weight
    return alloc


def split_buckets(
    amount_eur: float,
    targets: Dict[str, float],
    before_values: Dict[str, float],
    min_line_eur: float,
) -> List[dict]:
    """Split new money over buckets by water-filling (spec 5.2).

    Each bucket's gap is ``target/100 * (total + amount) - value``. The amount
    goes to underweight buckets in proportion to their gap, never above it,
    any remainder by target; an overweight bucket gets nothing. With no
    underweight bucket the amount follows the targets. Allocations under
    ``min_line_eur`` are dropped (smallest first) and their money is spread
    over the others by the same rule until stable. If every allocation is
    under the minimum, the largest-gap bucket keeps the whole amount.

    Args:
        amount_eur: Money to place.
        targets: Investable targets, summing to 100.
        before_values: Current EUR value per bucket (the denominator is their
            sum, so pass every bucket the mix is measured against).
        min_line_eur: Smallest worthwhile allocation.

    Returns:
        One dict per bucket in ``targets``: ``asset_type``, ``target_pct``,
        ``before_pct``, ``after_pct``, ``gap_eur``, ``allocated_eur``. The
        allocations sum exactly to ``amount_eur``.
    """
    buckets = [b for b, pct in targets.items() if pct > 0]
    if not buckets:
        return []
    total = sum(before_values.values())
    total_after = total + amount_eur
    gaps = {
        b: targets[b] / 100 * total_after - before_values.get(b, 0.0) for b in buckets
    }
    eligible = list(buckets)
    alloc = _water_fill(amount_eur, targets, gaps, eligible)
    while True:
        small = [b for b in eligible if 0 < alloc[b] < min_line_eur]
        if not small:
            break
        smallest = min(small, key=lambda b: alloc[b])
        remaining = [b for b in eligible if b != smallest]
        if not remaining:
            break
        eligible = remaining
        alloc = _water_fill(amount_eur, targets, gaps, eligible)
    if not any(alloc[b] > 0 for b in buckets):
        alloc[max(buckets, key=lambda b: gaps[b])] = amount_eur

    cents = {b: round(alloc[b], 2) for b in buckets}
    residue = round(amount_eur - sum(cents.values()), 2)
    if residue:
        largest = max(buckets, key=lambda b: cents[b])
        cents[largest] = round(cents[largest] + residue, 2)

    rows = []
    for b in buckets:
        before = before_values.get(b, 0.0)
        rows.append(
            {
                "asset_type": b,
                "target_pct": targets[b],
                "before_pct": before / total * 100 if total else 0.0,
                "after_pct": (
                    (before + cents[b]) / total_after * 100 if total_after else 0.0
                ),
                "gap_eur": round(gaps[b], 2),
                "allocated_eur": cents[b],
            }
        )
    return rows


def open_lots(
    db, holdings: List[dict], transactions: List[dict], asset_ids: set
) -> Tuple[Dict[int, List[dict]], Dict[int, float], List[str]]:
    """FIFO lots and EUR prices for the given assets, via the rebalance planner.

    Returns:
        ``(lots_by_asset, price_eur_by_asset, warnings)``.
    """
    chosen = [h for h in holdings if h["asset_id"] in asset_ids]
    gaps = [
        {"asset_type": t, "side": "SELL", "gap_eur": 0.0}
        for t in {h["asset_type"] for h in chosen}
    ]
    cands, warnings = build_sell_candidates(db, chosen, gaps, transactions=transactions)
    return (
        {c["asset_id"]: c["lots"] for c in cands},
        {c["asset_id"]: c["price_eur"] for c in cands},
        warnings,
    )


def _ter_key(candidate: dict) -> float:
    """Sort key where an unknown TER counts as the highest."""
    ter = candidate.get("ter_pct")
    return float("inf") if ter is None else ter


def _is_smaller_overlap_member(candidate: dict) -> bool:
    """Whether the fund overlaps a bigger one it should be merged into."""
    group = candidate.get("overlap_group")
    return bool(group) and group["largest_member_id"] != candidate["asset_id"]


def _fund_ref(candidate: dict) -> dict:
    return {
        "asset_id": candidate["asset_id"],
        "symbol": candidate["symbol"],
        "name": candidate["name"],
        "bucket": candidate["bucket"],
        "portfolio_id": candidate["portfolio_id"],
        "portfolio_name": candidate["portfolio_name"],
        "ter_pct": candidate["ter_pct"],
    }


def plan_traspasos(
    candidates: List[dict],
    targets: Dict[str, float],
    before_values: Dict[str, float],
    threshold_pp: float,
    lots_by_asset: Dict[int, List[dict]],
    price_eur_by_asset: Dict[int, float],
) -> Tuple[List[dict], Dict[str, float]]:
    """Fund-to-fund switches that rebalance without realising a gain (5.5).

    Only funds that are not ETFs and not managed portfolios take part. A
    bucket holding such funds that exceeds its target by more than
    ``threshold_pp`` gives money to a fund bucket below target: from its
    smaller overlapping fund, else its highest-TER fund (unknown TER counts
    as highest), to the lowest-TER fund of the receiving bucket.

    Args:
        candidates: ``build_candidates`` output.
        targets: ``{bucket: target_pct}``.
        before_values: Current EUR value per bucket.
        threshold_pp: Overweight (percentage points) that triggers a switch.
        lots_by_asset: Open FIFO lots per asset id (``unit_cost_eur``).
        price_eur_by_asset: EUR price per asset id.

    Returns:
        ``(traspasos, after_values)``.
    """
    after = dict(before_values)
    total = sum(before_values.values())
    if total <= 0:
        return [], after

    funds: Dict[str, List[dict]] = {}
    for c in candidates:
        if c["managed"] or not c["is_fund"] or c["asset_type"] == "etf":
            continue
        funds.setdefault(c["bucket"], []).append(c)

    def pct(bucket: str) -> float:
        return before_values.get(bucket, 0.0) / total * 100

    over = sorted(
        (b for b in funds if pct(b) - targets.get(b, 0.0) > threshold_pp),
        key=lambda b: before_values[b] - targets.get(b, 0.0) / 100 * total,
        reverse=True,
    )
    room = {
        b: targets[b] / 100 * total - before_values.get(b, 0.0)
        for b in funds
        if targets.get(b, 0.0) > 0 and pct(b) < targets[b]
    }
    traspasos: List[dict] = []
    given: Dict[int, float] = {}
    for src_bucket in over:
        holders = [c for c in funds[src_bucket] if c["value_eur"] > 0]
        if not holders:
            continue
        smaller = [c for c in holders if _is_smaller_overlap_member(c)]
        if smaller:
            source = min(smaller, key=lambda c: c["value_eur"])
            why = "the smaller of two funds that overlap"
        else:
            source = max(holders, key=lambda c: (_ter_key(c), c["value_eur"]))
            ter = source["ter_pct"]
            why = (
                "the fund with the highest TER " f"({ter:.2f}%)"
                if ter is not None
                else "a fund with unknown TER"
            )
        excess = before_values[src_bucket] - targets.get(src_bucket, 0.0) / 100 * total
        for dst_bucket in sorted(room, key=lambda b: room[b], reverse=True):
            if dst_bucket == src_bucket or room[dst_bucket] <= 0 or excess <= 0:
                continue
            targets_in = [
                c for c in funds[dst_bucket] if not _is_smaller_overlap_member(c)
            ]
            if not targets_in:
                continue
            dest = min(targets_in, key=lambda c: (_ter_key(c), -c["value_eur"]))
            left = source["value_eur"] - given.get(source["asset_id"], 0.0)
            amount = round(min(excess, room[dst_bucket], left), 2)
            if amount <= 0:
                continue
            price = price_eur_by_asset.get(source["asset_id"])
            lots = lots_by_asset.get(source["asset_id"])
            gain: Optional[float] = None
            if price and lots:
                gain = round(fifo_gain_eur(lots, amount / price, price), 2)
            if gain is None:
                tax = "the gain on selling this amount is unknown (no open lots)"
            elif gain >= 0:
                tax = (
                    "traspaso keeps the gain untaxed; selling this amount "
                    f"would realise {gain:,.0f} € of gain"
                )
            else:
                tax = (
                    "selling this amount would realise a loss of "
                    f"{-gain:,.0f} €; a traspaso defers it"
                )
            traspasos.append(
                {
                    "from": _fund_ref(source),
                    "to": _fund_ref(dest),
                    "amount_eur": amount,
                    "gain_eur": gain,
                    "reason": (
                        f"{src_bucket} is {pct(src_bucket):.1f}% against a "
                        f"{targets.get(src_bucket, 0.0):.1f}% target; the source "
                        f"is {why}; {tax}."
                    ),
                }
            )
            given[source["asset_id"]] = given.get(source["asset_id"], 0.0) + amount
            room[dst_bucket] -= amount
            excess -= amount
            after[src_bucket] = after.get(src_bucket, 0.0) - amount
            after[dst_bucket] = after.get(dst_bucket, 0.0) + amount
    return traspasos, after
