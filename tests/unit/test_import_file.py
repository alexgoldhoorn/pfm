"""POST /imports/import-file: parse, de-duplicate and save a broker file in one call."""

import io

import pytest
from fastapi.testclient import TestClient

from portf_manager.database import Database

_KEY = "test-key-broker-import-file-abc123"
HEADERS = {"X-API-Key": _KEY}

GENERIC = (
    "date,symbol,name,type,quantity,price,currency,fees,asset_type\n"
    "2026-01-05,EXCO,Example Corp,buy,10,100,EUR,1,stock\n"
    "2026-01-06,EXCO,Example Corp,buy,5,110,EUR,0,stock\n"
    "2026-02-01,EXFD,Example Fund,buy,2,50,EUR,0,etf\n"
)

INDEXA = (
    '"Fecha valor";"Fecha operación";Inversión;"Código ISIN";Tipo;'
    'Participaciones;Importe;Retenciones;"Resultado fiscal"\n'
    '"05/01/2026";"2026-01-05";"Example Global Fund";LU0000000001;SUSCRIPCIÓN;'
    '10,000000;"1.000,00 €";"0,00 €";"0,00 €"\n'
    '"06/02/2026";"2026-02-06";"Example Bond Fund";ES0000000001;SUSCRIPCIÓN;'
    '5,000000;"500,00 €";"0,00 €";"0,00 €"\n'
)

COINBASE = (
    "Transactions\nuser@example.com\n"
    "Timestamp,Transaction Type,Asset,Quantity Transacted,"
    "Price Currency,Price at Transaction,"
    "Total (inclusive of fees and/or spread),Notes\n"
    "2026-03-01 10:00:00 UTC,Deposit,EUR,500,EUR,,500,sepa in\n"
    "2026-03-02 10:00:00 UTC,Buy,BTC,0.001,EUR,60000,60,bought\n"
)

# The stored deposit (2026-03-01) is described one day later here, inside the
# span the file covers: a near match, not an exact one
COINBASE_NEAR = (
    "Transactions\nuser@example.com\n"
    "Timestamp,Transaction Type,Asset,Quantity Transacted,"
    "Price Currency,Price at Transaction,"
    "Total (inclusive of fees and/or spread),Notes\n"
    "2026-03-01 09:00:00 UTC,Withdrawal,EUR,200,EUR,,200,withdrew\n"
    "2026-03-02 10:00:00 UTC,Deposit,EUR,500,EUR,,500,sepa in\n"
)


