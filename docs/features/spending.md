# Spending tracking (bank accounts)

> Moved out of `CLAUDE.md` on 2026-09-25 so it isn't loaded into every session. CLAUDE.md keeps the must-know gotchas and links here; this file is the full reference. "See the X section" means the matching file in `docs/features/`, or CLAUDE.md.

### Spending Tracking (`portf_server/routers/spending.py` + `portf_manager/parsers/generic_bank_csv_parser.py` + `portf_manager/services/transfer_matcher.py`)

Bank-account spending tracking, kept deliberately separate from the investment `transactions`/`bookings` tables — a bank statement row has no asset/quantity/price and uses different dedup + transfer semantics. A bank account is still a `portfolios` row (`account_type='bank'`) so it gets the existing broker/account list machinery for free; Holdings/Rebalance/position endpoints need no filtering change since they source only from `transactions`, which bank accounts never populate.

- `POST /api/v1/spending/upload` — multipart file + `account_portfolio_id` or `account_name` (auto-creates via `get_or_create_portfolio(..., account_type='bank')`) → parsed + rule-categorized preview, no DB write. Uses `generic_bank_csv_parser.parse_generic_bank_csv` (required cols `date, description, amount`; optional `balance, currency`; EN/ES/NL header synonyms; reuses `generic_csv_parser.py`'s EU/US delimiter/date/decimal detection helpers rather than duplicating them).
- `POST /api/v1/spending/suggest-categories` — LLM-assisted category + rule-pattern suggestions for rows no rule matched, via `get_llm_client().generate()`. Explicit user-triggered button, not automatic (LLM calls are slow/costly). Accepting a suggestion in the UI creates a new `spending_rules` row so future imports auto-match — rules only grow from confirmed human decisions.
- `POST /api/v1/spending/save` — writes `spending_transactions`, honoring `duplicate_action` (skip/add/overwrite, dedup on portfolio+date+amount+description via `find_duplicate_spending_transaction`), then runs transfer auto-linking over the newly-saved batch.
- `GET /api/v1/spending/` (filters: `portfolio_id`, `category`, `categories` [repeatable, `IN`-matched — e.g. `?categories=A&categories=B`; omitted/empty means unfiltered], `start_date`, `end_date`, `is_transfer`, `amount_sign` [`positive`|`negative`, 400 on any other value], `min_abs_amount` [`ABS(amount) >= min_abs_amount`, composes with `amount_sign` or alone]; paginated/sorted server-side via `limit`/`offset`/`sort_by` [`date`|`portfolio_name`|`description`|`category`|`amount`]/`sort_dir` [`asc`|`desc`] query params — response shape is `{"items": [...], "total": N}`, not a bare array. Web: the Spending page's Transactions tab filter row has a checkbox-dropdown multi-select for category (Select all/none + the "N categories" button label) and an Amount cell (Expenses-only/Income-only sign toggles + a "≥ amount" threshold field) — unchecking every category resolves to zero results client-side with no network call, checking every category is treated as unfiltered.), `GET/POST/PUT /api/v1/spending/categories` (`GET` returns the deduplicated union of categories used on transactions, used on rules, and explicitly registered in the new `spending_categories` table; `POST` creates a bare unused category — 400 on a blank name, 409 on an exact duplicate; `PUT /api/v1/spending/categories/{old_name}` renames the category everywhere — every transaction, every rule, and the registry — 400 on a blank or same-old/new name, merging into an existing name without erroring if you rename onto one that's already registered), `PUT /api/v1/spending/{id}` (category only — see the `DELETE` bullet below for its transfer-clearing behavior), `GET/POST /api/v1/spending/rules` (`POST` rejects an exact duplicate — case-insensitive pattern match + exact category match against an existing rule — with 409), `PUT /api/v1/spending/rules/{id}` (edit an existing rule's pattern/category — the Rules card on the Spending page now has an edit-in-place pencil icon per row, not just add/delete), `DELETE /api/v1/spending/rules/{id}`, `GET /api/v1/spending/summary?days=30` (EUR-converted spent/income/transferred + by-category breakdown, powers both the Spending page cards and the Net Worth "Actual" widget), `POST /api/v1/spending/rescan-transfers`.
- `POST /api/v1/spending/rescan-categories` — re-applies current `spending_rules` to still-`uncategorized` rows, same on-demand pattern as `/rescan-transfers`; never touches a row that already has a non-`uncategorized` category, so it's safe to run after adding/editing rules without risk of overwriting a manually-set or previously-matched category. Optional JSON body `{"ids": [...]}` scopes it to just those rows (still only the ones among them still `uncategorized`) instead of every uncategorized row account-wide; an absent/empty body keeps the original all-rows behavior. Web: "Rescan categories" button next to "Re-scan transfers" on the Spending page (global, no `ids`); the Spending page's Rules card auto-calls this (global) right after "Add" creates a rule, reporting the match count in `#spRuleStatus`; the bulk-select bar's new "Apply rules to selected" button (`#spBulkApplyRulesBtn`) calls it scoped to the checked row ids.
- **AI category suggestions on already-imported rows**: the Spending page's bulk-select checkboxes (used for bulk recategorize/delete) also drive a "Suggest categories (AI)" action — calls the existing `/suggest-categories` endpoint (unchanged; already generic, not import-specific) against the selected `uncategorized` rows, deduplicated client-side by description (`dedupSpendingRowsByDescription` in `pfm_features.js`) to keep the LLM call small when many rows share a merchant. Results open in a review panel (`#spSuggestReviewPanel`, editable per-suggestion category dropdown + include/exclude checkbox) — nothing is written until "Apply", at which point each accepted suggestion both updates the matching row(s)' category and creates a new `spending_rules` row (same "accept creates a rule" behavior as the import flow; duplicate pattern+category rules are deduped client-side within the batch via `_ruleDedupKey` and rejected server-side with 409 by `POST /api/v1/spending/rules` either way), then Apply automatically calls `POST /api/v1/spending/rescan-categories` once so any *other* already-imported row still `uncategorized` that matches one of the newly-created (or already-existing) rules gets swept up in the same click, not just the rows that were part of this suggestion batch — the status message reports both counts ("Applied to N row(s). Rescan matched M more rows."). Each click is capped to `SP_AI_SUGGEST_BATCH_SIZE` (30) unique descriptions — a real account's uncategorized backlog can have 1000+ unique descriptions, and sending them all in one request risks exceeding `portf_web`'s nginx `proxy_read_timeout` (200s) with an opaque 502 and nothing logged server-side, since the request never completes. The status message states "Sent X of Y..." when the cap applies. A "Select all uncategorized" button (`#spSelectAllUncategorized`) sets the Category filter to `uncategorized` and selects every now-filtered row in one click, so working through a large backlog is: select-all-uncategorized → suggest (batch of 30) → review → Apply → repeat (no auto-chaining needed — applying a batch removes those rows from the uncategorized filter, so the next select-all click naturally surfaces a smaller remaining set). The review panel also has an editable pattern field per suggestion, allowing AI-suggested patterns to be corrected before they become rules. Category fields throughout the Spending page (the per-row table, the bulk-recategorize field, and this AI-suggest review panel) are now free-text inputs backed by a shared autocomplete `<datalist>` of existing categories, so a brand-new category can be typed directly in any of them instead of only via the separate Add Rule form; `PUT /api/v1/spending/{id}` also now rejects a blank category with a 400. The Spending page is now split into Transactions/Categories/Rules/Recurring/Analytics tabs (`#spTabs`); see "Click-to-edit category + rule offer" below for the Transactions tab's category column. The Categories tab holds a Chart.js category-breakdown chart (top 8 by 30-day amount, with a "Show all" toggle and a Bar/Pie type toggle — pie mode reuses the Dashboard donut's color palette, and the canvas sits in a fixed-height wrapper with `maintainAspectRatio: false` to avoid Chart.js's larger default aspect ratio for pie/doughnut types) plus the category-rename (cascading to every transaction, rule, and the registry), add-a-bare-category UI, and an A-Z/Z-A sort toggle on the category list, backed by the new `spending_categories` registry table (db v27). The Rules tab's Pattern/Category column headers are click-to-sort (client-side, asc/desc toggle with an arrow indicator) since all rules are loaded unpaginated.
- **Transfer auto-linking** (`transfer_matcher.find_all_transfer_matches`, pure/DB-free): an outflow matches an inflow of the same absolute amount + currency within ±3 days in a *different* account — either another bank account's unlinked `spending_transactions` row, or an existing brokerage `bookings` Deposit (covers "transfer to Indexa/MyInvestor" — `bookings` itself is never modified, only the spending side gets `transfer_link_type='booking'`). Matched rows get `category='Transfer'`, `is_transfer=1`. Runs after every `/save` and on-demand via `/rescan-transfers` (covers importing the matching leg later, from a different account's statement). `spending_transactions` rows self-exclude from the matcher's unlinked pool once `is_transfer=1`, but `bookings` rows have no such flag — `_run_transfer_matching` (`spending.py`) therefore filters `deposit_bookings` down to exclude any booking id already present as a `transfer_link_id` (`transfer_link_type='booking'`) on an existing linked spending row *before* calling the matcher, so a Deposit booking already claimed in an earlier `/save`/`/rescan-transfers` call can't be wrongly claimed a second time by an unrelated later outflow of the same amount.
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
- **`PUT /api/v1/spending/{id}` also takes `is_transfer`** — both fields are optional, and passing neither is a 400. The transfer matcher can only pair a counterpart that was actually **imported**, so a genuine move to an account you don't track (or one whose statement you haven't loaded) otherwise counts as spending forever with no way to say otherwise. `is_transfer: true` sets the flag, defaults `category` to `"Transfer"` to match what the matcher does, and writes **no link** (there is no counterpart row to point at) — so every surface that already excludes transfers (`/summary`, `/trend`, and the Budget) excludes it too, keeping the Budget↔Spending reconciliation intact. `is_transfer=1` also self-excludes from `list_unlinked_spending_transactions`, so a later rescan can't reclaim a row the user already settled. Setting a category to `"Transfer"` still does **not** by itself set the flag — that stays explicit. Web: "Mark as transfer" / "Not a transfer" in the Spending page's bulk-action bar (`#spBulkMarkTransferBtn`/`#spBulkUnmarkTransferBtn`), same select-rows-then-act pattern as bulk recategorize/delete.
- ⚠️ **Un-transferring a row now resets its counterpart too**, via the new `db.reset_transfer_counterpart(spending_id)` — shared by `DELETE`, by recategorizing away from `"Transfer"`, and by `is_transfer: false`. Previously only the delete path did this and the recategorize path documented leaving the other leg alone as "a known, accepted limitation"; that stranded the survivor with `is_transfer=1` and a link back to a row that no longer claimed it, permanently excluded from spending totals and invisible to rescan-transfers. Only a genuine **reciprocal** link is reset (the counterpart's own `transfer_link_id` must point back), never a coincidental one.
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

`merchant` feeds three surfaces: search (`q` below matches description OR
merchant), rule matching (a rule's pattern is checked against both), and
recurring-charge grouping (`services/recurring.py` groups by
`portfolio_id + merchant.upper() + currency`, so charges from the same shop
group together even when the raw description varies run to run). The
Transactions table's description cell shows the merchant name with the raw
description available underneath/on hover, rather than only the raw text.

### Search (`q` on `GET /spending/`)

`GET /api/v1/spending/?q=<text>` matches `description` OR `merchant`,
case-insensitively and **literally** (a plain substring match, no wildcards
or regex). Combines with every other filter (category, date range, account,
amount sign/threshold). Web: the `#spSearch` search box on the Transactions
tab.

### Rule conditions, priority and preview

Beyond `pattern` + `category`, a rule (`spending_rules`, v32) can carry:

- `portfolio_id` — restrict to one account.
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
when given.

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
(set whenever the latest charge moved ≥5% from the one before it, with no
time window — the 45-day recency gate below applies only to raising the
Action Item, not to this field).

`status` is the important part: a charge only counts as **`missed`** once a
statement covering the due date has actually been imported for that
account — a missing statement is never read as a cancelled subscription. A
series becomes `active` while the last import is still at or before the next
expected date (plus tolerance); `missed` once an import passes that date
without the charge showing up; **`ended`** only after a *second* consecutive
cycle goes by with no charge (i.e. it survives one missed cycle before being
written off). Totals (`monthly_total_eur`/`annual_total_eur`) sum only
non-ended series. Web: the Recurring tab on the Spending page, sorted
missed → active → ended, then by annual amount; the merchant name links to
the Transactions tab pre-filtered by search to that merchant.

### Action Item: missed recurring charges & price rises

`check_recurring_charges` (`portf_manager/services/action_items.py`) adds two
kinds of item, both category `spending`:

- **Missed charge** (severity `medium`) — one per series currently
  `status == "missed"`: "Expected charge from `<merchant>` didn't arrive."
- **Price rise** (severity `low`) — one per `active` series whose latest
  charge is both recent (within `RECURRING_PRICE_RECENT_DAYS`, 45 days) and
  up at least `RECURRING_PRICE_RISE_PCT` (10%) on the one before it. Price
  drops are never flagged.

Both link to the Spending page. Deterministic — no LLM involved.
