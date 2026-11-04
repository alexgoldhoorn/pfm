"""Shock stress test: look-through, sector, currency, presets and endpoints."""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from portf_manager.database import Database
from portf_manager.services import stress

_KEY = "test-key-stress-abc123"
HEADERS = {"X-API-Key": _KEY}


def _fx(currency: str) -> float:
    return {"EUR": 1.0, "USD": 0.5}.get(currency, 1.0)


def _buy(db, aid, pid, qty, price, currency):
    db.create_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=qty,
        price=price,
        total_amount=qty * price,
        transaction_date="2026-01-02",
        portfolio_id=pid,
        currency=currency,
    )


def _profile(db, aid, regions, classes, sectors=None, hedged=0, hedge=None):
    db.upsert_fund_profile(
        aid,
        None,
        "manual",
        json.dumps(classes),
        json.dumps(regions),
        json.dumps(sectors or {}),
        hedged,
        hedge,
        "2026-10-01",
    )


@pytest.fixture
def db(tmp_path):
    """A stock (1000 EUR), a mixed fund (1000 EUR) and a USD stock (1000 EUR)."""
    db = Database(str(tmp_path / "stress.db"))
    pid = db.get_or_create_portfolio("Example Broker", base_currency="EUR")
    stock = db.create_asset("EXCO", "Example Corp", "stock", currency="EUR")
    fund = db.create_asset("EXFUND", "Example Mixed Fund", "etf", currency="EUR")
    usd = db.create_asset("USCO", "Example US Corp", "stock", currency="USD")
    for aid, qty, price, cur in ((stock, 10, 100, "EUR"), (fund, 10, 100, "EUR")):
        _buy(db, aid, pid, qty, price, cur)
        db.create_price(aid, float(price), "2026-11-01")
    # 10 units at 200 USD, rate 0.5 -> 1000 EUR
    _buy(db, usd, pid, 10, 200, "USD")
    db.create_price(usd, 200.0, "2026-11-01")
    # half equity (US tech 60% / health 40%), half bond; all USD-region
    _profile(
        db,
        fund,
        {"north_america": 1.0},
        {"equity": 0.5, "bond": 0.5},
        {"Technology": 0.6, "Healthcare": 0.4},
    )
    return db


def _run(db, shocks):
    sectors = {
        "EXCO": ("Technology", "Germany"),
        "USCO": ("Healthcare", "United States"),
    }
    with patch(
        "portf_manager.services.stress._resolve_sector_country",
        side_effect=lambda _db, a: sectors[a["symbol"]],
    ):
        return stress.run_stress(db, shocks, fx=_fx, fx_on=lambda cur, day: _fx(cur))


def test_equity_shock_looks_through_fund(db):
    out = _run(db, {"asset_class": {"equity": -30}})
    by_symbol = {r["symbol"]: r for r in out["top_losers"]}
    # fund is half equity, so it loses 15%; the two stocks lose 30%
    assert by_symbol["EXFUND"]["loss_eur"] == pytest.approx(150.0)
    assert by_symbol["EXCO"]["loss_eur"] == pytest.approx(300.0)
    assert out["portfolio_value_eur"] == pytest.approx(3000.0)
    assert out["loss_eur"] == pytest.approx(750.0)
    assert out["loss_pct_of_portfolio"] == pytest.approx(25.0)


def test_sector_shock_beats_class_shock(db):
    out = _run(db, stress.SCENARIOS["semis_crash"]["shocks"])
    by_symbol = {r["symbol"]: r for r in out["top_losers"]}
    # stock in Technology: -50%; healthcare stock: -15%
    assert by_symbol["EXCO"]["loss_eur"] == pytest.approx(500.0)
    assert by_symbol["USCO"]["loss_eur"] == pytest.approx(150.0)
    # fund equity half: 60% tech at -50, 40% other at -15 -> 0.5*(30+6)=18%
    assert by_symbol["EXFUND"]["loss_eur"] == pytest.approx(180.0)


def test_currency_shock_hits_usd_exposure_only(db):
    out = _run(db, {"currency": {"usd": -10}})
    by_symbol = {r["symbol"]: r for r in out["top_losers"]}
    assert by_symbol["USCO"]["loss_eur"] == pytest.approx(100.0)
    # the fund's north_america region is USD
    assert by_symbol["EXFUND"]["loss_eur"] == pytest.approx(100.0)
    assert "EXCO" not in by_symbol
    assert out["loss_eur"] == pytest.approx(200.0)


def test_hedged_fund_ignores_other_currency(db):
    fund = db.get_asset_by_symbol("EXFUND")["id"]
    _profile(
        db,
        fund,
        {"north_america": 1.0},
        {"equity": 1.0},
        hedged=1,
        hedge="EUR",
    )
    out = _run(db, {"currency": {"USD": -10}})
    assert all(r["symbol"] != "EXFUND" for r in out["top_losers"])


def test_asset_type_shock_and_compounding(db):
    out = _run(db, {"asset_type": {"stock": -10}, "currency": {"USD": -10}})
    by_symbol = {r["symbol"]: r for r in out["top_losers"]}
    # USD stock: 1 - 0.9 * 0.9 = 19%
    assert by_symbol["USCO"]["loss_eur"] == pytest.approx(190.0)
    assert by_symbol["EXCO"]["loss_eur"] == pytest.approx(100.0)


