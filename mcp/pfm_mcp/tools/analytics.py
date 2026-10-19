"""Performance, dividends, diversification, risk, fundamentals, health, rebalance and data freshness."""

from typing import Optional

from pfm_mcp import client, tool
from pfm_mcp.client import _fmt_currency


@tool
def performance(period: str = "all", benchmark: str = "VWCE.DE") -> str:
    """
    Portfolio performance in EUR: total gain (incl. dividends and interest),
    total return, money-weighted IRR, and time-weighted period return vs a
    benchmark measured in EUR.

    Args:
        period: Return window — 'ytd', '1m', '1y', or 'all' (default).
        benchmark: Yahoo ticker for comparison, default 'VWCE.DE' (FTSE
            All-World, EUR, dividends reinvested). '^GSPC' is the S&P 500
            price index (no dividends), converted to EUR.
    """
    try:
        data = client._get(
            "/api/v1/analytics/performance", {"period": period, "benchmark": benchmark}
        )
    except Exception as e:
        return f"Error fetching performance: {e}"

    lines = [f"PERFORMANCE ({period.upper()}):"]
    lines.append(f"  Invested:         {_fmt_currency(data.get('invested_eur', 0))}")
    lines.append(
        f"  Current value:    {_fmt_currency(data.get('current_value_eur', 0))}"
    )
    lines.append(
        f"  Realised P&L:     {_fmt_currency(data.get('realised_pnl_eur', 0))}"
    )
    lines.append(f"  Div. + interest:  {_fmt_currency(data.get('income_eur', 0))}")
    lines.append(f"  Total gain:       {_fmt_currency(data.get('total_gain_eur', 0))}")
    tr = data.get("total_return_pct")
    lines.append(
        f"  Total return:     {tr:+.2f}%"
        if tr is not None
        else "  Total return:     n/a"
    )
    irr = data.get("money_weighted_irr_pct")
    lines.append(
        f"  IRR (MWRR):       {irr:+.2f}%/yr"
        if irr is not None
        else "  IRR (MWRR):       n/a"
    )
    pr = data.get("period_return_pct")
    lines.append(
        f"  TWR (period):     {pr:+.2f}%"
        if pr is not None
        else "  TWR (period):     n/a (daily history doesn't cover the window)"
    )
    ann = data.get("annualised_twr_pct")
    if ann is not None:
        lines.append(f"  TWR per year:     {ann:+.2f}%")
    br = data.get("benchmark_return_pct")
    bname = data.get("benchmark_label") or data.get("benchmark", benchmark)
    lines.append(
        f"  Benchmark (EUR):  {br:+.2f}%  [{bname}]"
        if br is not None
        else f"  Benchmark (EUR):  n/a  [{bname}]"
    )
    if period == "all":
        lines.extend(_progress_lines(benchmark))
    return "\n".join(lines)


