# Fund Look-Through Exposure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Break funds and ETFs down into asset class, region, sector and currency exposure so diversification stops reporting 61% "Unknown", and flag funds that hold the same thing.

**Architecture:** A stored `fund_profiles` row per fund holds weight maps. Region weights come from a versioned `benchmarks.json` keyed by the index a fund tracks; sector and asset-class weights come from yfinance `funds_data`. One exposure service consumes those profiles and replaces the two duplicated breakdown loops (the `/analytics/diversification` endpoint and `portfolio_advisor.gather_diversification`). Overlap detection is a pure function over the same profiles.

**Tech Stack:** Python 3.13, FastAPI, SQLite, pytest, yfinance, vanilla JS (no build step), Node built-in test runner.

**Spec:** `docs/superpowers/specs/2026-09-16-fund-lookthrough-design.md`

## Global Constraints

- **Black formatting, line length 88.** Run `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black <file>` before committing Python.
- **Comments go on the line before the code**, never inline. Type hints on all signatures. Google-style docstrings.
- **Tests:** `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`. JS: `make test-js`. Pre-commit runs black + flake8 + autoflake + the no-real-financial-data check; pre-push runs the full unit suite.
- **flake8 must stay at 0 warnings:** `uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501`.
- **Privacy (public repo):** no real ISINs of held assets, no real portfolio amounts, no absolute home-directory paths (write `~/`). Tests invent names like "Example World Index Fund" and ISINs from the `IE0000000001` family.
- **New tables go in BOTH `_create_all_tables` and `_migrate_to_vN`.** A migration-only add breaks fresh installs with "no such table".
- **Endpoints doing blocking IO (yfinance, LLM, `_fx`) are plain `def`,** never `async def`, so FastAPI runs them in a threadpool.
- **Route ordering:** single-segment literal routes are registered before `/{param}` routes.
- **`/api/v1/analytics/diversification` is additive only.** `~/mcp/scripts/finance_review.py` reads `by_asset_type`, `by_currency`, `by_sector`, `by_country`, `concentration_hhi`. Those keys keep their names and types.
- **Region taxonomy is fixed:** `north_america`, `europe_ex_uk`, `uk`, `japan`, `pacific_ex_japan`, `emerging`, `unknown`.
- **Docs are mandatory** (Task 13): `PROJECT_STATUS.md` and the matching `CLAUDE.md` sections.
- **After Python changes:** `docker exec portf_backend_dev kill -HUP 1`. After a migration: `docker compose restart portf_backend_dev`. After web changes: `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`.

---

### Task 1: Benchmark index table

**Files:**
- Create: `portf_manager/data/benchmarks.json`
- Create: `portf_manager/data/__init__.py` (empty, so the directory ships as package data)
- Create: `portf_manager/services/benchmarks.py`
- Test: `tests/unit/test_benchmarks.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `REGIONS: tuple[str, ...]` — the seven region keys
  - `load_benchmarks() -> dict[str, dict]` — key → entry, cached in a module global
  - `get_benchmark(key: str) -> Optional[dict]`
  - `benchmark_choices() -> list[dict]` — `[{"key", "label", "family", "asset_class"}]`, sorted by label
  - `validate_benchmarks(data: dict) -> list[str]` — human-readable problems, empty when valid
  - `ancestors(key: str) -> list[str]` — the `parent` chain, nearest first, cycle-safe

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_benchmarks.py`:

```python
"""The benchmark index table is hand-edited data, so it gets a schema test."""

import pytest

from portf_manager.services import benchmarks as bm


class TestSchema:
    def test_shipped_file_is_valid(self):
        assert bm.validate_benchmarks(bm.load_benchmarks()) == []

    def test_every_entry_has_the_required_fields(self):
        for key, entry in bm.load_benchmarks().items():
            for field in ("label", "family", "asset_class", "regions", "as_of"):
                assert field in entry, f"{key} missing {field}"

    def test_region_weights_sum_to_one(self):
        for key, entry in bm.load_benchmarks().items():
            total = sum(entry["regions"].values())
            assert abs(total - 1.0) < 0.005, f"{key} regions sum to {total}"

    def test_only_known_region_keys_are_used(self):
        for key, entry in bm.load_benchmarks().items():
            unknown = set(entry["regions"]) - set(bm.REGIONS)
            assert not unknown, f"{key} uses unknown regions {unknown}"

    def test_parents_refer_to_known_keys(self):
        data = bm.load_benchmarks()
        for key, entry in data.items():
            parent = entry.get("parent")
            assert parent is None or parent in data, f"{key} parent {parent} unknown"

    def test_covers_the_index_families_actually_held(self):
        keys = set(bm.load_benchmarks())
        for expected in (
            "msci_world",
            "msci_em",
            "sp500",
            "msci_japan",
            "global_agg_corp",
            "global_agg_gov",
        ):
            assert expected in keys


class TestValidation:
    def test_reports_a_bad_weight_sum(self):
        bad = {
            "x": {
                "label": "X",
                "family": "f",
                "asset_class": {"equity": 1.0},
                "regions": {"uk": 0.5},
                "as_of": "2026-09-01",
            }
        }
        problems = bm.validate_benchmarks(bad)
        assert any("regions sum" in p for p in problems)

    def test_reports_an_unknown_parent(self):
        bad = {
            "x": {
                "label": "X",
                "family": "f",
                "parent": "nope",
                "asset_class": {"equity": 1.0},
                "regions": {"uk": 1.0},
                "as_of": "2026-09-01",
            }
        }
        assert any("parent" in p for p in bm.validate_benchmarks(bad))


class TestAncestors:
    def test_walks_the_parent_chain(self):
        chain = bm.ancestors("sp500")
        assert "msci_world" in chain

    def test_unknown_key_has_no_ancestors(self):
        assert bm.ancestors("nope") == []

    def test_is_cycle_safe(self, monkeypatch):
        cyclic = {
            "a": {"parent": "b", "label": "A", "family": "f",
                  "asset_class": {"equity": 1.0}, "regions": {"uk": 1.0},
                  "as_of": "2026-09-01"},
            "b": {"parent": "a", "label": "B", "family": "f",
                  "asset_class": {"equity": 1.0}, "regions": {"uk": 1.0},
                  "as_of": "2026-09-01"},
        }
        monkeypatch.setattr(bm, "_CACHE", cyclic)
        assert bm.ancestors("a") == ["b"]


class TestChoices:
    def test_returns_key_and_label_sorted(self):
        choices = bm.benchmark_choices()
        labels = [c["label"] for c in choices]
        assert labels == sorted(labels)
        assert all({"key", "label", "family", "asset_class"} <= set(c) for c in choices)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_benchmarks.py -q`
Expected: FAIL — `ModuleNotFoundError: portf_manager.services.benchmarks`

- [ ] **Step 3: Create the data file**

Create `portf_manager/data/__init__.py` as an empty file, then `portf_manager/data/benchmarks.json`. Region weights are approximate index weights as of 2026-09-01 and are meant to be refreshed, which is what `as_of` records:

```json
{
  "acwi_imi": {
    "label": "MSCI ACWI IMI (All World)",
    "family": "global_all_cap",
    "asset_class": {"equity": 1.0},
    "regions": {"north_america": 0.65, "europe_ex_uk": 0.12, "uk": 0.035,
                "japan": 0.055, "pacific_ex_japan": 0.03, "emerging": 0.11},
    "as_of": "2026-09-01",
    "aliases": ["MSCI ACWI", "FTSE All-World", "Solactive GBS Global Markets"]
  },
  "msci_world": {
    "label": "MSCI World (Developed)",
    "family": "developed_global",
    "parent": "acwi_imi",
    "asset_class": {"equity": 1.0},
    "regions": {"north_america": 0.72, "europe_ex_uk": 0.13, "uk": 0.04,
                "japan": 0.06, "pacific_ex_japan": 0.03, "emerging": 0.02},
    "as_of": "2026-09-01",
    "aliases": ["MSCI World Index", "MSCI World IMI", "FTSE Developed",
                "MSCI World All Cap"]
  },
  "msci_world_esg": {
    "label": "MSCI World ESG Screened",
    "family": "developed_global",
    "parent": "acwi_imi",
    "asset_class": {"equity": 1.0},
    "regions": {"north_america": 0.73, "europe_ex_uk": 0.13, "uk": 0.04,
                "japan": 0.055, "pacific_ex_japan": 0.025, "emerging": 0.02},
    "as_of": "2026-09-01",
    "aliases": ["ESG Developed World All Cap", "MSCI World SRI"]
  },
  "sp500": {
    "label": "S&P 500",
    "family": "us_large_cap",
    "parent": "msci_world",
    "asset_class": {"equity": 1.0},
    "regions": {"north_america": 1.0},
    "as_of": "2026-09-01",
    "aliases": ["S&P 500 Index", "MSCI USA"]
  },
  "msci_japan": {
    "label": "MSCI Japan",
    "family": "japan_equity",
    "parent": "msci_world",
    "asset_class": {"equity": 1.0},
    "regions": {"japan": 1.0},
    "as_of": "2026-09-01",
    "aliases": ["MSCI Japan IMI", "TOPIX"]
  },
  "msci_em": {
    "label": "MSCI Emerging Markets",
    "family": "emerging_equity",
    "parent": "acwi_imi",
    "asset_class": {"equity": 1.0},
    "regions": {"emerging": 1.0},
    "as_of": "2026-09-01",
    "aliases": ["MSCI EM", "MSCI EM IMI", "FTSE Emerging All Cap",
                "ESG Emerging Markets All Cap"]
  },
  "msci_china_tech": {
    "label": "MSCI China Tech",
    "family": "china_sector_equity",
    "parent": "msci_em",
    "asset_class": {"equity": 1.0},
    "regions": {"emerging": 1.0},
    "as_of": "2026-09-01",
    "aliases": ["MSCI China Tech Index"]
  },
  "msci_world_staples": {
    "label": "MSCI World Consumer Staples",
    "family": "global_sector_equity",
    "parent": "msci_world",
    "asset_class": {"equity": 1.0},
    "regions": {"north_america": 0.6, "europe_ex_uk": 0.2, "uk": 0.1,
                "japan": 0.07, "pacific_ex_japan": 0.03},
    "as_of": "2026-09-01",
    "aliases": ["MSCI World Consumer Staples Index"]
  },
  "stoxx_600_utilities": {
    "label": "STOXX Europe 600 Utilities",
    "family": "europe_sector_equity",
    "parent": "msci_world",
    "asset_class": {"equity": 1.0},
    "regions": {"europe_ex_uk": 0.85, "uk": 0.15},
    "as_of": "2026-09-01",
    "aliases": ["STOXX Europe 600 Utilities Index"]
  },
  "global_agg_gov": {
    "label": "Global Aggregate Government Bond",
    "family": "global_gov_bond",
    "asset_class": {"bond": 1.0},
    "regions": {"north_america": 0.45, "europe_ex_uk": 0.25, "uk": 0.05,
                "japan": 0.2, "pacific_ex_japan": 0.05},
    "as_of": "2026-09-01",
    "aliases": ["FTSE World Government Bond", "JPM Global Government Bond",
                "Bloomberg Global Aggregate Treasuries"]
  },
  "global_agg_corp": {
    "label": "Global Aggregate Corporate Bond",
    "family": "global_corp_bond",
    "asset_class": {"bond": 1.0},
    "regions": {"north_america": 0.55, "europe_ex_uk": 0.25, "uk": 0.07,
                "japan": 0.08, "pacific_ex_japan": 0.05},
    "as_of": "2026-09-01",
    "aliases": ["Bloomberg Global Aggregate Corporate",
                "Screened Global Corporate Bond"]
  },
  "euro_gov_short": {
    "label": "Euro Government Bond 0-1Y",
    "family": "euro_gov_bond",
    "asset_class": {"bond": 1.0},
    "regions": {"europe_ex_uk": 1.0},
    "as_of": "2026-09-01",
    "aliases": ["Euro Government Bond 0-1 Year", "Euro Government Bond"]
  },
  "euro_corp": {
    "label": "Euro Corporate Bond",
    "family": "euro_corp_bond",
    "asset_class": {"bond": 1.0},
    "regions": {"europe_ex_uk": 0.9, "uk": 0.1},
    "as_of": "2026-09-01",
    "aliases": ["Euro Corporate Bond 0-3Y", "Euro High Yield Corporate Bond"]
  }
}
```

- [ ] **Step 4: Write the loader**

Create `portf_manager/services/benchmarks.py`:

```python
"""Benchmark index table — region weights for the indices funds track.

yfinance exposes a fund's sector weights but no geography at all, so region
exposure is resolved by mapping a fund to the index it tracks and reading that
index's region weights from ``portf_manager/data/benchmarks.json``.

The file is hand-maintained public data. ``validate_benchmarks`` is run against
it by the test suite so an edit cannot silently break the shape.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# The fixed region taxonomy. Region rather than country: it is granular enough
# for allocation targets and cheap enough to keep current by hand.
REGIONS: tuple[str, ...] = (
    "north_america",
    "europe_ex_uk",
    "uk",
    "japan",
    "pacific_ex_japan",
    "emerging",
    "unknown",
)

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "benchmarks.json"

# Parsed file, kept for the process's lifetime. Tests patch this directly.
_CACHE: Optional[dict[str, dict]] = None


def load_benchmarks() -> dict[str, dict]:
    """Return the benchmark table, reading the JSON file once per process."""
    global _CACHE
    if _CACHE is None:
        try:
            with open(_DATA_PATH, encoding="utf-8") as fh:
                _CACHE = json.load(fh)
        except Exception as e:
            logger.error(f"Could not load benchmarks.json: {e}")
            _CACHE = {}
    return _CACHE


def get_benchmark(key: str) -> Optional[dict]:
    """Return one benchmark entry, or None when the key is unknown."""
    return load_benchmarks().get(key)


def benchmark_choices() -> list[dict]:
    """Return the table as dropdown options, sorted by label."""
    out = [
        {
            "key": key,
            "label": entry.get("label", key),
            "family": entry.get("family"),
            "asset_class": entry.get("asset_class", {}),
        }
        for key, entry in load_benchmarks().items()
    ]
    return sorted(out, key=lambda c: c["label"])


def ancestors(key: str) -> list[str]:
    """Return the ``parent`` chain above *key*, nearest first.

    Used by overlap detection to spot a fund nested inside a broader one (an
    S&P 500 fund held alongside a world fund). Cycle-safe: a mis-edited file
    that loops stops instead of hanging.
    """
    data = load_benchmarks()
    chain: list[str] = []
    seen = {key}
    current = data.get(key, {}).get("parent")
    while current and current not in seen:
        chain.append(current)
        seen.add(current)
        current = data.get(current, {}).get("parent")
    return chain


def validate_benchmarks(data: dict) -> list[str]:
    """Return a list of problems with *data*, empty when it is valid."""
    problems: list[str] = []
    for key, entry in data.items():
        for field in ("label", "family", "asset_class", "regions", "as_of"):
            if field not in entry:
                problems.append(f"{key}: missing '{field}'")
        regions = entry.get("regions", {})
        unknown = set(regions) - set(REGIONS)
        if unknown:
            problems.append(f"{key}: unknown regions {sorted(unknown)}")
        if regions:
            total = sum(regions.values())
            if abs(total - 1.0) >= 0.005:
                problems.append(f"{key}: regions sum to {total:.3f}, expected 1.0")
        classes = entry.get("asset_class", {})
        if classes:
            total = sum(classes.values())
            if abs(total - 1.0) >= 0.005:
                problems.append(f"{key}: asset_class sums to {total:.3f}, expected 1.0")
        parent = entry.get("parent")
        if parent is not None and parent not in data:
            problems.append(f"{key}: parent '{parent}' is not a known benchmark")
    return problems
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_benchmarks.py -q`
Expected: PASS (13 tests)

- [ ] **Step 6: Format, lint and commit**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/services/benchmarks.py tests/unit/test_benchmarks.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/ --max-line-length=88 --extend-ignore=E203,W503,E501
git add portf_manager/data/ portf_manager/services/benchmarks.py tests/unit/test_benchmarks.py
git commit -m "feat(exposure): benchmark index table with region weights"
```

---

### Task 2: `fund_profiles` table (db v30)

**Files:**
- Modify: `portf_manager/database.py` (`DATABASE_VERSION`, `_create_all_tables`, `_run_migrations`, new `_migrate_to_v30`, four new helper methods)
- Test: `tests/test_database.py`

**Interfaces:**
- Consumes: nothing
- Produces (all return raw rows — JSON columns stay TEXT, parsed by the service in Task 4):
  - `db.get_fund_profile(asset_id: int) -> Optional[Dict]`
  - `db.list_fund_profiles() -> List[Dict]`
  - `db.upsert_fund_profile(asset_id: int, benchmark_key: Optional[str], source: str, asset_class: str, regions: str, sectors: str, currency_hedged: int, hedge_currency: Optional[str], as_of: str, notes: Optional[str] = None) -> Dict`
  - `db.delete_fund_profile(asset_id: int) -> bool`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_database.py` (follow the existing migration-test class style):

