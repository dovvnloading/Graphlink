"""Basic logging infrastructure.

Nothing in the app ever configured Python's logging module - the one existing
logging.exception() call (graphlink_session/content_codec.py, for corrupted image
data during deserialization) went to Python's "handler of last resort" (stderr),
which is invisible in a windowed app with no console. This wires up a rotating log
file instead so anything that does call logging.* has somewhere durable and
inspectable to land.

This is infrastructure only - it does not convert the app's print()/except: pass call
sites to use logging. That's a much larger, more judgment-heavy change left open.
"""

import logging
import logging.handlers
from pathlib import Path

_LOG_MAX_BYTES = 2 * 1024 * 1024
_LOG_BACKUP_COUNT = 3
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_configured = False


def configure_logging(log_path: Path | str | None = None, level: int = logging.INFO):
    """Attach a rotating file handler to the root logger. Safe to call more than
    once - later calls are no-ops so handlers are never duplicated."""
    global _configured
    if _configured:
        return

    resolved_path = Path(log_path) if log_path is not None else Path.home() / ".graphlink" / "graphlink.log"
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        resolved_path,
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    _configured = True
