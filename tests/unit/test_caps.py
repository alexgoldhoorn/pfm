"""Position, sector and asset-type caps: status, Action Item, router, allocator."""

import json
from datetime import date

import pytest
from fastapi import status
from httpx import AsyncClient

from portf_manager.services import allocator
from portf_manager.services.action_items import checks_for_tests
from portf_manager.services.caps import cap_status, check_position_caps
from portf_server.chat_tools import execute_tool

TODAY = date(2026, 11, 4)
SECTORS = {"EXA": "Technology", "EXB": "Technology", "EXC": "Energy"}


@pytest.fixture(autouse=True)
def sector_lookup(monkeypatch):
    """Sector of a direct holding without touching yfinance."""
    monkeypatch.setattr(
        "portf_manager.services.exposure._resolve_sector_country",
        lambda db, asset, *a: (SECTORS.get(asset["symbol"], "Unknown"), "Unknown"),
    )


def _hold(db, symbol, value, atype="stock"):
    """One EUR position worth *value* (10 units at value/10)."""
    pid = (db.get_all_portfolios() or [None])[0]
    pid = pid["id"] if pid else db.get_or_create_portfolio("Example Broker")
    aid = db.create_asset(symbol, f"{symbol} Corp", atype, currency="EUR")
    db.create_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=10,
        price=value / 10,
        total_amount=value,
        transaction_date="2026-01-02",
        portfolio_id=pid,
        currency="EUR",
    )
    db.create_price(aid, value / 10, "2026-11-03")
    return aid


@pytest.fixture
def held(test_database):
    for symbol, value in (("EXA", 600.0), ("EXB", 300.0), ("EXC", 100.0)):
        _hold(test_database, symbol, value)
    return test_database


class TestCapStatus:
    def test_maths_for_every_scope(self, held):
        held.create_position_cap("symbol", "exa", 50)
        held.create_position_cap("sector", "technology", 91)
        held.create_position_cap("asset_type", "stock", 100)
        out = cap_status(held, today=TODAY)
        assert out["as_of"] == "2026-11-04"
        assert out["total_value_eur"] == 1000.0
        rows = {(c["scope"], c["key"]): c for c in out["caps"]}

        exa = rows[("symbol", "EXA")]
        assert exa["current_pct"] == 60.0 and exa["headroom_pct"] == -10.0
        assert exa["headroom_eur"] == -100.0 and exa["status"] == "breached"
        assert exa["implicit"] is False

        tech = rows[("sector", "technology")]
        assert tech["current_pct"] == 90.0 and tech["headroom_pct"] == 1.0
        assert tech["status"] == "near"

        kind = rows[("asset_type", "stock")]
        assert kind["headroom_pct"] == 0.0 and kind["status"] == "near"

    def test_implicit_single_stock_caps_for_uncapped_stocks(self, held):
        held.create_position_cap("symbol", "EXA", 80)
        out = cap_status(held, today=TODAY)
        implicit = {c["key"]: c for c in out["caps"] if c["implicit"]}
        assert set(implicit) == {"EXB", "EXC"}
        assert implicit["EXB"]["max_pct"] == 5.0
        assert implicit["EXB"]["status"] == "breached"
        assert implicit["EXB"]["id"] is None
        assert out["breached"] == 2 and out["near"] == 0

    def test_funds_get_no_implicit_cap_and_ok_status(self, test_database):
        _hold(test_database, "EXF", 1000.0, atype="etf")
        test_database.create_position_cap("symbol", "EXF", 30)
        out = cap_status(test_database, today=TODAY)
        assert [c["key"] for c in out["caps"]] == ["EXF"]
        assert out["caps"][0]["status"] == "breached"

    def test_empty_portfolio(self, test_database):
        test_database.create_position_cap("sector", "Energy", 10)
        out = cap_status(test_database, today=TODAY)
        assert out["total_value_eur"] == 0
        assert out["caps"][0]["current_pct"] == 0 and out["caps"][0]["status"] == "ok"


