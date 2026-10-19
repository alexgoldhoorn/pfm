# pfm MCP server as a shareable package — design and plan

**Date:** 2026-10-19
**Status:** approved in chat (owner: "ok do all" on the five-point outline)

## Why

The owner does much of his money thinking in a chat with Claude, and wants the
pfm tools usable by others too. Today the MCP server lives in the pfm repo
(`mcp/server.py`) but only works on his machine: credentials come from a
hard-coded `~/repos/pfm/.env.local`, it is one ~2,100-line file with 33 tools,
it runs on system Python reached through a `~/mcp` symlink, most of its tests
live outside the public repo (`~/mcp/pfm/test_server.py`), and since the
allocator work it imports pfm internals (`portf_manager`) through a
`sys.path` insertion, which an independent install cannot do.

## Goals

- `uvx --from "git+https://github.com/alexgoldhoorn/pfm#subdirectory=mcp" pfm-mcp`
  (or `pip install` of that subdirectory) gives a working server whose only
  dependency is the `mcp` package. Others point it at their own pfm with two
  environment variables.
- One module per area, so a tool is easy to find and the file stays small.
- A pure HTTP client again: no import of `portf_manager`.
- All MCP tests in the public repo, runnable without pfm's own dependencies.
- Nothing changes for the owner's setup: the `~/mcp/pfm/server.py` symlink,
  the local gateway (`importlib.import_module("pfm.server")`), the phone gateway
  (`from pfm.server import (...)`) and Hermes (runs the file as a script) keep
  working with the same tool names and output.

## Non-goals

- Write tools (pfm's MCP stays read-only by convention).
- Publishing to PyPI, a Docker image, OAuth.
- Moving the owner's non-pfm tools (Indexa, homelab) — they stay in `~/mcp`.

## Design

**Layout** (all under `mcp/`, which becomes its own small Python project):

```
mcp/
  pyproject.toml          # name "pfm-mcp", requires-python >=3.11, deps ["mcp>=1.2"],
                          # [project.scripts] pfm-mcp = "pfm_mcp.server:main"
  README.md               # install, config, Claude Desktop / Claude Code / Hermes snippets, tool table
  pfm_mcp/
    __init__.py
    config.py             # SERVER_URL / API_KEY resolution (below)
    client.py             # _get, _fmt_currency, _na and the other shared formatting helpers
    server.py             # mcp = FastMCP("pfm"); imports every tools module; main() runs stdio
    tools/
      portfolio.py        # portfolio_holdings, list_portfolios, list_assets, list_transactions, bookings, quote, watchlist
      analytics.py        # performance, dividends, diversification, risk, fundamentals, portfolio_health, rebalance_analysis, data_freshness
      research.py         # research_lookup, research_compare
      tax.py              # tax_report, tax_estimate, wash_sale_check
      spending.py         # budget_summary, spending_summary, spending_transactions
      networth.py         # networth, goals, retirement_readiness, action_items
      allocate.py         # next_euro, deployable_cash, allocation_targets, allocation_candidates, allocator_settings
  tests/
    conftest.py           # patches pfm_mcp.client._get; no network
    test_<area>.py        # moved from ~/mcp/pfm/test_server.py and mcp/test_server_local.py
    test_readme.py        # every registered tool appears in README's table, and nothing extra
  server.py               # back-compat shim (below)
```

Tools register on the single `mcp` object from `pfm_mcp.server` (each tools
module does `from pfm_mcp.server import mcp` lazily via a small registry in
`pfm_mcp/__init__` to avoid an import cycle — builder's call on the exact
mechanism, as long as `import pfm_mcp.server` registers all 33 tools and each
tool function stays importable by name).

**Configuration** (`config.py`), first hit wins:
1. `PFM_URL` and `PFM_API_KEY` environment variables;
2. a dotenv file named by `PFM_ENV_FILE` (keys `PORTF_SERVER_URL`, `SERVER_API_KEY`);
3. the legacy `~/repos/pfm/.env.local` if it exists (the owner's setup);
4. defaults `http://127.0.0.1:8000` and an empty key, with a one-line stderr
   warning when the key is empty (never stdout: Hermes delivers stdout).

**Antiaplicación as an API endpoint.** New `GET /api/v1/analytics/wash-sale?symbol=`
in `portf_server/routers/analytics.py` returns `wash_sale.analyse(...)`'s dict
(`blocked`, `no_rebuy`, `recent_buys`) computed in-process from the same
realised lots the tool fetched before, plus the holdings it used. The
`wash_sale_check` tool calls it and keeps its exact output text. The
`sys.path.append` and every `portf_manager` import leave the MCP code.

**Back-compat shim** `mcp/server.py`: puts its own real directory on
`sys.path` (append; only `pfm_mcp` lives there), does
`from pfm_mcp.server import mcp, main` and `from pfm_mcp.tools.<area> import *`
for every area so `from pfm.server import networth, next_euro, ...` keeps
working, and runs `main()` under `__main__`. `~/mcp/pfm/test_server.py` is
replaced by a short smoke test that imports the shim and asserts the 33 names
exist (the real tests now live in the repo).

**Docs:** `mcp/README.md` rewritten; pfm `CLAUDE.md` section "Exposed via the
internet-facing MCP gateway" updated (package path, tests location, the
README-sync test); `PROJECT_STATUS.md` line.

## Plan (subagent-driven, Sonnet seats)

1. **Wash-sale endpoint.** Add the endpoint (test with invented lots in
   `tests/unit/test_wash_sale.py`), switch the tool to it, prove the tool's
   output is byte-identical on the existing MCP fixtures, remove the
   `sys.path.append`. Commit.
2. **Package split.** Create the layout above by moving code (no behaviour
   change), config resolution with tests (env, env file, legacy, default +
   stderr warning), the shim, the moved tests (privacy hook must pass: replace
   any realistic figure with an invented one), the README-sync test,
   `mcp/pyproject.toml`, README. Verify: `cd mcp && uv run --with mcp pytest -q`
   (or `python3 -m pytest -q` on system Python), `python3 mcp/server.py` starts
   and lists 33 tools over stdio (smoke via `mcp` client or a list-tools call),
   `uvx --from ./mcp pfm-mcp` resolves and starts. Commit.
3. **Owner-side switch (outside the repo, after merge).** `~/mcp/pfm/test_server.py`
   → smoke test; run the `~/mcp` pfm, remote_gateway and local_gateway suites;
   restart the phone gateway; check `mine_status` shows pfm loaded; Hermes
   job using pfm still imports.
4. **Review**: one whole-branch review (most capable model), one fix wave.

## Left to the builder

- The registration mechanism that avoids the import cycle.
- Exact grouping of the private helpers between `client.py` and the tool modules.
- README wording.
