# Action Items Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new "Action Items" page that aggregates cross-cutting maintenance signals (stale broker imports, data-quality issues, price-update failures, stale research, off-track goals, watchlist/price-target alerts, net-worth setup gaps) into one dismissible, severity-sorted list.

**Architecture:** A new backend endpoint `GET /api/v1/action-items/` runs six independent checks server-side, reusing existing in-process router functions (no HTTP self-calls, no duplicated business logic) rather than the raw DB each time. The frontend page fetches that endpoint plus `GET /api/v1/networth/` + `GET /api/v1/networth/cashflow`, and merges in the existing (already-tested) `computeNetWorthChecklist()` output client-side — the one deliberate exception, since that checklist logic is intentionally client-only and shouldn't be duplicated in Python.

**Tech Stack:** FastAPI (plain `def` endpoint — pure DB reads, no yfinance calls), SQLite via `Database`, vanilla JS (no framework, classic scripts sharing one global scope), pytest + Node's built-in test runner.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-16-action-items-design.md` (read this first — it has the full rationale for every architectural choice below).
- Black (line length 88), flake8 `--max-line-length=88 --extend-ignore=E203,W503,E501`, type hints on all function signatures — per `CLAUDE.md`.
- Comments go on the line before the code they describe, not inline — per `CLAUDE.md`.
- New endpoints must be registered with `dependencies=_PROTECTED` in `app.py` or `tests/unit/test_auth_coverage.py::test_all_data_endpoints_require_auth` fails.
- Web client changes require a rebuild + redeploy to take effect (`docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`) — not live-mounted.
- Python changes require `docker exec portf_backend_dev kill -HUP 1` (gunicorn has no reload).
- No new personal/financial data in test fixtures — use invented names ("TestBroker", "Term Deposit").

---

### Task 1: Extract `compute_price_target_alerts` in research.py (pure refactor)

**Files:**
- Modify: `portf_server/routers/research.py:722-785`
- Test: `tests/unit/test_rebalance_research.py` (existing test at line ~102 must still pass unchanged — proves the refactor preserves behavior)

**Interfaces:**
- Produces: `compute_price_target_alerts(db) -> list[dict]` in `portf_server/routers/research.py` — each dict has keys `symbol, name, currency, price_date, quantity, avg_price, cost_basis, value, unrealized_pnl, unrealized_pnl_pct, triggers` (`triggers` is `list[{"type": "BUY"|"SELL", "threshold": float, "price": float}]`). Task 3 imports this.

Why: `check_alerts` (the existing `/research/alerts/check` endpoint) computes triggered price-target alerts AND dispatches push notifications as a side effect. Task 3 needs the alert computation without re-triggering a push send on every Action Items page load, so the computation must be extracted into a side-effect-free function first.

- [ ] **Step 1: Read the current function to confirm line range**

Run: `sed -n '722,785p' portf_server/routers/research.py`
Expected: shows `@router.get("/alerts/check")` through the final `return {"alerts": alerts, "total": len(alerts)}`.

- [ ] **Step 2: Replace the block with an extracted pure function + a thin endpoint**

Replace lines 722-785 (the full `check_alerts` function) with:

```python
def compute_price_target_alerts(db) -> list[dict]:
    """Compare all price targets against latest stored prices.

    Pure computation (no push-notification side effect) so other call sites
    (the Action Items aggregator) can reuse it without re-triggering a push
    send on every read. ``check_alerts`` below is the HTTP entry point that
    adds push dispatch on top of this.
    """
    positions, _ = compute_positions(db.get_all_transactions())

    alerts = []
    for pt in db.get_all_price_targets():
        asset_id = pt["asset_id"]
        price_data = db.get_latest_price(asset_id)
        if not price_data:
            continue
        price = float(price_data["price"])
        symbol = pt["symbol"]
        triggered = []
        if pt.get("buy_below") and price <= pt["buy_below"]:
            triggered.append(
                {"type": "BUY", "threshold": pt["buy_below"], "price": price}
            )
        if pt.get("sell_above") and price >= pt["sell_above"]:
            triggered.append(
                {"type": "SELL", "threshold": pt["sell_above"], "price": price}
            )
        if triggered:
            asset = db.get_asset(asset_id) or {}
            pos = positions.get(asset_id, {"quantity": 0.0, "cost": 0.0})
            qty = round(pos["quantity"], 6) if pos["quantity"] > 0 else 0.0
            cost_basis = round(pos["cost"], 2) if qty else 0.0
            value = round(qty * price, 2)
            unrealized = round(value - cost_basis, 2) if qty else 0.0
            unrealized_pct = (
                round((value - cost_basis) / cost_basis * 100, 2)
                if cost_basis > 0
                else 0.0
            )
            alerts.append(
                {
                    "symbol": symbol,
                    "name": pt.get("name") or asset.get("name") or "",
                    "currency": asset.get("currency", "EUR"),
                    "price_date": price_data.get("price_date"),
                    "quantity": qty,
                    "avg_price": round(cost_basis / qty, 4) if qty else 0.0,
                    "cost_basis": cost_basis,
                    "value": value,
                    "unrealized_pnl": unrealized,
                    "unrealized_pnl_pct": unrealized_pct,
                    "triggers": triggered,
                }
            )
    return alerts


@router.get("/alerts/check")
async def check_alerts(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """
    Compare all price targets against latest stored prices.
    Returns triggered alerts (does NOT send Telegram — use the cron for that).
    """
    alerts = compute_price_target_alerts(db)
    # Dispatch push notifications for triggered alerts
    if alerts:
        try:
            from portf_manager.push_notifications import send_alerts_push

            send_alerts_push(db, alerts)
        except Exception as e:
            logger.warning(f"Push notification dispatch failed: {e}")
    return {"alerts": alerts, "total": len(alerts)}
```

- [ ] **Step 3: Run the existing test to confirm no regression**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_rebalance_research.py -v`
Expected: PASS (same as before the refactor — this proves the extraction didn't change behavior).

- [ ] **Step 4: Format and lint**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/research.py && UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_server/routers/research.py --max-line-length=88 --extend-ignore=E203,W503,E501`
Expected: black reports no changes needed (or reformats cleanly); flake8 reports nothing.

- [ ] **Step 5: Commit**

```bash
git add portf_server/routers/research.py
git commit -m "refactor: extract compute_price_target_alerts from check_alerts

Pulls the pure price-target comparison out of the /research/alerts/check
endpoint so it can be reused by the upcoming Action Items aggregator
without re-triggering the push-notification side effect on every read."
```

---

