"""Broker cash ledger: the cash balance each portfolio holds, in EUR."""

from typing import Any, Callable, Optional


def cash_by_portfolio(
    db, fx_on: Callable[[str, Any], float], transactions: Optional[list] = None
) -> dict[int, dict]:
    """Compute the EUR cash balance and last activity date per portfolio.

    Deposits, sells, dividends and interest add cash; withdrawals and buys
    remove it. Each amount is converted at the FX rate of its own date, so the
    figure matches the broker's statement and does not drift with live rates.
    Transactions on ``cash``-type assets are skipped: such an asset is already
    a position, so counting its trades as cash would double count.

    Args:
        db: Database instance.
        fx_on: ``fx_on(currency, on_date)`` returning the EUR rate on that date.
        transactions: Optional pre-loaded transactions, to avoid a second read.

    Returns:
        ``{portfolio_id: {"cash_eur": float, "as_of": "YYYY-MM-DD"}}``. A
        portfolio with no transactions and no bookings is absent.
    """
    if transactions is None:
        transactions = db.get_all_transactions()
    # Per-asset (currency, is_cash) lookups, memoised.
    assets: dict = {}

    def asset_info(aid: int) -> tuple:
        if aid not in assets:
            a = db.get_asset(aid)
            cur = (a.get("currency", "EUR") if a else "EUR") or "EUR"
            assets[aid] = (cur, bool(a) and a.get("asset_type") == "cash")
        return assets[aid]

    result: dict[int, dict] = {}

    def add(pid, amount: float, on_date) -> None:
        entry = result.setdefault(pid, {"cash_eur": 0.0, "as_of": None})
        entry["cash_eur"] += amount
        day = str(on_date)[:10] if on_date else None
        if day and (entry["as_of"] is None or day > entry["as_of"]):
            entry["as_of"] = day

    for tx in transactions:
        currency, is_cash = asset_info(tx["asset_id"])
        if is_cash:
            continue
        on_date = tx.get("transaction_date")
        amt = float(tx["total_amount"] or 0) * fx_on(currency, on_date)
        kind = tx["transaction_type"].lower()
        if kind == "buy":
            add(tx.get("portfolio_id"), -amt, on_date)
        elif kind in ("sell", "dividend", "interest"):
            add(tx.get("portfolio_id"), amt, on_date)
    for bk in db.get_all_bookings():
        amt = float(bk["amount"] or 0) * fx_on(
            bk.get("currency", "EUR") or "EUR", bk.get("date")
        )
        add(
            bk.get("portfolio_id"),
            amt if bk.get("action") == "Deposit" else -amt,
            bk.get("date"),
        )
    return result
