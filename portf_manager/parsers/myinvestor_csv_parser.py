"""
MyInvestor CSV parser — "Movimientos Mi Cuenta" account-movements export.

Columns (semicolon-delimited, European comma decimals):
    Fecha de operación;Fecha de valor;Concepto;Importe;Divisa

The ``Concepto`` encodes the movement; we classify by pattern + amount sign:
  - ``INVEST``/``MY INVESTOR``       → cash deposit into the account (booking)
    — the latter is MyInvestor's own in-app P2P/instant-transfer feature name
  - ``Sent from <service>`` (e.g. "Sent from Revolut") → deposit booking, an
    external transfer routed in from another provider
  - ``NAME @ QTY`` with Importe < 0  → BUY      (QTY units, cost = |Importe|)
  - ``NAME @ QTY`` with Importe > 0  → DIVIDEND (QTY = shares held; amount = payout)
  - ``NAME`` (no @), Importe > 0     → DIVIDEND (lump-sum payout or fund redemption)
  - platform fee (SUSCRIPCIÓN PREMIUM, comisión, ...)  → cash withdrawal (booking)
  - remunerated-cash interest (``Liq./Liquidación intereses <mes>``,
    ``PERIODO dd/mm/yyyy dd/mm/yyyy``, ``regularización intereses <mes>``)
    → INTEREST against a synthetic ``MYINVESTOR-CASH`` asset
  - its IRPF withholding (``Ret./Retención IRPF intereses <mes>``, or the
    "Ret. liq intereses ... promo" variant) → folded into the *same* interest
    row's ``tax`` field when a same-date credit row exists (MyInvestor always
    reports gross interest and its withholding as two separate lines, never
    combined); a withholding row with no same-date credit is emitted as its
    own (negative) interest row so cash still reconciles

KNOWN GAP: a P2P transfer labelled with an arbitrary person's name (e.g. a
family member sending cash) has the exact same shape as a genuine lump-sum
dividend — positive, no "@", not a fixed keyword — and there's no reliable,
generalizable way to tell them apart from the Concepto text alone (a real
example: "LABORATORIOS FARMACEUTIC ROVI" is a genuine dividend with this same
shape). Hardcoding a specific name would also leak whoever's statement it came
from into a public parser. These still land as a dividend on a fake symbol
and need catching in the import preview before saving.

NOTE: MyInvestor gives no ISIN, truncates names (~30 chars) and reports only an
EUR amount with no fee breakdown — so buy/sell rows are approximate (price =
amount/qty, currency EUR, fees 0) and won't auto-reconcile with ISIN-keyed
holdings. They're surfaced in the preview so the user decides per row.
"""

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from typing import List, Tuple

from portf_manager.llm_types import LLMTransaction
from portf_manager.parsers.utils import parse_european_number as _num

# "NAME @ 12" or "NAME @ 12,5" → (name, quantity)
_TRADE_RE = re.compile(r"^(.*?)\s*@\s*([0-9]+(?:[.,][0-9]+)?)\s*$")
# "MY INVESTOR" is MyInvestor's own in-app P2P/instant-transfer feature name
# (not a person's name — safe to hardcode, it's the same for every user of
# the product), used as the Concepto for an incoming transfer.
_DEPOSIT_CONCEPTS = {"INVEST", "INGRESO", "APORTACIÓN", "APORTACION", "MY INVESTOR"}
# An external transfer routed in from another provider, e.g. "Sent from
# Revolut" — the service name varies, the phrasing doesn't.
_EXTERNAL_TRANSFER_RE = re.compile(r"^sent from\s+\S+", re.IGNORECASE)
# Platform fees / charges (negative, no security) — recorded as a withdrawal.
_FEE_KEYWORDS = re.compile(
    r"suscripci[oó]n premium|comisi[oó]n|comision|tarifa|fee|custodia|coste",
    re.IGNORECASE,
)
# Remunerated-cash interest credit — must NOT also match the withholding
# pattern below ("Ret. liq intereses ... promo" starts with "Ret", not "Liq").
_INTEREST_CREDIT_RE = re.compile(
    r"^(?:liq\.?|liquidaci[oó]n)\s+intereses", re.IGNORECASE
)
# Its IRPF withholding — always a separate CSV line, same date as the credit.
_INTEREST_WITHHOLDING_RE = re.compile(
    r"^ret(?:\.|enci[oó]n)?\s+(?:irpf\s+)?(?:liq\s+)?intereses", re.IGNORECASE
)
# Quarterly/period settlement, e.g. "PERIODO 07/04/2026 07/05/2026" — no
# separate withholding line observed for these, so never paired.
_PERIODO_RE = re.compile(
    r"^periodo\s+\d{2}/\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}$", re.IGNORECASE
)
# Same-day +/- true-up pair with identical concept text on both sides, so
# unlike the credit/withholding pair above there's no reliable way to match
# which negative row corrects which positive one — each imported standalone.
_REGULARIZACION_RE = re.compile(r"^regularizaci[oó]n\s+intereses", re.IGNORECASE)

_CASH_INTEREST_SYMBOL = "MYINVESTOR-CASH"
_CASH_INTEREST_NAME = "MyInvestor Cash Interest"


@dataclass
class MyInvestorParseResult:
    transactions: List[LLMTransaction] = field(default_factory=list)
    bookings: List[dict] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def _date(raw: str) -> str:
    s = (raw or "").strip()[:10]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


