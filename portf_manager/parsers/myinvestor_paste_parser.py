"""
MyInvestor paste parser — "Movimientos" copied from the MyInvestor web/app.

Each entry in the copy-paste stream looks like:

    DD/MM/YYYY              ← date section header (appears once per day)

    CR                      ← operation code (2-3 uppercase letters)
    Compra Rv Contado       ← human description (ignored)
    1.083,02 €              ← amount (always positive)
    INTUITIVE SURGICAL @ 3  ← concept: name, optionally @ shares_held
    16.679,05 €             ← running balance (ignored)

Operation codes → tx type:
    CR  Compra Rv Contado   → buy
    VD  Venta De Valores    → sell
    AD  Abono De Dividendo  → dividend
    TS  Transferencia Sepa  → deposit booking
    SL  Salida              → withdrawal booking

The parser scans line-by-line and classifies lines within each block by
content (amount regex vs text) rather than fixed positions, so it handles any
blank-line variations, Windows CRLF, or reordering in browser copy-paste.
"""

import re
from datetime import date as _today

from portf_manager.llm_types import LLMTransaction
from portf_manager.parsers.myinvestor_csv_parser import (
    MyInvestorParseResult,
    _FEE_KEYWORDS,
    _TRADE_RE,
    _date,
    _num,
)

_DATE_RE = re.compile(r"^\d{1,2}/\d{2}/\d{4}$")
# Matches a monetary amount line: "1.083,02 €"
_AMOUNT_RE = re.compile(r"^[\d.,]+\s*€?$")

_CODE_TO_TYPE: dict[str, str] = {
    "CR": "buy",
    "VD": "sell",
    "AD": "dividend",
    "TS": "booking",
    "SL": "withdrawal",
}


def parse_myinvestor_paste(text: str) -> MyInvestorParseResult:
    """Parse a MyInvestor copy/paste statement into transactions + bookings."""
    res = MyInvestorParseResult()

    # Normalise all line endings, then keep only non-empty stripped lines.
    all_lines = [
        ln.strip()
        for ln in text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        if ln.strip()
    ]

    current_date = ""
    i = 0

    while i < len(all_lines):
        line = all_lines[i]

        # Date section header: DD/MM/YYYY or "Hoy" (today in Spanish)
        if line.lower() in ("hoy", "ayer", "today"):
            offset = 1 if line.lower() in ("ayer", "yesterday") else 0
            from datetime import timedelta

            current_date = (_today.today() - timedelta(days=offset)).isoformat()
            i += 1
            continue
        if _DATE_RE.match(line):
            current_date = _date(line)
            i += 1
            continue

        # Operation code: known 2-letter code on its own line
        code = line.upper()
        if code not in _CODE_TO_TYPE:
            i += 1
            continue

        # Collect the next up to 5 non-empty lines for this block.
        block = []
        j = i + 1
        while j < len(all_lines) and len(block) < 5:
            nxt = all_lines[j]
            # Stop if we hit the next operation code or a date header
            if nxt.upper() in _CODE_TO_TYPE or _DATE_RE.match(nxt):
                break
            block.append(nxt)
            j += 1

        # Classify lines by content: amount lines vs text (concept/desc) lines.
        amounts = [ln for ln in block if _AMOUNT_RE.match(ln)]
        texts = [ln for ln in block if not _AMOUNT_RE.match(ln)]

        if not amounts:
            res.skipped.append(
                (f"Line {i}", f"no amount found in block for '{code}': {block}")
            )
            i = j
            continue

        tx_type = _CODE_TO_TYPE[code]
        amount = _num(amounts[0].replace("€", "").strip())
        # First text line = human description (ignored); last = concept.
        # If only one text line, it doubles as both.
        concepto = texts[-1] if texts else ""
        raw = "\n".join([code] + block)

        # Cash booking (deposit or withdrawal)
        if tx_type in ("booking", "withdrawal"):
            if _FEE_KEYWORDS.search(concepto):
                res.skipped.append((f"Line {i}", f"fee/charge skipped: {concepto}"))
                i = j
                continue
            res.bookings.append(
                {
                    "broker": "MyInvestor",
                    "date": current_date,
                    "action": "Withdrawal" if tx_type == "withdrawal" else "Deposit",
                    "amount": amount,
                    "currency": "EUR",
                }
            )
            i = j
            continue

        # Buy
        if tx_type == "buy":
            m = _TRADE_RE.match(concepto)
            if not m:
                res.skipped.append(
                    (f"Line {i}", f"buy without unit detail (no '@ qty'): {concepto}")
                )
                i = j
                continue
            name = m.group(1).strip()
            qty = abs(_num(m.group(2)))
            if qty <= 0:
                res.skipped.append((f"Line {i}", f"zero quantity: {concepto}"))
                i = j
                continue
            res.transactions.append(
                LLMTransaction(
                    tx_type="buy",
                    symbol=name,
                    asset_name=name,
                    quantity=qty,
                    price=round(amount / qty, 6),
                    date=current_date,
                    currency="EUR",
                    raw_text=raw,
                )
            )
            i = j
            continue

        # Sell
        if tx_type == "sell":
            m = _TRADE_RE.match(concepto)
            if m:
                name = m.group(1).strip()
                qty = abs(_num(m.group(2)))
                price = round(amount / qty, 6) if qty else amount
            else:
                name = concepto
                qty = 1.0
                price = amount
            res.transactions.append(
                LLMTransaction(
                    tx_type="sell",
                    symbol=name,
                    asset_name=name,
                    quantity=qty,
                    price=price,
                    date=current_date,
                    currency="EUR",
                    raw_text=raw,
                )
            )
            i = j
            continue

        # Dividend — always qty=1, price=total payout (shares_held in concepto is
        # informational, not the transaction quantity).
        m = _TRADE_RE.match(concepto)
        name = m.group(1).strip() if m else concepto
        qty = 1.0
        price = amount
        res.transactions.append(
            LLMTransaction(
                tx_type="dividend",
                symbol=name,
                asset_name=name,
                quantity=qty,
                price=price,
                date=current_date,
                currency="EUR",
                raw_text=raw,
            )
        )
        i = j

    return res
