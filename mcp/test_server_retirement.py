"""Tests for the retirement_readiness tool and the networth balance-sheet lines.

After the branch merges these move into ~/mcp/pfm/test_server.py.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402


def _scenario(age, case, **over):
    base = {
        "retirement_age": age,
        "case": case,
        "verdict": "on_track",
        "lasts_until_age": None,
        "pot_at_retirement_eur": 500000,
        "pot_at_horizon_eur": 100000,
        "sustainable_monthly_eur": 3000,
        "gap_monthly_eur": -200,
        "extra_saving_needed_monthly_eur": 0,
    }
    base.update(over)
    return base


def _ok_payload():
    return {
        "status": "ok",
        "as_of": "2026-10-18",
        "plan": {"retirement_age": 65, "statutory_age": 67, "horizon_age": 95},
        "inputs": {
            "financial_assets_eur": 400000,
            "spending_target_monthly_eur": 2800,
            "spending_months_used": 12,
        },
        "scenarios": [
            _scenario(65, "base"),
            _scenario(65, "pessimistic", verdict="short", lasts_until_age=80),
            _scenario(
                67,
                "base",
                verdict="at_risk",
                lasts_until_age=72,
                gap_monthly_eur=500,
                extra_saving_needed_monthly_eur=None,
            ),
            _scenario(67, "pessimistic", verdict="at_risk", lasts_until_age=70),
        ],
        "warnings": ["One income line is a placeholder."],
    }


def test_retirement_readiness_prints_both_ages():
    with patch.object(server, "_get", return_value=_ok_payload()):
        out = server.retirement_readiness()
    assert "2026-10-18" in out
    assert "12 months" in out
    assert "age 65" in out and "age 67" in out
    assert "ON TRACK" in out and "AT RISK" in out
    assert "past the horizon (age 95)" in out
    assert "surplus" in out and "short by" in out
    assert "not reachable by saving alone" in out
    assert "lasts until age 80" in out
    assert "Warnings:" in out and "placeholder" in out


def test_retirement_readiness_needs_setup_is_one_line():
    payload = {"status": "needs_setup", "scenarios": []}
    with patch.object(server, "_get", return_value=payload):
        out = server.retirement_readiness()
    assert out.count("\n") == 0
    assert out.startswith("Retirement readiness: needs_setup")
    assert "enter a birth date on the Net Worth page" in out


def test_networth_mentions_stale_lines():
    nw = {"net_worth_eur": 100000}
    bs = {
        "totals": {
            "assets_eur": 120000,
            "liabilities_eur": 20000,
            "financial_assets_eur": 70000,
            "liquid_eur": 30000,
            "stale_lines": 2,
        },
        "groups": [
            {
                "key": "bank",
                "lines": [
                    {"label": "Example Bank", "stale": True, "note": "no import"},
                    {"label": "Example Fund", "stale": True, "age_days": 9},
                    {"label": "Fresh", "stale": False},
                ],
            }
        ],
    }

    def fake_get(path, params=None):
        return bs if "balance-sheet" in path else nw

    with patch.object(server, "_get", side_effect=fake_get):
        out = server.networth()
    assert "Balance sheet: assets" in out
    assert "2 stale line(s)" in out
    assert "Example Bank — no import" in out
    assert "Example Fund — 9 days old" in out
    assert "Fresh" not in out

    def failing(path, params=None):
        if "balance-sheet" in path:
            raise RuntimeError("boom")
        return nw

    with patch.object(server, "_get", side_effect=failing):
        out = server.networth()
    assert "NET WORTH" in out
    assert "Balance sheet: unavailable (boom)" in out
