# Internal Transfers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Money moving between the owner's own accounts never counts as income or expense, in either direction and whichever leg is imported first.

**Architecture:** The pure matcher learns the inflow ↔ `Withdrawal` direction and treats "no link" (not "not flagged") as matchable. The DB-touching glue moves from the spending router into a service that every booking-creating path also calls. A settings-backed list of own-account text patterns flags one-legged moves. A new `transfer_source` column records why a row is a transfer and makes "Transfer" category and `is_transfer` inseparable.

**Tech Stack:** Python 3.13, FastAPI, SQLite (`portf_manager/database.py`), vanilla JS + Bootstrap web client, pytest, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-13-internal-transfers-design.md` — read it first; this plan argues from it.

## Global Constraints

- Work only in the worktree `~/repos/pfm-transfers` (branch `feat/internal-transfers`). Never edit `~/repos/pfm`: it is the live backend.
- Run tooling with `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run …`. Unit suite: `uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`. JS: `make test-js`.
- Black (88), type hints on every signature, Google docstrings, comments on the line before the code. `flake8` must stay at 0 warnings.
- Public repo: fixtures use invented names ("Example Bank", "Sam Sample") and round amounts only. No real names, descriptions or amounts anywhere.
- Schema goes 40 → 41. A new column must be in `_create_all_tables` **and** `_migrate_to_v41`. Update every `== 40` in `tests/test_database.py`.
- `transfer_source` values, exactly: `match`, `pattern`, `manual`, `rejected` (or NULL).
- Match window stays ±3 days (`_MATCH_WINDOW_DAYS`). Action Item constants: 90 days, minimum booking amount 100.
- `bookings` is never modified.
- LLM-free; tests cannot reach the network.
- Commits: conventional, ending with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.

## Review Focus

1. Two same-amount transfers inside one window (two monthly deposits of 200 a day apart): each booking is claimed by exactly one bank row, across separate calls too. → Task 3.
2. `own_account_patterns` in `app_settings` holds malformed JSON or a non-list: treated as an empty list, never a 5xx on save, rescan or GET. → Task 4.
3. A pattern matches a row that is already linked to a counterpart: the row is left exactly as it is. → Task 4.
4. A database stamped 41 without the column, or the migration run twice: no error, same end state. → Task 1.
5. The owner un-marks a pattern-flagged row, then imports another statement and rescans: the row stays un-marked. → Task 5.

## File Structure

| File | Role |
|---|---|
| `portf_manager/database.py` | v41 column + migration; `update_spending_transaction` accepts `transfer_source`; "unlinked" means no link; counterpart reset clears source |
| `portf_manager/services/transfer_matcher.py` | pure pairing: both booking directions, flagged-unlinked rows, tie-break |
| `portf_manager/services/transfer_linking.py` (new) | DB glue: pair, flag by pattern, settle after bookings |
| `portf_manager/services/own_accounts.py` (new) | pattern list: load, validate, save, match (pure matching + settings I/O) |
| `portf_manager/services/spending_rules.py` | rules never yield "Transfer" |
| `portf_server/routers/spending.py` | uses the linking service; pattern endpoints; PUT invariant; rules reject "Transfer"; `transfer_source` in responses; `transfer_linked` filter |
| `portf_server/routers/bookings.py`, `imports.py`, `sync.py`, `portf_manager/cli.py` | call `settle_after_bookings` |
| `portf_manager/services/action_items.py` | two checks |
| `web_client/` (`index.html`, `js/pfm_core.js`, `js/pfm_features.js`, `js/help_text.js`) | "My own accounts" card, "no counterpart" marker, filter option |
| `docs/features/spending.md`, `docs/features/action-items.md`, `PROJECT_STATUS.md`, `CLAUDE.md` | docs |

---

### Task 1: Schema v41 and DB methods

**Files:**
- Modify: `portf_manager/database.py` (`DATABASE_VERSION`, `_create_all_tables` spending_transactions DDL, `_run_migrations` near line 1000, new `_migrate_to_v41`, `update_spending_transaction` ~4504, `list_unlinked_spending_transactions` ~4524, `reset_transfer_counterpart` ~4581, `list_spending_transactions` ~4410)
- Create: `tests/unit/test_schema_v41.py`
- Modify: `tests/test_database.py` (every `== 40`), `tests/unit/test_spending_db.py`

**Interfaces:**
- Produces:
  - column `spending_transactions.transfer_source TEXT CHECK (transfer_source IN ('match','pattern','manual','rejected'))`, nullable
  - `db.update_spending_transaction(id, **kwargs)` also accepts `transfer_source`
  - `db.list_unlinked_spending_transactions() -> List[Dict]`: rows where `transfer_link_id IS NULL` (flagged or not)
  - `db.list_spending_transactions(..., transfer_linked: Optional[bool] = None)`: `True` → `transfer_link_id IS NOT NULL`; `False` → `is_transfer = 1 AND transfer_link_id IS NULL`
  - `db.reset_transfer_counterpart(id)` also sets the counterpart's `transfer_source = NULL`

- [ ] **Step 1: Write failing tests** in `tests/unit/test_schema_v41.py` (model on `test_schema_v40.py`; build the v40 state by creating a fresh DB, dropping the column is not possible in SQLite, so create a v40-shaped `spending_transactions` table by hand in a temp file and stamp version 40, as `test_schema_v37.py` does for its table):

```python
def test_fresh_db_has_transfer_source(tmp_path): ...
    # PRAGMA table_info(spending_transactions) contains "transfer_source"
    # version == DATABASE_VERSION >= 41

