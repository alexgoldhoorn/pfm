"""Year-end tax planner: harvestable losses, carry-forward and the deadline.

One implementation behind ``GET /analytics/tax-optimizer``, the chat tool
``get_year_end_tax``, the MCP ``year_end_tax`` tool and the Action Item
``check_year_end_tax``. Informational only, not tax advice.
"""

import logging
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Spain lets a capital loss be carried forward for four tax years.
CARRY_YEARS = 4
# Days a buy blocks a loss on a listed security (the endpoint's historic flag).
WASH_DAYS = 60
# Window of the Action Item and its trigger threshold.
ITEM_FROM = (11, 1)
ITEM_MIN_LOSS_EUR = 100.0
# A net capital loss may offset at most this share of dividend/interest income.
INCOME_OFFSET_SHARE = 0.25


def _year_result(db, yr: int) -> float:
    """Net realised capital result of one tax year in EUR (FIFO, tx-date FX)."""
    # Imported here: the router imports from portf_manager at module level.
    from portf_manager.tax_calculator import TaxCalculator
    from portf_server.routers.analytics import _lot_eur_amounts

    report = TaxCalculator(db).calculate_tax_report(
        user_id=1, start_date=date(yr, 1, 1), end_date=date(yr, 12, 31)
    )
    total = 0.0
    for sym, txns in report.items():
        asset = db.get_asset_by_symbol(sym)
        currency = ((asset or {}).get("currency") or "EUR").upper()
        for t in txns:
            proceeds_eur, cost_eur = _lot_eur_amounts(db, currency, t)
            total += proceeds_eur - cost_eur
    return total


def carry_forward_losses(results: dict[int, float], year: int) -> dict[int, float]:
    """Unused losses from the four years before ``year``.

    Years are walked oldest first: a gain consumes the oldest still-valid
    losses, a loss joins the pool, and a loss expires after four years.

    Args:
        results: Net capital result per tax year (negative = loss).
        year: The tax year being planned.

    Returns:
        ``{origin_year: remaining_loss_eur}`` (positive amounts), only the
        non-zero entries still usable in ``year``.
    """
    pool: dict[int, float] = {}
    for yr in sorted(y for y in results if year - CARRY_YEARS <= y < year):
        net = results[yr]
        if net < 0:
            pool[yr] = pool.get(yr, 0.0) - net
            continue
        remaining = net
        for origin in sorted(pool):
            if remaining <= 0:
                break
            if origin < yr - CARRY_YEARS:
                continue
            used = min(pool[origin], remaining)
            pool[origin] -= used
            remaining -= used
    return {y: round(v, 2) for y, v in pool.items() if v > 0.005}


def _base(realised: float, income: float) -> float:
    """Savings base after netting a capital loss against up to 25% of income."""
    if realised >= 0:
        return realised + income
    return max(income - min(income * INCOME_OFFSET_SHARE, -realised), 0.0)


def _apply_carry(realised: float, income: float, carry: float) -> float:
    """Savings base once the carried losses are used where the law allows.

    Carried capital losses offset a positive capital result first; against
    income they can only use what is left of the 25% allowance.
    """
    base = _base(realised, income)
    if carry <= 0:
        return base
    if realised > 0:
        on_gains = min(carry, realised)
        on_income = min(carry - on_gains, income * INCOME_OFFSET_SHARE)
        return base - on_gains - on_income
    allowance = income * INCOME_OFFSET_SHARE - min(
        income * INCOME_OFFSET_SHARE, -realised
    )
    return base - min(carry, max(allowance, 0.0))


def _wash_blocked(db, candidates: list[dict], today: date, notes: list) -> list[dict]:
    """Candidates whose harvest a buy inside the antiaplicación window blocks."""
    from portf_manager.services.wash_sale import analyse
    from portf_server.routers.analytics import _build_tax_report_data
    from portf_server.routers.portfolios import get_holdings

    try:
        lots: list = []
        for yr in (today.year - 1, today.year):
            lots.extend(_build_tax_report_data(db, yr, strict=True)["realised_lots"])
        assets = {a["symbol"]: a for a in db.get_all_assets() if a.get("symbol")}
        holdings = get_holdings(portfolio_id=None, database=db)["holdings"]
        recent = analyse(lots, db.get_all_transactions(), assets, today, None, holdings)
    except Exception as e:
        logger.warning("Year-end tax wash-sale check failed: %s", e)
        notes.append(f"wash-sale check unavailable ({e}); only the 60-day flag applies")
        return []
    by_symbol = {str(r["symbol"]).upper(): r for r in recent["recent_buys"]}
    out = []
    for c in candidates:
        hit = by_symbol.get(str(c["symbol"]).upper())
        if hit and hit.get("at_loss", True):
            out.append(
                {
                    "symbol": c["symbol"],
                    "last_buy": hit.get("last_buy"),
                    "until": hit.get("until"),
                }
            )
    return out


