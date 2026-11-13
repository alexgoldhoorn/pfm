"""What-if scenarios: job loss, income change, house purchase, market crash."""

from typing import Optional

from pfm_mcp import client, tool
from pfm_mcp.client import _fmt_currency

_LEVELS = (
    ("as_today", "Spending as today"),
    ("no_optional", "Without optional spending"),
    ("lean", "Lean (optional cut, flexible trimmed)"),
)


def _months(value) -> str:
    return "more than 50 years" if value is None else f"{value} months"


def _warnings(data: dict) -> list[str]:
    return [f"  ! {w}" for w in data.get("warnings") or []]


def _cuts(cuts: list) -> list[str]:
    lines = []
    for c in (cuts or [])[:15]:
        lines.append(
            f"  {c.get('category', '?'):<28} {c.get('tier', ''):<9} "
            f"-{c.get('cut_pct', 0):g}% saves "
            f"{_fmt_currency(c.get('saves_monthly_eur', 0))}/month"
        )
    return lines


@tool
def scenario_job_loss(
    income_lost_monthly_eur: Optional[float] = None,
    benefit_monthly_eur: float = 0.0,
    benefit_months: int = 0,
    severance_eur: float = 0.0,
    flexible_cut_pct: float = 20.0,
    market_drop_pct: float = 0.0,
    exclude_categories: Optional[str] = None,
) -> str:
    """
    What if income from work stops: months the money lasts at three spending
    levels (as today, without optional spending, lean), on cash and with
    investments sold after tax, and which categories to cut first. Uses the
    imported bank statements, balances and the portfolio. Read-only.

    Args:
        income_lost_monthly_eur: Monthly income that stops; default all work income.
        benefit_monthly_eur: Unemployment benefit per month while it lasts.
        benefit_months: Months the benefit is paid.
        severance_eur: One-off severance payment.
        flexible_cut_pct: Trim of flexible spending in the lean level (default 20).
        market_drop_pct: Portfolio fall at the same time (default 0).
        exclude_categories: Comma-separated spending categories to leave out
            (sub-categories included), e.g. a one-off renovation.
    """
    params = {
        "income_lost_monthly_eur": income_lost_monthly_eur,
        "benefit_monthly_eur": benefit_monthly_eur,
        "benefit_months": benefit_months,
        "severance_eur": severance_eur,
        "flexible_cut_pct": flexible_cut_pct,
        "market_drop_pct": market_drop_pct,
        "exclude_categories": exclude_categories,
    }
    try:
        data = client._get("/api/v1/scenarios/job-loss", params)
    except Exception as e:
        return f"Error running job-loss scenario: {e}"

    lines = [f"JOB LOSS SCENARIO (as of {data.get('as_of', '?')}):"]
    lines.append(
        f"  Income lost: {_fmt_currency(data.get('income_lost_monthly_eur', 0))}/month, "
        f"remaining {_fmt_currency(data.get('income_remaining_monthly_eur', 0))}/month"
    )
    lines.append(
        f"  Cash {_fmt_currency(data.get('cash_available_eur', 0))}, investments "
        f"after tax {_fmt_currency(data.get('investments_after_tax_eur', 0))}"
    )
    levels = data.get("levels") or {}
    for key, label in _LEVELS:
        lv = levels.get(key) or {}
        if lv.get("sustainable"):
            lines.append(
                f"  {label}: {_fmt_currency(lv.get('monthly_spend_eur', 0))}/month, "
                "income still covers it"
            )
            continue
        lines.append(
            f"  {label}: {_fmt_currency(lv.get('monthly_spend_eur', 0))}/month, gap "
            f"{_fmt_currency(lv.get('monthly_gap_eur', 0))}; cash lasts "
            f"{_months(lv.get('months_on_cash'))}, with investments "
            f"{_months(lv.get('months_with_investments'))}"
        )
    cuts = _cuts(data.get("cut_first"))
    if cuts:
        lines.append("  Cut first:")
        lines.extend("  " + c for c in cuts)
    lines.extend(_warnings(data))
    return "\n".join(lines)


