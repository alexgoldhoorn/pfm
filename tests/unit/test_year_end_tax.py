"""Tests for the year-end tax planner (optimizer extension and Action Item)."""

from datetime import date

import pytest

from portf_manager.services.action_items import checks_for_tests
from portf_manager.services.year_end_tax import (
    carry_forward_losses,
    check_year_end_tax,
    compute_year_end_tax,
)

TODAY = date(2026, 11, 10)


def _seed(db, gain_2026=True):
    """Prior-year loss, a gain, two losing positions (one bought recently)."""
    pid = db.get_or_create_portfolio("Broker")
    # 2024: -300 loss; 2025: +100 gain uses 100 of it, so 200 is carried.
    a = db.create_asset("OLD", "Example Old", "stock", currency="EUR")
    db.create_transaction(a, "buy", 10, 100, 1000, "2024-01-10", portfolio_id=pid)
    db.create_transaction(a, "sell", 10, 70, 700, "2024-06-10", portfolio_id=pid)
    b = db.create_asset("MID", "Example Mid", "stock", currency="EUR")
    db.create_transaction(b, "buy", 10, 50, 500, "2025-01-10", portfolio_id=pid)
    db.create_transaction(b, "sell", 10, 60, 600, "2025-06-10", portfolio_id=pid)
    if gain_2026:
        g = db.create_asset("WIN", "Example Winner", "stock", currency="EUR")
        db.create_transaction(g, "buy", 10, 100, 1000, "2025-02-01", portfolio_id=pid)
        db.create_transaction(g, "sell", 10, 150, 1500, "2026-03-01", portfolio_id=pid)
    # Held at a loss: one old buy, one bought ten days ago.
    ls = db.create_asset("LOS", "Example Loser", "stock", currency="EUR")
    db.create_transaction(ls, "buy", 10, 100, 1000, "2026-01-05", portfolio_id=pid)
    db.create_price(ls, 60.0, "2026-11-09")
    rc = db.create_asset("REC", "Example Recent", "stock", currency="EUR")
    db.create_transaction(rc, "buy", 5, 100, 500, "2026-10-31", portfolio_id=pid)
    db.create_price(rc, 80.0, "2026-11-09")


def test_carry_forward_consumes_oldest_and_ignores_expired():
    results = {2021: -900.0, 2022: -500.0, 2023: -100.0, 2024: 150.0, 2025: -40.0}
    # The 2024 gain uses 150 of the oldest valid loss (2022); 2021 is too old.
    assert carry_forward_losses(results, 2026) == {
        2022: 350.0,
        2023: 100.0,
        2025: 40.0,
    }


def test_marginal_saving_deadline_and_carry_forward(test_database):
    _seed(test_database)
    out = compute_year_end_tax(test_database, 2026, TODAY)
    assert out["realised_gain_eur"] == 500.0
    by_symbol = {c["symbol"]: c for c in out["candidates"]}
    # Harvesting LOS (-400) on a +500 result saves 19% of 400.
    assert by_symbol["LOS"]["unrealised_loss_eur"] == -400.0
    assert by_symbol["LOS"]["tax_saved_eur"] == 76.0
    assert by_symbol["LOS"]["can_harvest_until"] == "2026-12-31"
    assert by_symbol["LOS"]["wash_sale_risk"] is False
    assert by_symbol["REC"]["wash_sale_risk"] is True
    # Existing keys are unchanged.
    assert out["harvestable_loss_eur"] == -400.0
    assert out["estimated_tax_saved_eur"] == 76.0
    assert out["deadline"] == {"date": "2026-12-31", "days_left": 51}
    # 2024 loss of 300 minus the 100 used by the 2025 gain.
    assert out["carry_forward"] == {"total_eur": 200.0, "by_year": {2024: 200.0}}
    assert out["base_after_carry_forward_eur"] == 300.0
    assert any("declared" in n for n in out["notes"])
    assert [w["symbol"] for w in out["wash_blocked"]] == ["REC"]


def test_no_carry_forward_without_prior_losses(test_database):
    out = compute_year_end_tax(test_database, 2026, TODAY)
    assert out["carry_forward"] == {"total_eur": 0.0, "by_year": {}}
    assert out["deadline"]["days_left"] == 51
    assert out["candidates"] == []


def test_action_item_inside_window(test_database):
    _seed(test_database)
    items = check_year_end_tax(test_database, TODAY)
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "tax:year-end:2026"
    assert item["category"] == "tax"
    assert item["severity"] == "medium"
    assert item["link_page"] == "analytics"
    assert item["title"] == (
        "Year-end tax check: €600 of losses could offset €500 of gains"
    )


def test_action_item_silent_outside_window(test_database):
    _seed(test_database)
    assert check_year_end_tax(test_database, date(2026, 10, 31)) == []


def test_action_item_silent_without_losses(test_database):
    assert check_year_end_tax(test_database, TODAY) == []


def test_registered_in_checks():
    assert check_year_end_tax in checks_for_tests()


@pytest.mark.asyncio
async def test_endpoint_adds_keys_and_keeps_old_ones(
    async_test_client, auth_headers, test_database
):
    _seed(test_database)
    resp = await async_test_client.get(
        "/api/v1/analytics/tax-optimizer?year=2026", headers=auth_headers
    )
    assert resp.status_code == 200
    d = resp.json()
    for key in (
        "year",
        "realised_gain_eur",
        "income_eur",
        "harvestable_loss_eur",
        "estimated_tax_saved_eur",
        "candidates",
        "note",
        "carry_forward",
        "base_after_carry_forward_eur",
        "deadline",
        "wash_blocked",
        "notes",
    ):
        assert key in d
    assert d["deadline"]["date"] == "2026-12-31"
    assert d["carry_forward"]["total_eur"] == 200.0
    assert all("tax_saved_eur" in c for c in d["candidates"])


def test_chat_tool(test_database):
    import json

    from portf_server.chat_tools import execute_tool

    _seed(test_database)
    out = json.loads(execute_tool("get_year_end_tax", {"year": 2026}, test_database))
    assert out["year"] == 2026
    assert out["carry_forward"]["total_eur"] == 200.0


def test_managed_only_holding_is_never_a_candidate(test_database):
    """A losing fund held only at the robo-advisor cannot be harvested."""
    from datetime import timedelta

    robo = test_database.create_portfolio("Robo", "EUR", is_managed=True)
    own = test_database.get_or_create_portfolio("Broker", base_currency="EUR")
    for sym, pid in (("ROBOFUND", robo), ("OWNFUND", own)):
        aid = test_database.create_asset(sym, f"{sym} fund", "etf", currency="EUR")
        test_database.create_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=10.0,
            price=100.0,
            total_amount=1000.0,
            transaction_date=(date.today() - timedelta(days=120)).isoformat(),
            portfolio_id=pid,
            currency="EUR",
        )
        test_database.create_price(aid, 60.0, date.today().isoformat())
    out = compute_year_end_tax(test_database)
    symbols = {c["symbol"] for c in out["candidates"]}
    assert "OWNFUND" in symbols
    assert "ROBOFUND" not in symbols
    assert any("managed portfolios skipped" in n for n in out["notes"])
