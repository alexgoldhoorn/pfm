# Portfolio Health Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Portfolio Health" collapsible panel to the Research page that gathers all portfolio metrics plus per-holding yfinance fundamentals, calls the LLM, and returns a scored health report (5 category scores + prioritised action list). Results are cached in `kv_cache`; TTL stored in `app_settings`.

**Architecture:** New `portf_manager/services/portfolio_advisor.py` has six `gather_*` helpers (each takes `db` + optional `portfolio_id`) plus `build_analysis_prompt` and `parse_analysis_response`. Three new routes added to `portf_server/routers/research.py` before the `/{symbol}` wildcard. Frontend panel lives at the top of the Research page; JS wired in `setupResearchPage()`. Settings modal gets a TTL dropdown that saves server-side.

**Tech Stack:** Python 3.13, FastAPI, yfinance (via existing cache pattern), `portf_manager` DB/cache/LLM/market modules, vanilla JS + Bootstrap 5.

---

## File Map

**Create:**
- `portf_manager/services/portfolio_advisor.py` — all gathering + prompt + parse logic
- `tests/unit/test_portfolio_analysis.py` — unit tests (mocked DB + LLM)

**Modify:**
- `portf_server/routers/research.py` — 3 new endpoints (before `/{symbol}` routes)
- `web_client/js/pfm_core.js` — 3 new `apiClient` methods
- `web_client/index.html` — Portfolio Health panel + Settings TTL dropdown
- `web_client/js/pfm_features.js` — panel JS + TTL load/save in settings
- `web_client/js/help_text.js` — add help entry

---

### Task 1: Failing tests

**Files:**
- Create: `tests/unit/test_portfolio_analysis.py`

- [ ] **Step 1: Write the tests**

