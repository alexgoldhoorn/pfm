"""Application logging: one place to configure it, and a persistent log store.

``configure_logging()`` sets the process-wide format and level (``PORTF_LOG_LEVEL``)
and, when ``PORTF_LOG_FILE`` is set, adds a size-rotated file.

``install_db_log_handler(db)`` persists the records worth reading later into the
``app_logs`` table, viewable at ``GET /api/v1/system/logs`` and on the
Diagnostics page:

- every WARNING or worse from pfm's own loggers (``portf_manager``/``portf_server``)
- every record that carries a structured event, e.g. each LLM call
  (``logger.info(..., extra={"pfm_event": "llm.call", "pfm_details": {...}})``)

Messages and details are truncated: log text can echo imported statements.
"""

from __future__ import annotations

import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
PFM_LOGGERS = ("portf_manager", "portf_server")
MAX_MESSAGE_CHARS = 2000
MAX_DETAIL_CHARS = 1000
RETENTION_DAYS_DEFAULT = 30
# Prune old rows every this many inserts (and once at install)
PRUNE_EVERY = 500


def configure_logging(
    level: Optional[str] = None, log_file: Optional[str] = None
) -> None:
    """Configure the root logger once: format, level and optional rotated file.

    Args:
        level: Log level name; defaults to ``PORTF_LOG_LEVEL`` or ``INFO``.
        log_file: Path for a rotated log file; defaults to ``PORTF_LOG_FILE``.
    """
    root = logging.getLogger()
    level = (level or os.getenv("PORTF_LOG_LEVEL") or "INFO").upper()
    root.setLevel(level)
    if not any(getattr(h, "_pfm_console", False) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(LOG_FORMAT))
        console._pfm_console = True
        root.addHandler(console)
    log_file = log_file or os.getenv("PORTF_LOG_FILE")
    if log_file and not any(getattr(h, "_pfm_file", False) for h in root.handlers):
        file_handler = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        file_handler._pfm_file = True
        root.addHandler(file_handler)


def _truncate(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"… [{len(value) - limit} more chars]"
    if isinstance(value, dict):
        return {k: _truncate(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v, limit) for v in value]
    return value


class DatabaseLogHandler(logging.Handler):
    """Writes selected log records to the ``app_logs`` table.

    A failure to write is swallowed (logging must never break the caller), and
    a per-thread guard stops the database's own log lines from recursing.
    """

    def __init__(self, db, retention_days: int = RETENTION_DAYS_DEFAULT):
        super().__init__(level=logging.INFO)
        self.db = db
        self.retention_days = retention_days
        self._busy = threading.local()
        self._inserts = 0

    def wants(self, record: logging.LogRecord) -> bool:
        """True for structured events, and for pfm warnings and worse."""
        if getattr(record, "pfm_event", None):
            return True
        return record.levelno >= logging.WARNING and record.name.startswith(PFM_LOGGERS)

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._busy, "on", False) or not self.wants(record):
            return
        self._busy.on = True
        try:
            message = record.getMessage()
            details = getattr(record, "pfm_details", None) or {}
            if record.exc_info and record.exc_info[1] is not None:
                details = {**details, "exception": repr(record.exc_info[1])}
            self.db.log_event(
                level=record.levelname,
                source=record.name,
                message=_truncate(message, MAX_MESSAGE_CHARS),
                event=getattr(record, "pfm_event", None),
                details=_truncate(details, MAX_DETAIL_CHARS) or None,
            )
            self._inserts += 1
            if self._inserts % PRUNE_EVERY == 0:
                self.db.prune_logs(self.retention_days)
        except Exception:
            pass
        finally:
            self._busy.on = False


_installed: Optional[DatabaseLogHandler] = None


def install_db_log_handler(db) -> Optional[DatabaseLogHandler]:
    """Attach the persistent log handler to the root logger (replacing any old one).

    Returns None when the database adapter has no log store (the Postgres
    adapter doesn't implement it yet).
    """
    global _installed
    remove_db_log_handler()
    if not hasattr(db, "log_event"):
        return None
    retention = int(os.getenv("PORTF_LOG_RETENTION_DAYS", RETENTION_DAYS_DEFAULT))
    handler = DatabaseLogHandler(db, retention_days=retention)
    try:
        db.prune_logs(retention)
    except Exception:
        pass
    logging.getLogger().addHandler(handler)
    _installed = handler
    return handler


def remove_db_log_handler() -> None:
    """Detach the handler installed by install_db_log_handler, if any."""
    global _installed
    if _installed is not None:
        logging.getLogger().removeHandler(_installed)
        _installed = None
