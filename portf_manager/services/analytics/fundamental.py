"""
Fundamental Analysis Engine

Provides fundamental analysis and risk assessment for stock recommendations.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional

from ..market_data import (
    EnhancedMarketDataService,
    get_market_data_service,
    FundamentalData,
)

logger = logging.getLogger(__name__)


class FundamentalRating(str, Enum):
    """Fundamental analysis rating levels."""

    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    VERY_POOR = "very_poor"


@dataclass
class FundamentalScore:
    """Individual fundamental metric score."""

    metric: str
    value: Optional[float]
    score: float  # 0-10
    weight: float
    comment: str


@dataclass
class FundamentalAnalysis:
    """Complete fundamental analysis."""

    symbol: str
    overall_rating: FundamentalRating
    overall_score: float
    scores: List[FundamentalScore]
    valuation_summary: str
    financial_health: str
    growth_prospects: str
    risk_factors: List[str]
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class FundamentalAnalysisEngine:
    """Fundamental analysis engine."""

    def __init__(self, market_data_service: Optional[EnhancedMarketDataService] = None):
        self.market_data_service = market_data_service or get_market_data_service()

    async def analyze_symbol(self, symbol: str) -> Optional[FundamentalAnalysis]:
        """Generate comprehensive fundamental analysis for a symbol."""
        try:
            # Get fundamental data
            fundamental_data = await self.market_data_service.get_fundamental_data(
                symbol
            )
            if not fundamental_data:
                logger.warning(f"No fundamental data available for {symbol}")
                return None

            # Generate individual scores
            scores = self._calculate_scores(fundamental_data)

            # Calculate overall score
            overall_score = self._calculate_overall_score(scores)
            overall_rating = self._score_to_rating(overall_score)

            # Generate analysis summaries
            valuation_summary = self._generate_valuation_summary(
                fundamental_data, scores
            )
            financial_health = self._assess_financial_health(fundamental_data, scores)
            growth_prospects = self._assess_growth_prospects(fundamental_data, scores)
            risk_factors = self._identify_risk_factors(fundamental_data, scores)

            return FundamentalAnalysis(
                symbol=symbol,
                overall_rating=overall_rating,
                overall_score=overall_score,
                scores=scores,
                valuation_summary=valuation_summary,
                financial_health=financial_health,
                growth_prospects=growth_prospects,
                risk_factors=risk_factors,
            )

        except Exception as e:
            logger.error(f"Error analyzing symbol {symbol}: {e}")
            return None

    def _calculate_scores(self, data: FundamentalData) -> List[FundamentalScore]:
        """Calculate individual fundamental scores."""
        scores = []

        # Valuation metrics
        if data.pe_ratio is not None:
            pe_score = self._score_pe_ratio(data.pe_ratio)
            scores.append(
                FundamentalScore(
                    metric="P/E Ratio",
                    value=data.pe_ratio,
                    score=pe_score,
                    weight=0.15,
                    comment=self._pe_comment(data.pe_ratio),
                )
            )

        if data.price_to_book is not None:
            pb_score = self._score_price_to_book(data.price_to_book)
            scores.append(
                FundamentalScore(
                    metric="Price to Book",
                    value=data.price_to_book,
                    score=pb_score,
                    weight=0.1,
                    comment=self._pb_comment(data.price_to_book),
                )
            )

        # Profitability metrics
        if data.return_on_equity is not None:
            roe_score = self._score_roe(data.return_on_equity)
            scores.append(
                FundamentalScore(
                    metric="Return on Equity",
                    value=data.return_on_equity * 100,  # Convert to percentage
                    score=roe_score,
                    weight=0.2,
                    comment=self._roe_comment(data.return_on_equity),
                )
            )

        # Growth metrics
        if data.revenue_growth is not None:
            revenue_score = self._score_growth(data.revenue_growth)
            scores.append(
                FundamentalScore(
                    metric="Revenue Growth",
                    value=data.revenue_growth * 100,
                    score=revenue_score,
                    weight=0.15,
                    comment=self._growth_comment(data.revenue_growth, "revenue"),
                )
            )

        if data.earnings_growth is not None:
            earnings_score = self._score_growth(data.earnings_growth)
            scores.append(
                FundamentalScore(
                    metric="Earnings Growth",
                    value=data.earnings_growth * 100,
                    score=earnings_score,
                    weight=0.15,
                    comment=self._growth_comment(data.earnings_growth, "earnings"),
                )
            )

        # Financial health metrics
        if data.debt_to_equity is not None:
            debt_score = self._score_debt_to_equity(data.debt_to_equity)
            scores.append(
                FundamentalScore(
                    metric="Debt to Equity",
                    value=data.debt_to_equity,
                    score=debt_score,
                    weight=0.1,
                    comment=self._debt_comment(data.debt_to_equity),
                )
            )

        if data.current_ratio is not None:
            current_score = self._score_current_ratio(data.current_ratio)
            scores.append(
                FundamentalScore(
                    metric="Current Ratio",
                    value=data.current_ratio,
                    score=current_score,
                    weight=0.1,
                    comment=self._current_ratio_comment(data.current_ratio),
                )
            )

        # Dividend metrics
        if data.dividend_yield is not None:
            div_score = self._score_dividend_yield(data.dividend_yield)
            scores.append(
                FundamentalScore(
                    metric="Dividend Yield",
                    value=data.dividend_yield * 100,
                    score=div_score,
                    weight=0.05,
                    comment=self._dividend_comment(data.dividend_yield),
                )
            )

        return scores

    def _calculate_overall_score(self, scores: List[FundamentalScore]) -> float:
        """Calculate weighted overall score."""
        if not scores:
            return 0.0

        weighted_sum = sum(score.score * score.weight for score in scores)
        total_weight = sum(score.weight for score in scores)

        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def _score_to_rating(self, score: float) -> FundamentalRating:
        """Convert numeric score to rating."""
        if score >= 8:
            return FundamentalRating.EXCELLENT
        elif score >= 6:
            return FundamentalRating.GOOD
        elif score >= 4:
            return FundamentalRating.FAIR
        elif score >= 2:
            return FundamentalRating.POOR
        else:
            return FundamentalRating.VERY_POOR

    def _score_pe_ratio(self, pe: float) -> float:
        """Score P/E ratio (lower is generally better for value)."""
        if pe < 0:
            return 2  # Negative earnings
        elif pe <= 10:
            return 9  # Very attractive
        elif pe <= 15:
            return 8
        elif pe <= 20:
            return 6
        elif pe <= 30:
            return 4
        else:
            return 2  # Expensive

    def _score_price_to_book(self, pb: float) -> float:
        """Score P/B ratio."""
        if pb <= 1:
            return 9
        elif pb <= 2:
            return 7
        elif pb <= 3:
            return 5
        else:
            return 3

    def _score_roe(self, roe: float) -> float:
        """Score Return on Equity."""
        if roe >= 0.20:  # 20%+
            return 9
        elif roe >= 0.15:
            return 8
        elif roe >= 0.10:
            return 6
        elif roe >= 0.05:
            return 4
        else:
            return 2

    def _score_growth(self, growth: float) -> float:
        """Score growth rate."""
        if growth >= 0.30:  # 30%+
            return 9
        elif growth >= 0.20:
            return 8
        elif growth >= 0.10:
            return 6
        elif growth >= 0.05:
            return 4
        elif growth >= 0:
            return 3
        else:
            return 1  # Negative growth

    def _score_debt_to_equity(self, debt: float) -> float:
        """Score debt to equity (lower is better)."""
        if debt <= 0.2:
            return 9
        elif debt <= 0.5:
            return 7
        elif debt <= 1.0:
            return 5
        else:
            return 2

    def _score_current_ratio(self, ratio: float) -> float:
        """Score current ratio."""
        if 1.5 <= ratio <= 3.0:
            return 8
        elif 1.0 <= ratio < 1.5:
            return 6
        elif ratio >= 3.0:
            return 5  # Too much cash might not be optimal
        else:
            return 2  # Below 1.0 is concerning

    def _score_dividend_yield(self, yield_val: float) -> float:
        """Score dividend yield."""
        if 0.02 <= yield_val <= 0.06:  # 2-6%
            return 7
        elif 0.01 <= yield_val < 0.02:
            return 5
        elif yield_val > 0.06:
            return 4  # Might be unsustainable
        else:
            return 3  # No dividend

    # Comment generation methods
    def _pe_comment(self, pe: float) -> str:
        if pe < 0:
            return "Negative earnings"
        elif pe <= 15:
            return "Attractive valuation"
        elif pe <= 25:
            return "Fair valuation"
        else:
            return "Expensive valuation"

    def _pb_comment(self, pb: float) -> str:
        if pb <= 1:
            return "Trading below book value"
        elif pb <= 2:
            return "Reasonable valuation"
        else:
            return "Premium to book value"

    def _roe_comment(self, roe: float) -> str:
        if roe >= 0.15:
            return "Excellent profitability"
        elif roe >= 0.10:
            return "Good profitability"
        else:
            return "Moderate profitability"

    def _growth_comment(self, growth: float, metric: str) -> str:
        if growth >= 0.20:
            return f"Strong {metric} growth"
        elif growth >= 0.10:
            return f"Healthy {metric} growth"
        elif growth >= 0:
            return f"Modest {metric} growth"
        else:
            return f"Declining {metric}"

    def _debt_comment(self, debt: float) -> str:
        if debt <= 0.3:
            return "Conservative debt levels"
        elif debt <= 0.7:
            return "Moderate debt levels"
        else:
            return "High debt levels"

    def _current_ratio_comment(self, ratio: float) -> str:
        if ratio >= 2:
            return "Strong liquidity position"
        elif ratio >= 1.2:
            return "Adequate liquidity"
        else:
            return "Potential liquidity concerns"

    def _dividend_comment(self, yield_val: float) -> str:
        if yield_val >= 0.04:
            return "Attractive dividend yield"
        elif yield_val >= 0.02:
            return "Moderate dividend yield"
        else:
            return "Low/no dividend"

    def _generate_valuation_summary(
        self, data: FundamentalData, scores: List[FundamentalScore]
    ) -> str:
        """Generate valuation summary."""
        valuation_scores = [
            s for s in scores if s.metric in ["P/E Ratio", "Price to Book"]
        ]
        if not valuation_scores:
            return "Insufficient valuation data"

        avg_score = sum(s.score for s in valuation_scores) / len(valuation_scores)

        if avg_score >= 7:
            return "Stock appears undervalued based on key metrics"
        elif avg_score >= 5:
            return "Stock appears fairly valued"
        else:
            return "Stock appears overvalued based on key metrics"

    def _assess_financial_health(
        self, data: FundamentalData, scores: List[FundamentalScore]
    ) -> str:
        """Assess financial health."""
        health_scores = [
            s
            for s in scores
            if s.metric in ["Debt to Equity", "Current Ratio", "Return on Equity"]
        ]
        if not health_scores:
            return "Insufficient financial health data"

        avg_score = sum(s.score for s in health_scores) / len(health_scores)

        if avg_score >= 7:
            return "Strong financial health with good profitability and manageable debt"
        elif avg_score >= 5:
            return "Adequate financial health"
        else:
            return "Concerns about financial health and debt management"

    def _assess_growth_prospects(
        self, data: FundamentalData, scores: List[FundamentalScore]
    ) -> str:
        """Assess growth prospects."""
        growth_scores = [
            s for s in scores if s.metric in ["Revenue Growth", "Earnings Growth"]
        ]
        if not growth_scores:
            return "Insufficient growth data"

        avg_score = sum(s.score for s in growth_scores) / len(growth_scores)

        if avg_score >= 7:
            return "Strong growth prospects with healthy revenue and earnings expansion"
        elif avg_score >= 5:
            return "Moderate growth prospects"
        else:
            return "Limited growth prospects or declining trends"

    def _identify_risk_factors(
        self, data: FundamentalData, scores: List[FundamentalScore]
    ) -> List[str]:
        """Identify key risk factors."""
        risks = []

        # High debt
        if data.debt_to_equity and data.debt_to_equity > 1.0:
            risks.append("High debt levels may limit financial flexibility")

        # Low liquidity
        if data.current_ratio and data.current_ratio < 1.2:
            risks.append("Potential liquidity concerns")

        # Negative growth
        if data.revenue_growth and data.revenue_growth < 0:
            risks.append("Declining revenue trend")

        if data.earnings_growth and data.earnings_growth < 0:
            risks.append("Declining earnings trend")

        # High valuation
        if data.pe_ratio and data.pe_ratio > 30:
            risks.append("High valuation may limit upside potential")

        return risks


# Global instance
_fundamental_engine: Optional[FundamentalAnalysisEngine] = None


def get_fundamental_analysis_engine() -> FundamentalAnalysisEngine:
    """Get the global fundamental analysis engine instance."""
    global _fundamental_engine
    if _fundamental_engine is None:
        _fundamental_engine = FundamentalAnalysisEngine()
    return _fundamental_engine


def set_fundamental_analysis_engine(engine: FundamentalAnalysisEngine) -> None:
    """Set the global fundamental analysis engine instance."""
    global _fundamental_engine
    _fundamental_engine = engine