### Task 2: Action Items service — stale imports, data quality, price-update failures

**Files:**
- Create: `portf_manager/services/action_items.py`
- Test: `tests/unit/test_action_items.py`

**Interfaces:**
- Consumes: `db.get_all_portfolios()`, `db.get_portfolio_date_ranges()`, `db.get_price_update_runs(limit=1)` (all existing `Database` methods); `dq_duplicates(db, api_key_info)`, `dq_suspicious(db, api_key_info)` from `portf_server.routers.analytics` (existing plain functions — call as `dq_duplicates(db=db, api_key_info={})`, `api_key_info` is unused inside their bodies so an empty dict is safe).
- Produces (this task): `check_stale_imports(db, today=None) -> list[dict]`, `check_data_quality(db) -> list[dict]`, `check_price_update_failures(db) -> list[dict]` in `portf_manager/services/action_items.py`. Each returns a list of dicts shaped `{id, category, severity, title, detail, link_page, context}` (see spec). Task 3 adds three more check functions plus the aggregator to this same file.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_action_items.py`:

```python
"""Unit tests for the cross-cutting Action Items aggregator."""

from datetime import date, timedelta

from portf_manager.services.action_items import (
    check_data_quality,
    check_price_update_failures,
    check_stale_imports,
)


def _portfolio_with_transaction(db, name="TestBroker", days_ago=10):
    """One portfolio with a single buy transaction dated `days_ago` days back."""
    pid = db.get_or_create_portfolio(name, base_currency="EUR")
    aid = db.create_asset(f"{name}SYM", f"{name} Asset", "stock", currency="EUR")
    tx_date = (date.today() - timedelta(days=days_ago)).isoformat()
    db.create_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=10.0,
        price=100.0,
        total_amount=1000.0,
        transaction_date=tx_date,
        portfolio_id=pid,
        currency="EUR",
    )
    return pid, aid


class TestStaleImports:
    def test_flags_portfolio_with_no_recent_activity(self, test_database):
        pid, _ = _portfolio_with_transaction(test_database, days_ago=90)
        items = check_stale_imports(test_database)
        assert any(i["context"]["portfolio_id"] == pid for i in items)

    def test_does_not_flag_recent_activity(self, test_database):
        pid, _ = _portfolio_with_transaction(test_database, days_ago=5)
        items = check_stale_imports(test_database)
        assert not any(i["context"]["portfolio_id"] == pid for i in items)

    def test_ignores_portfolio_with_no_transactions_ever(self, test_database):
        pid = test_database.get_or_create_portfolio("Empty", base_currency="EUR")
        items = check_stale_imports(test_database)
        assert not any(i["context"]["portfolio_id"] == pid for i in items)


class TestDataQuality:
    def test_flags_suspicious_zero_price(self, test_database):
        pid = test_database.get_or_create_portfolio("Broker", base_currency="EUR")
        aid = test_database.create_asset(
            "ZP", "Zero Price Co", "stock", currency="EUR"
        )
        test_database.create_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=10.0,
            price=0.0,
            total_amount=0.0,
            transaction_date=date.today().isoformat(),
            portfolio_id=pid,
            currency="EUR",
        )
        items = check_data_quality(test_database)
        assert any(i["id"] == "dq:suspicious" for i in items)

    def test_empty_when_clean(self, test_database):
        _portfolio_with_transaction(test_database, days_ago=5)
        assert check_data_quality(test_database) == []


class TestPriceUpdateFailures:
    def test_flags_latest_run_with_errors(self, test_database):
        test_database.record_price_update_run(
            started_at="2026-07-15T20:00:00",
            duration_seconds=12.0,
            updated_count=5,
            skipped_count=0,
            error_count=2,
            error_symbols=["AAPL", "MSFT"],
            source="cron",
        )
        items = check_price_update_failures(test_database)
        assert len(items) == 1
        assert items[0]["context"]["symbols"] == ["AAPL", "MSFT"]

    def test_no_item_when_latest_run_clean(self, test_database):
        test_database.record_price_update_run(
            started_at="2026-07-15T20:00:00",
            duration_seconds=12.0,
            updated_count=5,
            skipped_count=0,
            error_count=0,
            source="cron",
        )
        assert check_price_update_failures(test_database) == []

    def test_no_item_when_no_runs(self, test_database):
        assert check_price_update_failures(test_database) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_action_items.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'portf_manager.services.action_items'`.

- [ ] **Step 3: Create the service module with the three check functions**

Create `portf_manager/services/action_items.py`:

```python
"""Action Items — cross-cutting maintenance checklist aggregator.

Pulls together checks that don't already have a single-call equivalent:
stale broker imports, a data-quality summary, price-update failures, stale
research on held positions, off-track goals, and watchlist/research price
alerts. Each check is independent; get_action_items() (added in a later
task) wraps each in a try/except so one failing check can't take down the
rest.

Net Worth setup gaps are deliberately NOT computed here — that checklist
logic lives client-side only (computeNetWorthChecklist() in
pfm_analytics.js, already unit-tested) and the frontend merges it into the
same displayed list. See docs/superpowers/specs/2026-07-16-action-items-design.md.
"""

from datetime import date, datetime

STALE_IMPORT_DAYS = 60


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def check_stale_imports(db, today: date = None) -> list[dict]:
    """Portfolios with a transaction history but no activity in 60+ days."""
    today = today or date.today()
    ranges = db.get_portfolio_date_ranges()
    items = []
    for p in db.get_all_portfolios():
        pid = p["id"]
        r = ranges.get(pid)
        if not r:
            continue  # never funded — nothing to import yet
        dates = [
            d
            for d in (
                _parse_date(r.get("last_transaction_date")),
                _parse_date(r.get("last_booking_date")),
            )
            if d is not None
        ]
        if not dates:
            continue
        most_recent = max(dates)
        days = (today - most_recent).days
        if days < STALE_IMPORT_DAYS:
            continue
        items.append(
            {
                "id": f"import:portfolio:{pid}",
                "category": "import",
                "severity": "medium",
                "title": f"No new activity in {p['name']} for {days} days",
                "detail": (
                    f"Last transaction or booking recorded on "
                    f"{most_recent.isoformat()}. Import recent statements if "
                    "this broker is still active."
                ),
                "link_page": "importexport",
                "context": {"portfolio_id": pid},
            }
        )
    return items


