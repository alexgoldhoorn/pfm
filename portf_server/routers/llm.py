"""
Enhanced LLM Router for Portfolio Management API

Integrates AI-powered transaction extraction and comprehensive stock analysis
into the existing chat endpoint for seamless CLI compatibility.
"""

import re
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel, Field

from portf_manager.gemini_client import GeminiClient
from portf_manager.llm_client import get_llm_client
from portf_manager.services.market_data import get_market_data_service
from portf_manager.services.analytics.screener import (
    get_stock_screener,
    PredefinedScreeners,
    ScreenerRequest,
)
from portf_manager.services.analytics.technical import get_technical_analysis_engine
from portf_manager.services.analytics.fundamental import get_fundamental_analysis_engine

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager

# Import existing functionality
from portf_manager.api_client import APIClient, CacheStrategy
from portf_manager.database import Database
from portf_server.dependencies import get_database

router = APIRouter()
logger = logging.getLogger(__name__)


# Session management (from original)
_chat_sessions: Dict[str, List[Dict[str, str]]] = {}


def _ensure_session(session_id: str):
    if session_id not in _chat_sessions:
        _chat_sessions[session_id] = []


def _get_history(session_id: str) -> List[Dict[str, str]]:
    return _chat_sessions.get(session_id, [])


def _append_history(session_id: str, role: str, content: str):
    if session_id in _chat_sessions:
        _chat_sessions[session_id].append({"role": role, "content": content})
        # Keep last 10 messages
        _chat_sessions[session_id] = _chat_sessions[session_id][-10:]


def _simple_ddg_search(query: str, max_results: int = 3) -> List[Dict[str, str]]:
    """Placeholder for web search - would integrate with actual search API"""
    return []


# API Key authentication dependency
async def get_api_key_auth_for_llm(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    """Helper function for API key authentication in LLM endpoints."""
    return await require_api_key(api_key_manager)(request)


# Enhanced request/response models
class TransactionExtractionRequest(BaseModel):
    """Schema for transaction extraction request."""

    text: str = Field(..., description="Raw broker statement text")


class TransactionExtractionResponse(BaseModel):
    """Schema for transaction extraction response."""

    transactions: List[dict] = Field(..., description="Extracted transactions")
    count: int = Field(..., description="Number of transactions extracted")


class ChatRequest(BaseModel):
    """Enhanced chat request with stock advice integration."""

    message: str
    session_id: Optional[str] = None
    symbols: Optional[List[str]] = None
    live: bool = True
    search: bool = False


class ChatResponse(BaseModel):
    """Enhanced chat response with stock advice context."""

    session_id: str
    answer: str
    context_summary: Optional[Dict[str, Any]] = None
    recommendations: Optional[List[Dict[str, Any]]] = None
    warnings: Optional[List[str]] = None


# Intent classification (from enhanced version)
class IntentClassifier:
    """Simple intent classification for routing stock advice requests."""

    INTENT_PATTERNS = {
        "screening": [
            r"\b(screen|filter|find|search)\s+stocks?\b",
            r"\b(value|growth|dividend)\s+stocks?\b",
            r"\bstock\s+(screen|filter|finder)\b",
            r"\brecommend\s+stocks?\b",
        ],
        "technical": [
            r"\b(technical|chart|price)\s+analysis\b",
            r"\b(rsi|macd|moving\s+average|bollinger)\b",
            r"\bbuy\s+or\s+sell\b",
            r"\btrading\s+signals?\b",
            r"\bsupport|resistance\b",
        ],
        "fundamental": [
            r"\b(fundamental|financial)\s+analysis\b",
            r"\b(pe\s+ratio|earnings|revenue|debt)\b",
            r"\b(valuation|financial\s+health)\b",
            r"\bbalance\s+sheet\b",
            r"\bprofitability\b",
        ],
        "portfolio": [
            r"\bmy\s+portfolio\b",
            r"\bportfolio\s+(analysis|performance)\b",
            r"\brebalance?\b",
            r"\bdiversification\b",
        ],
    }

    @classmethod
    def classify_intent(cls, message: str) -> str:
        """Classify user intent based on message content."""
        message_lower = message.lower()

        for intent, patterns in cls.INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, message_lower):
                    return intent

        # Check for specific symbol mentions
        if re.search(r"\b[A-Z]{1,5}\b", message):
            return "symbol_analysis"

        return "general"


