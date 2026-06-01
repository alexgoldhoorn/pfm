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

from portf_manager.llm_client import get_llm_client

logger = logging.getLogger(__name__)

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


def fetch_fundamentals(symbol: str) -> dict[str, Any]:
    """Pull key fundamentals from yfinance for *symbol*."""
    try:
        info = yf.Ticker(symbol).info
        data = {k: info.get(k) for k in _FUNDAMENTAL_FIELDS if info.get(k) is not None}
        data["symbol"] = symbol
        return data
    except Exception as e:
        logger.warning(f"Could not fetch fundamentals for {symbol}: {e}")
        return {"symbol": symbol}


def generate_valuation_report(
    symbol: str,
    asset_name: str,
    asset_type: str,
    current_price: float,
    avg_cost: float,
    currency: str,
    fundamentals: dict[str, Any],
) -> dict[str, Any]:
    """
    Call the LLM to produce a valuation report.

    Returns a dict with keys:
      fair_value, recommendation (BUY/HOLD/SELL), confidence (high/medium/low),
      summary (≤3 sentences), rationale, risks, catalysts, price_targets
    """
    llm = get_llm_client()

    fund_str = json.dumps(
        {k: v for k, v in fundamentals.items() if k != "symbol"},
        indent=2,
        default=str,
    )

    prompt = f"""You are a professional equity analyst. Analyse the following position and produce a structured valuation report.

POSITION:
- Symbol: {symbol}
- Name: {asset_name}
- Type: {asset_type}
- Current price: {current_price} {currency}
- Investor's average cost: {avg_cost} {currency}
- Unrealised P&L vs cost: {((current_price - avg_cost) / avg_cost * 100):.1f}%

FUNDAMENTALS (from Yahoo Finance):
{fund_str}

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
        raw = llm.generate(prompt).strip()
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
        }
