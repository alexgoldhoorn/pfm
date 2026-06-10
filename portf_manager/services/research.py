"""
Research & Valuation Service

Fetches fundamentals from yfinance and uses the LLM to produce:
- Fair value estimate
- Buy / Hold / Sell recommendation
- Confidence level
- 3-line summary + detailed JSON report
"""

from __future__ import annotations

import json
import logging
from typing import Any

import yfinance as yf

import os

from portf_manager.llm_client import OpenRouterLLMClient, get_llm_client

logger = logging.getLogger(__name__)


def _is_rate_limited(exc: Exception) -> bool:
    msg = str(exc)
    return "429" in msg or "quota" in msg.lower() or "rate limit" in msg.lower()


# Fields pulled from yfinance Ticker.info
_FUNDAMENTAL_FIELDS = [
    "shortName",
    "sector",
    "industry",
    "country",
    "currentPrice",
    "previousClose",
    "fiftyTwoWeekLow",
    "fiftyTwoWeekHigh",
    "marketCap",
    "trailingPE",
    "forwardPE",
    "priceToBook",
    "trailingEps",
    "forwardEps",
    "revenueGrowth",
    "earningsGrowth",
    "returnOnEquity",
    "returnOnAssets",
    "debtToEquity",
    "freeCashflow",
    "operatingCashflow",
    "totalRevenue",
    "grossMargins",
    "operatingMargins",
    "profitMargins",
    "dividendYield",
    "payoutRatio",
    "beta",
    "recommendationKey",
    "targetMeanPrice",
    "numberOfAnalystOpinions",
]


def fetch_fundamentals(symbol: str, db=None) -> dict[str, Any]:
    """Pull key fundamentals from yfinance for *symbol*.

    When *db* is supplied the result is cached for ~6h (fundamentals change
    roughly quarterly), so repeated research lookups don't re-hit yfinance.
    """
    if db is not None:
        try:
            hit = db.cache_get(f"yf:fund:{symbol}")
            if hit is not None:
                return hit
        except Exception as e:
            logger.warning(f"fundamentals cache_get failed for {symbol}: {e}")
    try:
        info = yf.Ticker(symbol).info
        data = {k: info.get(k) for k in _FUNDAMENTAL_FIELDS if info.get(k) is not None}
        data["symbol"] = symbol
    except Exception as e:
        logger.warning(f"Could not fetch fundamentals for {symbol}: {e}")
        return {"symbol": symbol}
    # Only cache a genuine hit (more than just the symbol key).
    if db is not None and len(data) > 1:
        try:
            db.cache_set(f"yf:fund:{symbol}", data, 6 * 3600)
        except Exception as e:
            logger.warning(f"fundamentals cache_set failed for {symbol}: {e}")
    return data


def fetch_recent_news(symbol: str, limit: int = 6, db=None) -> list[dict]:
    """Recent news headlines + links for *symbol* (web context + citations).

    Uses yfinance's news feed — live, no API key — so the LLM analysis is
    grounded in current news and we can store the article URLs as sources.
    When *db* is given the result is cached ~30min (news moves, but not by the
    minute) so repeated lookups don't re-hit yfinance.
    """
    if db is not None:
        try:
            hit = db.cache_get(f"yf:news:{symbol}")
            if hit is not None:
                return hit
        except Exception as e:
            logger.warning(f"news cache_get failed for {symbol}: {e}")
    out = []
    try:
        for item in (yf.Ticker(symbol).news or [])[:limit]:
            # yfinance shapes vary; support both flat and {'content': {...}} forms.
            c = item.get("content", item)
            title = c.get("title") or item.get("title")
            link = (
                (c.get("canonicalUrl") or {}).get("url")
                or c.get("clickThroughUrl", {}).get("url")
                or item.get("link")
            )
            pub = (
                (c.get("provider") or {}).get("displayName")
                or item.get("publisher")
                or ""
            )
            if title and link:
                out.append({"title": title, "url": link, "publisher": pub})
    except Exception as e:
        logger.warning(f"Could not fetch news for {symbol}: {e}")
    if db is not None and out:
        try:
            db.cache_set(f"yf:news:{symbol}", out, 30 * 60)
        except Exception as e:
            logger.warning(f"news cache_set failed for {symbol}: {e}")
    return out


