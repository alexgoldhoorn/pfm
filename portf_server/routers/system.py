"""System administration endpoints: DB restore."""

import gzip
import logging
import os
import sqlite3
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile

from portf_manager.database import DATABASE_VERSION, Database
from ..dependencies import get_database, get_api_key_manager
from ..auth_middleware import APIKeyManager, require_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/system", tags=["system"])


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _auto_backup(db: Database, backup_dir: str) -> str | None:
    """Save a pre-restore snapshot using the SQLite backup API."""
    src_path = getattr(db, "db_path", None)
    if not src_path or not os.path.exists(src_path):
        return None
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(backup_dir, f"portfolio-prerestore-{ts}.db")
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(out)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    return out


@router.post("/restore")
async def restore_db(
    file: UploadFile,
    request: Request,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
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
            conn = sqlite3.connect(tmp_db)
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result[0] != "ok":
                raise ValueError("integrity_check failed")
            uploaded_version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.close()
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

        upload_conn = sqlite3.connect(tmp_db)
        live_conn = sqlite3.connect(src_path)
        with live_conn:
            upload_conn.backup(live_conn)
        live_conn.close()
        upload_conn.close()

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