def _cash_interest_tx(
    date: str, amount: float, currency: str, raw: str, tax: float = 0.0
) -> LLMTransaction:
    return LLMTransaction(
        tx_type="interest",
        symbol=_CASH_INTEREST_SYMBOL,
        asset_name=_CASH_INTEREST_NAME,
        quantity=1.0,
        price=amount,
        date=date,
        currency=currency,
        raw_text=raw,
        tax=tax,
        asset_type="cash",
    )


def parse_myinvestor_csv(csv_content: str) -> MyInvestorParseResult:
    """Parse a MyInvestor 'Movimientos' CSV into transactions + bookings."""
    res = MyInvestorParseResult()
    reader = csv.reader(StringIO(csv_content.strip()), delimiter=";")
    rows = list(reader)
    if not rows:
        return res

    # Skip the header row if present (first cell looks like a date label).
    start = 1 if rows and "fecha" in (rows[0][0] or "").strip().lower() else 0
    parsed = []
    for i, row in enumerate(rows[start:], start=start + 1):
        if len(row) < 4:
            if any(c.strip() for c in row):
                res.skipped.append((f"Row {i}", f"too few columns ({len(row)})"))
            continue
        date = _date(row[0])
        concepto = (row[2] or "").strip()
        try:
            importe = _num(row[3])
        except ValueError:
            res.skipped.append((f"Row {i}", f"bad amount '{row[3]}'"))
            continue
        currency = row[4].strip() if len(row) > 4 and row[4].strip() else "EUR"
        parsed.append((i, date, concepto, importe, currency, ";".join(row)))

    # IRPF withholding is always its own CSV line, never combined with the
    # gross interest credit it belongs to — collect them first so a credit
    # row processed below can fold its same-date withholding into `tax`.
    # File order isn't reliable (withholding sometimes precedes the credit,
    # sometimes follows it), so this has to be a separate pass.
    withholding_by_date: dict[str, list[float]] = {}
    for i, date, concepto, importe, currency, raw in parsed:
        if _INTEREST_WITHHOLDING_RE.match(concepto):
            withholding_by_date.setdefault(date, []).append(importe)

    for i, date, concepto, importe, currency, raw in parsed:
        if _INTEREST_WITHHOLDING_RE.match(concepto):
            continue  # consumed below by its matching credit row, if any

        if concepto.upper() in _DEPOSIT_CONCEPTS or _EXTERNAL_TRANSFER_RE.match(
            concepto
        ):
            res.bookings.append(
                {
                    "broker": "MyInvestor",
                    "date": date,
                    "action": "Deposit" if importe >= 0 else "Withdrawal",
                    "amount": abs(importe),
                    "currency": currency,
                }
            )
            continue

        if importe < 0 and _FEE_KEYWORDS.search(concepto):
            res.bookings.append(
                {
                    "broker": "MyInvestor",
                    "date": date,
                    "action": "Withdrawal",
                    "amount": abs(importe),
                    "currency": currency,
                }
            )
            continue

        if (
            _INTEREST_CREDIT_RE.match(concepto)
            or _PERIODO_RE.match(concepto)
            or _REGULARIZACION_RE.match(concepto)
        ):
            tax = 0.0
            if _INTEREST_CREDIT_RE.match(concepto):
                pending = withholding_by_date.get(date)
                if pending:
                    tax = abs(pending.pop(0))
            res.transactions.append(
                _cash_interest_tx(date, importe, currency, raw, tax)
            )
            continue

        m = _TRADE_RE.match(concepto)
        if m:
            name = m.group(1).strip()
            qty = abs(_num(m.group(2)))
            if qty <= 0:
                res.skipped.append((f"Row {i}", f"zero quantity: {concepto}"))
                continue
            total = abs(importe)
            res.transactions.append(
                LLMTransaction(
                    tx_type="dividend" if importe > 0 else "buy",
                    symbol=name,
                    asset_name=name,
                    quantity=qty,
                    price=round(total / qty, 6),
                    date=date,
                    currency=currency,
                    raw_text=raw,
                )
            )
            continue

        # No "@" and positive → cash dividend for that holding. (Large amounts
        # on fund names could be redemptions rather than dividends — MyInvestor
        # doesn't distinguish — so these are flagged for review downstream.)
        if importe > 0:
            res.transactions.append(
                LLMTransaction(
                    tx_type="dividend",
                    symbol=concepto,
                    asset_name=concepto,
                    quantity=1.0,
                    price=abs(importe),
                    date=date,
                    currency=currency,
                    raw_text=raw,
                )
            )
            continue

        # Negative, no "@", not a deposit/fee/interest. A fund buy MyInvestor
        # recorded by amount with no unit count — can't import a unit-based
        # trade from it, so skip with an honest reason.
        res.skipped.append(
            (f"Row {i}", f"buy without unit detail (no '@ qty'): {concepto}")
        )

    # Any withholding row that never found a same-date credit row (shouldn't
    # normally happen) still reduced real cash — surface it standalone rather
    # than silently dropping it.
    for date, amounts in withholding_by_date.items():
        for amt in amounts:
            res.transactions.append(
                _cash_interest_tx(
                    date, amt, "EUR", f"unmatched IRPF withholding on {date}"
                )
            )

    return res
