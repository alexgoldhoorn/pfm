"""Tests for net-worth history: snapshot mapping, change split and routes."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from portf_manager.database import Database
from portf_manager.services import networth_history as nh
from portf_manager.services.balance_sheet import build_balance_sheet

TODAY = date(2026, 10, 18)
_KEY = "test-key-networth-history-abc"
HEADERS = {"X-API-Key": _KEY}


def _fx_on(currency, day):
    return {"EUR": 1.0, "USD": 0.9}[currency]


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "nh.db"))


def _sheet(db):
    return build_balance_sheet(db, TODAY, fx=lambda c: 1.0, fx_on=lambda c, d: 1.0)


def _fill(db):
    """One of everything the balance sheet knows, with round amounts."""
    pid = db.create_portfolio("Example Broker", "EUR")
    aid = db.create_asset("EXC", "Example Corp", "stock", currency="EUR")
    db.create_price(aid, 10.0, "2026-10-17")
    db.create_transaction(aid, "buy", 10, 10.0, 100.0, "2026-01-05", portfolio_id=pid)
    db.create_booking("2026-01-01", "Deposit", 150.0, "EUR", portfolio_id=pid)
    db.create_manual_asset("Example Jar", "cash", 300.0, as_of="2026-10-01")
    db.create_manual_asset("Example Flat", "property", 200000.0, as_of="2026-09-01")
    db.create_manual_asset("Example Art", "other", 700.0, as_of="2026-09-01")
    db.create_manual_asset("Example Debt", "other", 1000.0, is_liability=True)
    db.create_fixed_deposit(
        "Example Deposit", 500.0, start_date="2026-01-01", maturity_date="2027-06-01"
    )


def test_components_sum_to_balance_sheet_net_worth(db):
    _fill(db)
    sheet = _sheet(db)
    parts = nh.components_from_sheet(sheet)
    assert parts["investments_eur"] == 100.0
    # broker cash 50 + jar 300 + deposit 500
    assert parts["cash_eur"] == 850.0
    assert parts["property_eur"] == 200000.0
    assert parts["other_assets_eur"] == 700.0
    assert parts["liabilities_eur"] == 1000.0
    total = (
        parts["investments_eur"]
        + parts["cash_eur"]
        + parts["property_eur"]
        + parts["other_assets_eur"]
        - parts["liabilities_eur"]
    )
    assert round(total, 2) == sheet["totals"]["net_worth_eur"]
    assert parts["net_worth_eur"] == sheet["totals"]["net_worth_eur"]


def test_negative_counted_lines_still_reconcile(db):
    # a manual cash line with a negative amount is counted as the sheet counts it
    db.create_manual_asset("Example Overdraft", "cash", -250.0, as_of="2026-10-01")
    db.create_manual_asset("Example Jar", "cash", 400.0, as_of="2026-10-01")
    sheet = _sheet(db)
    parts = nh.components_from_sheet(sheet)
    assert parts["cash_eur"] == 150.0
    assert parts["net_worth_eur"] == sheet["totals"]["net_worth_eur"] == 150.0


def test_unknown_group_goes_to_other_assets():
    sheet = {"groups": [{"key": "mystery", "total_eur": 40.0}]}
    parts = nh.components_from_sheet(sheet)
    assert parts["other_assets_eur"] == 40.0
    assert parts["net_worth_eur"] == 40.0


def test_take_snapshot_stores_and_same_day_replaces(db):
    _fill(db)
    first = nh.take_snapshot(db, TODAY, fx=lambda c: 1.0, fx_on=lambda c, d: 1.0)
    assert (
        db.get_networth_snapshot("2026-10-18")["net_worth_eur"]
        == first["net_worth_eur"]
    )
    db.create_manual_asset("Example Cash 2", "cash", 1000.0, as_of="2026-10-17")
    second = nh.take_snapshot(db, TODAY, fx=lambda c: 1.0, fx_on=lambda c, d: 1.0)
    assert second["net_worth_eur"] == first["net_worth_eur"] + 1000.0
    assert len(db.list_networth_snapshots()) == 1
    row = db.get_networth_snapshot("2026-10-18")
    assert row["detail"]["totals"]["net_worth_eur"] == second["net_worth_eur"]
    assert {g["key"] for g in row["detail"]["groups"]} >= {"cash", "liabilities"}


def test_take_snapshot_raises_and_stores_nothing_when_sheet_fails(db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no sheet")

    monkeypatch.setattr(nh, "build_balance_sheet", boom)
    with pytest.raises(RuntimeError):
        nh.take_snapshot(db, TODAY)
    assert db.list_networth_snapshots() == []


def _snap(db, day, inv, cash, prop, other, liab):
    net = round(inv + cash + prop + other - liab, 2)
    db.upsert_networth_snapshot(day, net, inv, cash, prop, other, liab)


def _two_snapshots(db):
    _snap(db, "2026-10-01", 1000.0, 500.0, 0.0, 0.0, 200.0)
    _snap(db, "2026-10-31", 1600.0, 450.0, 0.0, 50.0, 150.0)


def _flows(db):
    pid = db.create_portfolio("Example Broker", "EUR")
    stock = db.create_asset("EXC", "Example Corp", "stock", currency="EUR")
    usd = db.create_asset("EXU", "Example Dollar Corp", "stock", currency="USD")
    cash = db.create_asset("EURCASH", "Example Cash", "cash", currency="EUR")

    def tx(asset, kind, amount, day, currency="EUR"):
        db.create_transaction(
            asset,
            kind,
            1,
            amount,
            amount,
            day,
            portfolio_id=pid,
            currency=currency,
        )

    # on the start day: already in the start snapshot
    tx(stock, "buy", 100.0, "2026-10-01")
    tx(stock, "buy", 400.0, "2026-10-10")
    tx(usd, "buy", 100.0, "2026-10-15", currency="USD")
    tx(stock, "sell", 100.0, "2026-10-31")
    # not flows into a holding, or outside the window
    tx(cash, "buy", 999.0, "2026-10-12")
    tx(stock, "dividend", 20.0, "2026-10-20")
    tx(stock, "buy", 777.0, "2026-11-02")


def test_change_split_sums_to_the_cent(db):
    _two_snapshots(db)
    _flows(db)
    out = nh.compute_change(db, fx_on=_fx_on)
    assert out["start"] == "2026-10-01"
    assert out["end"] == "2026-10-31"
    assert out["net_worth_change_eur"] == 650.0
    # 400 + 90 (100 USD at 0.9) - 100
    assert out["net_invested_eur"] == 390.0
    assert out["parts"] == {
        "saved_eur": 340.0,
        "market_eur": 210.0,
        "debt_paid_eur": 50.0,
        "property_change_eur": 0.0,
        "other_change_eur": 50.0,
    }
    assert round(sum(out["parts"].values()), 2) == out["net_worth_change_eur"]
    assert out["by_component"]["cash_eur"] == {
        "start": 500.0,
        "end": 450.0,
        "change": -50.0,
    }
    assert out["warnings"] == []


def test_rounding_remainder_lands_in_other(db):
    _snap(db, "2026-10-01", 1000.004, 500.0, 0.0, 0.0, 0.0)
    _snap(db, "2026-10-31", 1100.0, 500.0, 0.0, 0.0, 0.0)
    out = nh.compute_change(db, fx_on=_fx_on)
    assert round(sum(out["parts"].values()), 2) == out["net_worth_change_eur"]


def test_defaults_pick_snapshot_nearest_30_days_back(db):
    _snap(db, "2026-09-01", 100.0, 0.0, 0.0, 0.0, 0.0)
    _snap(db, "2026-10-04", 200.0, 0.0, 0.0, 0.0, 0.0)
    _snap(db, "2026-10-20", 300.0, 0.0, 0.0, 0.0, 0.0)
    _snap(db, "2026-11-03", 400.0, 0.0, 0.0, 0.0, 0.0)
    out = nh.compute_change(db, fx_on=_fx_on)
    # 30 days before 2026-11-03 is 2026-10-04
    assert (out["start"], out["end"]) == ("2026-10-04", "2026-11-03")
    explicit = nh.compute_change(
        db, start=date(2026, 9, 2), end=date(2026, 10, 20), fx_on=_fx_on
    )
    assert (explicit["start"], explicit["end"]) == ("2026-09-01", "2026-10-20")
    assert explicit["warnings"] == ["no snapshot on 2026-09-02; used 2026-09-01"]


def test_fewer_than_two_snapshots_gives_null_parts(db):
    empty = nh.compute_change(db, fx_on=_fx_on)
    assert empty["net_worth_change_eur"] is None
    assert empty["warnings"] == ["no snapshots stored yet"]
    _snap(db, "2026-10-01", 100.0, 0.0, 0.0, 0.0, 0.0)
    one = nh.compute_change(db, fx_on=_fx_on)
    assert one["warnings"] == ["history starts 2026-10-01"]
    assert set(one["parts"].values()) == {None}
    assert one["by_component"] is None


def test_flow_failure_nulls_saved_and_market_only(db):
    _two_snapshots(db)
    _flows(db)

    def broken(currency, day):
        raise RuntimeError("fx down")

    out = nh.compute_change(db, fx_on=broken)
    assert out["parts"]["saved_eur"] is None
    assert out["parts"]["market_eur"] is None
    assert out["net_invested_eur"] is None
    assert out["parts"]["debt_paid_eur"] == 50.0
    assert out["parts"]["other_change_eur"] == 50.0
    assert out["warnings"] == [
        "buys and sells could not be read; saved and market unknown"
    ]


def test_history_window_and_warnings(db):
    assert nh.get_history(db, 24, TODAY)["warnings"] == ["no snapshots stored yet"]
    _snap(db, "2024-01-01", 1.0, 0.0, 0.0, 0.0, 0.0)
    _snap(db, "2026-10-01", 100.0, 0.0, 0.0, 0.0, 0.0)
    out = nh.get_history(db, 24, TODAY)
    assert out["count"] == 1
    assert out["first_date"] == out["last_date"] == "2026-10-01"
    assert out["warnings"] == ["history starts 2026-10-01"]
    assert set(out["snapshots"][0]) == {
        "snapshot_date",
        "net_worth_eur",
        *nh.COMPONENTS,
    }
    _snap(db, "2026-10-10", 150.0, 0.0, 0.0, 0.0, 0.0)
    out = nh.get_history(db, 24, TODAY)
    assert [s["snapshot_date"] for s in out["snapshots"]] == [
        "2026-10-01",
        "2026-10-10",
    ]
    assert out["warnings"] == []


# ── price-update hook ────────────────────────────────────────────────────────


class _Client:
    def __init__(self, prices):
        self.prices = prices

    def fetch_latest_prices(self, symbols):
        return {s: p for s, p in self.prices.items() if s in symbols}

    def get_fx_rate(self, a, b):
        return 1.0

    def get_quote_currency(self, symbol):
        return "EUR"


@pytest.fixture
def priced(db, monkeypatch):
    db.create_asset("EXA", "Example Corp", "stock", currency="EUR")
    monkeypatch.setattr(
        "portf_manager.api_client.get_client", lambda: _Client({"EXA": 12.5})
    )
    return db


def test_price_update_stores_a_snapshot(priced):
    from portf_manager.services.price_updater import run_price_update

    priced.create_manual_asset("Example Jar", "cash", 300.0)
    result = run_price_update(priced, source="cron")
    assert result["updated_count"] == 1
    rows = priced.list_networth_snapshots()
    assert [r["snapshot_date"] for r in rows] == [date.today().isoformat()]
    assert rows[0]["cash_eur"] == 300.0


def test_failing_snapshot_does_not_fail_the_run(priced, monkeypatch):
    from portf_manager.services.price_updater import run_price_update

    def boom(*a, **k):
        raise RuntimeError("sheet broke")

    monkeypatch.setattr(nh, "take_snapshot", boom)
    result = run_price_update(priced, source="cron")
    assert result["updated_count"] == 1
    assert result["error_count"] == 0
    assert priced.list_networth_snapshots() == []


def test_failed_fetch_takes_no_snapshot(priced, monkeypatch):
    from portf_manager.api_client import APIError
    from portf_manager.services.price_updater import run_price_update

    class Down(_Client):
        def fetch_latest_prices(self, symbols):
            raise APIError("down")

    monkeypatch.setattr("portf_manager.api_client.get_client", lambda: Down({}))
    result = run_price_update(priced, source="cron")
    assert result["api_errors"] == ["API error: down"]
    assert priced.list_networth_snapshots() == []


# ── routes ───────────────────────────────────────────────────────────────────


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


def test_history_routes(tmp_path):
    client, inst = _client(tmp_path)
    inst.create_manual_asset("Example Jar", "cash", 300.0)

    r = client.post("/api/v1/networth/history/snapshot", headers=HEADERS)
    assert r.status_code == 200
    snap = r.json()
    assert snap["snapshot_date"] == date.today().isoformat()
    assert snap["cash_eur"] == 300.0
    assert snap["net_worth_eur"] == 300.0
    assert set(snap) == {"snapshot_date", "net_worth_eur", *nh.COMPONENTS}

    r = client.get("/api/v1/networth/history?months=12", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"snapshots", "first_date", "last_date", "count", "warnings"}
    assert body["count"] == 1
    assert body["warnings"] == [f"history starts {date.today().isoformat()}"]

    # registered before the /{asset_id} routes: not parsed as an id
    r = client.get("/api/v1/networth/history/change", headers=HEADERS)
    assert r.status_code == 200
    change = r.json()
    assert set(change) == {
        "start",
        "end",
        "net_worth_change_eur",
        "net_invested_eur",
        "parts",
        "by_component",
        "warnings",
    }
    assert change["net_worth_change_eur"] is None
    assert change["warnings"] == [f"history starts {date.today().isoformat()}"]


def test_change_route_with_two_snapshots_and_bad_range(tmp_path):
    client, inst = _client(tmp_path)
    _two_snapshots(inst)
    r = client.get(
        "/api/v1/networth/history/change?start=2026-10-01&end=2026-10-31",
        headers=HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["net_worth_change_eur"] == 650.0
    assert body["parts"]["debt_paid_eur"] == 50.0
    bad = client.get(
        "/api/v1/networth/history/change?start=2026-10-31&end=2026-10-01",
        headers=HEADERS,
    )
    assert bad.status_code == 400
    assert (
        client.get("/api/v1/networth/history?months=0", headers=HEADERS).status_code
        == 422
    )
