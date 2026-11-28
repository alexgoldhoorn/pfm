# pfm MCP server and the internet-facing gateway

> Moved out of `CLAUDE.md` to keep it short. CLAUDE.md points here.

The MCP server is a small Python package, `mcp/pfm_mcp/` (one module per area in
`pfm_mcp/tools/`, shared HTTP helpers in `client.py`, settings in `config.py`).
`mcp/server.py` is only a compatibility shim: it is symlinked from
`~/mcp/pfm/server.py` and imported live by `~/mcp/remote_gateway/server.py`, the
always-on, internet-facing `mcp-remote-gateway.service`
(host in `~/mcp/CLAUDE.md`), which re-exports most of its tools. A change here
affects that surface too — see `~/mcp/CLAUDE.md`, "Exception:
remote_gateway/server.py".

- The tools are read-only wrappers over the HTTP API. Every test lives in
  `mcp/tests/` and runs without pfm's own dependencies:
  `cd mcp && python3 -m pytest -q` (patch `pfm_mcp.client._get`; a conftest
  blocks the real network). A new tool uses `@tool` from `pfm_mcp`, goes in the
  matching `tools/` module and in the README's tool table
  (`tests/test_readme.py` fails if the table and the registered tools differ;
  `tests/test_package.py` pins the tool count).
- Configuration, first hit wins: `PFM_URL` / `PFM_API_KEY`, then the dotenv file
  named by `PFM_ENV_FILE`, then the legacy `~/repos/pfm/.env.local`, then
  `http://127.0.0.1:8000` with an empty key (warning on stderr, never stdout).
  The package must not import `portf_manager`/`portf_server`.
- When an API response changes shape, update the matching tool. Tools read fields
  with `.get(..., 0)`, so a renamed field silently becomes zero, and a test that
  pins the old field names stays green.
- `budget_summary` mirrors `months_without_activity`: no verdicts for a month with
  nothing imported, and it flags a month still in progress.
- `wash_sale_check` applies art. 33.5 LIRPF: a 2-month window, or 1 year for
  unlisted fund units, matched against FIFO lots from `/analytics/tax-report` for
  this year and last. "Bought recently" lists only positions still held.
  ⚠️ **Don't trust `asset_type` to spot a fund** — no live asset is typed
  `mutual_fund` (heuristic imports store index funds as `stock`; Indexa's funds
  are `etf` on exchange `"Funds"`). The server-side rule (`services/wash_sale.py`) counts an asset as a
  fund if it is typed `mutual_fund`, OR its exchange is `"Funds"`, OR it isn't
  `etf` and its name contains fund/idx/fondo/fonds — erring towards the year.
  Crypto, cash and the synthetic `MINTOS` asset are excluded.
- `spending_transactions` returns raw bank descriptions (tax ids, account
  numbers, other people's names) and is on the gateway since 2026-10-08, so
  keep it read-only and don't widen what each row prints without asking.
- A new tool must be registered in three places or it stays local-only: the
  gateway (`~/mcp/remote_gateway/server.py`, including `TOOL_SPECIALIST`), the
  finance agent's tools line (`~/.claude/agents/finance.md`), and the persona
  (`~/agents/finance/persona.md`).
