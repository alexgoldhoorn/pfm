"""Balance sheet: every asset and liability as a sourced, dated line.

One builder feeds net worth, the emergency-fund metric and the balance-sheet
page, so they can never disagree. Each line says where its figure comes from
and how old it is; a line past its freshness limit is flagged ``stale``.
"""

from datetime import date, datetime, timedelta
from typing import Callable, Optional

from portf_manager.positions import compute_positions
from portf_manager.services import loans as loan_math
from portf_manager.services.cash_ledger import cash_by_portfolio

# Freshness limits in days (None = never stale).
MAX_AGE_POSITIONS = 4
MAX_AGE_VALUATION = 7
MAX_AGE_BANK = 45
MAX_AGE_BROKER_CASH = 60
MAX_AGE_MANUAL_CASH = 90
MAX_AGE_MANUAL_PROPERTY = 365
MAX_AGE_MANUAL_OTHER = 180
# A loan rate older than this is flagged as possibly out of date.
LOAN_RATE_STALE_DAYS = 400
# Deposits maturing within this many days count as liquid.
LIQUID_DEPOSIT_DAYS = 365

_CASH_CATEGORIES = {"cash", "current_account", "savings_account"}
_INVESTMENT_CATEGORIES = {"pension", "investment_external"}

GROUPS = [
    ("cash", "Cash"),
    ("deposits", "Fixed deposits"),
    ("investments", "Investments"),
    ("property", "Property"),
    ("other_assets", "Other assets"),
    ("liabilities", "Liabilities"),
]


