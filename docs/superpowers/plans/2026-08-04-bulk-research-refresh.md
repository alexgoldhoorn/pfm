# Bulk Research Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Refresh all targets" button on the Research page that batch-generates buy/sell price targets for every held + watchlist symbol that has no research note (or one 90+ days old), reusing the existing single-symbol LLM valuation and save logic.

**Architecture:** A new pure DB-read helper (`get_symbols_needing_refresh`) computes the eligible symbol list. A new background-thread + polling endpoint pair on the research router (mirroring the existing `_BACKFILL` pattern in `portf_server/routers/analytics.py:473-599`) runs the per-symbol LLM valuation sequentially and persists results with the same DB calls the single-symbol `/save` endpoint already uses. A new button on the Research page triggers it and polls for progress, mirroring `triggerPriceUpdate()` in `pfm_core.js`.

**Tech Stack:** Python 3.13 / FastAPI / SQLite (existing `Database` class) on the backend; vanilla JS + Bootstrap 5 on the frontend. No new dependencies, no DB migration.

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-08-04-bulk-research-refresh-design.md` — read it before starting; this plan implements it exactly.
- Scope: held positions (qty > 0) **and** watchlist symbols, deduplicated by symbol.
- Selection: only symbols with no research note, or one `STALE_RESEARCH_DAYS` (90, from `portf_manager/services/action_items.py`) or more days old.
- Overwrite: a stale symbol's existing target is always overwritten with the new value — no per-symbol confirmation (unattended background batch).
- `generate_valuation_report` **swallows its own exceptions** and returns a fallback dict with `fair_value`/`buy_below`/`sell_above` all `None` on failure — never assume a raised exception is the only failure signal; check the returned values.
- No new DB table, no schema migration (`DATABASE_VERSION` stays unchanged) — reuses `research_notes`, `price_targets`, `watchlist` exactly as the existing single-symbol `/save` endpoint does.
- Code style: black (line length 88), type hints, Google-style docstrings, comments on the line before the code they describe (per `CLAUDE.md`).
- Every code task ends with `uv run black <file>` and the relevant `pytest` run passing.

---

### Task 1: `get_symbols_needing_refresh` eligibility helper

**Files:**
- Modify: `portf_manager/services/research.py` (add `datetime` import near the top; add the new function after `compute_targets`, i.e. after line 180 / before `def _num` at line 183)
- Test: Create `tests/unit/test_research_bulk_refresh.py`

**Interfaces:**
- Produces: `get_symbols_needing_refresh(db) -> list[dict[str, Any]]`, each item `{"symbol": str, "asset_id": int | None, "name": str}`, sorted by `symbol`. Task 2 imports and calls this.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_research_bulk_refresh.py`:

