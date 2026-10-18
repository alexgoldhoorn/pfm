# Balance Sheet and Retirement Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Net Worth page into a sourced balance sheet (every line with a source, an as-of date and a stale flag) and add a deterministic, real-terms retirement readiness projection with verdicts at 65 and 67.

**Architecture:** Pure service modules in `portf_manager/services/` (`loans`, `cash_ledger`, `balance_sheet`, `indexa`, `retirement`) do all the maths over plain dicts; thin FastAPI routers (`networth.py`, new `retirement.py`) expose them; the existing Net Worth page is reorganised in the vanilla-JS client; two new Action Item checks and two MCP tools read the same endpoints. Schema goes to v35.

**Tech Stack:** Python 3.13, FastAPI, SQLite (`portf_manager/database.py`), pytest, vanilla JS + Bootstrap 5.3 with Node's built-in test runner for pure helpers.

**Spec:** `docs/superpowers/specs/2026-10-18-balance-sheet-retirement-design.md`

## Global Constraints

- Work only in the worktree `~/repos/pfm-balance-sheet` (branch `feat/balance-sheet-retirement`); never edit `~/repos/pfm`.
- Tooling: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest …`, `… uv run black <file>`, `… uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501` (must stay at 0 warnings). JS: `make test-js`.
- `DATABASE_VERSION = 35`; new tables in BOTH `_create_all_tables` and `_migrate_to_v35`; every `== 34` assertion in `tests/test_database.py` becomes `== 35`.
- Blocking-I/O endpoints are plain `def`. Tests cannot reach the network; the Indexa client is mocked at `portf_manager.services.indexa._get`.
- Public repo: tests, docs and fixtures use invented round figures and names (`Example Broker`, `Example Bank`); no real amounts, ISINs, account references or home paths.
- Freshness limits (days): positions 4, account valuation 7, bank 45, broker cash 60, manual `cash`/`savings_account`/`current_account` 90, manual `property` 365, other manual 180; deposits and loans never stale.
- Plan defaults: `retirement_age 65`, `statutory_age 67`, `horizon_age 95`, `real_return_pct 3.0`, `pessimistic_real_return_pct 1.0`, `cash_real_return_pct -2.0`, `withdrawal_tax_pct 0.0`, `spending_months 12`, `spending_includes_loan_payments true`, `excluded_categories ["Income Tax", "Social Security", "Invest", "House purchase"]`, `ASSUMED_INFLATION_PCT 2` for non-indexed incomes.
- Verdicts: `on_track` = pot ≥ 0 at `horizon_age`; `short` = depletes at or after `retirement_age + 10`; `at_risk` otherwise.
- Commit after every task with a conventional-commit message ending in `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Docs are part of the last task, not optional: `docs/features/networth-portfolios.md`, `CLAUDE.md` (v35 line + two gotchas), `PROJECT_STATUS.md`.

## Review Focus

1. A bank account whose latest statement has no `balance` row must appear as a stale line with `amount_eur: null` and be left out of every total, never counted as 0 (test in Task 4).
2. A loan whose `term_months` have all elapsed must report balance 0 and no payments in later years, and the mortgage subtraction in the projection must stop adding negative need (tests in Tasks 2 and 6).
3. A `retirement_incomes` row with `owner = 'partner'` and no `partner_birth_date` must be ignored with a warning, not crash or be treated as the owner's (test in Task 6).
4. A fresh account valuation must replace, not add to, the portfolio's positions and cash lines; an Indexa fetch error must leave the previous value and `as_of` untouched (tests in Tasks 4 and 5).
5. Fewer than 6 complete imported months must yield `status: "insufficient_data"` and no scenarios, even when every other input is present (test in Task 6).

---

### Task 1: Schema v35 and database helpers

**Files:**
- Modify: `portf_manager/database.py` (`DATABASE_VERSION`, `_create_all_tables`, migration chain near line 842, new `_migrate_to_v35` after `_migrate_to_v34`, CRUD helpers next to `get_manual_assets`)
- Modify: `tests/test_database.py` (every `== 34` → `== 35`)
- Test: `tests/unit/test_schema_v35.py`

**Interfaces:**
- Produces, all on `Database`:
  - `get_manual_assets()` rows gain `as_of: str | None`; `create_manual_asset(..., as_of: str | None = None)` stores today's date when None; `update_manual_asset(id, **fields)` accepts `as_of`.
  - `create_loan(name, principal, annual_rate_pct, start_date, term_months, currency="EUR", lender=None, rate_as_of=None, balance_as_of=None, extra_payment_eur=0.0, notes=None) -> int`, `get_loans() -> list[dict]`, `get_loan(id) -> dict | None`, `update_loan(id, **fields) -> bool`, `delete_loan(id) -> bool`.
  - `upsert_account_valuation(portfolio_id, provider, account_ref, value, cash, currency, as_of, fetched_at, last_error=None) -> None`, `get_account_valuations() -> list[dict]`, `get_account_valuation(portfolio_id) -> dict | None`, `delete_account_valuation(portfolio_id) -> bool`.
  - `create_retirement_income(name, owner, monthly_eur, from_age, to_age=None, indexed=1, source=None, as_of=None, notes=None) -> int`, `get_retirement_incomes() -> list[dict]`, `update_retirement_income(id, **fields) -> bool`, `delete_retirement_income(id) -> bool`.
