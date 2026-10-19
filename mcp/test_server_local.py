"""Tests for the retirement_readiness and next_euro tools and the networth lines.

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


def test_retirement_readiness_missing_lasts_until_is_na():
    payload = _ok_payload()
    del payload["scenarios"][0]["lasts_until_age"]
    with patch.object(server, "_get", return_value=payload):
        out = server.retirement_readiness()
    assert "Money lasts n/a" in out


def test_retirement_readiness_other_status_default_reason():
    with patch.object(server, "_get", return_value={"status": "insufficient_data"}):
        out = server.retirement_readiness()
    assert out == "Retirement readiness: insufficient_data — see the Net Worth page"


def test_networth_missing_stale_lines_is_na_and_still_lists_stale():
    bs = {
        "totals": {"assets_eur": 1000},
        "groups": [{"lines": [{"label": "Old", "stale": True, "age_days": 12}]}],
    }
    with patch.object(
        server,
        "_get",
        side_effect=lambda p, params=None: (
            bs if "balance-sheet" in p else {"net_worth_eur": 1}
        ),
    ):
        out = server.networth()
    assert "n/a stale line(s)" in out
    assert "0 stale" not in out
    assert "Old — 12 days old" in out


def test_next_euro_prints_summary():
    payload = {"status": "ok", "chat_summary": "NEXT EURO\n  Deployable: 1,000"}
    with patch.object(server, "_get", return_value=payload) as get:
        out = server.next_euro(500.0)
    assert out == "NEXT EURO\n  Deployable: 1,000"
    assert get.call_args.args[0] == "/api/v1/allocate/next-euro"
    assert get.call_args.args[1] == {"amount": 500.0}


def test_next_euro_non_ok_prints_the_breakdown_when_there_is_one():
    payload = {
        "status": "targets_incomplete",
        "reasons": ["no target for etf"],
        "chat_summary": "Allocation targets are incomplete:\n- no target for etf",
    }
    with patch.object(server, "_get", return_value=payload):
        out = server.next_euro()
    assert out == payload["chat_summary"]


def test_next_euro_non_ok_without_summary_is_one_line():
    payload = {"status": "targets_incomplete", "reasons": ["no target for etf", "x"]}
    with patch.object(server, "_get", return_value=payload):
        out = server.next_euro()
    assert out == "Next euro: targets_incomplete — no target for etf; x"
    with patch.object(server, "_get", return_value={"status": "nothing_to_deploy"}):
        assert server.next_euro().endswith("see the Assets page")
    with patch.object(server, "_get", side_effect=RuntimeError("boom")):
        assert server.next_euro() == "Error fetching next euro: boom"


def test_next_euro_missing_summary_is_na():
    with patch.object(server, "_get", return_value={"status": "ok"}) as get:
        assert server.next_euro() == "n/a"
    assert get.call_args.args[1] is None
