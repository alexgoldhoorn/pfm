"""Candidates, target checks, the bucket split and traspasos of the allocator.

Pure functions where possible: :func:`check_targets`,
:func:`investable_targets`, :func:`split_buckets` and :func:`plan_traspasos`
take plain dicts, so the arithmetic is testable without a database.
:func:`build_candidates` is the one that reads the database.
"""

from datetime import date
from itertools import zip_longest
from typing import Callable, Dict, List, Optional, Tuple

from portf_manager.services import deployable as deployable_service
from portf_manager.services.exposure import compute_exposure, find_fund_overlaps
from portf_manager.services.rebalance_planner import (
    build_sell_candidates,
    compute_before_state,
    fifo_gain_eur,
    merge_targets,
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
        ``is_watchlist``, ``price_eur``, ``price_native`` (the price in the
        asset's own ``currency``, the one a buy-below is written in).
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
            f"Antiaplicación check unavailable ({exc}); stocks are not proposed, "
            "and funds and ETFs are proposed without checking their window."
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
                "price_native": h.get("price"),
                "currency": h.get("currency") or "EUR",
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
                "price_native": None,
                "currency": None,
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
                "price_native": price,
                "currency": asset.get("currency") or "EUR",
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
    min_amount: float = 0.0,
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
        min_amount: Switches under this many EUR are not proposed.

    Returns:
        ``(traspasos, after_values)``. A fund blocked by the antiaplicación
        rule is never a destination (it may still be a source: a traspaso
        sells nothing).
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
                c
                for c in funds[dst_bucket]
                if not _is_smaller_overlap_member(c) and not c.get("blocked")
            ]
            if not targets_in:
                continue
            dest = min(targets_in, key=lambda c: (_ter_key(c), -c["value_eur"]))
            left = source["value_eur"] - given.get(source["asset_id"], 0.0)
            amount = round(min(excess, room[dst_bucket], left), 2)
            if amount <= 0 or amount < min_amount:
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


# ---- ranking and the proposal (spec 5.3, 5.6) ------------------------------

FUND_BUCKETS = ("etf", "mutual_fund", "index")
_CONVICTION_WORDS = {"low": 1.0, "medium": 2.0, "high": 3.0}


def _conviction(candidate: dict) -> float:
    """Numeric conviction of the research note; 0 when absent or unparsable."""
    value = candidate.get("conviction")
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return _CONVICTION_WORDS.get(str(value).strip().lower(), 0.0)


def fund_exclusions(cands: List[dict]) -> List[Tuple[dict, str]]:
    """Funds left out because a bigger fund in the same overlap group exists."""
    by_id = {c["asset_id"]: c for c in cands}
    out = []
    for c in cands:
        if _is_smaller_overlap_member(c):
            big = by_id.get(c["overlap_group"]["largest_member_id"])
            name = big["symbol"] if big else "a larger fund"
            out.append((c, f"overlaps with {name}, which is the bigger holding"))
    return out


def rank_fund_candidates(
    cands: List[dict],
) -> List[Tuple[dict, List[str]]]:
    """Funds and ETFs by lowest TER, then larger existing position (5.3).

    Non-largest members of an overlap group are excluded (see
    :func:`fund_exclusions`). A fund without TER ranks after those with one.
    Candidates carry no per-fund regions, so ties keep the larger position.
    """
    kept = [c for c in cands if not _is_smaller_overlap_member(c)]
    kept.sort(key=lambda c: (_ter_key(c), -c["value_eur"], c["symbol"]))
    ranked = []
    for c in kept:
        if c.get("ter_pct") is None:
            reasons = ["TER unknown, enter it on the Fees tab"]
        else:
            reasons = [f"TER {c['ter_pct']:.2f} %"]
            if c is kept[0]:
                reasons = [f"lowest TER in the bucket ({c['ter_pct']:.2f} %)"]
        if c.get("overlap_group"):
            reasons.append("the bigger holding of an overlapping group")
        if c["value_eur"] > 0:
            reasons.append("adds to an existing position")
        ranked.append((c, reasons))
    return ranked


def _under_buy_below(c: dict) -> bool:
    """Whether the native price is at or below the buy-below (same currency)."""
    return (
        c.get("buy_below") is not None
        and c.get("price_native") is not None
        and c["price_native"] <= c["buy_below"]
    )


def rank_stock_candidates(
    cands: List[dict],
    total_after: float,
    max_single_stock_pct: float,
    buy_eur: float = 0.0,
) -> Tuple[List[Tuple[dict, List[str]]], List[dict], List[Tuple[dict, str]]]:
    """Stocks: blocked and over-cap out, unresearched aside, the rest ranked.

    Returns:
        ``(ranked, needs_research, excluded)``; ``excluded`` holds
        ``(candidate, reason)`` for blocked and over-cap stocks.
    """
    ranked_in: List[dict] = []
    needs: List[dict] = []
    excluded: List[Tuple[dict, str]] = []
    cap = f"{max_single_stock_pct:g}"
    for c in cands:
        if c.get("blocked"):
            excluded.append((c, c["blocked"]["reason"]))
            continue
        rating = str(c.get("recommendation") or "").upper()
        if rating == "SELL":
            excluded.append((c, "your research says SELL"))
            continue
        if rating == "HOLD" and c.get("buy_below") is not None:
            if not _under_buy_below(c):
                excluded.append((c, "HOLD and above your buy-below"))
                continue
        after = (c["value_eur"] + buy_eur) / total_after * 100 if total_after else 0.0
        if after > max_single_stock_pct:
            excluded.append((c, f"would exceed the {cap} % single-stock cap"))
            continue
        if (
            c.get("buy_below") is None
            and not c.get("recommendation")
            and c.get("conviction") is None
        ):
            needs.append(c)
            continue
        ranked_in.append(c)

    under = _under_buy_below

    def is_buy(c: dict) -> bool:
        return str(c.get("recommendation") or "").upper() == "BUY"

    ranked_in.sort(
        key=lambda c: (
            not under(c),
            not is_buy(c),
            -_conviction(c),
            c["weight_pct"],
            c["symbol"],
        )
    )
    ranked = []
    for c in ranked_in:
        reasons = []
        if under(c):
            cur = c.get("currency") or "EUR"
            reasons.append(
                f"price {c['price_native']:,.2f} {cur} at or below your "
                f"buy-below of {c['buy_below']:,.2f} {cur}"
            )
        if is_buy(c):
            reasons.append("latest research says BUY")
        if _conviction(c) > 0:
            reasons.append(f"research conviction {c['conviction']}")
        reasons.append(f"weight {c['weight_pct']:.1f} % of the portfolio")
        ranked.append((c, reasons))
    return ranked, needs, excluded


def rank_crypto(cands: List[dict]) -> List[Tuple[dict, List[str]]]:
    """Held coins by current value; the split follows these weights."""
    held = [c for c in cands if c["value_eur"] > 0]
    held.sort(key=lambda c: (-c["value_eur"], c["symbol"]))
    return [(c, ["split in proportion to your current crypto weights"]) for c in held]


def rank_bonds(cands: List[dict]) -> List[Tuple[dict, List[str]]]:
    """Bonds; candidates carry no maturity, so the lightest holding first."""
    ordered = sorted(cands, key=lambda c: (c["weight_pct"], c["symbol"]))
    return [(c, ["no maturity data, so the lightest bond holding"]) for c in ordered]


def _rank_other(cands: List[dict]) -> List[Tuple[dict, List[str]]]:
    ordered = sorted(cands, key=lambda c: (c["weight_pct"], c["symbol"]))
    return [(c, ["lowest weight in the bucket"]) for c in ordered]


def _split_cents(amount: float, shares: List[float]) -> List[float]:
    """Split by shares to cents, the rounding residue on the first part."""
    total = sum(shares)
    parts = [round(amount * s / total, 2) for s in shares]
    parts[0] = round(parts[0] + amount - sum(parts), 2)
    return parts


def _why_not(kind: str, win: dict, lose: dict) -> str:
    """Why *lose* ranked below *win*."""
    if kind == "stock" and bool(win.get("is_fund")) != bool(lose.get("is_fund")):
        return "funds and stocks take turns in this bucket"
    if kind == "stock" and win.get("is_fund"):
        kind = "fund"
    if kind == "fund":
        wt, lt = win.get("ter_pct"), lose.get("ter_pct")
        if lt is None:
            return "TER unknown, enter it on the Fees tab"
        if wt is not None and lt > wt:
            return f"higher TER ({lt:.2f} % vs {wt:.2f} %)"
        return "smaller existing position"
    if kind == "stock":
        if _under_buy_below(win) and not _under_buy_below(lose):
            return "price above your buy-below"
        if _conviction(win) > _conviction(lose) or (
            str(win.get("recommendation") or "").upper() == "BUY"
            and str(lose.get("recommendation") or "").upper() != "BUY"
        ):
            return "weaker research"
        return "higher current weight"
    return "smaller holding"


def pick_lines(
    bucket: str,
    cands: List[dict],
    amount_eur: float,
    settings: dict,
    ctx: dict,
) -> Tuple[List[dict], float, List[dict], List[str]]:
    """Turn a bucket's amount into at most ``max_lines_per_bucket`` lines.

    Args:
        bucket: Asset type.
        cands: The bucket's candidates.
        amount_eur: Money for the bucket.
        settings: Allocator settings.
        ctx: ``total_after`` (portfolio value after all new money).

    Returns:
        ``(lines, unallocated_eur, needs_research, reasons)``; ``reasons``
        says why money stayed unallocated (empty when none did).
    """
    n = max(1, int(settings["max_lines_per_bucket"]))
    min_line = float(settings["min_line_eur"])
    needs: List[dict] = []
    extra: List[Tuple[dict, str]] = []
    kind = "fund"
    # a candidate blocked by the antiaplicación rule is never ranked
    blocked_out = [
        (c, f"{c['symbol']}: {c['blocked']['reason']}")
        for c in cands
        if c.get("blocked")
    ]
    cands = [c for c in cands if not c.get("blocked")]
    stock_ranked: List[Tuple[dict, List[str]]] = []
    if bucket in FUND_BUCKETS:
        ranked = rank_fund_candidates(cands)
        extra = fund_exclusions(cands)
    elif bucket == "stock":
        kind = "stock"
        # a fund typed "stock" is ranked fund-style, outside the stock rules
        fund_like = [c for c in cands if c["is_fund"]]
        stocks = [c for c in cands if not c["is_fund"]]
        stock_ranked, needs, extra = rank_stock_candidates(
            stocks, ctx["total_after"], settings["max_single_stock_pct"]
        )
        fund_ranked = rank_fund_candidates(fund_like)
        extra = extra + fund_exclusions(fund_like)
        # best of each list in turn, fund-style first
        ranked = []
        for pair in zip_longest(fund_ranked, stock_ranked):
            ranked.extend(item for item in pair if item is not None)
    elif bucket == "crypto":
        kind = "crypto"
        ranked = rank_crypto(cands)
    elif bucket == "bond":
        kind = "bond"
        ranked = rank_bonds(cands)
    else:
        kind = "other"
        ranked = _rank_other(cands)
    extra = extra + blocked_out
    needs_out = [
        {"symbol": c["symbol"], "weight_pct": round(c["weight_pct"], 2)} for c in needs
    ]
    if not ranked or amount_eur <= 0:
        left = round(amount_eur, 2) if amount_eur > 0 else 0.0
        why: List[str] = []
        if left:
            why = list(dict.fromkeys(r for _, r in extra))
            if not why:
                why = ["no researched candidate under the price target"]
        return [], left, needs_out, why

    picked = ranked[:n]
    runner = ranked[n] if len(ranked) > n else None

    def shares_of(items: List[Tuple[dict, List[str]]]) -> List[float]:
        if kind == "crypto":
            return [c["value_eur"] for c, _ in items]
        return [1.0] * len(items)

    chosen = list(picked)
    while True:
        parts = _split_cents(amount_eur, shares_of(chosen))
        if len(chosen) == 1 or min(parts) >= min_line:
            break
        # drop the smallest line, its money goes to the others
        small = min(range(len(chosen)), key=lambda i: parts[i])
        chosen.pop(small)
    dropped = [p for p in picked if p not in chosen]
    others = list(dropped) + ([runner] if runner is not None and not dropped else [])

    lines = []
    for (c, reasons), part in zip(chosen, parts):
        if dropped:
            ru = {
                "symbol": ", ".join(d[0]["symbol"] for d in dropped),
                "why_not": f"its share would be under {min_line:,.0f} €",
            }
        elif others:
            ru = {
                "symbol": others[0][0]["symbol"],
                "why_not": _why_not(kind, c, others[0][0]),
            }
        elif extra:
            ru = {"symbol": extra[0][0]["symbol"], "why_not": extra[0][1]}
        else:
            ru = None
        lines.append(
            {
                "asset_type": bucket,
                "asset_id": c["asset_id"],
                "symbol": c["symbol"],
                "name": c["name"],
                "portfolio_id": c["portfolio_id"],
                "portfolio_name": c["portfolio_name"],
                "amount_eur": part,
                "reasons": list(reasons),
                "runner_up": ru,
            }
        )
    unallocated = 0.0
    why = []
    if stock_ranked:
        unallocated = _clamp_stock_lines(
            lines, stock_ranked, settings, ctx, n, min_line
        )
        if unallocated:
            why = list(dict.fromkeys(r for _, r in extra))
            cap = (
                f"would exceed the {settings['max_single_stock_pct']:g} % "
                "single-stock cap"
            )
            if cap not in why:
                why.append(cap)
    return lines, unallocated, needs_out, why


def _clamp_stock_lines(
    lines: List[dict],
    ranked: List[Tuple[dict, List[str]]],
    settings: dict,
    ctx: dict,
    max_lines: int,
    min_line: float,
) -> float:
    """Keep every stock line within the single-stock cap, spilling the excess.

    Excess goes to the next ranked stock with room (a new line only while
    under ``max_lines`` and worth ``min_line``). Mutates *lines*.

    Returns:
        Money that found no room.
    """
    cap_eur = settings["max_single_stock_pct"] / 100 * ctx["total_after"]
    room = {c["asset_id"]: max(0.0, cap_eur - c["value_eur"]) for c, _ in ranked}
    excess = 0.0
    for ln in lines:
        # fund-style lines carry no single-stock cap
        if ln["asset_id"] not in room:
            continue
        if ln["amount_eur"] > room[ln["asset_id"]]:
            clipped = round(room[ln["asset_id"]], 2)
            excess += ln["amount_eur"] - clipped
            ln["amount_eur"] = clipped
    excess = round(excess, 2)
    for c, reasons in ranked:
        if excess <= 0:
            break
        have = next((ln for ln in lines if ln["asset_id"] == c["asset_id"]), None)
        free = round(room[c["asset_id"]] - (have["amount_eur"] if have else 0), 2)
        give = round(min(excess, free), 2)
        if give <= 0:
            continue
        if have is not None:
            have["amount_eur"] = round(have["amount_eur"] + give, 2)
        elif len(lines) < max_lines and give >= min_line:
            lines.append(
                {
                    "asset_type": "stock",
                    "asset_id": c["asset_id"],
                    "symbol": c["symbol"],
                    "name": c["name"],
                    "portfolio_id": c["portfolio_id"],
                    "portfolio_name": c["portfolio_name"],
                    "amount_eur": give,
                    "reasons": list(reasons),
                    "runner_up": None,
                }
            )
        else:
            continue
        excess = round(excess - give, 2)
    lines[:] = [ln for ln in lines if ln["amount_eur"] > 0]
    return excess


def _eur(value: float) -> str:
    return f"{value:,.2f} EUR"


def chat_summary(result: dict) -> str:
    """Plain-text rendering of a ``next_euro`` result, empty sections omitted."""
    out: List[str] = []
    dep = result.get("deployable") or {}
    if result.get("status") == "targets_incomplete":
        out.append("Allocation targets are incomplete:")
        out.extend(f"- {r}" for r in result.get("reasons", []))
    if dep.get("liquid_eur") is not None:
        out.append("Deployable")
        out.append(f"Liquid: {_eur(dep['liquid_eur'])}")
        out.append(
            f"Tax reserve: {_eur((dep.get('tax_reserve') or {}).get('reserve_eur', 0))}"
        )
        buf = dep.get("buffer") or {}
        if buf.get("target_eur") is not None:
            out.append(f"Buffer target: {_eur(buf['target_eur'])}")
        if dep.get("deployable_eur") is not None:
            out.append(f"Deployable: {_eur(dep['deployable_eur'])}")
    if result.get("status") == "nothing_to_deploy":
        out.append("Nothing to deploy right now.")
    if result.get("amount_eur"):
        out.append(f"Amount to place: {_eur(result['amount_eur'])}")
    if result.get("buckets"):
        out.append("")
        out.append("Split")
        for b in result["buckets"]:
            out.append(
                f"- {b['asset_type']}: {b['before_pct']:.1f}% -> "
                f"{b['after_pct']:.1f}% (target {b['target_pct']:.1f}%), "
                f"{_eur(b['allocated_eur'])}"
            )
    if result.get("lines"):
        out.append("")
        out.append("Proposed lines")
        for ln in result["lines"]:
            out.append(
                f"- {ln['symbol']} ({ln['name']}) in {ln['portfolio_name']}: "
                f"{_eur(ln['amount_eur'])}"
            )
            out.extend(f"    {r}" for r in ln["reasons"])
            if ln.get("runner_up"):
                ru = ln["runner_up"]
                out.append(f"    runner-up {ru['symbol']}: {ru['why_not']}")
    if result.get("unallocated_eur"):
        out.append(f"Unallocated: {_eur(result['unallocated_eur'])}")
    if result.get("traspasos"):
        out.append("")
        out.append("Traspasos")
        for t in result["traspasos"]:
            out.append(
                f"- {t['from_symbol']} -> {t['to_symbol']}: {_eur(t['amount_eur'])}"
                f" ({t['reason']})"
            )
    if result.get("blocked"):
        out.append("")
        out.append("Blocked")
        for b in result["blocked"]:
            out.append(f"- {b['symbol']}: {b['reason']}")
    if result.get("needs_research"):
        out.append("")
        out.append("Needs research")
        for n in result["needs_research"]:
            out.append(f"- {n['symbol']} ({n['weight_pct']:.1f}% of the portfolio)")
    if result.get("warnings"):
        out.append("")
        out.append("Warnings")
        out.extend(f"- {w}" for w in result["warnings"])
    return "\n".join(out)


def next_euro(
    db,
    amount_eur: Optional[float] = None,
    today: Optional[date] = None,
    fx: Optional[Callable[[str], float]] = None,
    fx_on: Optional[Callable] = None,
) -> dict:
    """The full proposal for the next euro (spec 5.6).

    Args:
        db: Database.
        amount_eur: Explicit amount; defaults to the deployable cash.
        today: Reference date.
        fx: ``fx(currency)`` rate to EUR.
        fx_on: ``fx_on(currency, day)`` historical rate.

    Returns:
        The response dict; ``status`` is ``ok``, ``nothing_to_deploy``,
        ``targets_incomplete`` or ``insufficient_data``.
    """
    today = today or date.today()
    # an explicit fx (tests) bypasses the 60 s deployable cache
    use_cache = fx is None and fx_on is None
    if fx is None or fx_on is None:
        from portf_server.routers.portfolios import _get_fx_rate, _get_fx_rate_on

        fx = fx or _get_fx_rate
        fx_on = fx_on or (lambda cur, day: _get_fx_rate_on(db, cur, day))
    settings = deployable_service.load_settings(db)
    if use_cache:
        dep = deployable_service.deployable_cached(db, today, fx, fx_on)
    else:
        dep = deployable_service.deployable(db, today, fx, fx_on, settings)
    warnings: List[str] = list(dep.get("warnings", []))
    result: dict = {
        "status": "ok",
        "deployable": dep,
        "amount_eur": 0.0,
        "buckets": [],
        "lines": [],
        "traspasos": [],
        "blocked": [],
        "needs_research": [],
        "unallocated_eur": 0.0,
        "warnings": warnings,
    }

    def done(status: str) -> dict:
        result["status"] = status
        result["chat_summary"] = chat_summary(result)
        return result

    if dep.get("status") == "insufficient_data" and amount_eur is None:
        warnings.append(dep.get("reason", "not enough data to size the buffer"))
        return done("insufficient_data")

    targets = merge_targets(db, None)
    # compute_before_state raises on targets that do not sum to 100
    early = check_targets(targets, {"allocations": []})
    if early:
        result["reasons"] = early
        return done("targets_incomplete")
    before, before_warnings, holdings, transactions = compute_before_state(
        db, None, None
    )
    warnings.extend(before_warnings)
    reasons = check_targets(targets, before)
    if reasons:
        result["reasons"] = reasons
        return done("targets_incomplete")

    deployable_eur = dep.get("deployable_eur") or 0.0
    amount = float(amount_eur) if amount_eur is not None else float(deployable_eur)
    if amount <= 0:
        return done("nothing_to_deploy")
    result["amount_eur"] = round(amount, 2)
    if amount_eur is not None and dep.get("status") == "insufficient_data":
        warnings.append(f"deployable unknown: {dep.get('reason', 'not enough data')}")
    elif amount_eur is not None and amount > deployable_eur + 0.005:
        warnings.append(
            f"amount exceeds deployable cash by {_eur(amount - deployable_eur)}"
        )

    candidates, cand_warnings = build_candidates(
        db, holdings, transactions, settings, today, fx, fx_on
    )
    warnings.extend(cand_warnings)
    result["blocked"] = [
        {
            "symbol": c["symbol"],
            "safe_from": c["blocked"].get("safe_from"),
            "loss_eur": c["blocked"].get("loss_eur"),
            "reason": c["blocked"]["reason"],
        }
        for c in candidates
        if c.get("blocked")
    ]

    before_values = {
        a["asset_type"]: a["current_value_eur"] for a in before["allocations"]
    }
    fund_ids = {
        c["asset_id"]
        for c in candidates
        if c["is_fund"] and not c["managed"] and c["asset_id"] is not None
    }
    lots, prices, lot_warnings = open_lots(db, holdings, transactions, fund_ids)
    warnings.extend(lot_warnings)
    traspasos, after_values = plan_traspasos(
        candidates,
        targets,
        before_values,
        settings["traspaso_threshold_pp"],
        lots,
        prices,
        min_amount=float(settings["min_line_eur"]),
    )
    result["traspasos"] = [
        {
            **t,
            "from_symbol": t["from"]["symbol"],
            "to_symbol": t["to"]["symbol"],
            "reasons": [t["reason"]],
        }
        for t in traspasos
    ]

    invest = investable_targets(targets, candidates)
    if not invest:
        result["unallocated_eur"] = round(amount, 2)
        warnings.append("no bucket with a target has anything to buy")
        return done("ok")
    invest_values = {b: after_values.get(b, 0.0) for b in invest}
    buckets = split_buckets(amount, invest, invest_values, settings["min_line_eur"])
    # before_pct is the mix as it stands now, before any traspaso
    now_total = sum(before_values.get(b, 0.0) for b in invest)
    for row in buckets:
        row["before_pct"] = (
            before_values.get(row["asset_type"], 0.0) / now_total * 100
            if now_total
            else 0.0
        )
    result["buckets"] = buckets
    total_after = sum(after_values.values()) + amount
    ctx = {"total_after": total_after}

    for b in buckets:
        if b["allocated_eur"] <= 0:
            continue
        in_bucket = [c for c in candidates if c["bucket"] == b["asset_type"]]
        lines, left, needs, why = pick_lines(
            b["asset_type"], in_bucket, b["allocated_eur"], settings, ctx
        )
        result["lines"].extend(lines)
        result["needs_research"].extend(needs)
        if left:
            detail = "; ".join(why) if why else "found no line"
            warnings.append(f"{b['asset_type']}: {_eur(left)} unallocated, {detail}")
    # whatever no line took, residue of the bucket split included
    placed = sum(ln["amount_eur"] for ln in result["lines"])
    result["unallocated_eur"] = round(amount - placed, 2)
    return done("ok")