def _progress_lines(benchmark: str) -> list[str]:
    """Contributions vs growth, real return, years, latent tax, savings."""
    try:
        p = client._get("/api/v1/analytics/progress", {"benchmark": benchmark})
    except Exception as e:
        return [f"PROGRESS: unavailable ({e})"]

    def pct(v, suffix: str = "%") -> str:
        return "n/a" if v is None else f"{v:+.2f}{suffix}"

    c = p.get("contributions") or {}
    r = p.get("real") or {}
    t = p.get("latent_tax") or {}
    s = p.get("savings") or {}
    lines = ["PROGRESS:"]
    lines.append(
        f"  Money put in:     {_fmt_currency(c.get('net_contributions_eur') or 0)}"
        f"  | market growth {_fmt_currency(c.get('growth_eur') or 0)}"
    )
    infl = r.get("inflation_annual_pct")
    lines.append(
        f"  Real IRR:         {pct(r.get('real_irr_pct'), '%/yr')}"
        f"  (inflation {'n/a' if infl is None else f'{infl:.2f}%/yr'},"
        f" HICP Spain to {r.get('inflation_to') or 'n/a'})"
    )
    for y in p.get("calendar_years") or []:
        part = " (part)" if y.get("partial") else ""
        lines.append(
            f"  {y.get('year')}{part}: TWR {pct(y.get('return_pct'))}"
            f" | benchmark {pct(y.get('benchmark_return_pct'))}"
            f" | real {pct(y.get('real_return_pct'))}"
        )
    lines.append(
        f"  Sell-all IRPF:    {_fmt_currency(t.get('latent_tax_eur') or 0)}"
        f" on {_fmt_currency(t.get('unrealised_gain_eur') or 0)} unrealised"
        f" ({_fmt_currency(t.get('fund_unrealised_gain_eur') or 0)} in funds,"
        " traspaso-eligible)"
    )
    sr, em = s.get("savings_rate_pct"), s.get("emergency_months")
    lines.append(
        f"  Savings rate:     {'n/a' if sr is None else f'{sr:.1f}%'}"
        f" over {len(s.get('months_used') or [])} imported months"
        f"  | emergency fund {'n/a' if em is None else f'{em:.1f} months'}"
    )
    for note in p.get("notes") or []:
        lines.append(f"  Note: {note}")
    return lines


@tool
def dividends() -> str:
    """
    Dividend income history by year and by symbol, trailing-12-month totals,
    yield-on-cost per position, and projected forward annual income.
    """
    try:
        data = client._get("/api/v1/analytics/dividends")
    except Exception as e:
        return f"Error fetching dividends: {e}"

    lines = ["DIVIDENDS:"]
    by_year = data.get("by_year", {})
    if by_year:
        lines.append("\nBy year:")
        for yr, amt in sorted(by_year.items()):
            lines.append(f"  {yr}:  {_fmt_currency(float(amt))}")

    lines.append(f"\nTrailing 12m total:  {_fmt_currency(data.get('ttm', 0))}")
    lines.append(
        f"Projected annual:    {_fmt_currency(data.get('projected_annual', 0))}"
    )

    ttm_sym = data.get("ttm_by_symbol", {})
    names = data.get("names", {})
    yoc = data.get("yield_on_cost", {})
    if ttm_sym:
        lines.append("\nTTM by symbol:")
        for sym, amt in sorted(ttm_sym.items(), key=lambda x: -x[1]):
            name = names.get(sym, sym)
            yoc_str = f"  YoC {yoc[sym]:.1f}%" if sym in yoc else ""
            lines.append(
                f"  {sym:12s}  {_fmt_currency(float(amt)):>14s}  {name}{yoc_str}"
            )
    return "\n".join(lines)


