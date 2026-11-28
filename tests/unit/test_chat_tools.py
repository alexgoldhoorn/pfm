"""Tests for chat tool catalog and execute_tool() dispatcher."""

import json
from datetime import date, timedelta
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


def test_tools_list_has_56_entries():
    from portf_server.chat_tools import TOOLS

    assert len(TOOLS) == 56


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


def test_execute_tool_get_household_finances(db):
    from portf_server.chat_tools import execute_tool

    db.create_monthly_cashflow("Salary", "salary", 3000)
    db.create_monthly_cashflow("Mortgage", "mortgage", 900)
    db.create_loan(
        name="Example Mortgage",
        principal=120_000,
        annual_rate_pct=3.0,
        start_date="2024-01-01",
        term_months=300,
    )
    db.create_goal("Example Trip", 5_000, "2028-01-01", 150, 2, start_networth_eur=0)
    # the balance sheet behind the cash figure and net worth would look up FX live
    with (
        patch("portf_server.routers.analytics._cash_eur", return_value=4_000.0),
        patch("portf_server.routers.networth.net_worth_eur", return_value=1_000.0),
    ):
        data = json.loads(execute_tool("get_household_finances", {}, db))
    cf = data["entered_monthly_cashflow"]
    assert cf["income_eur"] == 3000 and cf["expenses_eur"] == 900
    assert cf["net_monthly_eur"] == 2100
    measured = data["measured_from_bank_statements"]
    # no bank imports: nothing measured, but the cash buffer is known
    assert measured["months_used"] == []
    assert measured["cash_eur"] == 4_000.0
    assert measured["avg_monthly_net_eur"] is None
    assert data["budget_this_month"] is None
    (loan,) = data["loans"]
    assert loan["name"] == "Example Mortgage"
    assert loan["monthly_payment_eur"] > 0
    assert data["loan_payments_monthly_eur"] == loan["monthly_payment_eur"]
    assert data["goals_planned_monthly_eur"] == 150
    assert data["open_goals"] == 1
    assert "realistic" in data["notes"]


# ── action items / diagnostics tools ──────────────────────────────────────


@pytest.fixture
def eur_db(tmp_path):
    """An EUR-only database: the action-items checks convert every holding
    to EUR, and a USD asset would make them reach for a live FX rate."""
    from portf_manager.database import Database

    return Database(str(tmp_path / "eur.db"))


def _zero_price_buy(d):
    pid = d.get_or_create_portfolio("Test Broker")
    d.create_asset("ZP", "Zero Price Co", "stock", currency="EUR")
    asset = d.get_asset_by_symbol("ZP")
    return d.create_transaction(
        portfolio_id=pid,
        asset_id=asset["id"],
        transaction_type="buy",
        quantity=5.0,
        price=0.0,
        total_amount=0.0,
        transaction_date="2024-02-01",
        currency="EUR",
    )


def test_get_action_items_returns_open_items_and_summary(eur_db):
    from portf_server.chat_tools import execute_tool

    _zero_price_buy(eur_db)
    data = json.loads(execute_tool("get_action_items", {}, eur_db))
    assert data["count"] == len(data["items"])
    susp = next(i for i in data["items"] if i["id"] == "dq:suspicious")
    assert susp["status"] == "open"
    assert susp["page"] == "diagnostics"
    assert data["summary"]["open"] >= 1


def test_set_action_item_status_then_hidden_unless_resolved_included(eur_db):
    from portf_server.chat_tools import execute_tool

    _zero_price_buy(eur_db)
    out = json.loads(
        execute_tool(
            "set_action_item_status",
            {"id": "dq:suspicious", "status": "checked", "note": "placeholder row"},
            eur_db,
        )
    )
    assert out["ack"]["note"] == "placeholder row"

    open_items = json.loads(execute_tool("get_action_items", {}, eur_db))["items"]
    assert not any(i["id"] == "dq:suspicious" for i in open_items)

    all_items = json.loads(
        execute_tool("get_action_items", {"include_resolved": True}, eur_db)
    )["items"]
    checked = next(i for i in all_items if i["id"] == "dq:suspicious")
    assert checked["status"] == "checked"
    assert checked["note"] == "placeholder row"


