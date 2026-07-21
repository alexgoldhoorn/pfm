# AEB43/N43 Bank Statement Parser

**Date:** 2026-07-21
**Status:** Approved

## Problem

Spending Tracking's bank-statement import (`POST /api/v1/spending/upload`) only
understands delimited CSV (`generic_bank_csv_parser.py`). Caixa Enginyers and
Abanca both offer AEB43/Norma 43 ("Cuaderno 43") exports — a fixed-width,
80-byte-record national standard used by most Spanish banks — as an
alternative or sole export option. Neither the CLAUDE.md-documented Caixa
Enginyers export options (Excel or AEB43 text) work with the current
importer: the "Excel" option is binary and the upload endpoint only ever
`.decode("utf-8-sig")`s the file, and AEB43 is fixed-width, not delimited, so
`generic_bank_csv_parser` can't parse it either.

Two real sample exports (Caixa Enginyers `.Q43`, Abanca `.n43`) were used to
reverse-engineer and cross-validate the exact field layout — every
debit/credit count and sum, and a computed running balance, matched each
file's own trailer record to the cent for both banks.

## Scope

- New parser module for AEB43/Norma 43, wired into the existing Spending
  bank-statement upload with **automatic content detection** — no new API
  parameter, no new UI control. `generic_bank_csv_parser` remains the fallback
  for every non-AEB43 file, unchanged.
- Bank-agnostic by design (the format is a national standard, not
  Caixa-Enginyers-specific) — validated against two independent real bank
  exports (Caixa Enginyers, Abanca) with zero bank-specific branching.
- Computes a genuine running `balance` per row from the header's opening
  balance plus each movement's signed amount — this format uniquely allows
  it, unlike most bank CSV exports.
- **Not doing**: a manual format-selector UI (auto-detect only, per decision
  below). Fixing mangled legacy-codepage accented characters some exports
  contain (e.g. `VÌctor` for `Víctor`) — decoded as-is via `latin-1`, same
  best-effort behavior as the existing CSV parser for unusual encodings.
  Parsing/storing the trailer's totals anywhere — used only as an in-memory
  sanity check during development, not part of the shipped parser's output.

## Format reference

Fixed-width records, 80 bytes each when un-trimmed; CRLF line endings. Record
type = first 2 characters. Caixa Enginyers pads every line to 80 bytes;
Abanca's export **trims trailing whitespace per line** (movement records as
short as 70 bytes) — the parser left-pads (`.ljust(80)`) every line before
slicing so both are handled identically.

| Record | Purpose | Occurs |
|---|---|---|
| `11` | Header: entity/office/account codes, statement start/end dates (`AAMMDD`), opening balance + debit/credit flag, currency (ISO 4217 numeric) | once, first line |
| `22` | Movement: operation date, value date, debit/credit flag, amount | once per transaction |
| `23` | Complementary text (código dato `01` = free-text description) | 1+ per movement, immediately follows its `22` |
| `33` | Trailer: totals (debit/credit counts+sums, final balance) | once, last line — read only for validation during development |
| other (e.g. `88`) | Vendor-specific sentinel, not part of the AEB43 standard | ignored |

Field offsets (1-indexed, inclusive):

**Header (`11`)**: `3-6` entidad, `7-10` oficina, `11-20` cuenta, `21-26`
fecha inicio, `27-32` fecha final, `33` clave debe/haber inicial (`1`=debe,
`2`=haber), `34-47` importe saldo inicial (14 digits, 2 implied decimals),
`48-50` divisa (ISO 4217 numeric — `978`=EUR), `52-80` nombre (may be blank
or zero-filled).

**Movement (`22`)**: `11-16` fecha operación (`AAMMDD`), `17-22` fecha valor,
`28` clave debe/haber (`1`=debe/out, `2`=haber/in), `29-42` importe (14
digits, 2 implied decimals). Entidad/oficina/concepto/num-documento/
referencia fields are not needed — description comes from the following `23`
record(s).

**Complementary (`23`)**: `3-4` código dato, `5-80` free text. A movement may
have zero or more `23` records; all with código `01` (or any code, to be
lenient) are concatenated in order to form the description.

