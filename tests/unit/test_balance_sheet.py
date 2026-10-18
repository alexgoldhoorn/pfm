"""Tests for the balance sheet builder and its endpoints."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from portf_manager.database import Database
from portf_manager.services.balance_sheet import build_balance_sheet

TODAY = date(2026, 10, 18)
_KEY = "test-key-balance-sheet-abc"
HEADERS = {"X-API-Key": _KEY}


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "bs.db"))


def build(db):
    return build_balance_sheet(db, TODAY, fx=lambda c: 1.0, fx_on=lambda c, d: 1.0)


def lines_of(sheet):
    return {ln["id"]: ln for g in sheet["groups"] for ln in g["lines"]}


def group(sheet, key):
    return next(g for g in sheet["groups"] if g["key"] == key)


def add_position(db, pid, price_date="2026-10-17", auto=True, price=10.0, qty=10):
    aid = db.create_asset("EXC", "Example Corp", "stock", currency="EUR")
    if not auto:
        with db.get_connection() as conn:
            conn.execute("UPDATE assets SET auto_price = 0 WHERE id = ?", (aid,))
            conn.commit()
    if price_date:
        db.create_price(aid, price, price_date)
    db.create_transaction(
        aid, "buy", qty, price, qty * price, "2026-01-05", portfolio_id=pid
    )
    return aid


def test_groups_are_in_fixed_order_and_totals_add_up(db):
    pid = db.create_portfolio("Example Broker", "EUR")
    add_position(db, pid)
    # the deposit funds the 100 purchase, so broker cash nets to zero
    db.create_booking("2026-01-01", "Deposit", 100.0, "EUR", portfolio_id=pid)
    db.create_manual_asset("Example Flat", "property", 200000.0, as_of="2026-09-01")
    db.create_manual_asset("Example Loan", "other", 1000.0, is_liability=True)
    sheet = build(db)
    assert [g["key"] for g in sheet["groups"]] == [
        "cash",
        "deposits",
        "investments",
        "property",
        "other_assets",
        "liabilities",
    ]
    totals = sheet["totals"]
    assert totals["assets_eur"] == 200100.0
    assert totals["liabilities_eur"] == 1000.0
    assert totals["net_worth_eur"] == 199100.0
    assert sheet["as_of"] == "2026-10-18"


def test_positions_line_stale_after_four_days(db):
    pid = db.create_portfolio("Example Broker", "EUR")
    add_position(db, pid, price_date="2026-10-13")
    line = lines_of(build(db))[f"positions:{pid}"]
    assert line["age_days"] == 5
    assert line["max_age_days"] == 4
    assert line["stale"] is True
    assert line["source"] == "positions"


def test_positions_line_notes_unpriced_assets(db):
    pid = db.create_portfolio("Example Broker", "EUR")
    add_position(db, pid, price_date=None)
    line = lines_of(build(db))[f"positions:{pid}"]
    assert line["note"] == "1 asset unpriced"
    assert line["stale"] is True


def test_manual_price_asset_does_not_drive_staleness(db):
    pid = db.create_portfolio("Example Broker", "EUR")
    add_position(db, pid, price_date="2020-01-01", auto=False)
    line = lines_of(build(db))[f"positions:{pid}"]
    assert line["stale"] is False
    assert line["amount_eur"] == 100.0


def test_bank_without_balance_is_stale_and_excluded(db):
    pid = db.create_portfolio("Example Bank", "EUR", account_type="bank")
    sheet = build(db)
    line = lines_of(sheet)[f"bank:{pid}"]
    assert line["amount_eur"] is None
    assert line["stale"] is True
    assert line["note"] == "no statement with a balance"
    assert sheet["totals"]["assets_eur"] == 0.0


def _bank_balance(db, pid, day, balance):
    db.create_spending_transaction(
        portfolio_id=pid,
        date=day,
        description="Example row",
        amount=1.0,
        currency="EUR",
        balance=balance,
    )


def test_bank_balance_stale_after_45_days(db):
    pid = db.create_portfolio("Example Bank", "EUR", account_type="bank")
    _bank_balance(db, pid, "2026-08-30", 5000.0)
    line = lines_of(build(db))[f"bank:{pid}"]
    assert line["age_days"] == 49
    assert line["stale"] is True
    assert line["amount_eur"] == 5000.0
    assert line["source"] == "bank_statement"


def _valuation(db, pid, as_of):
    db.upsert_account_valuation(
        pid, "indexa", "ref", 12000.0, 500.0, "EUR", as_of, "2026-10-18T06:00:00"
    )


def test_fresh_valuation_replaces_positions_and_cash_lines(db):
    pid = db.create_portfolio("Example Broker", "EUR")
    add_position(db, pid)
    db.create_booking("2026-01-01", "Deposit", 2000.0, "EUR", portfolio_id=pid)
    _valuation(db, pid, "2026-10-18")
    sheet = build(db)
    ids = set(lines_of(sheet))
    assert ids == {f"valuation:{pid}"}
    assert lines_of(sheet)[f"valuation:{pid}"]["amount_eur"] == 12000.0
    assert sheet["totals"]["net_worth_eur"] == 12000.0


def test_stale_valuation_keeps_positions_line_with_note(db):
    pid = db.create_portfolio("Example Broker", "EUR")
    add_position(db, pid)
    _valuation(db, pid, "2026-10-08")
    ids = lines_of(build(db))
    assert f"valuation:{pid}" not in ids
    assert "10 days old" in ids[f"positions:{pid}"]["note"]


def test_never_fetched_valuation_noted(db):
    pid = db.create_portfolio("Example Broker", "EUR")
    add_position(db, pid)
    _valuation(db, pid, "1970-01-01")
    line = lines_of(build(db))[f"positions:{pid}"]
    assert "never fetched" in line["note"]


def test_broker_cash_line_only_when_non_zero(db):
    pid = db.create_portfolio("Example Broker", "EUR")
    assert not [ln for ln in lines_of(build(db)) if ln.startswith("cash:")]
    db.create_booking("2026-10-01", "Deposit", 2000.0, "EUR", portfolio_id=pid)
    line = lines_of(build(db))[f"cash:{pid}"]
    assert line["amount_eur"] == 2000.0
    assert line["group"] == "cash"
    assert line["source"] == "cash_ledger"
    assert line["max_age_days"] == 60


def test_negative_ledger_cash_is_shown_but_not_counted(db):
    pid = db.create_portfolio("Example Broker", "EUR")
    add_position(db, pid)
    sheet = build(db)
    line = lines_of(sheet)[f"cash:{pid}"]
    assert line["amount_eur"] == -100.0
    assert line["stale"] is True
    assert line["in_total"] is False
    assert line["note"] == "ledger cash is negative: deposits missing?"
    assert group(sheet, "cash")["total_eur"] == 0.0
    totals = sheet["totals"]
    assert totals["liquid_eur"] == 0.0
    assert totals["financial_assets_eur"] == 100.0
    assert totals["net_worth_eur"] == 100.0
    assert all(ln["in_total"] for ln in lines_of(sheet).values() if ln is not line)


def test_positions_line_with_undated_price_is_stale_with_unknown_age(db):
    pid = db.create_portfolio("Example Broker", "EUR")
    aid = add_position(db, pid)
    with db.get_connection() as conn:
        conn.execute("UPDATE prices SET price_date = '' WHERE asset_id = ?", (aid,))
        conn.commit()
    line = lines_of(build(db))[f"positions:{pid}"]
    assert line["age_days"] is None
    assert line["stale"] is True


def test_loan_line_balance_from_schedule_and_note(db):
    lid = db.create_loan(
        "Example Mortgage", 120000.0, 0.0, "2026-01-18", 120, lender="Example Bank"
    )
    sheet = build(db)
    line = lines_of(sheet)[f"loan:{lid}"]
    # nine payments of 1000 made by 2026-10-18
    assert line["amount_eur"] == 111000.0
    assert line["group"] == "liabilities"
    assert line["source"] == "loan_schedule"
    assert "1,000.00 a month" in line["note"]
    assert "ends 2036-01-18" in line["note"]
    assert line["stale"] is False
    assert sheet["totals"]["liabilities_eur"] == 111000.0


def test_manual_property_stale_after_365_days(db):
    db.create_manual_asset("Example Flat", "property", 1.0, as_of="2025-10-01")
    db.create_manual_asset("Example Plot", "property", 1.0, as_of="2026-01-01")
    lines = [ln for ln in lines_of(build(db)).values()]
    flat = next(ln for ln in lines if ln["label"] == "Example Flat")
    plot = next(ln for ln in lines if ln["label"] == "Example Plot")
    assert flat["stale"] is True and flat["max_age_days"] == 365
    assert plot["stale"] is False


def test_manual_cash_stale_after_90_days(db):
    db.create_manual_asset("Example Jar", "cash", 100.0, as_of="2026-07-01")
    db.create_manual_asset("Example Tin", "savings_account", 100.0, as_of="2026-09-01")
    lines = lines_of(build(db))
    jar = next(ln for ln in lines.values() if ln["label"] == "Example Jar")
    tin = next(ln for ln in lines.values() if ln["label"] == "Example Tin")
    assert jar["group"] == "cash" and jar["stale"] is True
    assert tin["group"] == "cash" and tin["stale"] is False


def test_manual_line_named_like_a_portfolio_gets_duplicate_note(db):
    db.create_portfolio("Example Broker", "EUR")
    mid = db.create_manual_asset(
        "example broker", "savings_account", 10.0, as_of="2026-10-01"
    )
    line = lines_of(build(db))[f"manual:{mid}"]
    assert "a portfolio with this name exists" in line["note"]


def test_deposit_past_maturity_gets_note(db):
    did = db.create_fixed_deposit(
        "Example Deposit",
        1000.0,
        interest_rate=2.0,
        start_date="2025-01-01",
        maturity_date="2026-01-01",
    )
    line = lines_of(build(db))[f"deposit:{did}"]
    assert line["note"] == "past maturity, mark matured"
    assert line["stale"] is False
    assert line["group"] == "deposits"


def test_financial_assets_exclude_property_and_other(db):
    db.create_manual_asset("Example Flat", "property", 1000.0)
    db.create_manual_asset("Example Car", "other", 500.0)
    db.create_manual_asset("Example Jar", "cash", 100.0)
    db.create_manual_asset("Example Pension", "pension", 300.0)
    totals = build(db)["totals"]
    assert totals["financial_assets_eur"] == 400.0
    assert totals["assets_eur"] == 1900.0


def test_liquid_eur_counts_deposits_maturing_within_a_year(db):
    db.create_manual_asset("Example Jar", "cash", 100.0)
    db.create_fixed_deposit(
        "Soon", 1000.0, start_date="2026-01-01", maturity_date="2027-06-01"
    )
    db.create_fixed_deposit(
        "Later", 5000.0, start_date="2026-01-01", maturity_date="2028-06-01"
    )
    totals = build(db)["totals"]
    assert totals["liquid_eur"] == 1100.0
    assert totals["financial_assets_eur"] == 6100.0


# ── endpoints ────────────────────────────────────────────────────────────────


def _client(tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    inst = Database(str(tmp_path / "api.db"))
    km = APIKeyManager(inst)
    km.create_api_key(key_name="t", description="t", raw_key=_KEY)
    app.dependency_overrides[get_database] = lambda: inst
    app.dependency_overrides[get_api_key_manager] = lambda: km
    return TestClient(app), inst


def test_networth_endpoint_total_equals_balance_sheet(tmp_path):
    client, inst = _client(tmp_path)
    pid = inst.create_portfolio("Example Broker", "EUR")
    add_position(inst, pid)
    inst.create_manual_asset("Example Flat", "property", 1000.0)
    inst.create_manual_asset("Example Debt", "other", 200.0, is_liability=True)
    inst.create_fixed_deposit(
        "Example Deposit", 500.0, start_date="2026-01-01", maturity_date="2027-01-01"
    )
    nw = client.get("/api/v1/networth/", headers=HEADERS).json()
    sheet = client.get("/api/v1/networth/balance-sheet", headers=HEADERS).json()
    assert nw["net_worth_eur"] == sheet["totals"]["net_worth_eur"]
    assert nw["brokerage_eur"] == 100.0
    assert nw["deposits_eur"] == 500.0
    assert nw["manual_liabilities_eur"] == 200.0


def test_balance_sheet_endpoint_shape(tmp_path):
    client, inst = _client(tmp_path)
    inst.create_manual_asset("Example Jar", "cash", 100.0)
    r = client.get("/api/v1/networth/balance-sheet", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"as_of", "totals", "groups"}
    assert set(body["totals"]) == {
        "assets_eur",
        "liabilities_eur",
        "net_worth_eur",
        "financial_assets_eur",
        "liquid_eur",
        "stale_lines",
    }
    line = body["groups"][0]["lines"][0]
    assert set(line) == {
        "id",
        "label",
        "group",
        "amount_eur",
        "amount",
        "currency",
        "source",
        "as_of",
        "age_days",
        "max_age_days",
        "stale",
        "note",
        "edit",
        "in_total",
    }
    assert line["edit"]["kind"] == "manual"


def test_manual_asset_post_accepts_as_of(tmp_path):
    client, inst = _client(tmp_path)
    r = client.post(
        "/api/v1/networth/",
        json={
            "name": "Example Flat",
            "category": "property",
            "amount": 5.0,
            "as_of": "2025-05-05",
        },
        headers=HEADERS,
    )
    mid = r.json()["id"]
    assert inst.get_manual_assets()[0]["as_of"] == "2025-05-05"
    # an amount edit without as_of dates the line today
    client.put(f"/api/v1/networth/{mid}", json={"amount": 6.0}, headers=HEADERS)
    assert inst.get_manual_assets()[0]["as_of"] == date.today().isoformat()
    client.put(
        f"/api/v1/networth/{mid}",
        json={"amount": 7.0, "as_of": "2025-06-06"},
        headers=HEADERS,
    )
    assert inst.get_manual_assets()[0]["as_of"] == "2025-06-06"
    bad = client.put(
        f"/api/v1/networth/{mid}", json={"as_of": "not-a-date"}, headers=HEADERS
    )
    assert bad.status_code == 422
