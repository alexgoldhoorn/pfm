"""Unit tests for the pure/DB-light helpers in
``portf_manager/services/rebalance_planner.py``.

These go straight at the functions — no HTTP layer — because they encode
rules the API shape can't show: that an override *replaces* a stored target
rather than adding to it, where exactly the target-sum tolerance band ends,
and which gaps ``compute_gaps`` drops. Trade generation itself is Task 3 and
is not covered here.

All data is invented (plain asset-type strings, round numbers).
"""

import pytest

from portf_manager.services.rebalance_planner import (
    compute_gaps,
    merge_targets,
    validate_target_sum,
)


class _FakeDB:
    """Minimal stand-in exposing only what ``merge_targets`` touches."""

    def __init__(self, targets):
        self._targets = targets

    def get_allocation_targets(self):
        return self._targets


class TestMergeTargets:
    def test_no_overrides_returns_stored_targets(self):
        db = _FakeDB(
            [
                {"asset_type": "stock", "target_pct": 60},
                {"asset_type": "etf", "target_pct": 40},
            ]
        )
        assert merge_targets(db, None) == {"stock": 60.0, "etf": 40.0}
        assert merge_targets(db, []) == {"stock": 60.0, "etf": 40.0}

    def test_override_replaces_and_never_adds(self):
        """The rule that matters: an override for "stock" is 50, not 60+50."""
        db = _FakeDB(
            [
                {"asset_type": "stock", "target_pct": 60},
                {"asset_type": "etf", "target_pct": 40},
            ]
        )
        merged = merge_targets(db, [{"asset_type": "stock", "target_pct": 50}])
        assert merged["stock"] == 50.0
        # Not summed...
        assert merged["stock"] != 110.0
        # ...and an asset type nobody overrode keeps its stored value.
        assert merged["etf"] == 40.0

    def test_override_can_introduce_a_new_asset_type(self):
        db = _FakeDB([{"asset_type": "stock", "target_pct": 100}])
        merged = merge_targets(db, [{"asset_type": "crypto", "target_pct": 5}])
        assert merged == {"stock": 100.0, "crypto": 5.0}

    def test_values_are_coerced_to_float(self):
        db = _FakeDB([{"asset_type": "stock", "target_pct": "60"}])
        merged = merge_targets(db, [{"asset_type": "etf", "target_pct": "40"}])
        assert merged == {"stock": 60.0, "etf": 40.0}


class TestValidateTargetSum:
    def test_exactly_100_passes(self):
        validate_target_sum({"stock": 60.0, "etf": 40.0})

    @pytest.mark.parametrize("total", [99.5, 100.5])
    def test_boundary_values_are_inclusive(self, total):
        """99.5 and 100.5 are inside the band, not outside it."""
        validate_target_sum({"stock": total})

    @pytest.mark.parametrize("total", [99.4, 100.6])
    def test_just_outside_the_band_raises(self, total):
        with pytest.raises(ValueError) as exc:
            validate_target_sum({"stock": total})
        assert "sum to ~100%" in str(exc.value)

    def test_empty_targets_raise(self):
        with pytest.raises(ValueError):
            validate_target_sum({})


class TestComputeGaps:
    """``gap_eur`` is ``drift_eur`` verbatim: positive = underweight (BUY),
    negative = overweight (SELL); ``abs(gap) < min_trade_eur`` is dropped.
    """

    ALLOCATIONS = [
        # Underweight by €500 → a BUY.
        {"asset_type": "stock", "drift_eur": 500.0, "drift_pct": -5.0},
        # Overweight by €500 → a SELL.
        {"asset_type": "etf", "drift_eur": -500.0, "drift_pct": 5.0},
        # Below any sane min_trade_eur → dropped.
        {"asset_type": "crypto", "drift_eur": 12.5, "drift_pct": 0.1},
    ]

    def test_sides_and_amounts(self):
        gaps = compute_gaps(self.ALLOCATIONS, min_trade_eur=100.0)
        assert gaps == [
            {"asset_type": "stock", "gap_eur": 500.0, "side": "BUY"},
            {"asset_type": "etf", "gap_eur": -500.0, "side": "SELL"},
        ]

    def test_small_gap_included_when_threshold_is_lower(self):
        gaps = compute_gaps(self.ALLOCATIONS, min_trade_eur=10.0)
        assert [g["asset_type"] for g in gaps] == ["stock", "etf", "crypto"]
        assert gaps[-1]["side"] == "BUY"

    def test_threshold_is_on_absolute_value_so_it_drops_sells_too(self):
        gaps = compute_gaps(self.ALLOCATIONS, min_trade_eur=600.0)
        assert gaps == []

    def test_gap_exactly_at_the_threshold_is_kept(self):
        """The check is ``abs(gap) < min_trade_eur``, so equality survives."""
        gaps = compute_gaps(
            [{"asset_type": "stock", "drift_eur": 100.0}], min_trade_eur=100.0
        )
        assert len(gaps) == 1

    def test_zero_min_trade_keeps_every_drifted_type(self):
        gaps = compute_gaps(self.ALLOCATIONS, min_trade_eur=0.0)
        assert len(gaps) == 3

    def test_empty_allocations(self):
        assert compute_gaps([], min_trade_eur=100.0) == []
