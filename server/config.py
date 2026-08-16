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

# Dashboard HTTP Basic auth. If either is empty the dashboard is left open,
# which is fine locally but should not be how it sits on a public URL.
ADMIN_USER = os.environ.get("ADMIN_USER", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


def admin_auth_enabled() -> bool:
    return bool(ADMIN_USER and ADMIN_PASSWORD)
