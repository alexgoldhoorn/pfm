"""API tests for the budget router."""

from datetime import date

from fastapi.testclient import TestClient

from portf_manager.database import Database

_TEST_API_KEY = "test-key-budgets-abc123"
HEADERS = {"X-API-Key": _TEST_API_KEY}


def _make_client(tmp_path):
    from portf_server.app import app
    from portf_server.dependencies import get_database, get_api_key_manager
    from portf_server.auth_middleware import APIKeyManager

    db_instance = Database(str(tmp_path / "budget_api_test.db"))
    km = APIKeyManager(db_instance)
    km.create_api_key(key_name="test", description="test key", raw_key=_TEST_API_KEY)
    app.dependency_overrides[get_database] = lambda: db_instance
    app.dependency_overrides[get_api_key_manager] = lambda: km
    return TestClient(app), db_instance


def _seed_tree(db):
    """Spend > Housing > {Rent, Utilities}, Spend > Groceries, Income > Salary."""
    spend = db.find_spending_category_by_name("Spend")["id"]
    income = db.find_spending_category_by_name("Income")["id"]
    housing = db.create_spending_category("Housing", parent_id=spend)
    db.create_spending_category("Rent", parent_id=housing)
    db.create_spending_category("Utilities", parent_id=housing)
    db.create_spending_category("Groceries", parent_id=spend)
    db.create_spending_category("Salary", parent_id=income)


def _this_month(day="10"):
    return f"{date.today().strftime('%Y-%m')}-{day}"


# ── Budgets CRUD ───────────────────────────────────────────────────────────


def test_create_and_list_budgets(tmp_path):
    client, _db = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/budgets/",
        json={"name": "Base", "description": "baseline", "is_active": True},
        headers=HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Base" and resp.json()["is_active"] is True

    listed = client.get("/api/v1/budgets/", headers=HEADERS).json()
    assert len(listed) == 1 and listed[0]["line_count"] == 0