class TestActionItem:
    def test_registered(self):
        assert check_position_caps in checks_for_tests()

    def test_one_item_per_breached_explicit_cap(self, held):
        held.create_position_cap("symbol", "EXA", 50)
        held.create_position_cap("sector", "Energy", 50)
        items = check_position_caps(held)
        assert [i["id"] for i in items] == ["cap:symbol:EXA"]
        item = items[0]
        assert item["category"] == "allocation" and item["severity"] == "high"
        assert item["title"] == "EXA is over its 50% cap (60%)"
        assert item["link_page"] == "caps"

    def test_nothing_when_within_caps(self, held):
        held.create_position_cap("symbol", "EXA", 90)
        assert check_position_caps(held) == []


class TestRouter:
    @pytest.mark.asyncio
    async def test_crud_and_validation(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.post(
            "/api/v1/caps/",
            json={"scope": "symbol", "key": "exa", "max_pct": 4.5, "note": "core"},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_201_CREATED
        row = resp.json()
        assert row["key"] == "EXA" and row["max_pct"] == 4.5

        dup = await async_test_client.post(
            "/api/v1/caps/",
            json={"scope": "symbol", "key": "EXA", "max_pct": 3},
            headers=auth_headers,
        )
        assert dup.status_code == status.HTTP_400_BAD_REQUEST
        bad = await async_test_client.post(
            "/api/v1/caps/",
            json={"scope": "symbol", "key": "EXB", "max_pct": 0},
            headers=auth_headers,
        )
        assert bad.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        listing = await async_test_client.get("/api/v1/caps/", headers=auth_headers)
        assert listing.json()["total"] == 1

        upd = await async_test_client.put(
            f"/api/v1/caps/{row['id']}", json={"max_pct": 6}, headers=auth_headers
        )
        assert upd.status_code == 200 and upd.json()["max_pct"] == 6
        assert upd.json()["note"] == "core"
        empty = await async_test_client.put(
            f"/api/v1/caps/{row['id']}", json={}, headers=auth_headers
        )
        assert empty.status_code == status.HTTP_400_BAD_REQUEST
        missing = await async_test_client.put(
            "/api/v1/caps/999", json={"max_pct": 6}, headers=auth_headers
        )
        assert missing.status_code == status.HTTP_404_NOT_FOUND

        gone = await async_test_client.delete(
            f"/api/v1/caps/{row['id']}", headers=auth_headers
        )
        assert gone.status_code == status.HTTP_204_NO_CONTENT
        again = await async_test_client.delete(
            f"/api/v1/caps/{row['id']}", headers=auth_headers
        )
        assert again.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_status_route_is_not_read_as_an_id(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get("/api/v1/caps/status", headers=auth_headers)
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert set(body) == {"as_of", "total_value_eur", "caps", "breached", "near"}

    @pytest.mark.asyncio
    async def test_requires_auth(self, async_test_client: AsyncClient):
        resp = await async_test_client.get("/api/v1/caps/status")
        assert resp.status_code in (401, 403)


def test_chat_tool_returns_status(held):
    held.create_position_cap("symbol", "EXA", 50)
    out = json.loads(execute_tool("get_position_caps", {}, held))
    assert out["breached"] >= 1
    assert out["caps"][0]["key"] == "EXA"


# ---- allocator ---------------------------------------------------------------

STOCK_SETTINGS = {
    "max_lines_per_bucket": 2,
    "min_line_eur": 100,
    "max_single_stock_pct": 5.0,
}


def _cand(asset_id, **extra):
    base = {
        "asset_id": asset_id,
        "symbol": f"S{asset_id}",
        "name": f"Name {asset_id}",
        "asset_type": "stock",
        "bucket": "stock",
        "portfolio_id": 1,
        "portfolio_name": "Example Broker",
        "value_eur": 0.0,
        "weight_pct": 0.0,
        "is_fund": False,
        "ter_pct": None,
        "overlap_group": None,
        "buy_below": 20.0,
        "fair_value": None,
        "recommendation": None,
        "conviction": None,
        "blocked": None,
        "managed": False,
        "is_watchlist": False,
        "price_eur": 10.0,
        "price_native": 10.0,
        "currency": "EUR",
    }
    base.update(extra)
    return base


class TestAllocatorCaps:
    def test_tighter_symbol_cap_wins_over_the_default(self):
        capped = _cand(1, symbol_cap_pct=2.0)
        lines, left, _, why = allocator.pick_lines(
            "stock", [capped], 1000.0, STOCK_SETTINGS, {"total_after": 10000}
        )
        assert [ln["amount_eur"] for ln in lines] == [200.0] and left == 800.0
        assert why == ["would exceed the 5 % single-stock cap"]

    def test_looser_symbol_cap_does_not_raise_the_default(self):
        loose = _cand(1, symbol_cap_pct=20.0)
        lines, left, _, _ = allocator.pick_lines(
            "stock", [loose], 1000.0, STOCK_SETTINGS, {"total_after": 10000}
        )
        assert [ln["amount_eur"] for ln in lines] == [500.0] and left == 500.0

    def test_symbol_at_its_cap_is_excluded_with_the_cap_in_the_reason(self):
        full = _cand(1, symbol_cap_pct=2.0, value_eur=300.0)
        ranked, _, excluded = allocator.rank_stock_candidates([full], 10000, 5.0)
        assert ranked == []
        assert excluded[0][1] == "would exceed the 2 % cap on S1"

    def test_breached_sector_excludes_its_stocks(self):
        tech = _cand(1, sector="Technology", sector_cap_pct=20.0, sector_value_eur=2500)
        other = _cand(2, sector="Energy", sector_cap_pct=20.0, sector_value_eur=500)
        ranked, _, excluded = allocator.rank_stock_candidates([tech, other], 10000, 5.0)
        assert [c["symbol"] for c, _ in ranked] == ["S2"]
        assert excluded == [(tech, "would exceed the Technology cap")]

    def test_sector_room_is_shared_by_its_stocks(self):
        kw = {"sector": "Technology", "sector_cap_pct": 10.0, "sector_value_eur": 800}
        a = _cand(1, **kw)
        b = _cand(2, **kw)
        settings = {**STOCK_SETTINGS, "max_single_stock_pct": 50.0}
        lines, left, _, _ = allocator.pick_lines(
            "stock", [a, b], 400.0, settings, {"total_after": 10000}
        )
        assert sum(ln["amount_eur"] for ln in lines) == 200.0 and left == 200.0

    def test_excess_spills_to_a_stock_outside_the_capped_sector(self):
        tech = _cand(1, sector="Technology", sector_cap_pct=10.0, sector_value_eur=800)
        energy = _cand(2, sector="Energy", buy_below=5.0)
        settings = {**STOCK_SETTINGS, "max_single_stock_pct": 50.0}
        lines, left, _, _ = allocator.pick_lines(
            "stock", [tech, energy], 400.0, settings, {"total_after": 10000}
        )
        assert left == 0
        assert {ln["symbol"]: ln["amount_eur"] for ln in lines} == {
            "S1": 200.0,
            "S2": 200.0,
        }

    def test_without_caps_nothing_changes(self):
        plain = _cand(1)
        lines, left, _, _ = allocator.pick_lines(
            "stock", [plain], 1000.0, STOCK_SETTINGS, {"total_after": 10000}
        )
        assert [ln["amount_eur"] for ln in lines] == [500.0] and left == 500.0

    def test_build_candidates_attaches_the_stored_caps(self, held, monkeypatch):
        monkeypatch.setattr(allocator, "blocked_assets", lambda *a, **k: {})
        held.create_position_cap("symbol", "exb", 3)
        held.create_position_cap("sector", "technology", 95)
        before_holdings = [
            {
                "asset_id": a["id"],
                "symbol": a["symbol"],
                "asset_type": "stock",
                "value_eur": v,
                "price_eur": v / 10,
                "price": v / 10,
                "currency": "EUR",
            }
            for a, v in (
                (held.get_asset_by_symbol("EXA"), 600.0),
                (held.get_asset_by_symbol("EXB"), 300.0),
            )
        ]
        from portf_manager.services.deployable import load_settings

        cands, _ = allocator.build_candidates(
            held, before_holdings, [], load_settings(held), TODAY, lambda c: 1.0
        )
        by_symbol = {c["symbol"]: c for c in cands}
        assert by_symbol["EXB"]["symbol_cap_pct"] == 3.0
        assert by_symbol["EXA"]["symbol_cap_pct"] is None
        assert by_symbol["EXA"]["sector"] == "Technology"
        assert by_symbol["EXA"]["sector_cap_pct"] == 95.0
        assert by_symbol["EXA"]["sector_value_eur"] == pytest.approx(900.0)