```python
# tests/unit/test_portfolio_analysis.py
from unittest.mock import MagicMock, patch
import pytest


def _mock_db(txns=None, assets=None):
    db = MagicMock()
    db.get_all_transactions.return_value = txns or []
    db.get_all_assets.return_value = assets or []
    db.get_latest_price.return_value = None
    db.get_asset.return_value = None
    db.get_all_portfolios.return_value = []
    db.get_snapshots.return_value = []
    return db


class TestGatherPerformance:
    def test_empty_returns_zeros(self):
        from portf_manager.services.portfolio_advisor import gather_performance
        result = gather_performance(_mock_db(), portfolio_id=None)
        assert result["invested_eur"] == 0.0
        assert result["current_value_eur"] == 0.0
        assert result["cagr_pct"] is None

    def test_returns_required_keys(self):
        from portf_manager.services.portfolio_advisor import gather_performance
        result = gather_performance(_mock_db(), portfolio_id=None)
        for key in ("invested_eur", "current_value_eur", "total_return_pct", "cagr_pct", "irr_pct", "inception_date"):
            assert key in result


class TestGatherRisk:
    def test_insufficient_snapshots(self):
        from portf_manager.services.portfolio_advisor import gather_risk
        db = _mock_db()
        db.get_snapshots.return_value = [{"total_value_eur": 1000, "snapshot_date": "2026-01-01"}]
        result = gather_risk(db)
        assert result["sharpe_ratio"] is None
        assert "note" in result

    def test_returns_required_keys(self):
        from portf_manager.services.portfolio_advisor import gather_risk
        result = gather_risk(_mock_db())
        for key in ("sharpe_ratio", "sortino_ratio", "calmar_ratio", "max_drawdown_pct", "volatility_pct"):
            assert key in result


class TestGatherFeesAndDividends:
    def test_empty_returns_zeros(self):
        from portf_manager.services.portfolio_advisor import gather_fees_and_dividends
        result = gather_fees_and_dividends(_mock_db(), portfolio_id=None)
        assert result["total_fees_eur"] == 0.0
        assert result["ttm_dividends_eur"] == 0.0

    def test_returns_required_keys(self):
        from portf_manager.services.portfolio_advisor import gather_fees_and_dividends
        result = gather_fees_and_dividends(_mock_db(), portfolio_id=None)
        for key in ("total_fees_eur", "fee_drag_pct", "ttm_dividends_eur", "projected_annual_eur"):
            assert key in result


class TestGatherTax:
    def test_returns_required_keys(self):
        from portf_manager.services.portfolio_advisor import gather_tax
        result = gather_tax(_mock_db(), portfolio_id=None)
        for key in ("harvest_candidates", "harvestable_loss_eur", "estimated_tax_eur"):
            assert key in result


class TestPromptAndParse:
    _bundle = {
        "performance": {"invested_eur": 10000, "current_value_eur": 11000,
                        "total_return_pct": 10.0, "cagr_pct": 5.0,
                        "irr_pct": 5.5, "inception_date": "2023-01-01"},
        "risk": {"sharpe_ratio": 1.2, "sortino_ratio": 1.5, "calmar_ratio": 0.8,
                 "max_drawdown_pct": -8.0, "volatility_pct": 12.0, "note": None},
        "diversification": {"by_sector": {"Technology": 45}, "by_country": {"US": 60},
                            "by_currency": {"USD": 70}, "by_asset_type": {"stock": 80},
                            "concentration_hhi": 2800, "total_value_eur": 11000},
        "fees_and_dividends": {"total_fees_eur": 50, "fee_drag_pct": 0.5,
                               "ttm_dividends_eur": 300, "projected_annual_eur": 320},
        "tax": {"harvest_candidates": [], "harvestable_loss_eur": 0,
                "estimated_tax_eur": 500, "realised_gain_eur": 1000,
                "savings_base_eur": 1300, "year": 2026},
        "holdings": [{"symbol": "AAPL", "name": "Apple", "weight_pct": 20,
                      "value_eur": 2200, "pe": 28, "dividend_yield": 0.5, "sector": "Technology"}],
    }

    def test_prompt_contains_key_sections(self):
        from portf_manager.services.portfolio_advisor import build_analysis_prompt
        prompt = build_analysis_prompt(self._bundle)
        assert "Sharpe" in prompt
        assert "Technology" in prompt
        assert "AAPL" in prompt
        assert '"scores"' in prompt

    def test_parse_valid_json(self):
        from portf_manager.services.portfolio_advisor import parse_analysis_response
        raw = '{"scores": {"diversification": {"score": 7, "reason": "ok"}}, "recommendations": [], "summary": "Good"}'
        result = parse_analysis_response(raw)
        assert result["scores"]["diversification"]["score"] == 7
        assert result["summary"] == "Good"

    def test_parse_strips_code_fences(self):
        from portf_manager.services.portfolio_advisor import parse_analysis_response
        raw = '```json\n{"scores": {}, "recommendations": [], "summary": "x"}\n```'
        result = parse_analysis_response(raw)
        assert result["summary"] == "x"

    def test_parse_malformed_returns_error_key(self):
        from portf_manager.services.portfolio_advisor import parse_analysis_response
        result = parse_analysis_response("not json")
        assert "error" in result
```

- [ ] **Step 2: Confirm tests fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_portfolio_analysis.py -v 2>&1 | head -30
```
Expected: `ModuleNotFoundError: No module named 'portf_manager.services.portfolio_advisor'`

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_portfolio_analysis.py
git commit -m "test: add failing tests for portfolio_advisor"
```

---

### Task 2: `portf_manager/services/portfolio_advisor.py`

**Files:**
- Create: `portf_manager/services/portfolio_advisor.py`

- [ ] **Step 1: Create the file**

