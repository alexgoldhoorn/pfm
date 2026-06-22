"""Portfolio Health Advisor — data gathering helpers and LLM prompt/parse logic."""

from __future__ import annotations

import json
import logging
import math
import statistics
from datetime import date, datetime
from typing import Any, Optional

from portf_manager.cache import cached
from portf_manager.market import get_fundamentals
from portf_manager.positions import compute_positions
from portf_manager.services.analytics_service import (
    calmar_ratio,
    compute_cagr,
    dividend_income,
    irpf_savings_tax,
    money_weighted_irr,
    simple_return,
    sortino_ratio,
)
from portf_manager.tax_calculator import TaxCalculator

logger = logging.getLogger(__name__)


def _fx(currency: str) -> float:
    """EUR conversion rate — delegates to the portfolios router helper."""
    from portf_server.routers.portfolios import _get_fx_rate

    return _get_fx_rate(currency)


def gather_performance(db, portfolio_id: Optional[int] = None) -> dict[str, Any]:
    """Invested, current value, total return, CAGR, IRR, inception date."""
    txns = db.get_all_transactions(portfolio_id=portfolio_id)
    assets_by_id = {a["id"]: a for a in db.get_all_assets(active_only=False)}
    positions, realised = compute_positions(txns)

    invested = 0.0
    current_value = 0.0
    cash_flows: list[tuple[date, float]] = []

    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = assets_by_id.get(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        invested += pos["cost"] * _fx(cur)
        price_data = db.get_latest_price(aid)
        price = float(price_data["price"]) if price_data else 0.0
        current_value += pos["quantity"] * price * _fx(cur)

    for tx in txns:
        d = tx.get("transaction_date", "")
        try:
            dd = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        asset = assets_by_id.get(tx["asset_id"])
        cur = asset.get("currency", "EUR") if asset else "EUR"
        amount_eur = float(tx["total_amount"] or 0) * _fx(cur)
        t = tx["transaction_type"].lower()
        if t == "buy":
            cash_flows.append((dd, -amount_eur))
        elif t in ("sell", "dividend"):
            cash_flows.append((dd, amount_eur))

    inception = min((d for d, _ in cash_flows), default=None)
    cagr = (
        compute_cagr(invested, current_value, realised, inception)
        if inception
        else None
    )

    return {
        "invested_eur": round(invested, 2),
        "current_value_eur": round(current_value, 2),
        "total_return_pct": simple_return(invested, current_value, realised),
        "cagr_pct": cagr,
        "irr_pct": money_weighted_irr(cash_flows, current_value),
        "inception_date": inception.isoformat() if inception else None,
    }


def gather_risk(db) -> dict[str, Any]:
    """Risk metrics from daily snapshots (portfolio-wide, no portfolio_id filter)."""
    snapshots = db.get_snapshots()
    base: dict[str, Any] = {
        "max_drawdown_pct": None,
        "volatility_pct": None,
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "calmar_ratio": None,
    }
    if len(snapshots) < 3:
        base["note"] = "Need at least 3 daily snapshots."
        return base

    values = [s["total_value_eur"] for s in snapshots]
    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            max_dd = min(max_dd, (v - peak) / peak)

    returns = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] > 0
    ]
    vol = statistics.stdev(returns) * math.sqrt(252) if len(returns) > 1 else None
    mean_daily = statistics.mean(returns) if returns else 0
    sharpe = round((mean_daily * 252) / vol, 2) if vol and vol > 0 else None

    snap_dates = [s["snapshot_date"][:10] for s in snapshots]
    snap_days = (
        date.fromisoformat(snap_dates[-1]) - date.fromisoformat(snap_dates[0])
    ).days
    snap_cagr_pct = None
    if snap_days >= 365 and values[0] > 0 and values[-1] > 0:
        snap_cagr_pct = round(
            ((values[-1] / values[0]) ** (365.25 / snap_days) - 1) * 100, 2
        )

    max_dd_pct = round(max_dd * 100, 2)
    return {
        "max_drawdown_pct": max_dd_pct,
        "volatility_pct": round(vol * 100, 2) if vol else None,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino_ratio(returns),
        "calmar_ratio": calmar_ratio(snap_cagr_pct, max_dd_pct),
    }


_CRYPTO_SECTOR = "Cryptocurrency"
_CRYPTO_COUNTRY = "Global"
_TYPE_SECTOR_DEFAULTS: dict[str, str] = {
    "fund": "Diversified Fund",
    "index": "Diversified Index",
    "bond": "Fixed Income",
}


