# Next-Euro Allocator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute deployable cash (liquid minus an autónomo tax reserve minus a 6-month buffer) and propose concrete, reasoned lines for new money, on the Dashboard, the Rebalance card's page and as an MCP tool.

**Architecture:** Two pure service modules, `deployable.py` (settings, tax reserve, buffer, deployable) and `allocator.py` (candidates, bucket split, ranking, traspasos, chat summary), plus `wash_sale.py` extracted from the MCP server; one router `allocate.py`; two Action Item checks; a dashboard card and a "Next euro" card beside the rebalance card; MCP tool `next_euro`. No schema change (settings in `app_settings` key `allocator_settings`).

**Tech Stack:** Python 3.13, FastAPI, SQLite, pytest; vanilla JS + Bootstrap 5.3 with Node's test runner.

**Spec:** `docs/superpowers/specs/2026-10-19-next-euro-allocator-design.md`

## Global Constraints

- Work only in `~/repos/pfm-allocator` (branch `feat/next-euro-allocator`); never edit `~/repos/pfm`, `~/mcp` or `~/agents` before the merge.
- Tooling: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest …`, black, flake8 (`--max-line-length=88 --extend-ignore=E203,W503,E501`, 0 warnings), `make test-js`. Tests cannot reach the network; inject `fx=lambda c: 1.0`.
- No schema change; schema stays 35. Settings key `allocator_settings`, defaults exactly: `buffer_months_target 6, buffer_months_min 4, buffer_months_max 8, iva_pct 21.0, irpf_pct 20.0, irpf_retention_pct 0.0, income_category "Income", work_income_merchants [], tax_paid_categories ["Income Tax"], managed_portfolio_ids [], min_line_eur 100, max_lines_per_bucket 2, max_single_stock_pct 5.0, traspaso_threshold_pp 5.0`; validation `0 < min ≤ target ≤ max ≤ 24`, percentages 0–100, amounts ≥ 0, portfolio ids must exist.
- Deployable: `liquid` = counted (`in_total`) lines of the balance sheet `cash` group; `deployable = max(0, liquid − tax_reserve − target_months × monthly_spend)`; buffer status `rebuild` (< min), `topping_up` (min..target), `ok` (target..max), `idle` (> max, `idle_eur = liquid − reserve − max × spend`); monthly spend = `retirement.spending_inputs` with the plan's exclusions minus "Social Security"; fewer than 6 months → `insufficient_data`.
- Tax reserve per quarter: `iva = gross × iva/(100+iva)`, `net = gross − iva`, `irpf = net × irpf_pct/100 − net × retention/100`, `accrued = max(0, iva + irpf)`; work income = positive non-transfer rows in `income_category` whose `merchant` is in `work_income_merchants` (empty list = all, with a warning); a `tax_paid_categories` row dated within 30 days after a quarter end settles that quarter; previous quarter reserved until settled or 30 days past its due date (20th of the month after the quarter; 30 January for Q4).
- Proposal: buckets by raw `asset_type` from `merge_targets`; `targets_incomplete` when a held type > 1 % has no target or targets ≠ 100; cash and candidate-less buckets renormalised away; denominator = `compute_before_state` positions value; traspasos first, then water-filling of new money over underweights (proportional to gap, capped at gap, remainder by target), lines under `min_line_eur` dropped back to the pool; ≤ `max_lines_per_bucket` lines per bucket; stocks capped at `max_single_stock_pct` after the buy; antiaplicación blocks; unresearched stocks → `needs_research`; managed portfolio = one line; every line carries `portfolio_id`/`portfolio_name`.
- Fund heuristic (shared): `asset_type == "mutual_fund"` or `exchange == "Funds"` or (not `etf` and a fund word in the name); traspaso only between such funds, never ETFs, never from a managed portfolio.
- Public repo: invented names and round figures in tests and docs.
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Docs are part of the last task: `docs/features/analytics.md` or a new `docs/features/allocator.md` (new area → new file + a Feature reference row in `CLAUDE.md`), `docs/features/action-items.md`, `docs/features/web-client.md`, `PROJECT_STATUS.md`.

## Review Focus

1. A quarter with no work-income rows must reserve 0 for that quarter and say so, never crash or reserve a stale previous figure twice (Task 2).
2. A tax payment dated on the quarter's due date (20th) must settle the previous quarter, and one dated 31 days after the quarter end must not (Task 2).
3. Targets summing to 99 or 101 must give `targets_incomplete`, never a 500 from `validate_target_sum` (Task 4).
4. A stock sold at a loss 59 days ago must be blocked; the same sale 61 days ago must not (Task 1).
5. An `amount` larger than deployable must still produce a proposal with the "exceeds deployable" warning, and a managed portfolio must appear as exactly one line (Task 5).

---

### Task 1: Shared antiaplicación service

**Files:**
- Create: `portf_manager/services/wash_sale.py`
- Modify: `mcp/server.py` (`_wash_window_months`, `_wash_sale_analysis` → imported from the service; `wash_sale_check` output unchanged)
- Test: `tests/unit/test_wash_sale.py`

**Interfaces:**
- `is_fund(asset: dict) -> bool` (the heuristic above), `window_months(asset: dict) -> int` (12 for funds, 2 otherwise), `analyse(lots, transactions, assets, today, symbol=None, holdings=None) -> dict` (the current MCP function, moved verbatim with its docstring), `blocked_assets(db, today: date, fx) -> dict[int, dict]` → `{asset_id: {"symbol", "sold_on", "loss_eur", "safe_from"}}` built from `analytics.calculate_tax_report`-style realised lots for this year and last (reuse the tax-report FIFO via `portf_server.routers.analytics` helpers or the service they call; crypto excluded).

- [ ] **Step 1: Write the failing tests**

```python
def test_is_fund_by_type_exchange_and_name(): ...   # mutual_fund True; exchange Funds True; "Example Index Fund" stock True; etf False
def test_window_months(): assert window_months({"asset_type": "etf"}) == 2 and window_months({"asset_type": "mutual_fund"}) == 12
def test_blocked_59_days_ago_not_61(tmp_path):  # Review Focus 4: buy 2026-01-01 @ 100, sell 2026-08-20 @ 80 → blocked on 2026-10-18 (59 d), not on 2026-10-20 (61 d); safe_from == 2026-10-20
def test_analyse_moved_verbatim():  # one fixture from the existing ~/mcp/pfm tests, same output dict
```

- [ ] **Step 2: Run to verify they fail.** `… pytest tests/unit/test_wash_sale.py -v` → ImportError.
- [ ] **Step 3: Create the service; make `mcp/server.py` import `window_months as _wash_window_months` and `analyse as _wash_sale_analysis`** so existing MCP tests keep passing unchanged.
- [ ] **Step 4: Run `tests/unit/test_wash_sale.py` and `cd mcp && python3 -m pytest -q test_server_retirement.py`** → PASS; flake8 clean.
- [ ] **Step 5: Commit** — `refactor: antiaplicación rule as a shared service`.

---

### Task 2: Settings and the tax reserve

**Files:**
- Create: `portf_manager/services/deployable.py`
- Test: `tests/unit/test_deployable.py`

**Interfaces:**
- `SETTINGS_KEY = "allocator_settings"`, `DEFAULT_SETTINGS: dict` (Global Constraints), `load_settings(db) -> dict` (defaults merged with stored; fills `managed_portfolio_ids` with the id of a portfolio whose name contains "indexa" when empty, and returns `defaulted_managed: True` in that case), `save_settings(db, patch: dict) -> dict` (validates, raises `ValueError`).
- `quarter_of(day: date) -> tuple[int, int]`, `quarter_bounds(year, q) -> (start, end)`, `due_date(year, q) -> date` (20th of the month after; Q4 → 30 January).
- `work_income_rows(rows: list[dict], settings) -> list[dict]` (positive, non-transfer, category == income_category, merchant filter).
- `quarter_reserve(rows, tax_rows, year, q, settings, today) -> dict` → `{quarter, gross_eur, iva_eur, irpf_eur, paid_eur, accrued_eur, due, settled: bool}`.
- `tax_reserve(rows, tax_rows, settings, today) -> dict` → `{reserve_eur, quarters: [...], merchants_counted: [...], warnings: [...]}`; `rows` are spending rows `{date, amount_eur, category, merchant, is_transfer}`.

- [ ] **Step 1: Write the failing tests** (round numbers; `settings = DEFAULT_SETTINGS | {...}`)

```python
def test_defaults_and_validation(tmp_path):  # load → defaults; save {"buffer_months_min": 7} → ValueError; save {"iva_pct": 10} → stored
def test_quarter_reserve_formula():  # gross 12100 in Q4 → iva 2100, net 10000, irpf 2000, accrued 4100
def test_retention_reduces_irpf():  # retention 15 → irpf 2000 − 1500 = 500
def test_partner_transfers_not_counted():  # merchants ["Example Client"]: a 3000 row from "Partner" is ignored; merchants_counted == ["Example Client"]
def test_empty_merchant_list_counts_all_with_warning(): ...
def test_quarter_without_income_reserves_zero():  # Review Focus 1
def test_payment_on_due_date_settles_previous_quarter():  # Review Focus 2: Q3 accrued 4100, tax row 2026-10-20 of 4100 → previous remaining 0; row dated 2026-10-31 (31 d) → not applied
def test_previous_quarter_kept_until_due_plus_30():  # today 2026-10-10, no payment → reserve includes Q3; today 2026-11-25 → Q3 dropped
def test_due_date_q4_is_30_january(): ...
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the file** → PASS; flake8.
- [ ] **Step 5: Commit** — `feat: allocator settings and the autónomo tax reserve`.

