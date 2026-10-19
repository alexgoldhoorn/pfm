"""Spanish antiaplicación rule (art. 33.5.f/g LIRPF, the "2-month rule").

Shared by the MCP ``wash_sale_check`` tool and the next-euro allocator.
"""

import calendar
from datetime import date
from typing import Optional

# A loss on selling securities is not deductible while homogeneous securities
# bought within 2 months before or after the sale (listed) — or 1 year
# (unlisted, e.g. most mutual-fund units) — are still held. Crypto is not
# "valores homogéneos" in the usual reading, so it is left out, as are cash and
# the synthetic MINTOS asset (P2P loan principal, not a security).
LISTED_MONTHS = 2
UNLISTED_MONTHS = 12
_EXCLUDED_TYPES = {"crypto", "cash"}
_EXCLUDED_SYMBOLS = {"MINTOS"}
FUND_NAME_WORDS = {"fund", "idx", "fondo", "fonds"}


def _add_months(d: date, months: int) -> date:
    """Shift a date by calendar months, clamping to the target month's end."""
    index = d.year * 12 + (d.month - 1) + months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _parse_date(value: object) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def is_fund(asset: dict) -> bool:
    """Whether an asset counts as a fund (unlisted units) for the wash-sale rule.

    pfm has no reliable fund type — heuristic imports store index funds as
    "stock", and Indexa's funds are "etf" on the "Funds" exchange — so a fund is
    recognised by type, by that exchange, or (for anything not typed etf) by a
    fund word in its name.
    """
    asset_type = (asset.get("asset_type") or "").lower()
    if asset_type == "mutual_fund" or asset.get("exchange") == "Funds":
        return True
    name = (asset.get("name") or "").lower()
    words = set("".join(c if c.isalnum() else " " for c in name).split())
    return asset_type != "etf" and bool(words & FUND_NAME_WORDS)


def window_months(asset: dict) -> int:
    """1 year for unlisted fund units, 2 months for everything else.

    Erring towards the year is the safe side: a 2-month answer where the law
    says a year costs the deduction.
    """
    return UNLISTED_MONTHS if is_fund(asset) else LISTED_MONTHS