def test_set_action_item_status_reports_bad_input_as_error_field(eur_db):
    from portf_server.chat_tools import execute_tool

    out = json.loads(
        execute_tool(
            "set_action_item_status", {"id": "goals:1", "status": "snoozed"}, eur_db
        )
    )
    assert "error" in out


def test_get_diagnostics_covers_prices_runs_dq_and_logs(eur_db):
    from portf_server.chat_tools import execute_tool

    tx_id = _zero_price_buy(eur_db)
    eur_db.record_price_update_run(
        started_at="2026-07-15T20:00:00",
        duration_seconds=12.0,
        updated_count=5,
        skipped_count=0,
        error_count=1,
        error_symbols=["AAPL"],
        api_errors=["AAPL: rate limited"],
        source="cron",
    )
    data = json.loads(execute_tool("get_diagnostics", {}, eur_db))
    assert "stale_count" in data["price_data"]
    assert data["price_update_runs"][0]["error_symbols"] == ["AAPL"]
    assert data["price_update_runs"][0]["api_errors"] == ["AAPL: rate limited"]
    dq = data["data_quality"]
    assert dq["suspicious_open"] == 1
    assert dq["open_suspicious"][0]["finding_id"] == f"dq:susp:{tx_id}:zero_price"
    assert "last_24h" in data["log_problems"]

    execute_tool(
        "set_action_item_status",
        {"id": f"dq:susp:{tx_id}:zero_price", "status": "checked"},
        eur_db,
    )
    dq2 = json.loads(execute_tool("get_diagnostics", {}, eur_db))["data_quality"]
    assert dq2["suspicious_open"] == 0
    assert dq2["suspicious_checked"] == 1


def test_get_wealth_scenarios_returns_saved_plans(db):
    import json as _json

    from portf_server.chat_tools import execute_tool

    db.create_wealth_scenario(
        "Base case",
        _json.dumps({"stocks_amount": 1000, "years": 10}),
        "2026-01-01",
        100.0,
        is_primary=True,
    )
    with patch(
        "portf_manager.services.wealth_scenarios.current_net_worth",
        return_value=150.0,
    ):
        out = _json.loads(execute_tool("get_wealth_scenarios", {}, db))
    assert out["count"] == 1
    s = out["scenarios"][0]
    assert s["name"] == "Base case" and s["is_primary"] is True
    assert s["params"]["years"] == 10
    assert s["summary"]["milestones"][-1]["year"] == 10
    assert s["tracking"]["status"] in {"on_track", "ahead", "behind", "too_early"}
    assert out["notes"]


@pytest.fixture
def bank_db(db):
    """The base db plus a bank account with a few example rows."""
    from datetime import date

    bank = db.get_or_create_portfolio("Example Bank")
    today = date.today().isoformat()
    for desc, amt, cat in [
        ("Example Supermarket", -80.0, "Groceries"),
        ("Example Supermarket", -40.0, "Groceries"),
        ("Example Streaming", -12.0, "Entertainment"),
        ("Example Employer", 2000.0, "Salary"),
    ]:
        db.create_spending_transaction(bank, today, desc, amt, category=cat)
    return db


def test_spending_summary_tool(bank_db):
    from portf_server.chat_tools import execute_tool

    with patch("portf_server.routers.spending._fx", return_value=1.0):
        data = json.loads(execute_tool("get_spending_summary", {"days": 30}, bank_db))
    assert data["spent_eur"] == 132.0 and data["income_eur"] == 2000.0
    assert list(data["by_category_eur"])[0] == "Groceries"


def test_spending_trend_tool_is_zero_filled(bank_db):
    from portf_server.chat_tools import execute_tool

    with patch("portf_server.routers.spending._fx", return_value=1.0):
        data = json.loads(execute_tool("get_spending_trend", {"months": 3}, bank_db))
    assert len(data["months"]) == 3
    assert data["months"][-1]["spent_eur"] == 132.0
    assert data["months"][0]["spent_eur"] == 0.0


def test_spending_breakdown_tool_unknown_parent_is_error(bank_db):
    from portf_server.chat_tools import execute_tool

    out = execute_tool("get_spending_breakdown", {"parent": "No Such Node"}, bank_db)
    assert out.startswith("Error:")