def _resolve_sector_country(db, asset: dict, _value: float = 0) -> tuple[str, str]:
    """Return (sector, country) for an asset.

    Resolution order:
    1. Crypto / bond → hardcoded defaults (yfinance has no useful data).
    2. yfinance via ``asset["ticker"]`` when set (ISIN assets need the real ticker).
    3. yfinance via ``asset["symbol"]`` as fallback.
    4. Asset-type default sector, country stays Unknown.
    """
    atype = asset.get("asset_type", "other")
    if atype == "crypto":
        return (_CRYPTO_SECTOR, _CRYPTO_COUNTRY)

    sym = asset["symbol"]
    yf_sym = asset.get("ticker") or sym

    def _fetch(s=yf_sym):
        fund = get_fundamentals(db, s, max_age=0)  # force live when outer cache is cold
        return {"sector": fund.get("sector"), "country": fund.get("country")}

    try:
        meta = cached(db, f"yf:sectorcountry:{yf_sym}", 7 * 86400, _fetch) or {}
    except Exception:
        meta = {}

    sector = meta.get("sector") or _TYPE_SECTOR_DEFAULTS.get(atype)
    country = meta.get("country")
    return (sector or "Unknown", country or "Unknown")


def gather_diversification(db, portfolio_id: Optional[int] = None) -> dict[str, Any]:
    """Sector/country/currency/type breakdown and HHI. Fetches yfinance (cached 7d)."""
    txns = db.get_all_transactions(portfolio_id=portfolio_id)
    positions, _ = compute_positions(txns)

    by_type: dict[str, float] = {}
    by_currency: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    by_country: dict[str, float] = {}
    by_position: dict[str, float] = {}
    total = 0.0

    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        price_data = db.get_latest_price(aid)
        price = float(price_data["price"]) if price_data else 0.0
        value = pos["quantity"] * price * _fx(cur)
        if value <= 0:
            continue
        sym = asset["symbol"]
        total += value
        by_position[sym] = by_position.get(sym, 0) + value
        atype = asset.get("asset_type", "other")
        by_type[atype] = by_type.get(atype, 0) + value
        by_currency[cur] = by_currency.get(cur, 0) + value

        sector, country = _resolve_sector_country(db, asset, value)
        by_sector[sector] = by_sector.get(sector, 0) + value
        by_country[country] = by_country.get(country, 0) + value

    def pct_map(d: dict) -> dict:
        return (
            {
                k: round(v / total * 100, 1)
                for k, v in sorted(d.items(), key=lambda x: -x[1])
            }
            if total
            else {}
        )

    hhi = (
        round(sum((v / total) ** 2 for v in by_position.values()) * 10000, 0)
        if total
        else 0.0
    )

    return {
        "total_value_eur": round(total, 2),
        "by_asset_type": pct_map(by_type),
        "by_currency": pct_map(by_currency),
        "by_sector": pct_map(by_sector),
        "by_country": pct_map(by_country),
        "concentration_hhi": hhi,
    }


def gather_fees_and_dividends(db, portfolio_id: Optional[int] = None) -> dict[str, Any]:
    """Fee drag % and trailing-12m dividend income."""
    txns = db.get_all_transactions(portfolio_id=portfolio_id)
    portfolios = {p["id"]: p["name"] for p in db.get_all_portfolios()}
    total_fees = total_invested = 0.0
    by_broker: dict[str, dict] = {}

    for tx in txns:
        asset = db.get_asset(tx["asset_id"])
        cur = asset.get("currency", "EUR") if asset else "EUR"
        fees = float(tx.get("fees") or 0) * _fx(cur)
        pname = portfolios.get(tx.get("portfolio_id"), "Unassigned")
        entry = by_broker.setdefault(pname, {"fees_eur": 0.0, "invested_eur": 0.0})
        entry["fees_eur"] += fees
        total_fees += fees
        if tx["transaction_type"].lower() == "buy":
            amt = float(tx["total_amount"] or 0) * _fx(cur)
            entry["invested_eur"] += amt
            total_invested += amt

    cutoff = date.today().replace(year=date.today().year - 1).isoformat()
    ttm = sum(
        float(tx.get("total_amount") or 0)
        for tx in txns
        if tx.get("transaction_type", "").lower() == "dividend"
        and str(tx.get("transaction_date", ""))[:10] >= cutoff
    )

    return {
        "total_fees_eur": round(total_fees, 2),
        "fee_drag_pct": (
            round(total_fees / total_invested * 100, 3) if total_invested > 0 else 0
        ),
        "ttm_dividends_eur": round(ttm, 2),
        "projected_annual_eur": round(ttm, 2),
        "by_broker": {
            k: {
                "fees_eur": round(v["fees_eur"], 2),
                "fee_drag_pct": (
                    round(v["fees_eur"] / v["invested_eur"] * 100, 3)
                    if v["invested_eur"] > 0
                    else 0
                ),
            }
            for k, v in by_broker.items()
        },
    }


