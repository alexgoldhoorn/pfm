"""Loans and mortgage: where each loan stands, and what-if repayments."""

from typing import Optional

from pfm_mcp import client, tool
from pfm_mcp.client import _fmt_currency, _na


def _latest_complete(recon: Optional[dict]) -> Optional[dict]:
    """The newest reconciliation row with a debit and a complete statement."""
    rows = (recon or {}).get("rows") or []
    done = [r for r in rows if r.get("complete") and (r.get("actual_eur") or 0) > 0]
    return done[-1] if done else None


def _loan_block(ov: dict) -> list[str]:
    s = ov.get("summary") or {}
    lines = [
        f"{s.get('name', '?')}"
        + (f" ({s['lender']})" if s.get("lender") else "")
        + f"  [id {s.get('id', '?')}]",
        f"  Balance today:     {_fmt_currency(s.get('balance_eur') or 0)}"
        f"  ({s.get('months_left', 0)} payments left, ends {s.get('end_date', '?')})",
        f"  Rate:              {_na(s.get('annual_rate_pct'), lambda v: f'{v:.2f}%')}"
        + (f"  (from {s['rate_as_of']})" if s.get("rate_as_of") else ""),
        f"  Monthly payment:   {_fmt_currency(s.get('monthly_payment_eur') or 0)}",
        f"  Interest:          {_fmt_currency(s.get('interest_paid_this_year_eur') or 0)}"
        f" paid this year of {_fmt_currency(s.get('interest_scheduled_this_year_eur') or 0)}"
        f" scheduled; {_fmt_currency(s.get('interest_remaining_eur') or 0)} still to pay",
    ]
    if s.get("principal_eur") is not None:
        pct = s.get("repaid_pct")
        lines.insert(
            1,
            f"  Borrowed:          {_fmt_currency(s['principal_eur'])}"
            f" on {s.get('start_date', '?')}; {_fmt_currency(s.get('repaid_eur') or 0)}"
            " repaid" + (f" ({pct:.1f}%)" if pct is not None else ""),
        )
    paid = s.get("paid_to_date") or {}
    if paid.get("complete"):
        lines.append(
            f"  Paid to date:      {_fmt_currency(paid.get('principal_eur') or 0)} principal"
            f" + {_fmt_currency(paid.get('interest_eur') or 0)} interest"
            f" in {paid.get('payments_count', 0)} payments since {paid.get('from_date')}"
        )
        gap = paid.get("anchor_gap_eur")
        if gap is not None and abs(gap) > 1:
            lines.append(
                f"  ! rate history ends {_fmt_currency(abs(gap))} "
                f"{'above' if gap > 0 else 'below'} the recorded balance"
            )
    periods = ov.get("rate_periods") or []
    if periods:
        lines.append(
            "  Rate history:      "
            + ", ".join(
                f"{p['from_date']} {p['annual_rate_pct']:.2f}%" for p in periods
            )
        )
    elif s.get("anchored"):
        lines.append(
            "  (schedule runs from the recorded balance; nothing earlier is known)"
        )
    eq = ov.get("equity") or {}
    if eq.get("equity_eur") is None:
        lines.append(f"  Equity:            n/a ({eq.get('reason') or 'no property'})")
    else:
        lines.append(
            f"  Equity:            {_fmt_currency(eq['equity_eur'])}"
            f"  (LTV {_na(eq.get('ltv_pct'), lambda v: f'{v:.1f}%')})"
            + (f"  {eq['note']}" if eq.get("note") else "")
        )
    recon = ov.get("reconciliation") or {}
    latest = _latest_complete(recon)
    if latest:
        lines.append(
            f"  Bank, {latest['month']}:    scheduled "
            f"{_fmt_currency(latest['scheduled_eur'])}, charged "
            f"{_fmt_currency(latest['actual_eur'])} -> {latest['status']}"
        )
    elif recon.get("reason"):
        lines.append(f"  Bank:              n/a ({recon['reason']})")
    else:
        lines.append("  Bank:              no complete month with a debit yet")
    if recon.get("hint"):
        lines.append(f"  ! {recon['hint']}")
    return lines


