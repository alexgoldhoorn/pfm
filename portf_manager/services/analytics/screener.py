"""
Stock Screener Service

Provides stock screening functionality based on various criteria
including fundamental metrics, technical indicators, and custom filters.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from ..market_data import (
    EnhancedMarketDataService,
    get_market_data_service,
    FundamentalData,
    TechnicalAnalysis,
)

logger = logging.getLogger(__name__)


class ScreenerCriteria(str, Enum):
    """Stock screening criteria types."""

    MARKET_CAP = "market_cap"
    PE_RATIO = "pe_ratio"
    DIVIDEND_YIELD = "dividend_yield"
    REVENUE_GROWTH = "revenue_growth"
    EARNINGS_GROWTH = "earnings_growth"
    ROE = "return_on_equity"
    DEBT_TO_EQUITY = "debt_to_equity"
    CURRENT_RATIO = "current_ratio"
    BETA = "beta"
    RSI = "rsi_14"
    PRICE_CHANGE_1M = "price_change_1m"


@dataclass
class ScreenerFilter:
    """Individual screening filter."""

    criteria: ScreenerCriteria
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    exact_value: Optional[float] = None

    def matches(self, value: Optional[float]) -> bool:
        """Check if a value matches this filter."""
        if value is None:
            return False

        if self.exact_value is not None:
            return abs(value - self.exact_value) < 0.001

        if self.min_value is not None and value < self.min_value:
            return False

        if self.max_value is not None and value > self.max_value:
            return False

        return True


@dataclass
class ScreenerRequest:
    """Stock screening request configuration."""

    symbols: List[str]
    filters: List[ScreenerFilter]
    max_results: Optional[int] = None
    sort_by: Optional[ScreenerCriteria] = None
    sort_ascending: bool = True


@dataclass
class ScreenerResult:
    """Individual stock screening result."""

    symbol: str
    score: float
    matches_filters: bool
    fundamental_data: Optional[FundamentalData] = None
    technical_data: Optional[TechnicalAnalysis] = None
    metrics: Optional[Dict[str, float]] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


@dataclass
class ScreenerResponse:
    """Complete screening results."""

    results: List[ScreenerResult]
    total_screened: int
    total_matches: int
    filters_applied: List[ScreenerFilter]
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class StockScreener:
    """Advanced stock screening service."""

    def __init__(self, market_data_service: Optional[EnhancedMarketDataService] = None):
        self.market_data_service = market_data_service or get_market_data_service()

    async def screen_stocks(self, request: ScreenerRequest) -> ScreenerResponse:
        """Screen stocks based on the provided criteria."""
        results = []

        for symbol in request.symbols:
            try:
                # Get comprehensive data for the symbol
                fundamental_data = await self.market_data_service.get_fundamental_data(
                    symbol
                )
                technical_data = await self.market_data_service.get_technical_analysis(
                    symbol
                )

                # Create metrics dictionary
                metrics = self._extract_metrics(fundamental_data, technical_data)

                # Apply filters
                matches_filters = self._apply_filters(metrics, request.filters)

                # Calculate score
                score = self._calculate_score(metrics, request.filters)

                result = ScreenerResult(
                    symbol=symbol,
                    score=score,
                    matches_filters=matches_filters,
                    fundamental_data=fundamental_data,
                    technical_data=technical_data,
                    metrics=metrics,
                )

                results.append(result)

            except Exception as e:
                logger.error(f"Error screening symbol {symbol}: {e}")
                continue

        # Filter results that match all criteria
        matching_results = [r for r in results if r.matches_filters]

        # Sort results if requested
        if request.sort_by and matching_results:
            sort_key = request.sort_by.value
            matching_results.sort(
                key=lambda r: r.metrics.get(sort_key, 0) if r.metrics else 0,
                reverse=not request.sort_ascending,
            )

        # Limit results if requested
        if request.max_results:
            matching_results = matching_results[: request.max_results]

        return ScreenerResponse(
            results=matching_results,
            total_screened=len(results),
            total_matches=len(matching_results),
            filters_applied=request.filters,
        )

    def _extract_metrics(
        self,
        fundamental_data: Optional[FundamentalData],
        technical_data: Optional[TechnicalAnalysis],
    ) -> Dict[str, float]:
        """Extract relevant metrics from fundamental and technical data."""
        metrics = {}

        if fundamental_data:
            metrics.update(
                {
                    "market_cap": fundamental_data.market_cap,
                    "pe_ratio": fundamental_data.pe_ratio,
                    "dividend_yield": fundamental_data.dividend_yield,
                    "revenue_growth": fundamental_data.revenue_growth,
                    "earnings_growth": fundamental_data.earnings_growth,
                    "return_on_equity": fundamental_data.return_on_equity,
                    "debt_to_equity": fundamental_data.debt_to_equity,
                    "current_ratio": fundamental_data.current_ratio,
                    "beta": fundamental_data.beta,
                }
            )

        if technical_data:
            metrics.update(
                {
                    "rsi_14": technical_data.rsi_14,
                    "price_change_1m": technical_data.price_change_1m,
                    "current_price": technical_data.current_price,
                }
            )

        return {k: v for k, v in metrics.items() if v is not None}

    def _apply_filters(
        self, metrics: Dict[str, float], filters: List[ScreenerFilter]
    ) -> bool:
        """Apply all filters to the metrics."""
        for filter_obj in filters:
            metric_value = metrics.get(filter_obj.criteria.value)
            if not filter_obj.matches(metric_value):
                return False
        return True

    def _calculate_score(
        self, metrics: Dict[str, float], filters: List[ScreenerFilter]
    ) -> float:
        """Calculate a composite score for the stock."""
        # Simple scoring algorithm - can be made more sophisticated
        score = 0.0
        total_weight = 0.0

        scoring_weights = {
            "market_cap": 0.1,
            "pe_ratio": 0.2,  # Lower PE is better
            "dividend_yield": 0.1,
            "revenue_growth": 0.2,
            "earnings_growth": 0.2,
            "return_on_equity": 0.2,
            "debt_to_equity": 0.1,  # Lower is better
            "current_ratio": 0.1,
            "rsi_14": 0.1,
        }

        for metric, weight in scoring_weights.items():
            value = metrics.get(metric)
            if value is not None:
                # Normalize and weight the metric
                normalized_value = self._normalize_metric(metric, value)
                score += normalized_value * weight
                total_weight += weight

        return score / total_weight if total_weight > 0 else 0.0

    def _normalize_metric(self, metric: str, value: float) -> float:
        """Normalize metric values to 0-1 scale."""
        # Simple normalization - can be improved with historical data
        normalization_rules = {
            "market_cap": min(value / 1e12, 1.0),  # Cap at 1T
            "pe_ratio": max(0, min(1 - (value / 50), 1)),  # Lower is better
            "dividend_yield": min(value / 0.1, 1.0),  # Cap at 10%
            "revenue_growth": min(max(value, 0) / 0.5, 1.0),  # Cap at 50%
            "earnings_growth": min(max(value, 0) / 0.5, 1.0),  # Cap at 50%
            "return_on_equity": min(max(value, 0) / 0.3, 1.0),  # Cap at 30%
            "debt_to_equity": max(0, min(1 - (value / 2), 1)),  # Lower is better
            "current_ratio": min(max(value - 1, 0) / 2, 1.0),  # 1-3 range
            "rsi_14": 1 - abs(value - 50) / 50,  # Closer to 50 is better
        }

        return normalization_rules.get(metric, min(max(value, 0), 1))


# Predefined screening strategies
class PredefinedScreeners:
    """Predefined screening strategies."""

    @staticmethod
    def value_stocks() -> List[ScreenerFilter]:
        """Screen for value stocks."""
        return [
            ScreenerFilter(ScreenerCriteria.PE_RATIO, max_value=15),
            ScreenerFilter(ScreenerCriteria.MARKET_CAP, min_value=1e9),  # > $1B
            ScreenerFilter(ScreenerCriteria.DEBT_TO_EQUITY, max_value=0.6),
        ]

    @staticmethod
    def growth_stocks() -> List[ScreenerFilter]:
        """Screen for growth stocks."""
        return [
            ScreenerFilter(ScreenerCriteria.REVENUE_GROWTH, min_value=0.15),  # > 15%
            ScreenerFilter(ScreenerCriteria.EARNINGS_GROWTH, min_value=0.20),  # > 20%
            ScreenerFilter(ScreenerCriteria.ROE, min_value=0.15),  # > 15%
        ]

    @staticmethod
    def dividend_stocks() -> List[ScreenerFilter]:
        """Screen for dividend stocks."""
        return [
            ScreenerFilter(ScreenerCriteria.DIVIDEND_YIELD, min_value=0.03),  # > 3%
            ScreenerFilter(ScreenerCriteria.CURRENT_RATIO, min_value=1.2),
            ScreenerFilter(ScreenerCriteria.DEBT_TO_EQUITY, max_value=0.8),
        ]

    @staticmethod
    def quality_stocks() -> List[ScreenerFilter]:
        """Screen for high-quality stocks."""
        return [
            ScreenerFilter(ScreenerCriteria.ROE, min_value=0.15),  # > 15%
            ScreenerFilter(ScreenerCriteria.CURRENT_RATIO, min_value=1.5),
            ScreenerFilter(ScreenerCriteria.DEBT_TO_EQUITY, max_value=0.5),
            ScreenerFilter(ScreenerCriteria.MARKET_CAP, min_value=5e9),  # > $5B
        ]

    @staticmethod
    def momentum_stocks() -> List[ScreenerFilter]:
        """Screen for momentum stocks."""
        return [
            ScreenerFilter(
                ScreenerCriteria.PRICE_CHANGE_1M, min_value=5
            ),  # > 5% in 1 month
            ScreenerFilter(
                ScreenerCriteria.RSI, min_value=50, max_value=70
            ),  # Strong but not overbought
        ]


# Global screener instance
_stock_screener: Optional[StockScreener] = None


def get_stock_screener() -> StockScreener:
    """Get the global stock screener instance."""
    global _stock_screener
    if _stock_screener is None:
        _stock_screener = StockScreener()
    return _stock_screener


def set_stock_screener(screener: StockScreener) -> None:
    """Set the global stock screener instance."""
    global _stock_screener
    _stock_screener = screener