def gather_tax(db, portfolio_id: Optional[int] = None) -> dict[str, Any]:
    """Harvestable losses and current-year IRPF estimate."""
    yr = date.today().year
    realised_gain = 0.0
    calc = TaxCalculator(db)
    try:
        report = calc.calculate_tax_report(
            user_id=1,
            start_date=date(yr, 1, 1),
            end_date=date(yr, 12, 31),
            portfolio_id=portfolio_id,
        )
        for _sym, lots in report.items():
            realised_gain += sum(float(getattr(t, "gain_loss", 0) or 0) for t in lots)
    except Exception as e:
        logger.warning(f"Tax calc failed: {e}")

    txns = db.get_all_transactions(portfolio_id=portfolio_id)
    div = dividend_income(txns)

    # Interest income this year (P2P / savings — taxed in the savings base too)
    interest_this_year = 0.0
    for tx in txns:
        if (tx.get("transaction_type") or "").lower() != "interest":
            continue
        d = str(tx.get("transaction_date", ""))[:10]
        if d[:4] == str(yr):
            interest_this_year += float(tx.get("total_amount") or 0)

    savings_base = realised_gain + div["by_year"].get(str(yr), 0.0) + interest_this_year

    positions, _ = compute_positions(txns)
    harvest_candidates = []
    harvestable_loss = 0.0
    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        price_data = db.get_latest_price(aid)
        price = float(price_data["price"]) if price_data else 0.0
        gain = (pos["quantity"] * price - pos["cost"]) * _fx(cur)
        if gain < -1:
            harvest_candidates.append(
                {"symbol": asset["symbol"], "unrealised_loss_eur": round(gain, 2)}
            )
            harvestable_loss += gain

    return {
        "year": yr,
        "realised_gain_eur": round(realised_gain, 2),
        "interest_income_eur": round(interest_this_year, 2),
        "savings_base_eur": round(savings_base, 2),
        "estimated_tax_eur": irpf_savings_tax(savings_base),
        "harvestable_loss_eur": round(harvestable_loss, 2),
        "harvest_candidates": sorted(
            harvest_candidates, key=lambda x: x["unrealised_loss_eur"]
        ),
    }


def gather_holdings_fundamentals(db, portfolio_id: Optional[int] = None) -> list[dict]:
    """Per-holding weight % + fundamentals (P/E, yield, sector) from yfinance cache."""
    txns = db.get_all_transactions(portfolio_id=portfolio_id)
    positions, _ = compute_positions(txns)
    position_values: dict[str, dict] = {}
    total = 0.0

    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        price_data = db.get_latest_price(aid)
        price = float(price_data["price"]) if price_data else 0.0
        value = pos["quantity"] * price * _fx(cur)
        if value <= 0:
            continue
        sym = asset["symbol"]
        total += value
        position_values[sym] = {"value": value, "name": asset.get("name", sym)}

    holdings = []
    sorted_positions = sorted(position_values.items(), key=lambda x: -x[1]["value"])
    for sym, data in sorted_positions[:20]:
        weight = round(data["value"] / total * 100, 1) if total else 0
        fund: dict = {}
        try:
            fund = get_fundamentals(db, sym) or {}
        except Exception:
            pass
        holdings.append(
            {
                "symbol": sym,
                "name": data["name"],
                "weight_pct": weight,
                "value_eur": round(data["value"], 2),
                "pe": fund.get("trailingPE"),
                "dividend_yield": fund.get("dividendYield"),
                "sector": fund.get("sector"),
                "country": fund.get("country"),
            }
        )
    return holdings