def test_recurring_spending_tool_returns_totals(bank_db):
    from portf_server.chat_tools import execute_tool

    with patch("portf_server.routers.spending._fx", return_value=1.0):
        data = json.loads(execute_tool("get_recurring_spending", {}, bank_db))
    assert {"items", "monthly_total_eur", "annual_total_eur"} <= set(data)


def test_search_spending_transactions_filters_and_caps(bank_db):
    from portf_server.chat_tools import execute_tool

    with patch("portf_server.chat_tools._fx", return_value=1.0):
        data = json.loads(
            execute_tool(
                "search_spending_transactions",
                {"query": "supermarket", "amount_sign": "negative", "limit": 1},
                bank_db,
            )
        )
    assert data["count"] == 1 and data["limit"] == 1
    assert data["transactions"][0]["category"] == "Groceries"
    assert data["transactions"][0]["amount_eur"] < 0


def test_search_spending_transactions_rejects_bad_sign(bank_db):
    from portf_server.chat_tools import execute_tool

    out = execute_tool("search_spending_transactions", {"amount_sign": "up"}, bank_db)
    assert out.startswith("Error:")


_INFO = {
    "longName": "Example Corp",
    "quoteType": "EQUITY",
    "currency": "USD",
    "sector": "Technology",
    "longBusinessSummary": "word " * 400,
    "currentPrice": 100.0,
    "fiftyTwoWeekHigh": 120.0,
    "trailingPE": 20.0,
    "targetMeanPrice": 110.0,
    "recommendationKey": "buy",
    "exDividendDate": 1700000000,
    "marketCap": None,
}


def test_market_profile_groups_fields_and_serves_the_cache(db):
    from portf_manager import market

    calls = []

    def fake(symbol):
        calls.append(symbol)
        return dict(_INFO)

    with patch.object(market, "_fetch_info_live", fake):
        first = market.get_market_profile(db, "exco")
        second = market.get_market_profile(db, "EXCO")
    assert calls == ["EXCO"]
    assert first["source"] == "live" and second["source"] == "cache"
    assert first["name"] == "Example Corp"
    assert first["price"] == {"price": 100.0, "week52_high": 120.0}
    assert first["valuation"] == {"trailing_pe": 20.0}
    assert first["analysts"] == {"recommendation": "buy", "target_mean": 110.0}
    assert first["dividends"] == {"ex_dividend_date": "2023-11-14"}
    assert len(first["profile"]["summary"]) <= 801
    assert "fund" not in first


def test_market_profile_converts_pence_prices_only(db):
    from portf_manager import market

    info = {
        "currency": "GBp",
        "currentPrice": 18400.0,
        "marketCap": 6_000_000_000,
        "dividendRate": 4.0,
    }
    with patch.object(market, "_fetch_info_live", lambda s: info):
        out = market.get_market_profile(db, "EXCO.L")
    assert out["currency"] == "GBP"
    assert out["price"] == {"price": 184.0, "market_cap": 6_000_000_000}
    # Yahoo reports the dividend per share in pounds already.
    assert out["dividends"] == {"dividend_rate": 4.0}
    assert "already come in pounds" in out["note"]


def test_market_profile_failure_is_an_error_not_an_empty_profile(db):
    from portf_manager import market

    def boom(symbol):
        raise RuntimeError("yahoo down")

    with patch.object(market, "_fetch_info_live", boom):
        out = market.get_market_profile(db, "NOPE")
    assert out == {
        "symbol": "NOPE",
        "error": "yahoo down",
        "source": "error",
        "stale": True,
    }
    with patch.object(market, "_fetch_info_live", lambda s: {"currency": "USD"}):
        assert market.get_market_profile(db, "NOPE")["error"] == "no data available"


