"""What-if scenarios on the household's own numbers.

Four questions, each answered from the imported bank statements, the balance
sheet and the held portfolio:

* ``job_loss`` — how many months the money lasts and what to cut first;
* ``income_change`` — what a lower (or higher) income does to the monthly net;
* ``house_purchase`` — the cash needed, where it comes from, the new monthly
  picture and the debt ratio;
* ``market_crash`` — the loss (``services.stress``) and how long the portfolio
  takes to get back to today's value.

The maths is pure and takes a ``baseline`` dict; ``household_baseline`` builds
that dict from the database. Spending is split by each category's
``essentiality`` (``fixed`` / ``flexible`` / ``optional``, inherited from the
nearest ancestor that has one). A category with none is ``unclassified`` and
is never cut: guessing would make the runway look longer than it is.
"""

from datetime import date
from typing import Any, Callable, Optional

from portf_manager.services.loans import monthly_payment

TIERS = ("fixed", "flexible", "optional")
UNCLASSIFIED = "unclassified"
# Categories that move money into savings rather than spend it.
SAVINGS_CATEGORIES = ("Invest", "House purchase")
MAX_MONTHS = 600
SERIES_MONTHS = 60
# A category is "irregular" when it had spending in at most this share of the
# months used, given at least this many months of data.
IRREGULAR_MAX_SHARE = 0.25
IRREGULAR_MIN_MONTHS = 6
DEFAULT_FLEXIBLE_CUT_PCT = 20.0
DEFAULT_BUFFER_MONTHS = 6
DEFAULT_PURCHASE_COSTS_PCT = 12.0
# Lenders in Spain commonly cap all loan payments near 35% of net income.
DEBT_RATIO_LIMIT_PCT = 35.0

# Approximate index price levels in USD, peak to trough and back to the peak.
HISTORICAL_CRASHES = [
    {
        "name": "Dot-com (S&P 500, 2000-2002)",
        "drop_pct": -49,
        "years_to_recover": 7.2,
    },
    {
        "name": "Dot-com (Nasdaq Composite, 2000-2002)",
        "drop_pct": -78,
        "years_to_recover": 15.0,
    },
    {
        "name": "Financial crisis (S&P 500, 2007-2009)",
        "drop_pct": -57,
        "years_to_recover": 5.5,
    },
    {"name": "Covid (S&P 500, 2020)", "drop_pct": -34, "years_to_recover": 0.5},
    {
        "name": "Inflation and rates (S&P 500, 2022)",
        "drop_pct": -25,
        "years_to_recover": 2.0,
    },
]


# ── spending tiers ──────────────────────────────────────────────────────────


def tier_of(name: Optional[str], tree: dict[str, dict]) -> str:
    """The category's essentiality, or its nearest ancestor's.

    Args:
        name: category name.
        tree: ``{name: row}`` of ``db.list_spending_categories_tree()`` rows.
    """
    seen = 0
    row = tree.get(name or "")
    while row and seen < 100:
        if row.get("essentiality") in TIERS:
            return row["essentiality"]
        row = tree.get(row.get("parent_name") or "")
        seen += 1
    return UNCLASSIFIED


def _descendants(tree: dict[str, dict], names: tuple[str, ...]) -> frozenset[str]:
    from portf_manager.services.retirement import expand_categories

    return expand_categories(list(tree.values()), list(names))


def complete_months(rows: list[dict], today: date, months: int) -> list[str]:
    """The last ``months`` complete calendar months that have imported rows."""
    window = []
    y, m = today.year, today.month
    for _ in range(months):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        window.append(f"{y:04d}-{m:02d}")
    present = {str(r.get("date"))[:7] for r in rows}
    return sorted(k for k in window if k in present)


