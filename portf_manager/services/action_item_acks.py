"""Server-side follow-up state for Action Items and Diagnostics findings.

A finding (an action item, a possible duplicate, a suspicious transaction)
can be marked *checked* — looked at and judged fine, optionally with a note
saying why — or *snoozed* until a date. The state lives in ``app_settings``
under ``action_item_ack:{id}`` so every browser, the AI chat and the MCP
tools see the same list, instead of each browser keeping its own
``localStorage`` copy.

Ids are the deterministic action-item ids (``dq:duplicates``,
``errors:price-update:{run_id}`` ...) and, for the per-finding Data Quality
rows, ``dq:{finding_key}`` (``dq:dup:12:15``, ``dq:susp:40:zero_price``).
Because an id names one concrete occurrence, a check never hides a *new*
occurrence of the same kind.
"""

import json
from datetime import date, datetime, timedelta, timezone
from typing import Optional

ACK_PREFIX = "action_item_ack:"
STATUSES = ("open", "checked", "snoozed")


def _today() -> date:
    return date.today()


def _parse_date(value) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def ack_is_active(ack: Optional[dict], today: Optional[date] = None) -> bool:
    """Whether an ack still hides its finding.

    A checked finding stays hidden until its id changes. A snoozed one is
    hidden until ``until`` (inclusive); after that it reads as open again,
    while the record is kept so the note survives.
    """
    if not ack:
        return False
    status = ack.get("status")
    if status == "checked":
        return True
    if status == "snoozed":
        until = _parse_date(ack.get("until"))
        return until is not None and until >= (today or _today())
    return False


def effective_status(ack: Optional[dict], today: Optional[date] = None) -> str:
    """``open``, ``checked`` or ``snoozed`` as the finding reads today."""
    if ack_is_active(ack, today):
        return ack["status"]
    return "open"


def list_acks(db) -> dict[str, dict]:
    """Every stored ack, keyed by finding id.

    A backend without ``app_settings`` support (the PostgreSQL adapter has
    no settings methods) yields an empty map rather than failing the page.
    """
    lister = getattr(db, "list_settings", None)
    if lister is None:
        return {}
    acks = {}
    for key, raw in lister(ACK_PREFIX).items():
        item_id = key[len(ACK_PREFIX) :]
        try:
            ack = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(ack, dict) and ack.get("status") in STATUSES:
            ack["id"] = item_id
            acks[item_id] = ack
    return acks


def get_ack(db, item_id: str) -> Optional[dict]:
    """The stored ack for one finding, or None."""
    getter = getattr(db, "get_setting", None)
    if getter is None:
        return None
    raw = getter(ACK_PREFIX + item_id)
    if not raw:
        return None
    try:
        ack = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(ack, dict):
        return None
    ack["id"] = item_id
    return ack


def set_ack(
    db,
    item_id: str,
    status: str,
    note: Optional[str] = None,
    until: Optional[str] = None,
    days: Optional[int] = None,
    today: Optional[date] = None,
) -> Optional[dict]:
    """Record a finding as checked or snoozed, or reopen it.

    Args:
        db: Database with ``set_setting``/``delete_setting``.
        item_id: The finding id.
        status: ``checked``, ``snoozed`` or ``open`` (clears the record).
        note: Free text saying what was found or decided.
        until: ISO date a snooze lasts to (inclusive).
        days: Alternative to ``until``: snooze this many days from today.
        today: Injectable clock for tests.

    Returns:
        The stored ack, or None when the finding was reopened.

    Raises:
        ValueError: unknown status, or a snooze without a usable date.
        NotImplementedError: the database backend has no settings store.
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    if not item_id or not str(item_id).strip():
        raise ValueError("item id is required")
    item_id = str(item_id).strip()
    if not hasattr(db, "set_setting") or not hasattr(db, "delete_setting"):
        raise NotImplementedError("This database backend has no settings store")

    if status == "open":
        db.delete_setting(ACK_PREFIX + item_id)
        return None

    today = today or _today()
    ack = {
        "status": status,
        "note": (note or "").strip() or None,
        "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    if status == "snoozed":
        until_date = _parse_date(until)
        if until_date is None and days is not None:
            if int(days) < 1:
                raise ValueError("days must be at least 1")
            until_date = today + timedelta(days=int(days))
        if until_date is None:
            raise ValueError("a snooze needs 'until' (YYYY-MM-DD) or 'days'")
        if until_date < today:
            raise ValueError("a snooze date must not be in the past")
        ack["until"] = until_date.isoformat()
    db.set_setting(ACK_PREFIX + item_id, json.dumps(ack))
    ack["id"] = item_id
    return ack


def annotate_items(
    items: list[dict], acks: dict[str, dict], today: Optional[date] = None
) -> list[dict]:
    """Attach ``status`` and ``ack`` to each item (mutates and returns it)."""
    for item in items:
        ack = acks.get(item.get("id"))
        item["ack"] = ack
        item["status"] = effective_status(ack, today)
    return items


def summarize_items(items: list[dict]) -> dict:
    """Counts the dashboard, chat and MCP read instead of recomputing them."""
    summary = {
        "total": len(items),
        "open": 0,
        "checked": 0,
        "snoozed": 0,
        "open_by_severity": {"high": 0, "medium": 0, "low": 0},
        "open_by_category": {},
    }
    for item in items:
        status = item.get("status") or "open"
        summary[status] = summary.get(status, 0) + 1
        if status != "open":
            continue
        sev = item.get("severity") or "low"
        summary["open_by_severity"][sev] = summary["open_by_severity"].get(sev, 0) + 1
        cat = item.get("category") or "other"
        summary["open_by_category"][cat] = summary["open_by_category"].get(cat, 0) + 1
    return summary