def compute_year_end_tax(
    db, year: Optional[int] = None, today: Optional[date] = None
) -> dict:
    """Realised result, harvestable losses, carry-forward and deadline.

    Args:
        db: Database.
        year: Tax year, default the current one.
        today: Injectable clock.

    Returns:
        The ``/analytics/tax-optimizer`` payload.
    """
    from portf_manager.services.progress import savings_tax
    from portf_server.routers.analytics import (
        _compute_positions,
        _fx,
        _lot_eur_amounts,
        _savings_income_eur,
    )
    from portf_manager.tax_calculator import TaxCalculator

    today = today or date.today()
    yr = year or today.year
    notes: list[str] = []

    realised_gain = 0.0
    try:
        report = TaxCalculator(db).calculate_tax_report(
            user_id=1, start_date=date(yr, 1, 1), end_date=date(yr, 12, 31)
        )
        for sym, txns in report.items():
            a = db.get_asset_by_symbol(sym)
            currency = ((a or {}).get("currency") or "EUR").upper()
            for t in txns:
                proceeds_eur, cost_eur = _lot_eur_amounts(db, currency, t)
                realised_gain += proceeds_eur - cost_eur
    except Exception as e:
        logger.warning(f"Tax optimizer realised calc failed: {e}")

    div_this_year, interest_this_year = _savings_income_eur(
        db, db.get_all_transactions(), yr
    )
    income = div_this_year + interest_this_year

    positions, _ = _compute_positions(db)
    candidates = []
    harvestable = 0.0
    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        pd_ = db.get_latest_price(aid)
        price = float(pd_["price"]) if pd_ else 0.0
        gain = (pos["quantity"] * price - pos["cost"]) * _fx(
            asset.get("currency", "EUR")
        )
        if gain >= -1:
            continue
        last_buy = None
        for tx in db.get_transactions_by_asset(aid):
            if (tx.get("transaction_type") or "").lower() == "buy":
                d = str(tx.get("transaction_date", ""))[:10]
                if d and (last_buy is None or d > last_buy):
                    last_buy = d
        wash = False
        if last_buy:
            try:
                wash = (
                    today - datetime.strptime(last_buy, "%Y-%m-%d").date()
                ).days < WASH_DAYS
            except ValueError:
                wash = False
        if not wash:
            harvestable += gain
        candidates.append(
            {
                "symbol": asset["symbol"],
                "name": asset.get("name", asset["symbol"]),
                "quantity": round(pos["quantity"], 4),
                "unrealised_loss_eur": round(gain, 2),
                "last_buy": last_buy,
                "wash_sale_risk": wash,
                # Marginal saving of harvesting this one position on its own.
                "tax_saved_eur": round(
                    savings_tax(realised_gain, income)
                    - savings_tax(realised_gain + gain, income),
                    2,
                ),
                "can_harvest_until": f"{yr}-12-31",
            }
        )
    candidates.sort(key=lambda x: x["unrealised_loss_eur"])

    tax_current = savings_tax(realised_gain, income)
    tax_after = savings_tax(realised_gain + harvestable, income)

    # Losses of the four previous years that were never offset.
    results: dict[int, float] = {}
    for py in range(yr - CARRY_YEARS, yr):
        try:
            results[py] = _year_result(db, py)
        except Exception as e:
            logger.warning(f"Year-end tax result for {py} failed: {e}")
            notes.append(f"{py} result unavailable; carry-forward may be understated")
    by_year = carry_forward_losses(results, yr)
    carry_total = round(sum(by_year.values()), 2)
    notes.append(
        "carry-forward assumes the losses were declared; income offsets already "
        "used in earlier years are not modelled"
    )

    year_end = date(yr, 12, 31)
    return {
        "year": yr,
        "realised_gain_eur": round(realised_gain, 2),
        "dividend_income_eur": round(div_this_year, 2),
        "interest_income_eur": round(interest_this_year, 2),
        "income_eur": round(income, 2),
        "harvestable_loss_eur": round(harvestable, 2),
        "estimated_tax_now_eur": tax_current,
        "estimated_tax_after_harvest_eur": tax_after,
        "estimated_tax_saved_eur": round(tax_current - tax_after, 2),
        "candidates": candidates,
        "note": (
            "Estimate, not tax advice. Spain disallows a loss if you hold/rebuy "
            "the same security within 2 months — candidates bought in the last "
            "60 days are flagged; avoid rebuying harvested positions for 2 months."
        ),
        "carry_forward": {"total_eur": carry_total, "by_year": by_year},
        "base_after_carry_forward_eur": round(
            max(_apply_carry(realised_gain, income, carry_total), 0.0), 2
        ),
        "deadline": {
            "date": year_end.isoformat(),
            "days_left": max((year_end - today).days, 0),
        },
        "wash_blocked": _wash_blocked(db, candidates, today, notes),
        "notes": notes,
    }


def check_year_end_tax(db, today: Optional[date] = None) -> list[dict]:
    """Action Item from 1 November to 31 December: losses worth harvesting."""
    today = today or date.today()
    if (today.month, today.day) < ITEM_FROM:
        return []
    data = compute_year_end_tax(db, today.year, today)
    harvestable = -data["harvestable_loss_eur"]
    gains = max(data["realised_gain_eur"], 0.0)
    carry = data["carry_forward"]["total_eur"]
    if not (harvestable > ITEM_MIN_LOSS_EUR or (gains > 0 and carry > 0)):
        return []
    losses = harvestable + carry
    if gains > 0:
        title = (
            f"Year-end tax check: €{losses:,.0f} of losses could offset "
            f"€{gains:,.0f} of gains"
        )
    else:
        title = (
            f"Year-end tax check: €{losses:,.0f} of losses could be "
            "realised before 31 December"
        )
    return [
        {
            "id": f"tax:year-end:{today.year}",
            "category": "tax",
            "severity": "medium",
            "title": title,
            "detail": (
                f"{data['deadline']['days_left']} days left. Harvestable "
                f"€{harvestable:,.0f}, carried-forward €{carry:,.0f}, estimated "
                f"saving €{data['estimated_tax_saved_eur']:,.0f}. Mind the "
                "2-month rule before rebuying."
            ),
            "link_page": "analytics",
            "context": {
                "year": today.year,
                "harvestable_loss_eur": data["harvestable_loss_eur"],
                "realised_gain_eur": data["realised_gain_eur"],
                "carry_forward_eur": carry,
            },
        }
    ]
