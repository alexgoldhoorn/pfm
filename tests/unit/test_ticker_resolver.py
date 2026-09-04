"""Tests for the currency-match guard in ticker_resolver."""

from unittest.mock import MagicMock, patch

import pytest

from portf_manager import ticker_resolver as tr


def _cand(ticker, exch, sec="ETP"):
    return {"ticker": ticker, "exchCode": exch, "securityType": sec}


class TestExchangeCurrency:
    @pytest.mark.unit
    def test_known_codes(self):
        assert tr._exchange_currency("GR") == "EUR"
        assert tr._exchange_currency("fp") == "EUR"
        assert tr._exchange_currency("LN") == "GBP"
        assert tr._exchange_currency("US") == "USD"
        assert tr._exchange_currency("SW") == "CHF"

    @pytest.mark.unit
    def test_unknown_or_blank(self):
        assert tr._exchange_currency("ZZ") is None
        assert tr._exchange_currency("") is None
        assert tr._exchange_currency(None) is None


class TestPickBestTicker:
    @pytest.mark.unit
    def test_wrong_currency_listing_is_rejected(self):
        # Same ticker string on a US venue (USD) and a Paris venue (EUR);
        # the EUR asset must not resolve to the US listing.
        candidates = [_cand("PRAB", "US"), _cand("PRAB", "FP")]
        assert tr._pick_best_ticker(candidates, "EUR") == "PRAB"
        # ...and a USD asset picks it up, a EUR-only candidate is rejected.
        candidates = [_cand("XYZ", "FP"), _cand("XYZ", "US")]
        assert tr._pick_best_ticker(candidates, "USD") == "XYZ"

    @pytest.mark.unit
    def test_all_candidates_wrong_currency_returns_none(self):
        candidates = [_cand("AAA", "US"), _cand("AAA", "UN")]
        assert tr._pick_best_ticker(candidates, "EUR") is None

    @pytest.mark.unit
    def test_unknown_currency_does_not_filter(self):
        candidates = [_cand("AAA", "US")]
        # currency unknown -> no currency filtering, first usable ticker wins
        assert tr._pick_best_ticker(candidates, "") == "AAA"

    @pytest.mark.unit
    def test_unknown_exchange_not_filtered_but_ranked_below_match(self):
        # ZZ is an unknown venue; GR is known-EUR. The known-EUR one wins.
        candidates = [_cand("AAA", "ZZ"), _cand("BBB", "GR")]
        assert tr._pick_best_ticker(candidates, "EUR") == "BBB"

    @pytest.mark.unit
    def test_candidate_without_ticker_skipped(self):
        candidates = [
            {"ticker": "", "exchCode": "GR"},
            _cand("REAL", "GR"),
        ]
        assert tr._pick_best_ticker(candidates, "EUR") == "REAL"


def _fast_info(price, currency):
    m = MagicMock()
    m.get.side_effect = lambda k, d=None: {
        "lastPrice": price,
        "regularMarketPrice": price,
        "currency": currency,
    }.get(k, d)
    return m


class TestVerifyYf:
    @pytest.mark.unit
    def test_currency_match_passes(self):
        with patch.object(tr.yf, "Ticker") as mk:
            mk.return_value.fast_info = _fast_info(21.7, "EUR")
            assert tr._verify_yf("PRAB.DE", "EUR") is True

    @pytest.mark.unit
    def test_currency_mismatch_fails(self):
        with patch.object(tr.yf, "Ticker") as mk:
            mk.return_value.fast_info = _fast_info(24.8, "USD")
            assert tr._verify_yf("PRAB", "EUR") is False

    @pytest.mark.unit
    def test_missing_quote_currency_not_rejected(self):
        with patch.object(tr.yf, "Ticker") as mk:
            mk.return_value.fast_info = _fast_info(10.0, None)
            assert tr._verify_yf("AAA", "EUR") is True

    @pytest.mark.unit
    def test_gbx_treated_as_gbp(self):
        with patch.object(tr.yf, "Ticker") as mk:
            mk.return_value.fast_info = _fast_info(830.0, "GBp")
            assert tr._verify_yf("VOD.L", "GBP") is True

    @pytest.mark.unit
    def test_no_price_fails_before_currency_check(self):
        with patch.object(tr.yf, "Ticker") as mk:
            mk.return_value.fast_info = _fast_info(0, "EUR")
            assert tr._verify_yf("DEAD", "EUR") is False

    @pytest.mark.unit
    def test_no_expected_currency_keeps_old_behaviour(self):
        with patch.object(tr.yf, "Ticker") as mk:
            mk.return_value.fast_info = _fast_info(24.8, "USD")
            assert tr._verify_yf("PRAB") is True


class TestResolveTickerForIsin:
    @pytest.mark.unit
    def test_wrong_currency_resolution_returns_none_not_wrong_ticker(self):
        # OpenFIGI offers only "PRAB" on Paris; yfinance serves neither
        # "PRAB.PA" nor bare "PRAB" as the EUR fund — bare "PRAB" matches an
        # unrelated USD security. The currency guard must reject it -> None,
        # never store the USD ticker.
        openfigi = {"LU0000000014": [_cand("PRAB", "FP")]}
        with (
            patch.object(tr, "_openfigi_batch", return_value=openfigi),
            patch.object(tr.yf, "Ticker") as mk,
        ):
            mk.return_value.fast_info = _fast_info(24.8, "USD")
            assert tr.resolve_ticker_for_isin("LU0000000014", "EUR") is None

    @pytest.mark.unit
    def test_right_currency_resolution_succeeds(self):
        openfigi = {"IE00B4L5Y983": [_cand("IWDA", "NA")]}
        with (
            patch.object(tr, "_openfigi_batch", return_value=openfigi),
            patch.object(tr.yf, "Ticker") as mk,
        ):
            mk.return_value.fast_info = _fast_info(95.0, "EUR")
            assert tr.resolve_ticker_for_isin("IE00B4L5Y983", "EUR") == "IWDA.AS"
