"""
Configurable tax-rate tables.

Currently ships Spanish IRPF "base del ahorro" (savings base) brackets used for
capital gains + dividends/interest. Structured so other jurisdictions can be
added without touching the calculation logic.
"""

from __future__ import annotations

# Each jurisdiction maps to a list of (upper_bound_eur, marginal_rate) tuples,
# applied progressively. The final bound is float("inf").
SAVINGS_BRACKETS: dict[str, list[tuple[float, float]]] = {
    # Spain — base del ahorro 2024/2025
    "ES": [
        (6_000, 0.19),
        (50_000, 0.21),
        (200_000, 0.23),
        (300_000, 0.27),
        (float("inf"), 0.28),
    ],
}

DEFAULT_JURISDICTION = "ES"


def progressive_tax(base: float, jurisdiction: str = DEFAULT_JURISDICTION) -> float:
    """Apply the progressive savings-base brackets for *jurisdiction* to *base*.

    Args:
        base: Taxable amount in EUR (gains + dividend/interest income).
        jurisdiction: ISO country code; falls back to the default if unknown.

    Returns:
        Estimated tax in EUR, rounded to cents.
    """
    if base <= 0:
        return 0.0
    brackets = SAVINGS_BRACKETS.get(
        jurisdiction, SAVINGS_BRACKETS[DEFAULT_JURISDICTION]
    )
    tax = 0.0
    lower = 0.0
    for upper, rate in brackets:
        if base > lower:
            taxable = min(base, upper) - lower
            tax += taxable * rate
            lower = upper
        else:
            break
    return round(tax, 2)
