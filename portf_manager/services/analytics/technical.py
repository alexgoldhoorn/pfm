"""
Technical Analysis Engine

Provides technical analysis signals and recommendations based on
price action, volume, and technical indicators.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

from ..market_data import (
    EnhancedMarketDataService,
    get_market_data_service,
    TechnicalAnalysis,
)

logger = logging.getLogger(__name__)


class SignalStrength(str, Enum):
    """Signal strength levels."""

    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD = "hold"
    SELL = "sell"
    STRONG_SELL = "strong_sell"


class SignalType(str, Enum):
    """Types of technical signals."""

    MOMENTUM = "momentum"
    TREND = "trend"
    VOLUME = "volume"
    SUPPORT_RESISTANCE = "support_resistance"
    REVERSAL = "reversal"


@dataclass
class TechnicalSignal:
    """Individual technical analysis signal."""

    signal_type: SignalType
    strength: SignalStrength
    confidence: float  # 0-1
    description: str
    supporting_indicators: List[str]
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None


@dataclass
class TechnicalRecommendation:
    """Complete technical analysis recommendation."""

    symbol: str
    overall_signal: SignalStrength
    overall_confidence: float
    current_price: float
    signals: List[TechnicalSignal]
    key_levels: Dict[str, float]  # support, resistance, etc.
    risk_assessment: str
    time_horizon: str  # short, medium, long
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class TechnicalAnalysisEngine:
    """Technical analysis engine for generating trading signals."""

    def __init__(self, market_data_service: Optional[EnhancedMarketDataService] = None):
        self.market_data_service = market_data_service or get_market_data_service()

    async def analyze_symbol(self, symbol: str) -> Optional[TechnicalRecommendation]:
        """Generate comprehensive technical analysis for a symbol."""
        try:
            # Get technical data
            technical_data = await self.market_data_service.get_technical_analysis(
                symbol
            )
            if not technical_data:
                logger.warning(f"No technical data available for {symbol}")
                return None

            # Generate individual signals
            signals = []

            # Momentum signals
            momentum_signal = self._analyze_momentum(technical_data)
            if momentum_signal:
                signals.append(momentum_signal)

            # Trend signals
            trend_signal = self._analyze_trend(technical_data)
            if trend_signal:
                signals.append(trend_signal)

            # Volume signals
            volume_signal = self._analyze_volume(technical_data)
            if volume_signal:
                signals.append(volume_signal)

            # Support/Resistance signals
            sr_signal = self._analyze_support_resistance(technical_data)
            if sr_signal:
                signals.append(sr_signal)

            # Reversal signals
            reversal_signal = self._analyze_reversal_patterns(technical_data)
            if reversal_signal:
                signals.append(reversal_signal)

            # Calculate overall recommendation
            overall_signal, overall_confidence = self._calculate_overall_signal(signals)

            # Determine key levels
            key_levels = self._identify_key_levels(technical_data)

            # Risk assessment
            risk_assessment = self._assess_risk(technical_data, signals)

            # Time horizon
            time_horizon = self._determine_time_horizon(signals)

            return TechnicalRecommendation(
                symbol=symbol,
                overall_signal=overall_signal,
                overall_confidence=overall_confidence,
                current_price=technical_data.current_price or 0.0,
                signals=signals,
                key_levels=key_levels,
                risk_assessment=risk_assessment,
                time_horizon=time_horizon,
            )

        except Exception as e:
            logger.error(f"Error analyzing symbol {symbol}: {e}")
            return None

    def _analyze_momentum(self, data: TechnicalAnalysis) -> Optional[TechnicalSignal]:
        """Analyze momentum indicators."""
        indicators = []
        score = 0

        # RSI Analysis
        if data.rsi_14 is not None:
            if data.rsi_14 > 70:
                score -= 2  # Overbought
                indicators.append("RSI overbought (>70)")
            elif data.rsi_14 < 30:
                score += 2  # Oversold
                indicators.append("RSI oversold (<30)")
            elif 40 <= data.rsi_14 <= 60:
                score += 1  # Neutral momentum
                indicators.append("RSI neutral")

        # MACD Analysis
        if all(
            x is not None for x in [data.macd, data.macd_signal, data.macd_histogram]
        ):
            if data.macd > data.macd_signal:
                score += 1
                indicators.append("MACD bullish crossover")
            else:
                score -= 1
                indicators.append("MACD bearish crossover")

            if data.macd_histogram > 0:
                score += 0.5
                indicators.append("MACD histogram positive")

        # Price momentum
        if data.price_change_1m is not None:
            if data.price_change_1m > 10:
                score += 1.5
                indicators.append(f"Strong 1M momentum (+{data.price_change_1m:.1f}%)")
            elif data.price_change_1m < -10:
                score -= 1.5
                indicators.append(f"Weak 1M momentum ({data.price_change_1m:.1f}%)")

        # Convert score to signal
        if score >= 2:
            strength = SignalStrength.BUY
        elif score >= 1:
            strength = SignalStrength.HOLD
        elif score <= -2:
            strength = SignalStrength.SELL
        elif score <= -1:
            strength = SignalStrength.HOLD
        else:
            strength = SignalStrength.HOLD

        confidence = min(abs(score) / 3.0, 1.0)

        return TechnicalSignal(
            signal_type=SignalType.MOMENTUM,
            strength=strength,
            confidence=confidence,
            description="Momentum analysis based on RSI, MACD, and price momentum",
            supporting_indicators=indicators,
        )

    def _analyze_trend(self, data: TechnicalAnalysis) -> Optional[TechnicalSignal]:
        """Analyze trend indicators."""
        indicators = []
        score = 0

        current_price = data.current_price
        if not current_price:
            return None

        # Moving average analysis
        if data.sma_20 and data.sma_50:
            if data.sma_20 > data.sma_50:
                score += 1
                indicators.append("SMA20 > SMA50 (short-term uptrend)")
            else:
                score -= 1
                indicators.append("SMA20 < SMA50 (short-term downtrend)")

        if data.sma_50 and data.sma_200:
            if data.sma_50 > data.sma_200:
                score += 1.5
                indicators.append("SMA50 > SMA200 (golden cross signal)")
            else:
                score -= 1.5
                indicators.append("SMA50 < SMA200 (death cross signal)")

        # Price vs moving averages
        if data.sma_20:
            if current_price > data.sma_20:
                score += 0.5
                indicators.append("Price above SMA20")
            else:
                score -= 0.5
                indicators.append("Price below SMA20")

        # EMA analysis
        if data.ema_12 and data.ema_26:
            if data.ema_12 > data.ema_26:
                score += 1
                indicators.append("EMA12 > EMA26 (bullish)")
            else:
                score -= 1
                indicators.append("EMA12 < EMA26 (bearish)")

        # Convert score to signal
        if score >= 2:
            strength = SignalStrength.BUY
        elif score >= 1:
            strength = SignalStrength.HOLD
        elif score <= -2:
            strength = SignalStrength.SELL
        elif score <= -1:
            strength = SignalStrength.HOLD
        else:
            strength = SignalStrength.HOLD

        confidence = min(abs(score) / 4.0, 1.0)

        return TechnicalSignal(
            signal_type=SignalType.TREND,
            strength=strength,
            confidence=confidence,
            description="Trend analysis based on moving averages",
            supporting_indicators=indicators,
        )

    def _analyze_volume(self, data: TechnicalAnalysis) -> Optional[TechnicalSignal]:
        """Analyze volume patterns."""
        indicators = []
        score = 0

        if data.volume_avg_10 is not None and data.price_change_1d is not None:
            # Volume confirmation
            if data.price_change_1d > 2 and data.volume_avg_10 > 0:
                score += 1
                indicators.append("Price up with volume confirmation")
            elif data.price_change_1d < -2 and data.volume_avg_10 > 0:
                score -= 1
                indicators.append("Price down with volume confirmation")

        if not indicators:
            return None

        strength = SignalStrength.HOLD
        if score > 0:
            strength = SignalStrength.BUY
        elif score < 0:
            strength = SignalStrength.SELL

        confidence = min(abs(score) / 2.0, 0.7)  # Volume signals are less reliable

        return TechnicalSignal(
            signal_type=SignalType.VOLUME,
            strength=strength,
            confidence=confidence,
            description="Volume analysis for price confirmation",
            supporting_indicators=indicators,
        )

    def _analyze_support_resistance(
        self, data: TechnicalAnalysis
    ) -> Optional[TechnicalSignal]:
        """Analyze support and resistance levels."""
        indicators = []
        score = 0

        current_price = data.current_price
        if not current_price:
            return None

        # Bollinger Bands analysis
        if all(
            x is not None
            for x in [data.bollinger_upper, data.bollinger_lower, data.bollinger_middle]
        ):
            if current_price > data.bollinger_upper:
                score -= 1
                indicators.append("Price above Bollinger upper band (overbought)")
            elif current_price < data.bollinger_lower:
                score += 1
                indicators.append("Price below Bollinger lower band (oversold)")
            elif current_price > data.bollinger_middle:
                score += 0.5
                indicators.append("Price above Bollinger middle (bullish)")

        if not indicators:
            return None

        strength = SignalStrength.HOLD
        if score >= 1:
            strength = SignalStrength.BUY
        elif score <= -1:
            strength = SignalStrength.SELL

        confidence = min(abs(score) / 2.0, 0.8)

        return TechnicalSignal(
            signal_type=SignalType.SUPPORT_RESISTANCE,
            strength=strength,
            confidence=confidence,
            description="Support/resistance analysis using Bollinger Bands",
            supporting_indicators=indicators,
        )

    def _analyze_reversal_patterns(
        self, data: TechnicalAnalysis
    ) -> Optional[TechnicalSignal]:
        """Analyze potential reversal patterns."""
        indicators = []
        score = 0

        # Stochastic analysis for reversals
        if data.stochastic_k is not None and data.stochastic_d is not None:
            if data.stochastic_k > 80 and data.stochastic_d > 80:
                score -= 1
                indicators.append("Stochastic in overbought territory")
            elif data.stochastic_k < 20 and data.stochastic_d < 20:
                score += 1
                indicators.append("Stochastic in oversold territory")

        # RSI divergence (simplified)
        if data.rsi_14 is not None:
            if data.rsi_14 > 75:
                score -= 0.5
                indicators.append("RSI extremely overbought")
            elif data.rsi_14 < 25:
                score += 0.5
                indicators.append("RSI extremely oversold")

        if not indicators:
            return None

        strength = SignalStrength.HOLD
        if score >= 1:
            strength = SignalStrength.BUY
        elif score <= -1:
            strength = SignalStrength.SELL

        confidence = min(abs(score) / 2.0, 0.6)  # Reversal signals are less certain

        return TechnicalSignal(
            signal_type=SignalType.REVERSAL,
            strength=strength,
            confidence=confidence,
            description="Reversal pattern analysis",
            supporting_indicators=indicators,
        )

    def _calculate_overall_signal(
        self, signals: List[TechnicalSignal]
    ) -> Tuple[SignalStrength, float]:
        """Calculate overall signal from individual signals."""
        if not signals:
            return SignalStrength.HOLD, 0.0

        # Weight different signal types
        weights = {
            SignalType.TREND: 0.3,
            SignalType.MOMENTUM: 0.25,
            SignalType.VOLUME: 0.2,
            SignalType.SUPPORT_RESISTANCE: 0.15,
            SignalType.REVERSAL: 0.1,
        }

        signal_scores = {
            SignalStrength.STRONG_BUY: 2,
            SignalStrength.BUY: 1,
            SignalStrength.HOLD: 0,
            SignalStrength.SELL: -1,
            SignalStrength.STRONG_SELL: -2,
        }

        weighted_score = 0.0
        total_weight = 0.0
        total_confidence = 0.0

        for signal in signals:
            weight = weights.get(signal.signal_type, 0.1)
            score = signal_scores.get(signal.strength, 0)

            weighted_score += score * weight * signal.confidence
            total_weight += weight
            total_confidence += signal.confidence

        if total_weight == 0:
            return SignalStrength.HOLD, 0.0

        final_score = weighted_score / total_weight
        avg_confidence = total_confidence / len(signals)

        # Convert score back to signal strength
        if final_score >= 1.5:
            return SignalStrength.STRONG_BUY, avg_confidence
        elif final_score >= 0.5:
            return SignalStrength.BUY, avg_confidence
        elif final_score <= -1.5:
            return SignalStrength.STRONG_SELL, avg_confidence
        elif final_score <= -0.5:
            return SignalStrength.SELL, avg_confidence
        else:
            return SignalStrength.HOLD, avg_confidence

    def _identify_key_levels(self, data: TechnicalAnalysis) -> Dict[str, float]:
        """Identify key support and resistance levels."""
        levels = {}

        if data.current_price:
            levels["current"] = data.current_price

        if data.sma_200:
            levels["sma_200"] = data.sma_200

        if data.bollinger_upper:
            levels["resistance"] = data.bollinger_upper

        if data.bollinger_lower:
            levels["support"] = data.bollinger_lower

        return levels

    def _assess_risk(
        self, data: TechnicalAnalysis, signals: List[TechnicalSignal]
    ) -> str:
        """Assess the risk level of the current position."""
        risk_factors = []

        if data.rsi_14 and (data.rsi_14 > 70 or data.rsi_14 < 30):
            risk_factors.append("extreme RSI levels")

        if data.price_change_1m and abs(data.price_change_1m) > 20:
            risk_factors.append("high volatility")

        # Check for conflicting signals
        buy_signals = sum(
            1
            for s in signals
            if s.strength in [SignalStrength.BUY, SignalStrength.STRONG_BUY]
        )
        sell_signals = sum(
            1
            for s in signals
            if s.strength in [SignalStrength.SELL, SignalStrength.STRONG_SELL]
        )

        if buy_signals > 0 and sell_signals > 0:
            risk_factors.append("conflicting signals")

        if len(risk_factors) >= 2:
            return "High Risk"
        elif len(risk_factors) == 1:
            return "Medium Risk"
        else:
            return "Low Risk"

    def _determine_time_horizon(self, signals: List[TechnicalSignal]) -> str:
        """Determine the recommended time horizon."""
        # Simplified logic - can be made more sophisticated
        momentum_signals = [s for s in signals if s.signal_type == SignalType.MOMENTUM]
        trend_signals = [s for s in signals if s.signal_type == SignalType.TREND]

        if momentum_signals and any(s.confidence > 0.7 for s in momentum_signals):
            return "Short-term (1-4 weeks)"
        elif trend_signals and any(s.confidence > 0.7 for s in trend_signals):
            return "Medium-term (1-3 months)"
        else:
            return "Long-term (3+ months)"


# Global instance
_technical_engine: Optional[TechnicalAnalysisEngine] = None


def get_technical_analysis_engine() -> TechnicalAnalysisEngine:
    """Get the global technical analysis engine instance."""
    global _technical_engine
    if _technical_engine is None:
        _technical_engine = TechnicalAnalysisEngine()
    return _technical_engine


def set_technical_analysis_engine(engine: TechnicalAnalysisEngine) -> None:
    """Set the global technical analysis engine instance."""
    global _technical_engine
    _technical_engine = engine
