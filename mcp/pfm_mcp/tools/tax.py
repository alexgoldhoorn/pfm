"""Tax report, tax estimate and the antiaplicacion (wash-sale) check."""

import calendar
import urllib.parse
from datetime import date
from typing import Optional

from pfm_mcp import client, tool
from pfm_mcp.client import _fmt_currency


@tool
def tax_report(year: Optional[int] = None) -> str:
    """
    Generate a Spanish IRPF tax summary for a year: per-lot realised capital
    gains (FIFO), dividend income and withholding, plus an estimated savings-base
    tax. Backed by /analytics/tax-report and /analytics/tax-estimate.

    Args:
        year: Tax year (defaults to the current calendar year).
    """
    params = {"year": year} if year else {}
    try:
        data = client._get("/api/v1/analytics/tax-report", params or None)
    except Exception as e:
        return f"Error fetching tax report: {e}"

    # Estimate is best-effort — don't fail the whole report if it errors.
    try:
        est = client._get("/api/v1/analytics/tax-estimate", params or None)
    except Exception:
        est = {}

    yr = data.get("year", year or "N/A")
    lines = [f"IRPF TAX REPORT — {yr}:"]

    lots = data.get("realised_lots", [])
    if lots:
        lines.append("\nRealised capital gains (FIFO, Box 27):")
        for lot in lots:
            gain = float(lot.get("gain_loss", 0) or 0)
            sign = "+" if gain >= 0 else ""
            lines.append(
                f"  {lot.get('symbol', '?'):12s}"
                f"  sold {str(lot.get('sell_date', '?'))[:10]}"
                f"  qty {float(lot.get('quantity', 0)):.4f}"
                f"  cost {_fmt_currency(float(lot.get('cost_basis', 0)))}"
                f"  proceeds {_fmt_currency(float(lot.get('proceeds', 0)))}"
                f"  G/L {sign}{_fmt_currency(gain)}"
            )

    sign = "+" if data.get("realised_gain_total", 0) >= 0 else ""
    lines.append("")
    lines.append(
        f"Realised gain total:  {sign}{_fmt_currency(data.get('realised_gain_total', 0))}"
        f"  ({data.get('lot_count', 0)} lot(s))"
    )
    lines.append(
        f"Dividends (gross):    {_fmt_currency(data.get('dividends_gross_eur', 0))}"
    )
    lines.append(
        f"Dividend withholding: {_fmt_currency(data.get('dividend_withholding_eur', 0))}"
    )

    if est:
        lines.append("\nIRPF savings-base estimate:")
        lines.append(
            f"  Interest income:    {_fmt_currency(est.get('interest_income_eur', 0))}"
        )
        lines.append(
            f"  Savings base:       {_fmt_currency(est.get('savings_base_eur', 0))}"
        )
        lines.append(
            f"  Estimated tax:      {_fmt_currency(est.get('estimated_tax_eur', 0))}"
        )
        lines.append(
            f"  Unrealised gain:    {_fmt_currency(est.get('unrealised_gain_eur', 0))}"
        )

    note = data.get("note")
    if note:
        lines.append(f"\n{note}")

    return "\n".join(lines)


@tool
def tax_estimate(year: Optional[int] = None) -> str:
    """
    Quick IRPF tax estimate for a given year: realised capital gains, dividend
    income, interest income, and the progressive tax on the savings base.

    Args:
        year: Tax year (defaults to the current calendar year).
    """
    params = {"year": year} if year else {}
    try:
        data = client._get("/api/v1/analytics/tax-estimate", params or None)
    except Exception as e:
        return f"Error fetching tax estimate: {e}"

    yr = data.get("year", year or "?")
    lines = [f"IRPF TAX ESTIMATE — {yr}:"]
    lines.append(
        f"  Realised capital gains:  {_fmt_currency(data.get('realised_gain_eur', 0))}"
    )
    lines.append(
        f"  Dividend income:         {_fmt_currency(data.get('dividend_income_eur', 0))}"
    )
    lines.append(
        f"  Interest income:         {_fmt_currency(data.get('interest_income_eur', 0))}"
    )
    lines.append("  ─────────────────────────────────────────")
    lines.append(
        f"  Savings base total:      {_fmt_currency(data.get('savings_base_eur', 0))}"
    )
    tax = data.get("estimated_tax_eur", data.get("irpf_savings_tax_eur", 0))
    lines.append(f"  Estimated IRPF tax:      {_fmt_currency(tax)}")
    unrealised = data.get("unrealised_gain_eur")
    if unrealised is not None:
        lines.append(f"  Unrealised gain (info):  {_fmt_currency(unrealised)}")
    return "\n".join(lines)


