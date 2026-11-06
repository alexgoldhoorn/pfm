#!/usr/bin/env python3
"""
Demo account generator for pfm (Portfolio Manager).

Builds a self-contained ``portfolio_demo.db`` with a realistic, *fictional*
portfolio so the product can be demoed and screenshotted without touching any
real data. Everything here is synthetic sample data — it is not anyone's real
holdings.

What it creates:
  - a ``demo`` / ``demo`` login plus a fixed demo API key (for the web UI)
  - several broker portfolios (EUR base)
  - real, live-priceable tickers across regions + asset classes
    (US stocks, EUR-listed ETFs, a UK GBX stock, crypto) so daily price
    refresh and FX conversion populate current value
  - buys spread over ~2 years, DCA top-ups, a couple of sells (realised
    gains for the FIFO tax report), and cash dividends
  - deposits (bookings) so cash-flow / money-weighted IRR look right
  - ~12 months of daily net-worth snapshots (for the chart + risk metrics)
  - a watchlist, a FIRE goal, and rebalancing allocation targets

Usage:
    python scripts/create_demo_account.py            # writes portfolio_demo.db
    python scripts/create_demo_account.py --fresh     # delete any existing one first

Then populate live prices and view it — see scripts/DEMO.md (printed at the end).
"""

import argparse
import math
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

# Repo root is the parent of scripts/ — make the package importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portf_manager.database import Database  # noqa: E402
from portf_manager.auth import AuthManager  # noqa: E402
from portf_manager.models import AssetType, TransactionType  # noqa: E402

DEMO_DB = "portfolio_demo.db"
DEMO_API_KEY = "demo-key-0000000000000000000000000000000000000000000000000000000000"

# Deterministic output so screenshots are reproducible.
random.seed(42)


def _iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _fetch_prices(db: "Database", asset_ids: dict) -> int:
    """Fetch today's close from Yahoo for each demo symbol and store it.

    The demo symbols are already valid yfinance tickers. GBX (pence) quotes
    are normalised to the asset's currency (GBP) by dividing by 100. Returns
    the number of prices stored; degrades gracefully if yfinance/network is
    unavailable (holdings then show cost basis until prices are refreshed).
    """
    try:
        import yfinance as yf
    except Exception:
        print("⚠️  yfinance not available — skipping price fetch")
        return 0
    today = date.today().isoformat()
    stored = 0
    for sym, aid in asset_ids.items():
        try:
            fi = yf.Ticker(sym).fast_info
            px = float(fi["last_price"])
            if fi.get("currency") == "GBp":
                px /= 100.0
            db.create_price(
                asset_id=aid,
                price=round(px, 6),
                price_date=today,
                price_type="close",
                source="yfinance-demo",
            )
            stored += 1
        except Exception:
            pass
    return stored


def _months_ago(n: int, day: int) -> date:
    """The given day of the month *n* months before the current one."""
    today = date.today()
    y, m = today.year, today.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(day, 28))