def _make_client(tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    db = Database(str(tmp_path / "import_file.db"))
    km = APIKeyManager(db)
    km.create_api_key(key_name="test", description="test key", raw_key=_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km
    return TestClient(app), db


@pytest.fixture(autouse=True)
def _clear_overrides(monkeypatch):
    # the GBX check would ask Yahoo about every new symbol
    monkeypatch.setattr("portf_manager.currency_utils.is_gbx", lambda *a, **k: False)
    yield
    from portf_server.app import app

    app.dependency_overrides.clear()


def _post(client, pid, text, name="s.csv", fmt=None):
    data = {"portfolio_id": str(pid)}
    if fmt:
        data["format"] = fmt
    return client.post(
        "/api/v1/import/import-file",
        data=data,
        files={"file": (name, io.BytesIO(text.encode()), "text/csv")},
        headers=HEADERS,
    )


def _counts(db):
    with db.get_connection() as conn:
        return tuple(
            conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("transactions", "bookings", "assets")
        )


def test_generic_csv_is_detected_and_saved(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Broker")

    r = _post(client, pid, GENERIC)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "portfolio_id": pid,
        "portfolio_name": "Example Broker",
        "format": "generic",
        "parsed": 3,
        "imported": 3,
        "duplicates_skipped": 0,
        "bookings_to_review": 0,
        "bookings_imported": 0,
        "new_assets": 2,
        "first_date": "2026-01-05",
        "last_date": "2026-02-01",
        "warnings": [],
    }
    assert _counts(db) == (3, 0, 2)
    # every row went to the chosen portfolio
    with db.get_connection() as conn:
        pids = {r[0] for r in conn.execute("SELECT portfolio_id FROM transactions")}
    assert pids == {pid}


def test_indexa_csv_is_detected_by_its_header(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Some Account")

    r = _post(client, pid, INDEXA)
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["format"], d["parsed"], d["imported"], d["new_assets"]) == (
        "indexacapital",
        2,
        2,
        2,
    )
    assert d["first_date"] == "2026-01-05"
    assert d["last_date"] == "2026-02-06"
    # the parser's asset type is stored
    assert db.get_asset_by_symbol("LU0000000001")["asset_type"] == "mutual_fund"


def test_coinbase_saves_trade_and_settled_booking(tmp_path, monkeypatch):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Exchange")
    settled = []
    monkeypatch.setattr(
        "portf_server.routers.imports.settle_after_bookings",
        lambda d: settled.append(1),
    )

    r = _post(client, pid, COINBASE)
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["format"], d["imported"], d["bookings_imported"]) == ("coinbase", 1, 1)
    assert _counts(db) == (1, 1, 1)
    assert settled == [1]


def test_second_import_of_the_same_file_imports_nothing(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Exchange")
    assert _post(client, pid, COINBASE).status_code == 200
    before = _counts(db)

    r = _post(client, pid, COINBASE)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["parsed"] == 2
    assert d["imported"] == 0
    assert d["bookings_imported"] == 0
    assert d["duplicates_skipped"] == 2
    assert d["bookings_to_review"] == 0
    assert d["first_date"] is None and d["last_date"] is None
    assert _counts(db) == before


def test_near_date_booking_is_left_out_and_counted_for_review(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Exchange")
    db.create_booking("2026-03-01", "Deposit", 500.0, "EUR", portfolio_id=pid)

    r = _post(client, pid, COINBASE_NEAR)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "possible_duplicates" not in d
    assert d["bookings_to_review"] == 1
    assert d["bookings_imported"] == 1
    assert d["duplicates_skipped"] == 0
    assert any(
        "web Import page" in w and w.startswith("1 booking") for w in d["warnings"]
    )
    # the stored deposit, plus the withdrawal; the near deposit is not saved
    assert _counts(db)[1] == 2


def test_bank_portfolio_is_refused(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")

    r = _post(client, pid, GENERIC)
    assert r.status_code == 400
    assert "not a brokerage" in r.json()["detail"]
    assert _counts(db) == (0, 0, 0)


def test_unknown_portfolio_is_400(tmp_path):
    client, _db = _make_client(tmp_path)
    assert _post(client, 999, GENERIC).status_code == 400


def test_unknown_format_is_400_and_writes_nothing(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Broker")

    r = _post(client, pid, "foo,bar\n1,2\n")
    assert r.status_code == 400
    assert "Looked for" in r.json()["detail"]
    r = _post(client, pid, GENERIC, fmt="nonsense")
    assert r.status_code == 400
    assert _counts(db) == (0, 0, 0)


def test_broker_name_is_the_fallback_when_the_header_matches_nothing(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("My Coinbase")

    # not a recognisable header, but the portfolio names the broker: the parser
    # runs and finds no rows
    r = _post(client, pid, "a,b\n1,2\n3,4\n")
    assert r.status_code == 400
    assert "format coinbase" in r.json()["detail"]


def test_explicit_format_overrides_detection(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Broker")
    r = _post(client, pid, GENERIC, fmt="GENERIC")
    assert r.status_code == 200
    assert r.json()["format"] == "generic"


def test_empty_parse_is_400(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Broker")
    header_only = "date,symbol,type,quantity,price,currency\n"
    r = _post(client, pid, header_only)
    assert r.status_code == 400
    assert "No rows" in r.json()["detail"]


def test_failure_mid_save_leaves_the_database_as_before(tmp_path, monkeypatch):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Exchange")
    # a stored asset whose type the import would correct, to prove it is restored
    db.create_asset("EXCO", "Example Corp", "etf", currency="EUR")
    before = _counts(db)
    real = db.create_transaction
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(db, "create_transaction", flaky)

    r = _post(client, pid, GENERIC)
    assert r.status_code == 500
    assert "nothing was stored" in r.json()["detail"]
    assert "disk full" in r.json()["detail"]
    assert _counts(db) == before
    assert db.get_asset_by_symbol("EXCO")["asset_type"] == "etf"
    assert db.get_asset_by_symbol("EXFD") is None


def test_failure_on_a_booking_removes_the_trades_too(tmp_path, monkeypatch):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Exchange")
    monkeypatch.setattr(
        db, "create_booking", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    r = _post(client, pid, COINBASE)
    assert r.status_code == 500
    assert _counts(db) == (0, 0, 0)


def test_settle_failure_keeps_the_rows_and_warns(tmp_path, monkeypatch):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Exchange")

    def broken(_db):
        raise RuntimeError("no luck")

    monkeypatch.setattr("portf_server.routers.imports.settle_after_bookings", broken)

    r = _post(client, pid, COINBASE)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["bookings_imported"] == 1
    assert any("not matched" in w and "no luck" in w for w in d["warnings"])
    assert _counts(db)[1] == 1


def test_response_carries_no_symbols_or_amounts(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Broker")
    text = _post(client, pid, GENERIC).text
    assert "EXCO" not in text and "Example Corp" not in text