def test_migration_backfills_and_repairs(tmp_path): ...
    # v40 rows before migration:
    #  A: is_transfer=1, link_type='booking', link_id=7      -> source 'match'
    #  B: is_transfer=1, link NULL                           -> source 'manual'
    #  C: is_transfer=0, category='Transfer'                 -> category 'uncategorized', source NULL
    #  D: is_transfer=0, category='Groceries'                -> unchanged, source NULL

def test_migration_is_rerunnable(tmp_path): ...
    # call db._migrate_to_v41(conn) a second time: no exception, A..D unchanged

def test_bad_source_value_rejected(tmp_path): ...
    # update_spending_transaction(id, transfer_source="guess") raises DatabaseError
```

And in `tests/unit/test_spending_db.py`:

```python
def test_unlinked_includes_flagged_rows_without_link(db): ...
    # manual-flagged row (no link) IS returned; a linked row is NOT
def test_transfer_linked_filter(db): ...
def test_reset_counterpart_clears_source(db): ...
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_schema_v41.py tests/unit/test_spending_db.py -q` — expect failures on the missing column.

- [ ] **Step 3: Implement.** `DATABASE_VERSION = 41`. `_migrate_to_v41(conn)`: add the column only if `PRAGMA table_info` lacks it (guarded like v33, so a re-run or a mis-stamped DB heals); then three `UPDATE`s — linked flagged rows → `match` where source is NULL; unlinked flagged rows → `manual` where source is NULL; `category = 'Transfer' AND is_transfer = 0` → `category = 'uncategorized'`. SQLite `ALTER TABLE ADD COLUMN` accepts the CHECK inline. Add `transfer_source` to `valid_fields`. Change the unlinked query to `WHERE transfer_link_id IS NULL`.

- [ ] **Step 4: Update** every `== 40` assertion in `tests/test_database.py` to 41; run the full unit suite; expect green. (Callers of `list_unlinked_spending_transactions` now see flagged rows: `_run_transfer_matching` still filters by `saved_ids`, and the matcher still skips `is_transfer` rows until Task 2, so behaviour is unchanged for now.)

- [ ] **Step 5: Commit** `feat(db): schema v41 — transfer_source, Transfer/flag repair`.

---

### Task 2: Matcher — both directions, flagged-unlinked rows, tie-break

**Files:**
- Modify: `portf_manager/services/transfer_matcher.py`
- Test: `tests/unit/test_transfer_matcher.py`

**Interfaces:**
- Consumes: plain dicts with `id, portfolio_id, date, amount, currency, is_transfer, transfer_link_id`.
- Produces (signatures unchanged, semantics widened):
  - `find_transfer_match(row, candidate_spending, candidate_bookings) -> Optional[TransferMatch]`
  - `find_all_transfer_matches(rows, all_unlinked_spending, all_bookings) -> List[TransferMatch]` — third argument is now bookings of **both** actions (rename the parameter from `all_deposit_bookings`).

Rules to implement:
- A row or candidate is skipped when `transfer_link_id` is not None (no longer when `is_transfer`).
- Outflow (`amount < 0`) ↔ `Deposit`; inflow (`amount > 0`) ↔ `Withdrawal`. Different portfolio, same currency, equal absolute amount, within the window.
- Spending candidates are tried before bookings.
- Among several fitting candidates of one kind: smallest absolute date distance, then lowest `id`.

- [ ] **Step 1: Write failing tests:**

```python
def test_inflow_matches_withdrawal_booking(): ...
    # row +3000 in portfolio 1 on 2026-03-10; Withdrawal 3000 in portfolio 2 on 2026-03-09
    # -> TransferMatch(link_type="booking", link_id=<booking id>)