def compute_targets(
    fundamentals: dict, method: str, assumptions: dict
) -> dict[str, Any]:
    """Deterministic valuation calculator → fair_value + buy/sell targets.

    Transparent, override-able math (the LLM only *suggests* the inputs):
      - "pe": fair_value = EPS × target_pe
      - "dividend_yield": fair_value = annual dividend ÷ target_yield
    buy_below / sell_above are derived from fair_value with a margin of safety
    and a take-profit premium.
    """
    a = assumptions or {}
    fair = None
    if method == "pe":
        eps = _num(a.get("eps", fundamentals.get("trailingEps")))
        target_pe = _num(a.get("target_pe"))
        if eps and target_pe:
            fair = eps * target_pe
    elif method == "dividend_yield":
        dividend = _num(a.get("annual_dividend", fundamentals.get("dividendRate")))
        target_yield = _num(a.get("target_yield"))  # in % (e.g. 4 = 4%)
        if dividend and target_yield:
            fair = dividend / (target_yield / 100.0)
    mos = _num(a.get("margin_of_safety", 20)) or 0  # %
    premium = _num(a.get("premium", 20)) or 0  # %
    buy_below = round(fair * (1 - mos / 100.0), 4) if fair else None
    sell_above = round(fair * (1 + premium / 100.0), 4) if fair else None
    return {
        "method": method,
        "fair_value": round(fair, 4) if fair else None,
        "buy_below": buy_below,
        "sell_above": sell_above,
    }


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def generate_valuation_report(
    symbol: str,
    asset_name: str,
    asset_type: str,
    current_price: float,
    avg_cost: float,
    currency: str,
    fundamentals: dict[str, Any],
    news: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Call the LLM to produce a valuation report.

    Returns a dict with keys:
      fair_value, recommendation (BUY/HOLD/SELL), confidence (high/medium/low),
      summary (≤3 sentences), rationale, risks, catalysts, price_targets
    """
    fund_str = json.dumps(
        {k: v for k, v in fundamentals.items() if k != "symbol"},
        indent=2,
        default=str,
    )
    # Not-yet-owned tickers have no cost basis → guard the P&L line.
    if avg_cost and avg_cost > 0:
        pnl_str = f"{((current_price - avg_cost) / avg_cost * 100):.1f}%"
    else:
        pnl_str = "n/a (not currently held)"

    prompt = f"""You are a professional equity analyst. Analyse the following position and produce a structured valuation report.

POSITION:
- Symbol: {symbol}
- Name: {asset_name}
- Type: {asset_type}
- Current price: {current_price} {currency}
- Investor's average cost: {avg_cost} {currency}
- Unrealised P&L vs cost: {pnl_str}

FUNDAMENTALS (from Yahoo Finance):
{fund_str}

RECENT NEWS HEADLINES (consider these for catalysts/risks; do not invent others):
{chr(10).join(f"- {n['title']} ({n.get('publisher', '')})" for n in (news or [])) or "- (none available)"}

Return ONLY a valid JSON object with exactly these fields:
{{
  "fair_value": <float — your intrinsic/fair value estimate in {currency}, or null if insufficient data>,
  "recommendation": "<BUY | HOLD | SELL>",
  "confidence": "<high | medium | low>",
  "summary": "<2-3 sentence plain-English summary of the investment case>",
  "rationale": "<why you give this recommendation, max 100 words>",
  "risks": ["<risk 1>", "<risk 2>", "<risk 3>"],
  "catalysts": ["<catalyst 1>", "<catalyst 2>"],
  "buy_below": <float — price below which the stock is attractive, or null>,
  "sell_above": <float — price above which you would take profit, or null>
}}

Be concise and data-driven. If this is a crypto, ETF, or P2P asset where DCF does not apply, base the recommendation on momentum, relative value, and risk/reward instead.
"""

    try:
        llm = get_llm_client()
        try:
            raw = llm.generate(prompt).strip()
        except Exception as e:
            if _is_rate_limited(e):
                or_key = os.getenv("OPENROUTER_API_KEY") or os.getenv(
                    "PORTF_OPENROUTER_API_KEY"
                )
                if or_key:
                    logger.warning(
                        f"Primary LLM rate-limited for {symbol}, retrying with OpenRouter"
                    )
                    raw = OpenRouterLLMClient(api_key=or_key).generate(prompt).strip()
                else:
                    raise
            else:
                raise
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = "\n".join(
                line for line in raw.splitlines() if not line.strip().startswith("```")
            )
        report = json.loads(raw)
        # Validate required keys
        for key in ("recommendation", "confidence", "summary"):
            if key not in report:
                raise ValueError(f"Missing key: {key}")
        report["sources"] = news or []
        return report
    except Exception as e:
        logger.error(f"LLM valuation failed for {symbol}: {e}")
        return {
            "fair_value": None,
            "recommendation": "HOLD",
            "confidence": "low",
            "summary": f"Could not generate automated analysis for {symbol}: {e}",
            "rationale": "",
            "risks": [],
            "catalysts": [],
            "buy_below": None,
            "sell_above": None,
            "sources": news or [],
        }
