"""pfm MCP server: read-only tools over a pfm API.

Tool modules decorate their functions with :func:`tool`, which only records
them; ``pfm_mcp.server`` registers every recorded function on its FastMCP
object. That keeps the tool modules free of an import cycle with the server.
"""

from typing import Callable

TOOLS: list[Callable] = []


def tool(fn: Callable) -> Callable:
    """Record a function as an MCP tool and return it unchanged.

    Args:
        fn: The tool function.

    Returns:
        The same function, so it stays importable and callable by name.
    """
    TOOLS.append(fn)
    return fn