def test_inflow_does_not_match_deposit_booking(): ...
def test_outflow_does_not_match_withdrawal_booking(): ...
def test_withdrawal_same_portfolio_currency_window_exclusions(): ...  # parametrised, each -> None
def test_flagged_unlinked_candidate_is_matched(): ...
    # candidate has is_transfer=1, transfer_link_id=None -> matched
def test_flagged_unlinked_source_is_matched(): ...
    # find_all_transfer_matches with a source row is_transfer=1, link None -> 1 match
def test_linked_row_is_neither_source_nor_candidate(): ...
def test_closest_date_wins_then_lowest_id(): ...
    # bookings: id 9 two days away, id 5 one day away, id 3 one day away -> link_id == 3
def test_two_same_amount_rows_claim_two_bookings(): ...
    # two -200 rows, two Deposit 200 bookings in window -> two matches, distinct link_ids
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_transfer_matcher.py -q` — new tests fail, old ones pass.
- [ ] **Step 3: Implement.** Existing tests that pass `is_transfer` rows without `transfer_link_id` to assert "skipped" must be updated to set a link id — that is the behaviour change, not a regression.
- [ ] **Step 4: Run** the file again — all pass.
- [ ] **Step 5: Commit** `feat(spending): match bank inflows to broker withdrawals; link flagged rows`.

---

### Task 3: Linking service and booking triggers

**Files:**
- Create: `portf_manager/services/transfer_linking.py`
- Modify: `portf_server/routers/spending.py` (remove `_run_transfer_matching` ~403; `/save` ~514; `/rescan-transfers` ~724), `portf_server/routers/bookings.py` (~46), `portf_server/routers/imports.py` (~924), `portf_server/routers/sync.py` (~366), `portf_manager/cli.py` (~2177)
- Test: `tests/unit/test_transfer_linking.py` (new), `tests/unit/test_spending_api.py`, plus the existing test modules for bookings/imports/sync routers (find with `grep -rl "create_booking\|/bookings" tests/unit`)

**Interfaces:**
- Consumes: Task 1 DB methods, Task 2 matcher.
- Produces:
  - `link_transfers(db, spending_ids: Optional[List[int]] = None) -> int` — pair matching over the given rows (None = every unlinked row); writes `category="Transfer", is_transfer=True, transfer_link_type, transfer_link_id, transfer_source="match"` on the row, and on the counterpart for `spending` links. Returns matches made.
  - `settle_transfers(db, spending_ids: Optional[List[int]] = None) -> Dict[str, int]` — returns `{"linked": n, "flagged": 0}` for now; Task 4 fills `flagged`.
  - `settle_after_bookings(db) -> Dict[str, int]` — `settle_transfers(db)` wrapped in `try/except Exception`: on failure logs with `logger.exception` and returns `{"linked": 0, "flagged": 0}`. Never raises.

Bookings already claimed (a spending row with `transfer_link_type == "booking"` pointing at them) are excluded before matching, for both actions — carry over the existing guard.

- [ ] **Step 1: Write failing tests** in `tests/unit/test_transfer_linking.py` (real temp `Database`, two bank portfolios "Example Bank"/"Second Bank" and a brokerage "Example Broker"):

```python
def test_booking_imported_after_bank_row_links_it(db): ...
    # bank row -800 saved; link_transfers -> 0. create Deposit 800 booking;
    # settle_after_bookings(db) == {"linked": 1, "flagged": 0}; row source == "match"