def spending_profile(
    rows: list[dict],
    tree: dict[str, dict],
    today: date,
    months: int = 12,
    work_income: Optional[Callable[[dict], bool]] = None,
    exclude: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Average monthly income and spending by category and tier.

    Args:
        rows: non-transfer ``{date, amount_eur, category, merchant}`` rows.
        tree: ``{name: row}`` category tree with ``essentiality``.
        today: reference date; the month in progress never counts.
        months: how many complete months back to look.
        work_income: predicate marking an income row as pay from work; when
            None every income row counts as work income.
        exclude: spending categories left out (with their sub-categories),
            e.g. a one-off renovation; reported as ``excluded_monthly_eur``.

    A category is flagged ``irregular`` when its spending fell in at most a
    quarter of the months used (at least six months of data): a one-off or
    seasonal cost that a monthly average spreads thin.
    """
    used = complete_months(rows, today, months)
    n = len(used)
    savings = _descendants(tree, SAVINGS_CATEGORIES)
    wanted = [c.strip() for c in (exclude or []) if c and c.strip()]
    lowered = {name.lower(): name for name in tree}
    resolved = [lowered.get(c.lower(), c) for c in wanted]
    excluded = _descendants(tree, tuple(resolved)) if resolved else frozenset()
    income = work = saved = left_out = 0.0
    by_cat: dict[str, float] = {}
    active: dict[str, set[str]] = {}
    for r in rows:
        if str(r.get("date"))[:7] not in used:
            continue
        amt = float(r.get("amount_eur") or 0)
        cat = r.get("category") or "uncategorized"
        if amt > 0:
            income += amt
            if work_income is None or work_income(r):
                work += amt
        elif cat in savings:
            saved -= amt
        elif cat in excluded:
            left_out -= amt
        else:
            by_cat[cat] = by_cat.get(cat, 0.0) - amt
            active.setdefault(cat, set()).add(str(r.get("date"))[:7])
    div = n or 1
    categories = sorted(
        (
            {
                "name": name,
                "tier": tier_of(name, tree),
                "monthly_eur": round(total / div, 2),
                "months_with_spending": len(active[name]),
                "irregular": n >= IRREGULAR_MIN_MONTHS
                and len(active[name]) <= n * IRREGULAR_MAX_SHARE,
            }
            for name, total in by_cat.items()
        ),
        key=lambda c: -c["monthly_eur"],
    )
    by_tier = {t: 0.0 for t in (*TIERS, UNCLASSIFIED)}
    for c in categories:
        by_tier[c["tier"]] += c["monthly_eur"]
    return {
        "months_used": used,
        "income_monthly_eur": round(income / div, 2),
        "work_income_monthly_eur": round(work / div, 2),
        "other_income_monthly_eur": round((income - work) / div, 2),
        "spend_monthly_eur": round(sum(by_tier.values()), 2),
        "savings_transfers_monthly_eur": round(saved / div, 2),
        "by_tier_monthly_eur": {k: round(v, 2) for k, v in by_tier.items()},
        "categories": categories,
        "excluded_categories": sorted(resolved),
        "excluded_monthly_eur": round(left_out / div, 2),
    }


# ── money that can be drawn on ──────────────────────────────────────────────


def sale_tax(gain_eur: float) -> float:
    """Spanish savings-base tax on a realised gain (zero for a loss)."""
    from portf_manager.services.tax_rates import progressive_tax

    return progressive_tax(gain_eur) if gain_eur > 0 else 0.0


def investments_after_tax(
    investments_eur: float, unrealised_gain_eur: float, market_drop_pct: float = 0.0
) -> float:
    """What selling everything would leave, after a market drop and tax."""
    value = investments_eur * (1 - market_drop_pct / 100)
    gain = unrealised_gain_eur - (investments_eur - value)
    return max(value - sale_tax(gain), 0.0)


def gross_sale_for(
    net_needed: float, investments_eur: float, unrealised_gain_eur: float
) -> Optional[float]:
    """How much to sell so ``net_needed`` is left after tax.

    Assumes every euro sold carries the portfolio's average gain. Returns None
    when selling everything is not enough.
    """
    if net_needed <= 0:
        return 0.0
    if investments_eur <= 0:
        return None
    ratio = max(unrealised_gain_eur, 0.0) / investments_eur

    def net(sold: float) -> float:
        return sold - sale_tax(sold * ratio)

    if net(investments_eur) < net_needed:
        return None
    lo, hi = net_needed, investments_eur
    for _ in range(60):
        mid = (lo + hi) / 2
        if net(mid) < net_needed:
            lo = mid
        else:
            hi = mid
    return hi


def runway(
    cash_eur: float,
    reserve_eur: float,
    burn: Callable[[int], float],
) -> dict[str, Any]:
    """Months the money lasts while ``burn(month)`` euros leave each month.

    Cash is used first, then ``reserve_eur`` (investments after tax). A month
    counts when it can be paid in full.

    Returns:
        ``months_on_cash``, ``months_total`` (None = lasts beyond the 50-year
        horizon), ``sustainable`` (the money never runs down) and ``series``
        (total money left at the end of each month, first five years).
    """
    cash, reserve = float(cash_eur), float(reserve_eur)
    months_cash: Optional[int] = None
    months_total: Optional[int] = None
    series = [round(cash + reserve, 2)]
    for month in range(1, MAX_MONTHS + 1):
        need = burn(month)
        if need <= 0:
            cash -= need
        elif cash >= need:
            cash -= need
        else:
            if months_cash is None:
                months_cash = month - 1
            need -= cash
            cash = 0.0
            if reserve >= need:
                reserve -= need
            else:
                months_total = month - 1
                break
        if month <= SERIES_MONTHS:
            series.append(round(cash + reserve, 2))
    steady = burn(MAX_MONTHS) <= 0
    return {
        "months_on_cash": months_cash if months_cash is not None else months_total,
        "months_total": months_total,
        "sustainable": months_total is None and steady,
        "series": series,
    }


# ── job loss / income change ────────────────────────────────────────────────


def cut_plan(profile: dict, flexible_cut_pct: float) -> list[dict]:
    """What to cut first: optional categories in full, then flexible ones."""
    out = []
    for tier, pct in (("optional", 100.0), ("flexible", flexible_cut_pct)):
        for c in profile["categories"]:
            if c["tier"] != tier or c["monthly_eur"] <= 0 or pct <= 0:
                continue
            out.append(
                {
                    "category": c["name"],
                    "tier": tier,
                    "monthly_eur": c["monthly_eur"],
                    "cut_pct": pct,
                    "saves_monthly_eur": round(c["monthly_eur"] * pct / 100, 2),
                }
            )
    return out


def job_loss(
    base: dict,
    income_lost_monthly_eur: Optional[float] = None,
    benefit_monthly_eur: float = 0.0,
    benefit_months: int = 0,
    severance_eur: float = 0.0,
    flexible_cut_pct: float = DEFAULT_FLEXIBLE_CUT_PCT,
    market_drop_pct: float = 0.0,
) -> dict[str, Any]:
    """Runway after losing income, at three levels of belt-tightening.

    Args:
        base: ``household_baseline`` output.
        income_lost_monthly_eur: income that stops; default all work income.
        benefit_monthly_eur: unemployment benefit while it lasts.
        benefit_months: how many months the benefit is paid.
        severance_eur: one-off payment added to cash.
        flexible_cut_pct: how far flexible spending is trimmed in the leanest
            level.
        market_drop_pct: fall in the portfolio at the same time (a job loss
            in a recession), applied before investments are sold.

    Raises:
        ValueError: for out-of-range arguments.
    """
    if not 0 <= flexible_cut_pct <= 100:
        raise ValueError("flexible_cut_pct must be between 0 and 100")
    if not 0 <= market_drop_pct < 100:
        raise ValueError("market_drop_pct must be between 0 and 100")
    if benefit_monthly_eur < 0 or benefit_months < 0 or severance_eur < 0:
        raise ValueError("benefit and severance cannot be negative")
    profile = base["spending"]
    lost = (
        profile["work_income_monthly_eur"]
        if income_lost_monthly_eur is None
        else float(income_lost_monthly_eur)
    )
    if lost < 0:
        raise ValueError("income_lost_monthly_eur cannot be negative")
    lost = min(lost, profile["income_monthly_eur"])
    remaining = profile["income_monthly_eur"] - lost
    tiers = profile["by_tier_monthly_eur"]
    spend = profile["spend_monthly_eur"]
    levels_spend = {
        "as_today": spend,
        "no_optional": spend - tiers["optional"],
        "lean": spend - tiers["optional"] - tiers["flexible"] * flexible_cut_pct / 100,
    }
    cash = (base.get("liquid_eur") or 0.0) + severance_eur
    reserve = investments_after_tax(
        base.get("investments_eur") or 0.0,
        base.get("unrealised_gain_eur") or 0.0,
        market_drop_pct,
    )
    levels = {}
    for key, monthly in levels_spend.items():

        def burn(month: int, monthly: float = monthly) -> float:
            benefit = benefit_monthly_eur if month <= benefit_months else 0.0
            return monthly - remaining - benefit

        r = runway(cash, reserve, burn)
        levels[key] = {
            "monthly_spend_eur": round(monthly, 2),
            "monthly_gap_eur": round(monthly - remaining, 2),
            "months_on_cash": r["months_on_cash"],
            "months_with_investments": r["months_total"],
            "sustainable": r["sustainable"],
            "money_left_by_month_eur": r["series"],
        }
    warnings = list(base.get("warnings") or [])
    if tiers[UNCLASSIFIED] > 0:
        warnings.append(
            f"{round(tiers[UNCLASSIFIED], 2)} EUR a month sits in categories with "
            "no fixed/flexible/optional mark; it is treated as not cuttable. "
            "Mark those categories on the Spending page for a sharper answer."
        )
    return {
        "as_of": base["as_of"],
        "months_used": profile["months_used"],
        "income_monthly_eur": profile["income_monthly_eur"],
        "income_lost_monthly_eur": round(lost, 2),
        "income_remaining_monthly_eur": round(remaining, 2),
        "benefit_monthly_eur": benefit_monthly_eur,
        "benefit_months": benefit_months,
        "severance_eur": severance_eur,
        "cash_available_eur": round(cash, 2),
        "investments_after_tax_eur": round(reserve, 2),
        "market_drop_pct": market_drop_pct,
        "spend_by_tier_monthly_eur": tiers,
        "savings_transfers_stopped_monthly_eur": profile[
            "savings_transfers_monthly_eur"
        ],
        "levels": levels,
        "cut_first": cut_plan(profile, flexible_cut_pct),
        "loan_payments_monthly_eur": base.get("loan_payments_monthly_eur"),
        "warnings": warnings,
        "notes": (
            "Three levels: as_today keeps all spending, no_optional drops the "
            "optional categories, lean also trims flexible ones by "
            f"{flexible_cut_pct:g}%. Transfers into investments stop in every "
            "level and are not counted as spending. months_on_cash uses bank "
            "cash and liquid deposits; months_with_investments also sells the "
            "portfolio after tax (None = lasts more than 50 years). Loan "
            "payments are already inside the spending figures."
        ),
    }


def income_change(
    base: dict,
    change_pct: Optional[float] = None,
    new_income_monthly_eur: Optional[float] = None,
    plan_params: Optional[dict] = None,
    goals_planned_monthly_eur: Optional[float] = None,
    flexible_cut_pct: float = DEFAULT_FLEXIBLE_CUT_PCT,
) -> dict[str, Any]:
    """The monthly picture after income changes by a percentage or to a level.

    Raises:
        ValueError: when neither or both of the two inputs are given.
    """
    if (change_pct is None) == (new_income_monthly_eur is None):
        raise ValueError("give exactly one of change_pct or new_income_monthly_eur")
    profile = base["spending"]
    income = profile["income_monthly_eur"]
    new_income = (
        income * (1 + change_pct / 100)
        if change_pct is not None
        else float(new_income_monthly_eur)
    )
    if new_income < 0:
        raise ValueError("income cannot be negative")
    spend = profile["spend_monthly_eur"]

    def rate(inc: float) -> Optional[float]:
        return round((inc - spend) / inc * 100, 1) if inc > 0 else None

    net_before, net_after = income - spend, new_income - spend
    out: dict[str, Any] = {
        "as_of": base["as_of"],
        "months_used": profile["months_used"],
        "income_monthly_eur": income,
        "new_income_monthly_eur": round(new_income, 2),
        "change_monthly_eur": round(new_income - income, 2),
        "spend_monthly_eur": spend,
        "net_monthly_before_eur": round(net_before, 2),
        "net_monthly_after_eur": round(net_after, 2),
        "savings_rate_before_pct": rate(income),
        "savings_rate_after_pct": rate(new_income),
        "spend_by_tier_monthly_eur": profile["by_tier_monthly_eur"],
        "warnings": list(base.get("warnings") or []),
    }
    if goals_planned_monthly_eur is not None:
        out["goals_planned_monthly_eur"] = goals_planned_monthly_eur
        out["goals_still_fit"] = net_after >= goals_planned_monthly_eur
    if net_after < 0:
        lost = income - new_income
        shortfall = job_loss(
            base, income_lost_monthly_eur=lost, flexible_cut_pct=flexible_cut_pct
        )
        out["shortfall"] = {
            "levels": shortfall["levels"],
            "cut_first": shortfall["cut_first"],
        }
    else:
        cuts = cut_plan(profile, flexible_cut_pct)
        needed = max(net_before - net_after, 0.0)
        out["to_keep_saving_the_same"] = {
            "needed_monthly_eur": round(needed, 2),
            "available_from_cuts_monthly_eur": round(
                sum(c["saves_monthly_eur"] for c in cuts), 2
            ),
            "cut_first": cuts,
        }
    if plan_params:
        from portf_manager.services.wealth_scenarios import normalize_params, summarize

        before = normalize_params(plan_params)
        after = dict(before)
        after["stocks_contribution_monthly"] = max(
            before["stocks_contribution_monthly"] + min(net_after - net_before, 0.0),
            0.0,
        )
        s_before, s_after = summarize(before), summarize(after)
        out["plan_impact"] = {
            "contribution_before_monthly_eur": before["stocks_contribution_monthly"],
            "contribution_after_monthly_eur": round(
                after["stocks_contribution_monthly"], 2
            ),
            "milestones": [
                {
                    "year": b["year"],
                    "calendar_year": b["calendar_year"],
                    "net_worth_before_eur": b["net_worth_eur"],
                    "net_worth_after_eur": a["net_worth_eur"],
                }
                for b, a in zip(s_before["milestones"], s_after["milestones"])
            ],
            "note": (
                "The primary Wealth Simulator scenario re-run with the monthly "
                "contribution lowered by the income lost (never below zero)."
            ),
        }
    return out


# ── house purchase ──────────────────────────────────────────────────────────


def house_purchase(
    base: dict,
    price_eur: float,
    down_payment_pct: float = 20.0,
    rate_pct: float = 3.0,
    term_years: int = 30,
    purchase_costs_pct: float = DEFAULT_PURCHASE_COSTS_PCT,
    monthly_running_eur: float = 0.0,
    sale_proceeds_eur: float = 0.0,
    existing_loans_repaid: bool = False,
    rent_saved_monthly_eur: float = 0.0,
    buffer_months: int = DEFAULT_BUFFER_MONTHS,
) -> dict[str, Any]:
    """Buying a home: cash needed, where it comes from and the new month.

    Args:
        base: ``household_baseline`` output.
        price_eur: purchase price.
        down_payment_pct: share of the price paid up front.
        rate_pct: mortgage rate (nominal annual).
        term_years: mortgage term.
        purchase_costs_pct: taxes, notary and registry as a share of the
            price (an assumption: transfer tax differs by region and by new
            build or resale).
        monthly_running_eur: new monthly costs (property tax, community fees,
            insurance) on top of today's spending.
        sale_proceeds_eur: cash left from selling the current home, after
            repaying its mortgage.
        existing_loans_repaid: today's loan payments stop (the current
            mortgage is repaid from the sale).
        rent_saved_monthly_eur: rent that stops.
        buffer_months: months of the new spending kept in cash, not used for
            the purchase.

    Raises:
        ValueError: for out-of-range arguments.
    """
    if price_eur <= 0:
        raise ValueError("price_eur must be positive")
    if not 0 <= down_payment_pct <= 100:
        raise ValueError("down_payment_pct must be between 0 and 100")
    if not 0 <= rate_pct <= 50 or not 1 <= term_years <= 50:
        raise ValueError("rate_pct or term_years out of range")
    if not 0 <= purchase_costs_pct <= 30:
        raise ValueError("purchase_costs_pct must be between 0 and 30")
    if min(monthly_running_eur, sale_proceeds_eur, rent_saved_monthly_eur) < 0:
        raise ValueError("amounts cannot be negative")
    if buffer_months < 0:
        raise ValueError("buffer_months cannot be negative")
    profile = base["spending"]
    income, spend = profile["income_monthly_eur"], profile["spend_monthly_eur"]
    old_loans = base.get("loan_payments_monthly_eur") or 0.0
    freed = rent_saved_monthly_eur + (old_loans if existing_loans_repaid else 0.0)

    down = price_eur * down_payment_pct / 100
    costs = price_eur * purchase_costs_pct / 100
    cash_needed = max(down + costs - sale_proceeds_eur, 0.0)
    loan = price_eur - down
    months = term_years * 12
    payment = monthly_payment(loan, rate_pct, months)
    new_spend = spend + payment + monthly_running_eur - freed
    net_before, net_after = income - spend, income - new_spend

    liquid = base.get("liquid_eur") or 0.0
    invest = base.get("investments_eur") or 0.0
    gain = base.get("unrealised_gain_eur") or 0.0
    buffer = buffer_months * new_spend
    from_cash = min(max(liquid - buffer, 0.0), cash_needed)
    rest = cash_needed - from_cash
    sold = gross_sale_for(rest, invest, gain)
    shortfall = 0.0
    if sold is None:
        sold = invest
        shortfall = rest - investments_after_tax(invest, gain)
    tax = sold - (rest - shortfall) if sold else 0.0

    loans_after = payment + (0.0 if existing_loans_repaid else old_loans)
    ratio = round(loans_after / income * 100, 1) if income > 0 else None
    cap_payment = max(
        income * DEBT_RATIO_LIMIT_PCT / 100
        - (0.0 if existing_loans_repaid else old_loans),
        0.0,
    )
    r = rate_pct / 1200
    cap_loan = (
        cap_payment * months if r == 0 else cap_payment * (1 - (1 + r) ** -months) / r
    )
    financed = 1 - down_payment_pct / 100
    max_price_ratio = cap_loan / financed if financed > 0 else None

    liquid_after = liquid - from_cash
    flags = []
    if shortfall > 0:
        flags.append(
            f"Not enough money: {round(shortfall, 2)} EUR short even after "
            "selling every investment."
        )
    if ratio is not None and ratio > DEBT_RATIO_LIMIT_PCT:
        flags.append(
            f"Loan payments would be {ratio}% of income, above the "
            f"{DEBT_RATIO_LIMIT_PCT:g}% lenders usually accept."
        )
    if net_after < 0:
        flags.append("The month no longer closes: spending would exceed income.")
    if sold and not shortfall:
        flags.append(
            f"Needs selling {round(sold, 2)} EUR of investments "
            f"(about {round(tax, 2)} EUR tax on the gains)."
        )
    return {
        "as_of": base["as_of"],
        "months_used": profile["months_used"],
        "price_eur": price_eur,
        "down_payment_eur": round(down, 2),
        "purchase_costs_eur": round(costs, 2),
        "sale_proceeds_eur": sale_proceeds_eur,
        "cash_needed_eur": round(cash_needed, 2),
        "funding": {
            "from_cash_eur": round(from_cash, 2),
            "cash_buffer_kept_eur": round(min(buffer, liquid), 2),
            "investments_sold_eur": round(sold or 0.0, 2),
            "tax_on_sale_eur": round(max(tax, 0.0), 2),
            "shortfall_eur": round(shortfall, 2),
        },
        "mortgage": {
            "principal_eur": round(loan, 2),
            "rate_pct": rate_pct,
            "term_years": term_years,
            "monthly_payment_eur": round(payment, 2),
            "total_interest_eur": round(payment * months - loan, 2),
        },
        "monthly": {
            "income_eur": income,
            "spend_before_eur": spend,
            "spend_after_eur": round(new_spend, 2),
            "net_before_eur": round(net_before, 2),
            "net_after_eur": round(net_after, 2),
            "freed_eur": round(freed, 2),
            "running_costs_eur": monthly_running_eur,
        },
        "debt_ratio_pct": ratio,
        "debt_ratio_limit_pct": DEBT_RATIO_LIMIT_PCT,
        "max_price_at_debt_limit_eur": (
            round(max_price_ratio, 2) if max_price_ratio is not None else None
        ),
        "after": {
            "liquid_eur": round(liquid_after, 2),
            "investments_eur": round(invest - (sold or 0.0), 2),
            "emergency_months": (
                round(liquid_after / new_spend, 1) if new_spend > 0 else None
            ),
            "net_worth_change_eur": round(-costs - max(tax, 0.0), 2),
        },
        "flags": flags,
        "warnings": list(base.get("warnings") or []),
        "notes": (
            "purchase_costs_pct is an assumption (taxes, notary, registry); "
            "the down payment turns cash into home equity, so net worth falls "
            "only by the costs and the tax on investments sold. Cash is used "
            "down to the buffer first, then investments at the portfolio's "
            "average gain. Property value changes and tax deductions are not "
            "modelled."
        ),
    }


# ── market crash ────────────────────────────────────────────────────────────


def recovery_months(
    value_before: float,
    value_after: float,
    annual_return_pct: float,
    monthly_contribution_eur: float = 0.0,
) -> Optional[int]:
    """Months until the portfolio is back at ``value_before``.

    Contributions count towards the value, so this is "when do I see today's
    figure again", not "when has the market itself recovered". None when it
    takes longer than 50 years.
    """
    if value_after >= value_before:
        return 0
    monthly = (1 + annual_return_pct / 100) ** (1 / 12) - 1
    value = value_after
    for month in range(1, MAX_MONTHS + 1):
        value = value * (1 + monthly) + monthly_contribution_eur
        if value >= value_before:
            return month
    return None


def crash_recovery(
    value_before: float,
    loss_eur: float,
    expected_return_pct: float = 6.0,
    monthly_contribution_eur: float = 0.0,
) -> dict[str, Any]:
    """Recovery times for a loss, across a band of return assumptions."""
    if not -20 <= expected_return_pct <= 30:
        raise ValueError("expected_return_pct must be between -20 and 30")
    if monthly_contribution_eur < 0:
        raise ValueError("monthly_contribution_eur cannot be negative")
    after = value_before - loss_eur
    drop = loss_eur / value_before * 100 if value_before else 0.0
    rows = []
    for label, rate in (
        ("low", expected_return_pct - 2),
        ("expected", expected_return_pct),
        ("high", expected_return_pct + 2),
    ):
        rows.append(
            {
                "case": label,
                "annual_return_pct": rate,
                "months_without_contributions": recovery_months(
                    value_before, after, rate
                ),
                "months_with_contributions": recovery_months(
                    value_before, after, rate, monthly_contribution_eur
                ),
            }
        )
    return {
        "value_before_eur": round(value_before, 2),
        "value_after_eur": round(after, 2),
        "loss_eur": round(loss_eur, 2),
        "drop_pct": round(drop, 2),
        "gain_needed_to_recover_pct": (
            round((value_before / after - 1) * 100, 1) if after > 0 else None
        ),
        "monthly_contribution_eur": monthly_contribution_eur,
        "recovery": rows,
        "historical_reference": HISTORICAL_CRASHES,
        "notes": (
            "Recovery is the time until the portfolio shows today's value "
            "again at a steady annual return (a simplification: real "
            "recoveries are uneven). With contributions, new money counts. "
            "historical_reference gives approximate index price levels in "
            "USD, peak to trough and back to the old peak."
        ),
    }


# ── database wiring ─────────────────────────────────────────────────────────


def household_baseline(
    db: Any,
    today: Optional[date] = None,
    fx: Optional[Callable[[str], float]] = None,
    months: int = 12,
    exclude_categories: Optional[list[str]] = None,
) -> dict[str, Any]:
    """The household's own numbers every scenario starts from.

    Each source degrades on its own: a failing one leaves its figure None and
    adds a warning, so a scenario still answers with what is known.
    """
    from portf_manager.positions import compute_positions
    from portf_manager.services import loans as loan_service
    from portf_manager.services.balance_sheet import build_balance_sheet
    from portf_manager.services.deployable import load_settings, work_income_rows
    from portf_manager.services.exposure import _default_fx

    today = today or date.today()
    fx = fx or _default_fx
    warnings: list[str] = []
    index = today.year * 12 + today.month - 1 - months
    start = date(index // 12, index % 12 + 1, 1).isoformat()
    rows = [
        {
            "date": r["date"],
            "amount_eur": float(r["amount"]) * fx(r.get("currency") or "EUR"),
            "category": r.get("category"),
            "merchant": r.get("merchant"),
            "is_transfer": False,
        }
        for r in db.list_spending_transactions(start_date=start, is_transfer=False)
    ]
    tree = {row["name"]: row for row in db.list_spending_categories_tree()}
    work_pred: Optional[Callable[[dict], bool]] = None
    try:
        settings = load_settings(db)
        if settings.get("work_income_merchants"):
            work_ids = {id(r) for r in work_income_rows(rows, settings)}
            work_pred = lambda r: id(r) in work_ids  # noqa: E731
        else:
            warnings.append(
                "No work-income merchants are ticked in the allocator settings, "
                "so every income row counts as pay from work."
            )
    except Exception as e:
        warnings.append(f"Work income could not be told apart: {e}")
    profile = spending_profile(rows, tree, today, months, work_pred, exclude_categories)
    if not profile["months_used"]:
        warnings.append(
            "No complete month of bank statements is imported: spending and "
            "income read as zero."
        )
    irregular = [c["name"] for c in profile["categories"] if c["irregular"]]
    if irregular:
        warnings.append(
            "Irregular spending (in a quarter of the months or fewer) is "
            "averaged into the monthly figure: "
            + ", ".join(irregular[:8])
            + ". Re-run with exclude_categories to leave a one-off out."
        )
    if profile["excluded_categories"]:
        warnings.append(
            f"Left out on request: {', '.join(profile['excluded_categories'])} "
            f"({profile['excluded_monthly_eur']} EUR a month)."
        )
    if not profile["months_used"]:
        pass
    elif len(profile["months_used"]) < 6:
        warnings.append(
            f"Only {len(profile['months_used'])} complete months of bank "
            "statements: the monthly averages are rough."
        )

    out: dict[str, Any] = {
        "as_of": today.isoformat(),
        "spending": profile,
        "liquid_eur": None,
        "investments_eur": None,
        "unrealised_gain_eur": None,
        "net_worth_eur": None,
        "loans": [],
        "loan_payments_monthly_eur": 0.0,
    }
    try:
        sheet = build_balance_sheet(db, today=today, fx=fx)
        out["liquid_eur"] = sheet["totals"]["liquid_eur"]
        out["net_worth_eur"] = sheet["totals"]["net_worth_eur"]
        out["investments_eur"] = round(
            sum(
                g.get("total_eur") or 0.0
                for g in sheet["groups"]
                if g["key"] == "investments"
            ),
            2,
        )
    except Exception as e:
        warnings.append(f"Balance sheet unavailable: {e}")
    try:
        positions, _ = compute_positions(db.get_all_transactions())
        gain = 0.0
        for asset_id, pos in positions.items():
            if pos["quantity"] <= 0:
                continue
            asset = db.get_asset(asset_id)
            price = db.get_latest_price(asset_id)
            if not asset or not price:
                continue
            rate = fx(asset.get("currency") or "EUR")
            gain += (pos["quantity"] * float(price["price"]) - pos["cost"]) * rate
        out["unrealised_gain_eur"] = round(gain, 2)
    except Exception as e:
        warnings.append(f"Unrealised gains unavailable (tax on sales ignored): {e}")
    try:
        for loan in db.get_loans():
            out["loans"].append(
                {
                    "name": loan.get("name"),
                    "balance_eur": round(loan_service.balance_on(loan, today), 2),
                    "monthly_payment_eur": loan_service.payment_of(loan),
                }
            )
        out["loan_payments_monthly_eur"] = round(
            sum(ln["monthly_payment_eur"] for ln in out["loans"]), 2
        )
    except Exception as e:
        warnings.append(f"Loans unavailable: {e}")
    out["warnings"] = warnings
    return out


def _primary_plan_params(db: Any) -> Optional[dict]:
    from portf_manager.services.wealth_scenarios import params_from_json

    try:
        for row in db.get_wealth_scenarios():
            if row.get("is_primary"):
                return params_from_json(row.get("params"))
    except Exception:
        return None
    return None


def run_job_loss(
    db: Any, today: Optional[date] = None, fx: Optional[Callable] = None, **kwargs
) -> dict:
    """``job_loss`` on the live numbers."""
    exclude = kwargs.pop("exclude_categories", None)
    base = household_baseline(db, today, fx, exclude_categories=exclude)
    return job_loss(base, **kwargs)


def run_income_change(
    db: Any, today: Optional[date] = None, fx: Optional[Callable] = None, **kwargs
) -> dict:
    """``income_change`` on the live numbers, with goals and the primary plan."""
    from portf_manager.services.goals import goals_summary, list_goals_with_progress

    exclude = kwargs.pop("exclude_categories", None)
    base = household_baseline(db, today, fx, exclude_categories=exclude)
    planned = None
    try:
        planned = goals_summary(list_goals_with_progress(db, today or date.today()))[
            "planned_monthly_eur"
        ]
    except Exception:
        base["warnings"].append("Goals unavailable: goal fit not checked.")
    return income_change(
        base,
        plan_params=_primary_plan_params(db),
        goals_planned_monthly_eur=planned,
        **kwargs,
    )


def run_house_purchase(
    db: Any, today: Optional[date] = None, fx: Optional[Callable] = None, **kwargs
) -> dict:
    """``house_purchase`` on the live numbers."""
    exclude = kwargs.pop("exclude_categories", None)
    base = household_baseline(db, today, fx, exclude_categories=exclude)
    return house_purchase(base, **kwargs)


def run_market_crash(
    db: Any,
    drop_pct: Optional[float] = None,
    scenario: Optional[str] = None,
    expected_return_pct: float = 6.0,
    monthly_contribution_eur: Optional[float] = None,
    today: Optional[date] = None,
    fx: Optional[Callable] = None,
) -> dict:
    """A crash on the held portfolio plus the time to get back.

    Args:
        drop_pct: fall in all equities (positive number, e.g. 40); or
        scenario: a ``services.stress`` preset key. One of the two.
        expected_return_pct: annual return assumed during the recovery.
        monthly_contribution_eur: new money invested each month; defaults to
            the primary Wealth Simulator scenario's contribution, else the
            average monthly transfer into investments.

    Raises:
        ValueError: for a missing or unknown scenario or a bad drop.
    """
    from portf_manager.services.stress import SCENARIOS, run_stress

    if (drop_pct is None) == (scenario is None):
        raise ValueError("give exactly one of drop_pct or scenario")
    if scenario is not None:
        if scenario not in SCENARIOS:
            raise ValueError(
                f"unknown scenario '{scenario}'; one of {', '.join(SCENARIOS)}"
            )
        shocks = SCENARIOS[scenario]["shocks"]
        label = SCENARIOS[scenario]["label"]
    else:
        if not 0 < drop_pct < 100:
            raise ValueError("drop_pct must be between 0 and 100")
        shocks = {"asset_class": {"equity": -float(drop_pct)}}
        label = f"All equities -{drop_pct:g}%"
    stress = run_stress(db, shocks, fx=fx, today=today)
    source = "given"
    if monthly_contribution_eur is None:
        plan = _primary_plan_params(db)
        if plan:
            monthly_contribution_eur = plan["stocks_contribution_monthly"]
            source = "primary Wealth Simulator scenario"
        else:
            base = household_baseline(db, today, fx)
            monthly_contribution_eur = base["spending"]["savings_transfers_monthly_eur"]
            source = "average monthly transfer into investments"
    out = crash_recovery(
        stress["portfolio_value_eur"],
        stress["loss_eur"],
        expected_return_pct,
        monthly_contribution_eur,
    )
    out["scenario"] = label
    out["monthly_contribution_source"] = source
    for key in (
        "by_asset_type",
        "top_losers",
        "net_worth_before_eur",
        "net_worth_after_eur",
        "retirement_after",
        "unprofiled",
    ):
        if key in stress:
            out[key] = stress[key]
    return out