---

### Task 3: Deployable cash and its endpoints

**Files:**
- Modify: `portf_manager/services/deployable.py` (add `buffer`, `deployable`)
- Create: `portf_server/routers/allocate.py` (`GET /deployable`, `GET|PUT /settings`), registered in `portf_server/app.py` at `/api/v1/allocate` next to `retirement.router`
- Test: `tests/unit/test_deployable.py` (extend), `tests/unit/test_allocate_api.py`

**Interfaces:**
- Consumes: `balance_sheet.build_balance_sheet(db, today, fx, fx_on)` (`cash` group lines with `in_total`, `stale`), `retirement.load_plan(db)` + `retirement.spending_inputs(db, plan, today, fx)` (`target_monthly_eur`, `months_used`), `db.list_spending_transactions(start_date=..., is_transfer=False)`, `db.get_fixed_deposits(status="active")`.
- Produces: `buffer(liquid_eur, reserve_eur, monthly_spend_eur, settings) -> dict` (`target_eur, months, status, idle_eur`), `deployable(db, today=None, fx=None, fx_on=None, settings=None) -> dict` = spec §4.3 response (`status`, `liquid_eur`, `liquid_lines`, `tax_reserve`, `buffer` incl. `monthly_spend_eur`, `months_used`, `retirement_monthly_spend_eur`, `deployable_eur`, `coming_in` (deposits maturing within 30 days), `stale_lines`, `warnings`).
- Router: `SettingsUpdate` pydantic model with every key optional, `extra="ignore"`, `exclude_unset`; PUT returns the merged settings; `ValueError` → 422. `GET /settings` also returns `income_merchants: [...]` (distinct merchants of `income_category` rows in the last 12 months) for the form's checkboxes.