@tool
def scenario_income_change(
    change_pct: Optional[float] = None,
    new_income_monthly_eur: Optional[float] = None,
    flexible_cut_pct: float = 20.0,
    exclude_categories: Optional[str] = None,
) -> str:
    """
    What if household income changes (pay cut, part-time, a raise): monthly
    net and savings rate before and after, whether the goals' contributions
    still fit, the effect on the primary wealth plan and the cuts that would
    close a gap. Give exactly one of the two inputs. Read-only.

    Args:
        change_pct: Change in total income in percent, e.g. -30.
        new_income_monthly_eur: The new total monthly income.
        flexible_cut_pct: How far flexible spending could be trimmed (default 20).
        exclude_categories: Comma-separated spending categories to leave out.
    """
    params = {
        "change_pct": change_pct,
        "new_income_monthly_eur": new_income_monthly_eur,
        "flexible_cut_pct": flexible_cut_pct,
        "exclude_categories": exclude_categories,
    }
    try:
        data = client._get("/api/v1/scenarios/income-change", params)
    except Exception as e:
        return f"Error running income-change scenario: {e}"

    def rate(value) -> str:
        return "n/a" if value is None else f"{value:.1f}%"

    lines = [f"INCOME CHANGE SCENARIO (as of {data.get('as_of', '?')}):"]
    lines.append(
        f"  Income: {_fmt_currency(data.get('income_monthly_eur', 0))} → "
        f"{_fmt_currency(data.get('new_income_monthly_eur', 0))}/month"
    )
    lines.append(
        f"  Net: {_fmt_currency(data.get('net_monthly_before_eur', 0))} → "
        f"{_fmt_currency(data.get('net_monthly_after_eur', 0))}/month "
        f"(savings rate {rate(data.get('savings_rate_before_pct'))} → "
        f"{rate(data.get('savings_rate_after_pct'))})"
    )
    if "goals_still_fit" in data:
        fit = "still fit" if data["goals_still_fit"] else "no longer fit"
        lines.append(
            f"  Goal contributions "
            f"({_fmt_currency(data.get('goals_planned_monthly_eur', 0))}/month) {fit}"
        )
    short = data.get("shortfall")
    if short:
        lines.append("  Spending now exceeds income:")
        for key, label in _LEVELS:
            lv = (short.get("levels") or {}).get(key) or {}
            lines.append(
                f"    {label}: cash lasts {_months(lv.get('months_on_cash'))}, with "
                f"investments {_months(lv.get('months_with_investments'))}"
            )
        cuts = _cuts(short.get("cut_first"))
    else:
        keep = data.get("to_keep_saving_the_same") or {}
        lines.append(
            f"  To keep saving the same: "
            f"{_fmt_currency(keep.get('needed_monthly_eur', 0))}/month needed, "
            f"{_fmt_currency(keep.get('available_from_cuts_monthly_eur', 0))} "
            "available from cuts"
        )
        cuts = _cuts(keep.get("cut_first"))
    if cuts:
        lines.append("  Cut first:")
        lines.extend("  " + c for c in cuts)
    impact = data.get("plan_impact")
    if impact:
        lines.append(
            f"  Wealth plan contribution: "
            f"{_fmt_currency(impact.get('contribution_before_monthly_eur', 0))} → "
            f"{_fmt_currency(impact.get('contribution_after_monthly_eur', 0))}/month"
        )
        for m in impact.get("milestones") or []:
            lines.append(
                f"    {m.get('calendar_year')}: net worth "
                f"{_fmt_currency(m.get('net_worth_before_eur', 0))} → "
                f"{_fmt_currency(m.get('net_worth_after_eur', 0))}"
            )
    lines.extend(_warnings(data))
    return "\n".join(lines)


@tool
def scenario_house_purchase(
    price_eur: float,
    down_payment_pct: float = 20.0,
    rate_pct: float = 3.0,
    term_years: int = 30,
    purchase_costs_pct: float = 12.0,
    monthly_running_eur: float = 0.0,
    sale_proceeds_eur: float = 0.0,
    existing_loans_repaid: bool = False,
    rent_saved_monthly_eur: float = 0.0,
    buffer_months: int = 6,
    exclude_categories: Optional[str] = None,
) -> str:
    """
    What if a home is bought: cash needed, how much comes from cash and from
    selling investments (with tax), the mortgage payment, the month before
    and after, loan payments against the 35% of income lenders use, and what
    is left afterwards. Rate and purchase costs are assumptions. Read-only.

    Args:
        price_eur: Purchase price.
        down_payment_pct: Down payment as % of the price (default 20).
        rate_pct: Mortgage rate, % a year (default 3).
        term_years: Mortgage term (default 30).
        purchase_costs_pct: Taxes, notary, registry as % of price (default 12).
        monthly_running_eur: New monthly costs (property tax, fees, insurance).
        sale_proceeds_eur: Cash left from selling the current home.
        existing_loans_repaid: True when today's loan payments stop.
        rent_saved_monthly_eur: Rent that stops.
        buffer_months: Months of spending kept in cash (default 6).
        exclude_categories: Comma-separated spending categories to leave out.
    """
    params = {
        "price_eur": price_eur,
        "down_payment_pct": down_payment_pct,
        "rate_pct": rate_pct,
        "term_years": term_years,
        "purchase_costs_pct": purchase_costs_pct,
        "monthly_running_eur": monthly_running_eur,
        "sale_proceeds_eur": sale_proceeds_eur,
        "existing_loans_repaid": str(bool(existing_loans_repaid)).lower(),
        "rent_saved_monthly_eur": rent_saved_monthly_eur,
        "buffer_months": buffer_months,
        "exclude_categories": exclude_categories,
    }
    try:
        data = client._get("/api/v1/scenarios/house-purchase", params)
    except Exception as e:
        return f"Error running house-purchase scenario: {e}"

    funding = data.get("funding") or {}
    mortgage = data.get("mortgage") or {}
    monthly = data.get("monthly") or {}
    after = data.get("after") or {}
    lines = [f"HOUSE PURCHASE SCENARIO — {_fmt_currency(data.get('price_eur', 0))}:"]
    lines.append(
        f"  Cash needed: {_fmt_currency(data.get('cash_needed_eur', 0))} "
        f"(down payment {_fmt_currency(data.get('down_payment_eur', 0))}, costs "
        f"{_fmt_currency(data.get('purchase_costs_eur', 0))})"
    )
    lines.append(
        f"  From cash {_fmt_currency(funding.get('from_cash_eur', 0))}, investments "
        f"sold {_fmt_currency(funding.get('investments_sold_eur', 0))} (tax "
        f"{_fmt_currency(funding.get('tax_on_sale_eur', 0))}), shortfall "
        f"{_fmt_currency(funding.get('shortfall_eur', 0))}"
    )
    lines.append(
        f"  Mortgage {_fmt_currency(mortgage.get('principal_eur', 0))} at "
        f"{mortgage.get('rate_pct', 0):g}% for {mortgage.get('term_years', 0)} years: "
        f"{_fmt_currency(mortgage.get('monthly_payment_eur', 0))}/month, interest "
        f"{_fmt_currency(mortgage.get('total_interest_eur', 0))}"
    )
    lines.append(
        f"  Monthly net: {_fmt_currency(monthly.get('net_before_eur', 0))} → "
        f"{_fmt_currency(monthly.get('net_after_eur', 0))}"
    )
    ratio = data.get("debt_ratio_pct")
    lines.append(
        f"  Loan payments / income: {'n/a' if ratio is None else f'{ratio:.1f}%'} "
        f"(limit {data.get('debt_ratio_limit_pct', 35):g}%); highest price at the "
        f"limit {_fmt_currency(data.get('max_price_at_debt_limit_eur') or 0)}"
    )
    months = after.get("emergency_months")
    lines.append(
        f"  Afterwards: cash {_fmt_currency(after.get('liquid_eur', 0))} "
        f"({'n/a' if months is None else f'{months:.1f}'} months of spending), "
        f"investments {_fmt_currency(after.get('investments_eur', 0))}"
    )
    lines.extend(f"  ! {flag}" for flag in data.get("flags") or [])
    lines.extend(_warnings(data))
    return "\n".join(lines)