- Tables exactly as spec §5.1, §5.2, §5.4, plus `loans.balance_as_of REAL` (the balance on `rate_as_of`, so a rate change can be amortised forward without the old rate; builder's resolution of spec §5.1).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_schema_v35.py
def test_fresh_db_is_v35_and_has_tables(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    with db.get_connection() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(manual_assets)")}
    assert {"loans", "account_valuations", "retirement_incomes"} <= names
    assert "as_of" in cols

def test_migration_backfills_as_of_from_updated_at(tmp_path):
    # build a v34 db by hand: create via Database, drop the column with a
    # table rebuild, set version 34, reopen → as_of == updated_at[:10]
    ...

def test_manual_asset_as_of_defaults_to_today(tmp_path): ...
def test_loan_crud_roundtrip(tmp_path):
    mid = db.create_loan("Example Mortgage", 100000, 3.0, "2026-01-01", 240)
    assert db.get_loan(mid)["extra_payment_eur"] == 0.0
    assert db.update_loan(mid, annual_rate_pct=3.5, rate_as_of="2027-01-01", balance_as_of=96000)
    assert db.delete_loan(mid) and db.get_loans() == []
def test_account_valuation_upsert_replaces_row(tmp_path): ...   # second upsert → one row, new value
def test_retirement_income_owner_is_checked(tmp_path):
    with pytest.raises(Exception): db.create_retirement_income("x", "dog", 100, 67)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_schema_v35.py -v`
Expected: FAIL (no such table / attribute errors).

- [ ] **Step 3: Implement v35**

Bump `DATABASE_VERSION`, add the three `CREATE TABLE` statements and the `as_of TEXT` column to `_create_all_tables`, add `if current_version < 35: self._migrate_to_v35(conn)` to the chain, write `_migrate_to_v35` with a guarded `ALTER TABLE manual_assets ADD COLUMN as_of TEXT`, `UPDATE manual_assets SET as_of = substr(updated_at, 1, 10) WHERE as_of IS NULL`, and `CREATE TABLE IF NOT EXISTS` for the rest. Add the helpers listed in Interfaces following the `manual_assets` helpers' style (dict rows, `**fields` updates restricted to known columns). Update the `== 34` assertions.

- [ ] **Step 4: Run the schema tests and the whole DB suite**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_schema_v35.py tests/test_database.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/database.py tests/test_database.py tests/unit/test_schema_v35.py
git commit -m "feat: schema v35 — manual_assets.as_of, loans, account_valuations, retirement_incomes"
```

---

### Task 2: Loan amortisation service

**Files:**
- Create: `portf_manager/services/loans.py`
- Test: `tests/unit/test_loans.py`

**Interfaces:**
- `monthly_payment(principal: float, annual_rate_pct: float, term_months: int) -> float` (annuity formula; rate 0 → principal / term).
- `balance_on(loan: dict, day: date) -> float`: balance after the whole months elapsed from the anchor; anchor is (`start_date`, `principal`) or, when `balance_as_of` and `rate_as_of` are set, (`rate_as_of`, `balance_as_of`) over the remaining term `term_months − months_between(start_date, rate_as_of)`. Before `start_date` → `principal`. After the end → 0. `extra_payment_eur` is added to each payment and shortens the schedule.
- `schedule(loan: dict) -> list[dict]`: one entry per calendar year `{year, balance_end_eur, interest_paid_eur, payments_eur}` from the anchor year to the final payment year.
- `end_date(loan: dict) -> date`: date of the final payment.
- `payments_in_year(loan: dict, year: int) -> float`: scheduled payments (incl. extra) falling in that calendar year; 0 after the end.

- [ ] **Step 1: Write the failing tests** (round numbers; check against a hand calculation)

```python
def test_monthly_payment_zero_rate(): assert monthly_payment(12000, 0, 12) == 1000
def test_monthly_payment_known_value():
    assert round(monthly_payment(100000, 3.0, 240), 2) == 554.60
def test_balance_before_start_is_principal(): ...
def test_balance_after_end_is_zero(): loan 12000 @0% 12 months from 2026-01-01 → balance_on(date(2027, 3, 1)) == 0
def test_balance_after_six_payments_zero_rate(): == 6000 on date(2026, 7, 15)
def test_rate_change_anchors_on_balance_as_of(): balance_as_of 96000 at 2027-01-01, rate 3.5, remaining 228 → balance_on(2027-01-01) == 96000
def test_extra_payment_shortens_schedule(): 12000 @0% 12 months, extra 1000 → end_date month 6
def test_payments_in_year_after_end_is_zero(): ...
def test_schedule_last_year_ends_at_zero(): ...
```

- [ ] **Step 2: Run to verify they fail** — `… pytest tests/unit/test_loans.py -v` → ImportError.
- [ ] **Step 3: Implement `loans.py`** with the formulas from spec §5.1 (payment = P·r/(1−(1+r)^−n); balance after k payments = P(1+r)^k − pay·((1+r)^k−1)/r), rounding to cents, final payment clears any residual.
- [ ] **Step 4: Run the tests** — all PASS.
- [ ] **Step 5: Commit** — `feat: loan amortisation service`.

---

### Task 3: Broker cash ledger service

**Files:**
- Create: `portf_manager/services/cash_ledger.py`
- Modify: `portf_server/routers/portfolios.py` (the `cash_by_pid` block, around lines 285-310, replaced by a call)
- Test: `tests/unit/test_cash_ledger.py`

**Interfaces:**
- `cash_by_portfolio(db, fx_on: Callable[[str, Any], float]) -> dict[int, dict]` → `{portfolio_id: {"cash_eur": float, "as_of": "YYYY-MM-DD" | None}}`, where `fx_on(currency, on_date)` is the caller's transaction-date FX function (the router passes `lambda c, d: _get_fx_rate_on(database, c, d)`). Same arithmetic as today's router block (buy −, sell/dividend/interest +, Deposit +, Withdrawal −), **skipping transactions whose asset has `asset_type == 'cash'`**, `as_of` = newest transaction/booking date for that portfolio.

- [ ] **Step 1: Write the failing tests**

```python
def test_cash_from_deposit_and_buy(tmp_path):  # deposit 1000, buy 400 → 600, as_of == buy date
def test_cash_type_asset_transactions_are_ignored(tmp_path):  # a buy on an asset_type='cash' asset does not move cash
def test_portfolio_without_activity_is_absent(tmp_path):
def test_portfolios_values_endpoint_unchanged(tmp_path):  # GET /api/v1/portfolios/values cash_eur equals the service figure
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement the service and make the router call it** (no behaviour change for the endpoint except the cash-asset exclusion, which the spec requires).
- [ ] **Step 4: Run `tests/unit/test_cash_ledger.py` and `tests/unit/test_portfolio*.py`** — PASS.
- [ ] **Step 5: Commit** — `refactor: broker cash ledger as a service, cash-type assets excluded`.

---

### Task 4: Balance sheet builder and endpoint

**Files:**
- Create: `portf_manager/services/balance_sheet.py`
- Modify: `portf_server/routers/networth.py` (`net_worth_eur`, `get_networth`, new `GET /balance-sheet`, `ManualAssetBody`/`ManualAssetUpdate` gain `as_of`)
- Modify: `portf_server/routers/analytics.py` (`_cash_eur` → balance-sheet `liquid_eur`)
- Test: `tests/unit/test_balance_sheet.py`

**Interfaces:**
- Consumes: Task 1 helpers; Task 2 `balance_on`, `monthly_payment`, `end_date`; Task 3 `cash_by_portfolio`.
- Produces: `build_balance_sheet(db, today: date | None = None, fx=None, fx_on=None) -> dict` with the exact shape of spec §4.1 (`as_of`, `totals{assets_eur, liabilities_eur, net_worth_eur, financial_assets_eur, liquid_eur, stale_lines}`, `groups[]` in the fixed order cash/deposits/investments/property/other_assets/liabilities, each line with `id, label, group, amount_eur, amount, currency, source, as_of, age_days, max_age_days, stale, note, edit`). `fx`/`fx_on` default to the portfolios-router functions, imported lazily.
- Line ids: `positions:{pid}`, `cash:{pid}`, `valuation:{pid}`, `bank:{pid}`, `deposit:{id}`, `loan:{id}`, `manual:{id}`. Sources: `positions`, `cash_ledger`, `account_valuation`, `bank_statement`, `fixed_deposit`, `loan_schedule`, `manual`.
- Manual category → group: `cash`, `current_account`, `savings_account` → `cash`; `property` → `property`; `pension`, `investment_external` → `investments`; everything else → `other_assets`; any `is_liability` → `liabilities`. `financial_assets_eur` = cash + deposits + investments groups. `liquid_eur` = cash group + deposits maturing within 365 days.
- `net_worth_eur(db)` in `networth.py` returns `build_balance_sheet(db)["totals"]["net_worth_eur"]`; `GET /networth/` keeps its response keys and derives them from the sheet (`brokerage_eur` = investments group minus manual investment lines, `bank_accounts_eur`, `manual_assets_eur`, `manual_liabilities_eur`, `deposits_eur`, `items`, `deposits`, `bank_accounts`).

- [ ] **Step 1: Write the failing tests** (one `Database` per test under `tmp_path`, `fx=lambda c: 1.0`, `fx_on=lambda c, d: 1.0`, `today=date(2026, 10, 18)`)

```python
def test_groups_are_in_fixed_order_and_totals_add_up(): ...
def test_positions_line_stale_after_four_days():  # price_date 2026-10-13 → stale, age_days 5
def test_positions_line_notes_unpriced_assets():  # note "1 asset unpriced", stale True
def test_bank_without_balance_is_stale_and_excluded():  # amount_eur None, not in totals (Review Focus 1)
def test_bank_balance_stale_after_45_days(): ...
def test_fresh_valuation_replaces_positions_and_cash_lines():  # valuation as_of today → ids {valuation:pid} only (Review Focus 4)
def test_stale_valuation_keeps_positions_line_with_note():  # as_of 10 days old → positions line, note mentions "10 days old"
def test_loan_line_balance_from_schedule_and_note():  # note contains monthly payment and end date
def test_manual_property_stale_after_365_days(): ...
def test_manual_cash_stale_after_90_days(): ...
def test_manual_line_named_like_a_portfolio_gets_duplicate_note(): ...
def test_deposit_past_maturity_gets_note(): ...
def test_financial_assets_exclude_property_and_other(): ...
def test_liquid_eur_counts_deposits_maturing_within_a_year(): ...
def test_networth_endpoint_total_equals_balance_sheet(tmp_path):  # via TestClient, same pattern as tests/unit/test_networth.py
def test_balance_sheet_endpoint_shape(tmp_path): ...
def test_manual_asset_post_accepts_as_of(tmp_path): ...
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement `balance_sheet.py`**: positions per portfolio via `compute_positions(txs, key=lambda tx: (tx["portfolio_id"], tx["asset_id"]))` × latest price × `fx`; freshness via `age_days = (today − as_of).days`, `stale = max_age_days is not None and age_days > max_age_days`; valuation replacement when `age_days <= 7`; loans via Task 2; manual lines with the mapping above; duplicate note by case-insensitive name match against `db.get_all_portfolios()`.
- [ ] **Step 4: Rewire `networth.py` and `analytics._cash_eur`** (`_cash_eur` returns the sheet's `liquid_eur`, or None when the cash group is empty and no bank account has a balance). Add `as_of` to the manual-asset bodies.
- [ ] **Step 5: Run `tests/unit/test_balance_sheet.py tests/unit/test_networth.py tests/unit/test_action_items.py tests/unit/test_progress_metrics.py tests/unit/test_watchlist_goals_risk.py`** — PASS.
- [ ] **Step 6: Commit** — `feat: balance sheet with sourced, dated lines; net worth derived from it`.

---

### Task 5: Loans, account valuations (Indexa) and retirement incomes endpoints

**Files:**
- Create: `portf_manager/services/indexa.py`
- Create: `portf_server/routers/retirement.py` (plan + incomes now; readiness in Task 6)
- Modify: `portf_server/routers/networth.py` (loans + valuations routes)
- Modify: `portf_server/app.py` (include `retirement.router` at `/api/v1/retirement`, next to `networth` at line ~424)
- Modify: `portf_server/settings.py` (`indexa_token: Optional[str]` → env `PORTF_INDEXA_TOKEN`, beside `openrouter_api_key`)
- Modify: `portf_manager/services/price_updater.py` (`run_price_update` calls `refresh_account_valuations` after the price loop; result gains `valuations_refreshed: int, valuation_errors: list[str]`)
- Test: `tests/unit/test_indexa.py`, `tests/unit/test_loans_api.py`, `tests/unit/test_retirement_api.py`

**Interfaces:**
- `indexa._get(path: str, token: str) -> dict` (urllib, header `X-AUTH-TOKEN`, 15 s timeout) — the one network seam, mocked in tests.
- `indexa.fetch_portfolio(account_ref: str, token: str) -> dict` → `{"value": float, "cash": float, "as_of": "YYYY-MM-DD"}` from `response["portfolio"]["total_amount"]`, `["cash_amount"]`, and the response's date field if present else today.
- `indexa.refresh_account_valuations(db, token: str | None, today=None) -> list[dict]`: for each `provider == "indexa"` row calls `fetch_portfolio`, upserts value/cash/as_of/fetched_at with `last_error=None`; on any exception upserts only `fetched_at` and `last_error=str(e)` keeping value and `as_of`; returns `[{"portfolio_id", "ok": bool, "error": str | None}]`; with `token` None returns `[]` without calling.
- Routes (all `def`, `_auth` dependency):
  - `GET|POST /api/v1/networth/loans`, `PUT|DELETE /api/v1/networth/loans/{id}`, `GET /api/v1/networth/loans/{id}/schedule` (Task 2 `schedule`). Body: `LoanBody(name, principal, annual_rate_pct, start_date, term_months, currency="EUR", lender=None, rate_as_of=None, balance_as_of=None, extra_payment_eur=0.0, notes=None)`; GET rows add `balance_eur`, `monthly_payment_eur`, `end_date`.
  - `GET /api/v1/networth/valuations`, `POST /api/v1/networth/valuations {portfolio_id, provider, account_ref}` (creates with value 0, `as_of` `1970-01-01`, then one refresh attempt; 404 on unknown portfolio), `DELETE /valuations/{portfolio_id}`, `POST /api/v1/networth/valuations/refresh` → the refresh result list.
  - `GET|PUT /api/v1/retirement/plan` (JSON in `app_settings` key `retirement_plan`, merged over the defaults in Global Constraints; PUT validates ages `0 < retirement_age ≤ statutory_age < horizon_age ≤ 120`, returns the merged plan).
  - `GET|POST /api/v1/retirement/incomes`, `PUT|DELETE /api/v1/retirement/incomes/{id}`; `IncomeBody(name, owner: Literal["self","partner"], monthly_eur, from_age, to_age=None, indexed=True, source=None, as_of=None, notes=None)`; 422 when `to_age <= from_age`.

- [ ] **Step 1: Write the failing tests**

```python
# test_indexa.py — monkeypatch portf_manager.services.indexa._get
def test_fetch_portfolio_reads_total_and_cash(): _get → {"portfolio": {"total_amount": 10000, "cash_amount": 400}} → value 10000, cash 400
def test_refresh_keeps_previous_value_on_error(tmp_path):  # row value 9000 as_of 2026-10-01; _get raises → value 9000, as_of unchanged, last_error set (Review Focus 4)
def test_refresh_without_token_does_nothing(): ...
def test_run_price_update_reports_valuations(tmp_path, monkeypatch):  # api client + _get mocked → result["valuations_refreshed"] == 1
# test_loans_api.py
def test_loan_roundtrip_and_balance(tmp_path): POST → GET lists balance_eur, monthly_payment_eur, end_date; schedule endpoint returns yearly rows
def test_loan_put_rate_change(tmp_path): ...
# test_retirement_api.py
def test_plan_defaults(tmp_path): GET → retirement_age 65, horizon_age 95, excluded_categories == [...]
def test_plan_put_rejects_bad_ages(tmp_path): horizon 60 with retirement 65 → 422
def test_income_crud_and_validation(tmp_path): ...
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** the service, settings field, the routes, the app include and the price-updater hook (`settings.indexa_token` read lazily; the hook is wrapped in `try/except` so a valuation failure never fails the price run).
- [ ] **Step 4: Run the three new test files plus `tests/unit/test_price_updater.py`** — PASS; run flake8.
- [ ] **Step 5: Commit** — `feat: loans, Indexa account valuations and retirement plan/income endpoints`.

---

### Task 6: Retirement readiness projection and endpoint

**Files:**
- Create: `portf_manager/services/retirement.py`
- Modify: `portf_manager/services/progress.py` (`savings_and_buffer(rows, cash_eur, today=None, months=12, exclude: frozenset[str] = frozenset())`; a row whose `category` is in `exclude` is skipped; rows may now carry `category`)
- Modify: `portf_server/routers/analytics.py` (the `rows` list for `get_progress` adds `"category": r.get("category")`)
- Modify: `portf_server/routers/retirement.py` (`GET /api/v1/retirement/readiness`)
- Test: `tests/unit/test_retirement.py`, `tests/unit/test_retirement_api.py` (extend)

**Interfaces:**
- `expand_categories(tree: list[dict], names: list[str]) -> frozenset[str]`: the names plus every descendant in `db.list_spending_categories_tree()` rows (`name`, `parent_name`).
- `spending_inputs(db, plan: dict, today: date, fx) -> dict` → `{"target_monthly_eur", "months_used": [...], "breakdown": {category: monthly_eur}, "excluded": [...], "savings_now_yearly_eur", "income_monthly_eur"}`; target via `savings_and_buffer(..., exclude=expand_categories(tree, plan["excluded_categories"]))`, savings via a second call with `exclude=expand_categories(tree, ["Invest", "House purchase"])` and `max(income − spent, 0)` scaled to a year.
- `project(plan, retirement_age: int, real_return_pct: float, pot: dict, loans: list[dict], incomes: list[dict], spending: dict, today: date) -> dict` → one scenario of spec §6.4 (`retirement_age, case, verdict, lasts_until_age, pot_at_retirement_eur, pot_at_horizon_eur, path[]`), `pot = {"investments_eur", "cash_eur", "deposits": [{principal, rate_pct, maturity_date}]}`. Year loop per spec §6.3; first step pro-rated by `(13 − today.month) / 12`; cash drawn first down to 6 × target, then investments; depletion → `lasts_until_age` = that age, pot 0 thereafter.
- `sustainable_monthly(...) -> float` and `extra_saving_needed_monthly(...) -> float`: bisection on `project` (50 iterations or €1 tolerance).
- `readiness(db, today=None, fx=None) -> dict`: the full response of spec §6.4 (`status` ∈ `ok | needs_setup | insufficient_data`; four scenarios: ages `[retirement_age, statutory_age]` × cases `[base, pessimistic]`; `warnings` as listed in §6.4). Reads the pot from Task 4's sheet (`investments` group minus valuation cash, which goes to cash), loans from Task 1, incomes from Task 1, plan from Task 5.

- [ ] **Step 1: Write the failing tests** (pure-function tests pass dicts; no DB except the endpoint tests)

```python
def test_pot_lasts_exactly_ten_years_with_zero_returns():
    # retire now at 65, horizon 95, pot cash 120000, target 1000/month, no pensions, returns 0 → lasts_until_age 75, verdict at_risk
def test_pension_covering_spending_never_depletes():  # pension 1000/month from 65, target 1000 → on_track, pot unchanged
def test_short_verdict_between_ten_years_and_horizon():  # depletes at 80 → short
def test_loan_payment_drops_out_after_loan_ends():  # includes_loan_payments True, loan ends at 70 paying 500/month → need falls by 6000/yr from 70 (Review Focus 2)
def test_loan_added_when_spending_excludes_payments(): ...
def test_non_indexed_income_decays_two_percent_a_year(): ...
def test_partner_income_without_birth_date_is_ignored_with_warning():  # (Review Focus 3)
def test_savings_continue_until_retirement_then_stop(): ...
def test_cash_is_drawn_before_investments_down_to_six_months(): ...
def test_sustainable_monthly_matches_target_when_exactly_funded(): ...
def test_extra_saving_needed_is_zero_when_on_track(): ...
def test_expand_categories_includes_descendants():  # tree with Taxes → Income Tax → excluded set has both
def test_readiness_insufficient_data_under_six_months(tmp_path):  # 5 imported months → status insufficient_data, no scenarios (Review Focus 5)
def test_readiness_needs_setup_without_birth_date(tmp_path): ...
def test_readiness_endpoint_four_scenarios_and_warnings(tmp_path):  # birth date set, 12 months of rows, no incomes → 4 scenarios, warning about no pension
def test_progress_savings_unchanged_without_exclude(): ...
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement `retirement.py`**, the `exclude` parameter, the analytics row change and the endpoint (`def`, FX blocking).
- [ ] **Step 4: Run `tests/unit/test_retirement.py tests/unit/test_retirement_api.py tests/unit/test_progress_metrics.py`** — PASS; flake8 clean.
- [ ] **Step 5: Commit** — `feat: retirement readiness projection in real terms`.

---

### Task 7: Action Item checks

**Files:**
- Modify: `portf_manager/services/action_items.py` (two checks, registered in `_CHECKS` after `check_goals_off_track`)
- Test: `tests/unit/test_action_items.py` (extend)

**Interfaces:**
- `check_balance_sheet_freshness(db, today=None) -> list[dict]`: one item per stale line, `id = f"balance_sheet_stale:{line_id}"`, `category = "networth"`, severity `medium` for sources `positions`, `cash_ledger`, `account_valuation`, `bank_statement`, `low` for `manual`; title `"<label> is N days old"` (or "has no balance"), detail = the line's note or the source and limit, `link_page = "networth"`. Also one `low` item `balance_sheet_duplicate:{line_id}` per duplicate-named manual line.
- `check_retirement_readiness(db) -> list[dict]`: `id = "retirement_readiness"`, severity `high` for `at_risk`, `medium` for `short`, `low` for `needs_setup`; nothing for `on_track` or `insufficient_data` (the stale-imports check already covers missing data); `link_page = "networth"`.

- [ ] **Step 1: Write the failing tests** — stale bank line → one medium item; fresh sheet → none; readiness `short` (monkeypatch `portf_manager.services.retirement.readiness`) → one medium; both checks in `checks_for_tests()`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement and register.**
- [ ] **Step 4: Run `tests/unit/test_action_items.py`** — PASS.
- [ ] **Step 5: Commit** — `feat: action items for stale balance-sheet lines and retirement readiness`.

---

### Task 8: Net Worth page — balance sheet and manage section

**Files:**
- Modify: `web_client/index.html` (Net Worth page markup near line 3298: summary cards replaced by `<div id="nwBalanceSheet">`; manage section gains an "as of" date input `nwAsOf` on the manual form, a loans card with form `nwLoanForm` + table `nwLoansBody`, a valuations card with form `nwValuationForm` + table `nwValuationsBody` and a "Refresh" button `nwValuationsRefresh`)
- Modify: `web_client/js/pfm_core.js` (`apiClient`: `getBalanceSheet()`, `getLoans()`, `createLoan(body)`, `updateLoan(id, body)`, `deleteLoan(id)`, `getLoanSchedule(id)`, `getValuations()`, `createValuation(body)`, `deleteValuation(pid)`, `refreshValuations()`; `createManualAsset`/`updateManualAsset` pass `as_of`)
- Modify: `web_client/js/pfm_analytics.js` (`loadNetworthPage` renders the sheet; new pure `renderBalanceSheetRows(sheet) -> string`, `window.`-exported; `_wireLoanForm`, `_wireValuationForm`, `_renderLoans`, `_renderValuations`)
- Modify: `web_client/js/help_text.js` (`METRIC_HELP` entries `nw_source`, `nw_stale`, `nw_financial_assets`)
- Test: `web_client/js/tests/balance_sheet.test.mjs`

**Interfaces:**
- `renderBalanceSheetRows(sheet)` returns `<tbody>` HTML: a group header row per group with subtotal, one row per line (label + source badge + "as of <date>" small text, amount, stale rows with class `table-warning` and the note in a `title` attribute, an edit control per `edit.kind`: `manual` → the existing amount editor, `loan` → opens the loan form prefilled, `valuation` → refresh button, `bank` → link to the Import page, `positions` → link to Portfolios), and a totals footer with assets, liabilities, net worth and "Financial assets (excl. property)". All dynamic text through `esc`/`escapeForAttr`; money via `Fmt.money`.
- At ≤ 576 px the source and as-of cells collapse under the label (`d-none d-md-table-cell` on the two columns and a `d-md-none` line in the label cell).

- [ ] **Step 1: Write the failing JS tests**

```js
test('renders group subtotals and totals footer', ...)   // sheet fixture with 2 groups → contains 'Financial assets'
test('stale line gets table-warning and its note', ...)   // 'table-warning' and the note text in title
test('bank line without amount shows n/a not 0', ...)
test('escapes labels', ...)                                // label '<b>' rendered as '&lt;b&gt;'
```

- [ ] **Step 2: Run `make test-js`** — the new file fails (function undefined).
- [ ] **Step 3: Implement the markup, API client methods, renderer, loan/valuation forms and help entries.** Keep `computeNetWorthChecklist` as is (it still receives `d.items`, `d.deposits`, `d.bank_accounts` from `GET /networth/`).
- [ ] **Step 4: Run `make test-js`** — PASS. Redeploy the web image (`docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`) from the worktree only if the executor is told to; otherwise note the redeploy as pending.
- [ ] **Step 5: Commit** — `feat(web): balance sheet on the Net Worth page, loans and valuations editors`.

---

### Task 9: Net Worth page — retirement card, plan form, incomes; Goals link

**Files:**
- Modify: `web_client/index.html` (a `#nwRetirement` card between the sheet and the manage section: verdict columns for 65 and 67, inputs summary, `<svg id="nwRetirementChart">`, a gear button opening `#nwPlanForm`, an incomes table `#nwIncomesBody` with form `#nwIncomeForm`; empty-state blocks for `needs_setup` and `insufficient_data`)
- Modify: `web_client/js/pfm_core.js` (`getRetirementPlan()`, `updateRetirementPlan(body)`, `getRetirementIncomes()`, `createRetirementIncome(body)`, `updateRetirementIncome(id, body)`, `deleteRetirementIncome(id)`, `getRetirementReadiness()`)
- Modify: `web_client/js/pfm_analytics.js` (`loadRetirementCard()` called from `loadNetworthPage`; pure `retirementVerdictHtml(readiness) -> string` and `retirementChartSeries(scenario) -> {ages, investments, cash, loans}` `window.`-exported; chart via the shared SVG helpers with `SVG_GRID`/`SVG_AXIS`/`SVG_LABEL`)
- Modify: `web_client/js/pfm_features.js` (`loadGoals` around line 86: for goals whose name matches `/retire/i`, hide the progress bar and render `Readiness: <verdict>, money lasts until <age>` with a link to the Net Worth page, reading `getRetirementReadiness()` once)
- Modify: `web_client/js/help_text.js` (`METRIC_HELP`: `rt_verdict`, `rt_sustainable`, `rt_extra_saving`, `rt_before_tax`)
- Test: `web_client/js/tests/retirement.test.mjs`

**Interfaces:**
- `retirementVerdictHtml(readiness)`: `status` `needs_setup` → the setup copy; `insufficient_data` → the reason; `ok` → for each retirement age the base verdict badge (`on_track` green, `short` amber, `at_risk` red), "money lasts until age N" (or "past N" when `pot_at_horizon_eur ≥ 0`), either `surplus of X/month` or `short by X/month; save Y/month more`, pessimistic line in `small text-muted`; a warnings list.
- Verdict copy (exact): `On track`, `Short`, `At risk`.

- [ ] **Step 1: Write the failing JS tests** — `needs_setup` renders the setup copy; an `ok` fixture with gap −200 renders "surplus"; gap 300 and extra 150 renders "short by" and "save"; `retirementChartSeries` returns arrays of equal length; a `<` in a warning is escaped.
- [ ] **Step 2: Run `make test-js`** — new file fails.
- [ ] **Step 3: Implement** the markup, client methods, card, chart, plan form (every plan key as a labelled input, `excluded_categories` as a multi-select of `getSpendingCategories()`), incomes editor with the three suggested rows shown as quick-add buttons when the table is empty, the Goals change and help entries; call `initTooltips()` after rendering.
- [ ] **Step 4: Run `make test-js`** and open the page at 390 px (browser or the e2e smoke test `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/e2e -q` if Chromium is available) — no horizontal overflow, no console errors.
- [ ] **Step 5: Commit** — `feat(web): retirement readiness card, plan and incomes; goals link to it`.

---

### Task 10: MCP tools

**Files:**
- Modify: `mcp/server.py` (`networth()` adds the balance-sheet totals and `stale_lines` from `GET /api/v1/networth/balance-sheet`; new `retirement_readiness()` reading `GET /api/v1/retirement/readiness`)
- After merge, outside this repo (same task, second commit in each repo): `~/mcp/remote_gateway/server.py` (`_register(retirement_readiness)` + the `finance` entry of `TOOL_SPECIALIST`), `~/mcp/pfm/test_server.py` (tests below), `~/agents/finance/adapter.yaml` (tools list + the notes line), `~/agents/finance/persona.md` (tool bullet), then `python3 ~/agents/scripts/gen_adapters.py` and `python3 -m pytest -q ~/agents/evals/test_adapters.py`.

**Interfaces:**
- `retirement_readiness() -> str`: text with one block per retirement age: verdict, lasts until, sustainable monthly, gap, extra saving; then warnings; `status` other than `ok` printed as one line with the reason. Reads fields with explicit `.get` and prints `n/a` for a missing one, never 0.

- [ ] **Step 1: Write the failing tests in `~/mcp/pfm/test_server.py`** (monkeypatch `server._get`): `test_retirement_readiness_prints_both_ages`, `test_retirement_readiness_needs_setup_is_one_line`, `test_networth_mentions_stale_lines`.
- [ ] **Step 2: Run `cd ~/mcp/pfm && python3 -m pytest -q .`** — the new tests fail (they import the symlinked main checkout, so this step only passes after the pfm merge; write the tool in the worktree first and run the three tests with `PYTHONPATH` pointed at the worktree's `mcp/` if needed).
- [ ] **Step 3: Implement the tool in `mcp/server.py`; commit in pfm** — `feat(mcp): retirement_readiness tool, stale lines in networth`.
- [ ] **Step 4: After merge:** register in the gateway and the adapter/persona, regenerate adapters, run the gateway and pfm MCP suites (`cd ~/mcp/remote_gateway && python3 -m pytest -q .`, `cd ~/mcp/pfm && python3 -m pytest -q .`), commit in `~/mcp` and `~/agents`.

---

### Task 11: Docs, simulator prefill, status

**Files:**
- Modify: `docs/features/networth-portfolios.md` (sections: Balance sheet, Loans, Account valuations, Retirement readiness: endpoints, freshness table, plan keys, projection rules, UI wiring, the post-deployment checklist from spec §9)
- Modify: `CLAUDE.md` (migration list: v35 line; Feature reference row for net worth: "a fresh account valuation replaces the portfolio's positions and cash lines" and "ledger cash excludes `asset_type='cash'` assets"; schema version line → 35; test note `== 35`)
- Modify: `PROJECT_STATUS.md` (Last updated, Recent summary)
- Modify: `web_client/js/pfm_features.js` (`setupForecastPage` "Load from Net Worth": when `getLoans()` returns at least one loan, prefill mortgage principal, rate and payment from the first loan's `balance_eur`, `annual_rate_pct`, `monthly_payment_eur` instead of the `mortgage` cashflow category)
- Modify: `docs/features/web-client.md` (Net Worth page section)

- [ ] **Step 1: Update the simulator prefill and add a JS test** in `web_client/js/tests/web_client.test.mjs` if a pure helper is extracted (`forecastPrefillFromLoan(loan) -> {principal, rate, payment}`); `make test-js` PASS.
- [ ] **Step 2: Write the docs** as listed; invented figures only.
- [ ] **Step 3: Run the full unit suite, flake8 and black** — `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q` all PASS; `… uv run pytest tests/integration -q` PASS.
- [ ] **Step 4: Commit** — `docs: balance sheet, loans, valuations and retirement readiness; schema v35`.

---

## Rollout (after the branch is merged to `main`)

1. `cp ~/repos/pfm/portfolio.db ~/.hermes/backups/2026-10-XX-pfm-v35/portfolio.db` before `git merge` in `~/repos/pfm`; the live backend migrates on reload.
2. Rebuild the web image: `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`.
3. Add `PORTF_INDEXA_TOKEN` to `.env.local` (value from `INDEXA_API_TOKEN` in `~/.hermes/.env`), restart `portf_backend_dev` if settings are read at startup.
4. Owner's data steps from spec §9 (loan, valuation link, cash choice, plan, incomes, home as-of).
5. Task 10 step 4 (gateway, adapter, persona), then push pfm with `GIT_SSH_COMMAND="ssh -o IdentitiesOnly=no" git push origin main`.
