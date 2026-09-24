"""System administration endpoints: DB restore and the application log."""

import contextlib
import gzip
import logging
import os
import sqlite3
import tempfile
from datetime import datetime

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile

from portf_manager.database import DATABASE_VERSION, Database
from ..dependencies import get_database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/system", tags=["system"])


def _auto_backup(db: Database, backup_dir: str) -> str | None:
    """Save a pre-restore snapshot using the SQLite backup API."""
    src_path = getattr(db, "db_path", None)
    if not src_path or not os.path.exists(src_path):
        return None
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(backup_dir, f"portfolio-prerestore-{ts}.db")
    with (
        contextlib.closing(sqlite3.connect(src_path)) as src,
        contextlib.closing(sqlite3.connect(out)) as dst,
    ):
        with dst:
            src.backup(dst)
    return out


@router.get("/logs")
def list_logs(
    limit: int = Query(200, ge=1, le=1000),
    level: Optional[Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]] = Query(
        None, description="Lowest level to include, e.g. WARNING"
    ),
    event: Optional[str] = Query(
        None, description="Event name, or a prefix ending in * (llm.*)"
    ),
    source: Optional[str] = Query(None, description="Logger-name prefix"),
    db: Database = Depends(get_database),
) -> dict:
    """Recent application log entries, newest first.

    Holds pfm warnings/errors and every LLM call (``event="llm.call"``, with
    provider, model, attempts, duration and errors in ``details``).
    """
    if not hasattr(db, "list_logs"):
        raise HTTPException(
            status_code=501, detail="This database backend has no log store"
        )
    return {
        "items": db.list_logs(limit=limit, min_level=level, event=event, source=source)
    }


@router.post("/restore")
async def restore_db(
    file: UploadFile,
    db: Database = Depends(get_database),
):
    """Replace the live SQLite database with an uploaded backup.

    Accepts .db or .db.gz files. Validates integrity and schema version before
    replacing. Saves a pre-restore snapshot to PFM_BACKUP_DIR if configured.
    """
    src_path = getattr(db, "db_path", None)
    if not src_path:
        raise HTTPException(
            status_code=503,
            detail="Restore is only available for the SQLite backend.",
        )

    raw = await file.read()
    fname = file.filename or ""

    tmp_db = None
    try:
        fd, tmp_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        if fname.endswith(".gz"):
            try:
                raw = gzip.decompress(raw)
            except Exception:
                raise HTTPException(
                    status_code=422, detail="Could not decompress .gz file."
                )

        with open(tmp_db, "wb") as f:
            f.write(raw)

        try:
            with contextlib.closing(sqlite3.connect(tmp_db)) as conn:
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if result[0] != "ok":
                    raise ValueError("integrity_check failed")
                uploaded_version = conn.execute("PRAGMA user_version").fetchone()[0]
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=422,
                detail="Not a valid SQLite database.",
            )

        if uploaded_version != DATABASE_VERSION:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Schema version mismatch: backup is v{uploaded_version}, "
                    f"current app requires v{DATABASE_VERSION}."
                ),
            )

        backup_dir = os.environ.get("PFM_BACKUP_DIR", "")
        pre_restore_backup = None
        if backup_dir:
            try:
                pre_restore_backup = _auto_backup(db, backup_dir)
            except Exception as e:
                logger.warning("Pre-restore backup failed: %s", e)

        with (
            contextlib.closing(sqlite3.connect(tmp_db)) as upload_conn,
            contextlib.closing(sqlite3.connect(src_path)) as live_conn,
        ):
            with live_conn:
                upload_conn.backup(live_conn)

        logger.info(
            "Database restored from %s; pre-restore backup: %s",
            fname,
            pre_restore_backup,
        )
        return {"restored": True, "pre_restore_backup": pre_restore_backup}

    finally:
        if tmp_db and os.path.exists(tmp_db):
            try:
                os.unlink(tmp_db)
            except OSError:
                pass
