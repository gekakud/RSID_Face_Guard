"""Local storage (disk space) monitoring.

The kiosk runs off an SD card that also holds the rotating log file
(observability/logging_setup.py), the local user DB JSON (db/local_provider.py)
and the device identity file (provisioning/identity.py) -- none of which must
ever be allowed to silently fill the card. This module is a tiny,
dependency-free helper (mirrors observability/events.py in style) that:

  * reports current free space via shutil.disk_usage(), and
  * emits a "storage_low" / "storage_ok" telemetry event exactly once per
    threshold crossing (never every tick), so a long-running low-disk
    condition doesn't flood the event ring buffer.

Like observability/events.py, every public function here is safe to call from
any thread and never raises -- a failed disk check must never affect door
access.
"""

import os
import shutil
import threading
from typing import Any, Dict, Optional

import config
from observability import events
from observability.events import EventType
from observability.logging_setup import get_logger

log = get_logger("storage")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Tracks whether we're currently "below threshold", so emit() only fires on
# the transition, not on every check_storage() call.
_lock = threading.Lock()
_below_threshold = False


def _monitor_path() -> str:
    """Absolute path of the filesystem to monitor (config value may be relative/None)."""
    path = getattr(config, "STORAGE_MONITOR_PATH", None) or PROJECT_ROOT
    return path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)


def check_storage(path: Optional[str] = None) -> Dict[str, Any]:
    """Return current disk usage for `path` (default: the configured monitor path).

    Never raises -- returns an empty dict (with an "error" key) on failure so
    callers (heartbeat metadata collectors) can embed the result unconditionally.

    Also emits a "storage_low"/"storage_ok" event exactly once per threshold
    crossing, and logs a WARNING while free space stays below
    config.STORAGE_MIN_FREE_MB.
    """
    global _below_threshold

    target = path or _monitor_path()
    try:
        usage = shutil.disk_usage(target)
    except Exception as exc:
        log.error("Storage check failed for %s: %s", target, exc)
        events.emit(EventType.HARDWARE_ERROR, where="storage_monitor", error=str(exc))
        return {"path": target, "error": str(exc)}

    total_mb = usage.total / (1024 * 1024)
    used_mb = usage.used / (1024 * 1024)
    free_mb = usage.free / (1024 * 1024)
    free_pct = (usage.free / usage.total * 100.0) if usage.total else 0.0

    min_free_mb = getattr(config, "STORAGE_MIN_FREE_MB", 200)
    is_low = free_mb < min_free_mb

    with _lock:
        was_low = _below_threshold
        _below_threshold = is_low
        crossed = is_low != was_low

    if is_low:
        log.warning(
            "Low disk space on %s: %.1f MB free (< %.1f MB threshold)",
            target, free_mb, min_free_mb,
        )
        if crossed:
            events.emit(
                EventType.STORAGE_LOW, path=target, free_mb=round(free_mb, 1),
                min_free_mb=min_free_mb,
            )
    elif crossed:
        log.info("Disk space recovered on %s: %.1f MB free", target, free_mb)
        events.emit(EventType.STORAGE_OK, path=target, free_mb=round(free_mb, 1))

    return {
        "path": target,
        "total_mb": round(total_mb, 1),
        "used_mb": round(used_mb, 1),
        "free_mb": round(free_mb, 1),
        "free_pct": round(free_pct, 1),
        "low": is_low,
    }


def get_storage_metadata() -> Dict[str, Any]:
    """Best-effort storage snapshot for embedding in heartbeat metadata.

    Never raises -- swallows any unexpected error and returns {} instead,
    same guarantee as observability/events.emit().
    """
    try:
        return check_storage()
    except Exception:
        log.exception("Unexpected error collecting storage metadata")
        return {}