def test_create_budget_rejects_blank_and_duplicate_names(tmp_path):
    client, _db = _make_client(tmp_path)
    assert (
        client.post(
            "/api/v1/budgets/", json={"name": "   "}, headers=HEADERS
        ).status_code
        == 400
    )
    client.post("/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS)
    dup = client.post("/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS)
    assert dup.status_code == 409


def test_create_budget_can_duplicate_another_ones_lines(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    base = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()
    client.post(
        f"/api/v1/budgets/{base['id']}/lines",
        json={"line_type": "spending", "ref_key": "Groceries", "monthly_amount": 400},
        headers=HEADERS,
    )
    copy = client.post(
        "/api/v1/budgets/",
        json={"name": "Worst case", "copy_from_budget_id": base["id"]},
        headers=HEADERS,
    )
    assert copy.status_code == 201 and copy.json()["line_count"] == 1
    lines = client.get(
        f"/api/v1/budgets/{copy.json()['id']}/lines", headers=HEADERS
    ).json()
    assert lines[0]["ref_key"] == "Groceries" and lines[0]["monthly_amount"] == 400


def test_copy_from_an_unknown_budget_is_404(tmp_path):
    client, _db = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/budgets/",
        json={"name": "Copy", "copy_from_budget_id": 999},
        headers=HEADERS,
    )
    assert resp.status_code == 404


def test_get_update_and_delete_budget(tmp_path):
    client, _db = _make_client(tmp_path)
    budget = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()
    bid = budget["id"]

    got = client.get(f"/api/v1/budgets/{bid}", headers=HEADERS).json()
    assert got["name"] == "Base" and got["lines"] == []

    renamed = client.put(
        f"/api/v1/budgets/{bid}", json={"name": "Plan A"}, headers=HEADERS
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "Plan A"

    assert client.delete(f"/api/v1/budgets/{bid}", headers=HEADERS).status_code == 200
    assert client.get(f"/api/v1/budgets/{bid}", headers=HEADERS).status_code == 404


def test_rename_onto_an_existing_name_is_409(tmp_path):
    client, _db = _make_client(tmp_path)
    client.post("/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS)
    other = client.post(
        "/api/v1/budgets/", json={"name": "Best"}, headers=HEADERS
    ).json()
    resp = client.put(
        f"/api/v1/budgets/{other['id']}", json={"name": "Base"}, headers=HEADERS
    )
    assert resp.status_code == 409
    # Renaming a budget to its own current name is fine.
    assert (
        client.put(
            f"/api/v1/budgets/{other['id']}", json={"name": "Best"}, headers=HEADERS
        ).status_code
        == 200
    )


def test_activate_is_exclusive(tmp_path):
    client, _db = _make_client(tmp_path)
    first = client.post(
        "/api/v1/budgets/", json={"name": "Base", "is_active": True}, headers=HEADERS
    ).json()
    second = client.post(
        "/api/v1/budgets/", json={"name": "Best"}, headers=HEADERS
    ).json()
    client.post(f"/api/v1/budgets/{second['id']}/activate", headers=HEADERS)
    listed = {
        b["id"]: b["is_active"]
        for b in client.get("/api/v1/budgets/", headers=HEADERS).json()
    }
    assert listed[second["id"]] is True and listed[first["id"]] is False


# ── Lines ──────────────────────────────────────────────────────────────────


def test_create_line_returns_a_breadcrumb_label(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    resp = client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={
            "line_type": "spending",
            "ref_key": "Rent",
            "monthly_amount": 900,
            "overrides": {"2026-03": 950},
        },
        headers=HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["label"] == "Spend > Housing > Rent"
    assert resp.json()["overrides"] == {"2026-03": 950.0}


def test_line_rejects_an_unknown_category(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    resp = client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "spending", "ref_key": "Nope", "monthly_amount": 10},
        headers=HEADERS,
    )
    assert (
        resp.status_code == 400 and "Unknown spending category" in resp.json()["detail"]
    )


def test_line_rejects_a_category_from_the_wrong_tree_root(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    resp = client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "spending", "ref_key": "Salary", "monthly_amount": 10},
        headers=HEADERS,
    )
    assert resp.status_code == 400
    assert "an Income category" in resp.json()["detail"]
    assert "must budget one under Spend" in resp.json()["detail"]


def test_line_rejects_an_overlapping_category(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "spending", "ref_key": "Housing", "monthly_amount": 1000},
        headers=HEADERS,
    )
    # A child of an already-budgeted parent would count the same euros twice.
    resp = client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "spending", "ref_key": "Rent", "monthly_amount": 900},
        headers=HEADERS,
    )
    assert resp.status_code == 400 and "overlaps" in resp.json()["detail"]
    # A sibling branch is fine.
    assert (
        client.post(
            f"/api/v1/budgets/{bid}/lines",
            json={
                "line_type": "spending",
                "ref_key": "Groceries",
                "monthly_amount": 400,
            },
            headers=HEADERS,
        ).status_code
        == 201
    )