def test_get_market_data_tool_uses_the_held_assets_ticker_and_adds_news(db):
    from portf_manager import market
    from portf_manager.services import research
    from portf_server.chat_tools import execute_tool

    asset = db.get_asset_by_symbol("AAPL")
    db.update_asset(asset["id"], ticker="AAPL.XY")
    seen = []

    def fake(symbol):
        seen.append(symbol)
        return {**_INFO, "quoteType": "ETF"}

    news = [{"title": "Example news", "url": "https://example.com/n", "publisher": ""}]
    comp = {"sectors": {"Technology": 0.6}, "asset_class": {"equity": 1.0}}
    with (
        patch.object(market, "_fetch_info_live", fake),
        patch.object(market, "get_fund_composition", lambda d, s: comp),
        patch.object(research, "fetch_recent_news", lambda s, db=None: news),
    ):
        data = json.loads(execute_tool("get_market_data", {"symbol": "AAPL"}, db))
        quiet = json.loads(
            execute_tool(
                "get_market_data", {"symbol": "AAPL", "include_news": False}, db
            )
        )
    assert seen == ["AAPL.XY"]
    assert data["requested_symbol"] == "AAPL"
    # A readable time and an age, not the cache's epoch number.
    assert data["fetched_at"].endswith(" UTC")
    assert data["age_hours"] == 0.0
    assert data["held_asset"]["symbol"] == "AAPL"
    assert data["composition"] == comp
    assert data["news"] == news
    assert "news" not in quiet


def test_get_market_data_tool_resolves_a_held_coin_to_its_yahoo_pair(db):
    from portf_manager import market
    from portf_server.chat_tools import execute_tool

    db.create_asset(symbol="SUI", name="Example Coin", asset_type="crypto")
    db.create_asset(symbol="EXC", name="Other Coin", asset_type="crypto")
    seen = []

    def fake(symbol):
        seen.append(symbol)
        return dict(_INFO)

    with patch.object(market, "_fetch_info_live", fake):
        for sym in ("SUI", "EXC"):
            execute_tool("get_market_data", {"symbol": sym, "include_news": False}, db)
    # The override map wins; any other coin is the plain EUR pair.
    assert seen == ["SUI20947-USD", "EXC-EUR"]


# ── cost control tools ────────────────────────────────────────────────────


@pytest.fixture
def cost_db(tmp_path):
    from portf_manager.database import Database

    d = Database(str(tmp_path / "cost.db"))
    spend = d.find_spending_category_by_name("Spend")["id"]
    d.create_spending_category("Rent", spend)
    d.set_spending_category_essentiality("Rent", "fixed")
    pid = d.get_or_create_portfolio("Example Bank", account_type="bank")
    today = date.today()
    for ago in range(1, 5):
        day = (today.replace(day=1) - timedelta(days=30 * ago)).replace(day=3)
        d.create_spending_transaction(
            pid, day.isoformat(), "Example Landlord", -900.0, category="Rent"
        )
    d.create_spending_transaction(
        pid, today.isoformat(), "Example Market", -40.0, balance=500.0
    )
    return d


def test_compare_spending_reports_status_and_notes(cost_db):
    from portf_server.chat_tools import execute_tool

    data = json.loads(
        execute_tool("compare_spending", {"group_by": "merchant"}, cost_db)
    )
    assert data["group_by"] == "merchant"
    assert data["status"] in ("complete", "no_imports", "partial")
    assert "estimate" in data["notes"] and "data_through" in data
    assert execute_tool("compare_spending", {"group_by": "x"}, cost_db).startswith(
        "Error:"
    )


def test_anomalies_tiers_forecast_and_review_tools(cost_db):
    from portf_server.chat_tools import execute_tool

    anomalies = json.loads(
        execute_tool("get_spending_anomalies", {"days": 30}, cost_db)
    )
    assert anomalies["days"] == 30 and "verdicts" in anomalies["notes"]
    tiers = json.loads(execute_tool("get_spending_tiers", {"months": 3}, cost_db))
    assert len(tiers["months"]) == 3 and tiers["data_through"]
    forecast = json.loads(execute_tool("get_cashflow_forecast", {"months": 2}, cost_db))
    assert len(forecast["next_months"]) == 2
    assert forecast["balance"]["start_eur"] == 500.0
    assert "estimate" in forecast["notes"]
    review = json.loads(execute_tool("get_spending_review", {}, cost_db))
    assert review["data_through"] == date.today().isoformat()
    assert {"totals", "movers", "forecast", "warnings", "notes"} <= set(review)
    assert execute_tool(
        "get_spending_review", {"month": "2999-01"}, cost_db
    ).startswith("Error:")