@tool
def diversification() -> str:
    """
    Portfolio exposure with funds looked through: asset class, region, sector,
    country and currency, plus concentration, how much of the book is actually
    classified, and any funds holding the same exposure.
    """
    try:
        data = client._get("/api/v1/analytics/diversification")
    except Exception as e:
        return f"Error fetching diversification: {e}"

    def fmt_map(label: str, d: dict) -> list[str]:
        out = [f"\n{label}:"]
        for k, pct in d.items():
            out.append(f"  {k:25s}  {pct:.1f}%")
        return out

    lines = [
        f"DIVERSIFICATION  (total {_fmt_currency(data.get('total_value_eur', 0))})"
    ]
    largest = data.get("largest_position_symbol")
    if largest:
        lines.append(
            f"Largest position: {largest} ({data.get('largest_position_name', largest)})  "
            f"{data.get('largest_position_pct', 0):.1f}%"
        )
    hhi = data.get("concentration_hhi")
    if hhi is not None:
        lines.append(f"Concentration HHI: {hhi:.0f}  (10 000 = single asset)")

    coverage = data.get("coverage") or {}
    classified = coverage.get("classified_pct")
    if classified is not None:
        lines.append(
            f"Coverage: {classified:.1f}% of value classified by region, "
            f"{coverage.get('sector_classified_pct', 0):.1f}% by sector"
        )
    for fund in coverage.get("unprofiled", []):
        lines.append(
            f"  ! No look-through profile: {fund['name']} "
            f"({_fmt_currency(fund['value_eur'])})"
        )

    lines += fmt_map(
        "By asset class (funds looked through)", data.get("by_asset_class", {})
    )
    lines += fmt_map(
        "By region (equity, funds looked through)", data.get("by_region_equity", {})
    )
    lines += fmt_map("By region (all)", data.get("by_region", {}))
    lines += fmt_map("By sector", data.get("by_sector", {}))
    lines += fmt_map(
        "By currency exposure (approximate)", data.get("by_currency_exposure", {})
    )
    lines += fmt_map("By quote currency", data.get("by_currency", {}))
    lines += fmt_map("By asset type", data.get("by_asset_type", {}))
    lines += fmt_map("By country (direct holdings)", data.get("by_country", {}))

    # Overlap is a second call, and a useful breakdown must not be lost if it
    # fails.
    try:
        overlap = client._get("/api/v1/analytics/fund-overlap")
    except Exception as e:
        lines.append(f"\nFund overlap unavailable: {e}")
        return "\n".join(lines)

    groups = overlap.get("groups", [])
    if groups:
        lines.append("\nFunds holding the same exposure:")
        for group in groups:
            names = " + ".join(m["name"] for m in group["members"])
            tag = {
                "consolidation_candidate": "consolidation candidate",
                "similar": "similar",
                "informational": "nested, informational",
            }.get(group["kind"], group["kind"])
            lines.append(
                f"  [{tag}] {names} — {_fmt_currency(group['combined_value_eur'])} "
                f"({group['combined_pct']:.1f}%)"
            )
            lines.append(f"      {group['reason']}")
            if group.get("transferable"):
                lines.append(
                    "      Both are funds: a traspaso can merge them without "
                    "realising a gain."
                )
    return "\n".join(lines)


@tool
def risk() -> str:
    """
    Portfolio risk metrics from daily snapshot history, for the trailing year
    and for all history: annualised return vs a EUR benchmark (FTSE All-World,
    dividends reinvested), current and max drawdown, annualised volatility,
    Sharpe, Sortino (both over the €STR risk-free rate), Calmar, beta (weekly
    returns) and Jensen's alpha. Returns are flow-adjusted (buys, sells and
    imports are not gains/losses). Under ~a year of history a window is
    flagged low-confidence.
    """

    def _fmt(v, spec: str = ".2f", suffix: str = "") -> str:
        return "n/a" if v is None else f"{v:{spec}}{suffix}"

    lines = ["RISK METRICS (flow-adjusted, EUR):"]
    for window, label in (("1y", "Last 12 months"), ("all", "All history")):
        try:
            d = client._get(f"/api/v1/analytics/risk?window={window}")
        except Exception as e:
            return f"Error fetching risk metrics: {e}"
        if d.get("note"):
            lines.append(f"  {label}: {d['note']}")
            continue
        flag = "  ⚠ low confidence (<1y)" if d.get("low_confidence") else ""
        lines.append(f"  {label} ({d.get('start_date')} → {d.get('end_date')}){flag}")
        lines.append(
            f"    Return:           {_fmt(d.get('period_return_pct'), '+.2f', '%')}"
            f" (benchmark {_fmt(d.get('benchmark_return_pct'), '+.2f', '%')})"
        )
        lines.append(
            f"    Annualised:       {_fmt(d.get('annualised_return_pct'), '+.2f', '%')}"
        )
        lines.append(
            f"    Current drawdown: {_fmt(d.get('current_drawdown_pct'), '.2f', '%')}"
        )
        lines.append(
            f"    Max drawdown:     {_fmt(d.get('max_drawdown_pct'), '.2f', '%')}"
        )
        lines.append(
            f"    Volatility (ann): {_fmt(d.get('volatility_pct'), '.2f', '%')}"
        )
        lines.append(
            f"    Sharpe / Sortino / Calmar: {_fmt(d.get('sharpe_ratio'))} / "
            f"{_fmt(d.get('sortino_ratio'))} / {_fmt(d.get('calmar_ratio'))}"
            f"  (rf {_fmt(d.get('risk_free_rate_pct'), '.2f', '%')}"
            f" {d.get('risk_free_source') or ''})"
        )
        lines.append(
            f"    Beta / Alpha:     {_fmt(d.get('beta'))} / "
            f"{_fmt(d.get('alpha_pct'), '+.2f', '%/yr')}"
            f"  ({d.get('beta_observations') or 0} weekly obs)"
        )
    return "\n".join(lines)


