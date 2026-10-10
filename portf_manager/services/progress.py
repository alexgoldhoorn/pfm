"""Progress metrics for a long-term investor: where the money came from, how
each year went, what inflation and tax take, and whether the savings habit and
cash buffer are in place.

Pure functions over data the router gathers; every amount is in EUR.

* **Contributions vs growth** — net money put in (purchases − sale proceeds,
  each at its own date's FX) against market value, month by month. The gap is
  what the market added; early on most of a portfolio is your own savings.
* **Calendar-year and monthly returns** — the time-weighted return of each
  year / month (CFA Institute, GIPS 2020), the way funds report theirs.
* **Real return** — nominal return deflated by Spanish HICP with the Fisher
  relation ``(1 + r) / (1 + π) − 1`` (see ``inflation.py``).
* **Latent tax** — the IRPF savings-base tax due if everything were sold today,
  on top of this year's realised gains and income.
* **Savings rate and emergency fund** — from imported bank statements.
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Optional

from portf_manager.services.risk_metrics import flow_adjusted_returns

_FUND_NAME = re.compile(r"\b(fund|fondo|fonds|idx|index)\b", re.IGNORECASE)


def _d10(value: Any) -> str:
    return str(value or "")[:10]


def _month_end_key(year: int, month: int) -> str:
    """Last calendar day of the month as ``YYYY-MM-DD`` (string compare safe)."""
    return f"{year:04d}-{month:02d}-31"


def _months(first: date, last: date) -> list[tuple[int, int]]:
    out = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def contributions_vs_growth(
    eur_transactions: list[dict],
    snapshots: list[dict],
    current_value: float,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Net contributions and market value at each month end.

    Args:
        eur_transactions: transactions with ``total_amount`` already in EUR.
        snapshots: daily ``{snapshot_date, total_value_eur}``.
        current_value: today's market value of open positions (EUR).
        today: reference date.

    Returns:
        ``net_contributions_eur`` (purchases − sale proceeds), ``growth_eur``
        (value − net contributions = realised + unrealised gains), and
        ``months``: ``[{month, net_contributions_eur, value_eur}]`` where
        ``value_eur`` is the last snapshot of that month, or None.
    """
    today = today or date.today()
    flows = []
    for tx in eur_transactions:
        t = (tx.get("transaction_type") or "").lower()
        d = _d10(tx.get("transaction_date"))
        if not d or t not in ("buy", "sell"):
            continue
        amount = float(tx.get("total_amount") or 0)
        flows.append((d, amount if t == "buy" else -amount))
    if not flows:
        return {"net_contributions_eur": 0.0, "growth_eur": 0.0, "months": []}
    flows.sort()
    snaps = sorted(
        (_d10(s["snapshot_date"]), float(s["total_value_eur"] or 0)) for s in snapshots
    )

    months = []
    running = 0.0
    fi = si = 0
    for y, m in _months(date.fromisoformat(flows[0][0]), today):
        end = _month_end_key(y, m)
        while fi < len(flows) and flows[fi][0] <= end:
            running += flows[fi][1]
            fi += 1
        month_value = None
        while si < len(snaps) and snaps[si][0] <= end:
            if snaps[si][0][:7] == f"{y:04d}-{m:02d}":
                month_value = snaps[si][1]
            si += 1
        months.append(
            {
                "month": f"{y:04d}-{m:02d}",
                "net_contributions_eur": round(running, 2),
                "value_eur": round(month_value, 2) if month_value is not None else None,
            }
        )
    # The month in progress shows today's value, not its last snapshot.
    months[-1]["value_eur"] = round(current_value, 2)
    return {
        "net_contributions_eur": round(running, 2),
        "growth_eur": round(current_value - running, 2),
        "months": months,
    }


def calendar_returns(
    snapshots: list[dict], realised: dict[str, float], today: Optional[date] = None
) -> dict[str, Any]:
    """Time-weighted return per calendar year and per month, in %.

    A year's window runs from the last snapshot of the year before (or the
    first snapshot) to the year's last snapshot; ``partial`` marks a year that
    starts after the first week of January or is still running.

    Returns:
        ``{"years": [{year, return_pct, start, end, partial}],
        "months": {"YYYY": {"MM": pct}}}``.
    """
    today = today or date.today()
    ordered = sorted(snapshots, key=lambda s: _d10(s["snapshot_date"]))
    rets = flow_adjusted_returns(ordered, realised)
    if not rets:
        return {"years": [], "months": {}}
    dates = [_d10(s["snapshot_date"]) for s in ordered]
    # Each return's window starts at the snapshot before it.
    prev_of = dict(zip(dates[1:], dates[:-1]))

    by_year: dict[int, list[tuple[str, float]]] = {}
    by_month: dict[str, dict[str, float]] = {}
    for d, r in rets:
        by_year.setdefault(int(d[:4]), []).append((d, r))
        months = by_month.setdefault(d[:4], {})
        months[d[5:7]] = (1 + months.get(d[5:7], 0.0)) * (1 + r) - 1

    years = []
    for y in sorted(by_year):
        rs = by_year[y]
        start = prev_of[rs[0][0]]
        end = rs[-1][0]
        partial = start > f"{y}-01-07" and start[:4] == str(y)
        partial = partial or y == today.year
        years.append(
            {
                "year": y,
                "return_pct": round((math.prod(1 + r for _, r in rs) - 1) * 100, 2),
                "start": start,
                "end": end,
                "partial": partial,
            }
        )
    return {
        "years": years,
        "months": {
            y: {m: round(v * 100, 2) for m, v in sorted(ms.items())}
            for y, ms in sorted(by_month.items())
        },
    }


