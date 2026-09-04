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

# OpenFIGI/Bloomberg exchange code -> the currency that venue trades in.
# Used to reject a candidate whose only listing is in the wrong currency: an
# ISIN like a EUR ETF can map to a same-ticker security on a US venue (priced
# in USD) and, resolved without a suffix, yfinance happily returns that wrong
# security. Not exhaustive — an unknown code is treated as "currency unknown"
# and left to the yfinance verification step.
_CURRENCY_BY_EXCHANGE = {
    # Germany (Xetra + regional)
    "GR": "EUR",
    "GF": "EUR",
    "GS": "EUR",
    "GM": "EUR",
    "GD": "EUR",
    "GH": "EUR",
    "GB": "EUR",
    "GT": "EUR",
    "GY": "EUR",
    # Euronext + other euro-zone venues
    "FP": "EUR",
    "NA": "EUR",
    "IM": "EUR",
    "SM": "EUR",
    "SQ": "EUR",
    "BR": "EUR",
    "LS": "EUR",
    "FH": "EUR",
    "GA": "EUR",
    "PW": "EUR",
    "AV": "EUR",
    "MT": "EUR",
    "ID": "EUR",
    # legacy/pretty codes some feeds emit
    "XETRA": "EUR",
    "EAM": "EUR",
    "EPA": "EUR",
    "AMS": "EUR",
    "MIL": "EUR",
    # UK
    "LN": "GBP",
    "LI": "GBP",
    # US
    "US": "USD",
    "UN": "USD",
    "UQ": "USD",
    "UP": "USD",
    "UA": "USD",
    "UW": "USD",
    "UR": "USD",
    "UF": "USD",
    "UV": "USD",
    "UD": "USD",
    "PQ": "USD",
    # Switzerland
    "SW": "CHF",
    "SE": "CHF",
    "SR": "CHF",
    "VX": "CHF",
    "SX": "CHF",
    # Nordics
    "SS": "SEK",
    "DC": "DKK",
    "NO": "NOK",
}


def _exchange_currency(exch_code: str) -> Optional[str]:
    """The trading currency for an OpenFIGI exchange code, or None if unknown."""
    return _CURRENCY_BY_EXCHANGE.get((exch_code or "").upper())


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

    Currency-match guard: a candidate listed on a venue known to trade in a
    *different* currency than the asset is disqualified outright. Same-ticker
    listings across currencies are common (a EUR ETF and an unrelated US stock
    can share "PRAB"), and without a Yahoo suffix the wrong one wins at
    verification time.
    """
    want_ccy = (currency or "").upper()
    preferred_exchanges = _CURRENCY_EXCHANGE_PREFERENCE.get(want_ccy, [])

    def _score(c: dict) -> int:
        exch = c.get("exchCode", "")
        sec = c.get("securityType", "")
        if not c.get("ticker"):
            return -1
        # Disqualify a listing whose venue trades in the wrong currency.
        exch_ccy = _exchange_currency(exch)
        if want_ccy and exch_ccy and exch_ccy != want_ccy:
            return -1
        # Prefer equity / ETF / Open-End Fund
        type_ok = sec in ("Common Stock", "ETP", "Open-End Fund", "Mutual Fund")
        exch_preferred = exch in preferred_exchanges
        # A venue we positively know trades the right currency beats an
        # unknown one, even when it isn't in the hand-picked preference list.
        exch_ccy_ok = exch_ccy == want_ccy
        return (
            (2 if exch_preferred else 0)
            + (1 if exch_ccy_ok else 0)
            + (1 if type_ok else 0)
        )

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
        # OpenFIGI / Bloomberg exchange codes
        "GR": ".DE",  # Deutsche Börse Xetra
        "GY": ".DE",
        "GS": ".DE",  # Stuttgart — route to the (same-ticker) Xetra symbol
        "GF": ".F",  # Frankfurt
        "GM": ".MU",  # Munich
        "GD": ".DU",  # Düsseldorf
        "GH": ".HM",  # Hamburg
        "GB": ".BE",  # Berlin
        "FP": ".PA",  # Euronext Paris
        "NA": ".AS",  # Euronext Amsterdam
        "IM": ".MI",  # Borsa Italiana Milan
        "SM": ".MC",  # Bolsa de Madrid
        "SQ": ".MC",
        "BR": ".BR",  # Euronext Brussels
        "LS": ".LS",  # Euronext Lisbon
        "LN": ".L",  # London Stock Exchange
        "LI": ".L",
        "SW": ".SW",  # SIX Swiss
        "SE": ".SW",
        "VX": ".VX",
        "SS": ".ST",  # Nasdaq Stockholm
        "DC": ".CO",  # Nasdaq Copenhagen
        "NO": ".OL",  # Oslo Børs
        "FH": ".HE",  # Nasdaq Helsinki
        "PW": ".VI",  # Wiener Börse
        # legacy/pretty codes some feeds emit
        "XETRA": ".DE",
        "EAM": ".MC",
        "EPA": ".PA",
        "AMS": ".AS",
        "MIL": ".MI",
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


def _verify_yf(yf_sym: str, expected_currency: Optional[str] = None) -> bool:
    """Return True if yfinance can fetch a price for this symbol.

    When ``expected_currency`` is given, the quote's own currency must match it
    (GBX/GBp treated as GBP). This is the second half of the currency-match
    guard: a suffix-less ticker that resolved to a same-name security on a
    foreign venue is rejected here rather than stored. A quote with no
    currency field is not rejected — that's a yfinance data gap, not a
    mismatch.
    """
    try:
        info = yf.Ticker(yf_sym).fast_info
        price = info.get("lastPrice") or info.get("regularMarketPrice")
        if price is None or price <= 0:
            return False
        if expected_currency:
            got = (info.get("currency") or "").upper()
            want = expected_currency.upper()
            got = "GBP" if got in ("GBX", "GBP") else got
            want = "GBP" if want in ("GBX", "GBP") else want
            if got and got != want:
                return False
        return True
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
    if _verify_yf(yf_sym, currency):
        return yf_sym
    if yf_sym != base_ticker and _verify_yf(base_ticker, currency):
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

        want_ccy = asset.get("currency", "EUR")
        if _verify_yf(yf_sym, want_ccy):
            out[asset["id"]] = yf_sym
        elif yf_sym != base_ticker and _verify_yf(base_ticker, want_ccy):
            out[asset["id"]] = base_ticker
        else:
            out[asset["id"]] = None

        time.sleep(0.1)  # avoid hammering yfinance

    return out
