"""The research lookup must fetch fundamentals for the SAME instrument the
price came from.

Two failures motivated these tests, both visible in the daily finance digest:

* ``USDC`` resolved on yfinance to USDATA CORP (a $0.0012 penny stock), so a
  stablecoin held at €0.86 reported a +71,600% daily move. ``SUI`` resolved to
  Sun Communities, ``ADA`` to nothing at all. pfm already stores the correct
  yfinance ticker on each asset (``USDC-EUR``, ``SUI-EUR``, ``ADA-EUR``) and
  the price updater uses it — the lookup just didn't.
* ``GAW.L`` is quoted by yfinance in GBp (pence) while pfm stores the asset in
  GBP. ``current_price`` came back as 184.0 and ``previousClose`` as 18450.0 in
  the same payload, reading as a -99% day.
"""

from portf_manager.services.price_updater import _CRYPTO_YF_OVERRIDES
from portf_manager.services.research import _normalize_gbp_fundamentals
from portf_server.routers.research import _yf_symbol


class TestYfSymbol:
    """Mirrors price_updater's `asset.get("ticker") or sym` resolution."""

    def test_prefers_the_assets_resolved_ticker(self):
        asset = {"symbol": "USDC", "ticker": "USDC-EUR", "asset_type": "crypto"}
        assert _yf_symbol(asset, "USDC") == "USDC-EUR"

    def test_falls_back_to_crypto_eur_pair_when_ticker_unset(self):
        asset = {"symbol": "DOT", "ticker": None, "asset_type": "crypto"}
        assert _yf_symbol(asset, "DOT") == "DOT-EUR"

    def test_crypto_override_beats_a_stale_stored_ticker(self):
        # SUI's asset row carries ticker "SUI-EUR", which yfinance has no data
        # for; the price updater has always used the override instead, so the
        # fundamentals must follow it or the two describe different things.
        asset = {"symbol": "SUI", "ticker": "SUI-EUR", "asset_type": "crypto"}
        assert _yf_symbol(asset, "SUI") == "SUI20947-USD"

    def test_every_override_coin_resolves_to_its_override(self):
        # A coin in the override map must never fall back to "{SYM}-EUR" —
        # those pairs carry no yfinance data, which is how INV's price sat
        # frozen at a May figure and SUI reported against Sun Communities.
        for sym, (want, _ccy) in _CRYPTO_YF_OVERRIDES.items():
            asset = {"symbol": sym, "ticker": f"{sym}-EUR", "asset_type": "crypto"}
            assert _yf_symbol(asset, sym) == want

    def test_caller_passing_a_yahoo_pair_does_not_double_the_suffix(self):
        # The finance digest normalizes BTC -> BTC-EUR before calling, so the
        # request string is already a pair; deriving from it gave BTC-EUR-EUR.
        asset = {"symbol": "BTC", "ticker": "BTC-EUR", "asset_type": "crypto"}
        assert _yf_symbol(asset, "BTC-EUR") == "BTC-EUR"

    def test_equity_without_ticker_uses_the_symbol(self):
        asset = {"symbol": "AAPL", "ticker": None, "asset_type": "stock"}
        assert _yf_symbol(asset, "AAPL") == "AAPL"

    def test_equity_ticker_alias_wins_over_isin_symbol(self):
        asset = {"symbol": "GB0000000000", "ticker": "XYZ.L", "asset_type": "stock"}
        assert _yf_symbol(asset, "GB0000000000") == "XYZ.L"

    def test_unknown_symbol_with_no_asset_is_passed_through(self):
        assert _yf_symbol(None, "NVDA") == "NVDA"


class TestNormalizeGbpFundamentals:
    """Only the per-share quote fields are quoted in pence.

    marketCap, revenue, cashflow and EPS are already in pounds — dividing
    those by 100 would swap one wrong number for another. Verified against
    GAW.L: trailingPE 29.58 == 184 GBP / 6.22 EPS, and marketCap 6.08bn ==
    32.8M shares x 184 GBP.
    """

    def test_converts_only_the_pence_quote_fields(self):
        out = _normalize_gbp_fundamentals(
            {
                "currency": "GBp",
                "currentPrice": 18400.0,
                "previousClose": 18450.0,
                "fiftyTwoWeekLow": 14070.0,
                "fiftyTwoWeekHigh": 23540.0,
                "targetMeanPrice": 22075.0,
            }
        )
        assert out["currentPrice"] == 184.0
        assert out["previousClose"] == 184.5
        assert out["fiftyTwoWeekLow"] == 140.70
        assert out["fiftyTwoWeekHigh"] == 235.40
        assert out["targetMeanPrice"] == 220.75

    def test_leaves_pound_denominated_fields_alone(self):
        out = _normalize_gbp_fundamentals(
            {
                "currency": "GBp",
                "marketCap": 6080314880,
                "trailingEps": 6.22,
                "totalRevenue": 659699968,
                "freeCashflow": 178350000,
                "trailingPE": 29.581995,
                "dividendYield": 2.14,
            }
        )
        assert out["marketCap"] == 6080314880
        assert out["trailingEps"] == 6.22
        assert out["totalRevenue"] == 659699968
        assert out["freeCashflow"] == 178350000
        assert out["trailingPE"] == 29.581995
        assert out["dividendYield"] == 2.14

    def test_reports_gbp_so_callers_can_see_the_unit(self):
        # api_client.get_quote_currency does the same GBp -> GBP relabel.
        out = _normalize_gbp_fundamentals({"currency": "GBp", "previousClose": 18450.0})
        assert out["currency"] == "GBP"

    def test_non_pence_currencies_pass_through_untouched(self):
        src = {"currency": "USD", "currentPrice": 230.36, "previousClose": 228.5}
        assert _normalize_gbp_fundamentals(dict(src)) == src

    def test_missing_currency_is_left_alone(self):
        src = {"currentPrice": 1.0, "previousClose": 2.0}
        assert _normalize_gbp_fundamentals(dict(src)) == src

    def test_none_values_are_not_arithmetic_errors(self):
        out = _normalize_gbp_fundamentals({"currency": "GBp", "previousClose": None})
        assert out["previousClose"] is None