def _to_date(value) -> Optional[date]:
    """Parse a date, datetime or 'YYYY-MM-DD...' string; None when unusable."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _manual_group(item: dict) -> str:
    """Balance-sheet group of a manual line."""
    if item.get("is_liability"):
        return "liabilities"
    category = (item.get("category") or "").lower()
    if category in _CASH_CATEGORIES:
        return "cash"
    if category == "property":
        return "property"
    if category in _INVESTMENT_CATEGORIES:
        return "investments"
    return "other_assets"


def _manual_max_age(category: str) -> int:
    """Freshness limit of a manual line by category."""
    category = (category or "").lower()
    if category in _CASH_CATEGORIES:
        return MAX_AGE_MANUAL_CASH
    if category == "property":
        return MAX_AGE_MANUAL_PROPERTY
    return MAX_AGE_MANUAL_OTHER


def _plural(n: int, noun: str) -> str:
    """'1 asset' / '2 assets'."""
    return f"{n} {noun}" + ("" if n == 1 else "s")


def build_balance_sheet(
    db,
    today: Optional[date] = None,
    fx: Optional[Callable[[str], float]] = None,
    fx_on: Optional[Callable[[str, object], float]] = None,
) -> dict:
    """Build the balance sheet.

    Args:
        db: Database instance.
        today: The reference date; defaults to today.
        fx: ``fx(currency)`` returning the live EUR rate.
        fx_on: ``fx_on(currency, day)`` returning the EUR rate on a date.

    Returns:
        ``{"as_of", "totals", "groups"}``; see the spec for the line shape.
    """
    today = today or date.today()
    if fx is None or fx_on is None:
        from portf_server.routers.portfolios import _get_fx_rate, _get_fx_rate_on

        fx = fx or _get_fx_rate
        fx_on = fx_on or (lambda cur, day: _get_fx_rate_on(db, cur, day))

    lines: list[dict] = []

    def add(
        line_id: str,
        label: str,
        group: str,
        amount: Optional[float],
        currency: str,
        amount_eur: Optional[float],
        source: str,
        as_of,
        max_age: Optional[int],
        edit: dict,
        note: Optional[str] = None,
        force_stale: bool = False,
        in_total: bool = True,
    ) -> None:
        """Append one line, computing age and staleness."""
        day = _to_date(as_of)
        age = (today - day).days if day else None
        if max_age is None:
            stale = False
        else:
            stale = age is None or age > max_age
        lines.append(
            {
                "id": line_id,
                "label": label,
                "group": group,
                "amount_eur": None if amount_eur is None else round(amount_eur, 2),
                "amount": None if amount is None else round(amount, 2),
                "currency": currency,
                "source": source,
                "as_of": day.isoformat() if day else None,
                "age_days": age,
                "max_age_days": max_age,
                "stale": stale or force_stale,
                "note": note,
                "edit": edit,
                "in_total": in_total,
            }
        )

    portfolios = db.get_all_portfolios()
    names = {p["id"]: p["name"] for p in portfolios}
    brokerage_ids = {p["id"] for p in portfolios if p.get("account_type") != "bank"}
    valuations = {v["portfolio_id"]: v for v in db.get_account_valuations()}

    # A fresh account valuation replaces that portfolio's positions and cash.
    fresh_valuations = set()
    valuation_notes: dict = {}
    for pid, val in valuations.items():
        day = _to_date(val.get("as_of"))
        if day is None or day.year <= 1970:
            valuation_notes[pid] = "never fetched"
        elif (today - day).days <= MAX_AGE_VALUATION:
            fresh_valuations.add(pid)
        else:
            valuation_notes[pid] = f"{(today - day).days} days old"

    # Positions per portfolio.
    transactions = db.get_all_transactions()
    positions, _ = compute_positions(
        transactions, key=lambda tx: (tx.get("portfolio_id"), tx["asset_id"])
    )
    per_portfolio: dict = {}
    for (pid, asset_id), pos in positions.items():
        if pos["quantity"] <= 1e-9:
            continue
        asset = db.get_asset(asset_id)
        if not asset:
            continue
        price = db.get_latest_price(asset_id)
        entry = per_portfolio.setdefault(
            pid, {"eur": 0.0, "oldest": None, "unpriced": 0, "auto": 0, "undated": 0}
        )
        if price:
            entry["eur"] += (
                pos["quantity"]
                * float(price["price"])
                * fx(asset.get("currency") or "EUR")
            )
        auto = bool(asset.get("auto_price", 1))
        if not auto:
            continue
        entry["auto"] += 1
        if not price:
            entry["unpriced"] += 1
            continue
        day = _to_date(price.get("price_date"))
        if day is None:
            entry["undated"] += 1
        if day and (entry["oldest"] is None or day < entry["oldest"]):
            entry["oldest"] = day
    for pid, entry in per_portfolio.items():
        if pid in fresh_valuations:
            continue
        notes = []
        if entry["unpriced"]:
            notes.append(f"{_plural(entry['unpriced'], 'asset')} unpriced")
        if pid in valuation_notes:
            notes.append(
                "valued at fund NAVs, account valuation "
                + (
                    "never fetched"
                    if valuation_notes[pid] == "never fetched"
                    else valuation_notes[pid]
                )
            )
        add(
            f"positions:{pid or 0}",
            names.get(pid, "Unassigned positions"),
            "investments",
            entry["eur"],
            "EUR",
            entry["eur"],
            "positions",
            entry["oldest"],
            MAX_AGE_POSITIONS if entry["auto"] else None,
            {"kind": "positions", "id": pid or 0},
            note="; ".join(notes) or None,
            force_stale=bool(entry["unpriced"] or entry["undated"]),
        )

    # Broker cash.
    for pid, entry in cash_by_portfolio(db, fx_on, transactions).items():
        if pid not in brokerage_ids or pid in fresh_valuations:
            continue
        if abs(entry["cash_eur"]) < 0.005:
            continue
        # a broker cannot hold negative cash: the ledger is incomplete
        negative = entry["cash_eur"] < 0
        add(
            f"cash:{pid}",
            f"{names[pid]} cash",
            "cash",
            entry["cash_eur"],
            "EUR",
            entry["cash_eur"],
            "cash_ledger",
            entry["as_of"],
            MAX_AGE_BROKER_CASH,
            {"kind": "positions", "id": pid},
            note="ledger cash is negative: deposits missing?" if negative else None,
            force_stale=negative,
            in_total=not negative,
        )

    # Fresh account valuations.
    for pid in fresh_valuations:
        val = valuations[pid]
        currency = val.get("currency") or "EUR"
        value = float(val["value"] or 0)
        add(
            f"valuation:{pid}",
            names.get(pid, f"Portfolio {pid}"),
            "investments",
            value,
            currency,
            value * fx(currency),
            "account_valuation",
            val["as_of"],
            MAX_AGE_VALUATION,
            {"kind": "valuation", "id": pid},
            note=(
                f"last fetch failed: {val['last_error']}"
                if val.get("last_error")
                else None
            ),
        )

    # A stale or never-fetched valuation on a portfolio with no positions line
    # would otherwise vanish: keep its stored value, flagged stale.
    for pid, why in valuation_notes.items():
        if pid in per_portfolio:
            continue
        val = valuations[pid]
        currency = val.get("currency") or "EUR"
        value = float(val["value"] or 0)
        never = why == "never fetched"
        add(
            f"valuation:{pid}",
            names.get(pid, f"Portfolio {pid}"),
            "investments",
            value,
            currency,
            value * fx(currency),
            "account_valuation",
            None if never else val["as_of"],
            MAX_AGE_VALUATION,
            {"kind": "valuation", "id": pid},
            note=f"account valuation {why}",
            force_stale=True,
            in_total=not (never and value == 0),
        )

    # Bank accounts.
    for p in portfolios:
        if p.get("account_type") != "bank":
            continue
        latest = db.get_latest_bank_balance(p["id"])
        edit = {"kind": "bank", "id": p["id"]}
        if latest is None:
            add(
                f"bank:{p['id']}",
                p["name"],
                "cash",
                None,
                p.get("base_currency") or "EUR",
                None,
                "bank_statement",
                None,
                MAX_AGE_BANK,
                edit,
                note="no statement with a balance",
                in_total=False,
            )
            continue
        currency = latest.get("currency") or "EUR"
        balance = float(latest["balance"])
        add(
            f"bank:{p['id']}",
            p["name"],
            "cash",
            balance,
            currency,
            balance * fx(currency),
            "bank_statement",
            latest["date"],
            MAX_AGE_BANK,
            edit,
        )

    # Fixed deposits.
    deposits = db.get_fixed_deposits(status="active")
    for d in deposits:
        currency = d.get("currency") or "EUR"
        principal = float(d["principal"])
        maturity = _to_date(d.get("maturity_date"))
        add(
            f"deposit:{d['id']}",
            d["name"],
            "deposits",
            principal,
            currency,
            principal * fx(currency),
            "fixed_deposit",
            d.get("start_date"),
            None,
            {"kind": "deposit", "id": d["id"]},
            note=(
                "past maturity, mark matured" if maturity and maturity < today else None
            ),
        )

    # Loans.
    for loan in db.get_loans():
        balance = loan_math.balance_on(loan, today)
        if balance <= 0.005:
            continue
        currency = loan.get("currency") or "EUR"
        notes = [
            f"{loan_math.payment_of(loan):,.2f} a month, "
            f"ends {loan_math.end_date(loan).isoformat()}"
        ]
        rate_day = _to_date(loan.get("rate_as_of"))
        if rate_day and (today - rate_day).days > LOAN_RATE_STALE_DAYS:
            notes.append(f"rate last confirmed {rate_day.isoformat()}")
        add(
            f"loan:{loan['id']}",
            loan["name"],
            "liabilities",
            balance,
            currency,
            balance * fx(currency),
            "loan_schedule",
            today,
            None,
            {"kind": "loan", "id": loan["id"]},
            note="; ".join(notes),
        )

    # Manual lines.
    portfolio_names = {n.lower() for n in names.values()}
    for item in db.get_manual_assets():
        currency = item.get("currency") or "EUR"
        amount = float(item["amount"] or 0)
        note = item.get("notes") or None
        if (item["name"] or "").lower() in portfolio_names:
            warn = "a portfolio with this name exists; is this line still needed?"
            note = f"{note}; {warn}" if note else warn
        add(
            f"manual:{item['id']}",
            item["name"],
            _manual_group(item),
            amount,
            currency,
            amount * fx(currency),
            "manual",
            item.get("as_of") or item.get("updated_at"),
            _manual_max_age(item.get("category")),
            {"kind": "manual", "id": item["id"]},
            note=note,
        )

    groups = []
    totals_by_group = {}
    for key, label in GROUPS:
        members = [ln for ln in lines if ln["group"] == key]
        total = round(
            sum((ln["amount_eur"] or 0.0) for ln in members if ln["in_total"]), 2
        )
        totals_by_group[key] = total
        groups.append(
            {"key": key, "label": label, "total_eur": total, "lines": members}
        )

    assets = sum(v for k, v in totals_by_group.items() if k != "liabilities")
    liabilities = totals_by_group["liabilities"]
    horizon = today + timedelta(days=LIQUID_DEPOSIT_DAYS)
    liquid_deposits = 0.0
    for d in deposits:
        maturity = _to_date(d.get("maturity_date"))
        if maturity and maturity <= horizon:
            liquid_deposits += float(d["principal"]) * fx(d.get("currency") or "EUR")
    return {
        "as_of": today.isoformat(),
        "totals": {
            "assets_eur": round(assets, 2),
            "liabilities_eur": round(liabilities, 2),
            "net_worth_eur": round(assets - liabilities, 2),
            "financial_assets_eur": round(
                totals_by_group["cash"]
                + totals_by_group["deposits"]
                + totals_by_group["investments"],
                2,
            ),
            "liquid_eur": round(totals_by_group["cash"] + liquid_deposits, 2),
            "stale_lines": sum(1 for ln in lines if ln["stale"]),
        },
        "groups": groups,
    }
