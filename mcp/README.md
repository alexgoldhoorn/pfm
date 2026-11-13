# pfm-mcp

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server for
a [pfm](https://github.com/alexgoldhoorn/pfm) (Portfolio Manager) API. It lets
Claude (Desktop, Code, or any MCP client) answer questions about your holdings,
performance, tax, spending, net worth and the next-euro allocator. It is a thin
HTTP client: the only dependency is the `mcp` package, and it never writes.

## Install and configure

**Requirements:** a pfm API from 2026-10-19 onward (PROJECT_STATUS v2.5.83: `/analytics/wash-sale`, `/allocate/*`, `/retirement/*`). Older servers answer those tools with a 404, shown as "Error fetching …".

You need a running pfm API and one of its API keys. Point the server at them
with two environment variables:

| Variable | Meaning | Default |
|---|---|---|
| `PFM_URL` | Base URL of the pfm API | `http://127.0.0.1:8000` |
| `PFM_API_KEY` | API key, sent as `X-API-Key` | empty (a warning goes to stderr) |

If they are not set, the server also reads a dotenv file named by
`PFM_ENV_FILE` (keys `PORTF_SERVER_URL`, `SERVER_API_KEY`), then the legacy
`~/repos/pfm/.env.local`, then the defaults above. The first hit wins, for the
URL and the key separately.

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pfm": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/alexgoldhoorn/pfm#subdirectory=mcp",
        "pfm-mcp"
      ],
      "env": {
        "PFM_URL": "http://127.0.0.1:8000",
        "PFM_API_KEY": "your-api-key"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add pfm \
  -e PFM_URL=http://127.0.0.1:8000 \
  -e PFM_API_KEY=your-api-key \
  -- uvx --from "git+https://github.com/alexgoldhoorn/pfm#subdirectory=mcp" pfm-mcp
```

### Any other stdio client

```json
{
  "command": "uvx",
  "args": ["--from", "git+https://github.com/alexgoldhoorn/pfm#subdirectory=mcp", "pfm-mcp"],
  "env": { "PFM_URL": "http://127.0.0.1:8000", "PFM_API_KEY": "your-api-key" }
}
```

From a checkout, `pip install ./mcp` (or `uvx --from ./mcp pfm-mcp`) works too.
Never print to stdout from a tool: stdout is the protocol channel.

## Tools

| Tool | What it answers |
|---|---|
| `portfolio_holdings` | Current holdings with P&L, filter and sort |
| `list_portfolios` | Broker accounts tracked in pfm |
| `list_assets` | Tracked assets with their latest price |
| `list_transactions` | Transactions with flexible filters |
| `quote` | Live market quotes for symbols |
| `watchlist` | Watchlist with buy-below targets |
| `bookings` | Cash deposits and withdrawals |
| `performance` | Return in EUR against a benchmark |
| `dividends` | Dividend income by year and symbol |
| `diversification` | Exposure by class, region and sector, funds looked through |
| `risk` | Volatility, drawdown and related risk metrics |
| `fundamentals` | Key fundamentals for any ticker |
| `portfolio_health` | Scored health analysis and recommendations |
| `rebalance_analysis` | Allocation drift against the targets |
| `data_freshness` | How current the price data is |
| `research_lookup` | Research snapshot for one ticker |
| `research_compare` | Saved research compared across tickers |
| `tax_report` | Spanish IRPF summary with realised lots |
| `tax_estimate` | Quick IRPF estimate for a year |
| `wash_sale_check` | Antiaplicación (wash-sale) windows and blocked losses |
| `budget_summary` | Active budget, planned vs actual |
| `spending_summary` | Bank spending, income and trend |
| `spending_transactions` | Individual bank transactions |
| `networth` | Total net worth with accounts and balance sheet |
| `goals` | Financial goals and progress |
| `retirement_readiness` | Retirement scenarios in today's euros |
| `action_items` | Open items pfm has detected |
| `next_euro` | What to do with new money |
| `deployable_cash` | How much cash can be invested now |
| `allocation_targets` | Target vs current allocation |
| `allocation_candidates` | What the next euro could buy |
| `allocator_settings` | The allocator's configuration |
| `return_attribution` | What drove the return: gain per holding, type and broker |
| `position_caps` | Position, sector and asset-type caps vs current weights: headroom in € and pp, ok/near/breached, plus the implicit single-stock cap |
| `stress_test` | Shock the held portfolio through the fund look-through (presets: equity_bear, ai_bubble, semis_crash, usd_down, rates_up, crypto_winter, stagflation): loss, top losers, net worth before/after, retirement verdict after |
| `year_end_tax` | Year-end IRPF planner: realised gains and income, harvestable losses with the tax each would save, 2-month-rule blocks, loss carry-forward from prior years, days to 31 December |
| `decisions` | The decision journal with the price move since each call |
| `research_scorecard` | How good past research targets were, per report and in total |
| `scenario_job_loss` | What if work income stops: months the money lasts at three spending levels, on cash and with investments sold, and what to cut first |
| `scenario_income_change` | What if income changes: monthly net and savings rate before/after, goal fit, wealth-plan impact, cuts that close the gap |
| `scenario_house_purchase` | What if a home is bought: cash needed, funding from cash and investments, mortgage, debt ratio, what is left |
| `scenario_market_crash` | What if markets crash: loss on the held portfolio and months to get back to today's value, with past crashes for reference |

`tests/test_readme.py` fails if this table and the registered tools differ.

## Development

```bash
cd mcp
python3 -m pytest -q
```

The tests need only `pytest` and `mcp` (1.x — `pyproject.toml` pins
`mcp>=1.2,<2`; with mcp 2.x installed system-wide, collection fails on
`from mcp.server.fastmcp import FastMCP`, so run them in an environment with
the pinned version, e.g. `uv run --no-project --with pytest --with "mcp<2"
python -m pytest -q`); they patch `pfm_mcp.client._get` and refuse any real
network call. Tool modules live in `pfm_mcp/tools/`, one per
area; a tool is registered by the `@tool` decorator from `pfm_mcp` and attached
to the FastMCP object in `pfm_mcp/server.py`.

`mcp/server.py` is a compatibility shim for hosts that load the server through
a symlink (`from pfm.server import networth`) or run it as a script.
