"""Resolve Yahoo Finance tickers for ISIN-keyed assets via OpenFIGI."""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

import requests
import yfinance as yf

logger = logging.getLogger(__name__)

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
_BATCH_SIZE = 10
_CURRENCY_EXCHANGE_PREFERENCE = {
    "EUR": ["GS", "XETRA", "EAM", "EPA", "AMS", "MIL"],
    "GBP": ["LN"],
    "USD": ["US", "UQ", "UN"],
    "CHF": ["SW"],
}


def is_isin(symbol: str) -> bool:
    return bool(_ISIN_RE.match(symbol or ""))


def _openfigi_batch(isins: list[str]) -> dict[str, list[dict]]:
    """Query OpenFIGI for a batch of ISINs. Returns {isin: [result, ...]}."""
    payload = [{"idType": "ID_ISIN", "idValue": isin} for isin in isins]
    try:
        resp = requests.post(
            _OPENFIGI_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception as e:
        logger.warning(f"OpenFIGI request failed: {e}")
        return {}

    out: dict[str, list[dict]] = {}
    for isin, entry in zip(isins, results):
        if "data" in entry:
            out[isin] = entry["data"]
        else:
            out[isin] = []
    return out


def _pick_best_ticker(candidates: list[dict], currency: str) -> Optional[str]:
    """Pick the best Yahoo Finance ticker from OpenFIGI results.

    Preference order:
    1. Matches the asset's currency on a preferred exchange for that currency.
    2. Any equity/ETF/open-ended fund with a ticker on a major exchange.
    3. First candidate with a ticker.
    """
    preferred_exchanges = _CURRENCY_EXCHANGE_PREFERENCE.get(currency, [])

    def _score(c: dict) -> int:
        exch = c.get("exchCode", "")
        sec = c.get("securityType", "")
        if not c.get("ticker"):
            return -1
        # Prefer equity / ETF / Open-End Fund
        type_ok = sec in ("Common Stock", "ETP", "Open-End Fund", "Mutual Fund")
        exch_preferred = exch in preferred_exchanges
        return (2 if exch_preferred else 0) + (1 if type_ok else 0)

    ranked = sorted(candidates, key=_score, reverse=True)
    for c in ranked:
        if c.get("ticker") and _score(c) >= 0:
            return c["ticker"]
    return None


def _yf_ticker_for_exchange(ticker: str, exchcode: str, candidates: list[dict]) -> str:
    """Convert an OpenFIGI ticker + exchCode to a Yahoo Finance symbol.

    Yahoo uses suffixes: XETRA → .DE, LSE → .L, Euronext Paris → .PA, etc.
    """
    _SUFFIX = {
        "GS": ".DE",  # XETRA
        "XETRA": ".DE",
        "EAM": ".MC",  # Bolsa Madrid
        "EPA": ".PA",  # Euronext Paris
        "AMS": ".AS",  # Euronext Amsterdam
        "MIL": ".MI",  # Borsa Milano
        "LN": ".L",  # London Stock Exchange
        "SW": ".SW",  # SIX Swiss
        "VX": ".VX",
        "HK": ".HK",
        "TO": ".TO",
        "AU": ".AX",
    }
    # Try to find the exchCode for this ticker in the candidates list
    for c in candidates:
        if c.get("ticker") == ticker:
            code = c.get("exchCode", "")
            suffix = _SUFFIX.get(code, "")
            return f"{ticker}{suffix}"
    suffix = _SUFFIX.get(exchcode, "")
    return f"{ticker}{suffix}"


def _verify_yf(yf_sym: str) -> bool:
    """Return True if yfinance can fetch a price for this symbol."""
    try:
        info = yf.Ticker(yf_sym).fast_info
        price = info.get("lastPrice") or info.get("regularMarketPrice")
        return price is not None and price > 0
    except Exception:
        return False


def resolve_ticker_for_isin(isin: str, currency: str = "EUR") -> Optional[str]:
    """Return the best Yahoo Finance ticker for a single ISIN, or None."""
    results = _openfigi_batch([isin])
    candidates = results.get(isin, [])
    if not candidates:
        return None

    base_ticker = _pick_best_ticker(candidates, currency)
    if not base_ticker:
        return None

    # Find the exchCode for the chosen ticker
    exchcode = next(
        (c.get("exchCode", "") for c in candidates if c.get("ticker") == base_ticker),
        "",
    )
    yf_sym = _yf_ticker_for_exchange(base_ticker, exchcode, candidates)

    # Verify with yfinance; if that fails, try without suffix
    if _verify_yf(yf_sym):
        return yf_sym
    if yf_sym != base_ticker and _verify_yf(base_ticker):
        return base_ticker
    return None


def resolve_tickers_bulk(
    assets: list[dict],
) -> dict[int, Optional[str]]:
    """Resolve Yahoo Finance tickers for a list of asset dicts with ISIN symbols.

    Returns {asset_id: ticker_or_None}.
    Skips assets that already have a ticker or whose symbol is not an ISIN.
    """
    to_resolve = [
        a
        for a in assets
        if is_isin(a.get("symbol", "")) and not (a.get("ticker") or "").strip()
    ]
    if not to_resolve:
        return {}

    # Batch ISIN lookups
    isin_to_asset: dict[str, dict] = {a["symbol"]: a for a in to_resolve}
    isins = list(isin_to_asset)
    openfigi_results: dict[str, list[dict]] = {}
    for i in range(0, len(isins), _BATCH_SIZE):
        batch = isins[i : i + _BATCH_SIZE]
        openfigi_results.update(_openfigi_batch(batch))
        if i + _BATCH_SIZE < len(isins):
            time.sleep(0.5)  # respect rate limit

    out: dict[int, Optional[str]] = {}
    for isin, asset in isin_to_asset.items():
        candidates = openfigi_results.get(isin, [])
        if not candidates:
            out[asset["id"]] = None
            continue

        base_ticker = _pick_best_ticker(candidates, asset.get("currency", "EUR"))
        if not base_ticker:
            out[asset["id"]] = None
            continue

        exchcode = next(
            (
                c.get("exchCode", "")
                for c in candidates
                if c.get("ticker") == base_ticker
            ),
            "",
        )
        yf_sym = _yf_ticker_for_exchange(base_ticker, exchcode, candidates)

        if _verify_yf(yf_sym):
            out[asset["id"]] = yf_sym
        elif yf_sym != base_ticker and _verify_yf(base_ticker):
            out[asset["id"]] = base_ticker
        else:
            out[asset["id"]] = None

        time.sleep(0.1)  # avoid hammering yfinance

    return out
