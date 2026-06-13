# Design: Clear Portfolio Transactions + Backup Config

**Date:** 2026-06-14
**Status:** Approved

## Context

When re-importing transactions for a broker (e.g. Indexa Capital, where fecha valor vs.
fecha operacion date inconsistencies cause phantom duplicates), the user needs to wipe a
portfolio's transactions and start fresh from an import. The safest flow is: download a
backup, clear, re-import.

Backup infrastructure already exists:
- `~/scripts/portf-backup.sh` — daily cron at 03:00, SQLite online backup API, gzip,
  configurable retention via `PFM_BACKUP_KEEP`
- `GET /api/v1/export/backup` — streams a consistent SQLite snapshot
- "Download DB backup" button on the Import/Export page

## Scope

Three small additions:

1. **Backend** — one new DELETE endpoint
2. **Config** — document backup env vars in `.env.local`
3. **Frontend** — "Clear transactions" action with safety modal on the Portfolios page

## Backend

### `DELETE /api/v1/portfolios/{id}/transactions`

- Router: `portf_server/routers/portfolios.py`
- Auth: key-auth (same as all other portfolio endpoints)
- Behaviour: deletes every row in `transactions` where `portfolio_id = id`
- Returns: `{"deleted": N}` (count of removed rows)
- No cascades — assets, prices, bookings, and the portfolio itself are untouched
- 404 if the portfolio does not exist

## Config

Add to `.env.local` (with explanatory comments, no new code):

```bash
# Database backup — ~/scripts/portf-backup.sh runs daily via cron at 03:00
# Backups are gzip-compressed SQLite snapshots; the newest KEEP are retained.
PFM_BACKUP_DIR=/home/agoldhoorn/backups/pfm
PFM_BACKUP_KEEP=30
```

## Frontend

### Portfolios page — "Clear transactions" action

Location: the existing per-portfolio action buttons row (same place as Edit).

Trigger: small red/danger button labelled "Clear transactions" (or trash icon + label).

On click → open a Bootstrap modal containing:

| Element | Detail |
|---|---|
| Title | "Clear all transactions — [Portfolio Name]" |
| Body text | "This will permanently delete **all transactions** for this broker. This cannot be undone." |
| "Download DB backup" button | Calls the existing `/api/v1/export/backup` download (same logic as the button on Import/Export page) |
| Checkbox | "I have downloaded a backup or accept the risk" |
| "Clear transactions" button | Red/danger, disabled until checkbox is checked |

On confirm:
- `DELETE /api/v1/portfolios/{id}/transactions`
- Show toast: "Deleted N transactions from [Portfolio Name]"
- Refresh the portfolio list (to update transaction counts if shown)
- Close modal and uncheck checkbox (reset state for next use)

On error: show alert with the error message.

## Out of Scope

- Backup settings UI panel (the Import/Export "Download DB backup" button is sufficient)
- Clearing bookings alongside transactions (separate concern; not requested)
- Soft-delete / undo (hard delete matches the intent; user has the backup)