def close_at(closes: list[tuple[str, float]], day: str) -> Optional[float]:
    """Last close on or before *day* in ascending ``(date, close)`` pairs."""
    price = None
    for d, p in closes:
        if d <= day:
            price = p
        else:
            break
    return price


def window_return(
    closes: list[tuple[str, float]], start: str, end: str
) -> Optional[float]:
    """Price change between *start* and *end*, in %, or None."""
    p0, p1 = close_at(closes, start), close_at(closes, end)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return round((p1 / p0 - 1) * 100, 2)


def is_fund_like(asset: dict) -> bool:
    """True for a mutual/index fund, which Spain lets you switch without tax.

    ``asset_type`` isn't reliable (index funds imported from a broker CSV are
    often typed ``stock``), so the name and the PDT ``"Funds"`` exchange count
    too. ETFs are excluded: a traspaso doesn't apply to them.
    """
    kind = (asset.get("asset_type") or "").lower()
    if kind in ("mutual_fund", "index"):
        return True
    if kind in ("etf", "crypto", "cash"):
        return False
    if (asset.get("exchange") or "") == "Funds":
        return True
    return bool(_FUND_NAME.search(asset.get("name") or ""))


def savings_tax(capital_result: float, income: float) -> float:
    """Spanish savings-base IRPF on a capital result plus dividend/interest income.

    Capital gains and losses net together. A net capital loss offsets up to
    25% of the dividend/interest income (art. 49 LIRPF); the rest carries
    forward, which this ignores.
    """
    from portf_manager.services.analytics_service import irpf_savings_tax

    if capital_result >= 0:
        return irpf_savings_tax(capital_result + income)
    offset = min(income * 0.25, -capital_result)
    return irpf_savings_tax(max(income - offset, 0))


def latent_tax(
    positions: list[dict],
    realised_ytd: float,
    income_ytd: float,
) -> dict[str, Any]:
    """Tax due this year if every position were sold today.

    Args:
        positions: ``[{value_eur, cost_eur, fund}]`` for open positions.
        realised_ytd: gains already realised this year (EUR).
        income_ytd: dividends + interest this year (EUR).

    Returns:
        ``unrealised_gain_eur``, ``latent_tax_eur`` (the extra tax over what
        this year already owes; negative when net losses would lower it),
        ``after_tax_value_eur``, and ``fund_unrealised_gain_eur`` — gains in
        funds, which a traspaso can move without realising.
    """
    unrealised = sum(p["value_eur"] - p["cost_eur"] for p in positions)
    fund_gain = sum(p["value_eur"] - p["cost_eur"] for p in positions if p["fund"])
    value = sum(p["value_eur"] for p in positions)
    extra = savings_tax(realised_ytd + unrealised, income_ytd) - savings_tax(
        realised_ytd, income_ytd
    )
    return {
        "unrealised_gain_eur": round(unrealised, 2),
        "fund_unrealised_gain_eur": round(fund_gain, 2),
        "latent_tax_eur": round(extra, 2),
        "after_tax_value_eur": round(value - max(extra, 0.0), 2),
    }


def savings_and_buffer(
    rows: list[dict],
    cash_eur: Optional[float],
    today: Optional[date] = None,
    months: int = 12,
) -> dict[str, Any]:
    """Savings rate and emergency-fund cover from bank-statement rows.

    Only complete months that have at least one imported row count: the month
    in progress and months with nothing imported would read as "spent
    nothing", which flatters both numbers.

    Args:
        rows: non-transfer ``{date, amount_eur}`` rows (− out, + in).
        cash_eur: cash in bank accounts (and cash-type manual assets), or
            None when no account has a balance.
        today: reference date.
        months: how many complete months back to look.

    Returns:
        ``months_used``, ``income_eur``, ``spent_eur``, ``savings_rate_pct``,
        ``avg_monthly_spend_eur``, ``cash_eur``, ``emergency_months``.
    """
    today = today or date.today()
    this_month = today.strftime("%Y-%m")
    window = []
    y, m = today.year, today.month
    for _ in range(months):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        window.append(f"{y:04d}-{m:02d}")
    buckets: dict[str, dict[str, float]] = {}
    for r in rows:
        key = _d10(r.get("date"))[:7]
        if key == this_month or key not in window:
            continue
        b = buckets.setdefault(key, {"spent": 0.0, "income": 0.0})
        amt = float(r.get("amount_eur") or 0)
        if amt < 0:
            b["spent"] -= amt
        else:
            b["income"] += amt
    used = sorted(buckets)
    income = sum(b["income"] for b in buckets.values())
    spent = sum(b["spent"] for b in buckets.values())
    avg_spend = spent / len(used) if used else None
    return {
        "months_used": used,
        "income_eur": round(income, 2),
        "spent_eur": round(spent, 2),
        "savings_rate_pct": (
            round((income - spent) / income * 100, 1) if income > 0 else None
        ),
        "avg_monthly_spend_eur": round(avg_spend, 2) if avg_spend else None,
        "cash_eur": round(cash_eur, 2) if cash_eur is not None else None,
        "emergency_months": (
            round(cash_eur / avg_spend, 1)
            if avg_spend and cash_eur is not None
            else None
        ),
    }