def test_withdrawal_links_inflow(db): ...
def test_claimed_booking_not_claimed_again_across_calls(db): ...
    # Review Focus 1: Deposit 200 on day 1 and day 2; bank -200 on day 1 -> settle;
    # second bank -200 on day 2 -> settle; the two rows link to different bookings
def test_manual_flagged_row_upgrades_to_match(db): ...
    # row flagged by hand (source manual, no link); booking arrives -> source "match", link set
def test_settle_after_bookings_swallows_errors(db, monkeypatch): ...
    # monkeypatch settle_transfers to raise -> returns zeros, no exception
```

Router tests (one per path, asserting the bank row is linked after the call): `POST /api/v1/bookings/`, the import save with a booking, the PDT pull with a booking; and one asserting that with `settle_after_bookings` patched to raise inside (patch `transfer_linking.settle_transfers`), the booking is still created and the response is still 2xx. CLI: unit test that `settle_after_bookings` is called once after a booking import when `db_manager` is a real `Database`, and not called when it is the HTTP client (`hasattr(self.db_manager, "list_unlinked_spending_transactions")` is the switch, same style as the existing `match_existing_bookings` check).

- [ ] **Step 2: Run** the new tests — fail on the missing module.
- [ ] **Step 3: Implement** the service; replace the router's private function with `link_transfers`/`settle_transfers`; `/rescan-transfers` returns `{"transfers_linked": linked, "transfers_flagged": flagged}`; `/save` keeps its `transfers_linked` field and calls `settle_transfers(db, saved_ids)`. In each booking path call `settle_after_bookings(db)` **once after the loop**, only if at least one booking was created.
- [ ] **Step 4: Run** the full unit suite — green.
- [ ] **Step 5: Commit** `feat(spending): re-run transfer matching when bookings are created`.

---

### Task 4: Own-account patterns

**Files:**
- Create: `portf_manager/services/own_accounts.py`
- Modify: `portf_manager/services/transfer_linking.py`, `portf_server/routers/spending.py`
- Test: `tests/unit/test_own_accounts.py` (new), `tests/unit/test_transfer_linking.py`, `tests/unit/test_spending_api.py`

**Interfaces:**
- Produces in `own_accounts.py`:
  - `SETTING_KEY = "own_account_patterns"`, `MIN_PATTERN_LENGTH = 4`
  - `clean_patterns(raw: Iterable[str]) -> List[str]` — strip, drop case-insensitive duplicates keeping first; raises `ValueError` naming the offender for a blank pattern or one shorter than 4 characters after stripping
  - `load_patterns(db) -> List[str]` — `db.get_setting(SETTING_KEY)`; missing, malformed JSON, or anything but a list of strings → `[]` with a `logger.warning`
  - `save_patterns(db, patterns: Iterable[str]) -> List[str]` — `clean_patterns`, then `db.set_setting` as JSON; returns the stored list
  - `matches_own_account(patterns: Iterable[str], description: str, merchant: Optional[str]) -> bool` — case-insensitive substring on either field
- Produces in `transfer_linking.py`:
  - `flag_own_account_rows(db, spending_ids: Optional[List[int]] = None, patterns: Optional[List[str]] = None) -> int` — candidates: rows with `is_transfer = 0` and `transfer_source` not `rejected` (None = all such rows); a match gets `category="Transfer", is_transfer=True, transfer_source="pattern"`, no link. `patterns=None` loads from settings.
  - `preview_own_account_pattern(db, pattern: str) -> List[Dict]` — rows `flag_own_account_rows` would flag for this single pattern, nothing written
  - `settle_transfers` now runs `link_transfers` **then** `flag_own_account_rows` and reports both counts.
- Endpoints (register above every `/{spending_id}` route):
  - `GET /api/v1/spending/own-account-patterns` → `{"patterns": [...]}`
  - `PUT /api/v1/spending/own-account-patterns` body `{"patterns": [...]}` → 400 with the `ValueError` text on a bad pattern; otherwise saves, runs `flag_own_account_rows(db)` and returns `{"patterns": [...], "flagged": n}`
  - `POST /api/v1/spending/own-account-patterns/preview` body `{"pattern": "..."}` → `{"count": n, "rows": [first 20 as SpendingTransactionResponse]}`; 400 on a blank or short pattern

- [ ] **Step 1: Write failing tests:**

```python
# test_own_accounts.py
def test_clean_strips_and_dedups(): ...           # ["  Sam Sample ", "sam sample"] -> ["Sam Sample"]
def test_clean_rejects_blank_and_short(): ...     # "", "   ", "abc" -> ValueError
def test_load_tolerates_garbage(db): ...          # Review Focus 2: setting = "{not json", then '"text"', then '[1, 2]' -> []
def test_matches_description_or_merchant_case_insensitive(): ...

