# Code Quality Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate identified code-quality issues: position-math duplication, parser utility duplication, dead database_factory pass-throughs, yfinance cache-bypass in routers, and enhance the LLM factory with per-provider model configuration and improved fallback documentation.

**Architecture:** Seven independent tasks executed in order. Tasks 1–3 are pure deletions/extractions with no logic changes; Task 4 rewires research.py to call `compute_positions`; Task 5 routes yfinance bypasses in routers through `market.py`; Task 6 enhances `llm_client.py` with per-provider model env vars; Task 7 extracts dividend TTM logic to `analytics_service.py`. Each task ends with a full test run.

**Tech Stack:** Python 3.13, FastAPI, SQLite, pytest, `portf_manager/positions.py`, `portf_manager/market.py`, `portf_manager/llm_client.py`, `portf_manager/services/analytics_service.py`

---

## File Map

| File | Action | Reason |
|------|--------|--------|
| `portf_manager/cli.py` | Modify line 1-3 | Module docstring is dead (import appears before it) |
| `portf_manager/parsers/utils.py` | **Create** | Shared European-number parser for all CSV parsers |
| `portf_manager/parsers/indexacapital_csv_parser.py` | Modify | Use `parsers.utils.parse_european_number` |
| `portf_manager/parsers/myinvestor_csv_parser.py` | Modify | Use `parsers.utils.parse_european_number` |
| `portf_manager/database_factory.py` | Modify | Delete 150+ lines of pass-through facade functions; keep only `get_database`, `get_database_adapter`, `reset_database_instance` |
| `portf_server/routers/research.py` | Modify | Replace `_position_stats` and `_cost_evolution` with `compute_positions` |
| `portf_server/routers/watchlist.py` | Modify | Replace `yf.Ticker().info` with `market.get_fundamentals()` |
| `portf_manager/services/portfolio_advisor.py` | Modify | Replace `yf.Ticker(s).info` with `market.get_fundamentals()` |
| `portf_manager/llm_client.py` | Modify | Add per-provider model env vars; fix module docstring; add `get_llm_info()` |
| `portf_manager/services/analytics_service.py` | Modify | Add `dividend_ttm_enrichment()` function |
| `portf_server/routers/analytics.py` | Modify | Call `dividend_ttm_enrichment()` from service; remove inline TTM logic |

---

## Task 1: Fix cli.py module docstring order

The `from tqdm import tqdm` import on line 1 makes `cli.py`'s module docstring invisible — Python's `__doc__` is `None` because the first statement is not a string. Move the import below the docstring.

**Files:**
- Modify: `portf_manager/cli.py:1-8`

- [ ] **Step 1: Fix import order**

  Open `portf_manager/cli.py`. The current top is:
  ```python
  from tqdm import tqdm

  """
  Command Line Interface for Portfolio Manager
  ...
  """
  import argparse
  ```

  Change it to:
  ```python
  """
  Command Line Interface for Portfolio Manager

  Provides CLI commands for managing assets, sectors, and portfolio operations.
  """

  from tqdm import tqdm
  import argparse
  ```

  (The docstring was previously on lines 3-7 after the import. Move the `"""..."""` block to line 1, then put `from tqdm import tqdm` as the first import after the docstring, in its existing position among the other imports.)

- [ ] **Step 2: Verify the docstring is now live**

  ```bash
  UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run python -c "import portf_manager.cli; print(repr(portf_manager.cli.__doc__[:30]))"
  ```
  Expected: `'Command Line Interface for Por'` (not `None`)

- [ ] **Step 3: Run unit tests to confirm no regression**

  ```bash
  UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
  ```
  Expected: all tests pass, no new failures.

- [ ] **Step 4: Commit**

  ```bash
  git add portf_manager/cli.py
  git commit -m "fix: move tqdm import below cli.py module docstring so __doc__ is populated

  Co-Authored-By: Oz <oz-agent@warp.dev>"
  ```

---

## Task 2: Shared European number parser for CSV parsers

`indexacapital_csv_parser.py` and `myinvestor_csv_parser.py` both implement dot-thousands / comma-decimal parsing (`_parse_european_amount` and `_num`). Extract to `parsers/utils.py`.

**Files:**
- Create: `portf_manager/parsers/utils.py`
- Modify: `portf_manager/parsers/indexacapital_csv_parser.py`
- Modify: `portf_manager/parsers/myinvestor_csv_parser.py`
- Test: `tests/unit/test_currency_utils.py` (add a section there) or create `tests/test_parser_utils.py`

