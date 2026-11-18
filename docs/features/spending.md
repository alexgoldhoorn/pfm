# Spending tracking (bank accounts)

> Moved out of `CLAUDE.md` on 2026-09-25 so it isn't loaded into every session. CLAUDE.md keeps the must-know gotchas and links here; this file is the full reference. "See the X section" means the matching file in `docs/features/`, or CLAUDE.md.

### Spending Tracking (`portf_server/routers/spending.py` + `portf_manager/parsers/generic_bank_csv_parser.py` + `portf_manager/services/transfer_matcher.py`)

Bank-account spending tracking, kept deliberately separate from the investment `transactions`/`bookings` tables — a bank statement row has no asset/quantity/price and uses different dedup + transfer semantics. A bank account is still a `portfolios` row (`account_type='bank'`) so it gets the existing broker/account list machinery for free; Holdings/Rebalance/position endpoints need no filtering change since they source only from `transactions`, which bank accounts never populate.

- `POST /api/v1/spending/upload` — multipart file + `account_portfolio_id` or `account_name` (auto-creates via `get_or_create_portfolio(..., account_type='bank')`) → parsed + rule-categorized preview, no DB write. Uses `generic_bank_csv_parser.parse_generic_bank_csv` (required cols `date, description, amount`; optional `balance, currency`; EN/ES/NL header synonyms; reuses `generic_csv_parser.py`'s EU/US delimiter/date/decimal detection helpers rather than duplicating them).
- `POST /api/v1/spending/suggest-categories` — LLM-assisted category + rule-pattern suggestions for rows no rule matched, via `get_llm_client(tier="fast").generate()`. Explicit user-triggered button, not automatic (LLM calls are slow/costly). Accepting a suggestion in the UI creates a new `spending_rules` row so future imports auto-match — rules only grow from confirmed human decisions.
- `POST /api/v1/spending/save` — writes `spending_transactions`, honoring `duplicate_action` (skip/add/overwrite, dedup on portfolio+date+amount+description via `find_duplicate_spending_transaction`), then runs transfer auto-linking over the newly-saved batch.
- `GET /api/v1/spending/` (filters: `portfolio_id`, `category`, `categories` [repeatable, `IN`-matched — e.g. `?categories=A&categories=B`; omitted/empty means unfiltered], `start_date`, `end_date`, `is_transfer`, `amount_sign` [`positive`|`negative`, 400 on any other value], `min_abs_amount` [`ABS(amount) >= min_abs_amount`, composes with `amount_sign` or alone]; paginated/sorted server-side via `limit`/`offset`/`sort_by` [`date`|`portfolio_name`|`description`|`category`|`amount`]/`sort_dir` [`asc`|`desc`] query params — response shape is `{"items": [...], "total": N}`, not a bare array. Web: the Spending page's Transactions tab filter row has a checkbox-dropdown multi-select for category (Select all/none + the "N categories" button label) and an Amount cell (Expenses-only/Income-only sign toggles + a "≥ amount" threshold field) — unchecking every category resolves to zero results client-side with no network call, checking every category is treated as unfiltered.), `GET/POST/PUT /api/v1/spending/categories` (`GET` returns the deduplicated union of categories used on transactions, used on rules, and explicitly registered in the new `spending_categories` table; `POST` creates a bare unused category — 400 on a blank name, 409 on an exact duplicate; `PUT /api/v1/spending/categories/{old_name}` renames the category everywhere — every transaction, every rule, and the registry — 400 on a blank or same-old/new name, merging into an existing name without erroring if you rename onto one that's already registered), `PUT /api/v1/spending/{id}` (category only — see the `DELETE` bullet below for its transfer-clearing behavior), `GET/POST /api/v1/spending/rules` (`POST` rejects an exact duplicate — case-insensitive pattern match + exact category match against an existing rule — with 409), `PUT /api/v1/spending/rules/{id}` (edit an existing rule's pattern/category — the Rules card on the Spending page now has an edit-in-place pencil icon per row, not just add/delete), `DELETE /api/v1/spending/rules/{id}`, `GET /api/v1/spending/summary?days=30` (EUR-converted spent/income/transferred + by-category breakdown, powers both the Spending page cards and the Net Worth "Actual" widget), `POST /api/v1/spending/rescan-transfers`.
- `POST /api/v1/spending/rescan-categories` — re-applies current `spending_rules` to still-`uncategorized` rows, same on-demand pattern as `/rescan-transfers`; never touches a row that already has a non-`uncategorized` category, so it's safe to run after adding/editing rules without risk of overwriting a manually-set or previously-matched category. Optional JSON body `{"ids": [...]}` scopes it to just those rows (still only the ones among them still `uncategorized`) instead of every uncategorized row account-wide; an absent/empty body keeps the original all-rows behavior. Web: "Rescan categories" button next to "Re-scan transfers" on the Spending page (global, no `ids`); the Spending page's Rules card auto-calls this (global) right after "Add" creates a rule, reporting the match count in `#spRuleStatus`; the bulk-select bar's new "Apply rules to selected" button (`#spBulkApplyRulesBtn`) calls it scoped to the checked row ids.
- **AI category suggestions on already-imported rows**: the Spending page's bulk-select checkboxes (used for bulk recategorize/delete) also drive a "Suggest categories (AI)" action — calls the existing `/suggest-categories` endpoint (unchanged; already generic, not import-specific) against the selected `uncategorized` rows, deduplicated client-side by description (`dedupSpendingRowsByDescription` in `pfm_features.js`) to keep the LLM call small when many rows share a merchant. Results open in a review panel (`#spSuggestReviewPanel`, editable per-suggestion category dropdown + include/exclude checkbox) — nothing is written until "Apply", at which point each accepted suggestion both updates the matching row(s)' category and creates a new `spending_rules` row (same "accept creates a rule" behavior as the import flow; duplicate pattern+category rules are deduped client-side within the batch via `_ruleDedupKey` and rejected server-side with 409 by `POST /api/v1/spending/rules` either way), then Apply automatically calls `POST /api/v1/spending/rescan-categories` once so any *other* already-imported row still `uncategorized` that matches one of the newly-created (or already-existing) rules gets swept up in the same click, not just the rows that were part of this suggestion batch — the status message reports both counts ("Applied to N row(s). Rescan matched M more rows."). Each click is capped to `SP_AI_SUGGEST_BATCH_SIZE` (30) unique descriptions — a real account's uncategorized backlog can have 1000+ unique descriptions, and sending them all in one request risks exceeding `portf_web`'s nginx `proxy_read_timeout` (200s) with an opaque 502 and nothing logged server-side, since the request never completes. The status message states "Sent X of Y..." when the cap applies. A "Select all uncategorized" button (`#spSelectAllUncategorized`) sets the Category filter to `uncategorized` and selects every now-filtered row in one click, so working through a large backlog is: select-all-uncategorized → suggest (batch of 30) → review → Apply → repeat (no auto-chaining needed — applying a batch removes those rows from the uncategorized filter, so the next select-all click naturally surfaces a smaller remaining set). The review panel also has an editable pattern field per suggestion, allowing AI-suggested patterns to be corrected before they become rules. Category fields throughout the Spending page (the per-row table, the bulk-recategorize field, and this AI-suggest review panel) are now free-text inputs backed by a shared autocomplete `<datalist>` of existing categories, so a brand-new category can be typed directly in any of them instead of only via the separate Add Rule form; `PUT /api/v1/spending/{id}` also now rejects a blank category with a 400. The Spending page is now split into Transactions/Categories/Rules/Analytics/Recurring tabs (`#spTabs`); see "Click-to-edit category + rule offer" below for the Transactions tab's category column. The Categories tab holds a Chart.js category-breakdown chart (top 8 by 30-day amount, with a "Show all" toggle and a Bar/Pie type toggle — pie mode reuses the Dashboard donut's color palette, and the canvas sits in a fixed-height wrapper with `maintainAspectRatio: false` to avoid Chart.js's larger default aspect ratio for pie/doughnut types) plus the category-rename (cascading to every transaction, rule, and the registry), add-a-bare-category UI, and an A-Z/Z-A sort toggle on the category list, backed by the new `spending_categories` registry table (db v27). The Rules tab's Pattern/Category column headers are click-to-sort (client-side, asc/desc toggle with an arrow indicator) since all rules are loaded unpaginated.
- **Transfer auto-linking** (`transfer_matcher.find_all_transfer_matches`, pure/DB-free; DB glue in `services/transfer_linking.py`): works in both directions. An outflow matches an inflow of the same absolute amount and currency within ±3 days in a *different* account: another bank account's `spending_transactions` row, or a brokerage `bookings` `Deposit`. An inflow matches the other bank's outflow, or a brokerage `Withdrawal`. Spending rows are tried before bookings; with several fits the closest date wins, then the lowest id. Matched rows get `category='Transfer'`, `is_transfer=1`, `transfer_source='match'` and a link (`transfer_link_type='booking'` never modifies `bookings`). **Eligibility means "has no link", not "is not flagged"**: `list_unlinked_spending_transactions` returns every row with `transfer_link_id IS NULL`, so a row flagged by pattern or by hand is upgraded to a linked pair when its real counterpart is imported later (both legs end up `match`). A row that already has a link is never a candidate. Because `bookings` has no flag, `link_transfers` drops every booking id already used as a `transfer_link_id` before calling the matcher, so a booking cannot be claimed twice (this covers withdrawals too).
  - **When it runs:** `settle_transfers(db, ids)` (link, then own-account patterns) runs after every `/save` (over the saved ids) and on `POST /rescan-transfers` (all rows; the response now also has `transfers_flagged`). `settle_after_bookings(db)` re-runs it over all rows after bookings are created, and **every booking-creating path must call it**: `POST /bookings/`, the web import save, the PDT pull (`routers/sync.py`) and the CLI's direct-DB booking import. It never raises: a failure is logged and the booking import still succeeds.
  - **Own-account patterns** (`services/own_accounts.py`) cover a move whose other leg can never be imported (a closed or untracked own account). They are a JSON list of strings in `app_settings` `own_account_patterns`; each is a case-insensitive substring tested against description and merchant, at least 4 characters, blanks refused with 400, duplicates dropped. Endpoints (registered before any `/{id}` route): `GET /own-account-patterns` → `{patterns}`; `PUT` replaces the list and flags existing rows at once → `{patterns, flagged}`; `POST /own-account-patterns/preview` with `{pattern}` → `{count, rows}` (first 20 rows, nothing written). Order of application: pair matching first, then patterns, so a real counterpart always wins. A pattern flags a non-transfer row as `category='Transfer'`, `is_transfer=1`, no link, `transfer_source='pattern'`, replacing any category it had (the preview lists those rows first), and on save it wins over a category rule. It never flags a row whose source is `rejected`. Removing a pattern does not un-flag rows it flagged earlier. Web: the "My own accounts" card under the Rules card (list, remove, live preview count while typing, add).
- `POST /api/v1/spending/upload` auto-detects **AEB43/Norma 43** ("Cuaderno
  43") fixed-width bank exports — a Spanish national standard offered by
  Caixa Enginyers and Abanca as an alternative to CSV — via
  `portf_manager/parsers/aeb43_parser.py` (`looks_like_aeb43`/`parse_aeb43`).
  No new API param or UI control: the endpoint decodes the upload (falling
  back to Latin-1 when the bytes aren't valid UTF-8 — common for AEB43
  exports) and sniffs whether the first record is a valid AEB43 header
  before choosing between `parse_aeb43` and the generic CSV parser. Unlike
  the generic CSV parser, AEB43 exports carry a genuine per-row running
  `balance`, computed from the file's own opening-balance record — feeding
  directly into the Net Worth "bank balance" derivation (see Net Worth API
  section). Bank-agnostic by construction (validated against real Caixa
  Enginyers and Abanca exports with zero bank-specific branching), so it
  should also cover any other bank using the same national standard.
  **Deferred**: dedicated parsers for Revolut and MyInvestor-cash (real
  export column layouts not yet available) — the generic CSV parser covers
  them for now.
- Web: new top-level "Spending" nav page (import modal, category breakdown, sortable transaction table with inline category edit, rules management) in `pfm_features.js`; Net Worth page gets a read-only "Actual (last 30 days)" comparison next to the manual Monthly Cash Flow fields in `pfm_analytics.js` — comparison only, does not feed Goals/Forecast. Import modal (`#spImportModal`, `_wireSpendingImportModal`/`_renderSpImportPreview`) shows a duplicate-handling `<select id="spDuplicateAction">` (Skip/Add anyway/Overwrite existing) when the preview contains any `is_duplicate` rows — mirrors the investment-import `_dupControl`/`ioDupAction` pattern in `pfm_core.js`. Read via `_spDupAction()` (defaults to `'skip'` when not rendered) and passed as `saveSpendingTransactions`'s third arg instead of a hard-coded `'skip'`.
- **`PUT /api/v1/spending/{id}` takes `category` and/or `is_transfer`** (neither is a 400), and the "Transfer" category and the `is_transfer` flag always agree. Marking (`is_transfer: true`, or `category: "Transfer"` with no `is_transfer`) sets the flag, forces the category to `Transfer` and sets `transfer_source='manual'`; a row that already is a transfer keeps its link, any other row gets none (by-hand marking exists for a counterpart that was never imported). Un-marking (`is_transfer: false`, or any other category on a flagged row) clears flag and link, sets `transfer_source='rejected'` (a rescan, pair matching and the patterns skip the row from then on, as source and as counterpart, bookings included; only marking it by hand again makes it a transfer) and moves a `Transfer` category back to `uncategorized`. "Transfer" is outside the Income/Spend tree, so the sign check passes for both signs. Every surface that excludes transfers (`/summary`, `/trend`, the Budget) sees the row consistently. Web: "Mark as transfer" / "Not a transfer" in the bulk-action bar (`#spBulkMarkTransferBtn`/`#spBulkUnmarkTransferBtn`).
- **Transfer invariants outside `PUT`:** a row saved by `POST /spending/save` with category `Transfer` is stored as a transfer marked by hand (`is_transfer=1`, `transfer_source='manual'`, no link; pair matching may still upgrade it to `match`). `duplicate_action='overwrite'` leaves the category and transfer fields of an existing transfer untouched. `PUT /spending/categories/{old}` answers 400 for a rename to or from `Transfer`. `POST /suggest-categories` maps a suggested `Transfer` to `uncategorized`, and the import preview creates no rule for a `Transfer` row. Marking a row that already is a transfer keeps its `transfer_source` and link. Stored own-account patterns that are blank or shorter than 4 characters are dropped on load (logged). The own-accounts card asks for confirmation (with the affected row count) before an added pattern is applied.
- **`transfer_source`** (schema v41, nullable) says how a row became a transfer: `match` (paired automatically), `pattern` (own-account pattern), `manual` (marked by hand), `rejected` (the owner un-marked it). It is in every spending row response. **`transfer_linked`** is a `GET /spending/` filter: `true` keeps rows with a link, `false` keeps flagged rows with no link; the Transactions tab has a "Transfers without counterpart" option and shows a "no counterpart" marker on such rows (tooltip: by pattern or by hand).
- **Rules cannot target "Transfer"**: `POST` and `PUT /spending/rules` return 400 pointing at "My own accounts", and rule evaluation (`spending_rules.pick_category`) skips any stored rule with that category, so an old rule row is inert. Migration v41 also set every row with category `Transfer` but no flag back to `uncategorized`.
- ⚠️ **Un-transferring a row now resets its counterpart too**, via the new `db.reset_transfer_counterpart(spending_id)` — shared by `DELETE`, by recategorizing away from `"Transfer"`, and by `is_transfer: false`. Leaving the other leg alone would strand it with `is_transfer=1` and a link to a row that no longer claims it, excluded from totals and invisible to rescan. The counterpart's `transfer_source` is cleared to NULL, not `rejected`. Only a genuine **reciprocal** link is reset (the counterpart's own `transfer_link_id` must point back), never a coincidental one.
- `DELETE /api/v1/spending/{id}` — hard delete of a single spending transaction. If the deleted row is one leg of a `spending`<->`spending` transfer pair (`transfer_link_type == "spending"`), the surviving leg is reset (`is_transfer=0`, `transfer_link_type`/`transfer_link_id` cleared, `category='uncategorized'`) rather than left orphaned pointing at a now-nonexistent row — only when its own `transfer_link_id` genuinely points back at the deleted row. Booking-linked rows (`transfer_link_type == "booking"`) have no spending-side counterpart to touch. Same design principle as `PUT /api/v1/spending/{id}`, which clears `is_transfer`/link fields on a row when its category is edited away from `"Transfer"` (see above) — neither endpoint leaves orphaned transfer state behind. Spending page table also has a bulk-select checkbox column with a bulk action bar (`#spBulkBar`) to recategorize or delete multiple selected rows at once.
- Bank accounts can also be created directly via `POST /api/v1/portfolios/` with `account_type: "bank"` from the Brokers page UI, not just implicitly via a Spending import.
- `GET /api/v1/export/csv` accepts repeated `portfolio_id` query params (`?portfolio_id=1&portfolio_id=2`) for a combined multi-account CSV export.
- The Import/Export page's "Import Bank Statement" card reuses `_wireSpendingImportModal`/`_renderSpImportPreview` (now parameterized by an element-id config object) rather than duplicating the import flow — same code path as the Spending page's own import modal.
- **Hierarchical spending categories (v28):** Categories now form a tree rooted at two fixed "Income" and "Spend" nodes, via new `spending_categories.parent_id`/`is_root` columns. `spending_transactions.category` and `spending_rules.category` continue storing bare leaf names (globally unique) unchanged — a bare name still unambiguously identifies one tree node. A category's tree root must match its transactions' amount sign (income categories live under Income, spend categories under Spend): direct edits (`PUT /api/v1/spending/{id}`) reject a mismatch with 400; automated rule application (imports, rescans) silently falls back to `uncategorized` on a mismatch instead of erroring. A migration auto-filed every pre-existing category under Income or Spend based on the majority sign of its own past transactions. New endpoints: `GET /api/v1/spending/categories/tree` (every category with its tree position and full breadcrumb path) and `PUT /api/v1/spending/categories/{name}/parent` (reparent a category, with cycle prevention). `POST /api/v1/spending/categories` now requires a `parent_name` field (or the parent is rejected as invalid). The Spending page's category-breakdown chart (and the Dashboard's "Spending" card, which reads the same data) now rolls up to top-level Spend groups instead of showing every individual leaf category (this chart has since moved to the Spending page's Analytics tab — see below — and, after the fix in this branch, both it and the Dashboard card include an `uncategorized` bucket so the two stay in sync). The Categories tab is now an indented tree view with a parent-reassignment dropdown control next to the existing rename-in-place pencil per category. The category datalist (used by the bulk-recategorize field, the Add Rule form's category field, and the AI-suggest review panel) now shows full breadcrumb paths (`"Spend > Insurance > Car Insurance"`) as suggestions while still submitting/persisting only the bare leaf name.
- **Spending Analytics tab:** New Analytics tab (`#spTabBtnAnalytics`/`#spPaneAnalytics`) on the Spending page holds a monthly trend chart plus the category-breakdown chart, which moved here from the Categories tab — the Categories tab now shows only the tree/CRUD UI, no chart. `GET /api/v1/spending/trend?months=12` (`SpendingTrendMonth`: `month` [`"YYYY-MM"`], `spent_eur`, `income_eur`, `net_eur`) returns zero-filled monthly spent/income/net for the last N calendar months, EUR-converted at today's rate (same convention as `/summary`), transfers excluded, oldest month first; rendered by `_renderSpTrendChart` as a combo Chart.js chart (Spent/Income bars + a Net line). `GET /api/v1/spending/categories/breakdown?parent=Spend&days=30` (`SpendingCategoryBreakdownResponse`: `parent`, `children: [{name, amount_eur, has_children}]`) returns a tree node's immediate children with subtree-summed EUR totals for the period; 400 if `parent` is a leaf or an unknown name. The category chart is now click-to-drill-down: clicking a child with `has_children=true` re-scopes the chart to that child's own children — `window._spBreakdownPath` tracks the stack of parent names, `_loadSpBreakdownLevel` fetches the new level, and `_renderSpBreadcrumb` renders it as a clickable breadcrumb trail (clicking an earlier crumb truncates the path back to it); clicking a leaf child (`has_children=false`) instead opens `openSpCategoryTransactionsModal(categoryName, days)`, a read-only transactions list for that category+period whose "View in Transactions tab" link switches to the Transactions tab, sets its category filter and date range to match, and re-renders the table.

### Merchant names (v32)

`spending_transactions.merchant` holds a cleaned-up merchant name derived from
the raw bank description by `portf_manager/services/merchant.py`'s
`normalize_merchant()` — stored **alongside** the description, never instead
of it. Spanish bank exports wrap the actual merchant in noise: a glued-on
card/terminal reference number, a `\CITY\ES<digits>` location suffix,
processor prefixes (`PAYPAL *`, `SumUp *`), masked card numbers, order codes
after a star, short dates and trailing reference numbers; `normalize_merchant`
strips all of that in a fixed pipeline and falls back to the
whitespace-collapsed raw description when cleaning would otherwise leave
nothing. It is computed once at import time (`POST /spending/upload`) and
persisted, not recomputed on read. The v32 migration backfills `merchant` for
every existing row from its stored `description`.

`merchant` feeds three surfaces: search (`q` below matches description, merchant OR
category name), rule matching (a rule's pattern is checked against both), and
recurring-charge grouping (`services/recurring.py` groups by
`portfolio_id + merchant.upper() + currency`, so charges from the same shop
group together even when the raw description varies run to run). The
Transactions table's description cell shows the merchant name with the raw
description available underneath/on hover, rather than only the raw text.

### Search (`q` on `GET /spending/`)

`GET /api/v1/spending/?q=<text>` matches `description`, `merchant` OR the
category name, ignoring case **and accents** and **literally** (a plain substring match, no
wildcards or regex): `comissio` and `comissió` both find `COMISSIÓ`. SQLite's
`LIKE` folds only ASCII case, so both sides go through the `pfm_fold()` SQL
function, which `Database.get_connection` registers on every connection
(`services/merchant.py` `fold_for_search`: casefold first, then NFKD
decompose and drop combining marks — casefolding first matters for a
character that folds into a base letter plus a combining mark, e.g. a
dotted capital I); the query is folded before `%`/`_`/`\` are escaped. The category match also
covers the categories above the row's own in the tree, so `taxes` finds a row
filed under `Taxes > Social Security`; the `Income`/`Spend` roots are left out
of that walk, or their names would match every categorised row
(`_spending_categories_below_match`). Combines with every other filter (category, date range, account,
amount sign/threshold). Web: the `#spSearch` search box on the Transactions
tab.

### Rule conditions, priority and preview

Beyond `pattern` + `category`, a rule (`spending_rules`, v32) can carry:

- `portfolio_id` — restrict to one account. Must be a `bank` account
  (`_validate_rule_conditions`, 400 otherwise) — a rule against a brokerage
  or other non-bank portfolio can never match anything, since only bank
  accounts populate `spending_transactions`.
- `amount_sign` (`"positive"`/`"negative"`) — money in vs. money out.
- `min_amount`/`max_amount` — bounds on `abs(amount)`.
- `priority` (default `100`, lower runs first) — evaluation order is
  **priority, then rule id** (`services/spending_rules.rule_sort_key`), not
  insertion order.

A rule matches when its pattern is found in the description **or** the
merchant (case-insensitive substring) and every condition it sets holds
(`services/spending_rules.rule_matches`). `pick_category()` walks matching
rules in priority order; **a match whose category's tree root conflicts with
the transaction's sign (e.g. an Income category on a debit) is skipped, not
treated as a stop** — the search continues to the next rule instead of
falling back straight to `uncategorized`. This applies to imports, rescans
and `/rules/preview` alike.

`PUT /api/v1/spending/rules/{id}` — a condition field present in the body and
set to an explicit `null` **clears** that condition (vs. a field simply
omitted, which leaves it unchanged); `pattern`/`category`/`priority` update
when given. After merging the update onto the existing rule, it's rejected
with 409 if the merged result would be an exact duplicate — same pattern
(case-insensitive), category and all four conditions — of a **different**
rule (`find_duplicate_spending_rule`, excluding the rule being edited itself,
so resaving a rule's own unchanged values is always fine); same duplicate
check `POST /rules` already applies on create.

`POST /api/v1/spending/rules/preview` — rows a candidate rule (not yet saved)
would match, without writing anything: `{pattern, category?, portfolio_id?,
amount_sign?, min_amount?, max_amount?, only_uncategorized=true,
exclude_ids=[]}` → `{match_count, sample}` (first 10 matches). With
`category` given, rows whose sign that category can't hold are excluded, the
same as a real rescan would exclude them. Transfers are never counted. Counts
only what this rule matches on its own — a higher-priority rule could still
claim some of the same rows first on an actual rescan.

### Click-to-edit category + rule offer

Clicking a row's category cell in the Transactions table opens an inline
editor to change it by hand at any time — this and the AI-suggest panel and
bulk "Set category" are the transaction-level recategorization paths (the
old separate pencil-icon editor is gone; edits go through the same click). If
other still-`uncategorized` rows share the same merchant, the UI offers to
create a rule that files all of them in one click (`_offerRuleForMerchant`,
backed by `/rules/preview`) — declining leaves them untouched. Cancelling an
in-progress inline edit keeps any existing bulk-selection checkboxes checked
(they used to get cleared).

### `balance_breaks` on `/upload`

`POST /api/v1/spending/upload` now also returns `balance_rows_checked` (how
many parsed rows carried a `balance` value) and `balance_breaks` — a list of
`{date, description, currency, expected, actual, kind}`, one entry per place
the running balance doesn't follow from the row before it (missing or
duplicated rows are the usual cause). `kind` is `"gap_before_file"` (the
break is against the opening balance carried over from the previous import)
or `"within_file"` (the break is between two rows inside this same file).
Computed by `portf_manager/services/balance_check.find_balance_breaks`, pure
and DB-free. This **never blocks saving** — it's a warning shown in the
import preview, not a validation error, since a break can be a legitimate
gap (e.g. an account not imported before) as easily as a real data problem.

### Recurring charges (`GET /spending/recurring`)

`GET /api/v1/spending/recurring?portfolio_id=&include_ended=false` detects
subscriptions/bills from bank outflows (`services/recurring.py`) and returns
`{items, monthly_total_eur, annual_total_eur}`. Outflows are grouped by
account + merchant + currency, then checked for a steady cadence
(weekly/monthly/quarterly/yearly, each with its own tolerance) and a steady
amount (within 25% of the median, at least 75% of charges "regular" to
qualify); a cadence needs at least 3 occurrences unless it's yearly. Each
series reports `cadence`, `occurrences`, `typical_amount`, `annual_amount`
(+ `annual_amount_eur`), `next_expected`, `status` and `price_change_pct`
(set only for a flat *run* — at least three charges immediately before the
latest one (`PRICE_FLAT_MIN_RUN`: the charge right before the latest plus two
earlier ones, each within 2% (`PRICE_FLAT_TOLERANCE`) of it), walking back
only as far as that run stays flat — when
the latest charge moved ≥5% from the one before it; otherwise `null`, so a
bill that varies month to month, like a utility, never shows a "price rise".
Flatness is judged on that recent run, not the whole history: an earlier
price change further back (e.g. 8 → 10 a year ago) doesn't blind this to a
later one (10 → 12 now). There is no time window here — the 45-day recency
gate below applies only to raising the Action Item, not to this field).
`annual_amount` is `typical_amount * charges/year`, computed from the
already-rounded `typical_amount` so the two figures never visibly disagree
(a raw median landing on a half cent, e.g. 10.005, would otherwise round to
10.0 for display while annualizing the unrounded 10.005).

`status` is the important part: a charge only counts as **`missed`** once a
statement covering the due date has actually been imported for that
account — a missing statement is never read as a cancelled subscription. A
series becomes `active` while the last import is still at or before the next
expected date (plus tolerance); `missed` once an import passes that date
without the charge showing up; **`ended`** only after a *second* consecutive
cycle goes by with no charge (i.e. it survives one missed cycle before being
written off). That second cycle's due date is computed two cadence steps
straight from the *last actual charge*, not by advancing the
already-clamped `next_expected` a second time — chaining two clamped
month-adds from a month-end charge can land a few days short (a monthly
charge on Jan 31 clamps `next_expected` to Feb 28, and advancing *that* a
month lands on Mar 28 rather than the correct Mar 31, calling the series
`ended` up to three days early). Totals (`monthly_total_eur`/
`annual_total_eur`) sum only non-ended series, even when `include_ended=true`
also returns the ended ones in `items`. Web: the Recurring tab on the
Spending page, sorted missed → active → ended, then by annual amount; the
merchant name links to the Transactions tab pre-filtered by search to that
merchant, scoped to the series' own account, with every other transaction
filter (date range, min amount, sign, category) reset first.

### Action Item: missed recurring charges & price rises

`check_recurring_charges` (`portf_manager/services/action_items.py`) adds two
kinds of item, both category `spending`:

- **Missed charge** (severity `medium`) — one per series currently
  `status == "missed"`: "Expected charge from `<merchant>` didn't arrive."
- **Price rise** (severity `low`) — one per `active` series whose latest
  charge is both recent (within `RECURRING_PRICE_RECENT_DAYS`, 45 days) and
  up at least `RECURRING_PRICE_RISE_PCT` (10%) on the one before it. Price
  drops are never flagged.

Both link to the Spending page. Deterministic — no LLM involved. A third,
**returned charge** (`recurring:returned:<portfolio>:<merchant>:<date of the new
charge>`, `low`), is raised when a series marked cancelled or paid elsewhere
(below) charged again; a live resolution raises no missed item.

### Recurring resolutions and subscriptions (2026-11-18)

A series (account + merchant, matched case-insensitively) can be marked
`cancelled` or `paid_elsewhere` (table `recurring_resolutions`, schema v47).
`load_recurring` (never the pure `detect_recurring`) attaches it: a series
whose last charge is on or before the resolution's `since` takes that status
and carries `resolution {status, since, note}`; a charge after `since` leaves
the detected status, keeps `resolution` and sets `resolution_stale: true`.

- **`PUT /spending/recurring/resolution`** `{portfolio_id, merchant, status,
  note?}`: `since` is the series' last charge date, set by the server (400 for
  any other status, 404 when no such series is detected, upsert otherwise).
  **`DELETE /spending/recurring/resolution?portfolio_id=&merchant=`** clears it
  (404 when none).
- **`GET /spending/recurring`** items gain `resolution` and `resolution_stale`;
  parameters `include_resolved` (default true) and `sort` (`status`, the
  default and the old order, now missed, active, paid elsewhere, cancelled,
  ended; or `yearly_cost`, the subscriptions view, dearest first, no second
  endpoint). Totals: `annual_total_eur` = active + missed + paid elsewhere
  (cancelled and ended out; with `include_resolved=false` paid elsewhere is
  out too), `monthly_total_eur` = that / 12, `paid_elsewhere_annual_eur` and
  `cancelled_annual_eur` (what the cancellations save a year) always over every
  resolved series.
- **Effects of a live resolution.** `due_dates` returns nothing for it (as for
  `ended`), so it is not among the forecast's known bills and not in the budget
  pace's recurring charges still due; the review's `recurring.new` and
  `ended` lists skip it; no missed item. Its past rows stay recognised as
  recurring, so they never leak into the everyday baseline. A stale resolution
  changes none of this: the series is live again.

### Category essentiality (v42)

`spending_categories.essentiality` marks a category `fixed`, `flexible` or
`optional` (NULL = not marked). It is inherited from the nearest marked
ancestor and drives "what to cut first" in the what-if scenarios; an unmarked
category is never cut. `GET /spending/categories/tree` returns it,
`PUT /spending/categories/{name}/essentiality` sets or clears it (404 unknown,
400 for a root) and `POST /spending/categories/suggest-essentiality` returns
LLM suggestions for unmarked Spend categories without saving them. Full
description: `scenarios.md` → Spending tiers.

### Cost control: one-offs, comparison, anomalies, tiers, review (2026-11-15, schema v43)

All of it is deterministic (no LLM) and lives in `services/spending_insights.py`
(maths) and `services/spending_review.py` (assembly); the routes are in
`routers/spending.py`. Rows come from `load_rows` (EUR at today's rate,
transfers kept in so "has this month imports" can see them; every function
filters `is_transfer` itself).

- **Shared rules.** Only complete calendar months that have imported rows form
  a baseline; the month in progress never counts in an average. **"Data
  through" is kept per bank account** (the date of its latest imported row, not
  today), and completeness is judged against the **earliest** of those dates
  over the accounts that have rows in the window (one helper:
  `spending_insights.account_data_through` / `effective_data_through`; an
  account with no row since the window start, say a closed one, is ignored
  rather than treated as stale forever; with a `portfolio_id` filter only that
  account counts). It decides whether a month is complete or `partial` (the
  warning names the lagging account), which months are baseline months (a
  month ending after it is not complete), and the elapsed share of the month
  in progress in the forecast and the budget pace. Money moved to `Invest` /
  `House purchase` is not spending: it is reported apart as `savings_eur`.