def check_data_quality(db) -> list[dict]:
    """Summarize non-empty findings from the existing DQ endpoints."""
    from portf_server.routers.analytics import dq_duplicates, dq_suspicious

    items = []

    dups = dq_duplicates(db=db, api_key_info={})["duplicates"]
    if dups:
        likely = sum(1 for d in dups if d["label"] == "likely")
        items.append(
            {
                "id": "dq:duplicates",
                "category": "data_quality",
                "severity": "high" if likely else "medium",
                "title": f"{len(dups)} possible duplicate transaction(s)",
                "detail": (
                    f"{likely} likely, {len(dups) - likely} possible — review "
                    "before they distort cost basis."
                ),
                "link_page": "diagnostics",
                "context": {"count": len(dups)},
            }
        )

    issues = dq_suspicious(db=db, api_key_info={})["issues"]
    if issues:
        warnings = sum(1 for i in issues if i["severity"] == "warning")
        items.append(
            {
                "id": "dq:suspicious",
                "category": "data_quality",
                "severity": "high" if warnings else "medium",
                "title": f"{len(issues)} suspicious transaction pattern(s)",
                "detail": (
                    f"{warnings} warning(s) — zero prices, negative positions, "
                    "or price outliers."
                ),
                "link_page": "diagnostics",
                "context": {"count": len(issues)},
            }
        )

    return items


