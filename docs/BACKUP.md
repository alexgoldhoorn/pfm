# Backup & Restore

pfm stores everything in a single SQLite file (`portfolio.db`). Back it up.

## What's backed up
The whole database: transactions, dividends, bookings, assets, portfolios,
prices, snapshots, watchlist, goals, allocation/price targets, API keys.

## Automated nightly backup (recommended)
`scripts/portf-backup.sh` (in this repo, deployed to `~/scripts/`) makes a
**consistent online backup** (SQLite backup API — safe while the app runs),
gzips it to `~/backups/pfm/portfolio-YYYYMMDD-HHMMSS.db.gz`, and keeps the
newest 30. Schedule it with cron:

```cron
0 3 * * * /home/you/scripts/portf-backup.sh
```

Tunable via env: `PFM_DB`, `PFM_BACKUP_DIR`, `PFM_BACKUP_KEEP`.
(If you also sync `~/backups` off-box — e.g. to Google Drive — these go with it.)

## On-demand backup
- **Web UI**: Import / Export → Export → **Download DB backup** (`.db`).
- **API**: `GET /api/v1/export/backup` → a consistent `.db` snapshot.
- **Manual**: `cp portfolio.db portfolio.db.bak` (fine when the app is stopped).

## Restore
1. Stop the backend so nothing writes during the swap:
   ```bash
   docker stop portf_backend_dev
   ```
2. Put the backup in place (decompress a nightly one if needed):
   ```bash
   gunzip -c ~/backups/pfm/portfolio-YYYYMMDD-HHMMSS.db.gz > ~/repos/pfm/portfolio.db
   # or, from an on-demand download:
   # cp pfm-backup.db ~/repos/pfm/portfolio.db
   ```
3. Start it again:
   ```bash
   docker start portf_backend_dev
   ```
   Migrations run automatically on startup, so an older backup is upgraded to
   the current schema version if needed.

> Tip: keep a backup **before** large imports or a restore — the
> "Download DB backup" button takes two seconds.