```python
"""Unit tests for bulk research refresh: eligibility + the background worker."""

from datetime import date, timedelta

from portf_manager.services.research import get_symbols_needing_refresh


def _held_asset(db, symbol="AAPL", name="Apple Inc.", qty=10.0, asset_type="stock"):
    aid = db.create_asset(symbol, name, asset_type, currency="USD")
    pid = db.get_or_create_portfolio("TestBroker", base_currency="EUR")
    db.create_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=qty,
        price=100.0,
        total_amount=qty * 100.0,
        transaction_date=date.today().isoformat(),
        portfolio_id=pid,
        currency="USD",
    )
    return aid


def _age_note(db, note_id, days_ago):
    old_date = (date.today() - timedelta(days=days_ago)).isoformat()
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE research_notes SET created_at = ? WHERE id = ?",
            (old_date, note_id),
        )
        conn.commit()


class TestGetSymbolsNeedingRefresh:
    def test_includes_held_asset_with_no_research(self, test_database):
        _held_asset(test_database)
        out = get_symbols_needing_refresh(test_database)
        assert [c["symbol"] for c in out] == ["AAPL"]
        assert out[0]["asset_id"] is not None
        assert out[0]["name"] == "Apple Inc."

    def test_excludes_held_asset_with_fresh_research(self, test_database):
        aid = _held_asset(test_database)
        test_database.create_research_note(asset_id=aid, symbol="AAPL", thesis="x")
        assert get_symbols_needing_refresh(test_database) == []

    def test_includes_held_asset_with_stale_research(self, test_database):
        aid = _held_asset(test_database)
        note_id = test_database.create_research_note(
            asset_id=aid, symbol="AAPL", thesis="x"
        )
        _age_note(test_database, note_id, days_ago=120)
        out = get_symbols_needing_refresh(test_database)
        assert [c["symbol"] for c in out] == ["AAPL"]

    def test_boundary_89_days_excluded_90_days_included(self, test_database):
        aid = _held_asset(test_database)
        note_id = test_database.create_research_note(
            asset_id=aid, symbol="AAPL", thesis="x"
        )
        _age_note(test_database, note_id, days_ago=89)
        assert get_symbols_needing_refresh(test_database) == []

        _age_note(test_database, note_id, days_ago=90)
        out = get_symbols_needing_refresh(test_database)
        assert [c["symbol"] for c in out] == ["AAPL"]

    def test_includes_watchlist_only_symbol(self, test_database):
        test_database.add_watchlist(symbol="MSFT", name="Microsoft Corp.")
        out = get_symbols_needing_refresh(test_database)
        assert [c["symbol"] for c in out] == ["MSFT"]
        assert out[0]["asset_id"] is None
        assert out[0]["name"] == "Microsoft Corp."

    def test_dedupes_symbol_held_and_watchlisted(self, test_database):
        _held_asset(test_database, symbol="AAPL", name="Apple Inc.")
        test_database.add_watchlist(symbol="AAPL", name="Apple Inc.")
        out = get_symbols_needing_refresh(test_database)
        assert len(out) == 1
        assert out[0]["asset_id"] is not None

    def test_empty_when_nothing_held_or_watchlisted(self, test_database):
        assert get_symbols_needing_refresh(test_database) == []

    def test_sorted_by_symbol(self, test_database):
        _held_asset(test_database, symbol="MSFT", name="Microsoft Corp.")
        _held_asset(test_database, symbol="AAPL", name="Apple Inc.")
        out = get_symbols_needing_refresh(test_database)
        assert [c["symbol"] for c in out] == ["AAPL", "MSFT"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_research_bulk_refresh.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_symbols_needing_refresh'`

- [ ] **Step 3: Add the `datetime` import**

In `portf_manager/services/research.py`, the current top-of-file imports (lines 11-24) are:

```python
from __future__ import annotations

import json
import logging
from typing import Any

import yfinance as yf

import os

from portf_manager.llm_client import (
    OpenRouterLLMClient,
    get_llm_client,
)
```

Change to:

```python
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import yfinance as yf

import os

from portf_manager.llm_client import (
    OpenRouterLLMClient,
    get_llm_client,
)
```

- [ ] **Step 4: Implement `get_symbols_needing_refresh`**

Insert this function into `portf_manager/services/research.py` immediately after `compute_targets` ends (after line 180, i.e. right before the blank lines preceding `def _num(v):`):

```python
def get_symbols_needing_refresh(db) -> list[dict[str, Any]]:
    """Held + watchlist symbols with no research note, or one 90+ days stale.

    Powers the Research page's "Refresh all targets" bulk button — see
    docs/superpowers/specs/2026-08-04-bulk-research-refresh-design.md.
    Deduplicated by symbol (uppercase), sorted by symbol.
    """
    from portf_manager.positions import compute_positions
    from portf_manager.services.action_items import STALE_RESEARCH_DAYS, _parse_date

    today = datetime.now().date()
    positions, _ = compute_positions(db.get_all_transactions())
    held_asset_ids = {aid for aid, pos in positions.items() if pos["quantity"] > 0}

    candidates: dict[str, dict[str, Any]] = {}
    for aid in held_asset_ids:
        asset = db.get_asset(aid)
        if not asset:
            continue
        sym = asset["symbol"].upper()
        candidates[sym] = {
            "symbol": sym,
            "asset_id": aid,
            "name": asset.get("name") or sym,
        }
    for w in db.get_watchlist():
        sym = (w.get("symbol") or "").upper()
        if not sym or sym in candidates:
            continue
        linked = db.get_asset_by_symbol(sym)
        candidates[sym] = {
            "symbol": sym,
            "asset_id": linked["id"] if linked else None,
            "name": w.get("name") or sym,
        }

    latest_by_symbol = {n["symbol"].upper(): n for n in db.get_latest_research_notes()}
    out = []
    for sym, c in candidates.items():
        note = latest_by_symbol.get(sym)
        if note is None:
            out.append(c)
            continue
        created = _parse_date(note.get("created_at"))
        if created is None or (today - created).days >= STALE_RESEARCH_DAYS:
            out.append(c)
    return sorted(out, key=lambda c: c["symbol"])
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_research_bulk_refresh.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Format and lint**

Run:
```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/services/research.py tests/unit/test_research_bulk_refresh.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/services/research.py --max-line-length=88 --extend-ignore=E203,W503,E501
```
Expected: black reports no changes needed (or reformats cleanly); flake8 reports 0 warnings.

- [ ] **Step 7: Commit**

```bash
git add portf_manager/services/research.py tests/unit/test_research_bulk_refresh.py
git commit -m "$(cat <<'EOF'
feat: add get_symbols_needing_refresh eligibility helper

