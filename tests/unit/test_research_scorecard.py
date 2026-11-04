"""Research history scorecard: service and router."""

from datetime import date

import pytest
from httpx import AsyncClient

from portf_manager.services.research_scorecard import compute_scorecard

TODAY = date(2026, 11, 4)


def _hist(db, aid, day, rec, fair, price, horizon=12, source="llm", conf="high"):
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO research_history (asset_id, symbol, generated_at, source, "
            "recommendation, confidence, fair_value, currency, price_at_report, "
            "horizon_months) VALUES (?, 'EXM', ?, ?, ?, ?, ?, 'EUR', ?, ?)",
            (aid, f"{day} 08:00:00", source, rec, conf, fair, price, horizon),
        )
        conn.commit()


def _note(db, aid, day, fair, price):
    nid = db.create_research_note(
        asset_id=aid, symbol="EXM", fair_value=fair, price_at_save=price
    )
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE research_notes SET created_at = ? WHERE id = ?",
            (f"{day} 09:00:00", nid),
        )
        conn.commit()


@pytest.fixture
def seeded(test_database):
    db = test_database
    aid = db.create_asset("EXM", "Example Corp", "stock", currency="EUR")
    for day, price in (
        ("2025-01-10", 100.0),
        ("2025-06-01", 130.0),
        ("2026-01-10", 110.0),
        ("2026-06-01", 90.0),
    ):
        db.create_price(aid, price, day)
    _hist(db, aid, "2025-01-10", "BUY", 125.0, 100.0)
    _note(db, aid, "2025-03-01", 150.0, 120.0)
    _hist(db, aid, "2025-07-01", "HOLD", 128.0, 130.0, source="bulk", conf="low")
    _hist(db, aid, "2026-06-01", "BUY", 100.0, 90.0)
    return db


def _by_date(sc):
    return {e["date"]: e for e in sc["entries"]}


def test_horizon_scoring(seeded):
    sc = compute_scorecard(seeded, today=TODAY)
    a = _by_date(sc)["2025-01-10"]
    assert a["implied_return_pct"] == 25.0
    h = a["at_horizon"]
    assert h["date"] == "2026-01-10" and h["price"] == 110.0
    assert h["realised_return_pct"] == 10.0
    assert h["target_error_pct"] == -12.0
    assert h["direction_hit"] is True
    # the 130 close in between is above the 125 fair value
    assert h["reached_target"] is True
    # a HOLD that fell 30% missed
    b = _by_date(sc)["2025-07-01"]["at_horizon"]
    assert b["date"] == "2026-06-01"
    assert b["realised_return_pct"] == -30.77
    assert b["direction_hit"] is False
    assert b["reached_target"] is None


def test_next_report_scoring_and_manual_note(seeded):
    sc = compute_scorecard(seeded, today=TODAY)
    entries = _by_date(sc)
    a = entries["2025-01-10"]["at_next"]
    # the next entry is the manual note, priced 120
    assert a["date"] == "2025-03-01" and a["realised_return_pct"] == 20.0
    assert a["direction_hit"] is True
    assert a["reached_target"] is False
    note = entries["2025-03-01"]
    assert note["source"] == "manual"
    # 150 vs 120 is more than 10% up
    assert note["recommendation"] == "BUY"
    assert note["at_horizon"]["realised_return_pct"] == -8.33
    assert note["at_horizon"]["direction_hit"] is False
    assert note["at_next"]["price"] == 130.0
    # the last entry of a symbol has no next point
    assert entries["2026-06-01"]["at_next"] is None


def test_pending_entry_shows_now(seeded):
    sc = compute_scorecard(seeded, today=TODAY)
    last = _by_date(sc)["2026-06-01"]
    assert last["at_horizon"] is None
    assert last["now"]["price"] == 90.0
    assert last["now"]["realised_return_pct"] == 0.0
    assert sc["pending"] == 1 and sc["total"] == 4


