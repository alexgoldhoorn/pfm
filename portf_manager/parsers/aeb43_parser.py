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

from datetime import datetime
from typing import Optional

from .generic_bank_csv_parser import BankParseResult, SpendingRow

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


def _aammdd_to_iso(raw: str) -> Optional[str]:
    """Convert an AAMMDD date field to YYYY-MM-DD, or None if invalid."""
    try:
        return datetime.strptime(raw, "%y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_aeb43(content: str) -> BankParseResult:
    """Parse AEB43/Norma 43 fixed-width content into signed SpendingRow objects.

    Args:
        content: Raw AEB43 text (already decoded).

    Returns:
        BankParseResult with parsed rows and skipped records with reasons.
    """
    result = BankParseResult()
    lines = [ln.rstrip("\r\n").ljust(80) for ln in content.splitlines() if ln.strip()]

    if not lines or not lines[0].startswith("11"):
        result.skipped.append(("file", "Not a valid AEB43 file: missing header record"))
        return result

    header = lines[0]
    clave_inicial = header[32]
    saldo_inicial = int(header[33:47]) / 100
    currency = _CURRENCY_ISO_NUMERIC.get(header[47:50], "EUR")
    balance = saldo_inicial if clave_inicial == "2" else -saldo_inicial

    i = 1
    while i < len(lines):
        line = lines[i]
        if line[:2] != "22":
            i += 1
            continue

        fecha_op = _aammdd_to_iso(line[10:16])
        clave = line[27]
        importe = int(line[28:42]) / 100

        j = i + 1
        desc_parts = []
        while j < len(lines) and lines[j][:2] == "23":
            desc_parts.append(lines[j][4:80].rstrip())
            j += 1
        description = " ".join(p for p in desc_parts if p).strip()

        if clave not in ("1", "2"):
            result.skipped.append(
                (f"record {i + 1}", f"Unknown debit/credit flag: {clave!r}")
            )
            i = j
            continue
        if fecha_op is None:
            result.skipped.append((f"record {i + 1}", "Invalid operation date"))
            i = j
            continue

        signed_amount = importe if clave == "2" else -importe
        balance += signed_amount

        if importe == 0:
            result.skipped.append((f"record {i + 1}", "Amount is zero"))
            i = j
            continue

        result.rows.append(
            SpendingRow(
                date=fecha_op,
                description=description,
                amount=signed_amount,
                currency=currency,
                balance=round(balance, 2),
            )
        )
        i = j

    return result