def test_the_same_category_in_a_different_budget_is_fine(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    first = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    second = client.post(
        "/api/v1/budgets/", json={"name": "Best"}, headers=HEADERS
    ).json()["id"]
    for bid in (first, second):
        assert (
            client.post(
                f"/api/v1/budgets/{bid}/lines",
                json={
                    "line_type": "spending",
                    "ref_key": "Groceries",
                    "monthly_amount": 400,
                },
                headers=HEADERS,
            ).status_code
            == 201
        )


def test_duplicate_line_in_one_budget_is_409(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    body = {"line_type": "spending", "ref_key": "Groceries", "monthly_amount": 400}
    client.post(f"/api/v1/budgets/{bid}/lines", json=body, headers=HEADERS)
    assert (
        client.post(
            f"/api/v1/budgets/{bid}/lines", json=body, headers=HEADERS
        ).status_code
        == 409
    )


def test_investment_line_needs_a_real_portfolio(tmp_path):
    client, db = _make_client(tmp_path)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    assert (
        client.post(
            f"/api/v1/budgets/{bid}/lines",
            json={
                "line_type": "investment",
                "ref_key": "notanid",
                "monthly_amount": 100,
            },
            headers=HEADERS,
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/v1/budgets/{bid}/lines",
            json={"line_type": "investment", "ref_key": "999", "monthly_amount": 100},
            headers=HEADERS,
        ).status_code
        == 400
    )

    broker = db.get_or_create_portfolio("Example Broker")
    resp = client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "investment", "ref_key": str(broker), "monthly_amount": 100},
        headers=HEADERS,
    )
    assert resp.status_code == 201 and resp.json()["label"] == "Example Broker"


def test_update_and_delete_line(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    line = client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "spending", "ref_key": "Groceries", "monthly_amount": 400},
        headers=HEADERS,
    ).json()

    updated = client.put(
        f"/api/v1/budgets/{bid}/lines/{line['id']}",
        json={"monthly_amount": 450, "overrides": {"2026-08": 600}},
        headers=HEADERS,
    ).json()
    assert updated["monthly_amount"] == 450 and updated["overrides"] == {
        "2026-08": 600.0
    }

    assert (
        client.delete(
            f"/api/v1/budgets/{bid}/lines/{line['id']}", headers=HEADERS
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/v1/budgets/{bid}/lines/{line['id']}", headers=HEADERS
        ).status_code
        == 404
    )


def test_a_line_cannot_be_touched_through_the_wrong_budget(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    owner = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    other = client.post(
        "/api/v1/budgets/", json={"name": "Best"}, headers=HEADERS
    ).json()["id"]
    line = client.post(
        f"/api/v1/budgets/{owner}/lines",
        json={"line_type": "spending", "ref_key": "Groceries", "monthly_amount": 400},
        headers=HEADERS,
    ).json()
    assert (
        client.put(
            f"/api/v1/budgets/{other}/lines/{line['id']}",
            json={"monthly_amount": 1},
            headers=HEADERS,
        ).status_code
        == 404
    )


def test_bulk_upsert_creates_and_updates(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "spending", "ref_key": "Groceries", "monthly_amount": 400},
        headers=HEADERS,
    )
    resp = client.post(
        f"/api/v1/budgets/{bid}/lines/bulk",
        json={
            "lines": [
                {
                    "line_type": "spending",
                    "ref_key": "Groceries",
                    "monthly_amount": 450,
                },
                {"line_type": "income", "ref_key": "Salary", "monthly_amount": 3000},
            ]
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200 and resp.json() == {"created": 1, "updated": 1}


def test_bulk_upsert_validates_the_whole_batch_before_writing(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    resp = client.post(
        f"/api/v1/budgets/{bid}/lines/bulk",
        json={
            "lines": [
                {
                    "line_type": "spending",
                    "ref_key": "Groceries",
                    "monthly_amount": 400,
                },
                {"line_type": "spending", "ref_key": "Nope", "monthly_amount": 10},
            ]
        },
        headers=HEADERS,
    )
    assert resp.status_code == 400
    # Nothing was written, so a bad row can't leave the batch half-applied.
    assert client.get(f"/api/v1/budgets/{bid}/lines", headers=HEADERS).json() == []


def test_bulk_upsert_rejects_a_repeated_key_and_an_overlap(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    repeated = client.post(
        f"/api/v1/budgets/{bid}/lines/bulk",
        json={
            "lines": [
                {
                    "line_type": "spending",
                    "ref_key": "Groceries",
                    "monthly_amount": 400,
                },
                {
                    "line_type": "spending",
                    "ref_key": "Groceries",
                    "monthly_amount": 500,
                },
            ]
        },
        headers=HEADERS,
    )
    assert repeated.status_code == 400 and "Duplicate" in repeated.json()["detail"]

    overlapping = client.post(
        f"/api/v1/budgets/{bid}/lines/bulk",
        json={
            "lines": [
                {"line_type": "spending", "ref_key": "Housing", "monthly_amount": 1000},
                {"line_type": "spending", "ref_key": "Rent", "monthly_amount": 900},
            ]
        },
        headers=HEADERS,
    )
    assert overlapping.status_code == 400 and "overlaps" in overlapping.json()["detail"]


def test_bulk_upsert_can_restate_an_existing_line_without_self_conflict(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    body = {
        "lines": [
            {"line_type": "spending", "ref_key": "Housing", "monthly_amount": 1000}
        ]
    }
    assert (
        client.post(
            f"/api/v1/budgets/{bid}/lines/bulk", json=body, headers=HEADERS
        ).status_code
        == 200
    )
    # Saving the grid again must not read the line as overlapping itself.
    again = client.post(f"/api/v1/budgets/{bid}/lines/bulk", json=body, headers=HEADERS)
    assert again.status_code == 200 and again.json() == {"created": 0, "updated": 1}


def test_investment_line_accepts_a_spending_category(tmp_path):
    """The bank-side keying — the only option for an untracked destination."""
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    resp = client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "investment", "ref_key": "Groceries", "monthly_amount": 400},
        headers=HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["label"] == "Spend > Groceries"


def test_a_category_keyed_investment_line_must_live_under_spend(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    resp = client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "investment", "ref_key": "Salary", "monthly_amount": 400},
        headers=HEADERS,
    )
    assert (
        resp.status_code == 400
        and "must budget one under Spend" in resp.json()["detail"]
    )


def test_a_category_keyed_investment_line_shares_the_spend_coverage_space(tmp_path):
    """It reports under Investments but still covers its branch."""
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "investment", "ref_key": "Housing", "monthly_amount": 400},
        headers=HEADERS,
    )
    resp = client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "spending", "ref_key": "Rent", "monthly_amount": 900},
        headers=HEADERS,
    )
    assert resp.status_code == 400 and "overlaps" in resp.json()["detail"]