def _build_household(db: "Database") -> None:
    """Fictional household finances: one bank account with a year of
    categorised spending, a budget, a mortgage, property, a pension plan, a
    fixed deposit, a retirement plan and a short decision journal. All names,
    merchants and amounts are invented."""
    import json

    bank_id = db.get_or_create_portfolio(
        "Demo Bank", base_currency="EUR", account_type="bank"
    )

    # Category tree under the built-in Income / Spend roots.
    with db.get_connection() as conn:
        roots = {
            r["name"]: r["id"]
            for r in conn.execute(
                "SELECT id, name FROM spending_categories WHERE is_root = 1"
            )
        }
    spend_children = [
        "Housing",
        "Groceries",
        "Utilities",
        "Transport",
        "Restaurants",
        "Subscriptions",
        "Kids",
        "Health",
        "Holidays",
    ]
    for name in ("Salary", "Interest"):
        db.create_spending_category(name, parent_id=roots["Income"])
    for name in spend_children:
        db.create_spending_category(name, parent_id=roots["Spend"])

    # Merchant rules (pattern → category), so the Rules tab has content.
    rules = [
        ("NOMINA ACME", "Salary"),
        ("SUPERMERCADO SOL", "Groceries"),
        ("MERCADO CENTRAL", "Groceries"),
        ("ELECTRA ENERGIA", "Utilities"),
        ("AGUAS DEL VALLE", "Utilities"),
        ("FIBRANET", "Utilities"),
        ("GASOLINERA RUTA", "Transport"),
        ("STREAMFLIX", "Subscriptions"),
        ("GYM CENTRO", "Subscriptions"),
        ("ESCUELA ARCOIRIS", "Kids"),
        ("FARMACIA PLAZA", "Health"),
        ("CUOTA HIPOTECA", "Housing"),
    ]
    for pattern, cat in rules:
        db.create_spending_rule(pattern, cat, portfolio_id=bank_id)

    # 12 months of rows. (description, merchant, category, amount, day)
    monthly = [
        ("NOMINA ACME SL", "Acme SL", "Salary", 3850.00, 27),
        ("CUOTA HIPOTECA DEMO BANK", "Demo Bank", "Housing", -985.40, 1),
        ("ELECTRA ENERGIA FACTURA", "Electra Energía", "Utilities", -78.00, 6),
        ("AGUAS DEL VALLE", "Aguas del Valle", "Utilities", -31.50, 12),
        ("FIBRANET FIBRA+MOVIL", "FibraNet", "Utilities", -45.00, 3),
        ("STREAMFLIX SUSCRIPCION", "StreamFlix", "Subscriptions", -12.99, 9),
        ("GYM CENTRO CUOTA", "Gym Centro", "Subscriptions", -39.00, 2),
        ("ESCUELA ARCOIRIS RECIBO", "Escuela Arcoíris", "Kids", -165.00, 5),
    ]
    rows = []
    for n in range(11, -1, -1):
        for desc, merchant, cat, amount, day in monthly:
            d = _months_ago(n, day)
            if d > date.today():
                continue
            # Energy bills swing with the season.
            if cat == "Utilities" and merchant == "Electra Energía":
                amount = round(amount * random.uniform(0.75, 1.35), 2)
            rows.append((d, desc, merchant, cat, amount))
        for day in (4, 11, 18, 25):
            d = _months_ago(n, day)
            if d <= date.today():
                rows.append(
                    (
                        d,
                        "SUPERMERCADO SOL COMPRA",
                        "Supermercado Sol",
                        "Groceries",
                        -round(random.uniform(70, 145), 2),
                    )
                )
        extras = [
            ("MERCADO CENTRAL", "Mercado Central", "Groceries", (25, 60), 14),
            ("GASOLINERA RUTA", "Gasolinera Ruta", "Transport", (45, 75), 10),
            ("GASOLINERA RUTA", "Gasolinera Ruta", "Transport", (40, 70), 24),
            ("RESTAURANTE LA HUERTA", "La Huerta", "Restaurants", (35, 90), 16),
            ("PIZZERIA BELLA", "Pizzeria Bella", "Restaurants", (20, 45), 22),
            ("FARMACIA PLAZA", "Farmacia Plaza", "Health", (8, 40), 19),
        ]
        for desc, merchant, cat, (lo, hi), day in extras:
            d = _months_ago(n, day)
            if d <= date.today():
                rows.append((d, desc, merchant, cat, -round(random.uniform(lo, hi), 2)))
        if n in (4, 5):  # summer holiday
            d = _months_ago(n, 8)
            rows.append(
                (d, "HOTEL COSTA AZUL", "Hotel Costa Azul", "Holidays", -640.00)
            )
    rows.sort(key=lambda r: r[0])
    balance = 9200.0
    for d, desc, merchant, cat, amount in rows:
        balance = round(balance + amount, 2)
        db.create_spending_transaction(
            portfolio_id=bank_id,
            date=d.isoformat(),
            description=desc,
            amount=amount,
            currency="EUR",
            category=cat,
            source="demo",
            balance=balance,
            merchant=merchant,
        )
    print(f"🧾 bank rows: {len(rows)} (closing balance €{balance:,.0f})")

    # Budget with planned monthly amounts per category.
    budget_id = db.create_budget(
        "Household plan", description="Demo monthly budget", is_active=True
    )
    budget_lines = [
        ("income", "Salary", 3850),
        ("spending", "Groceries", 520),
        ("spending", "Utilities", 160),
        ("spending", "Transport", 110),
        ("spending", "Restaurants", 80),
        ("spending", "Subscriptions", 52),
        ("spending", "Kids", 165),
        ("spending", "Health", 25),
        ("spending", "Holidays", 100),
        ("debt", "Housing", 985),
    ]
    for line_type, ref, amount in budget_lines:
        db.create_budget_line(budget_id, line_type, ref, monthly_amount=amount)
    db.set_active_budget(budget_id)

    # Balance sheet: property, pension, mortgage, a fixed deposit.
    as_of = _iso(20)
    db.create_manual_asset(
        "Home (estimated value)", category="property", amount=310000.0, as_of=as_of
    )
    db.create_manual_asset(
        "Pension plan", category="pension", amount=26500.0, as_of=as_of
    )
    db.create_manual_asset(
        "Savings account", category="savings_account", amount=6000.0, as_of=as_of
    )
    for label, cat, amount in (
        ("Salary (net)", "salary", 3850.0),
        ("Mortgage payment", "mortgage", 953.0),
        ("Household expenses", "rest", 1450.0),
    ):
        db.create_monthly_cashflow(label, cat, amount)
    db.create_manual_asset(
        "Car", category="other", amount=9000.0, as_of=as_of, notes="Demo"
    )
    start = _months_ago(74, 15)
    db.create_loan(
        "Mortgage",
        principal=210000.0,
        annual_rate_pct=2.6,
        start_date=start.isoformat(),
        term_months=300,
        lender="Demo Bank",
        rate_as_of=_iso(120),
    )
    db.create_fixed_deposit(
        "12-month deposit",
        principal=10000.0,
        interest_rate=2.4,
        start_date=_iso(150),
        maturity_date=(date.today() + timedelta(days=215)).isoformat(),
        portfolio_id=bank_id,
    )

    # Retirement plan for a fictional 41-year-old.
    plan = {
        "retirement_age": 62,
        "statutory_age": 67,
        "horizon_age": 95,
        "birth_date": date(date.today().year - 41, 3, 15).isoformat(),
    }
    db.set_setting("retirement_plan", json.dumps(plan))
    db.create_retirement_income(
        "State pension (estimate)",
        owner="self",
        monthly_eur=1650.0,
        from_age=67,
        source="demo",
        as_of=as_of,
    )

    # Decision journal.
    decisions = [
        dict(
            decided_on=_iso(200),
            symbol="VWCE.DE",
            action="dca",
            title="Monthly top-up of the all-world ETF",
            rationale="Core holding; keep buying regardless of price.",
            expectation="Tracks the world index over 10+ years.",
            review_on=(date.today() + timedelta(days=165)).isoformat(),
            price_at_decision=150.0,
            currency="EUR",
            source="demo",
        ),
        dict(
            decided_on=_iso(60),
            symbol="NVDA",
            action="sell",
            title="Trim NVIDIA after the run-up",
            rationale="Position grew above the single-stock cap.",
            expectation="Lower concentration; accept missing some upside.",
            review_on=_iso(-30),
            price_at_decision=195.0,
            currency="USD",
            source="demo",
        ),
        dict(
            decided_on=_iso(300),
            symbol="LLOY.L",
            action="hold",
            title="Keep Lloyds for the dividend",
            rationale="Yield on cost above 5%.",
            review_on=_iso(5),
            price_at_decision=0.9,
            currency="GBP",
            source="demo",
        ),
    ]
    for dec in decisions:
        db.create_decision(**dec)
    db.create_position_cap(
        "sector", "Technology", 35.0, note="Limit tech concentration"
    )
    db.create_position_cap("symbol", "BTC-EUR", 10.0, note="Crypto stays a satellite")
    print("🏠 household: budget, mortgage, property, pension, deposit, plan, journal")