```python
"""Portfolio Health Advisor — data gathering helpers and LLM prompt/parse logic."""
from __future__ import annotations

import json
import logging
import math
import statistics
from datetime import date, datetime
from typing import Any, Optional

import yfinance as yf

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
    cagr = compute_cagr(invested, current_value, realised, inception) if inception else None

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
        "max_drawdown_pct": None, "volatility_pct": None,
        "sharpe_ratio": None, "sortino_ratio": None, "calmar_ratio": None,
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
    snap_days = (date.fromisoformat(snap_dates[-1]) - date.fromisoformat(snap_dates[0])).days
    snap_cagr_pct = None
    if snap_days >= 365 and values[0] > 0 and values[-1] > 0:
        snap_cagr_pct = round(((values[-1] / values[0]) ** (365.25 / snap_days) - 1) * 100, 2)

    max_dd_pct = round(max_dd * 100, 2)
    return {
        "max_drawdown_pct": max_dd_pct,
        "volatility_pct": round(vol * 100, 2) if vol else None,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino_ratio(returns),
        "calmar_ratio": calmar_ratio(snap_cagr_pct, max_dd_pct),
    }


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

        def _fetch_sc(s=sym):
            info = yf.Ticker(s).info
            return {"sector": info.get("sector"), "country": info.get("country")}

        try:
            meta = cached(db, f"yf:sectorcountry:{sym}", 7 * 86400, _fetch_sc) or {}
        except Exception:
            meta = {}
        sector = meta.get("sector") or "Unknown"
        country = meta.get("country") or "Unknown"
        by_sector[sector] = by_sector.get(sector, 0) + value
        by_country[country] = by_country.get(country, 0) + value

    def pct_map(d: dict) -> dict:
        return (
            {k: round(v / total * 100, 1) for k, v in sorted(d.items(), key=lambda x: -x[1])}
            if total else {}
        )

    hhi = round(sum((v / total) ** 2 for v in by_position.values()) * 10000, 0) if total else 0.0

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
        "fee_drag_pct": round(total_fees / total_invested * 100, 3) if total_invested > 0 else 0,
        "ttm_dividends_eur": round(ttm, 2),
        "projected_annual_eur": round(ttm, 2),
        "by_broker": {
            k: {
                "fees_eur": round(v["fees_eur"], 2),
                "fee_drag_pct": round(v["fees_eur"] / v["invested_eur"] * 100, 3)
                if v["invested_eur"] > 0 else 0,
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
    savings_base = realised_gain + div["by_year"].get(str(yr), 0.0)

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
            harvest_candidates.append({"symbol": asset["symbol"], "unrealised_loss_eur": round(gain, 2)})
            harvestable_loss += gain

    return {
        "year": yr,
        "realised_gain_eur": round(realised_gain, 2),
        "savings_base_eur": round(savings_base, 2),
        "estimated_tax_eur": irpf_savings_tax(savings_base),
        "harvestable_loss_eur": round(harvestable_loss, 2),
        "harvest_candidates": sorted(harvest_candidates, key=lambda x: x["unrealised_loss_eur"]),
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
    for sym, data in sorted(position_values.items(), key=lambda x: -x[1]["value"]):
        weight = round(data["value"] / total * 100, 1) if total else 0
        fund: dict = {}
        try:
            fund = get_fundamentals(db, sym) or {}
        except Exception:
            pass
        holdings.append({
            "symbol": sym,
            "name": data["name"],
            "weight_pct": weight,
            "value_eur": round(data["value"], 2),
            "pe": fund.get("trailingPE"),
            "dividend_yield": fund.get("dividendYield"),
            "sector": fund.get("sector"),
            "country": fund.get("country"),
        })
    return holdings


def build_analysis_prompt(bundle: dict) -> str:
    """Build the structured LLM prompt from a gathered data bundle."""
    perf = bundle.get("performance", {})
    risk = bundle.get("risk", {})
    divfee = bundle.get("fees_and_dividends", {})
    div_data = bundle.get("diversification", {})
    tax = bundle.get("tax", {})
    holdings = bundle.get("holdings", [])

    holdings_str = "\n".join(
        f"  {h['symbol']} ({h.get('sector') or 'N/A'}): {h['weight_pct']}% weight"
        f", P/E={h.get('pe') or 'N/A'}, div yield={h.get('dividend_yield') or 'N/A'}"
        for h in holdings[:20]
    ) or "  (no holdings data)"

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
- Asset types: {json.dumps(div_data.get('by_asset_type', {}))}
- Sectors: {json.dumps(div_data.get('by_sector', {}))}
- Countries: {json.dumps(div_data.get('by_country', {}))}
- Currencies: {json.dumps(div_data.get('by_currency', {}))}
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
        return {"error": "Analysis unavailable — LLM returned unexpected format. Try again."}
```

