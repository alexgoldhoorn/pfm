"""
Portfolio-Aware Multi-Agent Integration

Extends the multi-agent system to automatically inject portfolio context
into chat sessions and analysis workflows.
"""

import os
import sys
import asyncio
import logging
from typing import Dict, Any

from .portfolio_snapshot import get_portfolio_context_for_chat


class PortfolioAwareAgent:
    """
    Multi-agent wrapper that automatically includes portfolio context
    """

    def __init__(self, db_path: str, max_tokens: int = 4000):
        self.db_path = db_path
        self.max_tokens = max_tokens
        self.logger = logging.getLogger(__name__)

        # Initialize multi-agent system if available
        self.multi_agent_available = False
        self.orchestrator = None
        self._setup_multi_agent()

    def _setup_multi_agent(self):
        """Setup multi-agent system if available"""
        try:
            sys.path.append(
                os.path.join(
                    os.path.dirname(__file__),
                    "..",
                    "multi_agent_analysis",
                    "implementation",
                )
            )
            from agent_orchestration.orchestrator import AgentOrchestrator
            from agent_orchestration.config import AgentOrchestrationConfig

            config = AgentOrchestrationConfig()
            self.orchestrator = AgentOrchestrator(config)
            self.multi_agent_available = True
            self.logger.info(
                "✅ Multi-agent system initialized with portfolio awareness"
            )

        except ImportError as e:
            self.logger.warning(f"Multi-agent system not available: {e}")
            self.multi_agent_available = False

    def get_portfolio_context(self) -> str:
        """Get current portfolio context for injection into prompts"""
        return get_portfolio_context_for_chat(self.db_path, self.max_tokens)

    def enhance_prompt_with_portfolio(self, user_prompt: str) -> str:
        """
        Enhance user prompt with portfolio context
        """
        portfolio_context = self.get_portfolio_context()

        if "Error loading portfolio data" in portfolio_context:
            self.logger.error("Failed to load portfolio data for context")
            return user_prompt

        enhanced_prompt = f"""{portfolio_context}

---

User Query: {user_prompt}

Please analyze my query in the context of my current portfolio shown above. Reference specific holdings, positions, and recent transactions when relevant to provide personalized advice."""

        return enhanced_prompt

    async def chat_with_portfolio_context(
        self, user_query: str, analysis_type: str = "general"
    ) -> Dict[str, Any]:
        """
        Process a chat query with full portfolio context
        """
        if not self.multi_agent_available:
            return {
                "status": "error",
                "message": "Multi-agent system not available",
                "suggestion": "Portfolio context available, but need multi-agent system for analysis",
            }

        # Enhance query with portfolio context
        enhanced_query = self.enhance_prompt_with_portfolio(user_query)

        try:
            # Create workflow request based on analysis type
            workflow_type = self._determine_workflow_type(analysis_type, user_query)

            # This would integrate with your existing orchestrator
            result = await self._execute_portfolio_aware_workflow(
                enhanced_query, workflow_type
            )

            return {
                "status": "success",
                "result": result,
                "portfolio_context_included": True,
            }

        except Exception as e:
            self.logger.error(f"Error in portfolio-aware chat: {e}")
            return {"status": "error", "message": str(e)}

    def _determine_workflow_type(self, analysis_type: str, user_query: str) -> str:
        """Determine appropriate workflow type based on query"""
        query_lower = user_query.lower()

        if any(word in query_lower for word in ["buy", "sell", "trade", "position"]):
            return "trading_analysis"
        elif any(
            word in query_lower for word in ["risk", "diversification", "allocation"]
        ):
            return "risk_analysis"
        elif any(word in query_lower for word in ["performance", "return", "profit"]):
            return "performance_analysis"
        else:
            return "general_analysis"

    async def _execute_portfolio_aware_workflow(
        self, enhanced_query: str, workflow_type: str
    ) -> Dict[str, Any]:
        """Execute workflow with portfolio context"""
        # This is a placeholder for the actual workflow execution
        # In a real implementation, this would use your orchestrator

        # For now, return a mock result
        return {
            "analysis": f"Portfolio-aware {workflow_type} completed",
            "query": (
                enhanced_query[:200] + "..."
                if len(enhanced_query) > 200
                else enhanced_query
            ),
            "timestamp": asyncio.get_event_loop().time(),
        }

    def get_context_summary(self) -> Dict[str, Any]:
        """Get summary of current portfolio context"""
        try:
            from .portfolio_snapshot import create_snapshot_from_db_path

            snapshot = create_snapshot_from_db_path(self.db_path)
            portfolio_data = snapshot.build_compact_json()

            return {
                "status": "success",
                "summary": portfolio_data["summary"],
                "positions_count": len(portfolio_data["positions"]),
                "recent_transactions": len(portfolio_data["recent_activity"]),
                "estimated_tokens": snapshot.estimate_token_count(portfolio_data),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}


def create_portfolio_aware_chat_session(db_path: str) -> PortfolioAwareAgent:
    """Create a new portfolio-aware chat session"""
    return PortfolioAwareAgent(db_path)


# Console integration functions
def run_portfolio_chat_session(db_path: str):
    """Run an interactive portfolio-aware chat session"""
    agent = create_portfolio_aware_chat_session(db_path)

    print("🤖 Portfolio-Aware Chat Agent")
    print("=" * 50)

    # Show portfolio summary
    context_summary = agent.get_context_summary()
    if context_summary["status"] == "success":
        summary = context_summary["summary"]
        print(
            f"📊 Portfolio loaded: {summary['positions_count']} positions, ${summary['total_invested']:,.2f} invested"
        )
        print(f"💰 Cash balance: ${summary['cash_balance']:,.2f}")
        print(f"📈 Recent transactions: {context_summary['recent_transactions']}")
        print(f"🔤 Context tokens: ~{context_summary['estimated_tokens']}")
    else:
        print(f"⚠️  Portfolio context error: {context_summary['message']}")

    print("\nType your questions about your portfolio. Type 'quit' to exit.")
    print("-" * 50)

    while True:
        try:
            user_input = input("\n💬 You: ").strip()

            if user_input.lower() in ["quit", "exit", "q"]:
                print("👋 Goodbye!")
                break

            if not user_input:
                continue

            print("🤔 Analyzing with portfolio context...")

            # For now, show enhanced prompt (until full multi-agent integration)
            enhanced = agent.enhance_prompt_with_portfolio(user_input)
            print("\n📝 Enhanced prompt preview:")
            print("-" * 30)
            print(enhanced[:500] + "..." if len(enhanced) > 500 else enhanced)
            print("-" * 30)

            if agent.multi_agent_available:
                # Execute with multi-agent system
                result = asyncio.run(agent.chat_with_portfolio_context(user_input))
                print(f"\n🎯 Analysis result: {result}")
            else:
                print(
                    "\n💡 Multi-agent system not available. Enhanced prompt ready for manual use."
                )

        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    else:
        db_path = "portfolio.db"

    run_portfolio_chat_session(db_path)
