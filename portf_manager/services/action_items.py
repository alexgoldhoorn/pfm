"""Action Items — cross-cutting maintenance checklist aggregator.

Pulls together checks that don't already have a single-call equivalent:
stale broker imports, a data-quality summary, price-update failures, stale
research on held positions, off-track goals, and watchlist/research price
alerts. Each check is independent; get_action_items() (added in a later
task) wraps each in a try/except so one failing check can't take down the
rest.

Net Worth setup gaps are deliberately NOT computed here — that checklist
logic lives client-side only (computeNetWorthChecklist() in
pfm_analytics.js, already unit-tested) and the frontend merges it into the
same displayed list. See docs/superpowers/specs/2026-07-16-action-items-design.md.
"""

from datetime import date, datetime

STALE_IMPORT_DAYS = 60


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def check_stale_imports(db, today: date = None) -> list[dict]:
    """Portfolios with a transaction history but no activity in 60+ days."""
    today = today or date.today()
    ranges = db.get_portfolio_date_ranges()
    items = []
    for p in db.get_all_portfolios():
        pid = p["id"]
        r = ranges.get(pid)
        if not r:
            continue  # never funded — nothing to import yet
        dates = [
            d
            for d in (
                _parse_date(r.get("last_transaction_date")),
                _parse_date(r.get("last_booking_date")),
            )
            if d is not None
        ]
        if not dates:
            continue
        most_recent = max(dates)
        days = (today - most_recent).days
        if days < STALE_IMPORT_DAYS:
            continue
        items.append(
            {
                "id": f"import:portfolio:{pid}",
                "category": "import",
                "severity": "medium",
                "title": f"No new activity in {p['name']} for {days} days",
                "detail": (
                    f"Last transaction or booking recorded on "
                    f"{most_recent.isoformat()}. Import recent statements if "
                    "this broker is still active."
                ),
                "link_page": "importexport",
                "context": {"portfolio_id": pid},
            }
        )
    return items


def check_data_quality(db) -> list[dict]:
    """Summarize non-empty findings from the existing DQ endpoints."""
    from portf_server.routers.analytics import dq_duplicates, dq_suspicious

    items = []

    dups = dq_duplicates(db=db, api_key_info={})["duplicates"]
    if dups:
        likely = sum(1 for d in dups if d["label"] == "likely")
        items.append(
            {
                "id": "dq:duplicates",
                "category": "data_quality",
                "severity": "high" if likely else "medium",
                "title": f"{len(dups)} possible duplicate transaction(s)",
                "detail": (
                    f"{likely} likely, {len(dups) - likely} possible — review "
                    "before they distort cost basis."
                ),
                "link_page": "diagnostics",
                "context": {"count": len(dups)},
            }
        )

    issues = dq_suspicious(db=db, api_key_info={})["issues"]
    if issues:
        warnings = sum(1 for i in issues if i["severity"] == "warning")
        items.append(
            {
                "id": "dq:suspicious",
                "category": "data_quality",
                "severity": "high" if warnings else "medium",
                "title": f"{len(issues)} suspicious transaction pattern(s)",
                "detail": (
                    f"{warnings} warning(s) — zero prices, negative positions, "
                    "or price outliers."
                ),
                "link_page": "diagnostics",
                "context": {"count": len(issues)},
            }
        )

    return items


def check_price_update_failures(db) -> list[dict]:
    """Flag the most recent price-update run if it had errors."""
    runs = db.get_price_update_runs(limit=1)
    if not runs:
        return []
    run = runs[0]
    if not run.get("error_count"):
        return []
    symbols = run.get("error_symbols") or []
    return [
        {
            "id": f"errors:price-update:{run['id']}",
            "category": "errors",
            "severity": "high",
            "title": f"{run['error_count']} asset(s) failed to update prices",
            "detail": (
                f"Last run ({str(run.get('finished_at', ''))[:16]}): "
                + (", ".join(symbols) if symbols else "see Diagnostics for details")
            ),
            "link_page": "diagnostics",
            "context": {"run_id": run["id"], "symbols": symbols},
        }
    ]