```python
class TestFundProfilesV30:
    """v30 adds fund_profiles for look-through exposure."""

    def test_fresh_database_has_fund_profiles(self, temp_db):
        with temp_db.get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='fund_profiles'"
            ).fetchone()
        assert row is not None

    def test_upsert_then_get_roundtrips(self, temp_db):
        asset_id = temp_db.create_asset(
            symbol="IE0000000001", name="Example World Index Fund", asset_type="etf"
        )
        temp_db.upsert_fund_profile(
            asset_id=asset_id,
            benchmark_key="msci_world",
            source="benchmark",
            asset_class='{"equity": 1.0}',
            regions='{"north_america": 0.72, "europe_ex_uk": 0.28}',
            sectors='{"Technology": 0.25}',
            currency_hedged=0,
            hedge_currency=None,
            as_of="2026-09-01",
        )
        profile = temp_db.get_fund_profile(asset_id)
        assert profile["benchmark_key"] == "msci_world"
        assert profile["source"] == "benchmark"
        assert profile["regions"] == '{"north_america": 0.72, "europe_ex_uk": 0.28}'

    def test_upsert_replaces_an_existing_profile(self, temp_db):
        asset_id = temp_db.create_asset(
            symbol="IE0000000002", name="Example EM Index Fund", asset_type="etf"
        )
        for source in ("benchmark", "manual"):
            temp_db.upsert_fund_profile(
                asset_id=asset_id,
                benchmark_key="msci_em",
                source=source,
                asset_class='{"equity": 1.0}',
                regions='{"emerging": 1.0}',
                sectors="{}",
                currency_hedged=0,
                hedge_currency=None,
                as_of="2026-09-01",
            )
        assert temp_db.get_fund_profile(asset_id)["source"] == "manual"
        assert len(temp_db.list_fund_profiles()) == 1

    def test_delete_removes_it(self, temp_db):
        asset_id = temp_db.create_asset(
            symbol="IE0000000003", name="Example Bond Index Fund", asset_type="etf"
        )
        temp_db.upsert_fund_profile(
            asset_id=asset_id,
            benchmark_key="global_agg_corp",
            source="benchmark",
            asset_class='{"bond": 1.0}',
            regions='{"north_america": 1.0}',
            sectors="{}",
            currency_hedged=1,
            hedge_currency="EUR",
            as_of="2026-09-01",
        )
        assert temp_db.delete_fund_profile(asset_id) is True
        assert temp_db.get_fund_profile(asset_id) is None
        assert temp_db.delete_fund_profile(asset_id) is False

    def test_migrate_to_v30_adds_the_table(self, tmp_path):
        import sqlite3

        db_path = tmp_path / "v29.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE schema_version (version INTEGER)")
        conn.execute("INSERT INTO schema_version VALUES (29)")
        conn.commit()
        conn.close()

        from portf_manager.database import Database

        db = Database(str(db_path))
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='fund_profiles'"
            ).fetchone()
            version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert row is not None
        assert version == 30
```

Also change every `29` version assertion in this file to `30`: the four assertions at roughly lines 53, 1042, 1072 and 1143.

