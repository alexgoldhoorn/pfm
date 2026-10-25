"""Unit tests for portf_manager.services.goals — pure goal arithmetic."""

from datetime import date

import pytest

from portf_manager.services.goals import (
    goal_progress,
    goal_status,
    goals_summary,
    months_between,
    project_value,
    required_monthly,
)

TODAY = date(2026, 10, 24)


def _goal(**over):
    base = {
        "id": 1,
        "name": "Example Fund",
        "target_amount_eur": 12_000.0,
        "target_date": "2027-10-24",
        "monthly_contribution_eur": 500.0,
        "expected_return_pct": 0.0,
        "start_networth_eur": 4_000.0,
        "notes": None,
        "created_at": "2026-04-24 10:00:00",
    }
    base.update(over)
    return base


class TestArithmetic:
    def test_project_value_without_return_is_linear(self):
        assert project_value(1000, 100, 0, 12) == 2200

    def test_project_value_compounds(self):
        fv = project_value(1000, 0, 12, 12)
        assert fv == pytest.approx(1000 * 1.01**12)

    def test_required_monthly_zero_when_already_there(self):
        assert required_monthly(2000, 1000, 5, 12) == 0

    def test_required_monthly_none_when_no_time_left(self):
        assert required_monthly(0, 1000, 5, 0) is None

    def test_required_monthly_linear_without_return(self):
        assert required_monthly(0, 1200, 0, 12) == 100

    def test_required_monthly_reaches_target(self):
        need = required_monthly(1000, 10_000, 6, 36)
        assert project_value(1000, need, 6, 36) == pytest.approx(10_000, abs=0.5)

    def test_months_between_floors_at_zero(self):
        assert months_between(date(2026, 10, 24), date(2026, 1, 1)) == 0
        assert months_between(date(2026, 10, 24), date(2027, 1, 1)) == 3


class TestStatus:
    def test_achieved_beats_overdue(self):
        assert goal_status(100, 50, False, date(2020, 1, 1), TODAY) == "achieved"

    def test_overdue_when_date_passed_and_unmet(self):
        assert goal_status(10, 50, False, date(2026, 10, 23), TODAY) == "overdue"

    def test_projection_decides_otherwise(self):
        assert goal_status(10, 50, True, date(2027, 1, 1), TODAY) == "on_track"
        assert goal_status(10, 50, False, date(2027, 1, 1), TODAY) == "off_track"


class TestGoalProgress:
    def test_on_track_with_pace_ahead(self):
        g = goal_progress(_goal(), current_nw=7_000, today=TODAY)
        assert g["months_left"] == 12
        assert g["projected_value_eur"] == 13_000
        assert g["on_track"] is True
        assert g["status"] == "on_track"
        assert g["required_monthly_eur"] == pytest.approx(416.67)
        assert g["start_date"] == "2026-04-24"
        assert g["months_elapsed"] == 6.0
        assert g["gained_since_start_eur"] == 3_000
        # 3000 of the 8000 gap closed
        assert g["gap_closed_pct"] == 37.5
        assert g["actual_monthly_eur"] == 500
        assert g["pace"] == "ahead"

    def test_behind_pace_when_actual_below_required(self):
        g = goal_progress(_goal(monthly_contribution_eur=100), 4_300, TODAY)
        assert g["status"] == "off_track"
        assert g["shortfall_eur"] == 12_000 - (4_300 + 1_200)
        assert g["actual_monthly_eur"] == 50
        assert g["pace"] == "behind"

    def test_no_baseline_means_no_pace_fields(self):
        g = goal_progress(_goal(start_networth_eur=None), 7_000, TODAY)
        assert g["gained_since_start_eur"] is None
        assert g["gap_closed_pct"] is None
        assert g["actual_monthly_eur"] is None
        assert g["pace"] is None
        assert g["status"] == "on_track"

    def test_young_goal_has_gain_but_no_pace(self):
        g = goal_progress(_goal(created_at="2026-10-20"), 4_100, TODAY)
        assert g["gained_since_start_eur"] == 100
        assert g["months_elapsed"] == 0.1
        assert g["actual_monthly_eur"] is None
        assert g["pace"] is None

    def test_achieved_goal(self):
        g = goal_progress(_goal(), 12_500, TODAY)
        assert g["status"] == "achieved"
        assert g["progress_pct"] == 104.2
        assert g["gap_closed_pct"] == 100
        assert g["required_monthly_eur"] == 0
        assert g["pace"] == "ahead"

    def test_overdue_goal(self):
        g = goal_progress(_goal(target_date="2026-01-01"), 5_000, TODAY)
        assert g["status"] == "overdue"
        assert g["months_left"] == 0
        assert g["required_monthly_eur"] is None
        assert g["projected_value_eur"] == 5_000
        # pace is still reported: it says how the saving went
        assert g["actual_monthly_eur"] == pytest.approx(166.67)
        assert g["pace"] is None

    def test_negative_gain_closes_nothing(self):
        g = goal_progress(_goal(), 3_000, TODAY)
        assert g["gained_since_start_eur"] == -1_000
        assert g["gap_closed_pct"] == 0


class TestSummary:
    def test_counts_and_planned_monthly_skip_achieved(self):
        goals = [
            {
                "status": "achieved",
                "monthly_contribution_eur": 100,
                "target_date": "2026-01-01",
            },
            {
                "status": "on_track",
                "monthly_contribution_eur": 200,
                "target_date": "2030-01-01",
            },
            {
                "status": "off_track",
                "monthly_contribution_eur": 300,
                "target_date": "2028-05-01",
            },
            {
                "status": "overdue",
                "monthly_contribution_eur": 50,
                "target_date": "2025-01-01",
            },
        ]
        s = goals_summary(goals)
        assert s["total"] == 4
        assert (s["achieved"], s["on_track"], s["off_track"], s["overdue"]) == (
            1,
            1,
            1,
            1,
        )
        assert s["planned_monthly_eur"] == 550
        assert s["next_target_date"] == "2025-01-01"

    def test_empty(self):
        s = goals_summary([])
        assert s["total"] == 0
        assert s["planned_monthly_eur"] == 0
        assert s["next_target_date"] is None
