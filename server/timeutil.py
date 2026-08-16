"""UTC timestamp helpers.

Every timestamp crossing a wire or landing in SQLite uses TS_FORMAT. This is
not a stylistic choice: the device verifier parses the QR's expires_at with
`datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")` (qr_scanner/qr_scanner.py) and
rejects anything else as malformed. Keep these in lockstep.
"""

from datetime import datetime, timedelta, timezone

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_ts(dt: datetime) -> str:
    """Format an aware datetime as the canonical UTC string."""
    return dt.astimezone(timezone.utc).strftime(TS_FORMAT)


def now_ts() -> str:
    return to_ts(utcnow())


def parse_ts(value: str) -> datetime:
    """Parse a canonical UTC string back to an aware datetime."""
    return datetime.strptime(value, TS_FORMAT).replace(tzinfo=timezone.utc)


def is_expired(expires_at: str, now: datetime = None) -> bool:
    """True if expires_at is in the past. Malformed values count as expired."""
    try:
        expires = parse_ts(expires_at)
    except (TypeError, ValueError):
        return True
    return (now or utcnow()) > expires


def age_seconds(ts: str, now: datetime = None) -> float:
    """Seconds elapsed since ts. Returns inf for missing/unparseable values."""
    try:
        then = parse_ts(ts)
    except (TypeError, ValueError):
        return float("inf")
    return ((now or utcnow()) - then).total_seconds()


def plus_minutes(minutes: int, start: datetime = None) -> datetime:
    return (start or utcnow()) + timedelta(minutes=minutes)