- [ ] **Step 2: Run to verify failure**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/test_database.py -q -k "v30 or version"`
Expected: FAIL — no such table `fund_profiles`, and the version assertions read 29

- [ ] **Step 3: Bump the version and add the table in both places**

In `portf_manager/database.py`, set `DATABASE_VERSION = 30` (line 17).

Add to `_create_all_tables` (alongside the other `CREATE TABLE` calls) and to a new `_migrate_to_v30` the identical DDL:

```python
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_profiles (
                asset_id        INTEGER PRIMARY KEY
                                    REFERENCES assets(id) ON DELETE CASCADE,
                benchmark_key   TEXT,
                source          TEXT NOT NULL DEFAULT 'benchmark'
                                    CHECK (source IN ('benchmark', 'llm', 'manual')),
                asset_class     TEXT NOT NULL DEFAULT '{}',
                regions         TEXT NOT NULL DEFAULT '{}',
                sectors         TEXT NOT NULL DEFAULT '{}',
                currency_hedged INTEGER NOT NULL DEFAULT 0,
                hedge_currency  TEXT,
                as_of           TEXT,
                notes           TEXT,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
```

Register the migration in `_run_migrations`, following the existing chain:

```python
        if current_version < 30:
            self._migrate_to_v30(conn)
```

```python
    def _migrate_to_v30(self, conn: sqlite3.Connection) -> None:
        """Migrate from v29 to v30 — fund look-through profiles.

        Adds fund_profiles: one row per fund-like asset holding the weight maps
        that break it down into asset class, region and sector. Nothing is
        backfilled; a fund without a profile is reported as unclassified rather
        than guessed at.
        """
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_profiles (
                asset_id        INTEGER PRIMARY KEY
                                    REFERENCES assets(id) ON DELETE CASCADE,
                benchmark_key   TEXT,
                source          TEXT NOT NULL DEFAULT 'benchmark'
                                    CHECK (source IN ('benchmark', 'llm', 'manual')),
                asset_class     TEXT NOT NULL DEFAULT '{}',
                regions         TEXT NOT NULL DEFAULT '{}',
                sectors         TEXT NOT NULL DEFAULT '{}',
                currency_hedged INTEGER NOT NULL DEFAULT 0,
                hedge_currency  TEXT,
                as_of           TEXT,
                notes           TEXT,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
```

- [ ] **Step 4: Add the four helper methods**

Place them next to the other per-table helpers in `database.py`:

```python
    def get_fund_profile(self, asset_id: int) -> Optional[Dict]:
        """Get one fund profile by asset id. JSON columns are returned raw."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM fund_profiles WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_fund_profiles(self) -> List[Dict]:
        """Every fund profile, joined to its asset's symbol and name."""
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT f.*, a.symbol, a.name, a.asset_type, a.ticker, a.exchange
                FROM fund_profiles f
                JOIN assets a ON a.id = f.asset_id
                ORDER BY a.symbol
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def upsert_fund_profile(
        self,
        asset_id: int,
        benchmark_key: Optional[str],
        source: str,
        asset_class: str,
        regions: str,
        sectors: str,
        currency_hedged: int,
        hedge_currency: Optional[str],
        as_of: str,
        notes: Optional[str] = None,
    ) -> Dict:
        """Insert or replace a fund profile. Weight maps arrive as JSON text."""
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO fund_profiles (
                    asset_id, benchmark_key, source, asset_class, regions,
                    sectors, currency_hedged, hedge_currency, as_of, notes,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(asset_id) DO UPDATE SET
                    benchmark_key = excluded.benchmark_key,
                    source = excluded.source,
                    asset_class = excluded.asset_class,
                    regions = excluded.regions,
                    sectors = excluded.sectors,
                    currency_hedged = excluded.currency_hedged,
                    hedge_currency = excluded.hedge_currency,
                    as_of = excluded.as_of,
                    notes = excluded.notes,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    asset_id,
                    benchmark_key,
                    source,
                    asset_class,
                    regions,
                    sectors,
                    int(currency_hedged),
                    hedge_currency,
                    as_of,
                    notes,
                ),
            )
            conn.commit()
        return self.get_fund_profile(asset_id)

    def delete_fund_profile(self, asset_id: int) -> bool:
        """Delete a fund profile. Returns False when there was none."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM fund_profiles WHERE asset_id = ?", (asset_id,)
            )
            conn.commit()
            return cur.rowcount > 0
```

Note: unlike `budget_lines.overrides`, which the service parses, these columns are also stored as text here — parsing lives in `services/fund_profiles.py` (Task 4) for the same reason.

- [ ] **Step 5: Run the tests**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/test_database.py -q`
Expected: PASS, including the five new tests

- [ ] **Step 6: Commit**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/database.py tests/test_database.py
git add portf_manager/database.py tests/test_database.py
git commit -m "feat(db): v30 fund_profiles table"
```

---

### Task 3: Fund composition fetch from yfinance

**Files:**
- Modify: `portf_manager/market.py` (new function at the end of the fundamentals section)
- Test: `tests/unit/test_fund_composition.py`

**Interfaces:**
- Consumes: nothing
- Produces: `market.get_fund_composition(db, symbol: str, max_age: float = 604800) -> dict` returning `{"symbol", "sectors": {name: fraction}, "asset_class": {"equity"|"bond"|"cash"|"other": fraction}, "source": "live"|"cache", "stale": bool}`. On failure: `{"symbol", "sectors": {}, "asset_class": {}, "error": str, "stale": True}` — never zero-filled.

yfinance returns sector keys like `realestate`, `consumer_cyclical`. They are normalised to the Title Case names direct stocks report (`Real Estate`, `Consumer Cyclical`) so both sides of the sector breakdown add up.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_fund_composition.py`:

```python
"""Fund composition from yfinance funds_data: sectors + asset-class split."""

from unittest.mock import MagicMock, patch

from portf_manager import market


def _db():
    db = MagicMock()
    db.cache_get.return_value = None
    return db


def _funds_data(sectors=None, classes=None):
    fd = MagicMock()
    fd.sector_weightings = sectors
    fd.asset_classes = classes
    return fd


class TestGetFundComposition:
    def test_normalises_yfinance_sector_keys(self):
        fd = _funds_data(
            sectors={"realestate": 0.02, "consumer_cyclical": 0.1, "technology": 0.25},
            classes={"stockPosition": 1.0},
        )
        with patch.object(market, "_fetch_funds_data", return_value=fd):
            out = market.get_fund_composition(_db(), "EXMPL.DE")
        assert out["sectors"]["Real Estate"] == 0.02
        assert out["sectors"]["Consumer Cyclical"] == 0.1
        assert out["sectors"]["Technology"] == 0.25

    def test_maps_asset_classes(self):
        fd = _funds_data(
            sectors={"technology": 1.0},
            classes={"stockPosition": 0.98, "cashPosition": 0.02},
        )
        with patch.object(market, "_fetch_funds_data", return_value=fd):
            out = market.get_fund_composition(_db(), "EXMPL.DE")
        assert out["asset_class"]["equity"] == 0.98
        assert out["asset_class"]["cash"] == 0.02

    def test_bond_fund_reports_bond(self):
        fd = _funds_data(sectors=None, classes={"bondPosition": 1.0})
        with patch.object(market, "_fetch_funds_data", return_value=fd):
            out = market.get_fund_composition(_db(), "EXBND.DE")
        assert out["asset_class"] == {"bond": 1.0}
        assert out["sectors"] == {}

    def test_failure_returns_empty_maps_not_zeros(self):
        with patch.object(market, "_fetch_funds_data", side_effect=RuntimeError("no")):
            out = market.get_fund_composition(_db(), "NOPE")
        assert out["sectors"] == {}
        assert out["asset_class"] == {}
        assert out["stale"] is True
        assert "error" in out

    def test_uses_cache_when_fresh(self):
        db = MagicMock()
        db.cache_get.return_value = {
            "symbol": "EXMPL.DE",
            "sectors": {"Technology": 1.0},
            "asset_class": {"equity": 1.0},
        }
        with patch.object(market, "_fetch_funds_data") as fetch:
            out = market.get_fund_composition(db, "EXMPL.DE")
        fetch.assert_not_called()
        assert out["source"] == "cache"
```

- [ ] **Step 2: Run to verify failure**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_fund_composition.py -q`
Expected: FAIL — `AttributeError: module 'portf_manager.market' has no attribute 'get_fund_composition'`

- [ ] **Step 3: Implement**

Append to `portf_manager/market.py`:

```python
# yfinance reports fund sector weights under lowercase compact keys; direct
# stocks report Title Case names. Both feed one breakdown, so they must agree.
_YF_SECTOR_NAMES: dict[str, str] = {
    "basic_materials": "Basic Materials",
    "communication_services": "Communication Services",
    "consumer_cyclical": "Consumer Cyclical",
    "consumer_defensive": "Consumer Defensive",
    "energy": "Energy",
    "financial_services": "Financial Services",
    "healthcare": "Healthcare",
    "industrials": "Industrials",
    "realestate": "Real Estate",
    "technology": "Technology",
    "utilities": "Utilities",
}

_YF_ASSET_CLASSES: dict[str, str] = {
    "stockPosition": "equity",
    "bondPosition": "bond",
    "cashPosition": "cash",
    "preferredPosition": "other",
    "convertiblePosition": "other",
    "otherPosition": "other",
}


def _fetch_funds_data(symbol: str):
    """Live yfinance ``funds_data`` accessor. Patched in tests."""
    import yfinance as yf

    return yf.Ticker(symbol).funds_data


def get_fund_composition(db, symbol: str, max_age: float = 7 * 86400) -> dict:
    """Sector weights and the equity/bond/cash split for a fund.

    yfinance has no country or region data for funds, so geography does not
    come from here — it comes from the benchmark index the fund tracks. A
    failed fetch returns empty maps and ``stale``, never zeros, so the caller
    can report the gap instead of presenting it as a real composition.
    """
    sym = symbol.strip().upper()
    key = f"mkt:fundcomp:{sym}"
    try:
        hit = db.cache_get(key)
    except Exception:
        hit = None
    if hit:
        return {**hit, "source": "cache", "stale": False}

    try:
        fd = _fetch_funds_data(sym)
        raw_sectors = fd.sector_weightings or {}
        raw_classes = fd.asset_classes or {}
    except Exception as e:
        logger.warning(f"Could not fetch fund composition for {sym}: {e}")
        return {
            "symbol": sym,
            "sectors": {},
            "asset_class": {},
            "error": str(e),
            "stale": True,
        }

    sectors = {
        _YF_SECTOR_NAMES.get(k, k.replace("_", " ").title()): round(float(v), 4)
        for k, v in raw_sectors.items()
        if v
    }
    asset_class: dict[str, float] = {}
    for raw_key, value in raw_classes.items():
        if not value:
            continue
        mapped = _YF_ASSET_CLASSES.get(raw_key, "other")
        asset_class[mapped] = round(asset_class.get(mapped, 0.0) + float(value), 4)

    data = {"symbol": sym, "sectors": sectors, "asset_class": asset_class}
    if sectors or asset_class:
        try:
            db.cache_set(key, data, max_age)
        except Exception as e:
            logger.warning(f"fund composition cache_set failed for {sym}: {e}")
    return {**data, "source": "live", "stale": False}
```

- [ ] **Step 4: Run the tests**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_fund_composition.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/market.py tests/unit/test_fund_composition.py
git add portf_manager/market.py tests/unit/test_fund_composition.py
git commit -m "feat(market): fund composition (sectors, asset class) from yfinance"
```

---

### Task 4: Fund profile service — parse, validate, build

**Files:**
- Create: `portf_manager/services/fund_profiles.py`
- Test: `tests/unit/test_fund_profiles_service.py`

**Interfaces:**
- Consumes: `benchmarks.get_benchmark`, `benchmarks.REGIONS`, `market.get_fund_composition`
- Produces:
  - `FUND_ASSET_TYPES: frozenset[str]` = `{"etf", "mutual_fund", "index"}`
  - `parse_profile(row: Optional[dict]) -> Optional[dict]` — JSON columns become dicts; a malformed column becomes `{}`
  - `serialize_profile(profile: dict) -> dict` — dict columns become JSON text, ready for `db.upsert_fund_profile`
  - `validate_profile(profile: dict) -> list[str]` — problems, empty when valid
  - `build_from_benchmark(db, asset: dict, benchmark_key: str) -> dict` — a full profile dict with `source="benchmark"`
  - `is_stale(profile: dict, today: date, max_age_days: int = 365) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_fund_profiles_service.py`:

```python
"""Fund profile parsing, validation and construction from a benchmark."""

from datetime import date
from unittest.mock import MagicMock, patch

from portf_manager.services import fund_profiles as fp


class TestParse:
    def test_parses_json_columns(self):
        row = {
            "asset_id": 1,
            "benchmark_key": "msci_world",
            "source": "benchmark",
            "asset_class": '{"equity": 1.0}',
            "regions": '{"north_america": 0.72, "europe_ex_uk": 0.28}',
            "sectors": '{"Technology": 0.25}',
            "currency_hedged": 0,
            "hedge_currency": None,
            "as_of": "2026-09-01",
        }
        parsed = fp.parse_profile(row)
        assert parsed["regions"]["north_america"] == 0.72
        assert parsed["asset_class"] == {"equity": 1.0}
        assert parsed["currency_hedged"] is False

    def test_none_row_returns_none(self):
        assert fp.parse_profile(None) is None

    def test_malformed_json_becomes_empty_map(self):
        row = {
            "asset_id": 1,
            "benchmark_key": None,
            "source": "manual",
            "asset_class": "not json",
            "regions": "{}",
            "sectors": "{}",
            "currency_hedged": 1,
            "hedge_currency": "EUR",
            "as_of": "2026-09-01",
        }
        assert fp.parse_profile(row)["asset_class"] == {}


class TestSerialize:
    def test_roundtrips_through_parse(self):
        profile = {
            "asset_id": 7,
            "benchmark_key": "msci_em",
            "source": "manual",
            "asset_class": {"equity": 1.0},
            "regions": {"emerging": 1.0},
            "sectors": {},
            "currency_hedged": True,
            "hedge_currency": "EUR",
            "as_of": "2026-09-01",
            "notes": None,
        }
        row = fp.serialize_profile(profile)
        assert row["currency_hedged"] == 1
        assert fp.parse_profile({**row, "asset_id": 7})["regions"] == {"emerging": 1.0}


class TestValidate:
    def _valid(self):
        return {
            "asset_class": {"equity": 1.0},
            "regions": {"north_america": 0.5, "emerging": 0.5},
            "sectors": {"Technology": 1.0},
            "benchmark_key": "msci_world",
            "source": "manual",
        }

    def test_accepts_a_valid_profile(self):
        assert fp.validate_profile(self._valid()) == []

    def test_rejects_regions_that_do_not_sum_to_one(self):
        bad = self._valid()
        bad["regions"] = {"north_america": 0.5}
        assert any("regions" in p for p in fp.validate_profile(bad))

    def test_rejects_an_unknown_region_key(self):
        bad = self._valid()
        bad["regions"] = {"mars": 1.0}
        assert any("mars" in p for p in fp.validate_profile(bad))

    def test_rejects_an_unknown_benchmark(self):
        bad = self._valid()
        bad["benchmark_key"] = "nope"
        assert any("benchmark" in p for p in fp.validate_profile(bad))

    def test_rejects_an_unknown_source(self):
        bad = self._valid()
        bad["source"] = "guesswork"
        assert any("source" in p for p in fp.validate_profile(bad))

    def test_allows_empty_sectors(self):
        ok = self._valid()
        ok["sectors"] = {}
        assert fp.validate_profile(ok) == []


class TestBuildFromBenchmark:
    def test_takes_regions_from_the_benchmark_and_sectors_from_yfinance(self):
        asset = {"id": 3, "symbol": "IE0000000001", "ticker": "EXMPL.DE",
                 "currency": "EUR", "name": "Example World Index Fund"}
        composition = {
            "sectors": {"Technology": 0.25, "Healthcare": 0.1},
            "asset_class": {"equity": 1.0},
            "stale": False,
        }
        with patch.object(fp.market, "get_fund_composition", return_value=composition):
            profile = fp.build_from_benchmark(MagicMock(), asset, "msci_world")
        assert profile["source"] == "benchmark"
        assert profile["benchmark_key"] == "msci_world"
        assert profile["regions"]["north_america"] == 0.72
        assert profile["sectors"]["Technology"] == 0.25
        assert profile["asset_class"] == {"equity": 1.0}

    def test_falls_back_to_the_benchmark_asset_class_when_yfinance_fails(self):
        asset = {"id": 4, "symbol": "IE0000000002", "ticker": None,
                 "currency": "EUR", "name": "Example Bond Index Fund"}
        composition = {"sectors": {}, "asset_class": {}, "stale": True}
        with patch.object(fp.market, "get_fund_composition", return_value=composition):
            profile = fp.build_from_benchmark(MagicMock(), asset, "global_agg_corp")
        assert profile["asset_class"] == {"bond": 1.0}
        assert profile["sectors"] == {}

    def test_unknown_benchmark_raises(self):
        asset = {"id": 5, "symbol": "IE0000000003", "ticker": "X", "currency": "EUR"}
        try:
            fp.build_from_benchmark(MagicMock(), asset, "nope")
        except ValueError as e:
            assert "nope" in str(e)
        else:
            raise AssertionError("expected ValueError")

    def test_uses_the_symbol_when_no_ticker_is_set(self):
        asset = {"id": 6, "symbol": "EXMPL.DE", "ticker": None, "currency": "EUR"}
        with patch.object(
            fp.market, "get_fund_composition",
            return_value={"sectors": {}, "asset_class": {}, "stale": True},
        ) as get_comp:
            fp.build_from_benchmark(MagicMock(), asset, "msci_world")
        assert get_comp.call_args[0][1] == "EXMPL.DE"


class TestIsStale:
    def test_older_than_a_year_is_stale(self):
        assert fp.is_stale({"as_of": "2025-01-01"}, date(2026, 9, 16)) is True

    def test_recent_is_not_stale(self):
        assert fp.is_stale({"as_of": "2026-06-01"}, date(2026, 9, 16)) is False

    def test_missing_as_of_counts_as_stale(self):
        assert fp.is_stale({"as_of": None}, date(2026, 9, 16)) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_fund_profiles_service.py -q`
Expected: FAIL — `ModuleNotFoundError: portf_manager.services.fund_profiles`

- [ ] **Step 3: Implement**

Create `portf_manager/services/fund_profiles.py`:

```python
"""Fund profiles — the stored weight maps that make a fund see-through.

A profile is stored rather than computed per request so it can be reviewed and
corrected, and so a diversification call needs no live fund lookup. The DB layer
keeps the weight maps as JSON text; parsing and validation live here, the same
split budget_lines.overrides uses.
"""

import json
import logging
from datetime import date, datetime
from typing import Any, Optional

from portf_manager import market
from portf_manager.services import benchmarks

logger = logging.getLogger(__name__)

# Asset types that need a profile to be seen through.
FUND_ASSET_TYPES: frozenset[str] = frozenset({"etf", "mutual_fund", "index"})

_SOURCES = ("benchmark", "llm", "manual")

# A profile older than this is reported stale — index weights drift a few points
# a year, which is slow enough to be worth an annual review, not a refetch.
STALE_AFTER_DAYS = 365

_JSON_COLUMNS = ("asset_class", "regions", "sectors")


def _loads(value: Any) -> dict:
    """Parse a JSON weight map, returning {} for anything malformed."""
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_profile(row: Optional[dict]) -> Optional[dict]:
    """Turn a DB row into a profile with real dicts and a bool hedge flag."""
    if row is None:
        return None
    profile = dict(row)
    for column in _JSON_COLUMNS:
        profile[column] = _loads(profile.get(column))
    profile["currency_hedged"] = bool(profile.get("currency_hedged"))
    return profile


def serialize_profile(profile: dict) -> dict:
    """Turn a profile into the keyword arguments db.upsert_fund_profile takes."""
    return {
        "asset_id": profile["asset_id"],
        "benchmark_key": profile.get("benchmark_key"),
        "source": profile.get("source", "manual"),
        "asset_class": json.dumps(profile.get("asset_class", {})),
        "regions": json.dumps(profile.get("regions", {})),
        "sectors": json.dumps(profile.get("sectors", {})),
        "currency_hedged": 1 if profile.get("currency_hedged") else 0,
        "hedge_currency": profile.get("hedge_currency"),
        "as_of": profile.get("as_of"),
        "notes": profile.get("notes"),
    }


def _sums_to_one(weights: dict) -> bool:
    return abs(sum(weights.values()) - 1.0) < 0.005


def validate_profile(profile: dict) -> list[str]:
    """Return a list of problems with *profile*, empty when it is valid."""
    problems: list[str] = []

    regions = profile.get("regions") or {}
    unknown = set(regions) - set(benchmarks.REGIONS)
    if unknown:
        problems.append(f"unknown region keys: {sorted(unknown)}")
    if not regions or not _sums_to_one(regions):
        problems.append(f"regions must sum to 1.0, got {sum(regions.values()):.3f}")

    classes = profile.get("asset_class") or {}
    if not classes or not _sums_to_one(classes):
        problems.append(
            f"asset_class must sum to 1.0, got {sum(classes.values()):.3f}"
        )

    # Sectors may legitimately be empty: yfinance has none for bond funds, and a
    # failed fetch must not be papered over with invented weights.
    sectors = profile.get("sectors") or {}
    if sectors and not _sums_to_one(sectors):
        problems.append(f"sectors must sum to 1.0, got {sum(sectors.values()):.3f}")

    key = profile.get("benchmark_key")
    if key is not None and benchmarks.get_benchmark(key) is None:
        problems.append(f"unknown benchmark '{key}'")

    source = profile.get("source")
    if source not in _SOURCES:
        problems.append(f"source must be one of {_SOURCES}, got '{source}'")

    return problems


def build_from_benchmark(db, asset: dict, benchmark_key: str) -> dict:
    """Build a profile: regions from the benchmark, the rest from yfinance."""
    entry = benchmarks.get_benchmark(benchmark_key)
    if entry is None:
        raise ValueError(f"unknown benchmark '{benchmark_key}'")

    symbol = asset.get("ticker") or asset["symbol"]
    composition = market.get_fund_composition(db, symbol)

    return {
        "asset_id": asset["id"],
        "benchmark_key": benchmark_key,
        "source": "benchmark",
        # yfinance's split when it answered, the benchmark's own otherwise.
        "asset_class": composition.get("asset_class") or dict(entry["asset_class"]),
        "regions": dict(entry["regions"]),
        "sectors": composition.get("sectors") or {},
        "currency_hedged": False,
        "hedge_currency": None,
        "as_of": entry.get("as_of") or date.today().isoformat(),
        "notes": None,
    }


def is_stale(profile: dict, today: date, max_age_days: int = STALE_AFTER_DAYS) -> bool:
    """True when a profile's data is older than *max_age_days*, or undated."""
    raw = profile.get("as_of")
    if not raw:
        return True
    try:
        as_of = datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return True
    return (today - as_of).days > max_age_days
```

- [ ] **Step 4: Run the tests**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_fund_profiles_service.py -q`
Expected: PASS (17 tests)

- [ ] **Step 5: Commit**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/services/fund_profiles.py tests/unit/test_fund_profiles_service.py
git add portf_manager/services/fund_profiles.py tests/unit/test_fund_profiles_service.py
git commit -m "feat(exposure): fund profile parse, validate and benchmark build"
```

---

### Task 5: Exposure service

**Files:**
- Create: `portf_manager/services/exposure.py`
- Test: `tests/unit/test_exposure.py`

**Interfaces:**
- Consumes: `fund_profiles.parse_profile/is_stale/FUND_ASSET_TYPES`, `benchmarks.REGIONS`, `compute_positions`, `portfolio_advisor._resolve_sector_country`
- Produces: `compute_exposure(db, portfolio_id: Optional[int] = None, fx: Optional[Callable[[str], float]] = None, today: Optional[date] = None) -> dict` with keys:
  `total_value_eur`, `by_asset_type`, `by_asset_class`, `by_currency`, `by_currency_exposure`, `by_sector`, `by_country`, `by_region`, `by_region_equity`, `concentration_hhi`, `largest_position_pct`, `largest_position_symbol`, `largest_position_name`, `coverage`, `funds`.
  All `by_*` maps are percentages rounded to 1 decimal, descending. `coverage` is `{"classified_pct", "sector_classified_pct", "unprofiled": [{asset_id, symbol, name, value_eur}], "stale_profiles": [{asset_id, symbol, name, as_of}]}`. `funds` is the per-fund list Task 6 consumes: `{asset_id, symbol, name, portfolio_name, value_eur, asset_type, benchmark_key, regions, sectors, asset_class}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_exposure.py`:

```python
"""Exposure rollups with funds looked through."""

from datetime import date
from unittest.mock import MagicMock

from portf_manager.services import exposure


def _db(assets, prices, profiles=None, txns=None):
    """A db whose holdings are one buy per asset, at quantity 1."""
    profiles = profiles or {}
    db = MagicMock()
    db.get_all_transactions.return_value = txns if txns is not None else [
        {
            "id": i,
            "asset_id": a["id"],
            "portfolio_id": 1,
            "transaction_type": "buy",
            "quantity": 1.0,
            "price": prices[a["id"]],
            "total_amount": prices[a["id"]],
            "fees": 0.0,
            "currency": a.get("currency", "EUR"),
            "transaction_date": "2026-01-01",
        }
        for i, a in enumerate(assets, start=1)
    ]
    by_id = {a["id"]: a for a in assets}
    db.get_asset.side_effect = lambda aid: by_id.get(aid)
    db.get_latest_price.side_effect = lambda aid: {"price": prices[aid]}
    db.get_fund_profile.side_effect = lambda aid: profiles.get(aid)
    db.get_all_portfolios.return_value = [{"id": 1, "name": "Example Broker"}]
    return db


def _profile_row(asset_id, regions, sectors=None, asset_class=None, as_of="2026-09-01",
                 hedged=0, hedge_currency=None, benchmark_key="msci_world"):
    import json

    return {
        "asset_id": asset_id,
        "benchmark_key": benchmark_key,
        "source": "benchmark",
        "asset_class": json.dumps(asset_class or {"equity": 1.0}),
        "regions": json.dumps(regions),
        "sectors": json.dumps(sectors or {}),
        "currency_hedged": hedged,
        "hedge_currency": hedge_currency,
        "as_of": as_of,
    }


FUND = {"id": 1, "symbol": "IE0000000001", "name": "Example World Index Fund",
        "asset_type": "etf", "currency": "EUR", "ticker": "EXMPL.DE"}
STOCK = {"id": 2, "symbol": "EXCO", "name": "Example Corp", "asset_type": "stock",
         "currency": "EUR", "ticker": "EXCO.AS"}
COIN = {"id": 3, "symbol": "BTC", "name": "Bitcoin", "asset_type": "crypto",
        "currency": "EUR", "ticker": None}


class TestRegions:
    def test_splits_a_fund_across_its_regions(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db(
            [FUND],
            {1: 100.0},
            {1: _profile_row(1, {"north_america": 0.75, "japan": 0.25})},
        )
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_region"]["north_america"] == 75.0
        assert out["by_region"]["japan"] == 25.0

    def test_direct_stock_maps_country_to_region(self, monkeypatch):
        monkeypatch.setattr(
            exposure,
            "_resolve_sector_country",
            lambda db, a: ("Technology", "Netherlands"),
        )
        db = _db([STOCK], {2: 100.0})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_region"]["europe_ex_uk"] == 100.0
        assert out["by_country"]["Netherlands"] == 100.0

    def test_crypto_is_excluded_from_regions_and_sectors(self, monkeypatch):
        monkeypatch.setattr(
            exposure,
            "_resolve_sector_country",
            lambda db, a: ("Cryptocurrency", "Global"),
        )
        db = _db([COIN], {3: 100.0})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_asset_class"]["crypto"] == 100.0
        assert out["by_region"] == {}
        assert out["by_sector"] == {}


class TestCoverage:
    def test_an_unprofiled_fund_is_named_not_just_unknown(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db([FUND], {1: 100.0})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["coverage"]["classified_pct"] == 0.0
        assert out["coverage"]["unprofiled"][0]["symbol"] == "IE0000000001"
        assert out["coverage"]["unprofiled"][0]["value_eur"] == 100.0

    def test_classified_pct_mixes_profiled_and_direct(self, monkeypatch):
        monkeypatch.setattr(
            exposure,
            "_resolve_sector_country",
            lambda db, a: ("Technology", "Netherlands"),
        )
        db = _db(
            [FUND, STOCK],
            {1: 100.0, 2: 100.0},
            {1: _profile_row(1, {"north_america": 1.0})},
        )
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["coverage"]["classified_pct"] == 100.0

    def test_stale_profile_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db(
            [FUND],
            {1: 100.0},
            {1: _profile_row(1, {"north_america": 1.0}, as_of="2024-01-01")},
        )
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["coverage"]["stale_profiles"][0]["symbol"] == "IE0000000001"


class TestCurrencyExposure:
    def test_unhedged_fund_reports_underlying_currencies(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db(
            [FUND],
            {1: 100.0},
            {1: _profile_row(1, {"north_america": 0.7, "japan": 0.3})},
        )
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_currency_exposure"]["USD"] == 70.0
        assert out["by_currency_exposure"]["JPY"] == 30.0
        # Quote currency is unchanged and still EUR.
        assert out["by_currency"]["EUR"] == 100.0

    def test_hedged_fund_reports_its_hedge_currency(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db(
            [FUND],
            {1: 100.0},
            {
                1: _profile_row(
                    1,
                    {"north_america": 1.0},
                    asset_class={"bond": 1.0},
                    hedged=1,
                    hedge_currency="EUR",
                    benchmark_key="global_agg_corp",
                )
            },
        )
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_currency_exposure"]["EUR"] == 100.0


class TestSectors:
    def test_fund_sectors_are_weighted_by_its_equity_value(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db(
            [FUND],
            {1: 100.0},
            {
                1: _profile_row(
                    1,
                    {"north_america": 1.0},
                    sectors={"Technology": 0.4, "Healthcare": 0.6},
                )
            },
        )
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_sector"]["Healthcare"] == 60.0
        assert out["by_sector"]["Technology"] == 40.0
        assert out["coverage"]["sector_classified_pct"] == 100.0

    def test_missing_fund_sectors_lower_sector_coverage_only(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db([FUND], {1: 100.0}, {1: _profile_row(1, {"north_america": 1.0})})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["coverage"]["classified_pct"] == 100.0
        assert out["coverage"]["sector_classified_pct"] == 0.0


class TestBackwardCompatibility:
    def test_keeps_the_keys_finance_review_reads(self, monkeypatch):
        monkeypatch.setattr(
            exposure,
            "_resolve_sector_country",
            lambda db, a: ("Technology", "Netherlands"),
        )
        db = _db([STOCK], {2: 100.0})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        for key in (
            "by_asset_type",
            "by_currency",
            "by_sector",
            "by_country",
            "concentration_hhi",
            "largest_position_pct",
            "largest_position_symbol",
            "largest_position_name",
            "total_value_eur",
        ):
            assert key in out

    def test_country_puts_fund_value_in_one_named_bucket(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db([FUND], {1: 100.0}, {1: _profile_row(1, {"north_america": 1.0})})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_country"][exposure.VIA_FUNDS_LABEL] == 100.0

    def test_empty_portfolio_returns_zeros(self):
        db = _db([], {})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["total_value_eur"] == 0.0
        assert out["coverage"]["classified_pct"] == 0.0
        assert out["funds"] == []


class TestFundsList:
    def test_exposes_held_funds_for_overlap_detection(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db([FUND], {1: 100.0}, {1: _profile_row(1, {"north_america": 1.0})})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        fund = out["funds"][0]
        assert fund["benchmark_key"] == "msci_world"
        assert fund["portfolio_name"] == "Example Broker"
        assert fund["value_eur"] == 100.0
```

- [ ] **Step 2: Run to verify failure**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_exposure.py -q`
Expected: FAIL — `ModuleNotFoundError: portf_manager.services.exposure`

- [ ] **Step 3: Implement**

Create `portf_manager/services/exposure.py`:

```python
"""Portfolio exposure with funds looked through.

The single source for every breakdown the diversification view shows. A fund's
value is split across asset class, region and sector using its stored profile;
a direct holding keeps the per-symbol yfinance lookup; a fund with no profile is
reported by name rather than folded anonymously into "Unknown".

Both /analytics/diversification and portfolio_advisor.gather_diversification
call this, so the two can no longer disagree.
"""

import logging
from datetime import date
from typing import Callable, Optional

from portf_manager.positions import compute_positions
from portf_manager.services.fund_profiles import (
    FUND_ASSET_TYPES,
    is_stale,
    parse_profile,
)
from portf_manager.services.portfolio_advisor import _resolve_sector_country

logger = logging.getLogger(__name__)

# Country names as yfinance reports them, mapped to the fixed region taxonomy.
# Anything unlisted falls through to "unknown" and shows up in coverage.
_REGION_BY_COUNTRY: dict[str, str] = {
    "United States": "north_america",
    "Canada": "north_america",
    "Mexico": "north_america",
    "United Kingdom": "uk",
    "Japan": "japan",
    "Australia": "pacific_ex_japan",
    "New Zealand": "pacific_ex_japan",
    "Singapore": "pacific_ex_japan",
    "Hong Kong": "pacific_ex_japan",
    "China": "emerging",
    "Taiwan": "emerging",
    "South Korea": "emerging",
    "India": "emerging",
    "Brazil": "emerging",
    "South Africa": "emerging",
    "Netherlands": "europe_ex_uk",
    "France": "europe_ex_uk",
    "Germany": "europe_ex_uk",
    "Spain": "europe_ex_uk",
    "Italy": "europe_ex_uk",
    "Switzerland": "europe_ex_uk",
    "Sweden": "europe_ex_uk",
    "Denmark": "europe_ex_uk",
    "Norway": "europe_ex_uk",
    "Finland": "europe_ex_uk",
    "Belgium": "europe_ex_uk",
    "Ireland": "europe_ex_uk",
    "Austria": "europe_ex_uk",
    "Portugal": "europe_ex_uk",
    "Luxembourg": "europe_ex_uk",
}

# Approximate currency of each region's underlying assets. europe_ex_uk is
# mostly but not only EUR, and emerging is a basket — this is labelled as an
# approximation everywhere it is shown.
_CURRENCY_BY_REGION: dict[str, str] = {
    "north_america": "USD",
    "europe_ex_uk": "EUR",
    "uk": "GBP",
    "japan": "JPY",
    "pacific_ex_japan": "AUD",
    "emerging": "EM basket",
    "unknown": "Unknown",
}

# Where a fund's value lands in the country breakdown, which stays a
# direct-holdings view. Naming it beats leaving the value in "Unknown".
VIA_FUNDS_LABEL = "Via funds (see regions)"

UNKNOWN = "unknown"


def _default_fx(currency: str) -> float:
    """EUR conversion rate. Lazy import, same shim the other services use."""
    from portf_server.routers.portfolios import _get_fx_rate

    return _get_fx_rate(currency)


def _add(target: dict, key: str, value: float) -> None:
    target[key] = target.get(key, 0.0) + value


def compute_exposure(
    db,
    portfolio_id: Optional[int] = None,
    fx: Optional[Callable[[str], float]] = None,
    today: Optional[date] = None,
) -> dict:
    """Every exposure breakdown for the held portfolio, funds looked through."""
    fx = fx or _default_fx
    today = today or date.today()

    txns = db.get_all_transactions(portfolio_id=portfolio_id)
    positions, _ = compute_positions(txns)
    portfolio_names = {p["id"]: p["name"] for p in db.get_all_portfolios()}
    # A position's portfolio is whichever one its transactions belong to.
    portfolio_by_asset: dict[int, str] = {}
    for tx in txns:
        name = portfolio_names.get(tx.get("portfolio_id"), "Unassigned")
        portfolio_by_asset.setdefault(tx["asset_id"], name)

    by_type: dict[str, float] = {}
    by_class: dict[str, float] = {}
    by_currency: dict[str, float] = {}
    by_currency_exposure: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    by_country: dict[str, float] = {}
    by_region: dict[str, float] = {}
    by_region_equity: dict[str, float] = {}
    by_position: dict[str, float] = {}
    position_names: dict[str, str] = {}

    total = 0.0
    region_known = 0.0
    sector_known = 0.0
    unprofiled: list[dict] = []
    stale_profiles: list[dict] = []
    funds: list[dict] = []

    for asset_id, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(asset_id)
        if not asset:
            continue
        price_row = db.get_latest_price(asset_id)
        price = float(price_row["price"]) if price_row else 0.0
        currency = asset.get("currency", "EUR")
        value = pos["quantity"] * price * fx(currency)
        if value <= 0:
            continue

        symbol = asset["symbol"]
        atype = asset.get("asset_type", "other")
        total += value
        _add(by_position, symbol, value)
        position_names[symbol] = asset.get("name", symbol)
        _add(by_type, atype, value)
        _add(by_currency, currency, value)

        if atype == "crypto":
            _add(by_class, "crypto", value)
            _add(by_currency_exposure, "Crypto", value)
            _add(by_country, "Global", value)
            region_known += value
            sector_known += value
            continue

        if atype in FUND_ASSET_TYPES:
            profile = parse_profile(db.get_fund_profile(asset_id))
            if not profile or not profile.get("regions"):
                _add(by_class, UNKNOWN, value)
                _add(by_region, UNKNOWN, value)
                _add(by_country, VIA_FUNDS_LABEL, value)
                _add(by_currency_exposure, "Unknown", value)
                unprofiled.append(
                    {
                        "asset_id": asset_id,
                        "symbol": symbol,
                        "name": asset.get("name", symbol),
                        "value_eur": round(value, 2),
                    }
                )
                continue

            if is_stale(profile, today):
                stale_profiles.append(
                    {
                        "asset_id": asset_id,
                        "symbol": symbol,
                        "name": asset.get("name", symbol),
                        "as_of": profile.get("as_of"),
                    }
                )

            classes = profile["asset_class"] or {"equity": 1.0}
            for class_name, share in classes.items():
                _add(by_class, class_name, value * share)
            equity_value = value * float(classes.get("equity", 0.0))

            for region, share in profile["regions"].items():
                _add(by_region, region, value * share)
                if equity_value:
                    _add(by_region_equity, region, equity_value * share)
            region_known += value
            _add(by_country, VIA_FUNDS_LABEL, value)

            sectors = profile.get("sectors") or {}
            if sectors and equity_value:
                for sector, share in sectors.items():
                    _add(by_sector, sector, equity_value * share)
                sector_known += equity_value
            elif equity_value:
                _add(by_sector, "Unknown", equity_value)
            # A bond sleeve has no sector, so it counts as classified.
            sector_known += value - equity_value

            if profile.get("currency_hedged"):
                hedge = profile.get("hedge_currency") or currency
                _add(by_currency_exposure, hedge, value)
            else:
                for region, share in profile["regions"].items():
                    _add(
                        by_currency_exposure,
                        _CURRENCY_BY_REGION.get(region, "Unknown"),
                        value * share,
                    )

            funds.append(
                {
                    "asset_id": asset_id,
                    "symbol": symbol,
                    "name": asset.get("name", symbol),
                    "portfolio_name": portfolio_by_asset.get(asset_id, "Unassigned"),
                    "value_eur": round(value, 2),
                    "asset_type": atype,
                    "benchmark_key": profile.get("benchmark_key"),
                    "regions": profile["regions"],
                    "sectors": sectors,
                    "asset_class": classes,
                }
            )
            continue

        # Direct holding: the existing per-symbol resolution.
        sector, country = _resolve_sector_country(db, asset)
        _add(by_sector, sector, value)
        _add(by_country, country, value)
        if sector != "Unknown":
            sector_known += value
        region = _REGION_BY_COUNTRY.get(country, UNKNOWN)
        _add(by_region, region, value)
        if region != UNKNOWN:
            region_known += value
        _add(by_class, "bond" if atype == "bond" else "equity", value)
        _add(by_currency_exposure, currency, value)

    def pct_map(source: dict) -> dict:
        if not total:
            return {}
        return {
            key: round(value / total * 100, 1)
            for key, value in sorted(source.items(), key=lambda kv: -kv[1])
            if round(value / total * 100, 1) > 0
        }

    hhi = (
        round(sum((v / total) ** 2 for v in by_position.values()) * 10000, 0)
        if total
        else 0.0
    )
    largest_symbol = max(by_position, key=by_position.get) if by_position else None
    largest_pct = (
        round(by_position[largest_symbol] / total * 100, 1)
        if total and largest_symbol
        else 0
    )

    return {
        "total_value_eur": round(total, 2),
        "by_asset_type": pct_map(by_type),
        "by_asset_class": pct_map(by_class),
        "by_currency": pct_map(by_currency),
        "by_currency_exposure": pct_map(by_currency_exposure),
        "by_sector": pct_map(by_sector),
        "by_country": pct_map(by_country),
        "by_region": pct_map(by_region),
        "by_region_equity": pct_map(by_region_equity),
        "concentration_hhi": hhi,
        "largest_position_pct": largest_pct,
        "largest_position_symbol": largest_symbol,
        "largest_position_name": position_names.get(largest_symbol, largest_symbol),
        "coverage": {
            "classified_pct": round(region_known / total * 100, 1) if total else 0.0,
            "sector_classified_pct": (
                round(sector_known / total * 100, 1) if total else 0.0
            ),
            "unprofiled": sorted(
                unprofiled, key=lambda f: -f["value_eur"]
            ),
            "stale_profiles": stale_profiles,
        },
        "funds": funds,
    }
```

- [ ] **Step 4: Run the tests**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_exposure.py -q`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/services/exposure.py tests/unit/test_exposure.py
git add portf_manager/services/exposure.py tests/unit/test_exposure.py
git commit -m "feat(exposure): exposure service with fund look-through"
```

---

### Task 6: Fund overlap detection

**Files:**
- Modify: `portf_manager/services/exposure.py` (append)
- Test: `tests/unit/test_fund_overlap.py`

**Interfaces:**
- Consumes: `exposure.compute_exposure`'s `funds` list, `benchmarks.get_benchmark/ancestors`
- Produces: `find_fund_overlaps(funds: list[dict], total_value_eur: float) -> list[dict]`. Each group: `{"kind": "consolidation_candidate"|"informational"|"similar", "reason": str, "members": [...], "combined_value_eur": float, "combined_pct": float, "transferable": bool}`. Consolidation candidates first, each group at least two members, and no two groups with the same member set.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_fund_overlap.py`:

```python
"""Overlap between held funds: same index family, nesting, or similar weights."""

from portf_manager.services.exposure import find_fund_overlaps


def _fund(asset_id, name, benchmark_key=None, regions=None, sectors=None,
          value=1000.0, asset_type="etf"):
    return {
        "asset_id": asset_id,
        "symbol": f"IE000000000{asset_id}",
        "name": name,
        "portfolio_name": "Example Broker",
        "value_eur": value,
        "asset_type": asset_type,
        "benchmark_key": benchmark_key,
        "regions": regions or {"north_america": 1.0},
        "sectors": sectors or {},
        "asset_class": {"equity": 1.0},
    }


class TestSameFamily:
    def test_two_emerging_market_funds_are_a_consolidation_candidate(self):
        funds = [
            _fund(1, "Example EM Index Fund", "msci_em", {"emerging": 1.0}),
            _fund(2, "Example EM ETF", "msci_em", {"emerging": 1.0}),
        ]
        groups = find_fund_overlaps(funds, total_value_eur=4000.0)
        assert groups[0]["kind"] == "consolidation_candidate"
        assert len(groups[0]["members"]) == 2
        assert groups[0]["combined_value_eur"] == 2000.0
        assert groups[0]["combined_pct"] == 50.0

    def test_different_families_do_not_group(self):
        funds = [
            _fund(1, "Example EM Index Fund", "msci_em", {"emerging": 1.0}),
            _fund(2, "Example Japan ETF", "msci_japan", {"japan": 1.0}),
        ]
        assert find_fund_overlaps(funds, total_value_eur=2000.0) == []

    def test_transferable_only_when_every_member_is_a_mutual_fund(self):
        both_funds = [
            _fund(1, "Example EM Index Fund A", "msci_em", asset_type="mutual_fund"),
            _fund(2, "Example EM Index Fund B", "msci_em", asset_type="mutual_fund"),
        ]
        mixed = [
            _fund(1, "Example EM Index Fund", "msci_em", asset_type="mutual_fund"),
            _fund(2, "Example EM ETF", "msci_em", asset_type="etf"),
        ]
        assert find_fund_overlaps(both_funds, 4000.0)[0]["transferable"] is True
        assert find_fund_overlaps(mixed, 4000.0)[0]["transferable"] is False


class TestNesting:
    def test_an_sp500_fund_inside_a_world_fund_is_informational(self):
        funds = [
            _fund(1, "Example World Index Fund", "msci_world"),
            _fund(2, "Example S&P 500 ETF", "sp500"),
        ]
        groups = find_fund_overlaps(funds, total_value_eur=4000.0)
        assert [g["kind"] for g in groups] == ["informational"]
        assert "S&P 500" in groups[0]["reason"] or "MSCI World" in groups[0]["reason"]

    def test_consolidation_candidates_are_listed_before_informational(self):
        funds = [
            _fund(1, "Example World Index Fund", "msci_world"),
            _fund(2, "Example S&P 500 ETF", "sp500"),
            _fund(3, "Example EM Index Fund", "msci_em", {"emerging": 1.0}),
            _fund(4, "Example EM ETF", "msci_em", {"emerging": 1.0}),
        ]
        kinds = [g["kind"] for g in find_fund_overlaps(funds, 8000.0)]
        assert kinds[0] == "consolidation_candidate"
        assert "informational" in kinds


class TestSimilarity:
    def test_near_identical_weights_without_a_benchmark_are_similar(self):
        regions = {"north_america": 0.7, "europe_ex_uk": 0.3}
        funds = [
            _fund(1, "Example Global Fund A", None, regions,
                  {"Technology": 0.5, "Healthcare": 0.5}),
            _fund(2, "Example Global Fund B", None,
                  {"north_america": 0.71, "europe_ex_uk": 0.29},
                  {"Technology": 0.51, "Healthcare": 0.49}),
        ]
        groups = find_fund_overlaps(funds, total_value_eur=4000.0)
        assert groups[0]["kind"] == "similar"

    def test_different_weights_do_not_group(self):
        funds = [
            _fund(1, "Example US Fund", None, {"north_america": 1.0}),
            _fund(2, "Example Japan Fund", None, {"japan": 1.0}),
        ]
        assert find_fund_overlaps(funds, total_value_eur=2000.0) == []

    def test_a_fund_with_a_benchmark_is_not_matched_by_similarity(self):
        funds = [
            _fund(1, "Example World Index Fund", "msci_world"),
            _fund(2, "Example Global Fund", None, {"north_america": 1.0}),
        ]
        assert find_fund_overlaps(funds, total_value_eur=2000.0) == []


class TestEdges:
    def test_a_single_fund_produces_nothing(self):
        assert find_fund_overlaps([_fund(1, "Example World Index Fund",
                                         "msci_world")], 1000.0) == []

    def test_no_funds_produces_nothing(self):
        assert find_fund_overlaps([], 0.0) == []

    def test_members_carry_portfolio_and_value(self):
        funds = [
            _fund(1, "Example EM Index Fund", "msci_em"),
            _fund(2, "Example EM ETF", "msci_em"),
        ]
        member = find_fund_overlaps(funds, 4000.0)[0]["members"][0]
        assert member["portfolio_name"] == "Example Broker"
        assert member["value_eur"] == 1000.0
        assert "name" in member and "symbol" in member
```

- [ ] **Step 2: Run to verify failure**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_fund_overlap.py -q`
Expected: FAIL — `ImportError: cannot import name 'find_fund_overlaps'`

- [ ] **Step 3: Implement**

Append to `portf_manager/services/exposure.py`:

```python
# Two profile-less funds count as the same exposure above this cosine
# similarity of their region+sector weights.
SIMILARITY_THRESHOLD = 0.95


def _member(fund: dict) -> dict:
    return {
        "asset_id": fund["asset_id"],
        "symbol": fund["symbol"],
        "name": fund["name"],
        "portfolio_name": fund["portfolio_name"],
        "value_eur": fund["value_eur"],
    }


def _group(kind: str, reason: str, members: list[dict], total_value_eur: float) -> dict:
    combined = sum(f["value_eur"] for f in members)
    return {
        "kind": kind,
        "reason": reason,
        "members": [_member(f) for f in members],
        "combined_value_eur": round(combined, 2),
        "combined_pct": (
            round(combined / total_value_eur * 100, 1) if total_value_eur else 0.0
        ),
        # Consolidating mutual funds can go through a traspaso, which defers the
        # tax; selling an ETF realises the gain.
        "transferable": all(f["asset_type"] == "mutual_fund" for f in members),
    }


def _cosine(left: dict, right: dict) -> float:
    """Cosine similarity of two weight maps, 0.0 when either is empty."""
    keys = set(left) | set(right)
    dot = sum(left.get(k, 0.0) * right.get(k, 0.0) for k in keys)
    norm_l = sum(v * v for v in left.values()) ** 0.5
    norm_r = sum(v * v for v in right.values()) ** 0.5
    if not norm_l or not norm_r:
        return 0.0
    return dot / (norm_l * norm_r)


def find_fund_overlaps(funds: list[dict], total_value_eur: float) -> list[dict]:
    """Group held funds that hold the same thing.

    Three rules, in the order they are reported: funds tracking the same index
    family (worth consolidating), a narrower fund nested inside a broader one
    (informational — a deliberate tilt is legitimate), and, for funds with no
    benchmark, near-identical region and sector weights.
    """
    from portf_manager.services.benchmarks import ancestors, get_benchmark

    groups: list[dict] = []
    seen_member_sets: set[frozenset] = set()

    def _emit(kind: str, reason: str, members: list[dict]) -> None:
        key = frozenset(f["asset_id"] for f in members)
        if len(key) < 2 or key in seen_member_sets:
            return
        seen_member_sets.add(key)
        groups.append(_group(kind, reason, members, total_value_eur))

    # 1. Same index family.
    by_family: dict[str, list[dict]] = {}
    for fund in funds:
        entry = get_benchmark(fund.get("benchmark_key") or "")
        if entry and entry.get("family"):
            by_family.setdefault(entry["family"], []).append(fund)
    for family, members in by_family.items():
        if len(members) > 1:
            labels = {
                get_benchmark(f["benchmark_key"])["label"] for f in members
            }
            _emit(
                "consolidation_candidate",
                f"Both track the same exposure ({', '.join(sorted(labels))}).",
                members,
            )

    # 2. Nesting: one fund's index sits inside another's.
    for outer in funds:
        outer_key = outer.get("benchmark_key")
        if not outer_key:
            continue
        nested = [
            inner
            for inner in funds
            if inner is not outer
            and inner.get("benchmark_key")
            and outer_key in ancestors(inner["benchmark_key"])
        ]
        for inner in nested:
            outer_label = get_benchmark(outer_key)["label"]
            inner_label = get_benchmark(inner["benchmark_key"])["label"]
            _emit(
                "informational",
                f"{inner_label} is already part of {outer_label}.",
                [outer, inner],
            )

    # 3. Similar weights, for funds with no benchmark on either side.
    unbenchmarked = [f for f in funds if not f.get("benchmark_key")]
    for i, left in enumerate(unbenchmarked):
        for right in unbenchmarked[i + 1 :]:
            if set(left["asset_class"]) != set(right["asset_class"]):
                continue
            region_sim = _cosine(left["regions"], right["regions"])
            sector_sim = _cosine(left["sectors"], right["sectors"])
            combined = region_sim if not left["sectors"] or not right["sectors"] else (
                (region_sim + sector_sim) / 2
            )
            if combined >= SIMILARITY_THRESHOLD:
                _emit(
                    "similar",
                    f"Region and sector weights are {combined * 100:.0f}% alike.",
                    [left, right],
                )

    order = {"consolidation_candidate": 0, "similar": 1, "informational": 2}
    return sorted(
        groups, key=lambda g: (order[g["kind"]], -g["combined_value_eur"])
    )
```

- [ ] **Step 4: Run the tests**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_fund_overlap.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/services/exposure.py tests/unit/test_fund_overlap.py
git add portf_manager/services/exposure.py tests/unit/test_fund_overlap.py
git commit -m "feat(exposure): detect funds holding the same exposure"
```

---

### Task 7: Wire the analytics endpoints to the service

**Files:**
- Modify: `portf_server/routers/analytics.py` (replace the body of `get_diversification`, add `get_fund_overlap`)
- Modify: `portf_manager/services/portfolio_advisor.py` (`gather_diversification` delegates)
- Test: `tests/unit/test_watchlist_goals_risk.py` (extend the existing diversification test), `tests/unit/test_portfolio_analysis.py` (existing `TestGatherDiversification`)

**Interfaces:**
- Consumes: `exposure.compute_exposure`, `exposure.find_fund_overlaps`
- Produces: `GET /api/v1/analytics/diversification` (old keys plus `by_asset_class`, `by_region`, `by_region_equity`, `by_currency_exposure`, `coverage`) and `GET /api/v1/analytics/fund-overlap` → `{"groups": [...], "total_value_eur": float}`

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_watchlist_goals_risk.py`, replace `test_diversification` with:

```python
    @pytest.mark.asyncio
    async def test_diversification(self, async_test_client: AsyncClient, auth_headers):
        resp = await async_test_client.get(
            "/api/v1/analytics/diversification", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        d = resp.json()
        # The keys ~/mcp/scripts/finance_review.py reads must not move.
        for key in (
            "by_asset_type",
            "by_currency",
            "by_sector",
            "by_country",
            "concentration_hhi",
        ):
            assert key in d
        # New look-through fields.
        for key in (
            "by_asset_class",
            "by_region",
            "by_region_equity",
            "by_currency_exposure",
            "coverage",
        ):
            assert key in d
        assert "classified_pct" in d["coverage"]
        assert "unprofiled" in d["coverage"]

    @pytest.mark.asyncio
    async def test_fund_overlap_empty_portfolio(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/fund-overlap", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["groups"] == []
```

In `tests/unit/test_portfolio_analysis.py`, add to `TestGatherDiversification`:

```python
    def test_includes_coverage_for_the_health_prompt(self):
        from portf_manager.services.portfolio_advisor import gather_diversification

        result = gather_diversification(_mock_db(), portfolio_id=None)
        assert "coverage" in result
        assert "by_region_equity" in result
```

- [ ] **Step 2: Run to verify failure**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_watchlist_goals_risk.py -q -k diversification tests/unit/test_portfolio_analysis.py -q -k Diversification`
Expected: FAIL — `KeyError`/assertion on `by_asset_class`, and 404 for `/fund-overlap`

- [ ] **Step 3: Replace the endpoint body**

In `portf_server/routers/analytics.py`, replace the whole body of `get_diversification` (its loop, `pct_map`, `herfindahl` and return) with a delegation, keeping the docstring's note about being sync:

```python
@router.get("/diversification")
def get_diversification(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Exposure breakdown with funds looked through, plus concentration.

    Defined as a sync handler (not async) so FastAPI runs it in a threadpool:
    the per-holding yfinance lookups for direct holdings are blocking and would
    freeze the event loop if awaited inline.

    The computation lives in ``portf_manager.services.exposure`` so this and
    Portfolio Health cannot drift apart.
    """
    from portf_manager.services.exposure import compute_exposure

    result = compute_exposure(db, fx=_fx)
    # The per-fund list is an input to fund-overlap, not part of this view.
    result.pop("funds", None)
    return result


@router.get("/fund-overlap")
def get_fund_overlap(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Held funds that hold the same thing — same index family, or nested."""
    from portf_manager.services.exposure import compute_exposure, find_fund_overlaps

    result = compute_exposure(db, fx=_fx)
    total = result["total_value_eur"]
    return {
        "groups": find_fund_overlaps(result["funds"], total),
        "total_value_eur": total,
    }
```

Both routes are multi-segment or literal single-segment siblings under `/analytics`, and `analytics.py` has no `/{param}` route at that level, so no ordering hazard applies here.

- [ ] **Step 4: Delegate `gather_diversification`**

In `portf_manager/services/portfolio_advisor.py`, replace the body of `gather_diversification` (keep the function name and signature — `research.py` imports it by name):

```python
def gather_diversification(db, portfolio_id: Optional[int] = None) -> dict[str, Any]:
    """Exposure breakdown for the health bundle, funds looked through.

    Delegates to the exposure service: this and /analytics/diversification are
    the same question and used to be two implementations of it.
    """
    from portf_manager.services.exposure import compute_exposure

    result = compute_exposure(db, portfolio_id=portfolio_id, fx=_fx)
    result.pop("funds", None)
    return result
```

Leave `_resolve_sector_country` where it is — the exposure service imports it from here.

- [ ] **Step 5: Run the tests**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit -q`
Expected: PASS

- [ ] **Step 6: Restart the backend and check the live response**

```bash
docker exec portf_backend_dev kill -HUP 1
sleep 3
KEY=$(grep -E '^(SERVER_API_KEY|PORTF_API_KEY)=' .env.local | head -1 | cut -d= -f2-)
curl -s -m 180 -H "X-API-Key: $KEY" http://localhost:8000/api/v1/analytics/diversification \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['coverage']['classified_pct'], len(d['coverage']['unprofiled']))"
```
Expected: a low `classified_pct` and a list of unprofiled funds — correct at this stage, since no profiles exist yet.

- [ ] **Step 7: Commit**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/analytics.py portf_manager/services/portfolio_advisor.py
git add portf_server/routers/analytics.py portf_manager/services/portfolio_advisor.py tests/unit/test_watchlist_goals_risk.py tests/unit/test_portfolio_analysis.py
git commit -m "feat(analytics): serve look-through exposure and fund overlap"
```

---

### Task 8: Fund profiles router

**Files:**
- Create: `portf_server/routers/fund_profiles.py`
- Modify: `portf_server/app.py` (import + `include_router`)
- Test: `tests/unit/test_fund_profiles_api.py`

**Interfaces:**
- Consumes: `fund_profiles` service, `benchmarks.benchmark_choices`, `get_llm_client`
- Produces: `GET /api/v1/fund-profiles/`, `GET /api/v1/fund-profiles/benchmarks`, `GET|PUT /api/v1/fund-profiles/{asset_id}`, `POST /api/v1/fund-profiles/{asset_id}/refresh`, `POST /api/v1/fund-profiles/{asset_id}/suggest`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_fund_profiles_api.py`:

```python
"""Fund profile CRUD, refresh and AI suggestion."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from httpx import AsyncClient


async def _make_fund(client, auth_headers, symbol="IE0000000001"):
    resp = await client.post(
        "/api/v1/assets/",
        headers=auth_headers,
        json={
            "symbol": symbol,
            "name": "Example World Index Fund",
            "asset_type": "etf",
            "currency": "EUR",
            "ticker": "EXMPL.DE",
        },
    )
    assert resp.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED)
    return resp.json()["id"]


class TestRouteOrder:
    @pytest.mark.asyncio
    async def test_benchmarks_is_not_matched_as_an_asset_id(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/fund-profiles/benchmarks", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        keys = {c["key"] for c in resp.json()["benchmarks"]}
        assert "msci_world" in keys


class TestList:
    @pytest.mark.asyncio
    async def test_lists_held_funds_with_profile_status(
        self, async_test_client: AsyncClient, auth_headers
    ):
        await _make_fund(async_test_client, auth_headers)
        resp = await async_test_client.get(
            "/api/v1/fund-profiles/", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        funds = resp.json()["funds"]
        assert funds[0]["symbol"] == "IE0000000001"
        assert funds[0]["has_profile"] is False


class TestPut:
    @pytest.mark.asyncio
    async def test_saves_a_manual_profile(
        self, async_test_client: AsyncClient, auth_headers
    ):
        asset_id = await _make_fund(async_test_client, auth_headers)
        resp = await async_test_client.put(
            f"/api/v1/fund-profiles/{asset_id}",
            headers=auth_headers,
            json={
                "benchmark_key": "msci_world",
                "asset_class": {"equity": 1.0},
                "regions": {"north_america": 0.7, "japan": 0.3},
                "sectors": {"Technology": 1.0},
                "currency_hedged": False,
                "as_of": "2026-09-01",
            },
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["source"] == "manual"

    @pytest.mark.asyncio
    async def test_rejects_weights_that_do_not_sum_to_one(
        self, async_test_client: AsyncClient, auth_headers
    ):
        asset_id = await _make_fund(async_test_client, auth_headers)
        resp = await async_test_client.put(
            f"/api/v1/fund-profiles/{asset_id}",
            headers=auth_headers,
            json={
                "benchmark_key": None,
                "asset_class": {"equity": 1.0},
                "regions": {"north_america": 0.5},
                "sectors": {},
                "currency_hedged": False,
                "as_of": "2026-09-01",
            },
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "regions" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_unknown_asset_is_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.put(
            "/api/v1/fund-profiles/99999",
            headers=auth_headers,
            json={
                "benchmark_key": "msci_world",
                "asset_class": {"equity": 1.0},
                "regions": {"north_america": 1.0},
                "sectors": {},
                "currency_hedged": False,
                "as_of": "2026-09-01",
            },
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


class TestRefresh:
    @pytest.mark.asyncio
    async def test_builds_a_profile_from_the_benchmark(
        self, async_test_client: AsyncClient, auth_headers
    ):
        asset_id = await _make_fund(async_test_client, auth_headers)
        composition = {"sectors": {"Technology": 1.0}, "asset_class": {"equity": 1.0}}
        with patch(
            "portf_manager.services.fund_profiles.market.get_fund_composition",
            return_value=composition,
        ):
            resp = await async_test_client.post(
                f"/api/v1/fund-profiles/{asset_id}/refresh",
                headers=auth_headers,
                json={"benchmark_key": "msci_world"},
            )
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["source"] == "benchmark"
        assert body["regions"]["north_america"] == 0.72

    @pytest.mark.asyncio
    async def test_refuses_to_overwrite_a_manual_profile(
        self, async_test_client: AsyncClient, auth_headers
    ):
        asset_id = await _make_fund(async_test_client, auth_headers)
        await async_test_client.put(
            f"/api/v1/fund-profiles/{asset_id}",
            headers=auth_headers,
            json={
                "benchmark_key": "msci_world",
                "asset_class": {"equity": 1.0},
                "regions": {"north_america": 1.0},
                "sectors": {},
                "currency_hedged": False,
                "as_of": "2026-09-01",
            },
        )
        resp = await async_test_client.post(
            f"/api/v1/fund-profiles/{asset_id}/refresh",
            headers=auth_headers,
            json={"benchmark_key": "msci_world"},
        )
        assert resp.status_code == status.HTTP_409_CONFLICT

    @pytest.mark.asyncio
    async def test_force_overwrites_a_manual_profile(
        self, async_test_client: AsyncClient, auth_headers
    ):
        asset_id = await _make_fund(async_test_client, auth_headers)
        await async_test_client.put(
            f"/api/v1/fund-profiles/{asset_id}",
            headers=auth_headers,
            json={
                "benchmark_key": "msci_world",
                "asset_class": {"equity": 1.0},
                "regions": {"north_america": 1.0},
                "sectors": {},
                "currency_hedged": False,
                "as_of": "2026-09-01",
            },
        )
        with patch(
            "portf_manager.services.fund_profiles.market.get_fund_composition",
            return_value={"sectors": {}, "asset_class": {}},
        ):
            resp = await async_test_client.post(
                f"/api/v1/fund-profiles/{asset_id}/refresh",
                headers=auth_headers,
                json={"benchmark_key": "msci_world", "force": True},
            )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["source"] == "benchmark"


class TestSuggest:
    @pytest.mark.asyncio
    async def test_returns_a_draft_without_saving(
        self, async_test_client: AsyncClient, auth_headers
    ):
        asset_id = await _make_fund(async_test_client, auth_headers)
        llm = MagicMock()
        llm.generate.return_value = (
            '{"regions": {"north_america": 0.6, "emerging": 0.4},'
            ' "asset_class": {"equity": 1.0},'
            ' "sectors": {"Technology": 1.0},'
            ' "benchmark_key": null, "notes": "drafted"}'
        )
        with patch(
            "portf_server.routers.fund_profiles.get_llm_client", return_value=llm
        ):
            resp = await async_test_client.post(
                f"/api/v1/fund-profiles/{asset_id}/suggest", headers=auth_headers
            )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["suggestion"]["regions"]["emerging"] == 0.4
        # Nothing was written.
        get = await async_test_client.get(
            f"/api/v1/fund-profiles/{asset_id}", headers=auth_headers
        )
        assert get.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_an_llm_failure_is_502(
        self, async_test_client: AsyncClient, auth_headers
    ):
        asset_id = await _make_fund(async_test_client, auth_headers)
        with patch(
            "portf_server.routers.fund_profiles.get_llm_client",
            side_effect=RuntimeError("no key"),
        ):
            resp = await async_test_client.post(
                f"/api/v1/fund-profiles/{asset_id}/suggest", headers=auth_headers
            )
        assert resp.status_code == status.HTTP_502_BAD_GATEWAY
```

- [ ] **Step 2: Run to verify failure**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_fund_profiles_api.py -q`
Expected: FAIL — 404 on every route

- [ ] **Step 3: Implement the router**

Create `portf_server/routers/fund_profiles.py`:

```python
"""Fund profile router — the weight maps that make a fund see-through.

Every handler is a plain ``def``: they make blocking yfinance and LLM calls, so
FastAPI runs them in a threadpool rather than on the event loop.
"""

import json
import logging
from datetime import date
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from portf_manager.llm_client import get_llm_client
from portf_manager.services import benchmarks as benchmarks_service
from portf_manager.services import fund_profiles as fp_service

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


class ProfileBody(BaseModel):
    benchmark_key: Optional[str] = None
    asset_class: Dict[str, float]
    regions: Dict[str, float]
    sectors: Dict[str, float] = {}
    currency_hedged: bool = False
    hedge_currency: Optional[str] = None
    as_of: str
    notes: Optional[str] = None


class RefreshBody(BaseModel):
    benchmark_key: str
    force: bool = False


def _asset_or_404(db, asset_id: int) -> dict:
    asset = db.get_asset(asset_id)
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Asset {asset_id} not found"
        )
    return asset


def _save(db, profile: dict) -> dict:
    problems = fp_service.validate_profile(profile)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="; ".join(problems)
        )
    row = db.upsert_fund_profile(**fp_service.serialize_profile(profile))
    return fp_service.parse_profile(row)


# Registered before /{asset_id}: FastAPI matches in declaration order, so a
# literal route declared after a path parameter is swallowed by it.
@router.get("/benchmarks")
def list_benchmarks(api_key_info: dict = Depends(_auth)):
    """The benchmark index table, as dropdown options."""
    return {"benchmarks": benchmarks_service.benchmark_choices()}


@router.get("/")
def list_fund_assets(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Every fund-like asset, with whether it has a profile and if it is stale."""
    today = date.today()
    funds = []
    for asset in db.get_all_assets():
        if asset.get("asset_type") not in fp_service.FUND_ASSET_TYPES:
            continue
        profile = fp_service.parse_profile(db.get_fund_profile(asset["id"]))
        funds.append(
            {
                "asset_id": asset["id"],
                "symbol": asset["symbol"],
                "name": asset.get("name"),
                "asset_type": asset.get("asset_type"),
                "ticker": asset.get("ticker"),
                "has_profile": profile is not None,
                "source": profile.get("source") if profile else None,
                "benchmark_key": profile.get("benchmark_key") if profile else None,
                "as_of": profile.get("as_of") if profile else None,
                "is_stale": fp_service.is_stale(profile, today) if profile else None,
            }
        )
    return {"funds": funds}


@router.get("/{asset_id}")
def get_profile(
    asset_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """One fund profile."""
    _asset_or_404(db, asset_id)
    profile = fp_service.parse_profile(db.get_fund_profile(asset_id))
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No profile for asset {asset_id}",
        )
    return profile


@router.put("/{asset_id}")
def put_profile(
    asset_id: int,
    body: ProfileBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Save a hand-edited profile. Editing always marks it manual."""
    _asset_or_404(db, asset_id)
    profile = {**body.model_dump(), "asset_id": asset_id, "source": "manual"}
    return _save(db, profile)


@router.post("/{asset_id}/refresh")
def refresh_profile(
    asset_id: int,
    body: RefreshBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Rebuild a profile from its benchmark plus yfinance composition."""
    asset = _asset_or_404(db, asset_id)
    existing = fp_service.parse_profile(db.get_fund_profile(asset_id))
    if existing and existing.get("source") == "manual" and not body.force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This profile was edited by hand. Pass force=true to replace it "
                "with benchmark data."
            ),
        )
    try:
        profile = fp_service.build_from_benchmark(db, asset, body.benchmark_key)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _save(db, profile)


def _build_suggest_prompt(asset: dict) -> str:
    """Prompt for a fund whose index we do not know."""
    return (
        "You are a fund analyst. For the fund below, give its approximate "
        "geographic and sector breakdown.\n\n"
        f"Name: {asset.get('name')}\n"
        f"Identifier: {asset.get('symbol')}\n"
        f"Ticker: {asset.get('ticker') or 'unknown'}\n\n"
        "Return ONLY valid JSON, no markdown fences, in this shape:\n"
        '{"regions": {"north_america": 0.0, "europe_ex_uk": 0.0, "uk": 0.0, '
        '"japan": 0.0, "pacific_ex_japan": 0.0, "emerging": 0.0}, '
        '"asset_class": {"equity": 0.0, "bond": 0.0, "cash": 0.0}, '
        '"sectors": {"Technology": 0.0}, "benchmark_key": null, '
        '"notes": "one sentence on what this is based on"}\n\n'
        "Rules: regions must sum to 1.0 and use only those keys; asset_class "
        "must sum to 1.0; omit sectors entirely for a bond fund; if you are not "
        "reasonably confident, say so in notes rather than inventing precision."
    )


@router.post("/{asset_id}/suggest")
def suggest_profile(
    asset_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Ask the LLM to draft a profile. Writes nothing — the user applies it."""
    asset = _asset_or_404(db, asset_id)
    try:
        llm = get_llm_client()
        raw = llm.generate(_build_suggest_prompt(asset)).strip()
        if raw.startswith("```"):
            raw = "\n".join(
                line for line in raw.split("\n") if not line.strip().startswith("```")
            )
        suggestion = json.loads(raw)
    except Exception as e:
        logger.warning(f"Fund profile suggestion failed for {asset_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Profile suggestion failed: {e}",
        )

    # Report the problems rather than rejecting: the user reviews this in a form
    # and can correct a weight the model got wrong.
    candidate = {
        "asset_id": asset_id,
        "source": "llm",
        "benchmark_key": suggestion.get("benchmark_key"),
        "asset_class": suggestion.get("asset_class", {}),
        "regions": suggestion.get("regions", {}),
        "sectors": suggestion.get("sectors", {}),
        "currency_hedged": False,
        "hedge_currency": None,
        "as_of": date.today().isoformat(),
        "notes": suggestion.get("notes"),
    }
    return {
        "suggestion": candidate,
        "problems": fp_service.validate_profile(candidate),
    }
```

- [ ] **Step 4: Register the router**

In `portf_server/app.py`, add `fund_profiles` to the routers import list and register it next to the other analytics-adjacent routers:

```python
app.include_router(
    fund_profiles.router,
    prefix="/api/v1/fund-profiles",
    tags=["Fund Profiles"],
    dependencies=_PROTECTED,
)
```

- [ ] **Step 5: Run the tests**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_fund_profiles_api.py -q`
Expected: PASS (11 tests)

- [ ] **Step 6: Commit**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/fund_profiles.py portf_server/app.py tests/unit/test_fund_profiles_api.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501
git add portf_server/routers/fund_profiles.py portf_server/app.py tests/unit/test_fund_profiles_api.py
git commit -m "feat(api): fund profile CRUD, refresh and AI suggestion"
```

---

### Task 9: Action items and the Portfolio Health prompt

**Files:**
- Modify: `portf_manager/services/action_items.py` (new check + registration)
- Modify: `portf_manager/services/portfolio_advisor.py` (`build_analysis_prompt`)
- Test: `tests/unit/test_action_items.py` (add a class), `tests/unit/test_portfolio_analysis.py` (add a prompt test)

**Interfaces:**
- Consumes: `exposure.compute_exposure`, `exposure.find_fund_overlaps`
- Produces: `check_fund_exposure(db) -> list[dict]`, registered in the `checks` list

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_action_items.py`:

```python
class TestFundExposure:
    def _exposure(self, unprofiled=None, stale=None, funds=None):
        return {
            "total_value_eur": 10000.0,
            "coverage": {
                "classified_pct": 50.0,
                "sector_classified_pct": 50.0,
                "unprofiled": unprofiled or [],
                "stale_profiles": stale or [],
            },
            "funds": funds or [],
        }

    def test_flags_a_fund_with_no_profile(self):
        from unittest.mock import MagicMock, patch

        from portf_manager.services import action_items

        exposure = self._exposure(
            unprofiled=[
                {
                    "asset_id": 4,
                    "symbol": "IE0000000001",
                    "name": "Example World Index Fund",
                    "value_eur": 5000.0,
                }
            ]
        )
        with patch(
            "portf_manager.services.exposure.compute_exposure", return_value=exposure
        ):
            items = action_items.check_fund_exposure(MagicMock())
        assert items[0]["id"] == "exposure:profile:4"
        assert items[0]["category"] == "exposure"
        assert items[0]["severity"] == "medium"
        assert "Example World Index Fund" in items[0]["title"]

    def test_flags_a_stale_profile_as_low(self):
        from unittest.mock import MagicMock, patch

        from portf_manager.services import action_items

        exposure = self._exposure(
            stale=[
                {
                    "asset_id": 7,
                    "symbol": "IE0000000002",
                    "name": "Example EM Index Fund",
                    "as_of": "2024-01-01",
                }
            ]
        )
        with patch(
            "portf_manager.services.exposure.compute_exposure", return_value=exposure
        ):
            items = action_items.check_fund_exposure(MagicMock())
        assert items[0]["id"] == "exposure:stale:7"
        assert items[0]["severity"] == "low"

    def test_flags_a_consolidation_candidate_only(self):
        from unittest.mock import MagicMock, patch

        from portf_manager.services import action_items

        groups = [
            {
                "kind": "consolidation_candidate",
                "reason": "Both track the same exposure (MSCI Emerging Markets).",
                "members": [
                    {"asset_id": 1, "symbol": "IE0000000001",
                     "name": "Example EM Index Fund", "portfolio_name": "A",
                     "value_eur": 1000.0},
                    {"asset_id": 2, "symbol": "IE0000000002",
                     "name": "Example EM ETF", "portfolio_name": "B",
                     "value_eur": 1000.0},
                ],
                "combined_value_eur": 2000.0,
                "combined_pct": 20.0,
                "transferable": False,
            },
            {
                "kind": "informational",
                "reason": "S&P 500 is already part of MSCI World.",
                "members": [
                    {"asset_id": 3, "symbol": "IE0000000003",
                     "name": "Example World Index Fund", "portfolio_name": "A",
                     "value_eur": 1000.0},
                    {"asset_id": 4, "symbol": "IE0000000004",
                     "name": "Example S&P 500 ETF", "portfolio_name": "A",
                     "value_eur": 1000.0},
                ],
                "combined_value_eur": 2000.0,
                "combined_pct": 20.0,
                "transferable": False,
            },
        ]
        with patch(
            "portf_manager.services.exposure.compute_exposure",
            return_value=self._exposure(),
        ), patch(
            "portf_manager.services.exposure.find_fund_overlaps", return_value=groups
        ):
            items = action_items.check_fund_exposure(MagicMock())
        ids = [i["id"] for i in items]
        assert ids == ["exposure:overlap:1,2"]

    def test_clean_portfolio_produces_nothing(self):
        from unittest.mock import MagicMock, patch

        from portf_manager.services import action_items

        with patch(
            "portf_manager.services.exposure.compute_exposure",
            return_value=self._exposure(),
        ), patch(
            "portf_manager.services.exposure.find_fund_overlaps", return_value=[]
        ):
            assert action_items.check_fund_exposure(MagicMock()) == []

    def test_is_registered(self):
        from portf_manager.services import action_items

        assert action_items.check_fund_exposure in action_items.checks_for_tests()
```

Add to `tests/unit/test_portfolio_analysis.py`:

```python
class TestAnalysisPromptCoverage:
    def test_prompt_states_how_much_is_classified(self):
        from portf_manager.services.portfolio_advisor import build_analysis_prompt

        bundle = {
            "diversification": {
                "by_asset_type": {"etf": 100.0},
                "by_sector": {},
                "by_country": {},
                "by_currency": {},
                "by_region_equity": {"north_america": 100.0},
                "coverage": {"classified_pct": 42.0, "sector_classified_pct": 10.0},
                "concentration_hhi": 1000,
            }
        }
        prompt = build_analysis_prompt(bundle)
        assert "42.0%" in prompt
        assert "classified" in prompt.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_action_items.py tests/unit/test_portfolio_analysis.py -q -k "FundExposure or PromptCoverage"`
Expected: FAIL — `check_fund_exposure` and `checks_for_tests` do not exist; the prompt has no coverage line

- [ ] **Step 3: Implement the check**

Add to `portf_manager/services/action_items.py`, before the aggregator:

```python
def check_fund_exposure(db) -> list[dict]:
    """Funds that cannot be seen through, and funds holding the same thing."""
    from portf_manager.services import exposure as exposure_service

    result = exposure_service.compute_exposure(db)
    coverage = result.get("coverage", {})
    items: list[dict] = []

    for fund in coverage.get("unprofiled", []):
        items.append(
            {
                "id": f"exposure:profile:{fund['asset_id']}",
                "category": "exposure",
                "severity": "medium",
                "title": (
                    f"{_name_code(fund.get('name'), fund['symbol'])} has no "
                    f"look-through profile"
                ),
                "detail": (
                    f"{fund['value_eur']:,.0f} EUR is unclassified, so sector, "
                    f"region and currency exposure all understate it. Set its "
                    f"benchmark on the Analytics page."
                ),
                "link_page": "analytics",
                "context": {"asset_id": fund["asset_id"], "symbol": fund["symbol"]},
            }
        )

    for fund in coverage.get("stale_profiles", []):
        items.append(
            {
                "id": f"exposure:stale:{fund['asset_id']}",
                "category": "exposure",
                "severity": "low",
                "title": (
                    f"{_name_code(fund.get('name'), fund['symbol'])} profile is "
                    f"over a year old"
                ),
                "detail": (
                    f"Its index weights are as of {fund.get('as_of')}. Index "
                    f"weights drift a few points a year — refresh when convenient."
                ),
                "link_page": "analytics",
                "context": {"asset_id": fund["asset_id"], "symbol": fund["symbol"]},
            }
        )

    groups = exposure_service.find_fund_overlaps(
        result.get("funds", []), result.get("total_value_eur", 0.0)
    )
    for group in groups:
        # Nesting is a legitimate tilt, so only same-exposure groups are nudges.
        if group["kind"] != "consolidation_candidate":
            continue
        ids = ",".join(str(m["asset_id"]) for m in group["members"])
        names = " + ".join(m["name"] for m in group["members"])
        transfer = (
            " They are both funds, so a traspaso can merge them without "
            "realising a gain."
            if group["transferable"]
            else ""
        )
        items.append(
            {
                "id": f"exposure:overlap:{ids}",
                "category": "exposure",
                "severity": "low",
                "title": f"{names} hold the same exposure",
                "detail": (
                    f"{group['reason']} Combined "
                    f"{group['combined_value_eur']:,.0f} EUR "
                    f"({group['combined_pct']}% of the portfolio).{transfer}"
                ),
                "link_page": "analytics",
                "context": {"asset_ids": [m["asset_id"] for m in group["members"]]},
            }
        )

    return items
```

Register it in the aggregator's `checks` list, after `check_price_alerts`, and expose the list for the registration test:

```python
        check_budget_overruns,
        check_fund_exposure,
    ]
```

```python
def checks_for_tests() -> list:
    """The registered checks, exposed so a test can assert one is wired in."""
    return list(_CHECKS)
```

If the `checks` list is built inline inside the aggregator, lift it to a module-level `_CHECKS` tuple first and have the aggregator iterate that, so both the aggregator and `checks_for_tests` read one definition.

- [ ] **Step 4: Add the coverage line to the health prompt**

In `portf_manager/services/portfolio_advisor.py`, inside `build_analysis_prompt`, extend the Diversification block:

```python
### Diversification
- Asset types: {json.dumps(div_data.get('by_asset_type', _empty))}
- Sectors: {json.dumps(div_data.get('by_sector', _empty))}
- Countries: {json.dumps(div_data.get('by_country', _empty))}
- Regions (equity, funds looked through): {json.dumps(div_data.get('by_region_equity', _empty))}
- Currencies: {json.dumps(div_data.get('by_currency', _empty))}
- Concentration HHI: {div_data.get('concentration_hhi', 'N/A')} / 10000 (>2500 = high)
- Data coverage: {div_data.get('coverage', _empty).get('classified_pct', 0)}% of value is classified by region, {div_data.get('coverage', _empty).get('sector_classified_pct', 0)}% by sector. Below 90%, say so in the diversification reason and do not score it confidently — unclassified value is unknown exposure, not absent exposure.
```

- [ ] **Step 5: Run the tests**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/services/action_items.py portf_manager/services/portfolio_advisor.py tests/unit/test_action_items.py tests/unit/test_portfolio_analysis.py
git add portf_manager/services/action_items.py portf_manager/services/portfolio_advisor.py tests/unit/test_action_items.py tests/unit/test_portfolio_analysis.py
git commit -m "feat(action-items): flag unprofiled funds, stale profiles and overlap"
```

---

### Task 10: MCP diversification output

**Files:**
- Modify: `mcp/server.py` (the `diversification` tool)
- Test: `~/mcp/pfm/test_server.py`

`mcp/server.py` in this repo is the same file as `~/mcp/pfm/server.py` (symlink), and it is re-exported by the internet-facing gateway. This change is output-only: no new tool, so no gateway/persona registration is needed.

**Interfaces:**
- Consumes: `/api/v1/analytics/diversification` and `/api/v1/analytics/fund-overlap`
- Produces: a longer `diversification()` string

- [ ] **Step 1: Write the failing test**

Add to `~/mcp/pfm/test_server.py`:

```python
def test_diversification_shows_regions_coverage_and_overlap():
    diversification_payload = {
        "total_value_eur": 10000.0,
        "by_asset_type": {"etf": 60.0, "stock": 40.0},
        "by_asset_class": {"equity": 85.0, "bond": 15.0},
        "by_currency": {"EUR": 80.0, "USD": 20.0},
        "by_currency_exposure": {"USD": 55.0, "EUR": 35.0, "JPY": 10.0},
        "by_sector": {"Technology": 30.0},
        "by_country": {"Via funds (see regions)": 60.0, "Netherlands": 40.0},
        "by_region": {"north_america": 55.0, "europe_ex_uk": 35.0},
        "by_region_equity": {"north_america": 60.0, "europe_ex_uk": 40.0},
        "concentration_hhi": 1400.0,
        "largest_position_pct": 30.0,
        "largest_position_symbol": "IE0000000001",
        "largest_position_name": "Example World Index Fund",
        "coverage": {
            "classified_pct": 84.0,
            "sector_classified_pct": 70.0,
            "unprofiled": [
                {"asset_id": 9, "symbol": "IE0000000009",
                 "name": "Example Unprofiled Fund", "value_eur": 1600.0}
            ],
            "stale_profiles": [],
        },
    }
    overlap_payload = {
        "groups": [
            {
                "kind": "consolidation_candidate",
                "reason": "Both track the same exposure (MSCI Emerging Markets).",
                "members": [
                    {"asset_id": 1, "symbol": "IE0000000001",
                     "name": "Example EM Index Fund", "portfolio_name": "Broker A",
                     "value_eur": 1000.0},
                    {"asset_id": 2, "symbol": "IE0000000002",
                     "name": "Example EM ETF", "portfolio_name": "Broker B",
                     "value_eur": 800.0},
                ],
                "combined_value_eur": 1800.0,
                "combined_pct": 18.0,
                "transferable": False,
            }
        ],
        "total_value_eur": 10000.0,
    }
    with patch(
        "server.urllib.request.urlopen",
        side_effect=[
            mock_json_response(diversification_payload),
            mock_json_response(overlap_payload),
        ],
    ):
        result = server.diversification()
    assert "By region (equity" in result
    assert "north_america" in result
    assert "84.0%" in result
    assert "Example Unprofiled Fund" in result
    assert "Example EM Index Fund" in result


def test_diversification_survives_a_failing_overlap_call():
    payload = {
        "total_value_eur": 100.0,
        "by_asset_type": {"stock": 100.0},
        "by_asset_class": {"equity": 100.0},
        "by_currency": {"EUR": 100.0},
        "by_currency_exposure": {"EUR": 100.0},
        "by_sector": {"Technology": 100.0},
        "by_country": {"Netherlands": 100.0},
        "by_region": {"europe_ex_uk": 100.0},
        "by_region_equity": {"europe_ex_uk": 100.0},
        "concentration_hhi": 10000.0,
        "coverage": {"classified_pct": 100.0, "sector_classified_pct": 100.0,
                     "unprofiled": [], "stale_profiles": []},
    }
    with patch(
        "server.urllib.request.urlopen",
        side_effect=[mock_json_response(payload), urllib.error.URLError("boom")],
    ):
        result = server.diversification()
    assert "DIVERSIFICATION" in result
    assert "europe_ex_uk" in result
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/mcp/pfm && python3 -m pytest -q .`
Expected: FAIL — the output has no region, coverage or overlap sections

- [ ] **Step 3: Extend the tool**

In `mcp/server.py`, replace the `diversification` tool's body:

```python
@mcp.tool()
def diversification() -> str:
    """
    Portfolio exposure with funds looked through: asset class, region, sector,
    country and currency, plus concentration, how much of the book is actually
    classified, and any funds holding the same exposure.
    """
    try:
        data = _get("/api/v1/analytics/diversification")
    except Exception as e:
        return f"Error fetching diversification: {e}"

    def fmt_map(label: str, d: dict) -> list[str]:
        out = [f"\n{label}:"]
        for k, pct in d.items():
            out.append(f"  {k:25s}  {pct:.1f}%")
        return out

    lines = [
        f"DIVERSIFICATION  (total {_fmt_currency(data.get('total_value_eur', 0))})"
    ]
    largest = data.get("largest_position_symbol")
    if largest:
        lines.append(
            f"Largest position: {largest} ({data.get('largest_position_name', largest)})  "
            f"{data.get('largest_position_pct', 0):.1f}%"
        )
    hhi = data.get("concentration_hhi")
    if hhi is not None:
        lines.append(f"Concentration HHI: {hhi:.0f}  (10 000 = single asset)")

    coverage = data.get("coverage") or {}
    classified = coverage.get("classified_pct")
    if classified is not None:
        lines.append(
            f"Coverage: {classified:.1f}% of value classified by region, "
            f"{coverage.get('sector_classified_pct', 0):.1f}% by sector"
        )
    for fund in coverage.get("unprofiled", []):
        lines.append(
            f"  ! No look-through profile: {fund['name']} "
            f"({_fmt_currency(fund['value_eur'])})"
        )

    lines += fmt_map("By asset class (funds looked through)", data.get("by_asset_class", {}))
    lines += fmt_map("By region (equity, funds looked through)", data.get("by_region_equity", {}))
    lines += fmt_map("By region (all)", data.get("by_region", {}))
    lines += fmt_map("By sector", data.get("by_sector", {}))
    lines += fmt_map("By currency exposure (approximate)", data.get("by_currency_exposure", {}))
    lines += fmt_map("By quote currency", data.get("by_currency", {}))
    lines += fmt_map("By asset type", data.get("by_asset_type", {}))
    lines += fmt_map("By country (direct holdings)", data.get("by_country", {}))

    # Overlap is a second call, and a useful breakdown must not be lost if it
    # fails.
    try:
        overlap = _get("/api/v1/analytics/fund-overlap")
    except Exception as e:
        lines.append(f"\nFund overlap unavailable: {e}")
        return "\n".join(lines)

    groups = overlap.get("groups", [])
    if groups:
        lines.append("\nFunds holding the same exposure:")
        for group in groups:
            names = " + ".join(m["name"] for m in group["members"])
            tag = {
                "consolidation_candidate": "consolidation candidate",
                "similar": "similar",
                "informational": "nested, informational",
            }.get(group["kind"], group["kind"])
            lines.append(
                f"  [{tag}] {names} — {_fmt_currency(group['combined_value_eur'])} "
                f"({group['combined_pct']:.1f}%)"
            )
            lines.append(f"      {group['reason']}")
            if group.get("transferable"):
                lines.append(
                    "      Both are funds: a traspaso can merge them without "
                    "realising a gain."
                )
    return "\n".join(lines)
```

- [ ] **Step 4: Run the MCP tests**

Run: `cd ~/mcp/pfm && python3 -m pytest -q .`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/repos/pfm
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black mcp/server.py
git add mcp/server.py
git commit -m "feat(mcp): report regions, coverage and fund overlap in diversification"
```

---

### Task 11: Web client — API methods and pure helpers

**Files:**
- Modify: `web_client/js/pfm_core.js` (apiClient methods)
- Modify: `web_client/js/pfm_analytics.js` (pure helpers at module scope)
- Test: `web_client/js/tests/fund_profiles.test.mjs`

**Interfaces:**
- Produces on `apiClient`: `getFundOverlap()`, `getFundProfiles()`, `getFundProfile(assetId)`, `saveFundProfile(assetId, payload)`, `refreshFundProfile(assetId, benchmarkKey, force)`, `suggestFundProfile(assetId)`, `getBenchmarks()`
- Produces on `window`: `fundProfileValidate(profile) -> string[]`, `normalizeWeights(map) -> object`, `regionLabel(key) -> string`, `overlapGroupLabel(kind) -> string`

- [ ] **Step 1: Write the failing test**

Create `web_client/js/tests/fund_profiles.test.mjs`, following the loader pattern in `web_client.test.mjs` (import its `loadAppIntoContext` if exported, otherwise copy the same sandbox setup):

```javascript
// Pure helpers behind the fund profile editor and the overlap card.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

const win = loadAppIntoContext();

test("regionLabel renders a human label", () => {
    assert.equal(win.regionLabel("north_america"), "North America");
    assert.equal(win.regionLabel("europe_ex_uk"), "Europe ex-UK");
    assert.equal(win.regionLabel("unknown"), "Unclassified");
});

test("regionLabel passes an unexpected key through", () => {
    assert.equal(win.regionLabel("mars"), "mars");
});

test("overlapGroupLabel names each kind", () => {
    assert.equal(win.overlapGroupLabel("consolidation_candidate"), "Consolidation candidate");
    assert.equal(win.overlapGroupLabel("informational"), "Nested (informational)");
    assert.equal(win.overlapGroupLabel("similar"), "Similar exposure");
});

test("normalizeWeights turns percentages into fractions summing to 1", () => {
    const out = win.normalizeWeights({ north_america: 70, japan: 30 });
    assert.equal(out.north_america, 0.7);
    assert.equal(out.japan, 0.3);
});

test("normalizeWeights drops zero and blank entries", () => {
    const out = win.normalizeWeights({ north_america: 100, japan: 0, uk: "" });
    assert.deepEqual(Object.keys(out), ["north_america"]);
});

test("fundProfileValidate accepts a complete profile", () => {
    const problems = win.fundProfileValidate({
        regions: { north_america: 0.7, japan: 0.3 },
        asset_class: { equity: 1 },
        sectors: { Technology: 1 },
    });
    assert.deepEqual(problems, []);
});

test("fundProfileValidate rejects regions that miss 100%", () => {
    const problems = win.fundProfileValidate({
        regions: { north_america: 0.5 },
        asset_class: { equity: 1 },
        sectors: {},
    });
    assert.equal(problems.length, 1);
    assert.match(problems[0], /region/i);
});

test("fundProfileValidate rejects an empty asset class", () => {
    const problems = win.fundProfileValidate({
        regions: { north_america: 1 },
        asset_class: {},
        sectors: {},
    });
    assert.match(problems[0], /asset class/i);
});

test("fundProfileValidate allows empty sectors", () => {
    const problems = win.fundProfileValidate({
        regions: { north_america: 1 },
        asset_class: { bond: 1 },
        sectors: {},
    });
    assert.deepEqual(problems, []);
});

test("fundProfileValidate tolerates rounding within half a point", () => {
    const problems = win.fundProfileValidate({
        regions: { north_america: 0.333, europe_ex_uk: 0.333, japan: 0.334 },
        asset_class: { equity: 1 },
        sectors: {},
    });
    assert.deepEqual(problems, []);
});
```

Extract the sandbox builder from `web_client.test.mjs` into `web_client/js/tests/helpers.mjs` exporting `loadAppIntoContext()`, and have `web_client.test.mjs` import it, so both test files load the app the same way.

- [ ] **Step 2: Run to verify failure**

Run: `make test-js`
Expected: FAIL — `win.regionLabel is not a function`

- [ ] **Step 3: Add the apiClient methods**

In `web_client/js/pfm_core.js`, next to `getDiversification()`:

```javascript
        async getFundOverlap() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/fund-overlap', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },
        async getFundProfiles() {
            const resp = await fetch(this.baseURL + '/api/v1/fund-profiles/', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load fund profiles');
            return resp.json();
        },
        async getBenchmarks() {
            const resp = await fetch(this.baseURL + '/api/v1/fund-profiles/benchmarks', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load benchmarks');
            return resp.json();
        },
        async getFundProfile(assetId) {
            const resp = await fetch(this.baseURL + '/api/v1/fund-profiles/' + assetId, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (resp.status === 404) return null;
            if (!resp.ok) throw new Error('Failed to load fund profile');
            return resp.json();
        },
        async saveFundProfile(assetId, payload) {
            const resp = await fetch(this.baseURL + '/api/v1/fund-profiles/' + assetId, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(payload)
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'Failed to save profile');
            return resp.json();
        },
        async refreshFundProfile(assetId, benchmarkKey, force = false) {
            const resp = await fetch(this.baseURL + '/api/v1/fund-profiles/' + assetId + '/refresh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ benchmark_key: benchmarkKey, force })
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'Failed to refresh profile');
            return resp.json();
        },
        async suggestFundProfile(assetId) {
            const resp = await fetch(this.baseURL + '/api/v1/fund-profiles/' + assetId + '/suggest', {
                method: 'POST',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'Suggestion failed');
            return resp.json();
        },
```

- [ ] **Step 4: Add the pure helpers**

At module scope in `web_client/js/pfm_analytics.js` (not inside a function), next to the other diversification code:

```javascript
// Region keys as the API returns them, and how they read in the UI.
const REGION_LABELS = {
    north_america: 'North America',
    europe_ex_uk: 'Europe ex-UK',
    uk: 'United Kingdom',
    japan: 'Japan',
    pacific_ex_japan: 'Pacific ex-Japan',
    emerging: 'Emerging Markets',
    unknown: 'Unclassified'
};

function regionLabel(key) {
    return REGION_LABELS[key] || key;
}

function overlapGroupLabel(kind) {
    return {
        consolidation_candidate: 'Consolidation candidate',
        similar: 'Similar exposure',
        informational: 'Nested (informational)'
    }[kind] || kind;
}

// Form inputs are percentages; the API stores fractions.
function normalizeWeights(map) {
    const out = {};
    Object.entries(map || {}).forEach(([key, raw]) => {
        const value = parseFloat(raw);
        if (!isFinite(value) || value === 0) return;
        out[key] = Math.round(value) / 100;
    });
    return out;
}

// Mirrors the server's validate_profile so the form fails before the request.
function fundProfileValidate(profile) {
    const problems = [];
    const sum = (map) => Object.values(map || {}).reduce((a, b) => a + parseFloat(b || 0), 0);
    const regions = profile.regions || {};
    if (!Object.keys(regions).length || Math.abs(sum(regions) - 1) >= 0.005) {
        problems.push(`Region weights must total 100% (currently ${(sum(regions) * 100).toFixed(1)}%).`);
    }
    const classes = profile.asset_class || {};
    if (!Object.keys(classes).length || Math.abs(sum(classes) - 1) >= 0.005) {
        problems.push(`Asset class weights must total 100% (currently ${(sum(classes) * 100).toFixed(1)}%).`);
    }
    const sectors = profile.sectors || {};
    if (Object.keys(sectors).length && Math.abs(sum(sectors) - 1) >= 0.005) {
        problems.push(`Sector weights must total 100% or be left empty (currently ${(sum(sectors) * 100).toFixed(1)}%).`);
    }
    return problems;
}

window.regionLabel = regionLabel;
window.overlapGroupLabel = overlapGroupLabel;
window.normalizeWeights = normalizeWeights;
window.fundProfileValidate = fundProfileValidate;
```

- [ ] **Step 5: Run the JS tests**

Run: `make test-js`
Expected: PASS (existing tests plus 10 new)

- [ ] **Step 6: Commit**

```bash
git add web_client/js/pfm_core.js web_client/js/pfm_analytics.js web_client/js/tests/
git commit -m "feat(web): fund profile API methods and exposure helpers"
```

---

### Task 12: Web client — coverage banner, new bars, overlap card, profile editor

**Files:**
- Modify: `web_client/index.html` (the profile modal, and the diversification card's hint text)
- Modify: `web_client/js/pfm_analytics.js` (`loadAnalyticsDiversification`, new render functions)
- Test: manual, in the browser, after a redeploy

**Interfaces:**
- Consumes: Task 11's apiClient methods and helpers
- Produces: `window.openFundProfileModal(assetId, symbol, name)`

**Not included, deliberately:** the spec also mentioned a per-row "Profile" action on fund rows of the Assets page. The coverage banner and the overlap card both reach every fund that needs attention, so the third entry point is dropped unless it is asked for. `openFundProfileModal` is on `window`, so adding it later is one button.

- [ ] **Step 1: Add the modal markup**

In `web_client/index.html`, next to the other modals, add:

```html
<div class="modal fade" id="fpModal" tabindex="-1">
  <div class="modal-dialog modal-lg">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="fpModalTitle">Fund profile</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <p class="text-muted small">
          A fund's region weights come from the index it tracks; sector weights come from Yahoo Finance.
          Edit anything here and it is kept as a manual profile.
        </p>
        <div class="row g-3">
          <div class="col-md-6">
            <label class="form-label small fw-semibold">Benchmark index</label>
            <select class="form-select form-select-sm" id="fpBenchmark"></select>
          </div>
          <div class="col-md-3">
            <label class="form-label small fw-semibold">Data as of</label>
            <input type="date" class="form-control form-control-sm" id="fpAsOf">
          </div>
          <div class="col-md-3 d-flex align-items-end">
            <div class="form-check">
              <input class="form-check-input" type="checkbox" id="fpHedged">
              <label class="form-check-label small" for="fpHedged">Currency hedged</label>
            </div>
          </div>
        </div>
        <div class="row g-3 mt-1">
          <div class="col-md-6">
            <h6 class="small fw-semibold text-uppercase text-muted">Regions (%)</h6>
            <div id="fpRegions"></div>
          </div>
          <div class="col-md-6">
            <h6 class="small fw-semibold text-uppercase text-muted">Asset class (%)</h6>
            <div id="fpAssetClass"></div>
            <h6 class="small fw-semibold text-uppercase text-muted mt-3">Sectors (%)</h6>
            <div id="fpSectors" class="small text-muted"></div>
          </div>
        </div>
        <div id="fpProblems" class="text-danger small mt-3"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-outline-secondary btn-sm" id="fpSuggestBtn">
          <i class="bi bi-stars me-1"></i>Suggest (AI)
        </button>
        <button class="btn btn-outline-primary btn-sm" id="fpRefreshBtn">
          <i class="bi bi-arrow-clockwise me-1"></i>Fill from benchmark
        </button>
        <button class="btn btn-primary btn-sm" id="fpSaveBtn">Save</button>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Render coverage, the new bars and the overlap card**

In `web_client/js/pfm_analytics.js`, inside `loadAnalyticsDiversification`, after `const d = await window.apiClient.getDiversification();`:

- Build `blocks` from the new fields:

```javascript
        const blocks = [
            { title: 'By Asset Class', data: d.by_asset_class, upper: true },
            { title: 'By Region (equity)', data: _labelRegions(d.by_region_equity), upper: false },
            { title: 'By Sector', data: d.by_sector, upper: false },
            { title: 'By Currency Exposure', data: d.by_currency_exposure, upper: true },
            { title: 'By Asset Type', data: d.by_asset_type, upper: true },
            { title: 'By Country (direct)', data: d.by_country, upper: false }
        ];
```

- Add these module-scope helpers:

```javascript
function _labelRegions(map) {
    const out = {};
    Object.entries(map || {}).forEach(([key, pct]) => { out[regionLabel(key)] = pct; });
    return out;
}

// Coverage is stated before any breakdown: below 100%, none of them is complete.
function renderCoverageBanner(coverage) {
    if (!coverage) return '';
    const pct = parseFloat(coverage.classified_pct || 0);
    const unprofiled = coverage.unprofiled || [];
    const stale = coverage.stale_profiles || [];
    if (pct >= 99.9 && !unprofiled.length && !stale.length) {
        return `<div class="alert alert-success py-2 small mb-3">
            All holdings classified. Sector coverage ${parseFloat(coverage.sector_classified_pct || 0).toFixed(0)}%.
        </div>`;
    }
    const links = unprofiled.map(f => `
        <a href="#" class="fp-open" data-asset="${f.asset_id}" data-symbol="${esc(f.symbol)}"
           data-name="${esc(f.name || f.symbol)}">${esc(f.name || f.symbol)}</a>
        <span class="text-muted">(${Fmt.num(f.value_eur, 0)} EUR)</span>`).join(', ');
    const staleLinks = stale.map(f => `
        <a href="#" class="fp-open" data-asset="${f.asset_id}" data-symbol="${esc(f.symbol)}"
           data-name="${esc(f.name || f.symbol)}">${esc(f.name || f.symbol)}</a>
        <span class="text-muted">(as of ${esc(f.as_of || '—')})</span>`).join(', ');
    return `<div class="alert alert-warning py-2 small mb-3">
        <strong>${pct.toFixed(0)}% of value classified</strong> by region,
        ${parseFloat(coverage.sector_classified_pct || 0).toFixed(0)}% by sector.
        ${unprofiled.length ? `<div class="mt-1">No look-through profile: ${links}</div>` : ''}
        ${stale.length ? `<div class="mt-1">Profile over a year old: ${staleLinks}</div>` : ''}
    </div>`;
}

function renderOverlapCard(groups) {
    if (!groups || !groups.length) return '';
    const primary = groups.filter(g => g.kind !== 'informational');
    const nested = groups.filter(g => g.kind === 'informational');
    const row = (g) => `
        <div class="border rounded p-2 mb-2">
            <div class="d-flex justify-content-between align-items-start gap-2">
                <div>
                    <span class="badge ${g.kind === 'consolidation_candidate' ? 'bg-warning text-dark' : 'bg-secondary'} me-1">${esc(overlapGroupLabel(g.kind))}</span>
                    ${g.members.map(m => `<a href="#" class="fp-open" data-asset="${m.asset_id}" data-symbol="${esc(m.symbol)}" data-name="${esc(m.name)}">${esc(m.name)}</a> <span class="text-muted small">(${esc(m.portfolio_name)})</span>`).join(' + ')}
                    <div class="small text-muted">${esc(g.reason)}${g.transferable ? ' Both are funds, so a traspaso can merge them without realising a gain.' : ''}</div>
                </div>
                <div class="text-nowrap small fw-semibold">${g.combined_pct.toFixed(1)}%</div>
            </div>
        </div>`;
    return `
        <h6 class="fw-semibold small text-muted text-uppercase mt-4 mb-2">Funds holding the same exposure</h6>
        ${primary.map(row).join('')}
        ${nested.length ? `
            <button class="btn btn-sm btn-link p-0" type="button" data-bs-toggle="collapse" data-bs-target="#anOverlapNested">
                Show ${nested.length} nested holding${nested.length > 1 ? 's' : ''} (informational)
            </button>
            <div class="collapse mt-2" id="anOverlapNested">${nested.map(row).join('')}</div>` : ''}`;
}
```

- Insert `renderCoverageBanner(d.coverage)` directly above the KPI row in the card's `innerHTML`, and append the overlap card after the bars. Fetch overlap alongside the main call so a failure there cannot blank the view:

```javascript
        let overlapHtml = '';
        try {
            const overlap = await window.apiClient.getFundOverlap();
            overlapHtml = renderOverlapCard(overlap.groups);
        } catch (err) {
            overlapHtml = `<div class="text-muted small mt-3">Fund overlap unavailable: ${esc(err.message)}</div>`;
        }
```

- After setting `innerHTML`, wire the profile links:

```javascript
        body.querySelectorAll('.fp-open').forEach(a => {
            a.addEventListener('click', (e) => {
                e.preventDefault();
                window.openFundProfileModal(
                    parseInt(a.dataset.asset, 10), a.dataset.symbol, a.dataset.name);
            });
        });
```

- [ ] **Step 3: Implement the profile editor**

Add to `web_client/js/pfm_analytics.js`:

```javascript
const FP_REGION_KEYS = ['north_america', 'europe_ex_uk', 'uk', 'japan', 'pacific_ex_japan', 'emerging'];
const FP_CLASS_KEYS = ['equity', 'bond', 'cash', 'other'];

function _fpWeightRows(containerId, keys, weights, labelFn) {
    const container = document.getElementById(containerId);
    container.innerHTML = keys.map(key => `
        <div class="d-flex align-items-center gap-2 mb-1">
            <label class="small flex-grow-1">${esc(labelFn(key))}</label>
            <input type="number" min="0" max="100" step="0.1" class="form-control form-control-sm fp-weight"
                   style="max-width:6rem" data-key="${key}" value="${((weights[key] || 0) * 100).toFixed(1)}">
        </div>`).join('');
}

function _fpCollect(containerId) {
    const map = {};
    document.getElementById(containerId).querySelectorAll('.fp-weight').forEach(input => {
        map[input.dataset.key] = parseFloat(input.value || 0);
    });
    return normalizeWeights(map);
}

// Sectors come from Yahoo Finance and are shown read-only: hand-entering a
// sector split is busywork, and an empty one is honest about a failed fetch.
function _fpRenderSectors(sectors) {
    const entries = Object.entries(sectors || {});
    document.getElementById('fpSectors').innerHTML = entries.length
        ? entries.map(([k, v]) => `${esc(k)} ${(v * 100).toFixed(1)}%`).join(' · ')
        : 'No sector data (normal for a bond fund, or Yahoo Finance had none).';
}

window.openFundProfileModal = async function (assetId, symbol, name) {
    const modal = new bootstrap.Modal(document.getElementById('fpModal'));
    document.getElementById('fpModalTitle').textContent = `${name || symbol} — fund profile`;
    document.getElementById('fpProblems').textContent = '';
    let sectors = {};

    const { benchmarks } = await window.apiClient.getBenchmarks();
    document.getElementById('fpBenchmark').innerHTML =
        '<option value="">(none — no index)</option>' +
        benchmarks.map(b => `<option value="${esc(b.key)}">${esc(b.label)}</option>`).join('');

    const profile = await window.apiClient.getFundProfile(assetId);
    document.getElementById('fpBenchmark').value = (profile && profile.benchmark_key) || '';
    document.getElementById('fpAsOf').value = (profile && profile.as_of) || new Date().toISOString().slice(0, 10);
    document.getElementById('fpHedged').checked = !!(profile && profile.currency_hedged);
    _fpWeightRows('fpRegions', FP_REGION_KEYS, (profile && profile.regions) || {}, regionLabel);
    _fpWeightRows('fpAssetClass', FP_CLASS_KEYS, (profile && profile.asset_class) || {}, k => k);
    sectors = (profile && profile.sectors) || {};
    _fpRenderSectors(sectors);

    document.getElementById('fpRefreshBtn').onclick = async () => {
        const key = document.getElementById('fpBenchmark').value;
        if (!key) { document.getElementById('fpProblems').textContent = 'Pick a benchmark first.'; return; }
        try {
            const filled = await window.apiClient.refreshFundProfile(assetId, key, true);
            _fpWeightRows('fpRegions', FP_REGION_KEYS, filled.regions, regionLabel);
            _fpWeightRows('fpAssetClass', FP_CLASS_KEYS, filled.asset_class, k => k);
            sectors = filled.sectors || {};
            _fpRenderSectors(sectors);
            document.getElementById('fpAsOf').value = filled.as_of || '';
            document.getElementById('fpProblems').textContent = '';
        } catch (err) {
            document.getElementById('fpProblems').textContent = err.message;
        }
    };

    document.getElementById('fpSuggestBtn').onclick = async () => {
        document.getElementById('fpProblems').textContent = 'Asking the model…';
        try {
            const { suggestion, problems } = await window.apiClient.suggestFundProfile(assetId);
            _fpWeightRows('fpRegions', FP_REGION_KEYS, suggestion.regions, regionLabel);
            _fpWeightRows('fpAssetClass', FP_CLASS_KEYS, suggestion.asset_class, k => k);
            sectors = suggestion.sectors || {};
            _fpRenderSectors(sectors);
            // A draft, never saved on its own — review it, then Save.
            document.getElementById('fpProblems').textContent =
                (problems || []).join(' ') + ' Review these values before saving.';
        } catch (err) {
            document.getElementById('fpProblems').textContent = err.message;
        }
    };

    document.getElementById('fpSaveBtn').onclick = async () => {
        const payload = {
            benchmark_key: document.getElementById('fpBenchmark').value || null,
            regions: _fpCollect('fpRegions'),
            asset_class: _fpCollect('fpAssetClass'),
            sectors,
            currency_hedged: document.getElementById('fpHedged').checked,
            hedge_currency: document.getElementById('fpHedged').checked ? 'EUR' : null,
            as_of: document.getElementById('fpAsOf').value
        };
        const problems = fundProfileValidate(payload);
        if (problems.length) { document.getElementById('fpProblems').textContent = problems.join(' '); return; }
        try {
            await window.apiClient.saveFundProfile(assetId, payload);
            modal.hide();
            loadAnalyticsDiversification();
        } catch (err) {
            document.getElementById('fpProblems').textContent = err.message;
        }
    };

    modal.show();
};
```

- [ ] **Step 4: Update the card's hint text**

In `web_client/index.html`, replace the two "Sector / country breakdown is fetched live from Yahoo Finance and takes ~20-30s" strings with "Exposure with funds looked through. Fetches live data for direct holdings — takes ~20-30s."

- [ ] **Step 5: Run the JS tests and redeploy**

```bash
make test-js
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```

- [ ] **Step 6: Check it in the browser**

Open the Analytics page → Risk tab → "Load diversification". Confirm: the coverage banner names unprofiled funds; clicking one opens the modal; "Fill from benchmark" populates the weights; Save closes the modal and the banner's number rises.

- [ ] **Step 7: Commit**

```bash
git add web_client/index.html web_client/js/pfm_analytics.js
git commit -m "feat(web): coverage banner, region bars, overlap card and profile editor"
```

---

### Task 13: Backfill, verify, document

**Files:**
- Modify: `PROJECT_STATUS.md`, `CLAUDE.md`
- Data only: fund profiles, two `asset_type` corrections, missing tickers

**Interfaces:**
- Consumes: everything above
- Produces: coverage above 95% on the live database, and documentation

- [ ] **Step 1: Correct the two index funds typed as `stock`**

These are `mutual_fund`, not `stock`; they were created by a heuristic import. Find them and fix them through the API (this needs no code change):

```bash
KEY=$(grep -E '^(SERVER_API_KEY|PORTF_API_KEY)=' .env.local | head -1 | cut -d= -f2-)
curl -s -H "X-API-Key: $KEY" http://localhost:8000/api/v1/assets/ \
  | python3 -c "
import json,sys
for a in json.load(sys.stdin):
    name = (a.get('name') or '').lower()
    if a['asset_type'] == 'stock' and any(w in name for w in ('index fund', 'idx', 'fondo')):
        print(a['id'], a['symbol'], a['name'])
"
```

For each id printed: `curl -s -X PUT -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{"asset_type":"mutual_fund"}' http://localhost:8000/api/v1/assets/<id>`

- [ ] **Step 2: List the funds that need a profile**

```bash
curl -s -H "X-API-Key: $KEY" http://localhost:8000/api/v1/fund-profiles/ \
  | python3 -m json.tool
```

- [ ] **Step 3: Set each profile**

For each fund, pick its benchmark from `GET /api/v1/fund-profiles/benchmarks` and call refresh. A fund whose index is not in the table gets `POST /{id}/suggest`, then a reviewed `PUT`:

```bash
curl -s -X POST -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"benchmark_key":"msci_world"}' \
  http://localhost:8000/api/v1/fund-profiles/<asset_id>/refresh | python3 -m json.tool
```

A fund whose sectors come back empty needs its `ticker` set first (`PUT /api/v1/assets/{id} {"ticker": "..."}`), then a refresh.

For a currency-hedged bond fund, follow the refresh with a `PUT` setting `currency_hedged: true` and `hedge_currency: "EUR"` — the benchmark table does not know a particular share class is hedged.

- [ ] **Step 4: Verify coverage**

```bash
curl -s -m 180 -H "X-API-Key: $KEY" http://localhost:8000/api/v1/analytics/diversification \
  | python3 -c "
import json,sys
d = json.load(sys.stdin)
c = d['coverage']
print('classified', c['classified_pct'], 'sector', c['sector_classified_pct'])
print('unprofiled', [f['symbol'] for f in c['unprofiled']])
print('regions', d['by_region_equity'])
print('currency exposure', d['by_currency_exposure'])
"
```

Expected: `classified` above 95, `unprofiled` empty, `by_region_equity` dominated by North America, and `by_currency_exposure` showing far more USD than the quote-currency view did.

Also check overlap found the emerging-market funds:

```bash
curl -s -m 180 -H "X-API-Key: $KEY" http://localhost:8000/api/v1/analytics/fund-overlap \
  | python3 -c "
import json,sys
for g in json.load(sys.stdin)['groups']:
    print(g['kind'], [m['name'] for m in g['members']], g['combined_pct'])
"
```

- [ ] **Step 5: Run every test suite**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
make test-js
cd ~/mcp/pfm && python3 -m pytest -q . && cd ~/repos/pfm
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501
```
Expected: all green, flake8 silent.

- [ ] **Step 6: Update the docs**

`PROJECT_STATUS.md`: bump "Last updated" to the current date and add a Recent line describing look-through exposure, the `fund_profiles` table, the new endpoints, the coverage figure and overlap detection, plus the before/after coverage numbers.

`CLAUDE.md`:
- Migration history: a `- v30: fund_profiles (...)` line.
- Analytics API section: the new `/analytics/diversification` fields and `/analytics/fund-overlap`.
- A new "Fund look-through" subsection: where region weights come from and why not yfinance; that `benchmarks.json` is hand-maintained with `as_of`; that a fund with no profile is named rather than folded into Unknown; that `by_currency` is quote currency while `by_currency_exposure` is look-through and approximate; and that the exposure service is the single implementation both the endpoint and Portfolio Health use.
- Action Items section: the eighth check and the `exposure` category.
- Web client section: the coverage banner, overlap card and `#fpModal`.

- [ ] **Step 7: Commit**

```bash
git add PROJECT_STATUS.md CLAUDE.md
git commit -m "docs: fund look-through exposure"
```

---

## Follow-up (not in this plan)

`~/mcp/scripts/finance_review.py` (the Sunday review, separate repo) reads `/analytics/diversification`. Adding region exposure and coverage to that message is a few lines, done after this lands.