def test_forecast_tool_what_if_planned_items_and_tax_fields(cost_db):
    from portf_server.chat_tools import execute_tool

    month = (date.today().replace(day=1) + timedelta(days=40)).strftime("%Y-%m")
    cost_db.create_planned_item(
        date=f"{month}-10",
        description="Example Holiday",
        amount=-1000.0,
        kind="spending",
    )
    out = json.loads(
        execute_tool(
            "get_cashflow_forecast",
            {"months": 3, "extra_amount": 500, "extra_date": month},
            cost_db,
        )
    )
    assert out["what_if"]["amount_eur"] == -500.0
    assert out["what_if"]["date"] == f"{month}-15"
    assert {"lowest_balance_eur", "lowest_balance_without_eur"} <= set(out["what_if"])
    statuses = {i["description"]: i["status"] for i in out["planned_items"]}
    assert statuses["Example Holiday"] == "projected"
    assert out["tax_payments"] == []
    assert "planned_spending_eur" in out["next_months"][0]
    assert "tax_eur" in out["next_months"][0]
    assert "what_if" not in json.loads(
        execute_tool("get_cashflow_forecast", {"months": 2}, cost_db)
    )
    # one half of a what-if, a bad kind and a bad date are refused
    for args in (
        {"extra_amount": 100},
        {"extra_amount": 100, "extra_date": month, "extra_kind": "x"},
        {"extra_amount": 100, "extra_date": "2026-13"},
        {"extra_amount": -5, "extra_date": month},
    ):
        assert execute_tool("get_cashflow_forecast", args, cost_db).startswith("Error:")


def test_planned_items_tool_lists_open_and_done(cost_db):
    from portf_server.chat_tools import execute_tool

    day = (date.today() + timedelta(days=45)).isoformat()
    cost_db.create_planned_item(
        date=day, description="Example Repair", amount=-300.0, kind="spending"
    )
    done = cost_db.create_planned_item(
        date=day, description="Example Gift", amount=-50.0, kind="spending"
    )
    cost_db.update_planned_item(done, done=1)
    out = json.loads(execute_tool("get_planned_items", {}, cost_db))
    assert [i["description"] for i in out["items"]] == ["Example Repair"]
    assert out["items"][0]["done"] is False
    assert out["forecast_status"][0]["status"] in ("projected", "beyond_horizon")
    both = json.loads(
        execute_tool("get_planned_items", {"include_done": True}, cost_db)
    )
    assert len(both["items"]) == 2 and "web client" in both["notes"]


def test_spending_year_and_uncategorized_queue_tools(cost_db):
    from portf_server.chat_tools import execute_tool

    pid = cost_db.get_or_create_portfolio("Example Bank", account_type="bank")
    cost_db.create_spending_transaction(
        pid, date.today().isoformat(), "Example Kiosk", -75.0
    )
    year = json.loads(execute_tool("get_spending_year", {}, cost_db))
    assert year["year"] == date.today().year
    assert {"totals", "months", "categories", "warnings", "notes"} <= set(year)
    assert len(year["months"]["this"]) == 12
    assert execute_tool("get_spending_year", {"year": 1990}, cost_db).startswith(
        "Error:"
    )
    queue = json.loads(execute_tool("get_uncategorized_queue", {"limit": 5}, cost_db))
    assert {"groups", "total_count", "last_complete_month", "notes"} <= set(queue)
    assert queue["groups"][0]["merchant"]
    assert {"proposed_pattern", "transaction_ids", "total_eur"} <= set(
        queue["groups"][0]
    )


def test_household_finances_budget_reports_available_pace_and_fund(cost_db):
    from portf_server.chat_tools import _budget_digest

    digest = _budget_digest(
        {
            "budget_name": "Base",
            "months": ["2026-07"],
            "as_of": "2026-07-15",
            "data_through": "2026-07-10",
            "sections": [
                {
                    "key": "spending",
                    "label": "Spending",
                    "planned_total": 100.0,
                    "available_total": 140.0,
                    "actual_total": 90.0,
                    "variance_eur": 50.0,
                    "pace": {"status": "on_track"},
                    "lines": [
                        {
                            "label": "Insurance",
                            "set_aside_eur": 100.0,
                            "available_total": 140.0,
                            "actual_total": 0.0,
                            "fund": {
                                "yearly_amount_eur": 1200.0,
                                "remaining_eur": 140.0,
                            },
                            "pace": {
                                "status": "at_risk",
                                "limit_eur": 140.0,
                                "actual_to_date_eur": 0.0,
                                "projected_month_end_eur": 300.0,
                            },
                        }
                    ],
                }
            ],
            "net": {},
            "warnings": [],
        }
    )
    sec = digest["sections"][0]
    assert sec["available_total"] == 140.0 and sec["pace"] == {"status": "on_track"}
    assert digest["yearly_funds"][0]["yearly_amount_eur"] == 1200.0
    assert digest["lines_at_risk_or_over"][0]["status"] == "at_risk"
    assert digest["data_through"] == "2026-07-10"