- **One-off flag (`spending_transactions.one_off`).** `PUT /spending/{id}`
  accepts `one_off` (alone it is a valid body), the list endpoint returns it
  and filters on `one_off=true|false`, and `/trend` adds `one_off_eur` per
  month (already inside `spent_eur`). A one-off row **still counts in every
  total** (summary, trend, budget actuals, the comparison's actual); it is left
  out only of what means "normal": the comparison baseline, the anomaly
  history, the forecast baseline, the budget pace's everyday projection,
  budget seeding and `household_baseline` (which reports what it left out).
- **`GET /spending/compare?month=&baseline_months=6&group_by=category|merchant&portfolio_id=`.**
  Per group: `actual_eur`, `baseline_eur` (average of up to N complete earlier
  months with imports), `last_year_eur` (null when the account has no rows that
  month), `delta_eur`/`delta_pct`, `one_off_eur` and, for categories, the
  `tier`. `month` defaults to the last complete month. `status` is `complete`,
  `in_progress`, `partial` (the data of any account stops before the month ends)
  or `no_imports`; `data_through` is the effective (earliest) date and
  `accounts` lists each account's own; **the deltas are null unless the month is complete**, because
  comparing a part-month or an empty month with a full average flatters the
  result. A baseline of fewer months than asked for says so in `warnings`.
- **`GET /spending/anomalies?days=60`.** Four kinds, each with a stable `id`
  (the same id is the Action Item id, so a "checked" state sticks):
  `large_charge` (at least 3x the merchant's median and 1.5x its largest earlier
  charge, from 50 EUR, after 3 earlier charges), `new_merchant` (a first charge
  from 200 EUR, only when the data reaches back 90 days before it),
  `category_spike` (a complete month's category total at least 50 % and 75 EUR
  over its baseline, with 3 baseline months; `date` is the `YYYY-MM`) and
  `possible_duplicate` (same account, merchant and amount within 3 days, from
  20 EUR). One-off rows are never flagged. The thresholds are module constants
  in `spending_insights.py`. These are flags, not verdicts.
- **`GET /spending/tiers?months=12`.** Per month the fixed / flexible / optional
  / unclassified totals, `savings_eur`, `one_off_eur` and the top categories per
  tier, using the inherited-essentiality resolver (`tier_of`). Every outflow
  counts, so `total_eur + savings_eur` equals `spent_eur` of `/trend` for the
  same month. A month without imports is all zero with `has_imports: false`.
- **`GET /spending/review?month=&portfolio_id=`** (`spending_review.build_review`,
  also the chat tool `get_spending_review` and MCP `spending_review`): one
  payload assembled only from the services above, no maths of its own. Fields:
  `month`, `as_of`, `data_through`, `status`, `totals` (the comparison's status,
  baseline months and totals), `movers` (top 5 categories and top 5 merchants by
  size of move), `anomalies` (those dated inside the month, max 10), `tiers`
  (`month` and `previous`), `recurring` (`new`, `ended`, `missed`,
  `price_change`), `budget` (the active budget's result for the month: section
  totals against `available_total`, tier totals and the lines over budget, or
  null without an active budget), `forecast` (this month's projection, the next
  month's line, the known bills due in the next 45 days, `first_below_zero`),
  `freshness` (data-through per account) and `warnings` (all parts merged).
  Each part is built on its own: one that fails is null with a warning, never a
  5xx. A recurring series is "new" or "ended" when it started or stopped in the
  review month or the two before it (a series needs three charges before it is
  detected), "missed" when its status is `missed` with the due date on or before
  the month's end, and "price_change" when its latest charge fell in the month.
  `month` after the month in progress is a 400. Plain `def` (FX lookups).
- **Action Item:** `check_spending_anomalies` (low severity, last 14 days, max
  8), see `action-items.md`. Web: `web-client.md` → Cost control. The forecast
  is `cashflow-forecast.md`.

### Cost control 2: statement import, uncategorised queue, year view (2026-11-17, schema v46)

- **`POST /spending/import-file`** (multipart `file`, `portfolio_id`): parse with
  the auto-detection, drop rows already stored (the `/upload` duplicate rule,
  against rows stored before the import, never twins inside the file), apply
  rules and transfer matching, and save through the same `_save_rows` as
  `/save`. Returns `{parsed, imported, duplicates, uncategorized, date_from,
  date_to, data_through, balance_check, transfers_settled, warnings}`. **All or
  nothing:** a portfolio that is not a bank account, a file that cannot be
  parsed (400 here, unlike `/upload`'s 422) or parses to nothing is a 400; a row
  that fails to save deletes the rows this call already created and the answer
  is a 500 with the reason; `/save` keeps its partial behaviour. Calls are
  serialised by a module-level lock. `balance_check` is `ok`, `mismatch` (the
  stated balances do not add up with each other or with the balance stored
  before the file: rows may be missing; a warning, never a refusal, and an
  unattended caller should alert on it) or `unknown` (no balance column). If
  transfer settling raises after the rows are saved, the rows stay, the answer
  is 200 with `transfers_settled: false` and a warning (run the transfer
  rescan). A host job can post statements with it.
- **`GET /spending/uncategorized-queue?limit=20&months=12&portfolio_id=`**
  (`services/spending_uncategorized.py`): non-transfer rows with category
  `uncategorized`, grouped per merchant and direction (rows without a merchant
  by normalised description), biggest total first. Group:
  `{key, merchant, direction, count, total_eur, first_date, last_date,
  sample_descriptions, transaction_ids, proposed_pattern, account_names}`;
  `proposed_pattern` is the merchant, else the longest common description
  prefix of at least 5 characters, else null. Top level: `groups`,
  `total_count`, `total_eur`, `share_of_spending_pct`, `window_start`,
  `last_complete_month: {month, count, total_eur, share_pct}`. The shares count
  outflows only; `last_complete_month` is null without a complete month.
- **`POST /spending/uncategorized-queue/classify`** `{transaction_ids, category,
  create_rule, pattern}`: a thin wrapper over the row edit and the rules, with
  the same sign/root check as editing one row. All-or-nothing validation (400
  for an unknown id, a transfer, a wrong sign, `Transfer` as category, or
  `create_rule` without a pattern); rows that already have a category are
  counted in `skipped`. With `create_rule` an identical rule is reused or
  created **before** any row is updated (a rule failure is a 500 and changes
  nothing), then the rules re-scan the other uncategorised rows. Returns `{updated, skipped,
  rule_id, rule_created, recategorized_by_rule}`.
- **Review field.** `GET /spending/review` gains `uncategorized: {count,
  total_eur, share_pct, highlight}` for the reviewed month (null when nothing
  is imported); `highlight` is a line when the share is above 5%. Action Item:
  `action-items.md`.
- **`GET /spending/year?year=&depth=1|2&portfolio_id=`**
  (`services/spending_year.py`). Like for like: only months that are complete
  and have imports in **both** years are summed, on both sides. `totals` has
  `this`, `previous` (cut to the same months) and `previous_full` (every
  imported month of the previous year), each `{income_eur, spending_eur,
  savings_eur, net_eur, savings_rate_pct (null without income), one_off_eur,
  months}`. `months` is `{this, previous}`, 12 entries each with `has_imports`
  and `complete`; a month without imports has `income_eur`, `spending_eur` and
  `savings_eur` null (unknown, named in a warning), never zero. Completeness is
  judged only from the accounts that have rows in the year shown: an account
  closed in the previous year holds nothing back (its rows still count in the
  previous-year sums); an account that imported least is named in a warning.
  An account whose first row falls in the previous year after January, or in
  the year shown, is named too (the like-for-like change is then inflated). `compared_through` / `compared_months` say what was compared.
  `categories` are the top-level Spend categories (children with `depth=2`),
  `movers` `{increases, decreases}` by leaf category, `merchants` the 10
  largest, `one_offs` the one-off rows of the whole year (they count in the
  totals of the compared months). With nothing comparable the totals, categories and deltas are
  null/empty and a warning says so. Plain `def` (FX lookups).
- Clients: chat `get_planned_items`, `get_spending_year`,
  `get_uncategorized_queue`; MCP `planned_items`, `spending_year`,
  `uncategorized_queue` (read-only; classifying and planned items are written in
  the web client).

### Month close (2026-11-18, `services/month_close.py`, table `month_closes`, schema v47)

A sequence for month end and a "done" mark. No maths of its own and no LLM: it assembles `spending_insights`, `spending_uncategorized`, `spending_year`, `recurring` and the budget part of `spending_review`. A close is a mark, not a freeze: nothing is locked.

- **`GET /spending/month-close?month=`** -> `{month, closed, ready, checklist, figures, warnings}`; `month` defaults to the last complete month, one after the month in progress is 400. `closed` is `null` or `{closed_at, notes, report_id, stale}`.
- **Checklist** (each step `{key, label, status: ok|todo|warn|unknown, count, detail, link_page}`): `imports` (every bank account with rows in the 12 months before reaches the month's last day; a month with no rows at all is `todo`; the only step that blocks), `uncategorized` (outflows with no category), `transfers` (unlinked transfer rows in the month, with the pattern-flagged ones counted), `anomalies` (anomaly items dated in the month whose Action Item ack is not checked or snoozed, matched by item id), `recurring` (series due in the month that did not arrive and have no resolution), `one_offs` (a reminder only: the month's up to 5 largest outflows of 200 EUR or more, not marked one-off, from a merchant not charged in the 90 days before, so the rent never shows). A step whose source fails is `unknown` with a warning, and a month with no rows leaves the row-based steps `unknown`. `ready` is true when `imports` is `ok`.
- **`figures`:** `income_eur`, `spending_eur`, `savings_eur`, `net_eur` (income - spending, as in the year view), `savings_rate_pct`, `budget` (active budget: sections planned vs actual; null without one or without activity), `top_categories` (5) and `versus_normal` (the comparison's totals). A month with nothing imported or a part that fails is `null`, never zero.
- **`POST /spending/month-close`** `{month, notes, force}`: 400 for the month in progress or a future one, 409 when not `ready` and not `force`. It stores figures and checklist and writes a Markdown report to `reports` (title `Month close YYYY-MM`, whole euros, from the pure `render_month_close_report(month, figures, checklist)`). Closing again replaces the row and updates the same report; an omitted `notes` keeps the earlier one.
- **`DELETE /spending/month-close?month=`** reopens (404 when not closed); the saved report stays. These routes sit before `/{spending_id}`.
- **`closed.stale`:** the figures recomputed now differ from the stored ones by more than 1 EUR on income, spending or net (rows imported or recategorised after the close).
- **Action Item** `spending:month-close:<YYYY-MM>`: see `action-items.md`.