- [ ] **Step 1: Write the failing tests**

```python
def test_buffer_statuses():  # spend 1000: liquid−reserve 3500 → rebuild; 5000 → topping_up; 7000 → ok; 9000 → idle, idle_eur 1000
def test_deployable_formula_and_zero_floor():  # liquid 10000, reserve 1000, spend 1000 → deployable 3000; liquid 6000 → 0
def test_monthly_spend_adds_social_security_back(tmp_path):  # bank rows incl. 300/month Social Security → monthly_spend == retirement target + 300
def test_insufficient_data_under_six_months(tmp_path): ...
def test_negative_ledger_line_not_liquid(tmp_path): ...
def test_coming_in_lists_deposit_maturing_within_30_days(tmp_path): ...
# test_allocate_api.py (TestClient pattern from tests/unit/test_retirement_api.py)
def test_settings_roundtrip_and_422(tmp_path): ...
def test_settings_lists_income_merchants(tmp_path): ...
def test_deployable_endpoint_shape(tmp_path): ...
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement the service functions, the router and the app include.**
- [ ] **Step 4: Run both files plus `tests/unit/test_retirement_api.py`** → PASS; flake8.
- [ ] **Step 5: Commit** — `feat: deployable cash with tax reserve and buffer, allocate settings`.

---

### Task 4: Candidates and the bucket split

**Files:**
- Create: `portf_manager/services/allocator.py`
- Test: `tests/unit/test_allocator.py`

**Interfaces:**
- Consumes: `rebalance_planner.merge_targets(db, None)`, `compute_before_state(db, None, None) -> (before, warnings, holdings, transactions)` (`holdings` rows: `asset_id, symbol, asset_type, currency, quantity, price, price_eur, value_eur`, plus the portfolio id the builder attaches from the transactions), `validate_target_sum`, `exposure.compute_exposure(db, fx=fx, today=today)` (`funds`, `by_region`), `exposure.find_fund_overlaps(funds, total)`, `db.get_fund_profile(asset_id)` (`ongoing_charges_pct`), `db.get_watchlist()`, `db.get_asset_by_symbol`, `db.get_latest_research_notes()`, `db.get_price_target(asset_id)`, `db.get_research_report(asset_id)`, Task 1 `blocked_assets`, Task 1 `is_fund`.
- Produces: `build_candidates(db, holdings, settings, today, fx) -> list[dict]` (each: `asset_id, symbol, name, asset_type, bucket, portfolio_id, portfolio_name, value_eur, weight_pct, is_fund, ter_pct, overlap_group (kind, largest_member_id) | None, buy_below, fair_value, recommendation, conviction, blocked | None, managed: bool`); a managed portfolio's holdings collapse into one candidate `{"symbol": "contribution:<portfolio>", "managed": True, "ter_pct": value-weighted}` in the bucket of its largest asset type.
- `check_targets(targets: dict, before: dict) -> list[str]` (reasons; empty = ok; held type > 1 % without target; sum ≠ 100 within 0.01).
- `investable_targets(targets, candidates) -> dict` (drop `cash` and buckets with no candidate; renormalise to 100).
- `split_buckets(amount_eur, targets, before_values: dict[str, float], min_line_eur) -> list[dict]` (`asset_type, target_pct, before_pct, after_pct, gap_eur, allocated_eur`): water-filling per spec §5.2, pure.
- `plan_traspasos(candidates, targets, before_values, threshold_pp, lots_by_asset) -> (traspasos: list[dict], after_values)`: spec §5.5, gain via `rebalance_planner.fifo_gain_eur` on the moved amount.

- [ ] **Step 1: Write the failing tests** (pure functions with dict fixtures; one DB test for `build_candidates`)

```python
def test_check_targets_sum_99_is_incomplete():  # Review Focus 3
def test_check_targets_missing_held_type_over_1pct(): ...
def test_investable_targets_renormalise_without_cash():  # stock 50, etf 30, cash 20 → stock 62.5, etf 37.5
def test_split_water_fills_proportionally_and_caps():  # 1000 € over gaps stock 600, etf 200 (crypto overweight) → 750 / 250; crypto 0
def test_split_all_overweight_follows_targets(): ...
def test_split_drops_lines_under_min_and_reallocates():  # gap etf 60 (< 100) → 0, its 60 goes to the other bucket
def test_traspaso_only_between_funds_and_not_from_managed(): ...
def test_traspaso_amount_and_gain(): ...
def test_build_candidates_collapses_managed_portfolio(tmp_path):  # Review Focus 5 (part): two funds in "Example Managed" → one candidate
def test_build_candidates_marks_overlap_and_blocked(tmp_path): ...
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the file** → PASS; flake8.
- [ ] **Step 5: Commit** — `feat: allocator candidates, target checks and bucket split`.

