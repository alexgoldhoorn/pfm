# pfm MCP server

Model Context Protocol server exposing the portfolio (holdings, portfolios,
assets, transactions, IRPF tax summary) as tools for Claude Code / Hermes.
It is a thin client over the local pfm FastAPI server.

- **Canonical source**: `mcp/server.py` (this directory — versioned with the API).
- **Reference**: `~/mcp/pfm/server.py` is a **symlink** to this file. The Claude
  registration in `~/.claude.json` (`mcpServers.pfm`) points at the `~/mcp` path,
  so it keeps working unchanged through the symlink.
- **Credentials**: read at startup from `~/repos/pfm/.env.local`
  (`SERVER_URL` ← `PORTF_SERVER_URL`, `API_KEY` ← `SERVER_API_KEY`).
- **Runtime**: system `python3` with the `mcp` package (`pip install mcp`).

## Tools

| Tool | Backing endpoint |
|---|---|
| `portfolio_holdings` | `/api/v1/portfolios/holdings` |
| `list_portfolios` | `/api/v1/portfolios/` |
| `list_assets` | `/api/v1/assets/` |
| `list_transactions` | `/api/v1/transactions/` |
| `tax_report` | `/api/v1/analytics/tax-report` + `/api/v1/analytics/tax-estimate` |

## Quick test

```bash
cd mcp && python3 -c "import server; print(server.tax_report())"
```

The `@mcp.tool()` decorator returns the original function, so tools are callable
directly for smoke tests without starting the stdio loop.
