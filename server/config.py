"""Environment-driven settings for the provisioning / dashboard server.

Everything here has a working default so `uvicorn server.main:app` runs with no
setup at all. Only PUBLIC_BASE_URL and the ADMIN_* pair normally need setting in
a real deployment -- see server/README.md.
"""

import os


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


_HERE = os.path.dirname(os.path.abspath(__file__))

# Where the SQLite file lives. On Render this MUST point at a mounted disk
# (e.g. /var/data/faceguard.db) -- the default filesystem is wiped on deploy.
DB_PATH = os.environ.get("DB_PATH", os.path.join(_HERE, "data", "faceguard.db"))

# Base URL devices call back to. Signed into every QR code as "server_url",
# so devices learn where to register from the QR itself.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

# A device counts as online if it posted status within this many seconds.
# Should be comfortably more than HEARTBEAT_INTERVAL_SEC so one dropped
# heartbeat doesn't flap the device to offline.
HEARTBEAT_TIMEOUT_SEC = _env_int("HEARTBEAT_TIMEOUT_SEC", 90)

# The heartbeat period we hand back to devices at registration.
HEARTBEAT_INTERVAL_SEC = _env_int("HEARTBEAT_INTERVAL_SEC", 30)

# Default lifetime of a provisioning QR code.
DEFAULT_VALIDITY_MINUTES = _env_int("DEFAULT_VALIDITY_MINUTES", 10)

# Status history rows retained per device (oldest trimmed on insert).
STATUS_HISTORY_LIMIT = _env_int("STATUS_HISTORY_LIMIT", 200)

# Device event-log rows retained per device (oldest trimmed on insert). Events
# arrive piggybacked on heartbeat metadata; this caps how much door telemetry
# history is kept so the DB can't grow without bound.
EVENTS_LIMIT = _env_int("EVENTS_LIMIT", 500)

# Dashboard HTTP Basic auth. If either is empty the dashboard is left open,
# which is fine locally but should not be how it sits on a public URL.
ADMIN_USER = os.environ.get("ADMIN_USER", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


def admin_auth_enabled() -> bool:
    return bool(ADMIN_USER and ADMIN_PASSWORD)


# Seed a default Demo Customer/Site/Door on first startup (empty DB) so the
# /new page works immediately without creating anything. Tests turn this off so
# they assert against empty tables.
SEED_DEFAULTS = _env_bool("SEED_DEFAULTS", True)

# JSON file (relative to server/) holding per-device face-user records,
# shaped {device_id: {badge_id: {name, permission_level, faceprints}}}.
# Simple file-backed store for now; server-side user management is TBD.
USER_STORE_FILE = os.environ.get("USER_STORE_FILE", "server_user_database.json")

# JSON file (relative to server/) holding the flat {badge_id: user_data}
# template every new device is seeded with at registration (PoC: this is the
# only source of "who gets access" until real server-side user management
# exists). Admin can later replace a specific device's set via the dashboard.
DEFAULT_USER_DB_FILE = os.environ.get("DEFAULT_USER_DB_FILE", "default_user_database.json")