- [ ] **Step 2: Run tests**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_portfolio_analysis.py -v
```
Expected: all tests pass.

- [ ] **Step 3: Format and lint**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run black portf_manager/services/portfolio_advisor.py
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run flake8 portf_manager/services/portfolio_advisor.py --max-line-length=88 --extend-ignore=E203,W503,E501
```

- [ ] **Step 4: Run full suite**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
```
Expected: all previously passing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/services/portfolio_advisor.py tests/unit/test_portfolio_analysis.py
git commit -m "feat: add portfolio_advisor helpers, prompt builder, and response parser"
```

---

### Task 3: Backend endpoints in `research.py`

**Files:**
- Modify: `portf_server/routers/research.py`

These three routes must be added **before** the first `@router.get("/{symbol}")` route so FastAPI doesn't match `portfolio-analysis` as a symbol.

- [ ] **Step 1: Add missing imports to `research.py`**

At the top of `research.py`, ensure these are present (add any that are missing):

```python
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional
```

- [ ] **Step 2: Add `AdvisorSettingsBody` Pydantic model**

After the `ResearchSaveBody` class (around line 51), add:

```python
class AdvisorSettingsBody(BaseModel):
    cache_ttl_hours: int = 24
```

- [ ] **Step 3: Add the three endpoints before the `compare` route**

Immediately before the `@router.get("/compare")` route (around line 163), insert:

```python
# ── Portfolio Health Analysis ─────────────────────────────────────────────────


@router.get("/portfolio-analysis/settings")
async def get_advisor_settings(
    db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    ttl = db.get_setting("advisor_cache_ttl_hours")
    return {"cache_ttl_hours": int(ttl) if ttl else 24}


@router.put("/portfolio-analysis/settings")
async def put_advisor_settings(
    body: AdvisorSettingsBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    ttl = max(1, min(168, body.cache_ttl_hours))
    db.set_setting("advisor_cache_ttl_hours", str(ttl))
    return {"cache_ttl_hours": ttl}


@router.get("/portfolio-analysis")
def get_portfolio_analysis(
    portfolio_id: Optional[int] = None,
    refresh: bool = False,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Run (or return cached) LLM portfolio health analysis."""
    from portf_manager.llm_client import get_llm_client
    from portf_manager.services.portfolio_advisor import (
        build_analysis_prompt,
        gather_diversification,
        gather_fees_and_dividends,
        gather_holdings_fundamentals,
        gather_performance,
        gather_risk,
        gather_tax,
        parse_analysis_response,
    )

    cache_key = f"portf:advisor:{portfolio_id or 'all'}"
    ttl_raw = db.get_setting("advisor_cache_ttl_hours")
    ttl_secs = int(ttl_raw) * 3600 if ttl_raw else 24 * 3600

    # Force-refresh: delete cached entry
    if refresh:
        try:
            db.conn.execute("DELETE FROM kv_cache WHERE key = ?", (cache_key,))
            db.conn.commit()
        except Exception:
            pass
    else:
        # Return cached result if still valid
        row = db.conn.execute(
            "SELECT value, expires_at FROM kv_cache WHERE key = ?", (cache_key,)
        ).fetchone()
        if row and row[1] > time.time():
            try:
                return json.loads(row[0])
            except Exception:
                pass

    # Gather data in parallel (each thread returns (name, data_or_None))
    data_warnings: list[str] = []
    bundle: dict[str, Any] = {}

    def _run(name: str, fn, *args):
        try:
            return name, fn(*args)
        except Exception as e:
            logger.warning(f"Advisor '{name}' failed: {e}")
            return name, None

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [
            pool.submit(_run, "performance", gather_performance, db, portfolio_id),
            pool.submit(_run, "risk", gather_risk, db),
            pool.submit(_run, "diversification", gather_diversification, db, portfolio_id),
            pool.submit(_run, "fees_and_dividends", gather_fees_and_dividends, db, portfolio_id),
            pool.submit(_run, "tax", gather_tax, db, portfolio_id),
            pool.submit(_run, "holdings", gather_holdings_fundamentals, db, portfolio_id),
        ]
        for fut in as_completed(futures):
            name, result = fut.result()
            if result is None:
                data_warnings.append(f"{name} data unavailable")
            else:
                bundle[name] = result

    if not bundle:
        return {"error": "No portfolio data available. Add some transactions first."}

    prompt = build_analysis_prompt(bundle)
    try:
        llm = get_llm_client()
        raw = llm.generate(prompt).strip()
    except Exception as e:
        logger.error(f"LLM call failed for portfolio analysis: {e}")
        return {"error": f"Analysis unavailable — LLM error. Try again."}

    result = parse_analysis_response(raw)
    if "error" not in result:
        from datetime import datetime as _dt
        result["generated_at"] = _dt.utcnow().isoformat()
        result["cache_ttl_hours"] = ttl_secs // 3600
        result["data_warnings"] = data_warnings
        expires = time.time() + ttl_secs
        try:
            db.conn.execute(
                "INSERT OR REPLACE INTO kv_cache (key, value, expires_at) VALUES (?, ?, ?)",
                (cache_key, json.dumps(result), expires),
            )
            db.conn.commit()
        except Exception as e:
            logger.warning(f"Failed to cache advisor result: {e}")

    return result
```