def build(fresh: bool = False) -> bool:
    if fresh and os.path.exists(DEMO_DB):
        os.remove(DEMO_DB)
        print(f"🗑  removed existing {DEMO_DB}")

    print("🚀 Building demo database…")
    db = Database(DEMO_DB)
    auth = AuthManager(db)

    # ── Demo user + API key ────────────────────────────────────────────────
    try:
        user_id = auth.register_user(
            username="demo",
            email="demo@example.com",
            password="demo",
            full_name="Demo User",
        )
        print(f"👤 demo user created (id={user_id})")
    except Exception:
        # Already exists (running without --fresh) — fetch it.
        user_id = db.get_user_by_username("demo")["id"]
        print(f"👤 demo user already present (id={user_id})")

    # A fixed key so the web UI's "API key" login tab works out of the box.
    try:
        from portf_server.auth_middleware import APIKeyManager

        akm = APIKeyManager(db)
        if not akm.validate_api_key(DEMO_API_KEY):
            akm.create_api_key(
                key_name="demo", description="Demo account key", raw_key=DEMO_API_KEY
            )
            print("🔑 demo API key registered")
    except Exception as e:
        print(f"⚠️  could not register demo API key ({e}); password login still works")

    # ── Broker portfolios (EUR base) ───────────────────────────────────────
    portfolios = {
        name: db.get_or_create_portfolio(name, base_currency="EUR")
        for name in ("Degiro", "Trade Republic", "Indexa Capital", "Coinbase")
    }
    print(f"🏦 portfolios: {', '.join(portfolios)}")

    # ── Assets: real tickers (sector tagged for the diversification page) ──
    # (symbol, name, type, exchange, currency, sector)
    assets = [
        ("AAPL", "Apple Inc.", AssetType.STOCK.value, "Nasdaq", "USD", "Technology"),
        (
            "MSFT",
            "Microsoft Corp.",
            AssetType.STOCK.value,
            "Nasdaq",
            "USD",
            "Technology",
        ),
        ("NVDA", "NVIDIA Corp.", AssetType.STOCK.value, "Nasdaq", "USD", "Technology"),
        (
            "GOOGL",
            "Alphabet Inc.",
            AssetType.STOCK.value,
            "Nasdaq",
            "USD",
            "Communication",
        ),
        (
            "JNJ",
            "Johnson & Johnson",
            AssetType.STOCK.value,
            "NYSE",
            "USD",
            "Healthcare",
        ),
        (
            "LLOY.L",
            "Lloyds Banking Group",
            AssetType.STOCK.value,
            "LSE",
            "GBP",
            "Financials",
        ),
        (
            "IWDA.AS",
            "iShares Core MSCI World",
            AssetType.ETF.value,
            "Euronext Amsterdam",
            "EUR",
            "Diversified",
        ),
        (
            "VWCE.DE",
            "Vanguard FTSE All-World",
            AssetType.ETF.value,
            "XETRA Exchange",
            "EUR",
            "Diversified",
        ),
        ("BTC-EUR", "Bitcoin", AssetType.CRYPTO.value, "", "EUR", "Cryptocurrency"),
        ("ETH-EUR", "Ethereum", AssetType.CRYPTO.value, "", "EUR", "Cryptocurrency"),
    ]
    asset_ids = {}
    for sym, name, atype, exch, cur, sector in assets:
        asset_ids[sym] = db.create_asset(
            symbol=sym,
            name=name,
            asset_type=atype,
            exchange=exch,
            currency=cur,
            sector=sector,
            description=f"{name} — demo holding",
        )
    print(f"💰 assets: {len(asset_ids)}")

    # ── Deposits (bookings) — funding the brokers over time ────────────────
    deposits = [
        ("Degiro", 10000, 730),
        ("Degiro", 7000, 365),
        ("Trade Republic", 12000, 700),
        ("Trade Republic", 10000, 260),
        ("Indexa Capital", 14000, 720),
        ("Indexa Capital", 6500, 300),
        ("Coinbase", 10000, 545),
        ("Coinbase", 8000, 210),
    ]
    for pf, amount, days in deposits:
        db.create_booking(
            date=_iso(days),
            action="Deposit",
            amount=float(amount),
            currency="EUR",
            portfolio_id=portfolios[pf],
        )
    print(f"💶 deposits: {len(deposits)}")

    # ── Transactions ───────────────────────────────────────────────────────
    # Cost bases are set as plausible historical levels below today's prices so
    # the demo shows believable returns (roughly +15-35% on most names, BTC a
    # solid winner, ETH a small loser). They are illustrative, not real trades.
    # (symbol, portfolio, type, qty, price, currency, days_ago, fees)
    buys = [
        ("AAPL", "Degiro", 15, 245.00, "USD", 700, 1.0),
        ("AAPL", "Degiro", 10, 275.00, "USD", 400, 1.0),
        ("MSFT", "Degiro", 12, 360.00, "USD", 680, 1.0),
        ("MSFT", "Degiro", 6, 410.00, "USD", 300, 1.0),
        ("NVDA", "Trade Republic", 20, 160.00, "USD", 690, 1.0),
        ("NVDA", "Trade Republic", 8, 210.00, "USD", 250, 1.0),
        ("GOOGL", "Degiro", 14, 320.00, "USD", 520, 1.0),
        ("JNJ", "Indexa Capital", 18, 205.00, "USD", 660, 1.0),
        ("LLOY.L", "Trade Republic", 4000, 0.88, "GBP", 480, 2.0),
        ("IWDA.AS", "Indexa Capital", 80, 95.00, "EUR", 710, 0.0),
        ("IWDA.AS", "Indexa Capital", 40, 108.00, "EUR", 360, 0.0),
        ("IWDA.AS", "Indexa Capital", 20, 118.00, "EUR", 90, 0.0),
        ("VWCE.DE", "Trade Republic", 60, 130.00, "EUR", 600, 0.0),
        ("VWCE.DE", "Trade Republic", 30, 150.00, "EUR", 200, 0.0),
        ("BTC-EUR", "Coinbase", 0.15, 38000.00, "EUR", 540, 5.0),
        ("BTC-EUR", "Coinbase", 0.08, 50000.00, "EUR", 200, 5.0),
        ("ETH-EUR", "Coinbase", 2.5, 1750.00, "EUR", 520, 4.0),
        ("ETH-EUR", "Coinbase", 1.2, 2300.00, "EUR", 150, 4.0),
    ]
    n_tx = 0
    for sym, pf, qty, price, cur, days, fees in buys:
        db.create_transaction(
            asset_id=asset_ids[sym],
            portfolio_id=portfolios[pf],
            transaction_type=TransactionType.BUY.value,
            quantity=qty,
            price=price,
            total_amount=qty * price + fees,
            transaction_date=_iso(days),
            fees=fees,
            currency=cur,
            description="Demo buy",
            user_id=user_id,
        )
        n_tx += 1

    # A couple of sells → modest realised gains for the FIFO tax report
    # (sold above the earliest lot's cost, below today's price).
    sells = [
        ("NVDA", "Trade Republic", 6, 195.00, "USD", 60, 1.0),
        ("AAPL", "Degiro", 5, 290.00, "USD", 45, 1.0),
    ]
    for sym, pf, qty, price, cur, days, fees in sells:
        db.create_transaction(
            asset_id=asset_ids[sym],
            portfolio_id=portfolios[pf],
            transaction_type=TransactionType.SELL.value,
            quantity=qty,
            price=price,
            total_amount=qty * price - fees,
            transaction_date=_iso(days),
            fees=fees,
            currency=cur,
            description="Demo sell",
            user_id=user_id,
        )
        n_tx += 1

    # Cash dividends (quarterly-ish) for the dividend-payers.
    dividends = [
        ("AAPL", "Degiro", 21.00, "USD", 200),
        ("AAPL", "Degiro", 23.00, "USD", 110),
        ("AAPL", "Degiro", 24.00, "USD", 20),
        ("MSFT", "Degiro", 16.00, "USD", 180),
        ("MSFT", "Degiro", 18.00, "USD", 90),
        ("JNJ", "Indexa Capital", 22.00, "USD", 150),
        ("JNJ", "Indexa Capital", 22.50, "USD", 60),
        ("LLOY.L", "Trade Republic", 95.00, "GBP", 120),
    ]
    for sym, pf, amount, cur, days in dividends:
        db.create_transaction(
            asset_id=asset_ids[sym],
            portfolio_id=portfolios[pf],
            transaction_type=TransactionType.DIVIDEND.value,
            quantity=1.0,
            price=amount,
            total_amount=amount,
            transaction_date=_iso(days),
            fees=0.0,
            currency=cur,
            description="Demo dividend",
            user_id=user_id,
        )
        n_tx += 1
    print(
        f"📈 transactions: {n_tx} ({len(buys)} buys, {len(sells)} sells, {len(dividends)} dividends)"
    )

    # ── Net-worth snapshots: ~12 months daily, synthetic but realistic ─────
    # Smooth upward drift + mild volatility; cost steps up with contributions.
    days_back = 365
    base_cost = 50000.0
    cost_growth_per_day = 50.0  # ≈ contributions accruing over the year
    n_snap = 0
    for d in range(days_back, -1, -1):
        cost = base_cost + cost_growth_per_day * (days_back - d)
        # Value oscillates around cost with an upward trend.
        trend = 1.0 + 0.24 * (days_back - d) / days_back
        wobble = 1.0 + 0.05 * math.sin((days_back - d) / 18.0)
        noise = random.uniform(-0.015, 0.015)
        value = cost * trend * wobble * (1 + noise)
        db.record_snapshot(_iso(d), round(value, 2), round(cost, 2))
        n_snap += 1
    print(f"📉 snapshots: {n_snap} daily points")

    # ── Watchlist ──────────────────────────────────────────────────────────
    db.add_watchlist(
        "AMZN",
        name="Amazon.com Inc.",
        asset_type="stock",
        buy_below=150.0,
        notes="Add on a dip",
    )
    db.add_watchlist(
        "TSLA",
        name="Tesla Inc.",
        asset_type="stock",
        buy_below=180.0,
        notes="Watching valuation",
    )
    print("👀 watchlist: 2 tickers")

    # ── FIRE goal ──────────────────────────────────────────────────────────
    db.create_goal(
        name="Financial Independence",
        target_amount_eur=500000.0,
        target_date=date(date.today().year + 12, 1, 1).isoformat(),
        monthly_contribution_eur=1500.0,
        expected_return_pct=7.0,
    )
    print("🎯 goal: Financial Independence")

    # ── Rebalancing targets ────────────────────────────────────────────────
    for atype, pct in (("etf", 45.0), ("stock", 40.0), ("crypto", 15.0)):
        db.set_allocation_target(atype, pct)
    print("⚖️  allocation targets: etf 45 / stock 40 / crypto 15")

    # ── Household: bank account, spending, budget, balance sheet, plans ────
    _build_household(db)

    # ── Live prices (so holdings show current value out of the box) ────────
    n_px = _fetch_prices(db, asset_ids)
    print(f"💹 live prices stored: {n_px}/{len(asset_ids)}")

    print("\n✅ Demo database ready:", DEMO_DB)
    print("\nNext steps (see docs/DEMO_ACCOUNT_README.md for the full guide):")
    print("  1. Bring up the isolated full-UI demo stack (port 8081):")
    print("       docker compose -f docker-compose.demo.yml up -d")
    print("  2. Open http://localhost:8081 and log in via the API-key tab with:")
    print(f"       {DEMO_API_KEY}")
    print("     (or username / password: demo / demo)")
    print("  Holdings already have live prices baked in by this script; re-run")
    print("  the price step in the README to refresh them later.")
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate the pfm demo database.")
    ap.add_argument(
        "--fresh", action="store_true", help="delete existing demo DB first"
    )
    args = ap.parse_args()
    sys.exit(0 if build(fresh=args.fresh) else 1)