def test_manual_note_recommendation_bands(test_database):
    db = test_database
    aid = db.create_asset("EXM", "Example Corp", "stock", currency="EUR")
    _note(db, aid, "2025-01-01", 105.0, 100.0)
    _note(db, aid, "2025-02-01", 80.0, 100.0)
    _note(db, aid, "2025-03-01", 100.0, None)
    recs = {
        e["date"]: e["recommendation"]
        for e in compute_scorecard(db, today=TODAY)["entries"]
    }
    assert recs == {"2025-01-01": "HOLD", "2025-02-01": "SELL", "2025-03-01": None}


def test_aggregates_and_breakdowns(seeded):
    sc = compute_scorecard(seeded, today=TODAY)
    hz = sc["totals"]["at_horizon"]
    assert hz["n"] == 3
    assert hz["direction_hit_rate_pct"] == 33.3
    assert hz["mean_realised_return_pct"] == pytest.approx(-9.7, abs=0.05)
    assert hz["median_abs_target_error_pct"] == 26.7
    assert hz["reached_target_rate_pct"] == 50.0
    nx = sc["totals"]["at_next"]
    assert nx["n"] == 3 and nx["direction_hit_rate_pct"] == 66.7
    assert set(sc["by_source"]) == {"llm", "manual", "bulk"}
    assert set(sc["by_recommendation"]) == {"BUY", "HOLD"}
    assert set(sc["by_confidence"]) == {"high", "low"}
    assert set(sc["by_asset_type"]) == {"stock"}
    # best / worst rank by the horizon return
    assert sc["best"][0]["date"] == "2025-01-10"
    assert sc["worst"][0]["date"] == "2025-07-01"
    assert sc["method"]


def test_symbol_filter_and_empty(seeded):
    assert compute_scorecard(seeded, symbol="nope", today=TODAY)["total"] == 0
    assert compute_scorecard(seeded, symbol="exm", today=TODAY)["total"] == 4
    empty = compute_scorecard(seeded, symbol="nope", today=TODAY)
    assert empty["totals"]["at_horizon"]["n"] == 0
    assert empty["best"] == [] and empty["entries"] == []


def test_upsert_appends_history(test_database):
    db = test_database
    aid = db.create_asset("EXM", "Example Corp", "stock", currency="EUR")
    db.create_price(aid, 100.0, "2026-01-01")
    for source in ("llm", "bulk"):
        db.upsert_research_report(
            aid,
            "EXM",
            120.0,
            "BUY",
            "high",
            "s",
            "{}",
            source=source,
            buy_below=90.0,
            sell_above=130.0,
            horizon_months=18,
        )
    rows = db.get_research_history(asset_id=aid)
    assert [r["source"] for r in rows] == ["llm", "bulk"]
    assert rows[0]["price_at_report"] == 100.0
    assert rows[0]["currency"] == "EUR"
    assert rows[0]["buy_below"] == 90.0 and rows[0]["horizon_months"] == 18


def test_horizon_parsing():
    from portf_manager.services.research import _parse_horizon

    assert _parse_horizon(None) == 12
    assert _parse_horizon("abc") == 12
    assert _parse_horizon(3) == 6
    assert _parse_horizon(99) == 36
    assert _parse_horizon(24.0) == 24