def test_budget_digest_keeps_too_early_pace_and_tells_the_model_not_to_guess(cost_db):
    from portf_server.chat_tools import _budget_digest

    digest = _budget_digest(
        {
            "sections": [
                {
                    "key": "spending",
                    "label": "Spending",
                    "lines": [
                        {
                            "label": "Groceries",
                            "pace": {
                                "status": "too_early",
                                "limit_eur": 400.0,
                                "actual_to_date_eur": 90.0,
                                "projected_month_end_eur": None,
                            },
                        }
                    ],
                    "pace": {"status": "too_early", "projected_month_end_eur": None},
                }
            ]
        }
    )
    assert digest["sections"][0]["pace"]["projected_month_end_eur"] is None
    assert digest["lines_at_risk_or_over"] == []
    assert "too_early" in digest["notes"] and "rolling 12 months" in digest["notes"]


# ── loans ──────────────────────────────────────────────────────────────────


def _loan_db(tmp_path, with_periods=True):
    from portf_manager.database import Database

    d = Database(str(tmp_path / "loans.db"))
    lid = d.create_loan(
        "Example Mortgage",
        100000.0,
        3.0,
        "2020-01-01",
        240,
        rate_as_of="2025-01-01",
        balance_as_of=80000.0,
    )
    if with_periods:
        d.create_loan_rate_period(lid, "2020-01-01", 2.4)
    return d


def test_get_loans_reports_the_loan_and_the_history(tmp_path):
    from portf_server.chat_tools import execute_tool

    res = json.loads(execute_tool("get_loans", {}, _loan_db(tmp_path)))
    loan = res["loans"][0]
    assert loan["name"] == "Example Mortgage" and loan["annual_rate_pct"] == 3.0
    assert loan["monthly_payment_eur"] > 0 and loan["balance_eur"] > 0
    assert loan["paid_to_date"]["complete"] is True
    assert [p["from_date"] for p in loan["rate_periods"]] == ["2020-01-01"]
    assert loan["equity_eur"] is None and "property" in loan["equity_note"]
    assert loan["latest_bank_month"] is None and "No spending category" in (
        loan["bank_reason"]
    )


def test_get_loans_hides_an_incomplete_paid_to_date(tmp_path):
    from portf_server.chat_tools import execute_tool

    res = json.loads(
        execute_tool("get_loans", {}, _loan_db(tmp_path, with_periods=False))
    )
    assert res["loans"][0]["paid_to_date"] is None


def test_get_loans_with_no_loan(tmp_path):
    from portf_manager.database import Database
    from portf_server.chat_tools import execute_tool

    res = json.loads(execute_tool("get_loans", {}, Database(str(tmp_path / "e.db"))))
    assert res["loans"] == []


def test_loan_what_if_lump_sum_and_rate_change(tmp_path):
    from portf_server.chat_tools import execute_tool

    d = _loan_db(tmp_path)
    lump = json.loads(execute_tool("loan_what_if", {"lump_sum": 5000}, d))
    assert lump["interest_saved_eur"] > 0 and lump["loan"]["name"]
    rate = json.loads(
        execute_tool(
            "loan_what_if",
            {"loan": "example mortgage", "rate_change_pts": 1.0},
            d,
        )
    )
    assert rate["rate_change"]["payment_diff_eur"] > 0
    assert rate["interest_saved_eur"] < 0


def test_loan_what_if_errors_are_text(tmp_path):
    from portf_server.chat_tools import execute_tool

    d = _loan_db(tmp_path)
    out = execute_tool("loan_what_if", {"new_rate_pct": 4, "lump_sum": 100}, d)
    assert out.startswith("Error:") and "cannot be combined" in out
    assert execute_tool("loan_what_if", {"loan": "nope"}, d) == "Error: No such loan"
    d.create_loan("Second", 1000, 3, "2020-01-01", 12)
    assert "Several loans" in execute_tool("loan_what_if", {"lump_sum": 1}, d)


