"""Tests for chat tool catalog and execute_tool() dispatcher."""

import json
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def db(tmp_path):
    from portf_manager.database import Database

    d = Database(str(tmp_path / "test.db"))
    # Create a portfolio + asset + transaction for tests
    pid = d.get_or_create_portfolio("Test Broker")
    d.create_asset("AAPL", "Apple Inc", "stock", "USD")
    asset = d.get_asset_by_symbol("AAPL")
    d.create_transaction(
        portfolio_id=pid,
        asset_id=asset["id"],
        transaction_type="buy",
        quantity=10.0,
        price=150.0,
        total_amount=1500.0,
        transaction_date="2024-01-15",
        currency="USD",
    )
    d.create_price(asset["id"], 200.0, "2024-06-01")
    return d


def test_tools_list_has_19_entries():
    from portf_server.chat_tools import TOOLS

    assert len(TOOLS) == 19


def test_every_tool_definition_has_a_handler():
    from portf_server.chat_tools import TOOLS, _REGISTRY

    assert {t.name for t in TOOLS} == set(_REGISTRY)


def test_all_tool_names_unique():
    from portf_server.chat_tools import TOOLS

    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names))


def test_execute_tool_unknown_name_returns_error():
    from portf_server.chat_tools import execute_tool

    db = MagicMock()
    result = execute_tool("nonexistent_tool", {}, db)
    assert result.startswith("Error: unknown tool")


def test_execute_tool_get_holdings_returns_json(db):
    from portf_server.chat_tools import execute_tool

    with patch("portf_server.routers.portfolios._get_fx_rate", return_value=1.0):
        raw = execute_tool("get_holdings", {}, db)

    data = json.loads(raw)
    assert "holdings" in data
    assert data["count"] >= 1
    assert data["holdings"][0]["symbol"] == "AAPL"


def test_execute_tool_get_holdings_symbol_filter(db):
    from portf_server.chat_tools import execute_tool

    with patch("portf_server.routers.portfolios._get_fx_rate", return_value=1.0):
        raw = execute_tool("get_holdings", {"symbol": "AAPL"}, db)

    data = json.loads(raw)
    assert data["count"] == 1

    with patch("portf_server.routers.portfolios._get_fx_rate", return_value=1.0):
        raw2 = execute_tool("get_holdings", {"symbol": "MSFT"}, db)

    data2 = json.loads(raw2)
    assert data2["count"] == 0


def test_execute_tool_get_brokers_returns_list(db):
    from portf_server.chat_tools import execute_tool

    raw = execute_tool("get_brokers", {}, db)
    data = json.loads(raw)
    assert "brokers" in data
    assert len(data["brokers"]) >= 1


def test_execute_tool_get_kpis_returns_total_value(db):
    from portf_server.chat_tools import execute_tool

    with patch("portf_server.routers.portfolios._get_fx_rate", return_value=1.0):
        raw = execute_tool("get_kpis", {}, db)

    data = json.loads(raw)
    assert "total_value_eur" in data
    assert data["total_value_eur"] > 0


def test_execute_tool_get_transactions_returns_list(db):
    from portf_server.chat_tools import execute_tool

    raw = execute_tool("get_transactions", {"limit": 5}, db)
    data = json.loads(raw)
    assert "transactions" in data
    assert len(data["transactions"]) >= 1


def test_execute_tool_get_transactions_symbol_filter(db):
    from portf_server.chat_tools import execute_tool

    raw = execute_tool("get_transactions", {"symbol": "AAPL"}, db)
    data = json.loads(raw)
    assert all(t["symbol"] == "AAPL" for t in data["transactions"])


def test_execute_tool_asset_details_known_symbol(db):
    from portf_server.chat_tools import execute_tool

    raw = execute_tool("asset_details", {"symbol": "AAPL"}, db)
    data = json.loads(raw)
    assert data["symbol"] == "AAPL"
    assert data["name"] == "Apple Inc"


def test_execute_tool_asset_details_unknown_symbol(db):
    from portf_server.chat_tools import execute_tool

    raw = execute_tool("asset_details", {"symbol": "ZZZ_UNKNOWN"}, db)
    assert raw.startswith("Error:")


def test_execute_tool_get_price_no_history(db):
    from portf_server.chat_tools import execute_tool

    raw = execute_tool("get_price", {"symbol": "AAPL"}, db)
    data = json.loads(raw)
    assert "symbol" in data


def test_execute_tool_exception_returns_error_string():
    from portf_server.chat_tools import execute_tool

    bad_db = MagicMock()
    bad_db.get_all_transactions.side_effect = RuntimeError("DB exploded")

    result = execute_tool("get_holdings", {}, bad_db)
    assert result.startswith("Error:")


def test_execute_tool_get_tax_estimate_returns_year(db):
    from portf_server.chat_tools import execute_tool

    raw = execute_tool("get_tax_estimate", {"year": 2024}, db)
    data = json.loads(raw)
    assert "year" in data
    assert data["year"] == 2024


def test_execute_tool_get_allocation_targets(db):
    from portf_server.chat_tools import execute_tool

    db.set_allocation_target("stock", 60.0)
    with patch("portf_manager.market.get_fx_eur", return_value=(0.9, "test")):
        data = json.loads(execute_tool("get_allocation_targets", {}, db))
    assert data["targets"][0]["asset_type"] == "stock"
    assert "analysis" in data


def test_execute_tool_get_deployable_cash(db):
    from portf_server.chat_tools import execute_tool

    with patch(
        "portf_manager.services.deployable.deployable",
        return_value={"deployable_eur": 1234.0},
    ):
        data = json.loads(execute_tool("get_deployable_cash", {}, db))
    assert data == {"deployable_eur": 1234.0}


def test_execute_tool_get_next_euro_passes_amount(db):
    from portf_server.chat_tools import execute_tool

    with patch(
        "portf_manager.services.allocator.next_euro", return_value={"lines": []}
    ) as next_euro:
        json.loads(execute_tool("get_next_euro", {"amount": 500}, db))
    next_euro.assert_called_once_with(db, amount_eur=500.0)


def test_execute_tool_get_goals_returns_summary_and_rows(db):
    from portf_server.chat_tools import execute_tool

    db.create_goal(
        name="Example Fund",
        target_amount_eur=50_000,
        target_date="2040-01-01",
        monthly_contribution_eur=200,
        expected_return_pct=5,
        start_networth_eur=1_000,
        notes="For the example house",
    )
    # the balance sheet behind net worth would look up USD→EUR live
    with patch("portf_server.routers.networth.net_worth_eur", return_value=1500.0):
        data = json.loads(execute_tool("get_goals", {}, db))
    assert data["summary"]["total"] == 1
    (goal,) = data["goals"]
    assert goal["name"] == "Example Fund"
    assert goal["notes"] == "For the example house"
    assert goal["status"] in {"on_track", "off_track"}
    assert goal["start_networth_eur"] == 1_000
    assert goal["current_networth_eur"] == 1500.0
    assert goal["gained_since_start_eur"] == 500.0
    assert "required_monthly_eur" in goal
    assert "notes" in data