@tool
def scenario_market_crash(
    drop_pct: Optional[float] = None,
    scenario: Optional[str] = None,
    expected_return_pct: float = 6.0,
    monthly_contribution_eur: Optional[float] = None,
) -> str:
    """
    What if markets crash: loss on the held portfolio (funds looked through),
    net worth after, and how many months until the portfolio shows today's
    value again at low/expected/high returns, with and without monthly
    contributions, plus how long past crashes took. Read-only.

    Args:
        drop_pct: Fall in all equities as a positive number, e.g. 40; or
        scenario: a preset — 'ai_bubble' (used when neither is given),
            'equity_bear', 'semis_crash', 'usd_down', 'rates_up',
            'crypto_winter', 'stagflation'.
        expected_return_pct: Annual return assumed in the recovery (default 6).
        monthly_contribution_eur: New money invested per month; default from
            the wealth plan or the bank statements.
    """
    if drop_pct is None and not scenario:
        scenario = "ai_bubble"
    params = {
        "drop_pct": drop_pct,
        "scenario": scenario,
        "expected_return_pct": expected_return_pct,
        "monthly_contribution_eur": monthly_contribution_eur,
    }
    try:
        data = client._get("/api/v1/scenarios/market-crash", params)
    except Exception as e:
        return f"Error running market-crash scenario: {e}"

    lines = [f"MARKET CRASH SCENARIO — {data.get('scenario', '?')}:"]
    lines.append(
        f"  Portfolio {_fmt_currency(data.get('value_before_eur', 0))} → "
        f"{_fmt_currency(data.get('value_after_eur', 0))} "
        f"(-{data.get('drop_pct', 0):.1f}%, loss "
        f"{_fmt_currency(data.get('loss_eur', 0))})"
    )
    if data.get("net_worth_after_eur") is not None:
        lines.append(
            f"  Net worth {_fmt_currency(data.get('net_worth_before_eur', 0))} → "
            f"{_fmt_currency(data.get('net_worth_after_eur', 0))}"
        )
    gain = data.get("gain_needed_to_recover_pct")
    if gain is not None:
        lines.append(f"  Gain needed to recover: {gain:.1f}%")
    lines.append(
        f"  Recovery to today's value (contributions "
        f"{_fmt_currency(data.get('monthly_contribution_eur', 0))}/month, "
        f"{data.get('monthly_contribution_source', 'given')}):"
    )
    for row in data.get("recovery") or []:
        lines.append(
            f"    {row.get('case', '?'):<9}{row.get('annual_return_pct', 0):g}%/year: "
            f"{_months(row.get('months_with_contributions'))} with contributions, "
            f"{_months(row.get('months_without_contributions'))} without"
        )
    history = data.get("historical_reference") or []
    if history:
        lines.append("  Past crashes (index price, USD, approximate):")
        for h in history:
            lines.append(
                f"    {h.get('name')}: {h.get('drop_pct')}%, "
                f"{h.get('years_to_recover')} years to recover"
            )
    return "\n".join(lines)
