"""Tests for the own-account pattern settings helpers."""

import pytest

from portf_manager.database import Database
from portf_manager.services.own_accounts import (
    SETTING_KEY,
    clean_patterns,
    load_patterns,
    matches_own_account,
    save_patterns,
)


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "own.db"))


def test_clean_strips_and_dedups():
    assert clean_patterns(["  Sam Sample ", "sam sample"]) == ["Sam Sample"]


@pytest.mark.parametrize("bad", ["", "   ", "abc"])
def test_clean_rejects_blank_and_short(bad):
    with pytest.raises(ValueError):
        clean_patterns(["Sam Sample", bad])


def test_short_error_names_the_offender():
    with pytest.raises(ValueError, match="abc"):
        clean_patterns(["abc"])


@pytest.mark.parametrize("raw", ["{not json", '"text"', "[1, 2]", '{"a": 1}'])
def test_load_tolerates_garbage(db, raw):
    db.set_setting(SETTING_KEY, raw)
    assert load_patterns(db) == []


def test_load_missing_is_empty(db):
    assert load_patterns(db) == []


def test_save_roundtrip(db):
    assert save_patterns(db, [" Sam Sample ", "sam sample", "Example Savings"]) == [
        "Sam Sample",
        "Example Savings",
    ]
    assert load_patterns(db) == ["Sam Sample", "Example Savings"]


def test_matches_description_or_merchant_case_insensitive():
    assert matches_own_account(["sam sample"], "TRANSFER SAM SAMPLE", None)
    assert matches_own_account(["Example Savings"], "x", "example savings ltd")
    assert not matches_own_account(["Sam Sample"], "Groceries", "Shop")
    assert not matches_own_account([], "Sam Sample", None)
