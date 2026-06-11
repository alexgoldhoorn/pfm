#!/usr/bin/env python3
"""One-time backfill of assets.ticker from ISIN via Yahoo Finance search.

For each active asset without a ticker:
  - crypto: ticker = "<SYMBOL>-EUR" (yfinance quote convention)
  - ISIN-shaped symbol: resolve via Yahoo search API
  - anything else: symbol is already a market ticker; copy it

Usage:  python3 scripts/backfill_tickers.py [--db portfolio.db] [--dry-run]
"""

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, ".")
from portf_manager.database import Database  # noqa: E402

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def yahoo_lookup(query: str) -> str | None:
    """Return the first quote symbol Yahoo's search API finds for *query*."""
    url = (
        "https://query2.finance.yahoo.com/v1/finance/search?q="
        + urllib.parse.quote(query)
        + "&quotesCount=5&newsCount=0"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"  WARN Yahoo search failed for {query}: {e}", file=sys.stderr)
        return None
    quotes = data.get("quotes") or []
    return quotes[0].get("symbol") if quotes else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="portfolio.db")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = Database(args.db)
    misses = []
    for a in db.get_all_assets(active_only=True):
        if a.get("ticker"):
            continue
        sym = a["symbol"]
        if a["asset_type"] == "crypto":
            ticker = f"{sym}-EUR"
        elif ISIN_RE.match(sym):
            ticker = yahoo_lookup(sym)
            time.sleep(1)  # be polite to Yahoo
        else:
            ticker = sym
        if ticker:
            print(f"{sym:15} {a['name'][:35]:35} -> {ticker}")
            if not args.dry_run:
                db.update_asset(a["id"], ticker=ticker)
        else:
            misses.append(a)

    if misses:
        print("\nUnresolved — set manually with:", file=sys.stderr)
        print(
            "  curl -X PUT -H \"X-API-Key: $KEY\" -H 'Content-Type: application/json' \\",
            file=sys.stderr,
        )
        print(
            '       -d \'{"ticker": "XXX"}\' http://localhost:8000/api/v1/assets/<id>',
            file=sys.stderr,
        )
        for a in misses:
            print(f"  id={a['id']:4} {a['symbol']:15} {a['name']}", file=sys.stderr)


if __name__ == "__main__":
    main()