- [ ] **Step 4: Format, lint, full test run**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run black portf_server/routers/research.py
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add portf_server/routers/research.py
git commit -m "feat: add portfolio-analysis and advisor-settings endpoints to research router"
```

---

### Task 4: `apiClient` methods in `pfm_core.js`

**Files:**
- Modify: `web_client/js/pfm_core.js`

- [ ] **Step 1: Add three methods after `researchCompare()`**

Find `async researchCompare()` (around line 1687) and add after its closing `},`:

```javascript
        async getPortfolioAnalysis(portfolioId, refresh = false) {
            const params = new URLSearchParams();
            if (portfolioId) params.set('portfolio_id', portfolioId);
            if (refresh) params.set('refresh', 'true');
            const qs = params.toString();
            const r = await fetch(
                this.baseURL + '/api/v1/research/portfolio-analysis' + (qs ? '?' + qs : ''),
                { headers: { 'X-API-Key': this.apiKey } }
            );
            if (!r.ok) throw new Error(await r.text());
            return r.json();
        },
        async getAdvisorSettings() {
            const r = await fetch(
                this.baseURL + '/api/v1/research/portfolio-analysis/settings',
                { headers: { 'X-API-Key': this.apiKey } }
            );
            if (!r.ok) throw new Error(await r.text());
            return r.json();
        },
        async putAdvisorSettings(cacheTtlHours) {
            const r = await fetch(
                this.baseURL + '/api/v1/research/portfolio-analysis/settings',
                {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                    body: JSON.stringify({ cache_ttl_hours: cacheTtlHours }),
                }
            );
            if (!r.ok) throw new Error(await r.text());
            return r.json();
        },