def check_price_update_failures(db) -> list[dict]:
    """Flag the most recent price-update run if it had errors."""
    runs = db.get_price_update_runs(limit=1)
    if not runs:
        return []
    run = runs[0]
    if not run.get("error_count"):
        return []
    symbols = run.get("error_symbols") or []
    return [
        {
            "id": f"errors:price-update:{run['id']}",
            "category": "errors",
            "severity": "high",
            "title": f"{run['error_count']} asset(s) failed to update prices",
            "detail": (
                f"Last run ({str(run.get('finished_at', ''))[:16]}): "
                + (", ".join(symbols) if symbols else "see Diagnostics for details")
            ),
            "link_page": "diagnostics",
            "context": {"run_id": run["id"], "symbols": symbols},
        }
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_action_items.py -v`
Expected: 8 passed.

- [ ] **Step 5: Format and lint**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/services/action_items.py tests/unit/test_action_items.py && UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/services/action_items.py tests/unit/test_action_items.py --max-line-length=88 --extend-ignore=E203,W503,E501`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add portf_manager/services/action_items.py tests/unit/test_action_items.py
git commit -m "feat: action items service — stale imports, DQ, price-update checks

First three of six Action Items checks (see
docs/superpowers/specs/2026-07-16-action-items-design.md). Router +
remaining checks land in follow-up commits."
```

---

### Task 3: Action Items service — stale research, goals, price alerts, aggregator

**Files:**
- Modify: `portf_manager/services/action_items.py` (append to the file from Task 2)
- Modify: `tests/unit/test_action_items.py` (append to the file from Task 2)

**Interfaces:**
- Consumes: `compute_positions(transactions)` from `portf_manager.positions` (returns `(positions_dict, ...)`, `positions_dict[asset_id] = {"quantity": float, "cost": float, ...}`); `db.get_all_transactions()`, `db.get_latest_research_notes()`, `db.get_asset(asset_id)`, `db.create_goal(...)` (test only); `list_goals(db, api_key_info)` from `portf_server.routers.goals` (returns list of dicts with `id, name, on_track, projected_value_eur, target_amount_eur, target_date, required_monthly_eur`); `check_watchlist_alerts(db, api_key_info)` from `portf_server.routers.watchlist` (returns `{"alerts": [...]}`, each alert has `symbol, name, price, buy_below`); `compute_price_target_alerts(db)` from `portf_server.routers.research` (Task 1's new function).
- Produces: `check_stale_research(db, today=None) -> list[dict]`, `check_goals_off_track(db) -> list[dict]`, `check_price_alerts(db) -> list[dict]`, `get_action_items(db) -> list[dict]` (the aggregator — sorted by severity, each check wrapped in try/except). Task 4's router calls `get_action_items(db)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_action_items.py` (add these imports to the existing `from portf_manager.services.action_items import (...)` line, alphabetically: `check_goals_off_track, check_price_alerts, check_stale_research, get_action_items`, and add `import pytest` + `from httpx import AsyncClient` at the top):

```python
import pytest
from httpx import AsyncClient
```

(add just below the existing `from datetime import date, timedelta` line)

Update the existing import block to:

```python
from portf_manager.services.action_items import (
    check_data_quality,
    check_goals_off_track,
    check_price_alerts,
    check_price_update_failures,
    check_stale_imports,
    check_stale_research,
    get_action_items,
)
```

Then append these test classes at the end of the file:

```python
class TestStaleResearch:
    def test_flags_held_asset_with_no_research(self, test_database):
        _pid, aid = _portfolio_with_transaction(test_database, days_ago=5)
        items = check_stale_research(test_database)
        assert len(items) == 1
        assert "TestBrokerSYM" in items[0]["detail"]

    def test_flags_held_asset_with_old_research(self, test_database):
        _pid, aid = _portfolio_with_transaction(test_database, days_ago=5)
        note_id = test_database.create_research_note(
            asset_id=aid, symbol="TestBrokerSYM", thesis="x", conviction=3
        )
        old_date = (date.today() - timedelta(days=120)).isoformat()
        with test_database.get_connection() as conn:
            conn.execute(
                "UPDATE research_notes SET created_at = ? WHERE id = ?",
                (old_date, note_id),
            )
            conn.commit()
        items = check_stale_research(test_database)
        assert len(items) == 1

    def test_no_item_when_research_is_fresh(self, test_database):
        _pid, aid = _portfolio_with_transaction(test_database, days_ago=5)
        test_database.create_research_note(
            asset_id=aid, symbol="TestBrokerSYM", thesis="x", conviction=3
        )
        assert check_stale_research(test_database) == []

    def test_no_item_when_nothing_held(self, test_database):
        assert check_stale_research(test_database) == []


class TestGoalsOffTrack:
    def test_flags_off_track_goal(self, test_database):
        test_database.create_goal(
            name="Retire early",
            target_amount_eur=10_000_000,
            target_date="2027-01-01",
            monthly_contribution_eur=100,
            expected_return_pct=1,
        )
        items = check_goals_off_track(test_database)
        assert len(items) == 1
        assert items[0]["context"]["goal_id"]

    def test_no_item_when_no_goals(self, test_database):
        assert check_goals_off_track(test_database) == []


class TestPriceAlerts:
    def test_no_items_when_nothing_configured(self, test_database):
        assert check_price_alerts(test_database) == []


class TestGetActionItems:
    def test_aggregates_and_sorts_by_severity(self, test_database):
        test_database.record_price_update_run(
            started_at="2026-07-15T20:00:00",
            duration_seconds=1.0,
            updated_count=0,
            skipped_count=0,
            error_count=1,
            error_symbols=["AAPL"],
            source="cron",
        )
        _portfolio_with_transaction(test_database, days_ago=90)
        items = get_action_items(test_database)
        order = {"high": 0, "medium": 1, "low": 2}
        severities = [i["severity"] for i in items]
        assert severities == sorted(severities, key=lambda s: order[s])

    def test_empty_db_returns_empty_list(self, test_database):
        assert get_action_items(test_database) == []


class TestActionItemsEndpoint:
    @pytest.mark.asyncio
    async def test_endpoint_returns_items_and_timestamp(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        _portfolio_with_transaction(test_database, days_ago=90)
        resp = await async_test_client.get(
            "/api/v1/action-items/", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data and "generated_at" in data
```

Note: `TestActionItemsEndpoint` will keep failing until Task 4 registers the router — that's expected; it documents the target for this task's next step.

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_action_items.py -v`
Expected: `TestStaleResearch`, `TestGoalsOffTrack`, `TestPriceAlerts`, `TestGetActionItems` FAIL with `ImportError` (functions don't exist yet); `TestActionItemsEndpoint` also fails (404, no router yet — expected until Task 4).

- [ ] **Step 3: Append the remaining check functions and the aggregator**

Append to `portf_manager/services/action_items.py`:

```python
STALE_RESEARCH_DAYS = 90


def check_stale_research(db, today: date = None) -> list[dict]:
    """Held assets with no research note in the last 90 days."""
    from portf_manager.positions import compute_positions

    today = today or date.today()
    positions, _ = compute_positions(db.get_all_transactions())
    held_asset_ids = {aid for aid, pos in positions.items() if pos["quantity"] > 0}
    if not held_asset_ids:
        return []

    latest_by_asset = {}
    for note in db.get_latest_research_notes():
        if note.get("asset_id") in held_asset_ids:
            latest_by_asset[note["asset_id"]] = note

    stale_symbols = []
    for aid in held_asset_ids:
        note = latest_by_asset.get(aid)
        if note is None:
            asset = db.get_asset(aid) or {}
            stale_symbols.append(asset.get("symbol", f"#{aid}"))
            continue
        created = _parse_date(note.get("created_at"))
        if created is None or (today - created).days >= STALE_RESEARCH_DAYS:
            stale_symbols.append(note.get("symbol", f"#{aid}"))

    if not stale_symbols:
        return []
    return [
        {
            "id": "errors:stale-research",
            "category": "errors",
            "severity": "low",
            "title": (
                f"{len(stale_symbols)} holding(s) not re-valued in "
                f"{STALE_RESEARCH_DAYS}+ days"
            ),
            "detail": ", ".join(sorted(stale_symbols)),
            "link_page": "research",
            "context": {"symbols": sorted(stale_symbols)},
        }
    ]


def check_goals_off_track(db) -> list[dict]:
    """Savings goals whose projected value falls short of their target."""
    from portf_server.routers.goals import list_goals

    items = []
    for g in list_goals(db=db, api_key_info={}):
        if g.get("on_track"):
            continue
        items.append(
            {
                "id": f"goals:{g['id']}",
                "category": "goals",
                "severity": "medium",
                "title": f"Goal \"{g['name']}\" is off track",
                "detail": (
                    f"Projected {g['projected_value_eur']:,.0f} EUR vs target "
                    f"{g['target_amount_eur']:,.0f} EUR by {g['target_date']}. "
                    f"Required monthly contribution: "
                    f"{g.get('required_monthly_eur')} EUR."
                ),
                "link_page": "goals",
                "context": {"goal_id": g["id"]},
            }
        )
    return items


def check_price_alerts(db) -> list[dict]:
    """Watchlist buy-zone hits and price-target crossings."""
    from portf_server.routers.research import compute_price_target_alerts
    from portf_server.routers.watchlist import check_watchlist_alerts

    items = []
    for a in check_watchlist_alerts(db=db, api_key_info={})["alerts"]:
        items.append(
            {
                "id": f"watchlist:{a['symbol']}",
                "category": "watchlist",
                "severity": "medium",
                "title": f"{a['symbol']} dropped into its buy zone",
                "detail": (
                    f"Price {a['price']} at or below buy-below {a['buy_below']}."
                ),
                "link_page": "watchlist",
                "context": {"symbol": a["symbol"]},
            }
        )

    for a in compute_price_target_alerts(db):
        triggers = ", ".join(t["type"] for t in a["triggers"])
        items.append(
            {
                "id": f"research:{a['symbol']}",
                "category": "watchlist",
                "severity": "medium",
                "title": f"{a['symbol']} crossed a price target ({triggers})",
                "detail": (
                    f"Currently held: {a['quantity']} units, unrealised P&L "
                    f"{a['unrealized_pnl']} ({a['unrealized_pnl_pct']}%)."
                ),
                "link_page": "research",
                "context": {"symbol": a["symbol"]},
            }
        )
    return items


_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def get_action_items(db) -> list[dict]:
    """Run every check independently; one failure doesn't take down the rest."""
    import logging

    logger = logging.getLogger(__name__)
    checks = [
        check_stale_imports,
        check_data_quality,
        check_price_update_failures,
        check_stale_research,
        check_goals_off_track,
        check_price_alerts,
    ]
    items = []
    for check in checks:
        try:
            items.extend(check(db))
        except Exception:
            logger.exception(f"Action-items check {check.__name__} failed")
    items.sort(key=lambda i: _SEVERITY_ORDER.get(i["severity"], 99))
    return items
```

- [ ] **Step 4: Run tests to verify all but the endpoint test pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_action_items.py -v`
Expected: everything passes except `TestActionItemsEndpoint::test_endpoint_returns_items_and_timestamp` (still 404 — fixed in Task 4).

- [ ] **Step 5: Format and lint**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/services/action_items.py tests/unit/test_action_items.py && UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/services/action_items.py tests/unit/test_action_items.py --max-line-length=88 --extend-ignore=E203,W503,E501`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add portf_manager/services/action_items.py tests/unit/test_action_items.py
git commit -m "feat: action items service — research, goals, price-alert checks + aggregator

Completes the six-check service module and get_action_items(). Router
registration (Task 4) makes the endpoint test pass."
```

---

### Task 4: Action Items router + app.py registration

**Files:**
- Create: `portf_server/routers/action_items.py`
- Modify: `portf_server/app.py:27-51` (router import), `portf_server/app.py:400-405` (router registration, insert after the `market` router block)
- Test: `tests/unit/test_action_items.py::TestActionItemsEndpoint` (already written in Task 3 — this task makes it pass)

**Interfaces:**
- Consumes: `get_action_items(db)` from `portf_manager.services.action_items` (Task 3).
- Produces: `GET /api/v1/action-items/` → `{"items": [...], "generated_at": "<ISO 8601 UTC>"}`.

- [ ] **Step 1: Confirm the currently-failing endpoint test**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_action_items.py::TestActionItemsEndpoint -v`
Expected: FAIL — 404 (no route registered).

- [ ] **Step 2: Create the router**

Create `portf_server/routers/action_items.py`:

```python
"""Action Items Router — cross-cutting maintenance checklist.

GET /api/v1/action-items/ — aggregated stale-import, data-quality,
price-update-failure, stale-research, off-track-goal, and price-alert
checks. See portf_manager/services/action_items.py for the checks
themselves and docs/superpowers/specs/2026-07-16-action-items-design.md
for the design.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from portf_manager.services.action_items import get_action_items

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


@router.get("/")
def list_action_items(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Aggregated cross-cutting maintenance checklist.

    Plain ``def``: every check calls another router's plain-``def``
    function directly (dq_duplicates, dq_suspicious, list_goals,
    check_watchlist_alerts, compute_price_target_alerts) — none of them are
    coroutines, and there's no yfinance call in this endpoint's own path.
    """
    return {
        "items": get_action_items(db),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 3: Register the router in app.py**

In `portf_server/app.py`, add `action_items` to the router import block (currently lines 27-51):

Change:
```python
from .routers import (
    assets,
    transactions,
```
to:
```python
from .routers import (
    action_items,
    assets,
    transactions,
```

Then, right after the `market` router registration block (currently lines 400-405, immediately before the `system` router block), insert:

```python
app.include_router(
    action_items.router,
    prefix="/api/v1/action-items",
    tags=["Action Items"],
    dependencies=_PROTECTED,
)
```

- [ ] **Step 4: Run the full action-items test file**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_action_items.py -v`
Expected: all tests pass, including `TestActionItemsEndpoint`.

- [ ] **Step 5: Run the auth-coverage guard test**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_auth_coverage.py -v`
Expected: PASS (the new router is under `dependencies=_PROTECTED`, so it's automatically covered).

- [ ] **Step 6: Format and lint**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/action_items.py portf_server/app.py && UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_server/routers/action_items.py portf_server/app.py --max-line-length=88 --extend-ignore=E203,W503,E501`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add portf_server/routers/action_items.py portf_server/app.py
git commit -m "feat: register GET /api/v1/action-items/ endpoint"
```

---

### Task 5: Frontend — nav entry + page markup

**Files:**
- Modify: `web_client/index.html:137-139` and `:198-200` (nav links, both sidebar copies), `web_client/index.html` (new page container, inserted before line 2494's `diagnosticsPage` div)

**Interfaces:**
- Produces: a `data-page="actionitems"` nav link (desktop + offcanvas mobile copies) and an `#actionitemsPage` container with an `#actionItemsList` content div and a `#refreshActionItems` button — all consumed by Task 6's JS.

- [ ] **Step 1: Add the nav link to both sidebar copies**

Both copies are byte-identical 3-line blocks. Use one edit with `replace_all: true` on:

```html
                <a class="sidebar-nav-link active" href="#" data-page="dashboard">
                    <i class="bi bi-house-door me-2"></i>Dashboard
                </a>
```

replaced with:

```html
                <a class="sidebar-nav-link active" href="#" data-page="dashboard">
                    <i class="bi bi-house-door me-2"></i>Dashboard
                </a>

                <a class="sidebar-nav-link" href="#" data-page="actionitems">
                    <i class="bi bi-list-check me-2"></i>Action Items
                </a>
```

- [ ] **Step 2: Verify both copies were updated**

Run: `grep -c 'data-page="actionitems"' web_client/index.html`
Expected: `2`

- [ ] **Step 3: Add the page container**

In `web_client/index.html`, immediately before the line `<div id="diagnosticsPage" class="page-content" style="display: none;">`, insert:

```html
                <div id="actionitemsPage" class="page-content" style="display: none;">
                    <div class="d-flex align-items-center justify-content-between mb-3">
                        <div>
                            <h4 class="mb-0"><i class="bi bi-list-check me-2 text-primary"></i>Action Items<button class="btn btn-sm btn-link p-0 ms-2 align-baseline" onclick="showPageHelp('actionitems')" title="What is this page?"><i class="bi bi-question-circle"></i></button></h4>
                            <p class="text-muted small mb-0">Everything that needs your attention, in one place.</p>
                        </div>
                        <div class="d-flex gap-2">
                            <button class="btn btn-sm btn-outline-secondary" id="refreshActionItems" title="Refresh"><i class="bi bi-arrow-clockwise"></i></button>
                        </div>
                    </div>
                    <div id="actionItemsList">
                        <div class="text-muted small">Loading…</div>
                    </div>
                </div>

```

- [ ] **Step 4: Verify the page container was inserted**

Run: `grep -c 'id="actionitemsPage"' web_client/index.html`
Expected: `1`

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html
git commit -m "feat: add Action Items nav entry and page container to index.html

Markup only — no JS wiring yet, so the link currently shows an empty
page (Task 6 wires the fetch/render/dismiss logic)."
```

---

### Task 6: Frontend — apiClient method, merge logic, render/load, nav wiring, help text

**Files:**
- Modify: `web_client/js/pfm_core.js` (new `apiClient.getActionItems()` method, inserted after the existing `getDQSuspicious()` method around line 1624)
- Modify: `web_client/js/pfm_features.js` (new section: dismiss helpers, `mergeActionItems`, render/load functions; plus edits to the existing `showPage`/`loadPageData` nav wiring)
- Modify: `web_client/js/help_text.js` (new `PAGE_HELP.actionitems` entry)
- Modify: `web_client/js/tests/web_client.test.mjs` (new tests for `mergeActionItems`)

**Interfaces:**
- Consumes: `GET /api/v1/action-items/` (Task 4), `apiClient.getNetworth()` and `apiClient.getCashflow()` (existing, `pfm_core.js:1474` and `:1519`), `computeNetWorthChecklist(items, cashflowItems, deposits) -> {checklist, attention}` (existing, `pfm_analytics.js:75`), `esc()` (existing, `pfm_core.js`).
- Produces: `window.mergeActionItems(backendItems, netWorthResult, dismissedIds) -> list[dict]` (pure, unit-tested), `window.loadActionItemsPage()` (called by the nav switch-case), `apiClient.getActionItems()`.

- [ ] **Step 1: Add the apiClient method**

In `web_client/js/pfm_core.js`, immediately after the existing `getDQSuspicious()` method (ends at line 1624 with `},`), insert:

```javascript

        async getActionItems() {
            const resp = await fetch(this.baseURL + '/api/v1/action-items/', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load action items');
            return resp.json();
        },
```

- [ ] **Step 2: Write the failing JS tests for `mergeActionItems`**

Append to `web_client/js/tests/web_client.test.mjs` (at the end of the file):

```javascript

test("mergeActionItems: combines backend items with open net-worth checklist items", () => {
    const { mergeActionItems } = loadAppIntoContext();
    const backend = [
        { id: "a", category: "errors", severity: "high", title: "A", detail: "", link_page: "diagnostics" },
    ];
    const nw = {
        checklist: [{ key: "bank_accounts", label: "Bank accounts", done: false, hint: "Add one" }],
        attention: [],
    };
    const merged = mergeActionItems(backend, nw, []);
    assert.equal(merged.length, 2);
    assert.ok(merged.some(i => i.id === "networth:bank_accounts"));
});

test("mergeActionItems: done checklist items are excluded", () => {
    const { mergeActionItems } = loadAppIntoContext();
    const nw = { checklist: [{ key: "bank_accounts", label: "Bank accounts", done: true, hint: "" }], attention: [] };
    assert.equal(mergeActionItems([], nw, []).length, 0);
});

test("mergeActionItems: matured deposits become medium-severity items", () => {
    const { mergeActionItems } = loadAppIntoContext();
    const nw = { checklist: [], attention: [{ id: 7, name: "Term Deposit", maturity_date: "2026-01-01", days_overdue: 10 }] };
    const merged = mergeActionItems([], nw, []);
    assert.equal(merged.length, 1);
    assert.equal(merged[0].id, "networth:deposit:7");
    assert.equal(merged[0].severity, "medium");
});

test("mergeActionItems: dismissed ids are filtered out", () => {
    const { mergeActionItems } = loadAppIntoContext();
    const backend = [
        { id: "a", category: "errors", severity: "high", title: "A", detail: "", link_page: "diagnostics" },
    ];
    assert.equal(mergeActionItems(backend, { checklist: [], attention: [] }, ["a"]).length, 0);
});

test("mergeActionItems: sorts by severity high -> medium -> low", () => {
    const { mergeActionItems } = loadAppIntoContext();
    const backend = [
        { id: "low1", category: "errors", severity: "low", title: "L", detail: "", link_page: "x" },
        { id: "high1", category: "errors", severity: "high", title: "H", detail: "", link_page: "x" },
        { id: "med1", category: "errors", severity: "medium", title: "M", detail: "", link_page: "x" },
    ];
    const merged = mergeActionItems(backend, { checklist: [], attention: [] }, []);
    assert.deepEqual(merged.map(i => i.id), ["high1", "med1", "low1"]);
});
```

- [ ] **Step 3: Run the JS tests to verify the new ones fail**

Run: `node --test web_client/js/tests/`
Expected: the 5 new tests FAIL with `TypeError: mergeActionItems is not a function` (or `undefined`); all pre-existing tests still PASS.

- [ ] **Step 4: Implement `mergeActionItems` and the page render/load logic**

In `web_client/js/pfm_features.js`, add this new section immediately before the existing `// ---------------------------------------------------------------------------\n// Navigation Manager` comment block (around line 274):

```javascript
// ---------------------------------------------------------------------------
// Action Items — cross-cutting maintenance checklist
// ---------------------------------------------------------------------------
const ACTIONITEMS_SEVERITY_ORDER = { high: 0, medium: 1, low: 2 };

function _actionItemDismiss(id) {
    const items = JSON.parse(localStorage.getItem('pfmDismissedActionItems') || '[]');
    if (!items.some(i => i.id === id)) {
        items.push({ id, dismissed_at: new Date().toISOString() });
        localStorage.setItem('pfmDismissedActionItems', JSON.stringify(items));
    }
}

// Pure: converts the Net Worth checklist's {checklist, attention} shape into
// action-item-shaped objects, merges with the backend-provided items,
// filters dismissed ids, and sorts by severity. Unit-tested in
// web_client/js/tests/web_client.test.mjs.
function mergeActionItems(backendItems, netWorthResult, dismissedIds) {
    backendItems = backendItems || [];
    dismissedIds = dismissedIds || [];
    const nw = netWorthResult || { checklist: [], attention: [] };

    const nwItems = (nw.checklist || [])
        .filter(c => !c.done)
        .map(c => ({
            id: `networth:${c.key}`,
            category: 'networth',
            severity: 'low',
            title: c.label,
            detail: c.hint,
            link_page: 'networth',
            context: {},
        }));

    const attentionItems = (nw.attention || []).map(a => ({
        id: `networth:deposit:${a.id}`,
        category: 'networth',
        severity: 'medium',
        title: `${a.name} matured ${a.days_overdue} day${a.days_overdue === 1 ? '' : 's'} ago`,
        detail: 'Mark it matured to include the payout in your net worth.',
        link_page: 'networth',
        context: { deposit_id: a.id },
    }));

    const dismissedSet = new Set(dismissedIds);
    return [...backendItems, ...nwItems, ...attentionItems]
        .filter(i => !dismissedSet.has(i.id))
        .sort((a, b) => (ACTIONITEMS_SEVERITY_ORDER[a.severity] ?? 99) - (ACTIONITEMS_SEVERITY_ORDER[b.severity] ?? 99));
}
window.mergeActionItems = mergeActionItems;

const ACTIONITEMS_CATEGORY_LABELS = {
    import: 'Broker Imports', data_quality: 'Data Quality', errors: 'Errors',
    goals: 'Goals', watchlist: 'Price Alerts', networth: 'Net Worth',
};
const ACTIONITEMS_SEVERITY_BADGE = {
    high: 'text-bg-danger', medium: 'text-bg-warning', low: 'text-bg-secondary',
};

function _renderActionItems(items) {
    const wrap = document.getElementById('actionItemsList');
    if (!wrap) return;
    if (!items.length) {
        wrap.innerHTML = '<div class="alert alert-success"><i class="bi bi-check-circle me-1"></i>All clear — nothing needs your attention.</div>';
        return;
    }
    wrap.innerHTML = items.map(item => `
        <div class="card mb-2">
            <div class="card-body py-2 d-flex justify-content-between align-items-start">
                <div>
                    <span class="badge ${ACTIONITEMS_SEVERITY_BADGE[item.severity] || 'text-bg-secondary'} me-2">${esc(item.severity)}</span>
                    <span class="text-muted small">${esc(ACTIONITEMS_CATEGORY_LABELS[item.category] || item.category)}</span>
                    <div class="fw-semibold">${esc(item.title)}</div>
                    <div class="small text-muted">${esc(item.detail || '')}</div>
                </div>
                <div class="d-flex gap-2 ms-2 flex-shrink-0">
                    <a href="#" data-page="${esc(item.link_page)}" class="btn btn-sm btn-outline-primary">Go to page</a>
                    <button class="btn btn-sm btn-outline-secondary" onclick="window._dismissActionItem('${item.id}')" title="Dismiss"><i class="bi bi-x-lg"></i></button>
                </div>
            </div>
        </div>`).join('');
}

async function loadActionItemsPage() {
    const wrap = document.getElementById('actionItemsList');
    if (!wrap) return;

    const refreshBtn = document.getElementById('refreshActionItems');
    if (refreshBtn && !refreshBtn._wired) {
        refreshBtn._wired = true;
        refreshBtn.addEventListener('click', () => loadActionItemsPage());
    }

    wrap.innerHTML = '<div class="text-muted small">Loading…</div>';
    try {
        const [backendData, nwData, cfData] = await Promise.all([
            window.apiClient.getActionItems(),
            window.apiClient.getNetworth(),
            window.apiClient.getCashflow(),
        ]);
        const nwResult = computeNetWorthChecklist(nwData.items, (cfData && cfData.items) || [], nwData.deposits);
        window._lastActionItems = { backendItems: backendData.items, nwResult };
        const dismissed = JSON.parse(localStorage.getItem('pfmDismissedActionItems') || '[]').map(i => i.id);
        _renderActionItems(mergeActionItems(backendData.items, nwResult, dismissed));
    } catch (err) {
        wrap.innerHTML = `<div class="text-danger small">Could not load action items: ${esc(err.message)}</div>`;
    }
}
window.loadActionItemsPage = loadActionItemsPage;

window._dismissActionItem = function(id) {
    _actionItemDismiss(id);
    if (window._lastActionItems) {
        const dismissed = JSON.parse(localStorage.getItem('pfmDismissedActionItems') || '[]').map(i => i.id);
        _renderActionItems(mergeActionItems(window._lastActionItems.backendItems, window._lastActionItems.nwResult, dismissed));
    }
};
```

- [ ] **Step 5: Run the JS tests to verify they now pass**

Run: `node --test web_client/js/tests/`
Expected: all tests pass, including the 5 new ones.

- [ ] **Step 6: Wire the new page into navigation**

In `web_client/js/pfm_features.js`, three edits inside `createNavigationManager()`:

Change the `pages` array (currently ends `..., 'networthPage', 'diagnosticsPage'];`) to:
```javascript
            const pages = ['dashboardPage', 'assetsPage', 'transactionsPage', 'holdingsPage', 'analyticsPage', 'watchlistPage', 'goalsPage', 'researchPage', 'chatPage', 'importexportPage', 'portfoliosPage', 'forecastPage', 'helpPage', 'versionPage', 'aboutPage', 'resourcesPage', 'networthPage', 'diagnosticsPage', 'actionitemsPage'];
```

Change the `PAGE_TITLES` object (currently ends `networth: 'Net Worth', diagnostics: 'Diagnostics',\n            };`) to:
```javascript
            const PAGE_TITLES = {
                dashboard: 'Dashboard', assets: 'Assets', transactions: 'Transactions',
                holdings: 'Holdings', analytics: 'Analytics', watchlist: 'Watchlist',
                goals: 'Goals', research: 'Research', chat: 'AI Chat',
                importexport: 'Import / Export', portfolios: 'Brokers',
                forecast: 'Wealth Simulator', help: 'Help & Guide',
                version: "What's New", about: 'About', resources: 'Resources',
                networth: 'Net Worth', diagnostics: 'Diagnostics',
                actionitems: 'Action Items',
            };
```

Change the `loadPageData` switch (currently ends `case 'diagnostics':  if (window.loadDiagnosticsPage) window.loadDiagnosticsPage(); break;\n            }`) to add one more case right after it:
```javascript
                case 'diagnostics':  if (window.loadDiagnosticsPage) window.loadDiagnosticsPage(); break;
                case 'actionitems':  if (window.loadActionItemsPage) window.loadActionItemsPage(); break;
            }
```

- [ ] **Step 7: Add the help-modal entry**

In `web_client/js/help_text.js`, immediately after the `diagnostics: { ... }` entry closes (find the matching `},` that ends that object — it's the block starting `diagnostics: {\n    title: "Diagnostics",` seen earlier), insert a new entry:

```javascript
  actionitems: {
    title: "Action Items",
    body: `
      <p>A single, dismissible checklist of everything that needs your attention across the app — pulled from Diagnostics, Net Worth, Goals, Watchlist, and Research so you don't have to visit each page separately.</p>
      <ul class="mb-2 small">
        <li><strong>Broker Imports</strong>: a broker/portfolio with transaction history but no activity in 60+ days.</li>
        <li><strong>Data Quality</strong>: possible duplicate transactions or suspicious patterns (same checks as the Diagnostics Data Quality tab).</li>
        <li><strong>Errors</strong>: the most recent price-update run had failures, or held positions haven't been re-valued in 90+ days.</li>
        <li><strong>Goals</strong>: a savings goal is projected to miss its target.</li>
        <li><strong>Price Alerts</strong>: a watchlist symbol dropped into its buy zone, or a held position crossed a saved price target.</li>
        <li><strong>Net Worth</strong>: setup gaps (missing bank balance, income, etc.) or a fixed deposit past maturity — same checklist as the Net Worth page.</li>
      </ul>
      <p class="text-muted small mb-0">Dismissing an item hides it until the underlying issue changes (e.g. a new failing price-update run gets its own item).</p>`
  },
```

- [ ] **Step 8: Format and lint the JS (no formatter configured — visual check only)**

Run: `node --check web_client/js/pfm_core.js && node --check web_client/js/pfm_features.js && node --check web_client/js/help_text.js`
Expected: no syntax errors (empty output on success).

- [ ] **Step 9: Run the full JS test suite**

Run: `node --test web_client/js/tests/`
Expected: all tests pass (pre-existing + new).

- [ ] **Step 10: Commit**

```bash
git add web_client/js/pfm_core.js web_client/js/pfm_features.js web_client/js/help_text.js web_client/js/tests/web_client.test.mjs
git commit -m "feat: wire up the Action Items page — fetch, merge, render, dismiss

apiClient.getActionItems(), mergeActionItems() (merges backend items with
the existing Net Worth checklist client-side, unit-tested), render/load
logic, nav wiring, and help text."
```

---

### Task 7: Docs + full verification

**Files:**
- Modify: `PROJECT_STATUS.md` (new version entry)
- Modify: `CLAUDE.md` (new "Action Items API" section)

- [ ] **Step 1: Add a PROJECT_STATUS.md entry**

At the top of the `**Recent (vX.X.X):**` list in `PROJECT_STATUS.md`, bump "Last updated" to today's date and add (using the next patch version after whatever is currently at the top of the file):

```markdown
**Recent (vNEXT):** **Action Items page** — new `GET /api/v1/action-items/` endpoint aggregates six maintenance checks server-side (stale broker imports 60+ days, data-quality summary reusing the existing dq/* checks, price-update-run failures, held positions not re-valued in 90+ days, off-track goals, watchlist/price-target alerts) via `portf_manager/services/action_items.py`. New "Action Items" nav page merges that response with the existing (unchanged, still client-only) Net Worth setup checklist and renders one severity-sorted, dismissible list (`localStorage["pfmDismissedActionItems"]`, same pattern as the Data Quality tab's dismissals). `compute_price_target_alerts()` extracted from the `/research/alerts/check` endpoint so it can be reused without re-triggering push notifications. 20 new backend tests, 5 new JS tests.
```

(Read the current top of the file first to substitute the correct next version number and preserve the existing entries below it unchanged.)

- [ ] **Step 2: Add a CLAUDE.md section**

In `CLAUDE.md`, immediately after the "### Watchlist / Goals / Sync APIs" section (find it by its `### Watchlist / Goals / Sync APIs` heading) and before the next `###` heading, insert:

```markdown
### Action Items API (`portf_server/routers/action_items.py` + `services/action_items.py`)
- `GET /api/v1/action-items/` — plain `def`; aggregates six independent checks (each wrapped in try/except so one failure doesn't take down the rest), sorted by severity: stale broker imports (`import`, 60+ days no transaction/booking), data-quality summary (`data_quality`, reuses `dq_duplicates`/`dq_suspicious` in-process — **not** `dq_reconciliation`, which has no automatic pass/fail threshold, only informational implied-cash figures for manual comparison), price-update-run failures (`errors`, latest run's `error_count`/`error_symbols`), stale research on held positions (`errors`, no `research_notes` row in 90+ days — detects staleness, not past LLM-call failures, since only successful saves are persisted), off-track goals (`goals`, reuses `list_goals`'s `on_track`), watchlist/price-target alerts (`watchlist`, reuses `check_watchlist_alerts` and the extracted `compute_price_target_alerts`). Response: `{"items": [...], "generated_at": "..."}`, each item `{id, category, severity, title, detail, link_page, context}`.
- **Net Worth gaps are deliberately NOT included server-side** — the frontend Action Items page fetches `GET /api/v1/networth/` + `/networth/cashflow` and runs the existing client-only `computeNetWorthChecklist()` against them, merging the result in via `mergeActionItems()` (`pfm_features.js`). Avoids maintaining the same checklist rules in two languages.
- Web: new "Action Items" nav page (top-level, next to Dashboard). Dismissal via `localStorage["pfmDismissedActionItems"]`, same `{id, dismissed_at}` shape as the Diagnostics Data Quality tab's `pfmDismissedIssues`. Item ids are deterministic per entity (`import:portfolio:{id}`, `dq:duplicates`, `errors:price-update:{run_id}`, `goals:{goal_id}`, ...) so dismissing one doesn't hide a *new* occurrence (e.g. a later failing price-update run has a different `run_id`).
- `research.py`'s `/alerts/check` endpoint delegates to `compute_price_target_alerts(db)`, a pure function extracted so the Action Items aggregator can reuse the same alert computation without re-triggering `send_alerts_push()` on every page load.
```

- [ ] **Step 3: Run the full unit test suite**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q`
Expected: all tests pass (previous baseline 729 passed, 6 skipped + ~20 new tests from this feature).

- [ ] **Step 4: Run the full JS test suite**

Run: `node --test web_client/js/tests/`
Expected: all tests pass.

- [ ] **Step 5: Run flake8 across the whole touched Python surface**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501`
Expected: 0 warnings (per CLAUDE.md's "flake8 currently reports 0 warnings — keep it that way").

- [ ] **Step 6: Commit the docs**

```bash
git add PROJECT_STATUS.md CLAUDE.md
git commit -m "docs: document the Action Items page (v2.5.16)"
```

- [ ] **Step 7: Deploy**

Run:
```bash
docker exec portf_backend_dev kill -HUP 1
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```
Expected: backend picks up the new router without a full restart; web container rebuilds with the new page and redeploys.

- [ ] **Step 8: Manual smoke check**

Open the app, navigate to "Action Items" in the sidebar, confirm the page loads (either showing real items from your data or the "All clear" empty state), confirm a "Go to page" button navigates correctly, confirm a dismiss (×) button removes an item and it stays gone on refresh (until you clear `localStorage["pfmDismissedActionItems"]`).

---

## Plan Self-Review

**Spec coverage:** All six backend checks (stale imports, DQ, price-update failures, stale research, goals, watchlist/price alerts) → Tasks 2–3. Backend endpoint → Task 4. Net-worth exception (client-side merge) → Task 6. Nav placement, dismiss mechanism, severity sort, empty state → Tasks 5–6. Testing (backend + frontend) → embedded in every task. Docs → Task 7. Nothing in the spec is unaddressed.

**Placeholder scan:** No TBD/TODO markers; every step has complete, runnable code (not descriptions of code).

**Type consistency:** `check_*` functions all return `list[dict]` with the same six keys throughout (`id, category, severity, title, detail, link_page, context`) — verified consistent across Tasks 2, 3, and the frontend's `mergeActionItems` (which produces the same shape for `networth`-category items). `get_action_items(db) -> list[dict]` (Task 3) is what Task 4's router calls, matching its usage. `compute_price_target_alerts(db) -> list[dict]` (Task 1) matches its two call sites: `check_alerts` (Task 1) and `check_price_alerts` (Task 3).

**Scope check:** Single cohesive feature (one endpoint, one page) — not decomposed further; each task still produces an independently testable deliverable (Tasks 2–3 are separately runnable/passable test suites even before the router exists in Task 4; Task 5 is visible-but-inert markup before Task 6 wires it up).