def _parse_date(value: object) -> Optional[date]:
    """Parse the first ten characters of an ISO date, or None."""
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _add_months(d: date, months: int) -> date:
    """Shift a date by calendar months, clamping to the target month's end."""
    index = d.year * 12 + (d.month - 1) + months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


@tool
def wash_sale_check(symbol: Optional[str] = None) -> str:
    """
    Spanish antiaplicación check (art. 33.5 LIRPF — the "2-month rule"): which
    recent sales at a loss are blocked by a repurchase, which loss sales are
    still inside their window (don't rebuy yet, with the date it becomes safe),
    and which positions were bought recently (a sale at a loss now would be
    blocked). Window: 2 months for listed securities, 1 year for unlisted fund
    units; crypto not covered. Run this before advising a loss sale or a rebuy.

    Args:
        symbol: Optional ticker/ISIN to check just one position.
    """
    today = client._today()
    try:
        params = {"symbol": urllib.parse.quote(symbol)} if symbol else None
        out = client._get("/api/v1/analytics/wash-sale", params)
    except Exception as e:
        return f"Error fetching data for wash-sale check: {e}"

    def label(item: dict) -> str:
        name = item.get("name")
        return (
            f"{item['symbol']} ({name})"
            if name and name != item["symbol"]
            else item["symbol"]
        )

    scope = f" for {symbol.upper()}" if symbol else ""
    lines = [
        f"WASH-SALE CHECK (antiaplicación, art. 33.5 LIRPF){scope} — as of {today.isoformat()}:"
    ]
    if out["blocked"]:
        lines.append(
            "\nLoss sales blocked by a repurchase (loss deferred while those units are held):"
        )
        for b in out["blocked"]:
            bought = ", ".join(
                f"{x['date']} ({x['quantity']:g} units)" for x in b["blocking"]
            )
            lines.append(
                f"  {label(b)}: sold {b['sell_date']}, loss {_fmt_currency(b['loss_eur'])}"
                f" — blocked by buy(s) on {bought}"
            )
    if out["no_rebuy"]:
        lines.append("\nLoss sales still inside the window — don't rebuy yet:")
        for n in out["no_rebuy"]:
            lines.append(
                f"  {label(n)}: sold {n['sell_date']}, loss {_fmt_currency(n['loss_eur'])}"
                f" — buying it again before {n['until']} would block that loss"
            )
    if out["recent_buys"]:
        lines.append(
            "\nHeld positions bought recently — a sale at a loss would be blocked:"
        )
        for r in out["recent_buys"]:
            loss_str = " (currently at a loss)" if r.get("at_loss") else ""
            lines.append(
                f"  {label(r)}{loss_str}: last bought {r['last_buy']}"
                f" — a sale at a loss before {r['until']} is not deductible yet"
            )
    if not any(out.values()):
        lines.append(
            "  Nothing affected: no recent loss sales and no held position bought"
            " inside an antiaplicación window."
        )
    lines.append(
        "\nWindows: 2 months before/after the sale for listed securities; 1 year for"
        ' unlisted fund units (typed mutual_fund, on the "Funds" exchange, or named'
        " as a fund). Crypto, cash and Mintos P2P are excluded. Repurchases are"
        " matched against FIFO lots — a screen, so confirm edge cases before filing."
    )
    return "\n".join(lines)