```

- [ ] **Step 2: Commit**

```bash
git add web_client/js/pfm_core.js
git commit -m "feat: add getPortfolioAnalysis, getAdvisorSettings, putAdvisorSettings to apiClient"
```

---

### Task 5: HTML — panel and Settings TTL dropdown

**Files:**
- Modify: `web_client/index.html`

- [ ] **Step 1: Add Portfolio Health panel**

After the closing `</div>` of the Research page header flex row (after line 1283, before `<!-- Workbench tab -->`), insert:

```html
                    <!-- Portfolio Health Analysis panel -->
                    <div id="portfolioHealthPanel" class="card mb-3">
                        <div class="card-header d-flex align-items-center justify-content-between"
                             style="cursor:pointer;" data-bs-toggle="collapse" data-bs-target="#portfolioHealthBody">
                            <span class="fw-semibold"><i class="bi bi-heart-pulse me-2 text-success"></i>Portfolio Health</span>
                            <span id="portfolioHealthSummary" class="text-muted small"></span>
                        </div>
                        <div id="portfolioHealthBody" class="collapse show">
                            <div class="card-body">
                                <div class="d-flex align-items-center gap-2 mb-3 flex-wrap">
                                    <label class="form-label mb-0 small">Portfolio</label>
                                    <select class="form-select form-select-sm" id="phPortfolioSelect" style="max-width:200px;">
                                        <option value="">All portfolios</option>
                                    </select>
                                    <button class="btn btn-sm btn-primary" id="phRunBtn">
                                        <i class="bi bi-cpu me-1"></i>Run Analysis
                                    </button>
                                    <span id="phTimestamp" class="text-muted small ms-auto"></span>
                                    <button class="btn btn-sm btn-outline-secondary" id="phRefreshBtn" style="display:none;">
                                        <i class="bi bi-arrow-clockwise me-1"></i>Refresh
                                    </button>
                                </div>
                                <div id="phLoading" style="display:none;" class="text-center py-3">
                                    <div class="spinner-border spinner-border-sm text-primary me-2"></div>
                                    <span id="phLoadingText" class="text-muted small">Gathering metrics…</span>
                                </div>
                                <div id="phError" style="display:none;" class="alert alert-danger py-2 small mb-2"></div>
                                <div id="phWarnings" style="display:none;" class="alert alert-info py-2 small mb-2"></div>
                                <div id="phReport" style="display:none;">
                                    <div class="row g-2 mb-3" id="phScores"></div>
                                    <p id="phSummary" class="text-muted small mb-3"></p>
                                    <div id="phRecs"></div>
                                </div>
                            </div>
                        </div>
                    </div>
```

- [ ] **Step 2: Add TTL dropdown to the Settings modal**

In the Settings modal, find the row with `setHideBelowEur` and add a new row immediately after its closing `</div>`:

```html
                                <div class="row mb-2 align-items-center">
                                    <label class="col-sm-5 col-form-label col-form-label-sm">Portfolio Advisor cache</label>
                                    <div class="col-sm-7">
                                        <select class="form-select form-select-sm" id="setAdvisorTtl">
                                            <option value="6">6 hours</option>
                                            <option value="24" selected>24 hours (default)</option>
                                            <option value="168">7 days</option>
                                        </select>
                                    </div>
                                </div>
