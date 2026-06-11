# Deposit/withdrawal import support (Coinbase + Mintos)

**Date:** 2026-06-11
**Status:** Approved design (pending spec review)
**Scope:** Capture cash deposits/withdrawals on file import for the two brokers
that currently drop them — Coinbase (fiat only) and Mintos — and document the
others as audited/adequate.

## Context & goal

A file import produces two things: trade transactions and cash **bookings**
(deposits/withdrawals). The booking pipeline already exists end-to-end:
`PreviewBooking` → preview → `/import/save` resolves `broker` → portfolio
(`get_or_create_portfolio`) and dedups via `find_duplicate_booking`. Several
parsers emit bookings; two don't.

**Coinbase** classifies `Deposit`/`Withdrawal` as `REFERENCE_TYPES` and skips
them — `_parse_coinbase` doesn't even return bookings — so fiat deposits never
import. **Mintos** keeps only interest rows; deposits/withdrawals are ignored.

Goal: both brokers emit deposit/withdrawal bookings, following the established
parser-emits-bookings pattern, so they flow through the existing preview/save/
dedup path and land in the correct broker portfolio.

## Audit (all brokers)

| Broker | Today | Change |
|---|---|---|
| Coinbase | ❌ Deposit/Withdrawal skipped | **Add (fiat only)** |
| Mintos | ❌ only interest kept | **Add** |
| MyInvestor | ✅ deposit+withdrawal by sign on INVEST/INGRESO/APORTACIÓN (`_DEPOSIT_CONCEPTS`) | none |
| Indexa | ✅ SEPA transfers → deposit/withdrawal by sign | none |
| PDT | ✅ Bookings sheet | none |
| Generic CSV | ✅ `parsers/bookings_csv_parser.py` | none |

## Approach

Each parser emits a `bookings` list of dicts (`{date, action, amount, currency}`);
the import upload endpoint converts them to `PreviewBooking` and wires them into
the response — identical to how `parse_myinvestor_csv` → `_parse_myinvestor`
already works. Rejected alternative: classifying rows in the router — parsers are
unit-testable in isolation and already own per-broker row semantics.

## Components

### 1. Coinbase parser — `portf_manager/parsers/coinbase_csv_parser.py`

- Add `bookings: List[dict] = field(default_factory=list)` to
  `CoinbaseParseResult` (dataclass).
- Add a fiat-currency allowlist constant:
  `FIAT_CURRENCIES = {"EUR", "USD", "GBP", "CHF", "SEK", "DKK", "NOK", "JPY", "CAD", "AUD"}`.
- Add `DEPOSIT_TYPES = {"Deposit", "Pro Deposit"}` and
  `WITHDRAWAL_TYPES = {"Withdrawal", "Pro Withdrawal"}`.
- In `parse_csv_content`'s row loop: before the existing transaction parse, if
  `Transaction Type` is a deposit/withdrawal type **and** `Asset` is in
  `FIAT_CURRENCIES`, append a booking dict
  `{"date": <Timestamp[:10]>, "action": "Deposit"|"Withdrawal", "amount": abs(amount), "currency": Asset}`
  and `continue` (don't fall through to trade parsing). Amount source: the fiat
  `Quantity Transacted` (it's the cash amount; fall back to `Total ...` if blank),
  parsed with the existing currency-symbol/comma cleaning.
- A deposit/withdrawal whose `Asset` is **not** fiat (crypto received/sent), plus
  `Send`/`Receive`/`Retail Staking Transfer`, stay in `skipped` with their
  current reason (extend `_get_skip_reason` text so a skipped crypto transfer
  reads clearly, e.g. "crypto transfer — not a cash booking").
- `parse_csv_content` returns the result with `bookings` populated.

### 2. Coinbase router wiring — `portf_server/routers/imports.py`

- `_parse_coinbase` returns `(previews, bookings, skipped)` where
  `bookings = [PreviewBooking(broker="Coinbase", **bk) for bk in result.bookings]`.
  (`previews` already carry `broker="Coinbase"`.)
- Update the upload dispatch (currently `previews, skipped = _parse_coinbase(content); bookings = []`)
  to `previews, bookings, skipped = _parse_coinbase(content)`.

### 3. Mintos parser — `portf_manager/parsers/mintos_csv_parser.py`

- Add `bookings: List[dict] = field(default_factory=list)` to its result.
- In the row loop, classify `Tipo de pago` (payment type): keywords
  *deposit*/*depósito*/*incoming client* → `Deposit`; *withdrawal*/*retirada*/
  *outgoing* → `Withdrawal`. Amount from `Volumen de negocios` (`abs`),
  currency from `Divisa` (default EUR), date from `Fecha[:10]`. Append
  `{date, action, amount, currency}`. Keep these **individual** (deposits are
  rare, unlike the 20k interest rows). Don't double-count: a row matched as a
  deposit/withdrawal is not also counted in `ignored_summary`.
- `_parse_mintos` (currently returns `(previews, [], skipped)`): build
  `bookings = [PreviewBooking(broker="Mintos", **bk) for bk in result.bookings]`
  and return them.

**Assumption to verify:** the exact Spanish `Tipo de pago` strings Mintos uses
for deposits/withdrawals. The keyword set above is a reasonable guess; confirm
against a real statement. Unmatched payment types remain summarised in
`ignored_summary`/skipped, so a wrong guess fails safe (just doesn't import that
deposit) rather than mis-importing.

### 4. No change (documented as audited)

MyInvestor, Indexa, PDT, generic — already emit deposit+withdrawal bookings.
Minor potential gaps (MyInvestor withdrawal-specific concepts beyond a negative
INVEST; Indexa non-SEPA deposits) are **out of scope** — no evidence of missed
rows; revisit only if a real file shows one (YAGNI).

## Data flow, dedup, portfolio

No new save logic. Bookings flow through the existing `/import/save`:
`broker` → `get_or_create_portfolio` tags them to the right portfolio (Coinbase
deposits land in the Coinbase portfolio, consistent with the broker fix), and
`find_duplicate_booking` (date+action+amount+currency+portfolio) makes re-import
idempotent.

## Testing

**Parser unit tests:**
- Coinbase (`tests/unit/test_imports_exports.py` or a parser test): a CSV with a
  fiat `Deposit` (Asset=EUR) → `bookings` has one Deposit with that amount/EUR; a
  crypto `Receive` (Asset=BTC) → `skipped`, not a booking; a fiat `Withdrawal` →
  Withdrawal booking. Existing buy/sell + skipped-count assertions still pass.
- Mintos: a statement with a deposit row + interest rows → `bookings` has the
  deposit while interest is still aggregated into `interest` entries.

**Import-upload test:** `POST /import/upload` with a Coinbase CSV containing a
fiat deposit → response `bookings[0]` has `action="Deposit"`, the amount, and
`broker="Coinbase"`.

## Out of scope
- Crypto `Send`/`Receive` modelling (wallet transfers) — stays skipped.
- MyInvestor/Indexa deposit-keyword expansion (no evidence needed).
- Aggregating Mintos deposits (kept individual).