@tool
def loans() -> str:
    """
    Every loan (mortgage and others): balance, rate, monthly payment, end date,
    interest this year and still to pay, principal and interest paid to date
    (only when the rate history makes it complete), the rate periods, equity and
    loan-to-value when a property asset exists, and whether the bank charged the
    scheduled payment in the latest complete month. Read-only.
    """
    try:
        rows = client._get("/api/v1/networth/loans")
    except Exception as e:
        return f"Error fetching loans: {e}"
    if not rows:
        return "No loans entered. Add one on the Net Worth page."
    out = ["LOANS"]
    for row in rows:
        try:
            ov = client._get(f"/api/v1/networth/loans/{row['id']}/overview")
        except Exception as e:
            out += ["", f"{row.get('name', '?')}: could not load the overview ({e})"]
            continue
        out += [""] + _loan_block(ov)
    return "\n".join(out)


@tool
def loan_what_if(
    loan_id: Optional[int] = None,
    lump_sum: float = 0.0,
    lump_date: Optional[str] = None,
    extra_monthly: float = 0.0,
    mode: str = "term",
    invest_return_pct: Optional[float] = None,
    new_rate_pct: Optional[float] = None,
    rate_change_pts: Optional[float] = None,
    rate_from: Optional[str] = None,
) -> str:
    """
    What if: a lump-sum repayment, extra monthly payments, or a rate change on
    one loan. Never changes anything.

    Args:
        loan_id: The loan's id (see `loans`); optional when there is one loan.
        lump_sum: One-off extra repayment in euros.
        lump_date: YYYY-MM-DD of the lump sum (default today).
        extra_monthly: Extra euros on every later payment (mode term only).
        mode: "term" keeps the payment and finishes sooner; "payment" keeps
            the end date and lowers the payment.
        invest_return_pct: Yearly return to compare investing the same money.
        new_rate_pct: New annual rate in percent from `rate_from`.
        rate_change_pts: Rate change in percentage points (instead of a rate).
        rate_from: YYYY-MM-DD the new rate starts (default today). A rate
            change cannot be combined with the other options.
    """
    try:
        if loan_id is None:
            rows = client._get("/api/v1/networth/loans")
            if len(rows) != 1:
                names = ", ".join(f"{r['id']}: {r['name']}" for r in rows)
                return (
                    "Give loan_id. Loans: " + (names or "none entered")
                    if rows
                    else "No loans entered."
                )
            loan_id = rows[0]["id"]
        params = {
            "lump_sum": lump_sum or None,
            "lump_date": lump_date,
            "extra_monthly": extra_monthly or None,
            "mode": mode,
            "invest_return_pct": invest_return_pct,
            "new_rate_pct": new_rate_pct,
            "rate_change_pts": rate_change_pts,
            "rate_from": rate_from,
        }
        res = client._get(f"/api/v1/networth/loans/{loan_id}/what-if", params)
    except Exception as e:
        return f"Error calculating the what-if: {e}"

    base, scen = res.get("baseline") or {}, res.get("scenario") or {}
    lines = [f"WHAT-IF for loan {loan_id} (from {res.get('lump_date', '?')})"]
    rc = res.get("rate_change")
    if rc:
        lines.append(
            f"  Rate {rc['previous_rate_pct']:.2f}% -> {rc['new_rate_pct']:.2f}% "
            f"on a balance of {_fmt_currency(rc['balance_eur'])}"
        )
    lines += [
        f"  Ends:            {base.get('end_date')} -> {scen.get('end_date')}"
        f"  ({res.get('months_saved', 0)} months sooner)",
        f"  Monthly payment: {_fmt_currency(base.get('monthly_payment_eur') or 0)}"
        f" -> {_fmt_currency(scen.get('monthly_payment_eur') or 0)}",
        f"  Interest to pay: {_fmt_currency(base.get('interest_remaining_eur') or 0)}"
        f" -> {_fmt_currency(scen.get('interest_remaining_eur') or 0)}",
    ]
    saved = res.get("interest_saved_eur") or 0
    lines.append(
        f"  Interest {'saved' if saved >= 0 else 'extra'}: "
        f"{_fmt_currency(abs(saved))}"
    )
    if rc:
        lines.append(f"  Payment change:  {rc['payment_diff_eur']:+,.2f} EUR a month")
    inv = res.get("invest")
    if inv:
        lines.append(
            f"  Investing it at {inv['return_pct']:g}%: worth "
            f"{_fmt_currency(inv['value_at_baseline_end_eur'])} at the baseline "
            f"end ({_fmt_currency(inv['gain_minus_interest_saved_eur'])} versus "
            f"the interest saved). {inv['note']}"
        )
    return "\n".join(lines)