class TestScorecardApi:
    @pytest.mark.asyncio
    async def test_scorecard_before_symbol_route(
        self, async_test_client: AsyncClient, auth_headers, seeded
    ):
        resp = await async_test_client.get(
            "/api/v1/research/scorecard", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 4 and "totals" in body
        resp = await async_test_client.get(
            "/api/v1/research/scorecard?symbol=exm", headers=auth_headers
        )
        assert resp.json()["total"] == 4

    @pytest.mark.asyncio
    async def test_history_newest_first(
        self, async_test_client: AsyncClient, auth_headers, seeded
    ):
        resp = await async_test_client.get(
            "/api/v1/research/history/exm", headers=auth_headers
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert [r["generated_at"][:10] for r in rows] == [
            "2026-06-01",
            "2025-07-01",
            "2025-03-01",
            "2025-01-10",
        ]
        assert {r["source"] for r in rows} == {"llm", "bulk", "manual"}


class TestCalibration:
    def test_small_history_uses_next_basis(self, seeded):
        from portf_manager.services.research_scorecard import calibration

        cal = calibration(seeded, today=TODAY)
        overall = cal["overall"]
        assert overall["basis"] == "at_next"
        assert (
            overall["n"]
            == compute_scorecard(seeded, today=TODAY)["totals"]["at_next"]["n"]
        )
        assert set(cal["by_confidence"]) == {"high", "low"}

    def test_shrink_factor_is_clamped_ratio(self):
        from portf_manager.services.research_scorecard import _calibration_block

        def both(n, implied, realised):
            agg = {
                "n": n,
                "direction_hit_rate_pct": 60.0,
                "median_abs_target_error_pct": 12.0,
                "mean_implied_return_pct": implied,
                "mean_realised_return_pct": realised,
            }
            return {"at_horizon": agg, "at_next": agg}

        assert _calibration_block(both(12, 20.0, 10.0))["shrink_factor"] == 0.5
        assert _calibration_block(both(12, 20.0, 10.0))["implied_vs_realised"] == 2.0
        # realised beyond implied is capped at 1.0, a tiny one floored at 0.1
        assert _calibration_block(both(12, 10.0, 30.0))["shrink_factor"] == 1.0
        assert _calibration_block(both(12, 50.0, 1.0))["shrink_factor"] == 0.1
        # no positive realised return: no factor and no ratio
        blank = _calibration_block(both(12, 20.0, -5.0))
        assert blank["shrink_factor"] is None
        assert blank["implied_vs_realised"] is None
        assert _calibration_block(both(12, None, 5.0))["shrink_factor"] is None

    def test_horizon_basis_from_ten_scored_reports(self, test_database):
        from portf_manager.services.research_scorecard import calibration

        db = test_database
        aid = db.create_asset("EXM", "Example Corp", "stock", currency="EUR")
        db.create_price(aid, 100.0, "2024-01-02")
        db.create_price(aid, 110.0, "2025-01-02")
        for i in range(10):
            _hist(db, aid, f"2024-01-{i + 2:02d}", "BUY", 130.0, 100.0)
            db.create_price(aid, 110.0, f"2025-01-{i + 2:02d}")
        overall = calibration(db, today=TODAY)["overall"]
        assert overall["basis"] == "at_horizon" and overall["n"] == 10
        assert overall["direction_hit_rate_pct"] == 100.0
        assert overall["shrink_factor"] == pytest.approx(0.333, abs=0.001)

    def test_result_is_cached(self, seeded):
        from portf_manager.services.research_scorecard import calibration

        first = calibration(seeded, today=TODAY)
        with seeded.get_connection() as conn:
            conn.execute("DELETE FROM research_history")
            conn.commit()
        assert calibration(seeded, today=TODAY) == first

    def test_track_record_text_needs_ten_reports(self):
        from portf_manager.services.research_scorecard import track_record_text

        block = {
            "n": 12,
            "direction_hit_rate_pct": 58.3,
            "median_abs_target_error_pct": 14.2,
        }
        text = track_record_text({"overall": block})
        assert "last 12 scored reports" in text and "58%" in text and "14%" in text
        assert track_record_text({"overall": {**block, "n": 9}}) is None
        assert track_record_text(None) is None
        assert (
            track_record_text(
                {"overall": {**block, "n": 11, "direction_hit_rate_pct": None}}
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_scorecard_endpoint_carries_calibration(
        self, async_test_client: AsyncClient, auth_headers, seeded
    ):
        resp = await async_test_client.get(
            "/api/v1/research/scorecard?symbol=exm", headers=auth_headers
        )
        cal = resp.json()["calibration"]
        assert set(cal) == {"as_of", "overall", "by_confidence"}