# test_transfer_linking.py
def test_pattern_flags_on_settle(db): ...         # source "pattern", category "Transfer", link None
def test_pair_match_wins_over_pattern(db): ...    # row both pairable and pattern-matching -> source "match"
def test_later_leg_links_to_pattern_flagged_row(db): ...
def test_pattern_leaves_linked_row_alone(db): ... # Review Focus 3: row unchanged field by field
def test_pattern_overrides_existing_category(db): ...   # row categorised "Groceries" -> "Transfer"
def test_pattern_skips_rejected_row(db): ...
def test_removing_pattern_does_not_unflag(db): ...

# test_spending_api.py
def test_patterns_get_put_roundtrip(client): ...
def test_put_applies_and_reports_flagged(client): ...
def test_put_rejects_short_pattern_400(client): ...
def test_preview_counts_without_writing(client): ...
def test_save_pattern_wins_over_rule(client): ...  # rule would categorise the row; pattern flags it instead
def test_get_survives_malformed_setting(client): ...  # 200 with []
```

- [ ] **Step 2: Run** — fail on the missing module/routes.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full unit suite — green.
- [ ] **Step 5: Commit** `feat(spending): own-account patterns for one-legged transfers`.

---

### Task 5: "Transfer" category and the flag always agree

**Files:**
- Modify: `portf_server/routers/spending.py` (`update_spending_category` ~631, `create_rule` ~807, `update_rule` ~886, `SpendingTransactionResponse` ~125, list endpoint ~563), `portf_manager/services/spending_rules.py` (`pick_category`), `portf_manager/database.py` (`reset_transfer_counterpart` if not done)
- Test: `tests/unit/test_spending_api.py`, `tests/unit/test_spending_rules_service.py`

**Interfaces:**
- Produces:
  - `TRANSFER_CATEGORY = "Transfer"` constant in `spending_rules.py`; `pick_category` skips any rule whose category equals it (case-sensitive, as stored).
  - `PUT /spending/{id}`: `category == "Transfer"` (with `is_transfer` absent) behaves as `is_transfer: true`. Marking by either route sets `transfer_source = "manual"` and keeps an existing link untouched **only if the row is already a transfer**; otherwise link fields are cleared as today. Un-marking by either route (`is_transfer: false`, or a category other than "Transfer" on a flagged row) sets `is_transfer = 0`, clears the link and sets `transfer_source = "rejected"`.
  - `POST`/`PUT /spending/rules` with category "Transfer" → 400, detail: `Rules can't file rows as "Transfer". Add the text to "My own accounts" instead.`
  - `SpendingTransactionResponse.transfer_source: Optional[str]`; the PUT response gains the same field.
  - `GET /spending/` gains `transfer_linked: Optional[bool]` passed through to the DB filter.

