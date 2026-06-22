"""
CSV export helpers for third-party portfolio platforms.

Supported platforms:
  - Yahoo Finance  (transactions or positions, mode="transactions"|"positions")
  - Simply Wall St (transactions or positions)
"""

import csv
import io
from typing import Optional

from portf_manager.positions import compute_positions


def _is_isin(s: str) -> bool:
    return len(s) == 12 and s[:2].isalpha() and s[2:].isalnum()


def _resolve_ticker(symbol: str, ticker: Optional[str]) -> Optional[str]:
    if ticker:
        return ticker
    if not _is_isin(symbol):
        return symbol
    return None


def _fetch_buy_sell_txs(db, portfolio_id: Optional[int]) -> list[dict]:
    query = """
        SELECT
            t.id, t.asset_id, t.transaction_type,
            t.quantity, t.price, t.total_amount, t.fees,
            t.transaction_date,
            COALESCE(t.currency, a.currency) AS currency,
            a.symbol, a.name, a.ticker,
            a.currency AS asset_currency
        FROM transactions t
        JOIN assets a ON t.asset_id = a.id
        WHERE t.transaction_type IN ('buy', 'sell')
    """
    params: list = []
    if portfolio_id is not None:
        query += " AND t.portfolio_id = ?"
        params.append(portfolio_id)
    query += " ORDER BY t.transaction_date ASC"

    with db.get_connection() as conn:
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def _build_asset_meta(txs: list[dict]) -> dict[int, dict]:
    meta: dict[int, dict] = {}
    for tx in txs:
        aid = tx["asset_id"]
        if aid not in meta:
            meta[aid] = {
                "symbol": tx["symbol"],
                "ticker": tx["ticker"],
                "asset_currency": tx.get("asset_currency", ""),
            }
    return meta


def build_yahoo_finance_csv(
    db, portfolio_id: Optional[int], mode: str
) -> tuple[str, list[str]]:
    txs = _fetch_buy_sell_txs(db, portfolio_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["Symbol", "Shares", "Purchase Price", "Purchase Date", "Commission"]
    )

    skipped: list[str] = []
    seen_skipped: set[str] = set()

    if mode == "positions":
        asset_meta = _build_asset_meta(txs)
        positions, _ = compute_positions(txs)
        for asset_id, pos in positions.items():
            if pos["quantity"] <= 0:
                continue
            meta = asset_meta.get(asset_id, {})
            sym = meta.get("symbol", str(asset_id))
            ticker = _resolve_ticker(sym, meta.get("ticker"))
            if ticker is None:
                if sym not in seen_skipped:
                    skipped.append(sym)
                    seen_skipped.add(sym)
                continue
            writer.writerow([ticker, round(pos["quantity"], 8), "", "", "0"])
    else:
        for tx in txs:
            ticker = _resolve_ticker(tx["symbol"], tx["ticker"])
            if ticker is None:
                sym = tx["symbol"]
                if sym not in seen_skipped:
                    skipped.append(sym)
                    seen_skipped.add(sym)
                continue
            shares = (
                tx["quantity"] if tx["transaction_type"] == "buy" else -tx["quantity"]
            )
            date_str = ""
            raw = str(tx.get("transaction_date", ""))[:10]
            parts = raw.split("-")
            if len(parts) == 3:
                date_str = f"{parts[1]}/{parts[2]}/{parts[0]}"
            writer.writerow(
                [
                    ticker,
                    round(shares, 8),
                    round(tx["price"], 4) if tx.get("price") else "",
                    date_str,
                    round(tx["fees"], 2) if tx.get("fees") else "0.00",
                ]
            )

    return buf.getvalue(), skipped


def build_simply_wall_st_csv(
    db, portfolio_id: Optional[int], mode: str
) -> tuple[str, list[str]]:
    txs = _fetch_buy_sell_txs(db, portfolio_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Ticker Symbol",
            "Number of Shares",
            "Purchase Price (Per Share)",
            "Purchase Date",
            "Currency",
        ]
    )

    skipped: list[str] = []
    seen_skipped: set[str] = set()

    if mode == "positions":
        asset_meta = _build_asset_meta(txs)
        positions, _ = compute_positions(txs)
        for asset_id, pos in positions.items():
            if pos["quantity"] <= 0:
                continue
            meta = asset_meta.get(asset_id, {})
            sym = meta.get("symbol", str(asset_id))
            ticker = _resolve_ticker(sym, meta.get("ticker"))
            if ticker is None:
                if sym not in seen_skipped:
                    skipped.append(sym)
                    seen_skipped.add(sym)
                continue
            writer.writerow(
                [
                    ticker,
                    round(pos["quantity"], 8),
                    "",
                    "",
                    meta.get("asset_currency", ""),
                ]
            )
    else:
        for tx in txs:
            ticker = _resolve_ticker(tx["symbol"], tx["ticker"])
            if ticker is None:
                sym = tx["symbol"]
                if sym not in seen_skipped:
                    skipped.append(sym)
                    seen_skipped.add(sym)
                continue
            shares = (
                tx["quantity"] if tx["transaction_type"] == "buy" else -tx["quantity"]
            )
            date_str = str(tx.get("transaction_date", ""))[:10]
            writer.writerow(
                [
                    ticker,
                    round(shares, 8),
                    round(tx["price"], 4) if tx.get("price") else "",
                    date_str,
                    tx.get("currency", ""),
                ]
            )

    return buf.getvalue(), skipped
