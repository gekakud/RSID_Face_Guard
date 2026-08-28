"""Lightweight device event telemetry.

A tiny, dependency-free, thread-safe ring buffer of "something notable
happened" events (door granted, QR rejected, hardware error, ...). Events are
NOT delivered on their own connection: they piggyback on the heartbeat that the
provisioning layer already sends every ``config.HEARTBEAT_INTERVAL_SEC`` -- the
metadata collector calls :func:`snapshot`, the events ride along in the status
POST, and :func:`ack` drops them only once the server has acknowledged receipt.

Design notes
------------
* ``emit()`` is safe to call from any thread (auth thread, card-reader thread,
  relay, GUI) and never raises or blocks -- telemetry must never be able to
  break the door.
* Delivery is *guaranteed* in the sense that events are removed only after a
  successful (2xx) heartbeat: a failed beat leaves them buffered for the next
  one. The only loss case is the bounded deque overflowing during a very long
  server outage, at which point the oldest events are dropped (memory is capped
  rather than growing without bound on an SD-card-backed kiosk).
* Each event carries a uuid4 ``event_id`` so the server can ``INSERT OR IGNORE``
  and stay idempotent if a beat is delivered but its response is lost, causing
  the device to resend.
"""

import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List

from observability.logging_setup import get_logger

log = get_logger("events")

# Matches the server's timestamp format (server/signing.py / timeutil), so the
# dashboard can parse device event times the same way it parses everything else.
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Bounded so a long outage can't grow memory without limit. At ~12 event types
# and typical door traffic this holds many minutes of activity.
_MAX_EVENTS = 200

_lock = threading.Lock()
_buffer: "deque[Dict[str, Any]]" = deque(maxlen=_MAX_EVENTS)


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FMT)


def emit(event_type: str, **fields: Any) -> None:
    """Record an event. Thread-safe, never raises.

    Args:
        event_type: one of the known type strings (device_boot, access_granted,
            access_output_failed, auth_matched, qr_rejected, ...). Free-form;
            the server stores whatever it gets.
        **fields: optional extra context (e.g. user_id="u-8f2c1a", score=471,
            reason="expired"). Kept small -- this is telemetry, not a data dump.
            Access events reference a person by user_id only, never name/card id.
    """
    try:
        event = {
            "event_id": str(uuid.uuid4()),
            "ts": _now_ts(),
            "type": event_type,
        }
        if fields:
            event.update(fields)
        with _lock:
            _buffer.append(event)
        log.debug("event: %s %s", event_type, fields if fields else "")
    except Exception:
        # Telemetry must never break the caller.
        pass


def snapshot() -> List[Dict[str, Any]]:
    """Return buffered events WITHOUT removing them.

    The heartbeat sends this list; the events stay buffered until :func:`ack`
    confirms the server received them, so a failed beat loses nothing.
    """
    with _lock:
        return list(_buffer)


def ack(event_ids) -> None:
    """Drop the acknowledged events after a successful heartbeat.

    ``event_ids`` is the collection of ``event_id`` strings from the
    previously sent :func:`snapshot` that the server confirmed. Events are
    removed **by id, never by position**: the ring may evict old entries or
    gain new ones while a beat is in flight, so positional removal could drop
    undelivered events. Anything not in ``event_ids`` (e.g. emitted mid-beat)
    is preserved for the next beat. Unknown ids are ignored.
    """
    acked = set(event_ids or ())
    if not acked:
        return
    with _lock:
        kept = [e for e in _buffer if e.get("event_id") not in acked]
        _buffer.clear()
        _buffer.extend(kept)


def pending_count() -> int:
    """Number of events currently buffered (for diagnostics/tests)."""
    with _lock:
        return len(_buffer)


def clear() -> None:
    """Drop all buffered events (tests)."""
    with _lock:
        _buffer.clear()