First confirm two assumptions from the spec by reading the code: `db.get_spending_category_root("Transfer")` returns None and `_sign_matches_root(None, amount)` is True for both signs. If either is false, special-case "Transfer" before the sign check.

- [ ] **Step 1: Write failing tests:**

```python
def test_put_category_transfer_sets_flag_and_manual(client): ...
def test_put_is_transfer_true_sets_manual(client): ...
def test_unmark_sets_rejected_and_clears_link(client): ...
def test_recategorise_away_sets_rejected(client): ...
def test_unmarked_row_survives_rescan(client): ...
    # Review Focus 5: pattern flags row; un-mark; save another statement; POST /rescan-transfers;
    # row still is_transfer False, source "rejected"
def test_rejected_row_can_still_pair_match(client): ...
def test_counterpart_source_cleared_not_rejected(client): ...
def test_rule_targeting_transfer_rejected_on_post_and_put(client): ...   # 400
def test_pick_category_skips_stored_transfer_rule(): ...   # falls through to the next rule or "uncategorized"
def test_list_exposes_transfer_source_and_linked_filter(client): ...
```

- [ ] **Step 2: Run** — fail.
- [ ] **Step 3: Implement.** Update the endpoint docstring: the old "category alone does not set the flag" paragraph is now wrong.
- [ ] **Step 4: Run** the full unit suite, including the budget ↔ trend reconciliation test — green.
- [ ] **Step 5: Commit** `feat(spending): Transfer category always carries the flag; un-marking sticks`.

---

### Task 6: Action Items

**Files:**
- Modify: `portf_manager/services/action_items.py`
- Test: `tests/unit/test_action_items.py`

**Interfaces:**
- Produces, both added to `_CHECKS`:
  - `check_unmatched_pattern_transfers(db, today: date | None = None) -> list[dict]`
  - `check_bookings_without_bank_leg(db, today: date | None = None) -> list[dict]`
  - constants `TRANSFER_REVIEW_DAYS = 90`, `TRANSFER_SETTLE_DAYS = 3`, `BOOKING_BANK_LEG_MIN = 100`

Item shape as in `check_recurring_charges`: `category: "spending"`, `link_page: "spending"`, `severity: "medium"` for the booking check and `"low"` for the pattern check. Ids: `transfer:unmatched:{spending_id}` and `transfer:no-bank-leg:{booking_id}`. `context` carries `spending_id` / `booking_id`.

Rules:
- Pattern check: `transfer_source == "pattern"`, no link, `today − 90 ≤ date ≤ today − 3`.
- Booking check: booking of either action, `amount ≥ 100`, dated within the last 90 days, not the target of any `transfer_link_type == "booking"` row, and **every** bank portfolio (`account_type == "bank"`) that has at least one spending row in the last 90 days has a row dated after `booking date + 3 days`. No such bank portfolio at all → no item.