def test_a_line_can_be_reclassified_in_place(tmp_path):
    """The one-click lever: a mortgage charge is Debt, not spending."""
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    line = client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "spending", "ref_key": "Housing", "monthly_amount": 1000},
        headers=HEADERS,
    ).json()

    for new_type in ("debt", "investment", "spending"):
        resp = client.put(
            f"/api/v1/budgets/{bid}/lines/{line['id']}",
            json={"line_type": new_type},
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["line_type"] == new_type
        # The id survives, so a dismissed action item stays dismissed.
        assert resp.json()["id"] == line["id"]


def test_reclassifying_cannot_break_the_root_invariant(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    income_line = client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "income", "ref_key": "Salary", "monthly_amount": 3000},
        headers=HEADERS,
    ).json()
    resp = client.put(
        f"/api/v1/budgets/{bid}/lines/{income_line['id']}",
        json={"line_type": "spending"},
        headers=HEADERS,
    )
    assert resp.status_code == 400 and "an Income category" in resp.json()["detail"]


def test_reclassifying_a_line_does_not_conflict_with_itself(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    line = client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "spending", "ref_key": "Housing", "monthly_amount": 1000},
        headers=HEADERS,
    ).json()
    # The line's own category must be excluded from its coverage check.
    assert (
        client.put(
            f"/api/v1/budgets/{bid}/lines/{line['id']}",
            json={"line_type": "investment"},
            headers=HEADERS,
        ).status_code
        == 200
    )


# ── Variance & summary ─────────────────────────────────────────────────────