```

- [ ] **Step 3: Commit**

```bash
git add web_client/index.html
git commit -m "feat: add Portfolio Health panel and advisor TTL row to HTML"
```

---

### Task 6: JS — panel logic and Settings wiring

**Files:**
- Modify: `web_client/js/pfm_features.js`

- [ ] **Step 1: Add `setupPortfolioHealth()` before `setupResearchPage()`**

Insert before `function setupResearchPage()` (around line 2445):

```javascript
function setupPortfolioHealth() {
    const $ = id => document.getElementById(id);
    const SCORE_LABELS = {
        diversification: 'Diversification', risk_adjusted_return: 'Risk / Return',
        income: 'Income', fees: 'Fees', tax_efficiency: 'Tax Efficiency',
    };

    function scoreColor(s) {
        return s >= 8 ? 'success' : s >= 5 ? 'warning' : 'danger';
    }

    function avgScore(scores) {
        const vals = Object.values(scores).map(s => s.score).filter(Number.isFinite);
        return vals.length ? Math.round(vals.reduce((a, b) => a + b, 0) / vals.length) : null;
    }

    function renderReport(data) {
        // Score cards
        const scoresEl = $('phScores');
        scoresEl.innerHTML = '';
        for (const [key, val] of Object.entries(data.scores || {})) {
            const col = document.createElement('div');
            col.className = 'col-6 col-md-4 col-lg-3 col-xl-2';
            col.innerHTML = `<div class="card text-center border-${scoreColor(val.score)} h-100">
                <div class="card-body py-2 px-1">
                    <div class="fs-4 fw-bold text-${scoreColor(val.score)}">${val.score}<small class="fs-6 text-muted fw-normal">/10</small></div>
                    <div class="small fw-semibold">${esc(SCORE_LABELS[key] || key)}</div>
                    <div class="text-muted" style="font-size:0.7rem;">${esc(val.reason || '')}</div>
                </div>
            </div>`;
            scoresEl.appendChild(col);
        }
        // Summary
        $('phSummary').textContent = data.summary || '';
        // Recommendations
        const recs = data.recommendations || [];
        $('phRecs').innerHTML = recs.length
            ? `<ol class="ps-3 mb-0">${recs.map(r =>
                `<li class="mb-2"><span class="badge bg-secondary me-1">${esc(r.category)}</span><strong>${esc(r.action)}</strong><div class="text-muted small">${esc(r.rationale)}</div></li>`
              ).join('')}</ol>`
            : '';
        // Warnings
        const warns = data.data_warnings || [];
        if (warns.length) {
            $('phWarnings').textContent = 'Data note: ' + warns.join('; ');
            $('phWarnings').style.display = '';
        } else {
            $('phWarnings').style.display = 'none';
        }
        // Header summary bar
        const avg = avgScore(data.scores || {});
        $('portfolioHealthSummary').textContent = avg != null ? `Overall health: ${avg}/10` : '';
        // Timestamp
        if (data.generated_at) {
            const ago = Math.round((Date.now() - new Date(data.generated_at + 'Z').getTime()) / 60000);
            $('phTimestamp').textContent = `Last analysed ${ago < 60 ? ago + 'm' : Math.round(ago / 60) + 'h'} ago`;
        }
        $('phReport').style.display = '';
        $('phRefreshBtn').style.display = '';
    }

    const STEPS = ['Gathering metrics…', 'Fetching fundamentals…', 'Analysing…'];
    let _timer = null;

    function startLoading() {
        let i = 0;
        $('phLoadingText').textContent = STEPS[0];
        _timer = setInterval(() => { i = (i + 1) % STEPS.length; $('phLoadingText').textContent = STEPS[i]; }, 4000);
    }
    function stopLoading() { if (_timer) { clearInterval(_timer); _timer = null; } }

    async function runAnalysis(refresh = false) {
        const portfolioId = $('phPortfolioSelect').value || null;
        $('phLoading').style.display = '';
        $('phRunBtn').disabled = true;
        if ($('phRefreshBtn')) $('phRefreshBtn').disabled = true;
        $('phReport').style.display = 'none';
        $('phError').style.display = 'none';
        startLoading();
        try {
            const data = await window.apiClient.getPortfolioAnalysis(portfolioId, refresh);
            if (data.error) {
                $('phError').textContent = data.error;
                $('phError').style.display = '';
            } else {
                renderReport(data);
                // Collapse panel to summary bar
                const bsBody = bootstrap.Collapse.getOrCreateInstance($('portfolioHealthBody'), { toggle: false });
                bsBody.hide();
            }
        } catch (e) {
            $('phError').textContent = 'Failed: ' + e.message;
            $('phError').style.display = '';
        } finally {
            stopLoading();
            $('phLoading').style.display = 'none';
            $('phRunBtn').disabled = false;
            if ($('phRefreshBtn')) $('phRefreshBtn').disabled = false;
        }
    }

    // Populate portfolio dropdown
    (async () => {
        try {
            const portfolios = await window.apiClient.getPortfolios();
            const sel = $('phPortfolioSelect');
            (portfolios || []).forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.id;
                opt.textContent = p.name;
                sel.appendChild(opt);
            });
        } catch (_) { /* ignore */ }
    })();

    $('phRunBtn').addEventListener('click', () => runAnalysis(false));
    $('phRefreshBtn').addEventListener('click', () => runAnalysis(true));
}
```

- [ ] **Step 2: Call `setupPortfolioHealth()` at the start of `setupResearchPage()`**

Inside `setupResearchPage()`, after `page.dataset.wired = '1';`, add:

```javascript
    setupPortfolioHealth();