- [ ] **Step 1: Write failing tests** — for each check: fires in the plain case; quiet at each boundary (pattern: 2 days old, 91 days old, `manual` source, linked; booking: amount 99, 91 days old, already linked, one active bank account whose latest row is before the booking date + 3, no bank accounts); `checks_for_tests()` contains both.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_action_items.py -q` — fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — pass.
- [ ] **Step 5: Commit** `feat(action-items): unsettled transfer checks`.

---

### Task 7: Web client

**Files:**
- Modify: `web_client/index.html` (Spending page: next to the Rules card ~3328; transfer filter in the Transactions tab), `web_client/js/pfm_core.js` (API methods near `rescan-transfers` ~2641), `web_client/js/pfm_features.js` (row badge ~8304, rules card code ~8920), `web_client/js/help_text.js`
- Test: `web_client/js/tests/` — new `own_accounts.test.mjs`, added by name to the `make test-js` target

**Interfaces:**
- Consumes: Task 4 endpoints, Task 5 `transfer_source` and `transfer_linked`.
- Produces: pure, `window.`-exported helpers
  - `spTransferBadge(row) -> string` (HTML): linked transfer → the existing "Transfer" badge; flagged without link → the badge plus a muted "no counterpart" marker whose `title` is "Flagged by your own-accounts list" (`pattern`) or "Marked by hand" (`manual`)
  - `spOwnPatternPreviewText(count) -> string`: `"Would mark 0 rows"`, `"Would mark 1 row as a transfer"`, `"Would mark N rows as transfers"`

Card "My own accounts": the list with a remove button per entry, an input with Add, the preview text updating (debounced, like `_scheduleSpRulePreview`) as the owner types, and a status line reporting `flagged` after a save. Every change PUTs the whole list. Help text (also in `METRIC_HELP`): what belongs here — your own name as the bank prints it, labels of your own standing orders; what does not — a partner's name, a generic bank word for "transfer", a tax id that also appears on tax payments. Removing an entry shows a `notify` that already-marked rows stay marked.

Transfer filter: add "Transfers without counterpart" → `transfer_linked=false`.

House rules that apply: `esc()` on every pattern and description before `innerHTML`; `notify`/`confirmDialog`, no native dialogs; do not interpolate a `<span>`-emitting formatter into an attribute; call `initTooltips()` after rendering; check the card at 390px width.

- [ ] **Step 1: Write failing JS tests** for the two helpers (the three badge states; counts 0, 1, 5).
- [ ] **Step 2: Run** `make test-js` — fail.
- [ ] **Step 3: Implement** helpers, API methods, card, filter, help text.
- [ ] **Step 4: Run** `make test-js` and `uv run pytest tests/integration tests/e2e` (the e2e smoke test fails on any uncaught JS error or 5xx on the Spending page) — green.
- [ ] **Step 5: Commit** `feat(web): own-accounts card and no-counterpart marker`.

---

### Task 8: Docs and final verification

**Files:**
- Modify: `docs/features/spending.md`, `docs/features/action-items.md`, `PROJECT_STATUS.md`, `CLAUDE.md`

- [ ] **Step 1:** `spending.md` — rewrite the transfer auto-linking bullet (both directions, no-link semantics, tie-break, re-run on booking creation), add own-account patterns (endpoints, order of application, `rejected`), replace the "category alone does not set the flag" text, document `transfer_source` and `transfer_linked`.
- [ ] **Step 2:** `action-items.md` — the two checks with their constants. `PROJECT_STATUS.md` — bump "Last updated", add to the Recent line.
- [ ] **Step 3:** `CLAUDE.md` — "Current schema version: 41"; one v41 line in the migration history; in the Spending row of the Feature reference table add: category "Transfer" always carries `is_transfer` and rules cannot target it; every booking-creating path must call `settle_after_bookings`; "unlinked" means no link, not "not flagged". Update the `== 40` note in Testing to `== 41`.
- [ ] **Step 4: Verify.** Run, and expect all green / 0 warnings:

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/integration tests/e2e -q
make test-js
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501
cd mcp && python3 -m pytest -q
```

- [ ] **Step 5: Commit** `docs: internal transfers`.

---

## After the branch (not plan tasks; needs the owner)

1. Merging to `main` in `~/repos/pfm` makes the Python live at once and migrates the **live** database to v41 on the next reload. Take a copy of `portfolio.db` first. The web client needs `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`.
2. Spec section 6, the live data correction: shown to the owner as a list before any write.