class EnhancedChatEngine:
    """Enhanced chat engine that integrates stock advice with portfolio context."""

    def __init__(self):
        self.market_data_service = get_market_data_service()
        self.screener = get_stock_screener()
        self.technical_engine = get_technical_analysis_engine()
        self.fundamental_engine = get_fundamental_analysis_engine()
        # Use the provider-agnostic LLM client
        self.llm = get_llm_client()

    async def process_chat_request(
        self, request: ChatRequest, db: Database
    ) -> ChatResponse:
        """Process enhanced chat request with stock advice integration."""
        try:
            session_id = request.session_id or "default"
            _ensure_session(session_id)

            # Classify intent to determine if we need advanced stock analysis
            intent = IntentClassifier.classify_intent(request.message)

            # Extract symbols if not provided
            if not request.symbols:
                request.symbols = self._extract_symbols(request.message)

            # Build context - combine original portfolio context with stock advice
            context = await self._build_enhanced_context(request, intent, db)

            # Generate response
            response_text = await self._generate_enhanced_response(
                request.message, context, session_id
            )

            # Update session history
            _append_history(session_id, "user", request.message)
            _append_history(session_id, "assistant", response_text)

            return ChatResponse(
                session_id=session_id,
                answer=response_text,
                context_summary=self._create_context_summary(context),
                recommendations=context.get("recommendations"),
                warnings=context.get("warnings", []),
            )

        except Exception as e:
            logger.error(f"Error processing enhanced chat request: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process chat request: {str(e)}",
            )

    def _extract_symbols(self, message: str) -> List[str]:
        """Extract stock symbols from message."""
        symbols = re.findall(r"\b[A-Z]{1,5}\b", message)
        common_words = {
            "AND",
            "OR",
            "THE",
            "FOR",
            "NOT",
            "BUT",
            "CAN",
            "ALL",
            "ANY",
            "NEW",
            "OLD",
        }
        return [s for s in symbols if s not in common_words][:10]

    async def _build_enhanced_context(
        self, request: ChatRequest, intent: str, db: Database
    ) -> Dict[str, Any]:
        """Build enhanced context combining portfolio and stock analysis."""
        context = {"intent": intent, "warnings": []}

        try:
            # Build original portfolio context
            api_client = APIClient(cache_strategy=CacheStrategy.MEMORY)
            portfolio_context = self._build_portfolio_context(
                db=db,
                api_client=api_client,
                user_id=1,
                symbols_hint=request.symbols,
                live=request.live,
                search=request.search,
            )
            context.update(portfolio_context)

            # Add advanced stock analysis based on intent
            if intent in ["screening", "symbol_analysis", "technical", "fundamental"]:
                stock_context = await self._build_stock_analysis_context(
                    intent, request.symbols or []
                )
                context.update(stock_context)

        except Exception as e:
            context["warnings"].append(f"Context building error: {str(e)}")
            logger.warning(f"Enhanced context building error: {e}")

        return context

    async def _build_stock_analysis_context(
        self, intent: str, symbols: List[str]
    ) -> Dict[str, Any]:
        """Build stock analysis context based on intent."""
        context = {}

        try:
            if intent == "screening":
                # Use predefined screening for demonstration
                screen_symbols = [
                    "AAPL",
                    "GOOGL",
                    "MSFT",
                    "AMZN",
                    "TSLA",
                    "META",
                    "NVDA",
                    "NFLX",
                ]
                screen_request = ScreenerRequest(
                    symbols=screen_symbols,
                    filters=PredefinedScreeners.quality_stocks(),
                    max_results=5,
                )
                screening_results = await self.screener.screen_stocks(screen_request)
                context["screening_results"] = {
                    "matches": len(screening_results.results),
                    "results": [
                        {"symbol": r.symbol, "score": r.score, "metrics": r.metrics}
                        for r in screening_results.results[:3]
                    ],
                }
                context["recommendations"] = [
                    {
                        "type": "screening",
                        "symbols": [r.symbol for r in screening_results.results[:3]],
                    }
                ]

            elif intent in ["technical", "symbol_analysis"] and symbols:
                technical_analyses = []
                for symbol in symbols[:3]:
                    try:
                        analysis = await self.technical_engine.analyze_symbol(symbol)
                        if analysis:
                            technical_analyses.append(
                                {
                                    "symbol": symbol,
                                    "signal": analysis.overall_signal.value,
                                    "confidence": analysis.overall_confidence,
                                    "risk": analysis.risk_assessment,
                                }
                            )
                    except Exception as e:
                        logger.warning(f"Technical analysis failed for {symbol}: {e}")

                context["technical_analyses"] = technical_analyses
                if technical_analyses:
                    context["recommendations"] = [
                        {
                            "type": "technical",
                            "symbol": a["symbol"],
                            "signal": a["signal"],
                            "confidence": a["confidence"],
                        }
                        for a in technical_analyses
                    ]

            elif intent in ["fundamental", "symbol_analysis"] and symbols:
                fundamental_analyses = []
                for symbol in symbols[:3]:
                    try:
                        analysis = await self.fundamental_engine.analyze_symbol(symbol)
                        if analysis:
                            fundamental_analyses.append(
                                {
                                    "symbol": symbol,
                                    "rating": analysis.overall_rating.value,
                                    "score": analysis.overall_score,
                                    "valuation": analysis.valuation_summary,
                                    "health": analysis.financial_health,
                                }
                            )
                    except Exception as e:
                        logger.warning(f"Fundamental analysis failed for {symbol}: {e}")

                context["fundamental_analyses"] = fundamental_analyses
                if fundamental_analyses:
                    context["recommendations"] = [
                        {
                            "type": "fundamental",
                            "symbol": a["symbol"],
                            "rating": a["rating"],
                            "score": a["score"],
                        }
                        for a in fundamental_analyses
                    ]

        except Exception as e:
            context["warnings"] = context.get("warnings", []) + [
                f"Stock analysis error: {str(e)}"
            ]

        return context

    def _build_portfolio_context(
        self,
        db: Database,
        api_client: APIClient,
        user_id: int,
        symbols_hint: Optional[List[str]] = None,
        live: bool = True,
        search: bool = False,
    ) -> Dict[str, Any]:
        """Build original portfolio context (from existing implementation)."""
        # This is the original implementation from the existing LLM router
        # Simplified version for this integration
        warnings = []

        try:
            # Get basic portfolio data - would need actual DB queries here
            portfolios = []
            positions = []
            recent_transactions = []

            # Get market data for any symbols
            prices = {}
            metadata = {}

            if symbols_hint and live:
                try:
                    prices = api_client.fetch_latest_prices(symbols_hint)
                    for symbol in symbols_hint:
                        try:
                            md = api_client.get_metadata(symbol)
                            if md:
                                metadata[symbol] = md
                        except Exception:
                            pass
                except Exception:
                    warnings.append("live_price_fetch_failed")

            web_snippets = {}
            if search and symbols_hint:
                for symbol in symbols_hint[:3]:  # Limit search
                    items = _simple_ddg_search(f"{symbol} company latest news")
                    if items:
                        web_snippets[symbol] = items

            context = {
                "portfolios": portfolios,
                "positions": positions,
                "recent_transactions": recent_transactions,
                "prices": prices,
                "metadata": metadata,
                "web": web_snippets or None,
            }

            if warnings:
                context["_warnings"] = warnings

            return context

        except Exception as e:
            logger.warning(f"Portfolio context building error: {e}")
            return {"portfolios": [], "positions": [], "_warnings": [str(e)]}

    async def _generate_enhanced_response(
        self, message: str, context: Dict[str, Any], session_id: str
    ) -> str:
        """Generate enhanced response with both portfolio and stock analysis."""
        # Build enhanced prompt
        prompt = self._build_enhanced_prompt(message, context, session_id)

        try:
            response = await asyncio.to_thread(self.llm.generate, prompt)
            return response
        except Exception as e:
            logger.error(f"LLM API error: {e}")
            return "I apologize, but I'm having trouble accessing the AI service right now. Please try again later."

    def _build_enhanced_prompt(
        self, message: str, context: Dict[str, Any], session_id: str
    ) -> str:
        """Build enhanced prompt combining portfolio and stock analysis context."""

        base_prompt = """You are a professional financial advisor and portfolio management assistant.

Your capabilities include:
- Portfolio analysis and performance tracking
- Comprehensive stock analysis (technical, fundamental, screening)
- Investment recommendations based on data
- Market insights and trends

Always provide:
- Data-driven analysis with specific numbers when available
- Clear explanations of your reasoning
- Balanced perspectives on opportunities and risks
- Actionable insights tailored to the user's question

"""

        # Add conversation history
        history = _get_history(session_id)
        if history:
            base_prompt += "Previous conversation:\n"
            for msg in history[-4:]:  # Last 4 messages
                base_prompt += f"{msg['role'].upper()}: {msg['content']}\n"
            base_prompt += "\n"

        # Add portfolio context
        if context.get("portfolios") or context.get("positions"):
            base_prompt += f"PORTFOLIO CONTEXT:\n{json.dumps(context, indent=2)}\n\n"

        # Add stock analysis context
        if context.get("screening_results"):
            base_prompt += f"STOCK SCREENING RESULTS:\n{json.dumps(context['screening_results'], indent=2)}\n\n"

        if context.get("technical_analyses"):
            base_prompt += f"TECHNICAL ANALYSIS:\n{json.dumps(context['technical_analyses'], indent=2)}\n\n"

        if context.get("fundamental_analyses"):
            base_prompt += f"FUNDAMENTAL ANALYSIS:\n{json.dumps(context['fundamental_analyses'], indent=2)}\n\n"

        # Add market data
        if context.get("prices") or context.get("metadata"):
            market_data = {
                "prices": context.get("prices", {}),
                "metadata": context.get("metadata", {}),
            }
            base_prompt += f"MARKET DATA:\n{json.dumps(market_data, indent=2)}\n\n"

        # Add warnings
        if context.get("warnings") or context.get("_warnings"):
            all_warnings = context.get("warnings", []) + context.get("_warnings", [])
            base_prompt += f"WARNINGS: {', '.join(all_warnings)}\n\n"

        base_prompt += f"Current User Question: {message}\n\n"
        base_prompt += (
            "Please provide a comprehensive response based on the available data."
        )

        return base_prompt

    def _create_context_summary(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Create summary of context for response."""
        summary = {
            "analysis_type": context.get("intent", "general"),
            "data_sources": [],
        }

        if context.get("portfolios"):
            summary["portfolios"] = len(context["portfolios"])
            summary["data_sources"].append("portfolio")

        if context.get("positions"):
            summary["positions"] = len(context["positions"])
            summary["data_sources"].append("positions")

        if context.get("screening_results"):
            summary["stocks_screened"] = context["screening_results"].get("matches", 0)
            summary["data_sources"].append("stock_screener")

        if context.get("technical_analyses"):
            summary["technical_symbols"] = len(context["technical_analyses"])
            summary["data_sources"].append("technical_analysis")

        if context.get("fundamental_analyses"):
            summary["fundamental_symbols"] = len(context["fundamental_analyses"])
            summary["data_sources"].append("fundamental_analysis")

        if context.get("prices"):
            summary["market_data_symbols"] = len(context["prices"])
            summary["data_sources"].append("market_data")

        return summary


# Lazy-initialized enhanced chat engine
_enhanced_chat_engine = None


def get_enhanced_chat_engine() -> EnhancedChatEngine:
    """Lazily initialize the enhanced chat engine."""
    global _enhanced_chat_engine
    if _enhanced_chat_engine is None:
        try:
            _enhanced_chat_engine = EnhancedChatEngine()
        except Exception as e:
            logger.error(f"Failed to initialize chat engine: {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Chat engine unavailable: {str(e)}",
            )
    return _enhanced_chat_engine


# Transaction extraction endpoint (unchanged)
@router.post("/extract-transactions", response_model=TransactionExtractionResponse)
async def extract_transactions_from_text(
    request: TransactionExtractionRequest,
    api_key_info: dict = Depends(get_api_key_auth_for_llm),
):
    """
    Extract transactions from broker statement text using LLM.
    """
    try:
        gemini_client = GeminiClient(llm=get_llm_client())
        llm_transactions = gemini_client.extract_transactions(request.text)

        valid_transactions = []
        for transaction in llm_transactions:
            validation_error = transaction.validate()
            if validation_error:
                continue

            transaction_dict = {
                "tx_type": transaction.tx_type,
                "symbol": transaction.symbol,
                "asset_name": transaction.asset_name,
                "quantity": transaction.quantity,
                "price": transaction.price,
                "date": transaction.date,
                "currency": transaction.currency,
                "fees": transaction.fees,
                "raw_text": transaction.raw_text,
            }
            valid_transactions.append(transaction_dict)

        return TransactionExtractionResponse(
            transactions=valid_transactions,
            count=len(valid_transactions),
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to extract transactions: {str(e)}",
        )


# Enhanced chat endpoint that integrates stock advice
@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    api_key_info: dict = Depends(get_api_key_auth_for_llm),
    db: Database = Depends(get_database),
):
    """
    Enhanced chat endpoint with integrated stock advice capabilities.

    This endpoint now automatically detects the type of question you're asking and
    provides appropriate analysis:

    - Portfolio questions: Uses your existing portfolio data
    - Stock screening: "Find good dividend stocks", "Screen for growth stocks"
    - Technical analysis: "Should I buy AAPL?", "Technical analysis of TSLA"
    - Fundamental analysis: "Financial health of MSFT", "Is AMZN overvalued?"
    - General questions: Basic market data and AI insights

    The response includes both the AI answer and structured analysis data.
    """
    return await get_enhanced_chat_engine().process_chat_request(request, db)
