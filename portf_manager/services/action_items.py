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

from datetime import date, datetime, timedelta

STALE_IMPORT_DAYS = 60


def _name_code(name: str | None, symbol: str) -> str:
    """Format an asset for display as "Name (SYMBOL)", falling back to the

    bare symbol when no name is known — users generally don't recognise
    ISINs or less common tickers by code alone.
    """
    return f"{name} ({symbol})" if name else symbol


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def check_stale_imports(db, today: date = None) -> list[dict]:
    """Portfolios/bank accounts with a transaction history but no activity in
    60+ days. Covers brokerage transactions/bookings and bank-account
    spending imports equally, since a bank portfolio never populates the
    former."""
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
                _parse_date(r.get("last_spending_date")),
            )
            if d is not None
        ]
        if not dates:
            continue
        most_recent = max(dates)
        days = (today - most_recent).days
        if days < STALE_IMPORT_DAYS:
            continue
        since_date = most_recent + timedelta(days=1)
        items.append(
            {
                "id": f"import:portfolio:{pid}",
                "category": "import",
                "severity": "medium",
                "title": f"No new activity in {p['name']} for {days} days",
                "detail": (
                    f"Last imported {most_recent.isoformat()}. Upload "
                    f"transactions for {p['name']} from "
                    f"{since_date.isoformat()} onward to bring it up to date."
                ),
                "link_page": "importexport",
                "context": {
                    "portfolio_id": pid,
                    "portfolio_name": p["name"],
                    "account_type": p.get("account_type") or "brokerage",
                    "since_date": since_date.isoformat(),
                },
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
    labels = []
    for sym in symbols:
        asset = db.get_asset_by_symbol(sym)
        labels.append(_name_code(asset.get("name") if asset else None, sym))
    return [
        {
            "id": f"errors:price-update:{run['id']}",
            "category": "errors",
            "severity": "high",
            "title": f"{run['error_count']} asset(s) failed to update prices",
            "detail": (
                f"Last run ({str(run.get('finished_at', ''))[:16]}): "
                + (", ".join(labels) if labels else "see Diagnostics for details")
            ),
            "link_page": "diagnostics",
            "context": {"run_id": run["id"], "symbols": symbols},
        }
    ]


STALE_RESEARCH_DAYS = 90


def check_stale_research(db, today: date = None) -> list[dict]:
    """Held assets with no research note in the last 90 days."""
    from portf_manager.positions import compute_positions

    today = today or date.today()
    positions, _ = compute_positions(db.get_all_transactions())
    held_asset_ids = {aid for aid, pos in positions.items() if pos["quantity"] > 0}
    if not held_asset_ids:
        return []

    latest_by_asset = {}
    for note in db.get_latest_research_notes():
        if note.get("asset_id") in held_asset_ids:
            latest_by_asset[note["asset_id"]] = note

    stale_labels = []
    stale_symbols = []
    for aid in held_asset_ids:
        note = latest_by_asset.get(aid)
        if note is None:
            asset = db.get_asset(aid) or {}
            symbol = asset.get("symbol", f"#{aid}")
            stale_labels.append(_name_code(asset.get("name"), symbol))
            stale_symbols.append(symbol)
            continue
        created = _parse_date(note.get("created_at"))
        if created is None or (today - created).days >= STALE_RESEARCH_DAYS:
            asset = db.get_asset(aid) or {}
            symbol = note.get("symbol", f"#{aid}")
            stale_labels.append(_name_code(asset.get("name"), symbol))
            stale_symbols.append(symbol)

    if not stale_symbols:
        return []
    order = sorted(range(len(stale_symbols)), key=lambda i: stale_symbols[i])
    return [
        {
            "id": "errors:stale-research",
            "category": "errors",
            "severity": "low",
            "title": (
                f"{len(stale_symbols)} holding(s) not re-valued in "
                f"{STALE_RESEARCH_DAYS}+ days"
            ),
            "detail": ", ".join(stale_labels[i] for i in order),
            "link_page": "research",
            "context": {"symbols": sorted(stale_symbols)},
        }
    ]


def check_goals_off_track(db) -> list[dict]:
    """Savings goals whose projected value falls short of their target."""
    from portf_server.routers.goals import list_goals

    items = []
    for g in list_goals(db=db, api_key_info={}):
        if g.get("on_track"):
            continue
        items.append(
            {
                "id": f"goals:{g['id']}",
                "category": "goals",
                "severity": "medium",
                "title": f"Goal \"{g['name']}\" is off track",
                "detail": (
                    f"Projected {g['projected_value_eur']:,.0f} EUR vs target "
                    f"{g['target_amount_eur']:,.0f} EUR by {g['target_date']}. "
                    f"Required monthly contribution: "
                    f"{g.get('required_monthly_eur')} EUR."
                ),
                "link_page": "goals",
                "context": {"goal_id": g["id"]},
            }
        )
    return items


def check_price_alerts(db) -> list[dict]:
    """Watchlist buy-zone hits and price-target crossings."""
    from portf_server.routers.research import compute_price_target_alerts
    from portf_server.routers.watchlist import check_watchlist_alerts

    items = []
    for a in check_watchlist_alerts(db=db, api_key_info={})["alerts"]:
        items.append(
            {
                "id": f"watchlist:{a['symbol']}",
                "category": "watchlist",
                "severity": "medium",
                "title": f"{_name_code(a.get('name'), a['symbol'])} dropped into its buy zone",
                "detail": (
                    f"Price {a['price']} at or below buy-below {a['buy_below']}."
                ),
                "link_page": "watchlist",
                "context": {"symbol": a["symbol"]},
            }
        )

    for a in compute_price_target_alerts(db):
        triggers = ", ".join(t["type"] for t in a["triggers"])
        items.append(
            {
                "id": f"research:{a['symbol']}",
                "category": "watchlist",
                "severity": "medium",
                "title": (
                    f"{_name_code(a.get('name'), a['symbol'])} crossed a "
                    f"price target ({triggers})"
                ),
                "detail": (
                    f"Currently held: {a['quantity']} units, unrealised P&L "
                    f"{a['unrealized_pnl']} ({a['unrealized_pnl_pct']}%)."
                ),
                "link_page": "research",
                "context": {"symbol": a["symbol"]},
            }
        )
    return items


# A budget line has to miss plan by BOTH of these before it's worth a nudge --
# a percentage alone spams on small lines, an absolute alone spams on large
# ones. "Miss" means over plan on a cost, short of plan on a contribution.
BUDGET_OVERRUN_PCT = 10.0
BUDGET_OVERRUN_EUR = 50.0

# Share of a month's spending that can sit outside the budget before the budget
# stops describing reality.
BUDGET_UNBUDGETED_SHARE = 0.15


def check_budget_overruns(db) -> list[dict]:
    """Lines over plan, and spending the active budget doesn't cover.

    Silent when there is no active budget — an unused feature shouldn't nag.
    """
    from portf_server.routers.budgets import get_budget_summary

    from portf_manager.services.budget import months_without_activity

    summary = get_budget_summary(db=db, api_key_info={})
    if not summary:
        return []
    # Nothing imported for this month yet, so every line reads as zero actual:
    # costs look heroically under plan and contributions look behind. That is
    # a missing statement, not a budgeting problem, and flagging it every
    # month-start would train the user to ignore this whole category.
    if months_without_activity(summary):
        return []

    items = []
    for section in summary["sections"]:
        # Costs are worth flagging when they run over; contributions when they
        # fall short. Income is neither — a light month there is a goals
        # question, already covered by check_goals_off_track.
        if section["key"] == "income":
            continue
        for line in section["lines"]:
            # variance_eur is signed so negative is always the bad direction,
            # whichever way round the section reads.
            missed = -line["variance_eur"]
            pct = line.get("variance_pct")
            if missed < BUDGET_OVERRUN_EUR or pct is None or -pct < BUDGET_OVERRUN_PCT:
                continue
            if section["favourable_when_under"]:
                title = f"Over budget on {line['label']}"
                detail = (
                    f"{line['actual_total']:,.0f} EUR spent this month vs "
                    f"{line['planned_total']:,.0f} EUR planned "
                    f"({missed:,.0f} EUR over)"
                )
            else:
                title = f"Behind plan on {line['label']}"
                detail = (
                    f"{line['actual_total']:,.0f} EUR contributed this month vs "
                    f"{line['planned_total']:,.0f} EUR planned "
                    f"({missed:,.0f} EUR short)"
                )
            items.append(
                {
                    "id": f"budget:line:{line['line_id']}",
                    "category": "budget",
                    "severity": "medium",
                    "title": title,
                    "detail": f"{detail} in budget \"{summary['budget_name']}\".",
                    "link_page": "budget",
                    "context": {
                        "budget_id": summary["budget_id"],
                        "line_id": line["line_id"],
                        "ref_key": line["ref_key"],
                    },
                }
            )

    spending = next(s for s in summary["sections"] if s["key"] == "spending")
    unbudgeted_total = sum(u["actual_total"] for u in spending["unbudgeted"])
    if (
        spending["actual_total"] > 0
        and unbudgeted_total / spending["actual_total"] > BUDGET_UNBUDGETED_SHARE
    ):
        top = ", ".join(u["label"] for u in spending["unbudgeted"][:3])
        items.append(
            {
                "id": "budget:unbudgeted",
                "category": "budget",
                "severity": "low",
                "title": f"{unbudgeted_total:,.0f} EUR of spending isn't budgeted",
                "detail": (
                    f"{unbudgeted_total / spending['actual_total'] * 100:.0f}% of "
                    f"this month's spending falls outside budget "
                    f"\"{summary['budget_name']}\". Largest: {top}."
                ),
                "link_page": "budget",
                "context": {"budget_id": summary["budget_id"]},
            }
        )

    return items


_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def get_action_items(db) -> list[dict]:
    """Run every check independently; one failure doesn't take down the rest."""
    import logging

    logger = logging.getLogger(__name__)
    checks = [
        check_stale_imports,
        check_data_quality,
        check_price_update_failures,
        check_stale_research,
        check_goals_off_track,
        check_price_alerts,
        check_budget_overruns,
    ]
    items = []
    for check in checks:
        try:
            items.extend(check(db))
        except Exception:
            logger.exception(f"Action-items check {check.__name__} failed")
    items.sort(key=lambda i: _SEVERITY_ORDER.get(i["severity"], 99))
    return items
