"""Database-layer tests for the budgets / budget_lines tables (schema v29)."""

import pytest

from portf_manager.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def test_create_and_get_budget(db):
    budget_id = db.create_budget("Base", "the baseline plan")
    budget = db.get_budget(budget_id)
    assert budget["name"] == "Base"
    assert budget["description"] == "the baseline plan"
    assert budget["is_active"] == 0
    assert db.get_budget_by_name("Base")["id"] == budget_id
    assert db.get_budget(999) is None


def test_list_budgets_reports_line_counts_and_puts_active_first(db):
    quiet = db.create_budget("Worst case")
    active = db.create_budget("Base", is_active=True)
    db.create_budget_line(active, "spending", "Groceries", 400.0)
    db.create_budget_line(active, "income", "Salary", 3000.0)

    listed = db.list_budgets()
    assert [b["name"] for b in listed] == ["Base", "Worst case"]
    assert listed[0]["line_count"] == 2
    assert listed[1]["line_count"] == 0
    assert quiet == listed[1]["id"]


def test_only_one_budget_is_ever_active(db):
    first = db.create_budget("Base", is_active=True)
    second = db.create_budget("Best case", is_active=True)
    assert db.get_active_budget()["id"] == second
    assert db.get_budget(first)["is_active"] == 0

    db.set_active_budget(first)
    assert db.get_active_budget()["id"] == first
    assert db.get_budget(second)["is_active"] == 0


def test_set_active_budget_rejects_an_unknown_id(db):
    db.create_budget("Base", is_active=True)
    assert db.set_active_budget(999) is False
    # The existing active budget is untouched by the failed call.
    assert db.get_active_budget()["name"] == "Base"


def test_get_active_budget_is_none_when_nothing_is_flagged(db):
    db.create_budget("Base")
    assert db.get_active_budget() is None


def test_update_budget_whitelists_fields(db):
    budget_id = db.create_budget("Base")
    assert db.update_budget(budget_id, name="Renamed", is_active=1) is True
    budget = db.get_budget(budget_id)
    assert budget["name"] == "Renamed"
    # is_active is not in the whitelist — only set_active_budget moves it.
    assert budget["is_active"] == 0
    assert db.update_budget(budget_id) is False


def test_budget_names_are_unique(db):
    db.create_budget("Base")
    with pytest.raises(Exception):
        db.create_budget("Base")


def test_create_and_list_lines(db):
    budget_id = db.create_budget("Base")
    line_id = db.create_budget_line(
        budget_id, "spending", "Groceries", 400.0, '{"2026-03": 550.0}', None, "note"
    )
    lines = db.list_budget_lines(budget_id)
    assert len(lines) == 1
    assert lines[0]["id"] == line_id
    # overrides come back as raw JSON text — parsing belongs to the service.
    assert lines[0]["overrides"] == '{"2026-03": 550.0}'
    assert lines[0]["notes"] == "note"
    assert db.get_budget_line(line_id)["ref_key"] == "Groceries"
    assert db.find_budget_line(budget_id, "spending", "Groceries")["id"] == line_id
    assert db.find_budget_line(budget_id, "income", "Groceries") is None


def test_line_type_is_constrained(db):
    budget_id = db.create_budget("Base")
    with pytest.raises(Exception):
        db.create_budget_line(budget_id, "nonsense", "Groceries", 400.0)


def test_a_budget_cannot_hold_the_same_line_twice(db):
    budget_id = db.create_budget("Base")
    db.create_budget_line(budget_id, "spending", "Groceries", 400.0)
    with pytest.raises(Exception):
        db.create_budget_line(budget_id, "spending", "Groceries", 500.0)
    # ...but a different budget can budget the same category.
    other = db.create_budget("Best case")
    assert db.create_budget_line(other, "spending", "Groceries", 300.0)


def test_update_and_delete_line(db):
    budget_id = db.create_budget("Base")
    line_id = db.create_budget_line(budget_id, "spending", "Groceries", 400.0)
    assert db.update_budget_line(line_id, monthly_amount=450.0, notes="up") is True
    line = db.get_budget_line(line_id)
    assert line["monthly_amount"] == 450.0 and line["notes"] == "up"
    assert db.update_budget_line(line_id, bogus_field=1) is False
    assert db.delete_budget_line(line_id) is True
    assert db.get_budget_line(line_id) is None
    assert db.delete_budget_line(line_id) is False


def test_deleting_a_budget_takes_its_lines_with_it(db):
    budget_id = db.create_budget("Base")
    line_id = db.create_budget_line(budget_id, "spending", "Groceries", 400.0)
    assert db.delete_budget(budget_id) is True
    assert db.get_budget_line(line_id) is None
    assert db.delete_budget(budget_id) is False


def test_upsert_updates_in_place_and_keeps_line_ids(db):
    budget_id = db.create_budget("Base")
    line_id = db.create_budget_line(budget_id, "spending", "Groceries", 400.0)
    result = db.upsert_budget_lines(
        budget_id,
        [
            {"line_type": "spending", "ref_key": "Groceries", "monthly_amount": 450.0},
            {"line_type": "income", "ref_key": "Salary", "monthly_amount": 3000.0},
        ],
    )
    assert result == {"created": 1, "updated": 1}
    # The existing line keeps its id, so a dismissed action item stays dismissed.
    assert db.get_budget_line(line_id)["monthly_amount"] == 450.0
    assert len(db.list_budget_lines(budget_id)) == 2


def test_line_changes_bump_the_budgets_updated_at(db):
    budget_id = db.create_budget("Base")
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE budgets SET updated_at = '2020-01-01 00:00:00' WHERE id = ?",
            (budget_id,),
        )
        conn.commit()
    db.create_budget_line(budget_id, "spending", "Groceries", 400.0)
    assert db.get_budget(budget_id)["updated_at"] != "2020-01-01 00:00:00"
