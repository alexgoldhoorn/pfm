"""On-demand price update service — shared by CLI and API trigger endpoint."""

from datetime import datetime
from typing import Optional


_CRYPTO_YF_OVERRIDES: dict[str, tuple[str, str]] = {
    "UNI": ("UNI7083-USD", "USD"),
    "SUI": ("SUI20947-USD", "USD"),
}


def run_price_update(db, symbols: Optional[list[str]] = None) -> dict:
    """Fetch and store the latest prices for all (or specified) active assets.

    Args:
        db: PortfolioDatabase instance.
        symbols: If given, only update these symbols.  None → all active assets
            with auto_price enabled.

    Returns:
        Dict with keys: updated_count, skipped_count, error_count,
        skipped_symbols, error_symbols, api_errors, duration_seconds.
    """
    from portf_manager.api_client import (
        APIError,
        DataNotFoundError,
        get_client,
    )

    api_client = get_client()
    run_started = datetime.now()

    if symbols:
        assets_to_update = [
            db.get_asset_by_symbol(s.upper())
            for s in symbols
            if db.get_asset_by_symbol(s.upper())
        ]
    else:
        assets_to_update = [
            a for a in db.get_all_assets(active_only=True) if a.get("auto_price", 1)
        ]

    updated_count = 0
    skipped_symbols: list[str] = []
    error_symbols: list[str] = []
    api_errors: list[str] = []

    if not assets_to_update:
        duration = (datetime.now() - run_started).total_seconds()
        db.record_price_update_run(
            started_at=run_started.isoformat(),
            duration_seconds=duration,
            updated_count=0,
            skipped_count=0,
            error_count=0,
            skipped_symbols=[],
            error_symbols=[],
            api_errors=[],
            source="api",
        )
        return {
            "updated_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "skipped_symbols": [],
            "error_symbols": [],
            "api_errors": [],
            "duration_seconds": duration,
        }

    yf_to_db: dict[str, str] = {}
    yf_quote_ccy: dict[str, Optional[str]] = {}
    for asset in assets_to_update:
        sym = asset["symbol"]
        if asset.get("asset_type") == "crypto":
            yf_ticker, quote_ccy = _CRYPTO_YF_OVERRIDES.get(sym, (f"{sym}-EUR", "EUR"))
            yf_to_db[yf_ticker] = sym
            yf_quote_ccy[yf_ticker] = quote_ccy
        else:
            yf_ticker = asset.get("ticker") or sym
            yf_to_db[yf_ticker] = sym
            yf_quote_ccy[yf_ticker] = None

    try:
        prices_raw = api_client.fetch_latest_prices(list(yf_to_db.keys()))

        _fx_cache: dict[str, float] = {}

        def _to_eur(price: float, quote_ccy: Optional[str]) -> float:
            if not quote_ccy or quote_ccy == "EUR":
                return price
            if quote_ccy not in _fx_cache:
                rate = api_client.get_fx_rate(quote_ccy, "EUR")
                _fx_cache[quote_ccy] = float(rate) if rate else None
            rate = _fx_cache[quote_ccy]
            return price * rate if rate else price

        prices_data = {
            yf_to_db[yf_sym]: _to_eur(price, yf_quote_ccy.get(yf_sym))
            for yf_sym, price in prices_raw.items()
            if yf_sym in yf_to_db
        }

        for asset in assets_to_update:
            sym = asset["symbol"]
            try:
                if sym in prices_data:
                    db.insert_price_record(
                        symbol=sym,
                        price=prices_data[sym],
                        fetched_ts=datetime.now(),
                        source="yfinance",
                    )
                    updated_count += 1
                else:
                    skipped_symbols.append(sym)
            except Exception as e:
                error_symbols.append(sym)
                api_errors.append(f"DB error for {sym}: {e}")

    except DataNotFoundError as e:
        api_errors.append(f"Data not found: {e}")
    except APIError as e:
        api_errors.append(f"API error: {e}")
    except Exception as e:
        api_errors.append(f"Unexpected error: {e}")

    duration = (datetime.now() - run_started).total_seconds()
    db.record_price_update_run(
        started_at=run_started.isoformat(),
        duration_seconds=duration,
        updated_count=updated_count,
        skipped_count=len(skipped_symbols),
        error_count=len(error_symbols),
        skipped_symbols=skipped_symbols,
        error_symbols=error_symbols,
        api_errors=api_errors,
        source="api",
    )
    return {
        "updated_count": updated_count,
        "skipped_count": len(skipped_symbols),
        "error_count": len(error_symbols),
        "skipped_symbols": skipped_symbols,
        "error_symbols": error_symbols,
        "api_errors": api_errors,
        "duration_seconds": duration,
    }