def build_analysis_prompt(bundle: dict) -> str:
    """Build the structured LLM prompt from a gathered data bundle."""
    perf = bundle.get("performance", {})
    risk = bundle.get("risk", {})
    divfee = bundle.get("fees_and_dividends", {})
    div_data = bundle.get("diversification", {})
    tax = bundle.get("tax", {})
    holdings = bundle.get("holdings", [])

    holdings_str = (
        "\n".join(
            f"  {h['symbol']} ({h.get('sector') or 'N/A'}): {h['weight_pct']}% weight"
            f", P/E={h.get('pe') or 'N/A'}, div yield={h.get('dividend_yield') or 'N/A'}"
            for h in holdings[:20]
        )
        or "  (no holdings data)"
    )

    _empty: dict = {}
    return f"""You are a professional portfolio analyst. Analyse this portfolio and return a JSON health report.

## Portfolio Data

### Performance
- Invested: €{perf.get('invested_eur', 0):,.0f}  |  Current value: €{perf.get('current_value_eur', 0):,.0f}
- Total return: {perf.get('total_return_pct', 'N/A')}%  |  CAGR: {perf.get('cagr_pct', 'N/A')}%  |  IRR: {perf.get('irr_pct', 'N/A')}%
- Inception: {perf.get('inception_date', 'N/A')}

### Risk
- Max drawdown: {risk.get('max_drawdown_pct', 'N/A')}%  |  Volatility: {risk.get('volatility_pct', 'N/A')}%
- Sharpe: {risk.get('sharpe_ratio', 'N/A')}  |  Sortino: {risk.get('sortino_ratio', 'N/A')}  |  Calmar: {risk.get('calmar_ratio', 'N/A')}

### Diversification
- Asset types: {json.dumps(div_data.get('by_asset_type', _empty))}
- Sectors: {json.dumps(div_data.get('by_sector', _empty))}
- Countries: {json.dumps(div_data.get('by_country', _empty))}
- Currencies: {json.dumps(div_data.get('by_currency', _empty))}
- Concentration HHI: {div_data.get('concentration_hhi', 'N/A')} / 10000 (>2500 = high)

### Top Holdings
{holdings_str}

### Income
- TTM dividends: €{divfee.get('ttm_dividends_eur', 0):,.0f}  |  Projected annual: €{divfee.get('projected_annual_eur', 0):,.0f}

### Fees
- Total fees: €{divfee.get('total_fees_eur', 0):,.0f}  |  Fee drag: {divfee.get('fee_drag_pct', 0)}% of invested

### Tax (Spanish IRPF, year {tax.get('year', '')})
- Realised gains: €{tax.get('realised_gain_eur', 0):,.0f}  |  Est. tax: €{tax.get('estimated_tax_eur', 0):,.0f}
- Harvestable losses: €{abs(tax.get('harvestable_loss_eur', 0)):,.0f} ({len(tax.get('harvest_candidates', []))} positions)

---

Return ONLY valid JSON (no markdown fences, no text outside the JSON):

{{
  "scores": {{
    "diversification":      {{"score": <1-10 int>, "reason": "<one sentence>"}},
    "risk_adjusted_return": {{"score": <1-10 int>, "reason": "<one sentence>"}},
    "income":               {{"score": <1-10 int>, "reason": "<one sentence>"}},
    "fees":                 {{"score": <1-10 int>, "reason": "<one sentence>"}},
    "tax_efficiency":       {{"score": <1-10 int>, "reason": "<one sentence>"}}
  }},
  "recommendations": [
    {{"priority": 1, "category": "<Diversification|Income|Risk|Fees|Tax>", "action": "<concrete action>", "rationale": "<1-2 sentences>"}},
    {{"priority": 2, "category": "...", "action": "...", "rationale": "..."}},
    {{"priority": 3, "category": "...", "action": "...", "rationale": "..."}},
    {{"priority": 4, "category": "...", "action": "...", "rationale": "..."}},
    {{"priority": 5, "category": "...", "action": "...", "rationale": "..."}}
  ],
  "summary": "<2-3 sentence overall assessment>"
}}
"""


def parse_analysis_response(raw: str) -> dict:
    """Parse LLM JSON response; return {"error": ...} on failure."""
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("```")
        )
    try:
        result = json.loads(text)
        if "scores" not in result or "recommendations" not in result:
            raise ValueError("Missing required keys: scores / recommendations")
        return result
    except Exception as e:
        logger.error(f"Failed to parse LLM analysis response: {e}\nRaw: {raw[:300]}")
        return {
            "error": "Analysis unavailable — LLM returned unexpected format. Try again."
        }