@tool
def fundamentals(symbol: str) -> str:
    """
    Key yfinance fundamentals for any ticker (held or not): P/E, market cap,
    dividend yield, 52-week range, sector, beta, and more.

    Args:
        symbol: Yahoo-format ticker, e.g. 'NVDA', 'ASML.AS', 'BTC-EUR'.
    """
    try:
        data = client._get(f"/api/v1/market/fundamentals/{symbol.upper()}")
    except Exception as e:
        return f"Error fetching fundamentals for {symbol}: {e}"

    if not data:
        return f"No fundamentals available for {symbol.upper()}."

    lines = [f"FUNDAMENTALS — {symbol.upper()}:"]
    fields = [
        ("shortName", "Name"),
        ("sector", "Sector"),
        ("industry", "Industry"),
        ("country", "Country"),
        ("marketCap", "Market cap"),
        ("trailingPE", "Trailing P/E"),
        ("forwardPE", "Forward P/E"),
        ("priceToBook", "Price/Book"),
        ("dividendYield", "Div yield"),
        ("trailingAnnualDividendYield", "Div yield (TTM)"),
        ("fiftyTwoWeekLow", "52w low"),
        ("fiftyTwoWeekHigh", "52w high"),
        ("fiftyDayAverage", "50d MA"),
        ("twoHundredDayAverage", "200d MA"),
        ("beta", "Beta"),
    ]
    for key, label in fields:
        val = data.get(key)
        if val is None:
            continue
        if key == "marketCap":
            val = f"${val / 1e9:.1f}B" if val >= 1e9 else f"${val / 1e6:.0f}M"
        elif key in ("dividendYield", "trailingAnnualDividendYield") and isinstance(
            val, float
        ):
            val = f"{val * 100:.2f}%"
        elif isinstance(val, float):
            val = f"{val:.4g}"
        lines.append(f"  {label:22s}  {val}")
    return "\n".join(lines)


@tool
def portfolio_health(portfolio_id: Optional[int] = None) -> str:
    """
    Return the AI-generated portfolio health analysis: five scored categories
    (diversification, risk_adjusted_return, income, fees, tax_efficiency each
    scored 1–10), prioritised recommendations, and a summary.

    The analysis is cached — run it first from the Research → Portfolio Health
    page in the web UI. Returns an error message if no cached result exists.

    Args:
        portfolio_id: Optional broker/account filter (uses cached key for that ID).
    """
    params = {"portfolio_id": portfolio_id} if portfolio_id else {}
    try:
        data = client._get("/api/v1/research/portfolio-analysis", params or None)
    except Exception as e:
        return f"Error fetching portfolio health: {e}"

    if "error" in data:
        return data["error"]

    lines = ["PORTFOLIO HEALTH ANALYSIS:"]
    scores = data.get("scores", {})
    for category, info in scores.items():
        score = info.get("score", "?")
        reason = info.get("reason", "")
        lines.append(f"  {category.replace('_', ' ').title():30s} {score}/10  {reason}")

    summary = data.get("summary", "")
    if summary:
        lines.append(f"\nSummary: {summary}")

    recs = data.get("recommendations", [])
    if recs:
        lines.append("\nRecommendations:")
        for r in recs:
            lines.append(f"  • {r}")

    cached_at = data.get("cached_at") or data.get("generated_at", "")
    if cached_at:
        lines.append(f"\n(Analysis from {str(cached_at)[:10]})")

    return "\n".join(lines)