def test_breakdowns_and_net_worth(db):
    out = _run(db, {"asset_class": {"equity": -30}})
    by_type = {r["asset_type"]: r for r in out["by_asset_type"]}
    assert by_type["stock"]["loss_eur"] == pytest.approx(600.0)
    assert by_type["etf"]["loss_pct"] == pytest.approx(15.0)
    assert out["by_portfolio"][0]["portfolio"] == "Example Broker"
    assert out["net_worth_before_eur"] - out["net_worth_after_eur"] == pytest.approx(
        out["loss_eur"]
    )


def test_unprofiled_fund_assumed_equity(db):
    other = db.create_asset("EXBARE", "Example Bare Fund", "etf", currency="EUR")
    _buy(db, other, 1, 1, 100, "EUR")
    db.create_price(other, 100.0, "2026-11-01")
    out = _run(db, {"asset_class": {"equity": -50}})
    assert out["assumed_equity"] == ["EXBARE"]


def test_retirement_none_without_plan(db):
    assert _run(db, {"asset_class": {"equity": -30}})["retirement_after"] is None


def test_retirement_recomputed_with_override(db):
    calls = []

    def fake_readiness(_db, today=None, fx=None, investable_override_eur=None):
        calls.append(investable_override_eur)
        return {
            "status": "ok",
            "inputs": {"investments_eur": 3000.0},
            "scenarios": [
                {
                    "retirement_age": 65,
                    "case": "base",
                    "verdict": "lasts" if investable_override_eur is None else "short",
                }
            ],
        }

    with patch("portf_manager.services.retirement.readiness", fake_readiness):
        out = _run(db, {"asset_class": {"equity": -30}})
    assert calls == [None, pytest.approx(2250.0)]
    ret = out["retirement_after"]
    assert ret["scenarios_before"][0]["verdict"] == "lasts"
    assert ret["scenarios_after"][0]["verdict"] == "short"
    assert ret["investments_after_eur"] == pytest.approx(2250.0)


def test_readiness_override_replaces_investments(db):
    from portf_manager.services import retirement

    sheet = {"groups": [], "totals": {}}
    with (
        patch(
            "portf_manager.services.balance_sheet.build_balance_sheet",
            return_value=sheet,
        ),
        patch.object(
            retirement,
            "spending_inputs",
            return_value={
                "months_used": list(range(12)),
                "target_monthly_eur": 2000.0,
                "savings_now_yearly_eur": 0.0,
                "income_monthly_eur": 0.0,
                "breakdown": [],
                "excluded": [],
            },
        ),
        patch.object(
            retirement,
            "load_plan",
            return_value={**retirement._merge({}), "birth_date": "1980-01-01"},
        ),
    ):
        base = retirement.readiness(db, fx=_fx)
        low = retirement.readiness(db, fx=_fx, investable_override_eur=1000.0)
    assert base["inputs"]["investments_eur"] == 0.0
    assert low["inputs"]["investments_eur"] == 1000.0


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"bogus": {"x": -10}},
        {"asset_class": {"equity": "a lot"}},
        {"asset_class": {"equity": -150}},
        {"asset_class": []},
    ],
)
def test_invalid_shocks_rejected(db, bad):
    with pytest.raises(ValueError):
        stress.run_stress(db, bad, fx=_fx)


def test_list_scenarios_order():
    names = [s["name"] for s in stress.list_scenarios()]
    assert names == [
        "equity_bear",
        "semis_crash",
        "usd_down",
        "rates_up",
        "crypto_winter",
        "stagflation",
    ]


@pytest.fixture
def client(db):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    km = APIKeyManager(db)
    km.create_api_key(key_name="test", description="test key", raw_key=_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km
    with (
        patch("portf_server.routers.analytics._fx", _fx),
        patch(
            "portf_server.routers.portfolios._get_fx_rate_on", lambda d, c, o: _fx(c)
        ),
        patch(
            "portf_manager.services.stress._resolve_sector_country",
            return_value=("Technology", "Germany"),
        ),
    ):
        yield TestClient(app)
    app.dependency_overrides.clear()


def test_endpoints(client):
    r = client.get("/api/v1/analytics/stress/scenarios", headers=HEADERS)
    assert r.status_code == 200 and len(r.json()["scenarios"]) == 6

    r = client.get("/api/v1/analytics/stress?scenario=crypto_winter", headers=HEADERS)
    body = r.json()
    assert r.status_code == 200
    assert body["scenario"] == "crypto_winter" and body["loss_eur"] == 0
    assert body["retirement_after"] is None

    r = client.get("/api/v1/analytics/stress?scenario=nope", headers=HEADERS)
    assert r.status_code == 404

    r = client.post(
        "/api/v1/analytics/stress",
        json={"shocks": {"asset_class": {"equity": -10}}},
        headers=HEADERS,
    )
    assert r.status_code == 200 and r.json()["scenario"] == "custom"
    assert r.json()["loss_eur"] > 0

    r = client.post(
        "/api/v1/analytics/stress", json={"shocks": {"x": {}}}, headers=HEADERS
    )
    assert r.status_code == 422


def test_chat_tool(db):
    from portf_server import chat_tools

    with (
        patch("portf_server.chat_tools._fx", _fx),
        patch(
            "portf_server.routers.portfolios._get_fx_rate_on", lambda d, c, o: _fx(c)
        ),
        patch(
            "portf_manager.services.stress._resolve_sector_country",
            return_value=("Technology", "Germany"),
        ),
    ):
        out = json.loads(chat_tools._REGISTRY["get_stress_test"](db, "equity_bear"))
        bad = json.loads(chat_tools._REGISTRY["get_stress_test"](db, "nope"))
    assert out["loss_eur"] > 0 and out["scenario"] == "equity_bear"
    assert "error" in bad