def analyse(
    lots: list,
    transactions: list,
    assets: dict,
    today: date,
    symbol: Optional[str] = None,
    holdings: Optional[list] = None,
) -> dict:
    """Classify loss sales and recent buys against the antiaplicación window.

    Args:
        lots: FIFO realised lots (tax-report ``realised_lots``) — each carries
            symbol, sell_date, purchase_date and gain_loss_eur.
        transactions: All transactions; only ``buy`` rows are used.
        assets: symbol -> asset dict (asset_type, exchange, name), to pick the
            window and skip what the rule does not cover.
        today: Reference date.
        symbol: Optional single-symbol filter (case-insensitive).
        holdings: Optional current positions (symbol, pnl_amount). When given,
            ``recent_buys`` is limited to positions still held, each flagged
            with ``at_loss``.

    Returns:
        ``blocked``: loss sales with a repurchase inside the window. A buy
        before the sale counts only if FIFO did not consume it for that sale
        (its date is not one of the sale's lot purchase dates).
        ``no_rebuy``: loss sales still inside their window with no repurchase
        yet — buying before ``until`` would block the loss.
        ``recent_buys``: symbols bought inside the last window — selling them
        at a loss before ``until`` would be blocked.
    """
    wanted = symbol.upper() if symbol else None
    by_symbol = {str(k).upper(): v or {} for k, v in assets.items()}

    def covered(sym: str) -> bool:
        asset_type = (by_symbol.get(sym, {}).get("asset_type") or "").lower()
        return (
            bool(sym)
            and (wanted is None or sym == wanted)
            and sym not in _EXCLUDED_SYMBOLS
            and asset_type not in _EXCLUDED_TYPES
        )

    def sym_window(sym: str) -> int:
        return window_months(by_symbol.get(sym, {}))

    buys: dict[str, list[tuple[date, float]]] = {}
    names: dict[str, str] = {}
    for t in transactions:
        sym = str(t.get("symbol") or "").upper()
        if str(t.get("transaction_type") or "").lower() != "buy" or not covered(sym):
            continue
        bought = _parse_date(t.get("transaction_date"))
        if bought is None:
            continue
        buys.setdefault(sym, []).append((bought, float(t.get("quantity") or 0)))
        names.setdefault(sym, t.get("name") or "")

    # One sale can span several FIFO lots; judge the sale on their summed result.
    sales: dict[tuple[str, date], dict] = {}
    for lot in lots:
        sym = str(lot.get("symbol") or "").upper()
        sold = _parse_date(lot.get("sell_date"))
        if not covered(sym) or sold is None:
            continue
        gain = lot.get("gain_loss_eur")
        if gain is None:
            gain = lot.get("gain_loss")
        sale = sales.setdefault(
            (sym, sold),
            {
                "result": 0.0,
                "purchases": set(),
                "name": lot.get("name") or names.get(sym, ""),
            },
        )
        sale["result"] += float(gain or 0)
        purchased = _parse_date(lot.get("purchase_date"))
        if purchased:
            sale["purchases"].add(purchased)

    blocked, no_rebuy = [], []
    for (sym, sold), sale in sorted(sales.items(), key=lambda kv: kv[0][1]):
        if sale["result"] >= 0:
            continue
        window = sym_window(sym)
        start, end = _add_months(sold, -window), _add_months(sold, window)
        blocking = sorted(
            (d, q)
            for d, q in buys.get(sym, [])
            if start <= d <= end and d not in sale["purchases"]
        )
        base = {
            "symbol": sym,
            "name": sale["name"],
            "sell_date": sold.isoformat(),
            "loss_eur": round(sale["result"], 2),
        }
        if blocking:
            base["blocking"] = [
                {"date": d.isoformat(), "quantity": q} for d, q in blocking
            ]
            blocked.append(base)
        elif end > today:
            base["until"] = end.isoformat()
            no_rebuy.append(base)
    no_rebuy.sort(key=lambda n: n["until"])

    # A recent buy only matters for a position still held; a symbol can sit in
    # several portfolios, so its P&L is summed across them.
    held: Optional[dict[str, float]] = None
    if holdings is not None:
        held = {}
        for h in holdings:
            sym = str(h.get("symbol") or "").upper()
            held[sym] = held.get(sym, 0.0) + float(h.get("pnl_amount") or 0)

    recent_buys = []
    for sym, rows in buys.items():
        if held is not None and sym not in held:
            continue
        last = max(d for d, _ in rows)
        until = _add_months(last, sym_window(sym))
        if last <= today < until:
            item = {
                "symbol": sym,
                "name": names.get(sym, ""),
                "last_buy": last.isoformat(),
                "until": until.isoformat(),
            }
            if held is not None:
                item["at_loss"] = held[sym] < 0
            recent_buys.append(item)
    recent_buys.sort(key=lambda r: r["until"])
    return {"blocked": blocked, "no_rebuy": no_rebuy, "recent_buys": recent_buys}


def blocked_assets(db, today: date, fx=None) -> dict[int, dict]:
    """Assets whose recent loss sale still blocks a repurchase.

    Runs the tax-report FIFO for this year and last, feeds the realised lots to
    :func:`analyse`, and keeps its ``no_rebuy`` entries: loss sales whose window
    has not closed. The window is ``sale date + window_months`` (calendar
    months); the asset is blocked while that date is after ``today``, so on the
    date itself buying is allowed.

    Args:
        db: Database.
        today: Reference date.
        fx: Unused; the tax report converts at its own transaction-date FX.
            Kept so callers can inject a rate in tests.

    Returns:
        ``{asset_id: {"symbol", "sold_on", "loss_eur", "safe_from"}}`` (ISO
        dates); the latest blocking sale wins per asset.
    """
    # Imported here: the router imports from portf_manager at module level.
    from portf_server.routers.analytics import _build_tax_report_data

    lots: list = []
    for yr in (today.year - 1, today.year):
        lots.extend(_build_tax_report_data(db, yr)["realised_lots"])
    assets = db.get_all_assets()
    by_symbol = {str(a["symbol"]).upper(): a for a in assets if a.get("symbol")}
    out = analyse(lots, db.get_all_transactions(), by_symbol, today)
    result: dict[int, dict] = {}
    for item in out["no_rebuy"]:
        asset = by_symbol.get(item["symbol"])
        if asset is None:
            continue
        result[asset["id"]] = {
            "symbol": item["symbol"],
            "sold_on": item["sell_date"],
            "loss_eur": item["loss_eur"],
            "safe_from": item["until"],
        }
    return result