```

- [ ] **Step 3: Load TTL in `setupSettings`**

Inside the `function load()` block in `setupSettings()`, add at the end:

```javascript
        window.apiClient.getAdvisorSettings().then(s => {
            const el = document.getElementById('setAdvisorTtl');
            if (el && s && s.cache_ttl_hours) el.value = String(s.cache_ttl_hours);
        }).catch(() => {});
```

- [ ] **Step 4: Save TTL in `setupSettings` save handler**

Inside `$('settingsSaveBtn').addEventListener('click', ...)`, before `bs.hide()`, add:

```javascript
        const ttlEl = document.getElementById('setAdvisorTtl');
        if (ttlEl) window.apiClient.putAdvisorSettings(parseInt(ttlEl.value) || 24).catch(() => {});
```

- [ ] **Step 5: Commit**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: wire Portfolio Health panel JS and advisor TTL in Settings"
```

---

### Task 7: Help text

**Files:**
- Modify: `web_client/js/help_text.js`

- [ ] **Step 1: Add entry to `PAGE_HELP`**

In `window.PAGE_HELP`, add alongside `'research'`:

```javascript
    'portfolio-health': {
        title: 'Portfolio Health Analysis',
        body: `<p>The <strong>Portfolio Health</strong> panel gathers all your portfolio metrics and uses an LLM to produce a scored health report with prioritised improvement suggestions.</p>
<ul>
  <li><strong>Scores (1–10):</strong> Diversification, Risk/Return, Income, Fees, Tax Efficiency — green ≥ 8, amber 5–7, red ≤ 4.</li>
  <li><strong>Recommendations:</strong> Up to 5 ranked concrete actions with rationale.</li>
  <li><strong>Portfolio filter:</strong> Analyse all brokers together or narrow to one.</li>
  <li><strong>Cache:</strong> Results are cached (default 24 h). Click <em>Refresh</em> to re-run. Adjust TTL in Settings → Portfolio Advisor cache.</li>
  <li><strong>Slow first run:</strong> Fetching fundamentals per holding takes 10–30 s. Subsequent loads are instant from cache.</li>
</ul>`,
    },
```

- [ ] **Step 2: Commit**

```bash
git add web_client/js/help_text.js
git commit -m "docs: add Portfolio Health help text entry"
```

---

### Task 8: Deploy and smoke-test

- [ ] **Step 1: Rebuild web container**

```bash
docker compose build web && docker stop portf_web && docker compose up -d web
```

- [ ] **Step 2: Navigate to Research page**

Open the app. Go to Research. Confirm "Portfolio Health" panel appears at the top with portfolio dropdown and "Run Analysis" button.

- [ ] **Step 3: Run analysis**

Click "Run Analysis". Verify spinner appears with cycling text, then panel collapses showing `Overall health: X/10` in the header. Expand to confirm score cards (colored), summary text, and numbered recommendations.

- [ ] **Step 4: Test Refresh**

Click "Refresh" — analysis re-runs fresh (not from cache).

- [ ] **Step 5: Test portfolio filter**

Select a specific portfolio, run. Result should differ from "All portfolios" if multiple brokers exist.

- [ ] **Step 6: Test Settings TTL**

Settings → "Portfolio Advisor cache" shows current value. Change to 6h, save, re-open Settings — should persist.

- [ ] **Step 7: Full test suite**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
```
Expected: all tests pass.

- [ ] **Step 8: Final commit if any stray changes remain**

```bash
git status
# add and commit anything untracked
```
