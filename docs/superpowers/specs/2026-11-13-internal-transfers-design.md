# Internal transfers vs real income and expenses — design

Date: 2026-11-13. Extends the Spending feature (`docs/features/spending.md`).
No LLM involved; everything here is deterministic.

## Intent

The owner reads the monthly spending view (and asks the chat) to answer:
what really came in, what really went out, where an overshoot comes from and
whether it is regular. That only works if money moving between the owner's
own accounts never shows up as income or as an expense.

It did show up. A month's income read about one and a half times its real
value because a broker withdrawal that landed in a bank account was counted
as income. Looking into it found four separate holes:

1. **The matcher is one-directional.** A bank outflow can be paired with a
   broker `Deposit` booking, but a bank inflow is never paired with a broker
   `Withdrawal` booking.
2. **Matching only runs when bank rows are saved.** When the broker booking
   is imported after the bank statement (the usual order), nothing re-runs
   the pairing, so the bank row stays unlinked until someone presses
   "Re-scan transfers".
3. **A row can be in the "Transfer" category without `is_transfer`.** A
   category rule can assign "Transfer", but every total goes by the flag, so
   the row still counts as income or spending while looking settled.
4. **Rules that target "Transfer" by a generic bank word** (for example the
   bank's word for "wire transfer") also catch payments to other people,
   which are real expenses.

## Definitions (decided by the owner)

- **Income:** money arriving from anyone who is not the owner. A partner's
  payment into a joint account is income.
- **Expense:** money leaving to anyone who is not the owner, including a bank
  transfer to another person.
- **Internal transfer:** a move between the owner's own accounts, bank or
  brokerage, in either direction. It is neither income nor expense.

"Net of tax" is not part of this. Tax payments stay ordinary expenses.

## Goals

- Every internal move with both legs in pfm is linked automatically, whichever
  leg is imported first and whichever direction the money goes.
- A move whose other leg can never be imported (a closed or untracked own
  account) is still recognised, from a short owner-maintained list of text
  patterns.
- "Transfer" category and `is_transfer` can no longer disagree.
- The owner is told when a recent transfer looks unsettled.
- Monthly trend, summary, budget, recurring detection and the chat tools need
  no change of their own: they already filter on `is_transfer`.

## Non-goals

- No counterparty model (self / partner / external per row) and no
  double-entry ledger.
- No split matching (one outflow against two inflows) and no fuzzy amounts.
- No change to `bookings`: it is never modified, as today.
- No partner-specific handling. Partner payments are plain income.
- No new MCP tool.

## Anti-goals

- A payment to another person must never be hidden as a transfer. When in
  doubt a row counts as income or expense and is shown for review.
- Nothing may guess silently. A row flagged without a counterpart records why.

## 1. Complete the pair matching

`portf_manager/services/transfer_matcher.py` (stays pure, no DB):

- A bank **inflow** may match a `Withdrawal` booking in a different
  portfolio: same currency, equal absolute amount, within the existing
  ±3-day window. This mirrors the existing outflow ↔ `Deposit` rule. Spending
  ↔ spending candidates are still tried first.
- The candidate pool for the *counterpart* side also includes rows that are
  already `is_transfer = 1` but have no link (flagged by pattern or by hand).
  When the real other leg turns up later it links to them, and both rows end
  up linked with `transfer_source = 'match'`. Rows that already have a link
  are never candidates.
- The same holds for the *source* side: any row without a link is matched,
  flagged or not. So a bank row flagged by pattern or by hand is upgraded to a
  linked pair when its booking or its other bank leg arrives later. Note that
  today `find_all_transfer_matches` skips `is_transfer` rows and
  `list_unlinked_spending_transactions()` means `is_transfer = 0`; both need
  to mean "no link" instead (the exception is `rejected`, section 4).
- Ties: when several candidates fit, the closest date wins, then the lowest
  id. (Author's call.)

The existing guard that a booking already claimed by one spending row cannot
be claimed by another applies to withdrawals as well.

## 2. Re-run matching when bookings arrive

Move `_run_transfer_matching` out of the spending router into a service
(suggested `portf_manager/services/transfer_linking.py`) so non-spending code
can call it. Call it after bookings are created on every path:

- `POST /bookings/` (`routers/bookings.py`)
- the web import save (`routers/imports.py`)
- the PDT pull (`routers/sync.py`)
- the CLI's direct-DB booking import (`cli.py`); in server mode the CLI goes
  through the HTTP paths above and needs nothing of its own

The run may be scoped to unlinked bank rows within the match window of the
new bookings; a full rescan is also acceptable if it stays fast on a few
thousand rows. A failure in the re-match must not fail the booking import: log
it and carry on (the import is the user's action; the re-match is a follow-up).

`POST /spending/rescan-transfers` keeps working and now also applies section 3.

## 3. Own-account patterns

For moves with only one leg in pfm.

- Stored in `app_settings` under `own_account_patterns`: a JSON list of
  strings. Matching is a case-insensitive substring test against the row's
  description and merchant, like spending rules. Blank patterns are rejected.
  A minimum length of 4 characters guards against a pattern that matches
  everything.
- Endpoints under `/api/v1/spending/own-account-patterns`: `GET` (list),
  `PUT` (replace the whole list), and `POST .../preview` taking a candidate
  pattern and returning the unflagged rows it would flag (count plus the first
  rows), so the owner sees the effect before saving. Register these before any
  `/{id}` route.
- Applied **after** pair matching, on every save and every rescan: an
  unflagged row that matches a pattern gets `is_transfer = 1`,
  `category = 'Transfer'`, no link, `transfer_source = 'pattern'`. On save a
  pattern wins over a category rule.
- A pattern also flags an existing row that already has another category; the
  old category is not kept (the pair matcher behaves the same today). This is
  why the preview exists: it lists the affected rows with their current
  category before the owner saves. (Author's call.)
- A pattern never flags a row the owner un-marked (`transfer_source =
  'rejected'`, section 4).
- Saving a changed list applies it to existing unflagged rows straight away
  and reports how many rows were flagged.
- Removing a pattern does **not** un-flag rows it flagged earlier; the owner
  un-marks those by hand if wanted. (Builder's call how to word this in the
  UI.)
- Web: a small "My own accounts" card next to the Rules card on the Spending
  page: the list, add/remove, and the preview count before saving. Help text
  says what belongs here (your own name as the bank prints it, labels of your
  own standing orders) and what does not (a partner's name, a generic bank
  word for "transfer", a tax id that also appears on tax payments).

This is the author's proposal, accepted by the owner: the list is separate
from category rules on purpose, so that "who is me" is one short list and not
spread over rules.

## 4. "Transfer" category and the flag always agree

- New column `spending_transactions.transfer_source` (TEXT, nullable; one of
  `match`, `pattern`, `manual`, `rejected`). Schema version 41. Migration backfill:
  flagged rows with a link → `match`; flagged rows without one → `manual`.
  Add it to `_create_all_tables` as well as the migration.
  (Author's call: a column, because `transfer_link_type` has a CHECK
  constraint and a NULL link already means "by hand" for existing rows.)
- `PUT /spending/{id}` with `category: "Transfer"` now also sets
  `is_transfer = 1` with `transfer_source = 'manual'`. This reverses the
  documented "category alone does not set the flag" behaviour. Marking by
  `is_transfer: true` sets `manual` too. Un-marking a row (`is_transfer: false`, or
  recategorising away from "Transfer") sets its source to `rejected`, a fourth
  value meaning "the owner said this is not a transfer": patterns skip it from
  then on, so un-marking sticks. An exact pair match may still link a
  `rejected` row, as a rescan can today. The counterpart reset by
  `reset_transfer_counterpart` gets its source cleared (NULL), not `rejected`.
- "Transfer" is not in the category tree (it has no root), so the sign check
  on `PUT` should already pass for both signs; the builder confirms this, and
  the literal `uncategorized` used by the repair below, against the code.
- A spending rule may no longer target "Transfer": `POST` and `PUT` on rules
  return 400 with a message pointing to the own-account patterns. Rule
  evaluation skips any stored rule whose category is "Transfer", so an old
  row in `spending_rules` can do no harm.
- The same migration (v41) repairs existing data generically: any row with
  `category = 'Transfer'` and `is_transfer = 0` is set back to
  `uncategorized`. It then counts as income or expense, visibly, until the
  matcher, a pattern or the owner settles it. This is the safe direction (see
  anti-goals). Existing rules targeting "Transfer" are left in the table but
  inert; the migration does not delete user rules.
- The API response for a spending row gains `transfer_source`. The
  Transactions table shows unlinked transfers with a small "no counterpart"
  marker (tooltip: flagged by pattern / by hand), and the transfer filter
  gains a "without counterpart" option.

## 5. Action Items

Two checks in `portf_manager/services/action_items.py`, each wrapped
independently like the others, ids deterministic per entity:

- **Pattern-flagged transfer without a counterpart, recent.** A row with
  `transfer_source = 'pattern'`, no link, dated within the last 90 days and at
  least 3 days old (the match window). Since every current own account is
  tracked, this means a missing import or a wrong pattern. Older rows stay
  quiet; they are the closed-account history and are reachable through the
  "without counterpart" filter. Rows marked by hand never raise an item.
- **Broker booking with no bank leg.** A `Deposit` or `Withdrawal` booking in
  the last 90 days, of at least 100 in its currency, that no spending row
  links to, where some bank account has imported rows dated after the booking
  date plus the match window (so the bank leg should have been seen by now).
  Bookings do not say which bank they came from, so the test is conservative:
  *every* bank account with any row in the last 90 days must have a row dated
  after the booking date plus the window. (Author's call.)
  The usual cause is a bank row counted as income or expense, or a date gap
  wider than the window. The item names the booking and suggests looking for
  the amount in the Transactions tab.

Both thresholds (90 days, 100) are module constants. Checking an item off uses
the existing server-side acknowledgement.

## 6. Live data correction (not part of the repo)

After deploy, a one-off correction of the owner's own data, done through the
API and shown to the owner as a list before anything is written:

- run the rescan (links the rows that holes 1 and 2 missed);
- propose the initial own-account patterns from the owner's existing
  "Transfer" rules, minus the generic one;
- list the rows the migration set back to `uncategorized`, with a proposed
  outcome for each (transfer by pattern, or a real expense category), for the
  owner to confirm row by row where it is not obvious.

No real names, descriptions or amounts from this step go into the repo, the
tests or the docs.

## Testing

- Matcher unit tests: inflow ↔ withdrawal; unlinked-flagged row as a
  counterpart; a claimed withdrawal is not claimed twice; sign, currency,
  window and same-portfolio exclusions for the new direction.
- Linking service: booking created after the bank row links it, on each of
  the four booking paths (router-level tests for the three HTTP paths, a unit
  test for the CLI path); a failing re-match does not fail the import.
- Own-account patterns: flag on save and on rescan; pair matching wins over a
  pattern; later leg links to a pattern-flagged row; preview counts; blank and
  too-short patterns rejected; removing a pattern leaves rows flagged.
- Invariant: category "Transfer" via PUT sets the flag; rules cannot target
  "Transfer"; an inert stored rule is skipped; migration backfill and repair
  on a v40 database; fresh database has the column.
- Action Items: each check fires and stays quiet at its boundaries (age,
  amount, bank coverage, manual source).
- Budget ↔ `/spending/trend` reconciliation test still passes.
- Fixtures use invented names and round amounts only.

## Docs

`docs/features/spending.md` (matching, patterns, invariant, the reversed PUT
behaviour), `docs/features/action-items.md` (two checks), `PROJECT_STATUS.md`,
`help_text.js` for the new card, and in `CLAUDE.md` only: schema v41 in the
migration list and the Spending gotcha line (category "Transfer" always
carries the flag; rules cannot target it; booking imports re-run matching).

## Left to the builder

- Whether the re-match after a booking import is scoped or a full rescan.
- Exact endpoint and function names, and the layout of the new card.
- Wording of Action Item titles and of the 400 message on rules.