**Trailer (`33`)**: `21-25` num. movimientos debe, `26-39` importe debe (14
digits), `40-44` num. movimientos haber, `45-58` importe haber (14 digits),
`59` clave debe/haber saldo final, `60-73` importe saldo final (14 digits),
`74-76` divisa.

## Design

### `portf_manager/parsers/aeb43_parser.py`

```python
def looks_like_aeb43(content: str) -> bool: ...
def parse_aeb43(content: str) -> BankParseResult: ...
```

- `looks_like_aeb43`: cheap sniff — strip CRLF, take the first non-blank
  line, return `True` iff it starts with `11` and the remainder up to
  position 50 is all-numeric (entity/office/account/dates/clave/importe are
  all digits in every valid header — a real CSV header row will not be).
- `parse_aeb43` reuses the existing `SpendingRow`/`BankParseResult`
  dataclasses from `generic_bank_csv_parser.py` (no new result types).
- Parsing algorithm: split on lines, pad each to 80 bytes, decode the `11`
  header for the opening `balance` seed. Walk records in order; for each
  `22`, decode date/sign/amount, consume all immediately-following `23`
  records as the description, apply the signed amount to a running `balance`
  accumulator, and emit a `SpendingRow(date, description, amount, currency,
  balance)`. `amount` sign: clave `1` → negative (out), `2` → positive (in) —
  matches the existing signed-amount convention used across Spending
  Tracking. Currency: map the numeric ISO 4217 code (`978`→`EUR`; a small
  table covers a few common codes, default `EUR` since these exports are
  overwhelmingly Spanish accounts) rather than hardcoding `EUR`.
- A `22` with a debit/credit clave outside `{1, 2}` is skipped into
  `result.skipped` with a reason, same pattern as `generic_bank_csv_parser`'s
  existing skip handling — defensive only, not observed in either sample.
- `33`/other unrecognized record types are simply not matched by the `22`
  branch and fall through without action.

### Wiring into `portf_server/routers/spending.py`

In `upload_bank_statement`, after decoding `file_bytes` to `content`: if
`looks_like_aeb43(content)`, call `parse_aeb43(content)`; otherwise call
`parse_generic_bank_csv(content)` exactly as today. Everything downstream
(rule-categorization, duplicate detection, response shape) is unchanged since
both parsers return the same `BankParseResult`/`SpendingRow` shape.

No change to `POST /api/v1/spending/save` — it already writes whatever the
preview rows contain, including `balance`, once accepted.

### Error handling

- Empty or malformed AEB43 content (e.g. sniff passes but parsing yields no
  `22` records) → same "no rows" preview behavior as an empty CSV today, no
  special-cased error.
- **Decoding fix (in scope):** both real sample files actually **fail** to
  decode as `.decode("utf-8-sig")` — verified directly (`UnicodeDecodeError:
  invalid continuation byte` on both) — because AEB43 exports commonly
  contain raw Latin-1 bytes for accented characters. `upload_bank_statement`
  must fall back to `.decode("latin-1")` on `UnicodeDecodeError` before either
  parser runs, or neither sample file can be uploaded at all. Latin-1 decodes
  any byte sequence without raising, so this fallback is unconditionally
  safe as a second attempt. This applies to the shared decode step, so it
  also benefits (not just doesn't regress) any Latin-1-encoded CSV upload.

### Testing

`tests/unit/test_aeb43_parser.py` with **synthetic fixture records** built
byte-for-byte from the layout above (fictional names/amounts/account numbers,
not the real sample files) covering:

- Header parse (opening balance + sign + currency).
- A `22`+single-`23` movement pair, both debit and credit sign.
- A `22` followed by multiple `23` continuation lines (concatenated
  description).
- Running-balance accumulation across several movements.
- Trailing-whitespace-trimmed lines shorter than 80 bytes (Abanca-style)
  parse identically to full-width lines (Caixa-style).
- `looks_like_aeb43` true/false cases (AEB43 header vs. a real CSV header
  row).

`tests/unit/test_spending_api.py` (existing upload-endpoint tests live here)
gets one new case: uploading AEB43 content routes to `parse_aeb43` and
produces rows with non-null `balance`, while existing CSV upload tests are
unaffected.
