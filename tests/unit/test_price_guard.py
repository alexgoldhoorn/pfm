"""Tests for the pure price-spike rule and the held-price storage helpers."""

from datetime import date

from portf_manager.services import price_guard as pg

TODAY = date(2026, 10, 18)
YESTERDAY = date(2026, 10, 17)


def _held(price, day):
    return {"price": price, "date": day.isoformat()}


def test_inside_band_is_stored():
    assert pg.evaluate(100.0, YESTERDAY, 110.0, TODAY, None).outcome == pg.STORE


def test_no_previous_price_is_stored():
    assert pg.evaluate(None, None, 5.0, TODAY, None).outcome == pg.STORE


def test_times_100_is_held():
    assert pg.evaluate(10.0, YESTERDAY, 1000.0, TODAY, None).outcome == pg.HOLD


def test_divided_by_100_is_held():
    assert pg.evaluate(1000.0, YESTERDAY, 10.0, TODAY, None).outcome == pg.HOLD


def test_long_gap_widens_the_band():
    # +80 % in one day is out of band, but fine after 30 days (sqrt(30) wider).
    thirty_days_ago = date(2026, 9, 18)
    assert pg.evaluate(100.0, YESTERDAY, 180.0, TODAY, None).outcome == pg.HOLD
    assert pg.evaluate(100.0, thirty_days_ago, 180.0, TODAY, None).outcome == pg.STORE


def test_same_day_repeat_stays_held():
    held = _held(1000.0, TODAY)
    assert pg.evaluate(10.0, YESTERDAY, 1000.0, TODAY, held).outcome == pg.HOLD


def test_next_day_matching_price_confirms():
    held = _held(1000.0, YESTERDAY)
    got = pg.evaluate(10.0, date(2026, 10, 16), 1020.0, TODAY, held)
    assert got.outcome == pg.CONFIRM


def test_next_day_different_out_of_band_price_stays_held():
    held = _held(1000.0, YESTERDAY)
    assert pg.evaluate(10.0, date(2026, 10, 16), 500.0, TODAY, held).outcome == pg.HOLD


def test_next_day_back_in_band_clears():
    held = _held(1000.0, YESTERDAY)
    got = pg.evaluate(10.0, date(2026, 10, 16), 10.5, TODAY, held)
    assert got.outcome == pg.CLEAR


def test_held_roundtrip_and_listing(test_database):
    pg.set_held(test_database, 7, {"price": 1.0, "date": "2026-10-18"})
    pg.set_held(test_database, 9, {"price": 2.0, "date": "2026-10-18"})
    test_database.set_setting("unrelated", "x")
    assert pg.get_held(test_database, 7)["price"] == 1.0
    assert sorted(h["asset_id"] for h in pg.list_held_prices(test_database)) == [7, 9]
    pg.clear_held(test_database, 7)
    assert pg.get_held(test_database, 7) is None