def _seed_activity(db):
    """Two spending rows and one deposit in the current month."""
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    broker = db.get_or_create_portfolio("Example Broker")
    db.create_spending_transaction(
        bank, _this_month("05"), "Rent", -900.0, category="Rent"
    )
    db.create_spending_transaction(
        bank, _this_month("10"), "Supermarket", -500.0, category="Groceries"
    )
    db.create_booking(_this_month("15"), "Deposit", 500.0, "EUR", portfolio_id=broker)
    return bank, broker


def test_variance_reports_planned_actual_and_unbudgeted(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    _seed_activity(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "spending", "ref_key": "Housing", "monthly_amount": 1000},
        headers=HEADERS,
    )
    body = client.get(
        f"/api/v1/budgets/{bid}/variance?months=1", headers=HEADERS
    ).json()

    assert body["months"] == [date.today().strftime("%Y-%m")]
    spending = next(s for s in body["sections"] if s["key"] == "spending")
    assert spending["lines"][0]["actual_total"] == 900.0
    assert spending["lines"][0]["variance_eur"] == 100.0
    assert [u["ref_key"] for u in spending["unbudgeted"]] == ["Groceries"]
    assert spending["actual_total"] == 1400.0


def test_variance_rejects_a_silly_month_count(tmp_path):
    client, _db = _make_client(tmp_path)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    assert (
        client.get(
            f"/api/v1/budgets/{bid}/variance?months=0", headers=HEADERS
        ).status_code
        == 400
    )
    assert (
        client.get(
            f"/api/v1/budgets/{bid}/variance?months=99", headers=HEADERS
        ).status_code
        == 400
    )


def test_variance_accepts_an_explicit_end_month(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    body = client.get(
        f"/api/v1/budgets/{bid}/variance?months=2&end_month=2026-02", headers=HEADERS
    ).json()
    assert body["months"] == ["2026-01", "2026-02"]


def test_variance_for_an_unknown_budget_is_404(tmp_path):
    client, _db = _make_client(tmp_path)
    assert (
        client.get("/api/v1/budgets/999/variance", headers=HEADERS).status_code == 404
    )


def test_summary_returns_the_active_budgets_current_month(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    _seed_activity(db)
    # The route must resolve to /summary, not to /{budget_id} with id="summary".
    assert client.get("/api/v1/budgets/summary", headers=HEADERS).json() is None

    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base", "is_active": True}, headers=HEADERS
    ).json()["id"]
    client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={"line_type": "spending", "ref_key": "Groceries", "monthly_amount": 400},
        headers=HEADERS,
    )
    body = client.get("/api/v1/budgets/summary", headers=HEADERS).json()
    assert body["budget_name"] == "Base" and body["is_active"] is True
    assert body["months"] == [date.today().strftime("%Y-%m")]


def test_seed_proposals_write_nothing(tmp_path):
    client, db = _make_client(tmp_path)
    _seed_tree(db)
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    # Last month, so the current partial month is never the source.
    from portf_manager.services.budget import month_range

    today = date.today()
    year, month = today.year, today.month - 1
    if month == 0:
        month, year = 12, year - 1
    last_month = month_range(f"{year:04d}-{month:02d}", 1)[0]
    db.create_spending_transaction(
        bank, f"{last_month}-10", "Supermarket", -300.0, category="Groceries"
    )

    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    proposals = client.get(
        f"/api/v1/budgets/{bid}/seed-proposals?months=1", headers=HEADERS
    ).json()
    assert {p["ref_key"] for p in proposals} == {"Groceries"}
    assert proposals[0]["monthly_amount"] == 300.0
    assert client.get(f"/api/v1/budgets/{bid}/lines", headers=HEADERS).json() == []


def test_every_budget_endpoint_requires_auth(tmp_path):
    client, _db = _make_client(tmp_path)
    assert client.get("/api/v1/budgets/").status_code in (401, 403)
    assert client.get("/api/v1/budgets/summary").status_code in (401, 403)
    assert client.post("/api/v1/budgets/", json={"name": "X"}).status_code in (401, 403)
