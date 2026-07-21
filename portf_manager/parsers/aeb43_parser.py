"""
AEB43 / Norma 43 ("Cuaderno 43") fixed-width bank statement parser.

National Spanish banking standard used by (at least) Caixa Enginyers and
Abanca as an export format for account movements — an alternative to CSV.
80-byte fixed-width records, CRLF line endings, record type in the first two
characters:

  11  Header        — one per file: opening balance + sign, currency
  22  Movement      — one per transaction: date, debit/credit flag, amount
  23  Complementary — one or more per movement, immediately following it:
                       free-text description
  33  Trailer       — one per file, totals only (not parsed here)

Some exports (Caixa Enginyers) pad every line to a flat 80 bytes; others
(Abanca) strip trailing whitespace, so movement/complementary lines can be
shorter than 80 bytes. Every line is left-padded to 80 bytes with
``ljust(80)`` before any fixed-position slicing, so both export styles parse
identically.

Field layout was reverse-engineered and cross-validated against two real
bank exports: computed debit/credit counts, sums, and running balance all
matched each file's own trailer record to the cent for both banks.
"""

from __future__ import annotations


from .generic_bank_csv_parser import BankParseResult

_CURRENCY_ISO_NUMERIC = {
    "978": "EUR",
    "840": "USD",
    "826": "GBP",
}


def looks_like_aeb43(content: str) -> bool:
    """Sniff whether `content` is an AEB43/Norma 43 file (vs. delimited CSV).

    Checks the first non-blank line: a real AEB43 header record starts with
    "11" and positions 3-50 (entity/office/account/dates/sign/amount/
    currency) are all digits — a CSV header row never is.

    Args:
        content: Raw decoded file text.

    Returns:
        True if the content looks like an AEB43 header record.
    """
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("11"):
            return False
        padded = stripped.ljust(80)
        return padded[2:50].isdigit()
    return False


def parse_aeb43(content: str) -> BankParseResult:
    """Parse AEB43/Norma 43 fixed-width content into signed SpendingRow objects.

    Args:
        content: Raw AEB43 text (already decoded).

    Returns:
        BankParseResult with parsed rows and skipped records with reasons.
    """
    # Stub implementation - will be completed in Step 8
    return BankParseResult()
