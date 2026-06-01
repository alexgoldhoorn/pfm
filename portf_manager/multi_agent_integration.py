"""
Multi-Agent Analysis Integration for portf_track CLI
"""

import os
import sys


def is_multi_agent_available() -> bool:
    """Check if multi-agent system is available"""
    try:
        sys.path.append(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "multi_agent_analysis",
                "implementation",
            )
        )

        return True
    except ImportError:
        return False


async def run_multi_agent_console():
    """Launch the multi-agent interactive console"""
    try:
        sys.path.append(
            os.path.join(os.path.dirname(__file__), "..", "multi_agent_analysis")
        )
        from console_demo import main as console_main

        await console_main()
    except Exception as e:
        print(f"Error launching multi-agent console: {e}")
        return False
    return True


def get_integration_instructions():
    """Instructions for adding multi-agent commands to main CLI"""
    return """
To integrate multi-agent system with main portf_track CLI, add this to cli.py:

1. Import the integration:
   from .multi_agent_integration import is_multi_agent_available, run_multi_agent_console

2. Add command option:
   parser.add_argument('--multi-agent', action='store_true', help='Launch multi-agent analysis console')

3. Handle the command:
   if args.multi_agent and is_multi_agent_available():
       asyncio.run(run_multi_agent_console())
       return
"""
