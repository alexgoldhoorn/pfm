"""pfm (Portfolio Manager) MCP server: read-only tools over the pfm API.

Run with ``pfm-mcp`` (stdio). See the README for configuration.
"""

from mcp.server.fastmcp import FastMCP

from pfm_mcp import TOOLS, config
from pfm_mcp.tools import (  # noqa: F401  (importing registers the tools)
    allocate,
    analytics,
    networth,
    portfolio,
    research,
    spending,
    tax,
)

mcp = FastMCP("pfm")

for _fn in TOOLS:
    mcp.tool()(_fn)


def main() -> None:
    """Run the server over stdio."""
    # Resolve the settings now so a missing API key warns once at startup.
    config.settings()
    mcp.run()


if __name__ == "__main__":
    main()
