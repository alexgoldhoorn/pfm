"""
Enhanced Market Data Service for Stock Analysis

This module extends the existing API client with additional market data
functionality needed for stock analysis and recommendations.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

import pandas as pd
import yfinance as yf

from ..api_client import APIClient, get_client

logger = logging.getLogger(__name__)


class MarketDataError(Exception):
    """Base exception for market data service errors."""


class TechnicalIndicator(str, Enum):
    """Technical indicator types."""

    SMA = "sma"
    EMA = "ema"
    RSI = "rsi"
    MACD = "macd"
    BOLLINGER = "bollinger"
    STOCHASTIC = "stochastic"


@dataclass
class TechnicalAnalysis:
    """Technical analysis results for a symbol."""

    symbol: str
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    ema_12: Optional[float] = None
    ema_26: Optional[float] = None
    rsi_14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    bollinger_upper: Optional[float] = None
    bollinger_lower: Optional[float] = None
    bollinger_middle: Optional[float] = None
    stochastic_k: Optional[float] = None
    stochastic_d: Optional[float] = None
    volume_avg_10: Optional[float] = None
    price_change_1d: Optional[float] = None
    price_change_5d: Optional[float] = None
    price_change_1m: Optional[float] = None
    current_price: Optional[float] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


@dataclass
class FundamentalData:
    """Fundamental analysis data for a symbol."""

    symbol: str
    market_cap: Optional[float] = None
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None
    peg_ratio: Optional[float] = None
    price_to_book: Optional[float] = None
    price_to_sales: Optional[float] = None
    enterprise_value: Optional[float] = None
    ebitda: Optional[float] = None
    profit_margins: Optional[float] = None
    operating_margins: Optional[float] = None
    return_on_equity: Optional[float] = None
    return_on_assets: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    revenue_growth: Optional[float] = None
    earnings_growth: Optional[float] = None
    dividend_yield: Optional[float] = None
    payout_ratio: Optional[float] = None
    beta: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    analyst_target_price: Optional[float] = None
    recommendation: Optional[str] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


@dataclass
class SentimentData:
    """News sentiment analysis for a symbol."""

    symbol: str
    sentiment_score: Optional[float] = None  # -1 to 1
    news_count: Optional[int] = None
    recent_headlines: Optional[List[str]] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class EnhancedMarketDataService:
    """Enhanced market data service for comprehensive stock analysis."""

    def __init__(self, api_client: Optional[APIClient] = None):
        self.api_client = api_client or get_client()
        self._cache = {}
        self._cache_ttl = 300  # 5 minutes default TTL

    async def get_technical_analysis(self, symbol: str) -> Optional[TechnicalAnalysis]:
        """Get comprehensive technical analysis for a symbol."""
        try:
            # Get historical data for technical indicators
            history = self._get_price_history(symbol, period="3mo")
            if history is None or len(history) < 50:
                logger.warning(f"Insufficient data for technical analysis: {symbol}")
                return None

            analysis = TechnicalAnalysis(symbol=symbol)

            # Calculate moving averages
            analysis.sma_20 = self._calculate_sma(history["Close"], 20)
            analysis.sma_50 = self._calculate_sma(history["Close"], 50)
            analysis.sma_200 = self._calculate_sma(history["Close"], 200)

            # Calculate EMAs
            analysis.ema_12 = self._calculate_ema(history["Close"], 12)
            analysis.ema_26 = self._calculate_ema(history["Close"], 26)

            # Calculate RSI
            analysis.rsi_14 = self._calculate_rsi(history["Close"], 14)

            # Calculate MACD
            macd_data = self._calculate_macd(history["Close"])
            if macd_data:
                analysis.macd = macd_data.get("macd")
                analysis.macd_signal = macd_data.get("signal")
                analysis.macd_histogram = macd_data.get("histogram")

            # Calculate Bollinger Bands
            bollinger = self._calculate_bollinger_bands(history["Close"])
            if bollinger:
                analysis.bollinger_upper = bollinger.get("upper")
                analysis.bollinger_lower = bollinger.get("lower")
                analysis.bollinger_middle = bollinger.get("middle")

            # Calculate Stochastic Oscillator
            stochastic = self._calculate_stochastic(
                history["High"], history["Low"], history["Close"]
            )
            if stochastic:
                analysis.stochastic_k = stochastic.get("k")
                analysis.stochastic_d = stochastic.get("d")

            # Volume and price changes
            analysis.volume_avg_10 = history["Volume"].tail(10).mean()
            analysis.current_price = float(history["Close"].iloc[-1])

            # Price changes
            if len(history) >= 1:
                analysis.price_change_1d = (
                    (history["Close"].iloc[-1] - history["Close"].iloc[-2])
                    / history["Close"].iloc[-2]
                    * 100
                )
            if len(history) >= 5:
                analysis.price_change_5d = (
                    (history["Close"].iloc[-1] - history["Close"].iloc[-6])
                    / history["Close"].iloc[-6]
                    * 100
                )
            if len(history) >= 20:
                analysis.price_change_1m = (
                    (history["Close"].iloc[-1] - history["Close"].iloc[-21])
                    / history["Close"].iloc[-21]
                    * 100
                )

            return analysis

        except Exception as e:
            logger.error(f"Error calculating technical analysis for {symbol}: {e}")
            return None

    async def get_fundamental_data(self, symbol: str) -> Optional[FundamentalData]:
        """Get comprehensive fundamental data for a symbol."""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info

            if not info or "symbol" not in info:
                return None

            data = FundamentalData(symbol=symbol)

            # Market metrics
            data.market_cap = info.get("marketCap")
            data.pe_ratio = info.get("trailingPE")
            data.forward_pe = info.get("forwardPE")
            data.peg_ratio = info.get("pegRatio")
            data.price_to_book = info.get("priceToBook")
            data.price_to_sales = info.get("priceToSalesTrailing12Months")
            data.enterprise_value = info.get("enterpriseValue")
            data.ebitda = info.get("ebitda")

            # Profitability metrics
            data.profit_margins = info.get("profitMargins")
            data.operating_margins = info.get("operatingMargins")
            data.return_on_equity = info.get("returnOnEquity")
            data.return_on_assets = info.get("returnOnAssets")

            # Financial health
            data.debt_to_equity = info.get("debtToEquity")
            data.current_ratio = info.get("currentRatio")
            data.quick_ratio = info.get("quickRatio")

            # Growth metrics
            data.revenue_growth = info.get("revenueGrowth")
            data.earnings_growth = info.get("earningsGrowth")

            # Dividend metrics
            data.dividend_yield = info.get("dividendYield")
            data.payout_ratio = info.get("payoutRatio")

            # Risk metrics
            data.beta = info.get("beta")

            # Price ranges
            data.fifty_two_week_high = info.get("fiftyTwoWeekHigh")
            data.fifty_two_week_low = info.get("fiftyTwoWeekLow")

            # Analyst data
            data.analyst_target_price = info.get("targetMeanPrice")
            data.recommendation = info.get("recommendationKey")

            return data

        except Exception as e:
            logger.error(f"Error fetching fundamental data for {symbol}: {e}")
            return None

    async def get_sentiment_analysis(self, symbol: str) -> Optional[SentimentData]:
        """Get news sentiment analysis for a symbol."""
        try:
            # This is a placeholder for sentiment analysis
            # In a real implementation, you would integrate with:
            # - News APIs (Alpha Vantage News, Finnhub, etc.)
            # - Sentiment analysis services
            # - Social media sentiment (Twitter, Reddit, etc.)

            sentiment_data = SentimentData(symbol=symbol)

            # Placeholder implementation - in practice, integrate with news APIs
            ticker = yf.Ticker(symbol)
            news = ticker.news

            if news:
                sentiment_data.news_count = len(news)
                sentiment_data.recent_headlines = [
                    item.get("title", "") for item in news[:5]
                ]
                # Simple sentiment placeholder (would use actual NLP in production)
                sentiment_data.sentiment_score = 0.1  # Neutral to slightly positive

            return sentiment_data

        except Exception as e:
            logger.error(f"Error fetching sentiment data for {symbol}: {e}")
            return None

    def _get_price_history(
        self, symbol: str, period: str = "1y"
    ) -> Optional[pd.DataFrame]:
        """Get price history using the existing API client or yfinance."""
        try:
            ticker = yf.Ticker(symbol)
            history = ticker.history(period=period)
            return history if not history.empty else None
        except Exception as e:
            logger.error(f"Error fetching price history for {symbol}: {e}")
            return None

    def _calculate_sma(self, prices: pd.Series, window: int) -> Optional[float]:
        """Calculate Simple Moving Average."""
        if len(prices) < window:
            return None
        return float(prices.rolling(window=window).mean().iloc[-1])

    def _calculate_ema(self, prices: pd.Series, window: int) -> Optional[float]:
        """Calculate Exponential Moving Average."""
        if len(prices) < window:
            return None
        return float(prices.ewm(span=window).mean().iloc[-1])

    def _calculate_rsi(self, prices: pd.Series, window: int = 14) -> Optional[float]:
        """Calculate Relative Strength Index."""
        if len(prices) < window + 1:
            return None

        delta = prices.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        avg_gain = gain.rolling(window=window).mean()
        avg_loss = loss.rolling(window=window).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return float(rsi.iloc[-1])

    def _calculate_macd(
        self, prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> Optional[Dict[str, float]]:
        """Calculate MACD (Moving Average Convergence Divergence)."""
        if len(prices) < slow + signal:
            return None

        ema_fast = prices.ewm(span=fast).mean()
        ema_slow = prices.ewm(span=slow).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal).mean()
        histogram = macd_line - signal_line

        return {
            "macd": float(macd_line.iloc[-1]),
            "signal": float(signal_line.iloc[-1]),
            "histogram": float(histogram.iloc[-1]),
        }

    def _calculate_bollinger_bands(
        self, prices: pd.Series, window: int = 20, num_std: float = 2
    ) -> Optional[Dict[str, float]]:
        """Calculate Bollinger Bands."""
        if len(prices) < window:
            return None

        sma = prices.rolling(window=window).mean()
        std = prices.rolling(window=window).std()

        upper = sma + (std * num_std)
        lower = sma - (std * num_std)

        return {
            "upper": float(upper.iloc[-1]),
            "middle": float(sma.iloc[-1]),
            "lower": float(lower.iloc[-1]),
        }

    def _calculate_stochastic(
        self,
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        k_window: int = 14,
        d_window: int = 3,
    ) -> Optional[Dict[str, float]]:
        """Calculate Stochastic Oscillator."""
        if len(close) < k_window:
            return None

        lowest_low = low.rolling(window=k_window).min()
        highest_high = high.rolling(window=k_window).max()

        k_percent = 100 * ((close - lowest_low) / (highest_high - lowest_low))
        d_percent = k_percent.rolling(window=d_window).mean()

        return {
            "k": float(k_percent.iloc[-1]),
            "d": float(d_percent.iloc[-1]),
        }


# Global instance
_market_data_service: Optional[EnhancedMarketDataService] = None


def get_market_data_service() -> EnhancedMarketDataService:
    """Get the global market data service instance."""
    global _market_data_service
    if _market_data_service is None:
        _market_data_service = EnhancedMarketDataService()
    return _market_data_service


def set_market_data_service(service: EnhancedMarketDataService) -> None:
    """Set the global market data service instance."""
    global _market_data_service
    _market_data_service = service
