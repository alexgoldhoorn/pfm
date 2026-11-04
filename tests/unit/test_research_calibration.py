"""Calibration on the research lookup and in the valuation prompts."""

import json

import pytest
from httpx import AsyncClient

from portf_manager.services.research import generate_valuation_report
from portf_manager.services.research_scorecard import (
    calibrate_report,
    track_record_text,
)

BLOCK = {
    "n": 12,
    "basis": "at_horizon",
    "direction_hit_rate_pct": 60.0,
    "median_abs_target_error_pct": 15.0,
    "shrink_factor": 0.5,
}
CAL = {"overall": BLOCK, "by_confidence": {}}


class TestCalibrateReport:
    def test_adjusts_fair_value_towards_price(self):
        out = calibrate_report(CAL, "high", price=100.0, fair_value=140.0)
        assert out == {
            "confidence": "high",
            "hit_rate_pct": 60.0,
            "n": 12,
            "shrink_factor": 0.5,
            "adjusted_fair_value": 120.0,
            "basis": "at_horizon",
        }

    def test_confidence_bucket_wins_when_big_enough(self):
        cal = {
            "overall": BLOCK,
            "by_confidence": {"high": {**BLOCK, "n": 6, "shrink_factor": 0.2}},
        }
        assert calibrate_report(cal, "high", 100.0, 150.0)["shrink_factor"] == 0.2
        # a bucket below 5 reports falls back to the overall block
        cal["by_confidence"]["high"]["n"] = 4
        assert calibrate_report(cal, "high", 100.0, 150.0)["shrink_factor"] == 0.5

    def test_no_data_gives_none(self):
        assert calibrate_report(None, "high", 100.0, 140.0) is None
        assert calibrate_report(CAL, "high", 100.0, None) is None
        no_factor = {"overall": {**BLOCK, "shrink_factor": None}}
        assert calibrate_report(no_factor, "high", 100.0, 140.0) is None


def _seed_history(db, aid, count):
    for i in range(count):
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO research_history (asset_id, symbol, generated_at, "
                "source, recommendation, confidence, fair_value, currency, "
                "price_at_report, horizon_months) VALUES (?, 'EXC', ?, 'llm', "
                "'BUY', 'high', 130.0, 'EUR', 100.0, 12)",
                (aid, f"2024-01-{i + 2:02d} 08:00:00"),
            )
            conn.commit()
        db.create_price(aid, 110.0, f"2025-01-{i + 2:02d}")


@pytest.fixture
def lookup_setup(test_database, monkeypatch):
    db = test_database
    aid = db.create_asset("EXC", "Example Calibrated", "stock", currency="EUR")
    db.create_price(aid, 100.0, "2024-01-02")
    db.create_price(aid, 100.0, "2026-11-03")
    monkeypatch.setattr(
        "portf_manager.services.research.fetch_fundamentals", lambda *a, **k: {}
    )
    monkeypatch.setattr(
        "portf_manager.services.research.fetch_recent_news", lambda *a, **k: []
    )
    monkeypatch.setattr("portf_manager.market._fetch_quote_live", lambda s: None)
    return db, aid


class TestLookupCalibration:
    @pytest.mark.asyncio
    async def test_calibration_none_without_report(
        self, async_test_client: AsyncClient, auth_headers, lookup_setup
    ):
        resp = await async_test_client.get(
            "/api/v1/research/EXC/lookup", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["calibration"] is None

    @pytest.mark.asyncio
    async def test_calibration_none_without_history(
        self, async_test_client: AsyncClient, auth_headers, lookup_setup
    ):
        db, aid = lookup_setup
        db.upsert_research_report(
            asset_id=aid,
            symbol="EXC",
            fair_value=140.0,
            recommendation="BUY",
            confidence="high",
            summary="s",
            report_json="{}",
            source="llm",
        )
        resp = await async_test_client.get(
            "/api/v1/research/EXC/lookup", headers=auth_headers
        )
        assert resp.json()["calibration"] is None

    @pytest.mark.asyncio
    async def test_adjusted_fair_value(
        self, async_test_client: AsyncClient, auth_headers, lookup_setup
    ):
        db, aid = lookup_setup
        _seed_history(db, aid, 10)
        db.upsert_research_report(
            asset_id=aid,
            symbol="EXC",
            fair_value=140.0,
            recommendation="BUY",
            confidence="high",
            summary="s",
            report_json="{}",
            source="llm",
        )
        resp = await async_test_client.get(
            "/api/v1/research/EXC/lookup", headers=auth_headers
        )
        cal = resp.json()["calibration"]
        price = resp.json()["current_price"]
        assert set(cal) == {
            "confidence",
            "hit_rate_pct",
            "n",
            "shrink_factor",
            "adjusted_fair_value",
            "basis",
        }
        assert cal["basis"] == "at_horizon" and cal["n"] >= 10
        expected = price + (140.0 - price) * cal["shrink_factor"]
        assert cal["adjusted_fair_value"] == pytest.approx(expected, abs=1e-3)
        assert cal["adjusted_fair_value"] < 140.0


class _FakeLLM:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return json.dumps(
            {"recommendation": "HOLD", "confidence": "low", "summary": "ok"}
        )


class _FakeSearchLLM(_FakeLLM):
    def generate_with_search(self, prompt, symbol):
        self.prompts.append(prompt)
        return json.dumps(
            {
                "text": json.dumps(
                    {"recommendation": "HOLD", "confidence": "low", "summary": "ok"}
                ),
                "sources": [],
            }
        )


def _generate(monkeypatch, llm, **kwargs):
    monkeypatch.setattr(
        "portf_manager.services.research.get_llm_client", lambda **k: llm
    )
    return generate_valuation_report(
        symbol="EXC",
        asset_name="Example",
        asset_type="stock",
        current_price=10.0,
        avg_cost=9.0,
        currency="EUR",
        fundamentals={},
        news=[],
        **kwargs,
    )


class TestPrompts:
    @pytest.mark.parametrize("llm_cls", [_FakeLLM, _FakeSearchLLM])
    def test_track_record_in_prompt(self, monkeypatch, llm_cls):
        llm = llm_cls()
        text = track_record_text(CAL)
        _generate(monkeypatch, llm, track_record=text)
        assert "YOUR TRACK RECORD" in llm.prompts[0]
        assert "last 12 scored reports" in llm.prompts[0]

    @pytest.mark.parametrize("llm_cls", [_FakeLLM, _FakeSearchLLM])
    def test_no_paragraph_without_track_record(self, monkeypatch, llm_cls):
        llm = llm_cls()
        _generate(monkeypatch, llm)
        assert "TRACK RECORD" not in llm.prompts[0]
