"""
Stock analysis report generator.

Produces a terminal report for a given ticker using yfinance for prices and
optional news via NewsAPI or SerpAPI, then summarizes with Gemini.

Rules respected:
- Use black code formatting.
- If adding comments, do it the line before.
"""

from __future__ import annotations

import os
import math
import json
from dataclasses import dataclass
from typing import Dict, List, Optional

import yfinance as yf
import requests

from .llm_client import get_llm_client

# Place comments on the line before per user rule
HORIZONS = {
    "1d": 1,
    "5d": 5,
    "1M": 21,
    "3M": 63,
    "1Y": 252,
    "5Y": 252 * 5,
    "all": None,
}


@dataclass
class PriceSummary:
    current: float
    changes_pct: Dict[str, Optional[float]]
    ann_vol_30d_pct: Optional[float]
    ann_vol_90d_pct: Optional[float]
    currency: Optional[str]


@dataclass
class NewsItem:
    title: str
    url: str
    published_at: str
    source: Optional[str] = None


def _pct(a: float, b: float) -> Optional[float]:
    """
    Compute percentage change from b to a.
    """
    # Place comments on the line before per user rule
    try:
        if b is None or b == 0:
            return None
        return (a / b - 1.0) * 100.0
    except Exception:
        return None


def fetch_price_summary(symbol: str) -> PriceSummary:
    """
    Fetch OHLCV history with yfinance and compute percent changes and volatility.
    """
    # Place comments on the line before per user rule
    t = yf.Ticker(symbol)
    hist = t.history(period="max")

    # Place comments on the line before per user rule
    if hist.empty:
        raise ValueError(f"No price history for {symbol}")

    # Place comments on the line before per user rule
    close = hist["Close"].dropna()
    current = float(close.iloc[-1])

    # Place comments on the line before per user rule
    changes: Dict[str, Optional[float]] = {}
    for k, n in HORIZONS.items():
        if n is None:
            # Place comments on the line before per user rule
            base = float(close.iloc[0])
            changes[k] = _pct(current, base)
        else:
            # Place comments on the line before per user rule
            if len(close) > n:
                base = float(close.iloc[-n])
                changes[k] = _pct(current, base)
            else:
                changes[k] = None

    # Place comments on the line before per user rule
    retn = close.pct_change().dropna()
    ann_vol_30 = (
        float(retn.tail(30).std() * math.sqrt(252)) if len(retn) >= 30 else None
    )
    ann_vol_90 = (
        float(retn.tail(90).std() * math.sqrt(252)) if len(retn) >= 90 else None
    )

    # Place comments on the line before per user rule
    info = {}
    try:
        info = t.get_info() if hasattr(t, "get_info") else (t.info or {})
    except Exception:
        info = {}
    currency = info.get("currency") if isinstance(info, dict) else None

    # Place comments on the line before per user rule
    return PriceSummary(
        current=current,
        changes_pct=changes,
        ann_vol_30d_pct=ann_vol_30 * 100.0 if ann_vol_30 is not None else None,
        ann_vol_90d_pct=ann_vol_90 * 100.0 if ann_vol_90 is not None else None,
        currency=currency,
    )