@tool
def rebalance_analysis() -> str:
    """
    Current allocation by asset type vs the target allocation set on the pfm
    Rebalance page: drift in percentage points and EUR, plus suggested buy/sell
    amounts to get back to target. Asset-type level only, and tax-unaware.
    """
    try:
        data = client._get("/api/v1/rebalance/analysis")
    except Exception as e:
        return f"Error fetching rebalance analysis: {e}"

    allocations = data.get("allocations") or []
    if not allocations or not data.get("targets_sum_pct"):
        return "No allocation targets set (set them on the Rebalance page in the pfm web UI)."

    lines = [
        f"REBALANCE — total {_fmt_currency(float(data.get('total_value_eur') or 0))}:"
    ]
    targets_sum = float(data.get("targets_sum_pct") or 0)
    if abs(targets_sum - 100) > 0.5:
        lines.append(
            f"⚠️ Targets sum to {targets_sum:.1f}%, not 100% — drifts are measured"
            " against incomplete targets."
        )
    for a in sorted(allocations, key=lambda x: -abs(float(x.get("drift_pct") or 0))):
        lines.append(
            f"  {a.get('asset_type') or '?':14s}"
            f" now {float(a.get('current_pct') or 0):5.1f}%"
            f"  target {float(a.get('target_pct') or 0):5.1f}%"
            f"  drift {float(a.get('drift_pct') or 0):+.1f}pp"
            f" ({float(a.get('drift_eur') or 0):+,.2f} EUR)"
        )

    actions = data.get("actions") or []
    if actions:
        lines.append("\nSuggested moves:")
        for act in actions:
            lines.append(
                f"  {(act.get('action') or '?').upper():5s}"
                f" {act.get('asset_type') or '?':14s}"
                f" {_fmt_currency(float(act.get('amount_eur') or 0))}"
                f"  {act.get('reason') or ''}"
            )
    else:
        lines.append("\nWithin tolerance — no moves suggested.")
    lines.append(
        "\nIgnores taxes: selling to rebalance can realise gains — run"
        " wash_sale_check before selling anything at a loss."
    )
    return "\n".join(lines)


@tool
def data_freshness(stale_days: int = 4) -> str:
    """
    How current pfm's price data is: when prices were last refreshed, the
    latest price date, and which auto-priced held assets are stale or have never
    been priced. Check this before quoting position values if in doubt.

    Args:
        stale_days: A price older than this many days counts as stale (default 4).
    """
    try:
        data = client._get(
            "/api/v1/analytics/data-freshness", {"stale_days": stale_days}
        )
    except Exception as e:
        return f"Error fetching data freshness: {e}"

    lines = ["PRICE DATA FRESHNESS:"]
    last = str(data.get("last_refresh") or "never")[:16].replace("T", " ")
    age = data.get("refresh_age_hours")
    age_str = f" ({float(age):.1f}h ago)" if age is not None else ""
    lines.append(f"  Last price refresh: {last}{age_str}")
    lines.append(f"  Prices as of:       {str(data.get('prices_as_of') or '?')[:10]}")

    checked = data.get("checked") or 0
    threshold = data.get("stale_days_threshold", stale_days)
    stale = data.get("stale") or []
    if not stale:
        lines.append(f"\nAll {checked} priced assets are fresh (≤ {threshold} days).")
        return "\n".join(lines)

    lines.append(
        f"\n{data.get('stale_count', len(stale))} of {checked} auto-priced assets need"
        f" attention (older than {threshold} days, or never priced):"
    )
    for s in stale:
        if s.get("age_days") is None:
            detail = s.get("reason") or "no price data"
        else:
            detail = (
                f"{s['age_days']}d old (last {str(s.get('price_date') or '?')[:10]})"
            )
        lines.append(f"  {s.get('symbol') or '?':12s} {detail}  {s.get('name') or ''}")
    return "\n".join(lines)
