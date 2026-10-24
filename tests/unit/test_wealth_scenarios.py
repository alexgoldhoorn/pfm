"""Wealth scenarios service: projection parity with the page, tracking."""

from datetime import date

import pytest

from portf_manager.services import wealth_scenarios as ws

# The same fixture is pinned in web_client/js/tests/wealth_scenarios.test.mjs
# against runProjection(); the expected values below were produced by both.
FIXTURE = dict(
    cash_amount=20000,
    cash_rate_pct=1.5,
    stocks_amount=100000,
    stocks_rate_pct=6,
    stocks_vol_pct=16,
    stocks_contribution_monthly=500,
    bonds_amount=10000,
    bonds_rate_pct=3,
    mortgage_principal=150000,
    mortgage_rate_pct=3,
    mortgage_payment_monthly=900,
    years=20,
    confidence_z=0.674,
)


def _params():
    return ws.normalize_params(FIXTURE)


def test_normalize_fills_defaults_and_drops_unknown_keys():
    p = ws.normalize_params({"stocks_amount": 5000, "bogus": 1})
    assert p["stocks_amount"] == 5000
    assert p["years"] == 30 and p["confidence_z"] == 0.674
    assert "bogus" not in p


@pytest.mark.parametrize(
    "bad",
    [
        {"years": 0},
        {"years": 61},
        {"stocks_vol_pct": 101},
        {"cash_amount": -1},
        {"stocks_rate_pct": "8"},
        {"confidence_z": True},
        {"mortgage_rate_pct": float("nan")},
    ],
)
def test_normalize_rejects_out_of_range_or_non_numeric(bad):
    with pytest.raises(ValueError):
        ws.normalize_params(bad)


def test_params_from_json_corrupt_falls_back_to_defaults():
    assert ws.params_from_json("not json") == ws.DEFAULT_PARAMS
    assert ws.params_from_json(None) == ws.DEFAULT_PARAMS


def test_projection_matches_the_page_fixture():
    r = ws.project(_params())
    p0, p5, p20 = r["points"][0], r["points"][5], r["points"][20]
    assert round(p0["assets"], 2) == 130000.0
    assert round(p0["net_worth"], 2) == -20000.0
    # the band at the origin uses half a year of volatility, like the page
    assert round(p0["net_worth_high"], 2) == -11690.66
    assert round(p5["assets"], 2) == 200783.54
    assert round(p5["mortgage"], 2) == 116060.48
    assert round(p5["net_worth"], 2) == 84723.06
    assert round(p5["net_worth_low"], 2) == 47477.76
    assert round(p20["net_worth"], 2) == 586425.31
    assert round(p20["net_worth_high"], 2) == 926382.39
    assert p20["mortgage"] == 0
    assert r["mortgage_paid_off_year"] == 18
    assert round(r["total_interest_eur"], 2) == 44281.32


def test_payment_below_interest_never_pays_off():
    p = ws.normalize_params(
        {
            "mortgage_principal": 100000,
            "mortgage_rate_pct": 6,
            "mortgage_payment_monthly": 100,
            "years": 10,
        }
    )
    r = ws.project(p)
    assert r["mortgage_paid_off_year"] is None
    assert r["points"][10]["mortgage"] > 100000


def test_no_mortgage_means_no_payoff_and_zero_interest():
    p = ws.normalize_params({"stocks_amount": 1000, "years": 5})
    r = ws.project(p)
    assert r["mortgage_paid_off_year"] is None
    assert r["total_interest_eur"] == 0
    assert all(pt["mortgage"] == 0 for pt in r["points"])


def test_summary_milestones_and_gains():
    s = ws.summarize(_params())
    assert [m["year"] for m in s["milestones"]] == [5, 10, 20]
    assert s["final_net_worth_eur"] == 586425.31
    assert s["mortgage_paid_off_year"] == 18
    assert s["confidence_label"] == "50%"
    # gains = final liquid - starting liquid - 20 years of 500/month
    assert s["investment_gains_eur"] == round(586425.31 - 130000 - 120000, 2)


def test_summary_short_horizon_only_lists_the_horizon():
    s = ws.summarize(ws.normalize_params({"years": 3}))
    assert [m["year"] for m in s["milestones"]] == [3]


def test_point_at_fractional_year_amortises_whole_months():
    p = _params()
    half = ws.point_at(p, 0.5)
    assert half["mortgage"] == ws._mortgage_after(p, 6)[0]
    assert 130000 < half["assets"] < ws.point_at(p, 1.0)["assets"]


# ── tracking ────────────────────────────────────────────────────────────────


def _track(actual, days=365, baseline=500000.0):
    start = date(2026, 1, 1)
    today = date.fromordinal(start.toordinal() + days)
    return ws.track(_params(), start.isoformat(), baseline, actual, today=today)


def test_tracking_on_track_inside_band():
    tr = _track(500000 + 30000)
    assert tr["status"] == "on_track"
    assert tr["days_elapsed"] == 365
    assert tr["expected_low_change_eur"] <= 30000 <= tr["expected_high_change_eur"]
    assert tr["actual_change_eur"] == 30000
    assert tr["gap_eur"] == round(30000 - tr["expected_change_eur"], 2)
    assert tr["gap_pct"] == round(tr["gap_eur"] / 500000 * 100, 2)


def test_tracking_behind_and_ahead():
    assert _track(500000 - 50000)["status"] == "behind"
    assert _track(500000 + 200000)["status"] == "ahead"


def test_tracking_band_is_zero_on_the_baseline_day():
    tr = _track(500000, days=0)
    assert tr["status"] == "too_early"
    assert tr["expected_change_eur"] == 0
    assert tr["expected_low_change_eur"] == 0
    assert tr["expected_high_change_eur"] == 0


def test_tracking_too_early_under_30_days():
    tr = _track(400000, days=10)
    assert tr["status"] == "too_early"
    # the figures are still reported, only the verdict is withheld
    assert tr["actual_change_eur"] == -100000


def test_tracking_without_baseline_or_actual():
    today = date(2026, 6, 1)
    p = _params()
    assert ws.track(p, None, 1.0, 2.0, today)["status"] == "no_baseline"
    tr = ws.track(p, "2026-01-01", None, 2.0, today)
    assert tr["status"] == "no_baseline" and tr["expected_change_eur"] is not None
    assert ws.track(p, "2026-01-01", 1.0, None, today)["status"] == "no_baseline"
    assert ws.track(p, "garbage", 1.0, 2.0, today)["status"] == "no_baseline"


def test_scenario_view_decodes_row(tmp_path):
    import json

    row = {
        "id": 7,
        "name": "Base",
        "description": None,
        "is_primary": 1,
        "params": json.dumps(FIXTURE),
        "baseline_date": "2026-01-01",
        "baseline_net_worth_eur": 500000.0,
        "created_at": "x",
        "updated_at": "y",
    }
    v = ws.scenario_view(row, 530000.0, today=date(2027, 1, 1), with_projection=True)
    assert v["is_primary"] is True
    assert v["params"]["years"] == 20
    assert v["summary"]["final_net_worth_eur"] == 586425.31
    assert v["tracking"]["status"] == "on_track"
    assert len(v["projection"]) == 21 and v["projection"][20]["net_worth"] == 586425.31