Computes held + watchlist symbols with no research note, or one 90+
days stale, for the upcoming bulk research refresh button.

Co-Authored-By: Oz <oz-agent@warp.dev>
EOF
)"
```

---

### Task 2: Background worker + `bulk-refresh` / `bulk-refresh-status` endpoints

**Files:**
- Modify: `portf_server/routers/research.py` (add `import threading` near the top; add `_BULK_RESEARCH` state, `_run_bulk_research_refresh`, and the two new endpoints between `set_targets` (ends line 719) and `compute_price_target_alerts` (starts line 722))
- Test: Modify `tests/unit/test_research_bulk_refresh.py` (append)

**Interfaces:**
- Consumes: `get_symbols_needing_refresh(db)` from Task 1; `_position_stats(db, asset)`, `_current_price(db, asset, symbol)` already defined in `portf_server/routers/research.py` (lines 72, 143); `fetch_fundamentals(symbol, db)`, `fetch_recent_news(symbol, db=db)`, `generate_valuation_report(...)` already defined in `portf_manager/services/research.py`.
- Produces: module-level `_BULK_RESEARCH: dict` (keys: `running`, `total`, `done`, `current_symbol`, `results`, `started_at`, `finished_at`), `_run_bulk_research_refresh(db) -> None`, endpoints `POST /api/v1/research/bulk-refresh` and `GET /api/v1/research/bulk-refresh-status`. Task 3's frontend calls these two endpoints by URL.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_research_bulk_refresh.py` (add `import pytest` and `from unittest.mock import ANY` aren't needed; add these imports at the top of the file alongside the existing ones):

```python
from portf_server.routers.research import _BULK_RESEARCH, _run_bulk_research_refresh
```

Then append these classes at the end of the file:

```python
_USABLE_RESULT = {
    "fair_value": 175.0,
    "buy_below": 140.0,
    "sell_above": 200.0,
    "recommendation": "BUY",
    "confidence": "high",
    "summary": "Solid outlook.",
    "rationale": "Strong margins.",
    "risks": [],
    "catalysts": [],
    "sources": [],
}
_NO_DATA_RESULT = {
    "fair_value": None,
    "buy_below": None,
    "sell_above": None,
    "recommendation": "HOLD",
    "confidence": "low",
    "summary": "Could not generate automated analysis for AAPL: boom",
    "rationale": "",
    "risks": [],
    "catalysts": [],
    "sources": [],
}


class TestRunBulkResearchRefresh:
    def test_writes_price_target_for_held_asset(self, test_database, mocker):
        aid = _held_asset(test_database)
        mocker.patch(
            "portf_manager.services.research.fetch_fundamentals", return_value={}
        )
        mocker.patch(
            "portf_manager.services.research.fetch_recent_news", return_value=[]
        )
        mocker.patch(
            "portf_manager.services.research.generate_valuation_report",
            return_value=dict(_USABLE_RESULT),
        )

        _run_bulk_research_refresh(test_database)

        target = test_database.get_price_target(aid)
        assert target["buy_below"] == 140.0
        assert target["sell_above"] == 200.0
        assert _BULK_RESEARCH["running"] is False
        assert _BULK_RESEARCH["done"] == 1
        assert _BULK_RESEARCH["results"][0]["status"] == "updated"

    def test_no_usable_data_does_not_overwrite_existing_target(
        self, test_database, mocker
    ):
        aid = _held_asset(test_database)
        test_database.upsert_price_target(
            asset_id=aid, buy_below=90.0, sell_above=150.0
        )
        note_id = test_database.create_research_note(
            asset_id=aid, symbol="AAPL", thesis="x"
        )
        _age_note(test_database, note_id, days_ago=120)
        mocker.patch(
            "portf_manager.services.research.fetch_fundamentals", return_value={}
        )
        mocker.patch(
            "portf_manager.services.research.fetch_recent_news", return_value=[]
        )
        mocker.patch(
            "portf_manager.services.research.generate_valuation_report",
            return_value=dict(_NO_DATA_RESULT),
        )

        _run_bulk_research_refresh(test_database)

        target = test_database.get_price_target(aid)
        assert target["buy_below"] == 90.0
        assert _BULK_RESEARCH["results"][0]["status"] == "no_data"

    def test_one_symbol_error_does_not_abort_batch(self, test_database, mocker):
        _held_asset(test_database, symbol="AAPL", name="Apple Inc.")
        _held_asset(test_database, symbol="MSFT", name="Microsoft Corp.")
        mocker.patch(
            "portf_manager.services.research.fetch_fundamentals", return_value={}
        )
        mocker.patch(
            "portf_manager.services.research.fetch_recent_news", return_value=[]
        )
        mocker.patch(
            "portf_manager.services.research.generate_valuation_report",
            side_effect=[RuntimeError("boom"), dict(_USABLE_RESULT)],
        )

        _run_bulk_research_refresh(test_database)

        assert _BULK_RESEARCH["done"] == 2
        statuses = {r["symbol"]: r["status"] for r in _BULK_RESEARCH["results"]}
        assert statuses["AAPL"] == "error"
        assert statuses["MSFT"] == "updated"

    def test_watchlist_only_symbol_syncs_buy_zone_not_price_target(
        self, test_database, mocker
    ):
        test_database.add_watchlist(symbol="MSFT", name="Microsoft Corp.")
        mocker.patch(
            "portf_manager.services.research.fetch_fundamentals", return_value={}
        )
        mocker.patch(
            "portf_manager.services.research.fetch_recent_news", return_value=[]
        )
        mocker.patch(
            "portf_manager.services.research.generate_valuation_report",
            return_value=dict(_USABLE_RESULT),
        )

        _run_bulk_research_refresh(test_database)

        watch = next(
            w for w in test_database.get_watchlist() if w["symbol"] == "MSFT"
        )
        assert watch["buy_below"] == 140.0
        assert _BULK_RESEARCH["results"][0]["status"] == "updated"


class TestBulkRefreshEndpoints:
    @pytest.mark.asyncio
    async def test_start_and_status_endpoints_respond(
        self, async_test_client, auth_headers, test_database, mocker
    ):
        mocker.patch(
            "portf_manager.services.research.get_symbols_needing_refresh",
            return_value=[],
        )
        resp = await async_test_client.post(
            "/api/v1/research/bulk-refresh", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

        status_resp = await async_test_client.get(
            "/api/v1/research/bulk-refresh-status", headers=auth_headers
        )
        assert status_resp.status_code == 200
        assert "running" in status_resp.json()
```

Add `import pytest` to the top of `tests/unit/test_research_bulk_refresh.py` (it isn't needed for Task 1's synchronous tests, but is required now for `@pytest.mark.asyncio`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_research_bulk_refresh.py -v`
Expected: FAIL with `ImportError: cannot import name '_BULK_RESEARCH'`

- [ ] **Step 3: Add the `threading` import**

In `portf_server/routers/research.py`, the current imports (lines 11-19) are:

```python
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
```

Change to:

```python
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
```

- [ ] **Step 4: Implement the worker + endpoints**

Insert this block into `portf_server/routers/research.py` immediately after `set_targets` ends (after line 719, i.e. right before the blank lines preceding `def compute_price_target_alerts(db) -> list[dict]:` at line 722):

```python
# ── Bulk Research Refresh ───────────────────────────────────────────────────
# Batch-generates buy/sell targets for held + watchlist symbols missing or
# stale (90+ days) on research. See
# docs/superpowers/specs/2026-08-04-bulk-research-refresh-design.md.
_BULK_RESEARCH: dict = {
    "running": False,
    "total": 0,
    "done": 0,
    "current_symbol": None,
    "results": [],  # [{symbol, status: "updated" | "no_data" | "error", detail}]
    "started_at": None,
    "finished_at": None,
}


def _run_bulk_research_refresh(db) -> None:
    """Sequentially refresh targets for every eligible symbol.

    Runs in a background thread. Never raises — every per-symbol failure is
    caught and recorded so one bad symbol can't abort the batch.
    """
    from portf_manager.services.research import (
        fetch_fundamentals,
        fetch_recent_news,
        generate_valuation_report,
        get_symbols_needing_refresh,
    )

    _BULK_RESEARCH.update(
        running=True,
        done=0,
        current_symbol=None,
        results=[],
        started_at=datetime.now().isoformat(),
        finished_at=None,
    )
    try:
        candidates = get_symbols_needing_refresh(db)
        _BULK_RESEARCH["total"] = len(candidates)
        watchlist_symbols = {
            (w.get("symbol") or "").upper() for w in db.get_watchlist()
        }
        for c in candidates:
            sym = c["symbol"]
            _BULK_RESEARCH["current_symbol"] = sym
            try:
                asset = db.get_asset(c["asset_id"]) if c["asset_id"] else None
                pos = _position_stats(db, asset)
                price, currency = _current_price(db, asset, sym)
                fundamentals = fetch_fundamentals(sym, db)
                news = fetch_recent_news(sym, db=db)
                result = generate_valuation_report(
                    symbol=sym,
                    asset_name=c["name"],
                    asset_type=(
                        asset.get("asset_type", "stock") if asset else "stock"
                    ),
                    current_price=price,
                    avg_cost=pos["avg_cost"],
                    currency=currency,
                    fundamentals=fundamentals,
                    news=news,
                )
                usable = any(
                    result.get(k) is not None
                    for k in ("fair_value", "buy_below", "sell_above")
                )
                if not usable:
                    _BULK_RESEARCH["results"].append(
                        {
                            "symbol": sym,
                            "status": "no_data",
                            "detail": result.get("summary", ""),
                        }
                    )
                    continue

                db.create_research_note(
                    asset_id=c["asset_id"],
                    symbol=sym,
                    thesis=result.get("rationale"),
                    conviction=None,
                    method="ai-bulk",
                    assumptions=None,
                    fair_value=result.get("fair_value"),
                    buy_below=result.get("buy_below"),
                    sell_above=result.get("sell_above"),
                    price_at_save=price,
                    llm_summary=result.get("summary"),
                    sources=(
                        json.dumps(result.get("sources"))
                        if result.get("sources")
                        else None
                    ),
                )
                if c["asset_id"]:
                    db.upsert_price_target(
                        asset_id=c["asset_id"],
                        buy_below=result.get("buy_below"),
                        sell_above=result.get("sell_above"),
                        fair_value=result.get("fair_value"),
                        notes=(result.get("rationale") or "")[:500] or None,
                    )
                if sym in watchlist_symbols and result.get("buy_below"):
                    db.add_watchlist(symbol=sym, buy_below=result.get("buy_below"))
                _BULK_RESEARCH["results"].append(
                    {"symbol": sym, "status": "updated", "detail": ""}
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(f"Bulk research refresh failed for {sym}")
                _BULK_RESEARCH["results"].append(
                    {"symbol": sym, "status": "error", "detail": str(e)}
                )
            finally:
                _BULK_RESEARCH["done"] += 1
    finally:
        _BULK_RESEARCH.update(
            running=False,
            current_symbol=None,
            finished_at=datetime.now().isoformat(),
        )


@router.post("/bulk-refresh")
async def bulk_refresh(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Start a background research refresh for held + watchlist symbols
    missing or stale (90+ days) on research targets.

    Returns immediately; poll GET /bulk-refresh-status for progress. If a
    refresh is already running, returns its current progress instead of
    starting a second one.
    """
    if _BULK_RESEARCH["running"]:
        return {"status": "running", **_BULK_RESEARCH}
    threading.Thread(
        target=_run_bulk_research_refresh, args=(db,), daemon=True
    ).start()
    return {"status": "started"}


@router.get("/bulk-refresh-status")
async def bulk_refresh_status(api_key_info: dict = Depends(_auth)):
    """Progress of the bulk research refresh (poll while running)."""
    return _BULK_RESEARCH
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_research_bulk_refresh.py -v`
Expected: PASS (13 tests total: 8 from Task 1 + 5 new from this task — 4 in `TestRunBulkResearchRefresh` + 1 in `TestBulkRefreshEndpoints`)

- [ ] **Step 6: Run the full unit suite to check for regressions**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
Expected: all passing (827 + 13 new = 840, plus the 6 pre-existing skips)

- [ ] **Step 7: Format and lint**

Run:
```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/research.py tests/unit/test_research_bulk_refresh.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_server/routers/research.py --max-line-length=88 --extend-ignore=E203,W503,E501
```
Expected: black reports no changes needed (or reformats cleanly); flake8 reports 0 warnings.

- [ ] **Step 8: Commit**

```bash
git add portf_server/routers/research.py tests/unit/test_research_bulk_refresh.py
git commit -m "$(cat <<'EOF'
feat: add background worker + endpoints for bulk research refresh

POST /api/v1/research/bulk-refresh starts a background thread that
sequentially regenerates buy/sell targets for every symbol
get_symbols_needing_refresh flags as missing or stale; GET
bulk-refresh-status polls progress. Mirrors the existing
_BACKFILL/backfill-snapshots pattern in analytics.py.

Co-Authored-By: Oz <oz-agent@warp.dev>
EOF
)"
```

---

### Task 3: Frontend button + wiring

**Files:**
- Modify: `web_client/index.html` (research page header, lines 1369-1374)
- Modify: `web_client/js/pfm_features.js` (research page wiring closure, insert after the tabs-click handler block that ends at line 3496)

**Interfaces:**
- Consumes: `POST /api/v1/research/bulk-refresh`, `GET /api/v1/research/bulk-refresh-status` from Task 2 (response shape: `{running, total, done, current_symbol, results: [{symbol, status, detail}], started_at, finished_at}`); `window.apiClient.baseURL` / `window.apiClient.apiKey` (existing global, same as used by `triggerPriceUpdate` in `pfm_core.js:465-468`); local closure functions `loadCompare()` (`pfm_features.js:3986`) and `load(sym)` (`pfm_features.js:3737`), and the local variable `R` (holds the currently-loaded Workbench symbol, `pfm_features.js:3480`).

- [ ] **Step 1: Add the button to `index.html`**

The current content at `web_client/index.html:1369-1374` is:

```html
                        <div class="d-flex align-items-center gap-2">
                            <div class="btn-group" role="group" id="researchTabs">
                                <button type="button" class="btn btn-outline-primary active" data-rtab="workbench"><i class="bi bi-clipboard-data me-1"></i>Workbench</button>
                                <button type="button" class="btn btn-outline-primary" data-rtab="compare"><i class="bi bi-bar-chart-line me-1"></i>Compare</button>
                            </div>
                        </div>
```

Replace with:

```html
                        <div class="d-flex align-items-center gap-2 flex-wrap">
                            <button class="btn btn-sm btn-outline-primary" id="researchBulkRefreshBtn" title="Generate buy/sell targets for every held + watchlisted symbol missing research or with research 90+ days old">
                                <i class="bi bi-magic me-1" id="researchBulkRefreshIcon"></i><span id="researchBulkRefreshLabel">Refresh all targets</span>
                            </button>
                            <span id="researchBulkRefreshStatus" class="text-muted small"></span>
                            <div class="btn-group" role="group" id="researchTabs">
                                <button type="button" class="btn btn-outline-primary active" data-rtab="workbench"><i class="bi bi-clipboard-data me-1"></i>Workbench</button>
                                <button type="button" class="btn btn-outline-primary" data-rtab="compare"><i class="bi bi-bar-chart-line me-1"></i>Compare</button>
                            </div>
                        </div>
```

- [ ] **Step 2: Wire the button in `pfm_features.js`**

The current content at `web_client/js/pfm_features.js:3485-3497` is:

```javascript
    // Tabs
    page.querySelectorAll('#researchTabs [data-rtab]').forEach(a => {
        a.addEventListener('click', (e) => {
            e.preventDefault();
            page.querySelectorAll('#researchTabs [data-rtab]').forEach(n => n.classList.remove('active'));
            a.classList.add('active');
            const t = a.dataset.rtab;
            $('researchWorkbench').style.display = t === 'workbench' ? '' : 'none';
            $('researchCompare').style.display = t === 'compare' ? '' : 'none';
            if (t === 'compare') loadCompare();
        });
    });

    function recompute() {
```

Replace with (adds the bulk-refresh wiring between the tabs block and `recompute`):

```javascript
    // Tabs
    page.querySelectorAll('#researchTabs [data-rtab]').forEach(a => {
        a.addEventListener('click', (e) => {
            e.preventDefault();
            page.querySelectorAll('#researchTabs [data-rtab]').forEach(n => n.classList.remove('active'));
            a.classList.add('active');
            const t = a.dataset.rtab;
            $('researchWorkbench').style.display = t === 'workbench' ? '' : 'none';
            $('researchCompare').style.display = t === 'compare' ? '' : 'none';
            if (t === 'compare') loadCompare();
        });
    });

    // Bulk research refresh: generate buy/sell targets for every held +
    // watchlisted symbol missing research or 90+ days stale. Mirrors
    // triggerPriceUpdate() in pfm_core.js (trigger, then poll a status
    // endpoint every few seconds until the background thread finishes).
    const bulkBtn = $('researchBulkRefreshBtn');
    if (bulkBtn) {
        const bulkIcon = $('researchBulkRefreshIcon');
        const bulkLabel = $('researchBulkRefreshLabel');
        const bulkStatus = $('researchBulkRefreshStatus');
        const setBulkRunning = (doneCount, total) => {
            bulkBtn.disabled = true;
            if (bulkIcon) bulkIcon.className = 'spinner-border spinner-border-sm me-1';
            if (bulkLabel) bulkLabel.textContent = total ? `Refreshing ${doneCount}/${total}…` : 'Refreshing…';
        };
        const setBulkDone = (text) => {
            bulkBtn.disabled = false;
            if (bulkIcon) bulkIcon.className = 'bi bi-magic me-1';
            if (bulkLabel) bulkLabel.textContent = 'Refresh all targets';
            if (bulkStatus) bulkStatus.textContent = text || '';
        };
        const pollBulk = async () => {
            try {
                const r = await fetch(window.apiClient.baseURL + '/api/v1/research/bulk-refresh-status', {
                    headers: { 'X-API-Key': window.apiClient.apiKey },
                });
                if (!r.ok) { setBulkDone('Refresh failed.'); return; }
                const s = await r.json();
                if (s.running) {
                    setBulkRunning(s.done, s.total);
                    setTimeout(pollBulk, 3000);
                } else {
                    const updated = s.results.filter(x => x.status === 'updated').length;
                    const noData = s.results.filter(x => x.status === 'no_data').length;
                    const errored = s.results.filter(x => x.status === 'error').length;
                    const text = s.total === 0
                        ? 'Nothing needed refreshing.'
                        : `Updated ${updated} of ${s.total}` +
                          (noData ? ` · ${noData} had no usable data` : '') +
                          (errored ? ` · ${errored} failed` : '');
                    setBulkDone(text);
                    if ($('researchCompare').style.display !== 'none') loadCompare();
                    else if (R.symbol) load(R.symbol);
                }
            } catch (e) {
                setBulkDone('Refresh failed: ' + e.message);
            }
        };
        bulkBtn.addEventListener('click', async () => {
            setBulkRunning(0, 0);
            if (bulkStatus) bulkStatus.textContent = '';
            try {
                const resp = await fetch(window.apiClient.baseURL + '/api/v1/research/bulk-refresh', {
                    method: 'POST',
                    headers: { 'X-API-Key': window.apiClient.apiKey },
                });
                if (!resp.ok) { setBulkDone('Failed to start.'); return; }
            } catch (e) {
                setBulkDone('Failed to start: ' + e.message);
                return;
            }
            setTimeout(pollBulk, 1500);
        });
    }

    function recompute() {
```

- [ ] **Step 3: Rebuild and deploy the web client**

Run:
```bash
cd ~/repos/pfm && docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```
Expected: build succeeds, container restarts healthy (`docker ps` shows `portf_web` "Up ... (healthy)").

- [ ] **Step 4: Manually verify in the browser**

Use the `run` skill or open `http://localhost:8080` directly:
1. Navigate to the Research page.
2. Confirm the "Refresh all targets" button renders next to the Workbench/Compare tabs.
3. Click it. Confirm the button disables, shows a spinner, and the label updates to `Refreshing N/M…` within ~5 seconds (poll interval is 3s after an initial 1.5s delay).
4. Wait for completion (this will make real LLM calls for every currently-stale held/watchlist symbol — expect it to take several minutes if run against the real dev DB with ~45 stale holdings; for a faster manual check, temporarily test against a portfolio with only 1-2 stale symbols, or just verify the button reaches "Refreshing 1/1…" and don't wait out the full LLM call).
5. Confirm the button re-enables and a one-line summary appears (e.g. "Updated 1 of 1").
6. Switch to the Compare tab (or reload the Workbench for a symbol that was just refreshed) and confirm the new buy/sell values appear.

Document the actual observed behavior (pass/fail, and anything unexpected) before proceeding — do not claim success without having actually watched this happen.

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html web_client/js/pfm_features.js
git commit -m "$(cat <<'EOF'
feat: add "Refresh all targets" button to the Research page

Triggers POST /api/v1/research/bulk-refresh and polls its status,
mirroring the dashboard's triggerPriceUpdate() pattern. Refreshes
the Compare table or the currently-loaded Workbench symbol on
completion.

Co-Authored-By: Oz <oz-agent@warp.dev>
EOF
)"
```

---

### Task 4: Documentation + final verification

**Files:**
- Modify: `CLAUDE.md` (Research API section — the `#### Workbench & Compare` bullet list)
- Modify: `PROJECT_STATUS.md` (header date + new "Recent" entry)

**Interfaces:** None (docs only).

- [ ] **Step 1: Update `CLAUDE.md`**

Find the `#### Workbench & Compare` section (contains the bullet starting `- GET /api/v1/research/{symbol}/lookup`). Add a new bullet after the existing `POST /api/v1/research/{symbol}/save` line and before `GET /api/v1/research/compare`:

```markdown
- `POST /api/v1/research/bulk-refresh` / `GET /api/v1/research/bulk-refresh-status` — background-thread bulk version of `/generate` + `/save`: sequentially regenerates targets for every symbol `get_symbols_needing_refresh(db)` (`portf_manager/services/research.py`) flags as held-or-watchlisted with no research note or one 90+ days old (`STALE_RESEARCH_DAYS`, from `action_items.py`). Never overwrites an existing target with nulls — `generate_valuation_report` swallows its own failures into a null-fields dict rather than raising, so the worker checks for a usable result before writing. Progress (`running/total/done/current_symbol/results`) lives in the module-level `_BULK_RESEARCH` dict, same pattern as `_BACKFILL`/`backfill-snapshots` in `analytics.py`. Web: "Refresh all targets" button on the Research page header (`pfm_features.js`), polls the status endpoint like the dashboard's `triggerPriceUpdate()`.
```

- [ ] **Step 2: Update `PROJECT_STATUS.md`**

Change line 8 from:
```
Last updated: 2026-07-30
```
to:
```
Last updated: 2026-08-04
```

Insert a new line after line 9 (before the existing `**Recent (v2.5.36):**` line):

```markdown
**Recent (v2.5.37):** **Research: "Refresh all targets" bulk button.** New `POST /api/v1/research/bulk-refresh` + `GET /api/v1/research/bulk-refresh-status` background-job pair sequentially regenerates buy/sell price targets for every held + watchlist symbol with no research note, or one 90+ days old — the same LLM valuation + save logic the single-symbol Workbench already uses, just batched. A stale symbol's existing target is always overwritten (no per-symbol prompt, since it's an unattended batch); a symbol whose LLM call comes back with no usable value is left untouched rather than clobbered with nulls. New "Refresh all targets" button on the Research page polls progress the same way the dashboard's "Refresh prices" button does.
```

- [ ] **Step 3: Run the full test suite one final time**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
Expected: all passing.

- [ ] **Step 4: Restart the backend to pick up the code changes**

Run: `docker exec portf_backend_dev kill -HUP 1`
(The dev backend already auto-reloads on file save per the `watchfiles` log lines seen in earlier debugging, but issue the HUP explicitly to be certain the final committed state is what's running.)

- [ ] **Step 5: Commit the docs**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "$(cat <<'EOF'
docs: document bulk research refresh feature

Co-Authored-By: Oz <oz-agent@warp.dev>
EOF
)"
```

## Self-Review Notes

- **Spec coverage:** Scope (held+watchlist, dedup) → Task 1. Selection (missing-or-stale) → Task 1. Overwrite-always → Task 2 worker. Placement (Research page) → Task 3. No-usable-data-doesn't-overwrite → Task 2 test `test_no_usable_data_does_not_overwrite_existing_target`. No new DB table → confirmed, no migration task exists. Testing section of the spec → Tasks 1 & 2 tests cover every case it lists (held-only, watchlist-only, both/dedup, staleness boundary, no-overwrite-on-failure, one-symbol-error-continues).
- **Placeholder scan:** none found — every step has literal code or an exact shell command.
- **Type consistency:** `get_symbols_needing_refresh` returns `{"symbol", "asset_id", "name"}` in Task 1 and is consumed with exactly those three keys in Task 2's worker. `_BULK_RESEARCH` keys (`running/total/done/current_symbol/results/started_at/finished_at`) are identical between the Task 2 Python dict and the Task 3 JS consumer (`s.running`, `s.total`, `s.done`, `s.results`).
