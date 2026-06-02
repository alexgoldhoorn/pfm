#!/bin/bash
# Nightly backup of the pfm SQLite database.
#
# Uses SQLite's online backup API (consistent even while the app is running),
# writes a timestamped, gzip-compressed copy to ~/backups/pfm, and keeps the
# most recent N. Scheduled via cron (see docs/BACKUP.md in the pfm repo).
set -euo pipefail

DB="${PFM_DB:-/home/agoldhoorn/repos/pfm/portfolio.db}"
DEST_DIR="${PFM_BACKUP_DIR:-/home/agoldhoorn/backups/pfm}"
KEEP="${PFM_BACKUP_KEEP:-30}"

mkdir -p "$DEST_DIR"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST_DIR/portfolio-$TS.db"

# Consistent online backup via the SQLite backup API, then gzip.
python3 - "$DB" "$OUT" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src)
d = sqlite3.connect(dst)
with d:
    s.backup(d)
d.close(); s.close()
PY
gzip -f "$OUT"

# Retention: keep the newest $KEEP, delete older.
ls -1t "$DEST_DIR"/portfolio-*.db.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

echo "pfm backup -> ${OUT}.gz  (kept newest $KEEP in $DEST_DIR)"