---

### Task 5: Ranking, the proposal and its endpoint

**Files:**
- Modify: `portf_manager/services/allocator.py`
- Modify: `portf_server/routers/allocate.py` (`GET /next-euro?amount=`)
- Test: `tests/unit/test_allocator.py` (extend), `tests/unit/test_allocate_api.py` (extend)

**Interfaces:**
- `rank_fund_candidates(cands, by_region) -> list[(cand, reasons)]`, `rank_stock_candidates(cands, total_after, max_single_stock_pct) -> (ranked, needs_research, excluded)`, `rank_crypto(cands)`, `rank_bonds(cands)`: spec §5.3; reasons are the exact strings listed in the spec where it gives them ("overlaps with X, which is the bigger holding", "TER unknown, enter it on the Fees tab", "no researched candidate under the price target") and short sentences otherwise.
- `pick_lines(bucket, cands, amount_eur, settings, ctx) -> (lines, unallocated_eur, needs_research)`; ≤ `max_lines_per_bucket`, split amount equally across the picked lines, each line `{asset_type, asset_id, symbol, name, portfolio_id, portfolio_name, amount_eur, reasons, runner_up}`.
- `chat_summary(result: dict) -> str` (plain text: deployable breakdown, buckets, lines with reasons, traspasos, blocked, needs research, warnings; used by the page's copy button and the MCP tool).
- `next_euro(db, amount_eur: float | None = None, today=None, fx=None, fx_on=None) -> dict` = spec §5.6 response (`status` ∈ ok | nothing_to_deploy | targets_incomplete | insufficient_data).

- [ ] **Step 1: Write the failing tests**

```python
def test_fund_rank_prefers_lowest_ter_outside_overlap(): ...
def test_fund_without_ter_ranks_last_with_hint(): ...
def test_stock_rank_price_under_target_first_then_recommendation(): ...
def test_stock_over_cap_excluded_with_reason():  # weight after buy > 5 %
def test_blocked_stock_never_proposed(): ...
def test_unresearched_stock_goes_to_needs_research_and_unallocated(): ...
def test_crypto_split_by_weights(): ...
def test_next_euro_amount_above_deployable_warns(tmp_path):  # Review Focus 5
def test_next_euro_statuses(tmp_path):  # nothing_to_deploy, targets_incomplete, insufficient_data
def test_chat_summary_mentions_every_line(): ...
def test_next_euro_endpoint(tmp_path): ...
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run both files** → PASS; flake8.
- [ ] **Step 5: Commit** — `feat: next-euro proposal with reasons`.

---

### Task 6: Action Items

**Files:**
- Modify: `portf_manager/services/action_items.py` (register after `check_retirement_readiness`)
- Test: `tests/unit/test_action_items.py`

**Interfaces:** `check_allocation(db, today=None) -> list[dict]`: category `allocate`, `link_page "rebalance"` (or the page that holds the rebalance card); items `idle_cash` (`low`, title "Idle cash: N months of spending above the buffer ceiling"), `buffer_low` (`medium`, title "Cash buffer below N months"), `allocation_targets_incomplete` (`low`, naming the asset types); nothing on `insufficient_data`.

- [ ] **Step 1: Tests** — monkeypatch `portf_manager.services.deployable.deployable` / `allocator.check_targets` inputs: idle → one low; rebuild → one medium; targets missing → one low; ok → none; registered in `checks_for_tests()`.
- [ ] **Step 2–5:** fail, implement, pass, commit — `feat: action items for idle cash, low buffer and incomplete targets`.

---

### Task 7: Web client

**Files:**
- Modify: `web_client/index.html` (dashboard attention slot `#dashNextEuro` beside `#dashActionItems`; a "Next euro" card `#nextEuroCard` next to the rebalance card (`#rebalanceBody`'s parent), with the deployable breakdown, buffer gauge, amount input `#neAmount`, bucket table, lines, traspasos, blocked, needs-research, "Copy for chat" button, settings form `#neSettingsForm` with the merchant checkboxes)
- Modify: `web_client/js/pfm_core.js` (`apiClient.getDeployable()`, `getNextEuro(amount)`, `getAllocatorSettings()`, `updateAllocatorSettings(body)`)
- Modify: `web_client/js/pfm_features.js` (pure `renderNextEuroLines(result) -> string`, `renderDeployableSummary(dep) -> string`, `bufferGaugeHtml(buffer) -> string`, all `window.`-exported; `loadNextEuroCard()` called from the rebalance page loader; `loadDashboardNextEuro()` in `pfm_pages.js` dashboard load, using `dashAttentionSet('dashNextEuro', part)`)
- Modify: `web_client/js/help_text.js` (`ne_deployable`, `ne_buffer`, `ne_tax_reserve`, `ne_reasons`)
- Test: `web_client/js/tests/next_euro.test.mjs`

**Interfaces:** every amount via `Fmt.money`; every dynamic string escaped; `needs_research`, `blocked` and `warnings` rendered as lists; a `status` other than `ok` renders its reason and hides the lines; the copy button writes `chat_summary` to the clipboard with `notify`.

- [ ] **Step 1: JS tests** — summary shows the three parts and status badge; gauge marks min/target/max; lines render reasons and runner-up; a `<b>` in a reason is escaped; `targets_incomplete` renders the reason and no lines.
- [ ] **Step 2–5:** fail, implement, `make test-js` + e2e smoke, commit — `feat(web): next-euro card and dashboard summary`.

---

### Task 8: MCP tool, docs, status

**Files:**
- Modify: `mcp/server.py` (`next_euro(amount: Optional[float] = None) -> str` printing `chat_summary` from `GET /api/v1/allocate/next-euro`; a non-ok status is one line with the reason; `n/a` for missing fields), tests in `mcp/test_server_retirement.py` (rename to `mcp/test_server_local.py` holding both features' tests)
- Create: `docs/features/allocator.md` (deployable formula, tax-reserve rules, settings, proposal rules, response shapes, UI, MCP, known limits) + Feature reference row in `CLAUDE.md` with the gotchas "the reserve reads only ticked work merchants" and "traspasos are planned before the new-money split"
- Modify: `docs/features/action-items.md`, `docs/features/web-client.md`, `PROJECT_STATUS.md` (v2.5.82)
- Post-merge (outside this repo): register `next_euro` in `~/mcp/remote_gateway/server.py` (+ `TOOL_SPECIALIST`), the finance adapter and persona ("read `next_euro` before advising on new money"), move the MCP tests into `~/mcp/pfm/test_server.py`.

- [ ] **Step 1: MCP tests** — `test_next_euro_prints_lines_and_reasons`, `test_next_euro_non_ok_is_one_line`, `test_next_euro_missing_fields_are_na`.
- [ ] **Step 2–3:** implement, docs.
- [ ] **Step 4:** full unit suite, integration suite, `make test-js`, flake8 → all green; exact totals in the report.
- [ ] **Step 5: Commit** — `feat(mcp): next_euro tool; docs for the allocator`.
