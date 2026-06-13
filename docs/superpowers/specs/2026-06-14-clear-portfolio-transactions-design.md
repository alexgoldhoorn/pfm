# Design: Clear Portfolio Transactions + Backup/Restore

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

Four small additions:

1. **Backend** — `DELETE /api/v1/portfolios/{id}/transactions` + `POST /api/v1/system/restore`
2. **Config** — document backup env vars in `.env.local`
3. **Frontend** — "Clear transactions" action with safety modal on Portfolios page
4. **Frontend** — "Restore DB backup" upload on Import/Export page

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

## Backend — Restore

### `POST /api/v1/system/restore`

- Router: new `portf_server/routers/system.py` (registered at `/api/v1/system`)
- Auth: key-auth
- Input: multipart upload of a `.db` or `.db.gz` file
- Steps:
  1. Receive uploaded file into a temp path
  2. If `.gz`, decompress to a second temp file
  3. Validate: open with `sqlite3.connect`, run `PRAGMA integrity_check` — reject if not a valid SQLite DB
  4. Check schema version: `PRAGMA user_version` must equal current `DATABASE_VERSION` (reject with 422 + message if mismatched)
  5. Auto-save current DB to `PFM_BACKUP_DIR` using the SQLite backup API (same logic as `portf-backup.sh`) — pre-restore safety snapshot
  6. Replace live DB using `sqlite3.connect(src).backup(dst)` where src=upload, dst=live DB path
  7. Clean up temp files
- Returns: `{"restored": true, "pre_restore_backup": "<path or null if no backup dir configured>"}`
- On any failure: clean up temps, return 500 with error detail; live DB is untouched (backup happened before replace)

## Frontend — Restore

Location: Import/Export page, alongside the existing "Download DB backup" button.

New "Restore DB backup" button → opens a modal:

| Element | Detail |
|---|---|
| Title | "Restore database backup" |
| Warning | "This will **replace all current data** with the uploaded backup. A pre-restore snapshot will be saved automatically to the backup directory." |
| File input | Accepts `.db` and `.db.gz` |
| "Restore" button | Disabled until a file is selected; red/danger |

On confirm:
- `POST /api/v1/system/restore` with the file as multipart
- On success: show alert "Database restored. Pre-restore backup saved to [path]." Reload the page (forces fresh data everywhere).
- On error: show the server's error message (e.g. "Schema version mismatch — backup is v14, current is v17").

## Out of Scope

- Backup settings UI panel (the Import/Export "Download DB backup" button is sufficient)
- Clearing bookings alongside transactions (separate concern; not requested)
- Soft-delete / undo (hard delete matches the intent; user has the backup)
- Browsing/listing stored backup files from the UI
- Scheduling or triggering the cron backup from the UI (cron handles this)
