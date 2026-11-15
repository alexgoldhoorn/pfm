"""A budget debt line can link to a manual liability or to a loan record."""

from datetime import date

from tests.unit.test_budgets_api import HEADERS, _make_client, _seed_tree

LOAN = {
    "name": "Example Mortgage",
    "principal": 200000,
    "annual_rate_pct": 3.0,
    "start_date": "2020-01-01",
    "term_months": 300,
}


def _debt_line(client, db, **link):
    _seed_tree(db)
    bid = client.post(
        "/api/v1/budgets/", json={"name": "Base"}, headers=HEADERS
    ).json()["id"]
    line = client.post(
        f"/api/v1/budgets/{bid}/lines",
        json={
            "line_type": "debt",
            "ref_key": "Housing",
            "monthly_amount": 1000,
            **link,
        },
        headers=HEADERS,
    ).json()
    return bid, line


def _variance_line(client, bid):
    debt = next(
        s
        for s in client.get(
            f"/api/v1/budgets/{bid}/variance?months=1", headers=HEADERS
        ).json()["sections"]
        if s["key"] == "debt"
    )
    return debt["lines"][0]


def test_manual_link_is_the_default(tmp_path):
    client, db = _make_client(tmp_path)
    liability = db.create_manual_asset(
        name="Example Loan", category="personal_loan", amount=5000.0, is_liability=True
    )
    bid, line = _debt_line(client, db, link_id=liability)
    assert line["link_type"] == "manual"
    got = _variance_line(client, bid)
    assert got["link_label"] == "Example Loan"
    assert got["link_amount_eur"] == 5000.0
    assert got["link_broken"] is False
    assert got["link_principal_eur"] is None


def test_loan_link_shows_balance_and_this_months_split(tmp_path):
    client, db = _make_client(tmp_path)
    loan_id = client.post("/api/v1/networth/loans", json=LOAN, headers=HEADERS).json()[
        "id"
    ]
    bid, line = _debt_line(client, db, link_id=loan_id, link_type="loan")
    assert line["link_type"] == "loan"
    got = _variance_line(client, bid)
    assert got["link_label"] == "Example Mortgage"
    assert 0 < got["link_amount_eur"] < 200000
    assert got["link_broken"] is False
    # one payment falls in every month of the term, interest + principal = it
    loan = db.get_loan(loan_id)
    from portf_manager.services import loans

    assert (
        round(got["link_principal_eur"] + got["link_interest_eur"], 2)
        == loans.payment_split(loan, date.today().strftime("%Y-%m"))["payment"]
    )
    # display only: the balance never reaches a total
    assert got["planned_eur"] and got["actual_total"] == 0.0


def test_changing_the_link_type_via_put(tmp_path):
    client, db = _make_client(tmp_path)
    loan_id = client.post("/api/v1/networth/loans", json=LOAN, headers=HEADERS).json()[
        "id"
    ]
    bid, line = _debt_line(client, db)
    r = client.put(
        f"/api/v1/budgets/{bid}/lines/{line['id']}",
        json={"link_id": loan_id, "link_type": "loan"},
        headers=HEADERS,
    )
    assert r.json()["link_type"] == "loan"
    # clearing the link resets the type
    r = client.put(
        f"/api/v1/budgets/{bid}/lines/{line['id']}",
        json={"link_id": 0},
        headers=HEADERS,
    )
    assert r.json()["link_id"] is None and r.json()["link_type"] == "manual"


def test_bulk_save_keeps_and_sets_link_type(tmp_path):
    client, db = _make_client(tmp_path)
    loan_id = client.post("/api/v1/networth/loans", json=LOAN, headers=HEADERS).json()[
        "id"
    ]
    bid, line = _debt_line(client, db, link_id=loan_id, link_type="loan")
    body = {
        "lines": [
            {
                "line_type": "debt",
                "ref_key": "Housing",
                "monthly_amount": 1100,
                "link_id": loan_id,
            }
        ]
    }
    client.post(f"/api/v1/budgets/{bid}/lines/bulk", json=body, headers=HEADERS)
    assert db.get_budget_line(line["id"])["link_type"] == "loan"


def test_broken_loan_link_does_not_error(tmp_path):
    client, db = _make_client(tmp_path)
    loan_id = client.post("/api/v1/networth/loans", json=LOAN, headers=HEADERS).json()[
        "id"
    ]
    bid, _ = _debt_line(client, db, link_id=loan_id, link_type="loan")
    client.delete(f"/api/v1/networth/loans/{loan_id}", headers=HEADERS)
    got = _variance_line(client, bid)
    assert got["link_broken"] is True
    assert got["link_label"] is None and got["link_amount_eur"] is None
    assert got["link_id"] == loan_id


def test_broken_manual_link_is_flagged(tmp_path):
    client, db = _make_client(tmp_path)
    bid, _ = _debt_line(client, db, link_id=999)
    got = _variance_line(client, bid)
    assert got["link_broken"] is True and got["link_amount_eur"] is None


def test_unlinked_line_is_not_broken(tmp_path):
    client, db = _make_client(tmp_path)
    bid, _ = _debt_line(client, db)
    assert _variance_line(client, bid)["link_broken"] is False
