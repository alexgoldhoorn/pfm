import pytest
from portf_manager.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def _make_deposit(db, **overrides):
    defaults = dict(
        name="Superdepósito 1 mes",
        portfolio_id=None,
        principal=5000.0,
        currency="EUR",
        interest_rate=4.0,
        start_date="2026-06-12",
        maturity_date="2026-07-12",
        notes=None,
    )
    defaults.update(overrides)
    return db.create_fixed_deposit(**defaults)


def test_create_and_get_deposit(db):
    dep_id = _make_deposit(db)
    dep = db.get_fixed_deposit(dep_id)
    assert dep["name"] == "Superdepósito 1 mes"
    assert dep["principal"] == 5000.0
    assert dep["interest_rate"] == 4.0
    assert dep["status"] == "active"
    assert dep["interest_paid"] is None


def test_list_deposits(db):
    _make_deposit(db, name="Dep A")
    _make_deposit(db, name="Dep B")
    deps = db.get_fixed_deposits()
    assert len(deps) == 2


def test_update_deposit(db):
    dep_id = _make_deposit(db)
    ok = db.update_fixed_deposit(dep_id, status="matured", interest_paid=8.35)
    assert ok is True
    dep = db.get_fixed_deposit(dep_id)
    assert dep["status"] == "matured"
    assert dep["interest_paid"] == 8.35


def test_delete_deposit(db):
    dep_id = _make_deposit(db)
    assert db.delete_fixed_deposit(dep_id) is True
    assert db.get_fixed_deposit(dep_id) is None


def test_get_active_deposits(db):
    _make_deposit(db, name="Active")
    dep_id2 = _make_deposit(db, name="Matured")
    db.update_fixed_deposit(dep_id2, status="matured")
    active = db.get_fixed_deposits(status="active")
    assert len(active) == 1
    assert active[0]["name"] == "Active"