def fetch_news_newsapi(query: str, page_size: int = 10) -> List[NewsItem]:
    """
    Fetch recent news using NewsAPI if NEWSAPI_KEY is set.
    """
    # Place comments on the line before per user rule
    key = os.getenv("NEWSAPI_KEY")
    if not key:
        return []
    url = (
        "https://newsapi.org/v2/everything"
        f"?q={requests.utils.quote(query)}&language=en&sortBy=publishedAt&pageSize={page_size}&apiKey={key}"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    items: List[NewsItem] = []
    for a in data.get("articles", [])[:page_size]:
        items.append(
            NewsItem(
                title=a.get("title", ""),
                url=a.get("url", ""),
                published_at=a.get("publishedAt", ""),
                source=(a.get("source") or {}).get("name"),
            )
        )
    return items


def fetch_news_serpapi(query: str, num: int = 10) -> List[NewsItem]:
    """
    Fetch recent news using SerpAPI if SERPAPI_API_KEY is set.
    """
    # Place comments on the line before per user rule
    key = os.getenv("SERPAPI_API_KEY")
    if not key:
        return []
    url = (
        "https://serpapi.com/search.json"
        f"?engine=google&q={requests.utils.quote(query)}&tbm=nws&num={num}&api_key={key}"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    items: List[NewsItem] = []
    for a in data.get("news_results", [])[: num or 10]:
        items.append(
            NewsItem(
                title=a.get("title", ""),
                url=a.get("link", ""),
                published_at=a.get("date", ""),
                source=a.get("source"),
            )
        )
    return items


def summarize_with_llm(symbol: str, price: PriceSummary, news: List[NewsItem]) -> str:
    """
    Use the configured LLM to generate the final analysis text.
    """
    # Build the data payload
    payload = {
        "symbol": symbol.upper(),
        "currency": price.currency,
        "current_price": price.current,
        "changes_pct": price.changes_pct,
        "volatility_pct": {
            "ann_vol_30d": price.ann_vol_30d_pct,
            "ann_vol_90d": price.ann_vol_90d_pct,
        },
        "news": [n.__dict__ for n in news],
    }

    # Build the prompt
    system = (
        "You are a financial analysis assistant.\n"
        "Given structured inputs, produce: \n"
        "- multi-horizon price summary (1d, 5d, 1M, 3M, 1Y, 5Y, all) with % changes,\n"
        "- important events that plausibly explain moves (cite headlines),\n"
        "- volatility and key metrics (use provided vol; do not invent unavailable ratios),\n"
        "- a concise summary,\n"
        "- 1-month and 3-month directional probabilities (bullish/bearish/neutral) that sum to 100%.\n"
        "Be conservative and avoid speculation. If news is empty, state that clearly."
    )

    prompt = (
        f"SYSTEM:\n{system}\n\n"
        f"DATA (JSON):\n{json.dumps(payload)}\n\n"
        "Return a clean markdown report suitable for a terminal."
    )

    try:
        llm = get_llm_client()
        return llm.generate(prompt)
    except Exception as e:
        return f"LLM error: {e}"


def render_basic_report(symbol: str, price: PriceSummary, news: List[NewsItem]) -> str:
    """
    Fallback human-readable report if Gemini is unavailable.
    """
    # Place comments on the line before per user rule
    lines: List[str] = []
    lines.append(f"Stock report for {symbol.upper()}")
    lines.append("")
    curr = f" {price.currency}" if price.currency else ""
    lines.append(f"Current price: {price.current:.2f}{curr}")
    lines.append("Changes (%):")
    for k in ["1d", "5d", "1M", "3M", "1Y", "5Y", "all"]:
        v = price.changes_pct.get(k)
        lines.append(f"  {k}: {v:.2f}%" if v is not None else f"  {k}: n/a")
    if price.ann_vol_30d_pct is not None:
        lines.append(f"Ann. vol (30d): {price.ann_vol_30d_pct:.2f}%")

    if price.ann_vol_90d_pct is not None:
        lines.append(f"Ann. vol (90d): {price.ann_vol_90d_pct:.2f}%")
    lines.append("")
    if news:
        lines.append("Recent news:")
        for n in news[:10]:
            src = f" ({n.source})" if n.source else ""
            lines.append(f"- {n.published_at}: {n.title}{src} - {n.url}")
    else:
        lines.append("No recent news available (missing API key or provider).")
        lines.append("")
    lines.append(
        "Conclusion: Without LLM summary, consider recent momentum and volatility; assess catalysts from listed headlines."
    )
    return "\n".join(lines)


def run_stock_report(symbol: str, news_provider: Optional[str] = None) -> str:
    """
    Generate and return a stock report for the given symbol.
    """
    # Place comments on the line before per user rule
    price = fetch_price_summary(symbol)

    # Place comments on the line before per user rule
    news: List[NewsItem] = []
    prov = (news_provider or os.getenv("NEWS_PROVIDER", "")).lower()
    if prov == "newsapi":
        news = fetch_news_newsapi(symbol)
    elif prov == "serpapi":
        news = fetch_news_serpapi(symbol)
    else:
        # Place comments on the line before per user rule
        news = (
            fetch_news_newsapi(symbol)
            or fetch_news_serpapi(symbol)
            or fetch_news_google_rss(symbol)
        )

    # Use the configured LLM provider for the summary
    report = summarize_with_llm(symbol, price, news)
    if report.strip():
        return report
    return render_basic_report(symbol, price, news)


__all__ = [
    "run_stock_report",
    "fetch_price_summary",
    "fetch_news_newsapi",
    "fetch_news_serpapi",
]


def fetch_news_google_rss(query: str, num: int = 10) -> List[NewsItem]:
    """
    Fetch recent headlines via Google News RSS (no key). Best-effort parser.
    """
    # Place comments on the line before per user rule
    import xml.etree.ElementTree as ET

    url = (
        "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&"
        f"q={requests.utils.quote(query)}"
    )
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        items: List[NewsItem] = []
        for item in root.findall(".//item")[:num]:
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            pub = (
                item.findtext("{http://purl.org/dc/elements/1.1/}date")
                or item.findtext("pubDate")
                or ""
            )
            source = "Google News"
            items.append(
                NewsItem(title=title, url=link, published_at=pub, source=source)
            )
        return items
    except Exception:
        return []