def test_get_action_items_folds_a_family_and_lists_its_children(eur_db):
    from portf_server.chat_tools import execute_tool

    def child(n):
        return {
            "id": f"recurring:missed:{n}",
            "severity": "medium",
            "category": "spending",
            "status": "open",
            "title": f"Missed charge {n}",
            "detail": "d",
            "link_page": "spending",
        }

    report = {
        "items": [child(1), child(2), child(3)],
        "summary": {"open": 3},
        "acks": {},
    }
    with patch(
        "portf_manager.services.action_items.get_action_items",
        return_value=report["items"],
    ):
        folded = json.loads(execute_tool("get_action_items", {}, eur_db))
        groups = [i for i in folded["items"] if i.get("kind") == "group"]
        assert groups and groups[0]["count"] == 3
        assert groups[0]["summary"]
        fam = groups[0]["family"]
        kids = json.loads(execute_tool("get_action_items", {"family": fam}, eur_db))
    assert kids["count"] == 3
    assert {k["id"] for k in kids["items"]} == {
        "recurring:missed:1",
        "recurring:missed:2",
        "recurring:missed:3",
    }


def test_get_action_items_unknown_family_is_empty(eur_db):
    from portf_server.chat_tools import execute_tool

    out = json.loads(execute_tool("get_action_items", {"family": "nope"}, eur_db))
    assert out == {"family": "nope", "count": 0, "items": []}


def test_networth_history_tool_without_snapshots_says_not_available(eur_db):
    from portf_server.chat_tools import execute_tool

    out = json.loads(execute_tool("get_networth_history", {"period": "1m"}, eur_db))
    assert out["snapshots_in_window"] == 0
    assert out["change"]["parts"]["saved_eur"] is None
    assert "no snapshots stored yet" in out["warnings"]


def test_networth_history_tool_splits_change_between_two_snapshots(eur_db):
    from datetime import date, timedelta

    from portf_server.chat_tools import execute_tool

    today = date.today()
    for day, cash in ((today - timedelta(days=20), 1000.0), (today, 1500.0)):
        eur_db.upsert_networth_snapshot(
            snapshot_date=day.isoformat(),
            net_worth_eur=cash,
            investments_eur=0.0,
            cash_eur=cash,
            property_eur=0.0,
            other_assets_eur=0.0,
            liabilities_eur=0.0,
        )
    out = json.loads(execute_tool("get_networth_history", {"period": "3m"}, eur_db))
    assert out["snapshots_in_window"] == 2
    assert out["first_snapshot"] == (today - timedelta(days=20)).isoformat()
    assert out["latest"]["cash_eur"] == 1500.0
    assert out["change"]["net_worth_change_eur"] == 500.0


def test_networth_history_tool_rejects_bad_period(eur_db):
    from portf_server.chat_tools import execute_tool

    assert execute_tool("get_networth_history", {"period": "soon"}, eur_db).startswith(
        "Error:"
    )
    assert execute_tool(
        "get_networth_history", {"start": "2026-02-01", "end": "2026-01-01"}, eur_db
    ).startswith("Error:")


def test_month_close_tool_returns_checklist_for_a_month(bank_db):
    from portf_server.chat_tools import execute_tool

    with patch("portf_server.chat_tools._fx", return_value=1.0):
        out = json.loads(execute_tool("get_month_close", {"month": "2020-01"}, bank_db))
    assert out["month"] == "2020-01"
    assert {"checklist", "figures", "closed", "ready"} <= set(out)


def test_month_close_tool_rejects_bad_month(bank_db):
    from portf_server.chat_tools import execute_tool

    assert execute_tool("get_month_close", {"month": "March"}, bank_db).startswith(
        "Error:"
    )
    assert execute_tool("get_month_close", {"month": "2999-01"}, bank_db).startswith(
        "Error:"
    )


def test_recurring_tool_carries_resolution_fields_and_totals(bank_db):
    from portf_server.chat_tools import execute_tool

    with patch("portf_server.routers.spending._fx", return_value=1.0):
        data = json.loads(execute_tool("get_recurring_spending", {}, bank_db))
    assert {"paid_elsewhere_annual_eur", "cancelled_annual_eur"} <= set(data)
    for item in data["items"]:
        assert "resolution" in item and "resolution_stale" in item