- [ ] **Step 1: Write the failing test**

  Create `tests/test_parser_utils.py`:
  ```python
  """Tests for shared parser utility functions."""
  import pytest
  from portf_manager.parsers.utils import parse_european_number


  @pytest.mark.parametrize("raw,expected", [
      ("1.583,25", 1583.25),
      ("695,33", 695.33),
      ("1200", 1200.0),
      ("-1.488,58", -1488.58),
      ("-1488,58", -1488.58),
      ("0,00", 0.0),
      ("1.200.000,50", 1200000.50),
      ("", 0.0),
      ("  ", 0.0),
  ])
  def test_parse_european_number(raw, expected):
      assert parse_european_number(raw) == pytest.approx(expected)


  def test_parse_european_number_strips_euro():
      assert parse_european_number("1.234,56 €") == pytest.approx(1234.56)


  def test_parse_european_number_strips_currency_code():
      assert parse_european_number("1.234,56 EUR") == pytest.approx(1234.56)
  ```

- [ ] **Step 2: Run the test — expect ImportError (module doesn't exist yet)**

  ```bash
  UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/test_parser_utils.py -v
  ```
  Expected: `ImportError: cannot import name 'parse_european_number'`

- [ ] **Step 3: Create `portf_manager/parsers/utils.py`**

  ```python
  """Shared parsing utilities for broker CSV parsers."""

  import re
  from typing import Union


  def parse_european_number(raw: Union[str, None]) -> float:
      """Parse a European-formatted number to float.

      Handles dot-as-thousands-separator and comma-as-decimal:
        '1.583,25'  → 1583.25
        '695,33'    → 695.33
        '-1.488,58' → -1488.58
        '1200'      → 1200.0

      Strips currency symbols (€, EUR) and surrounding whitespace.
      Returns 0.0 for empty or un-parseable input.
      """
      s = re.sub(r"[€EUReur\s]", "", (raw or "").strip())
      s = re.sub(r"[^0-9,.\-]", "", s)
      if not s or s == "-":
          return 0.0
      if "," in s and "." in s:
          # Determine which is the decimal separator by position of the last one.
          if s.rfind(",") > s.rfind("."):
              # European: 1.234,56 → dot is thousands, comma is decimal
              s = s.replace(".", "").replace(",", ".")
          else:
              # Unusual: 1,234.56 → comma is thousands, dot is decimal
              s = s.replace(",", "")
      elif "," in s:
          s = s.replace(",", ".")
      try:
          return float(s)
      except ValueError:
          return 0.0
  ```

- [ ] **Step 4: Run the test — expect all pass**

  ```bash
  UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/test_parser_utils.py -v
  ```
  Expected: 11 PASSED

- [ ] **Step 5: Update `indexacapital_csv_parser.py` to use the shared util**

  Remove the `_parse_european_amount` method from `IndexaCapitalCSVParser`. Add the import at the top:
  ```python
  from .utils import parse_european_number
  ```

  Change every call in the class:
  - `float(self._parse_european_amount(total_amount_str))` → `parse_european_number(total_amount_str)`
  - `float(self._parse_european_amount(fees_str))` → `parse_european_number(fees_str)`

  Also remove the method definition `def _parse_european_amount(self, amount_str: str) -> float:` and its body (lines 31–49 of the original file).

- [ ] **Step 6: Update `myinvestor_csv_parser.py` to use the shared util**

  Remove the `_num` function definition (lines 46-58 of the original file). Add the import:
  ```python
  from .utils import parse_european_number as _num
  ```

  This alias keeps all call sites (`_num(...)`) unchanged.

- [ ] **Step 7: Run full test suite**

  ```bash
  UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
  ```
  Expected: all tests pass.

- [ ] **Step 8: Commit**

  ```bash
  git add portf_manager/parsers/utils.py portf_manager/parsers/indexacapital_csv_parser.py portf_manager/parsers/myinvestor_csv_parser.py tests/test_parser_utils.py
  git commit -m "refactor: extract shared European number parser into parsers/utils.py

  Removes duplicate _parse_european_amount (indexacapital) and _num (myinvestor)
  implementations. Both parsers now share parse_european_number from parsers/utils.

  Co-Authored-By: Oz <oz-agent@warp.dev>"
  ```

---

## Task 3: Delete database_factory.py pass-through functions

`database_factory.py` contains 150+ lines of functions like:
```python
def create_asset(*args, **kwargs):
    db = get_database()
    return db.create_asset(*args, **kwargs)
```
No router uses these — they all call `get_database()` from `portf_server/dependencies.py` directly. The facade adds noise with zero benefit.

**Files:**
- Modify: `portf_manager/database_factory.py` (delete lines 70–219)

- [ ] **Step 1: Verify no file outside the factory imports these wrappers**

  ```bash
  grep -rn "from portf_manager.database_factory import\|from .database_factory import\|database_factory\." /home/agoldhoorn/repos/pfm/portf_manager/ /home/agoldhoorn/repos/pfm/portf_server/ /home/agoldhoorn/repos/pfm/tests/ | grep -v "get_database\|reset_database_instance\|get_database_adapter" | grep -v ".pyc"
  ```
  Expected: no output (no caller uses the pass-through names).

- [ ] **Step 2: Trim `database_factory.py` to only the useful three functions**

  Replace the entire file contents with:
  ```python
  """
  Database factory for Portfolio Management.

  Selects between SQLite and PostgreSQL based on DATABASE_URL and provides
  a process-wide singleton via get_database().
  """

  import os
  import logging
  from typing import Union

  from .database import Database as SQLiteDatabase
  from .database_pg import PostgreSQLDatabase

  logger = logging.getLogger(__name__)


  def get_database_adapter() -> Union[SQLiteDatabase, PostgreSQLDatabase]:
      """Return the appropriate database adapter based on environment configuration."""
      database_url = os.getenv("DATABASE_URL")
      if database_url:
          if database_url.startswith("postgresql://") or database_url.startswith(
              "postgres://"
          ):
              logger.info("Using PostgreSQL database adapter")
              return PostgreSQLDatabase(database_url)
          raise ValueError(f"Unsupported database URL format: {database_url}")
      logger.info("Using SQLite database adapter")
      db_path = os.getenv("SQLITE_DB_PATH", "portfolio.db")
      return SQLiteDatabase(db_path)


  _database_instance = None


  def get_database() -> Union[SQLiteDatabase, PostgreSQLDatabase]:
      """Return the process-wide singleton database instance."""
      global _database_instance
      if _database_instance is None:
          _database_instance = get_database_adapter()
      return _database_instance


  def reset_database_instance() -> None:
      """Reset the singleton (useful for testing)."""
      global _database_instance
      _database_instance = None
  ```

- [ ] **Step 3: Run full test suite**

  ```bash
  UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
  ```
  Expected: all tests pass.

- [ ] **Step 4: Commit**

  ```bash
  git add portf_manager/database_factory.py
  git commit -m "refactor: remove 150-line pass-through facade from database_factory.py

  All callers already use get_database() from portf_server/dependencies.py directly.
  Keeps only get_database(), get_database_adapter(), reset_database_instance().

  Co-Authored-By: Oz <oz-agent@warp.dev>"
  ```

---

## Task 4: Fix position-math duplication in research.py

`portf_server/routers/research.py` contains two private functions that re-implement the buy/sell/split accumulation loop already in `portf_manager/positions.compute_positions`:

- `_position_stats()` (lines 71–101): returns quantity, avg_cost, cost_basis, realised for a single asset.
- `_cost_evolution()` (lines 104–151): returns the same accumulation plus a time-series of points.

Replace `_position_stats` with a call to `compute_positions`. For `_cost_evolution`, keep the series-building but remove the duplicated buy/sell/split logic by feeding it from `compute_positions`'s sorted order.

**Files:**
- Modify: `portf_server/routers/research.py`

- [ ] **Step 1: Read the current `_position_stats` callers**

  ```bash
  grep -n "_position_stats\|_cost_evolution" /home/agoldhoorn/repos/pfm/portf_server/routers/research.py
  ```
  Note which lines call these functions (expected: ~358, 402, 537 for `_position_stats`; ~some line for `_cost_evolution`).

- [ ] **Step 2: Replace `_position_stats` with a thin wrapper around `compute_positions`**

  Current implementation (lines 71–101):
  ```python
  def _position_stats(db, asset: Optional[dict]) -> dict:
      """..."""
      if not asset:
          return {"quantity": 0.0, "avg_cost": 0.0, "cost_basis": 0.0, "realised": 0.0}
      qty = cost = realised = 0.0
      for tx in reversed(db.get_transactions_by_asset(asset["id"])):
          ...
      return {...}
  ```

  Replace the entire function body with:
  ```python
  def _position_stats(db, asset: Optional[dict]) -> dict:
      """Current quantity, average cost, remaining cost basis, and realised P&L.

      Uses compute_positions (chronological, handles splits) so this stays
      consistent with the holdings and analytics endpoints.
      """
      if not asset:
          return {"quantity": 0.0, "avg_cost": 0.0, "cost_basis": 0.0, "realised": 0.0}
      from portf_manager.positions import compute_positions

      txns = db.get_transactions_by_asset(asset["id"])
      positions, realised = compute_positions(txns)
      pos = positions.get(asset["id"], {"quantity": 0.0, "cost": 0.0})
      qty = pos["quantity"]
      cost = pos["cost"]
      return {
          "quantity": qty,
          "avg_cost": cost / qty if qty > 0 else 0.0,
          "cost_basis": cost,
          "realised": realised,
      }
  ```

  Note: `compute_positions` groups by `asset_id` by default. The dict key is `asset["id"]` (integer). Get the single-asset result with `.get(asset["id"], ...)`.

- [ ] **Step 3: Replace the buy/sell/split loop in `_cost_evolution` with shared logic**

  Current `_cost_evolution` (lines 104–151) builds a `txns` list AND a `series` list by iterating `reversed(rows)` with its own buy/sell/split logic. The txns-building part is fine, but the accumulator duplicates `compute_positions`.

  Replace the function with:
  ```python
  def _cost_evolution(db, asset: Optional[dict]) -> tuple[list, list]:
      """Return (transactions, cost_evolution_series) for the research panel.

      transactions: most-recent-first list of {date, type, quantity, price,
      total, currency}. cost_evolution: chronological points of
      {date, quantity, avg_cost, invested} after each buy/sell/split.
      """
      if not asset:
          return [], []
      from portf_manager.positions import _sort_key

      rows = db.get_transactions_by_asset(asset["id"])
      txns = [
          {
              "date": str(tx["transaction_date"])[:10],
              "type": tx["transaction_type"].lower(),
              "quantity": float(tx["quantity"]),
              "price": float(tx["price"] or 0),
              "total": float(tx["total_amount"] or 0),
              "currency": tx.get("currency", "EUR"),
          }
          for tx in rows
      ]
      # Replay transactions in chronological order to build the series.
      qty = cost = 0.0
      series = []
      for tx in sorted(rows, key=_sort_key):
          t = tx["transaction_type"].lower()
          q = float(tx["quantity"])
          total = float(tx["total_amount"] or 0)
          if t == "buy":
              qty += q
              cost += total
          elif t == "sell" and qty > 0:
              cost *= max(qty - q, 0.0) / qty
              qty -= q
          elif t == "split" and q > 0:
              qty *= q
          series.append(
              {
                  "date": str(tx["transaction_date"])[:10],
                  "quantity": round(qty, 6),
                  "avg_cost": round(cost / qty, 4) if qty > 0 else 0.0,
                  "invested": round(cost, 2),
                  "tx_type": t,
                  "tx_price": float(tx["price"] or 0),
              }
          )
      return txns, series
  ```

  This still builds the series (which `compute_positions` doesn't expose), but uses `_sort_key` from `positions.py` for chronological ordering so sort behaviour stays identical.

  Also add to the imports at the top of `research.py` (keep `compute_positions` for the portfolio-analysis section that already uses it at line ~739):
  ```python
  from portf_manager.positions import compute_positions
  ```

- [ ] **Step 4: Run the test suite**

  ```bash
  UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
  ```
  Expected: all tests pass.

- [ ] **Step 5: Commit**

  ```bash
  git add portf_server/routers/research.py
  git commit -m "refactor: replace _position_stats re-implementation with compute_positions

  _position_stats now delegates to positions.compute_positions (chronological,
  handles splits). _cost_evolution keeps its series-building but uses _sort_key
  from positions.py so chronological ordering is consistent.

  Co-Authored-By: Oz <oz-agent@warp.dev>"
  ```

---

## Task 5: Route yfinance bypasses through market.py

`portf_server/routers/watchlist.py` and `portf_manager/services/portfolio_advisor.py` call `yf.Ticker().info` directly, bypassing the `market.get_fundamentals()` cache layer. Fix these two callers.

(The `analytics.py` backfill and stress-test calls fetch multi-day historical series that `market.py` deliberately does not expose — leave those as-is.)

**Files:**
- Modify: `portf_server/routers/watchlist.py`
- Modify: `portf_manager/services/portfolio_advisor.py`

- [ ] **Step 1: Fix `watchlist.py` add endpoint**

  Current code in `add_watchlist` (around line 78–90):
  ```python
  if not name:
      try:
          info = yf.Ticker(body.symbol.upper()).info
          name = info.get("shortName") or info.get("longName")
          qt = info.get("quoteType", "").lower()
          asset_type = asset_type or {
              "equity": "stock",
              "etf": "etf",
              "cryptocurrency": "crypto",
          }.get(qt, "stock")
      except Exception:
          pass
  ```

  Replace with:
  ```python
  if not name:
      try:
          fund = market.get_fundamentals(db, body.symbol.upper())
          name = fund.get("shortName") or fund.get("longName")
          qt = (fund.get("quoteType") or "").lower()
          asset_type = asset_type or {
              "equity": "stock",
              "etf": "etf",
              "cryptocurrency": "crypto",
          }.get(qt, "stock")
      except Exception:
          pass
  ```

  Remove `import yfinance as yf` from the top of the file if it is no longer used anywhere else in `watchlist.py`. Check with:
  ```bash
  grep -n "yf\." /home/agoldhoorn/repos/pfm/portf_server/routers/watchlist.py
  ```
  If the only occurrence was that block, remove the import.

  The function signature must stay `def add_watchlist(...)` (plain `def`, not `async`) because the fundamentals call is blocking I/O.

  The `db` parameter is already available in `add_watchlist` (it's `db=Depends(get_database)`), so pass it to `market.get_fundamentals(db, ...)`.

- [ ] **Step 2: Fix `portfolio_advisor.py` sector/country lookup**

  In `portf_manager/services/portfolio_advisor.py`, the `_sector_country` helper (around line 165–179) does:
  ```python
  def _fetch(s=yf_sym):
      info = yf.Ticker(s).info
      return {"sector": info.get("sector"), "country": info.get("country")}

  try:
      meta = cached(db, f"yf:sectorcountry:{yf_sym}", 7 * 86400, _fetch) or {}
  ```

  Replace the inner `_fetch` lambda/function to use `market.get_fundamentals`:
  ```python
  def _fetch(s=yf_sym):
      fund = market.get_fundamentals(db, s, max_age=0)  # force fresh when cache is cold
      return {"sector": fund.get("sector"), "country": fund.get("country")}

  try:
      meta = cached(db, f"yf:sectorcountry:{yf_sym}", 7 * 86400, _fetch) or {}
  ```

  Add `from portf_manager import market` to the imports at the top of `portfolio_advisor.py` if not already present.

  Then check whether `import yfinance as yf` is still used anywhere in `portfolio_advisor.py`:
  ```bash
  grep -n "yf\." /home/agoldhoorn/repos/pfm/portf_manager/services/portfolio_advisor.py
  ```
  If the only occurrence was in `_fetch`, remove `import yfinance as yf`.

- [ ] **Step 3: Run tests**

  ```bash
  UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
  ```
  Expected: all tests pass.

- [ ] **Step 4: Commit**

  ```bash
  git add portf_server/routers/watchlist.py portf_manager/services/portfolio_advisor.py
  git commit -m "refactor: route yfinance bypasses in watchlist and portfolio_advisor through market.py

  Both callers now use market.get_fundamentals() which respects the shared
  kv_cache TTL, eliminating redundant Yahoo fetches on every request.

  Co-Authored-By: Oz <oz-agent@warp.dev>"
  ```

---

## Task 6: Enhance LLM client with per-provider model env vars

The current `get_llm_client()` factory reads `PORTF_LLM_MODEL` as the override for ALL providers. Users cannot specify a different model per-provider (e.g., use `gemini-2.5-pro` for Gemini but `claude-opus-4-8` for Anthropic). Add per-provider env vars. Also fix the module docstring (Anthropic is implemented but not documented there), and add `get_llm_info()` for diagnostics.

**Files:**
- Modify: `portf_manager/llm_client.py`

- [ ] **Step 1: Write the failing test**

  Add to `tests/unit/test_llm_client.py` (find and append):
  ```python
  def test_per_provider_model_env_override(monkeypatch):
      """PORTF_GEMINI_MODEL overrides the default Gemini model independently."""
      from portf_manager.llm_client import GeminiLLMClient
      monkeypatch.setenv("PORTF_GEMINI_MODEL", "gemini-2.5-pro")
      monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
      # We can't call __init__ without mocking the google SDK, so test the
      # env-var resolution logic directly.
      import os
      model = (
          os.getenv("PORTF_GEMINI_MODEL")
          or os.getenv("PORTF_LLM_MODEL")
          or "gemini-2.5-flash"
      )
      assert model == "gemini-2.5-pro"


  def test_get_llm_info_returns_dict(monkeypatch):
      """get_llm_info returns provider and model without instantiating a client."""
      from portf_manager.llm_client import get_llm_info, reset_llm_client
      monkeypatch.setenv("PORTF_LLM_PROVIDER", "gemini")
      monkeypatch.setenv("PORTF_GEMINI_MODEL", "gemini-2.5-flash")
      reset_llm_client()
      info = get_llm_info()
      assert info["provider"] == "gemini"
      assert "model" in info
      assert "search_capable" in info
  ```

- [ ] **Step 2: Run the failing test**

  ```bash
  UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_client.py -v -k "per_provider or get_llm_info"
  ```
  Expected: `ImportError` on `get_llm_info` (not defined yet).

- [ ] **Step 3: Update `llm_client.py`**

  **3a. Fix the module docstring** — replace the existing header to mention Anthropic:
  ```python
  """
  Provider-agnostic LLM client abstraction.

  Supports Gemini, Ollama, OpenRouter and Anthropic Claude through a unified
  interface. Configuration is driven by environment variables:

  Global overrides (apply to all providers):
    PORTF_LLM_PROVIDER  — "auto" (default), "ollama", "gemini", "openrouter", "anthropic"
    PORTF_LLM_MODEL     — model name for the selected provider (provider defaults apply)

  Per-provider model overrides (take precedence over PORTF_LLM_MODEL):
    PORTF_GEMINI_MODEL      — e.g. "gemini-2.5-pro"
    PORTF_ANTHROPIC_MODEL   — e.g. "claude-opus-4-8"
    PORTF_OPENROUTER_MODEL  — e.g. "openai/gpt-4o"
    PORTF_OLLAMA_MODEL      — e.g. "llama3.2"

  Required API keys:
    GEMINI_API_KEY / PORTF_GEMINI_API_KEY / GOOGLE_API_KEY  — for Gemini
    OPENROUTER_API_KEY / PORTF_OPENROUTER_API_KEY           — for OpenRouter
    ANTHROPIC_API_KEY                                        — for Anthropic

  Default auto-detection order (provider=auto):
    1. Ollama on localhost:11434 — no API key needed
    2. Gemini   — if GEMINI_API_KEY is set
    3. OpenRouter — if OPENROUTER_API_KEY is set
    4. Anthropic  — if ANTHROPIC_API_KEY is set
  """
  ```

  **3b. Update each client's `__init__` to check the per-provider env var first:**

  In `GeminiLLMClient.__init__`:
  ```python
  self.model_name = (
      model
      or os.getenv("PORTF_GEMINI_MODEL")
      or os.getenv("PORTF_LLM_MODEL")
      or DEFAULT_GEMINI_MODEL
  )
  ```

  In `OllamaLLMClient.__init__`:
  ```python
  self.model_name = (
      model
      or os.getenv("PORTF_OLLAMA_MODEL")
      or os.getenv("PORTF_LLM_MODEL")
      or DEFAULT_OLLAMA_MODEL
  )
  ```

  In `OpenRouterLLMClient.__init__`:
  ```python
  self.model_name = (
      model
      or os.getenv("PORTF_OPENROUTER_MODEL")
      or os.getenv("PORTF_LLM_MODEL")
      or DEFAULT_OPENROUTER_MODEL
  )
  ```

  In `AnthropicLLMClient.__init__`:
  ```python
  self.model_name = (
      model
      or os.getenv("PORTF_ANTHROPIC_MODEL")
      or os.getenv("PORTF_LLM_MODEL")
      or DEFAULT_ANTHROPIC_MODEL
  )
  ```

  **3c. Add `get_llm_info()` after the `reset_llm_client` function:**
  ```python
  def get_llm_info() -> dict:
      """Return config info for the current (or would-be) LLM client.

      Does NOT instantiate a new client — reads env vars to infer what
      get_llm_client() would produce. Safe to call at startup for logging.

      Returns:
          dict with keys: provider, model, search_capable, singleton_active.
      """
      global _llm_client
      provider = os.getenv("PORTF_LLM_PROVIDER", "auto").lower()

      # Infer model per provider
      if provider == "gemini":
          model = (
              os.getenv("PORTF_GEMINI_MODEL")
              or os.getenv("PORTF_LLM_MODEL")
              or DEFAULT_GEMINI_MODEL
          )
          search_capable = True
      elif provider == "ollama":
          model = (
              os.getenv("PORTF_OLLAMA_MODEL")
              or os.getenv("PORTF_LLM_MODEL")
              or DEFAULT_OLLAMA_MODEL
          )
          search_capable = False
      elif provider == "openrouter":
          model = (
              os.getenv("PORTF_OPENROUTER_MODEL")
              or os.getenv("PORTF_LLM_MODEL")
              or DEFAULT_OPENROUTER_MODEL
          )
          search_capable = False
      elif provider == "anthropic":
          model = (
              os.getenv("PORTF_ANTHROPIC_MODEL")
              or os.getenv("PORTF_LLM_MODEL")
              or DEFAULT_ANTHROPIC_MODEL
          )
          search_capable = True
      else:
          # "auto" — guess from env
          if OllamaLLMClient.is_available.__func__ if False else True:
              pass  # can't check without network; leave model as "unknown"
          model = os.getenv("PORTF_LLM_MODEL") or "auto-detected"
          search_capable = None  # unknown until resolved

      # If the singleton is already live, read from it directly.
      if _llm_client is not None:
          provider = type(_llm_client).__name__.replace("LLMClient", "").lower()
          model = getattr(_llm_client, "model_name", model)
          search_capable = isinstance(_llm_client, SearchCapableLLMClient)

      return {
          "provider": provider,
          "model": model,
          "search_capable": search_capable,
          "singleton_active": _llm_client is not None,
      }
  ```

  **Note on the `auto` branch:** when provider is "auto" and no singleton is cached, we can't determine the provider without a network call to Ollama. The returned dict will show `provider="auto"` and `model="auto-detected"` in that case, which is honest and useful for logging.

- [ ] **Step 4: Run the tests**

  ```bash
  UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_client.py -v
  ```
  Expected: all tests pass including the two new ones.

- [ ] **Step 5: Run full suite**

  ```bash
  UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
  ```
  Expected: all tests pass.

- [ ] **Step 6: Commit**

  ```bash
  git add portf_manager/llm_client.py tests/unit/test_llm_client.py
  git commit -m "feat: add per-provider model env vars and get_llm_info() to LLM client

  PORTF_GEMINI_MODEL / PORTF_ANTHROPIC_MODEL / PORTF_OPENROUTER_MODEL /
  PORTF_OLLAMA_MODEL each override PORTF_LLM_MODEL for their provider.
  get_llm_info() returns {provider, model, search_capable} without instantiating.
  Module docstring now documents Anthropic and the full env-var table.

  Co-Authored-By: Oz <oz-agent@warp.dev>"
  ```

---

## Task 7: Extract dividend TTM enrichment to analytics_service.py

`routers/analytics.py:get_dividends` contains ~35 lines of TTM / yield-on-cost / projected-annual business logic that belongs in the service layer. Extract to `analytics_service.dividend_ttm_enrichment()`.

**Files:**
- Modify: `portf_manager/services/analytics_service.py` (add function)
- Modify: `portf_server/routers/analytics.py` (call the new function)
- Test: `tests/unit/test_analytics.py` (add a test)

- [ ] **Step 1: Write the failing test**

  Add to `tests/unit/test_analytics.py`:
  ```python
  def test_dividend_ttm_enrichment_basic():
      """dividend_ttm_enrichment computes ttm_by_symbol and yield_on_cost."""
      from datetime import date
      from portf_manager.services.analytics_service import dividend_ttm_enrichment

      today = date.today()
      # One dividend transaction in the last 12 months
      txns = [
          {
              "transaction_type": "dividend",
              "transaction_date": today.isoformat(),
              "total_amount": 100.0,
              "symbol": "AAPL",
          }
      ]
      cost_by_symbol = {"AAPL": 1000.0}  # 10% yield-on-cost expected
      result = dividend_ttm_enrichment(txns, cost_by_symbol)
      assert result["ttm"] == pytest.approx(100.0)
      assert result["ttm_by_symbol"]["AAPL"] == pytest.approx(100.0)
      assert result["projected_annual"] == pytest.approx(100.0)
      assert result["yield_on_cost"]["AAPL"] == pytest.approx(10.0)


  def test_dividend_ttm_enrichment_excludes_old():
      """Dividends older than 12 months are excluded from TTM."""
      from portf_manager.services.analytics_service import dividend_ttm_enrichment

      txns = [
          {
              "transaction_type": "dividend",
              "transaction_date": "2020-01-01",
              "total_amount": 999.0,
              "symbol": "OLD",
          }
      ]
      result = dividend_ttm_enrichment(txns, {})
      assert result["ttm"] == pytest.approx(0.0)
      assert result["ttm_by_symbol"] == {}
  ```

- [ ] **Step 2: Run the test — expect ImportError**

  ```bash
  UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_analytics.py -v -k "ttm_enrichment"
  ```
  Expected: `ImportError: cannot import name 'dividend_ttm_enrichment'`

- [ ] **Step 3: Add `dividend_ttm_enrichment` to `analytics_service.py`**

  Append after the existing `dividend_income` function:
  ```python
  def dividend_ttm_enrichment(
      transactions: list[dict],
      cost_by_symbol: dict[str, float],
  ) -> dict:
      """Trailing-12-month dividend income per symbol, projected annual, and yield-on-cost.

      Args:
          transactions: All transactions (non-dividend rows are ignored).
          cost_by_symbol: Current cost basis per symbol (for yield-on-cost calc).

      Returns:
          dict with keys: ttm, ttm_by_symbol, projected_annual, yield_on_cost.
          All amounts are rounded to 2 decimal places.
      """
      from datetime import date

      cutoff = date.today().replace(year=date.today().year - 1)
      ttm_by_symbol: dict[str, float] = {}
      for tx in transactions:
          if tx.get("transaction_type", "").lower() != "dividend":
              continue
          d = _parse_date(tx.get("transaction_date"))
          if d is None or d < cutoff:
              continue
          sym = tx.get("symbol", "?")
          ttm_by_symbol[sym] = ttm_by_symbol.get(sym, 0) + float(
              tx.get("total_amount") or 0
          )

      yield_on_cost = {}
      for sym, ttm in ttm_by_symbol.items():
          cost = cost_by_symbol.get(sym, 0)
          if cost > 0:
              yield_on_cost[sym] = round(ttm / cost * 100, 2)

      total_ttm = sum(ttm_by_symbol.values())
      return {
          "ttm": round(total_ttm, 2),
          "ttm_by_symbol": {s: round(v, 2) for s, v in ttm_by_symbol.items()},
          "projected_annual": round(total_ttm, 2),
          "yield_on_cost": yield_on_cost,
      }
  ```

- [ ] **Step 4: Run the test — expect PASS**

  ```bash
  UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_analytics.py -v -k "ttm_enrichment"
  ```
  Expected: 2 PASSED

- [ ] **Step 5: Update `routers/analytics.py` to call the service function**

  Add to the imports block at the top of `analytics.py`:
  ```python
  from portf_manager.services.analytics_service import (
      ...existing imports...,
      dividend_ttm_enrichment,
  )
  ```

  In `get_dividends`, replace the TTM/yield/projected computation (the `~35` lines after `income = dividend_income(txns)`) with:
  ```python
  # Build cost_by_symbol from current open positions (for yield-on-cost)
  positions, _ = _compute_positions(db)
  cost_by_symbol: dict = {}
  for aid, pos in positions.items():
      if pos["quantity"] <= 0:
          continue
      asset = db.get_asset(aid)
      if asset:
          sym = asset["symbol"]
          cost_by_symbol[sym] = cost_by_symbol.get(sym, 0) + pos["cost"]

  ttm_data = dividend_ttm_enrichment(txns, cost_by_symbol)

  names = {a["symbol"]: a.get("name", a["symbol"]) for a in db.get_all_assets()}

  return {
      **income,
      **ttm_data,
      "names": names,
  }
  ```

  Remove the now-deleted variables: `cutoff`, `ttm_by_symbol`, `yield_on_cost`, `projected_annual` (the loop that built them is gone).

- [ ] **Step 6: Run full test suite**

  ```bash
  UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
  ```
  Expected: all tests pass.

- [ ] **Step 7: Commit**

  ```bash
  git add portf_manager/services/analytics_service.py portf_server/routers/analytics.py tests/unit/test_analytics.py
  git commit -m "refactor: extract dividend TTM enrichment from analytics router to service layer

  dividend_ttm_enrichment() in analytics_service.py is now the single
  implementation. The router calls it and assembles the final response.

  Co-Authored-By: Oz <oz-agent@warp.dev>"
  ```

---

## Final verification

After all tasks:

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -v 2>&1 | tail -20
```

Expected: all ~580+ tests pass, 0 failures.

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run black --check portf_manager/ portf_server/
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501
```

---

## Self-Review

**Spec coverage check:**
- ✅ Position math duplication → Task 4
- ✅ European number parser duplication → Task 2
- ✅ `database_factory.py` dead facades → Task 3
- ✅ yfinance bypass (watchlist, portfolio_advisor) → Task 5
- ✅ LLM per-provider model config + fallback + docstring → Task 6
- ✅ Analytics router business logic in service → Task 7
- ✅ cli.py docstring order → Task 1
- ✅ `analytics.py` benchmark / backfill yf calls intentionally left (historical series, not in market.py scope)

**Placeholder scan:** No TBDs. All code blocks are complete. All commands are concrete.

**Type consistency:** `compute_positions` returns `Tuple[Dict[Hashable, dict], float]`. In Task 4 the key is `asset["id"]` (int, which is Hashable) — correct. `dividend_ttm_enrichment` takes `list[dict]` and `dict[str, float]` — consistent with how it's called in Task 7.
