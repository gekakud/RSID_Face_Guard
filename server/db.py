"""SQLite access: connection helper, schema bootstrap, FastAPI dependency.

Plain sqlite3 -- no ORM. The schema is small enough that raw SQL is clearer
than a mapping layer, and it keeps the server's dependency list short.
"""

import os
import sqlite3

from server import config

SCHEMA = """
-- Managed customer/site/door hierarchy the dashboard selects from when
-- issuing a QR. A door belongs to a site, a site belongs to a customer. Names
-- are unique within their parent so the dropdowns can't accumulate duplicates.
CREATE TABLE IF NOT EXISTS customers (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sites (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  name        TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  UNIQUE (customer_id, name)
);

CREATE TABLE IF NOT EXISTS doors (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id    INTEGER NOT NULL REFERENCES sites(id),
  name       TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (site_id, name)
);

CREATE TABLE IF NOT EXISTS tokens (
  token           TEXT PRIMARY KEY,
  nonce           TEXT UNIQUE NOT NULL,
  customer_id     TEXT NOT NULL,
  site_id         TEXT NOT NULL,
  door_id         TEXT NOT NULL,
  network_profile TEXT,                -- JSON of the signed network_profile
  issued_at       TEXT NOT NULL,
  expires_at      TEXT NOT NULL,
  used_at         TEXT,
  device_id       TEXT
);

CREATE TABLE IF NOT EXISTS devices (
  device_id       TEXT PRIMARY KEY,
  name            TEXT,
  customer_id     TEXT,
  site_id         TEXT,
  door_id         TEXT,
  network_profile TEXT,                -- JSON of the network_profile it was given
  token_hash      TEXT NOT NULL,
  mac             TEXT,
  device_type     TEXT,
  fw_version      TEXT,
  app_version     TEXT,
  ip_address      TEXT,
  registered_at   TEXT NOT NULL,
  last_seen_at    TEXT,
  status          TEXT,               -- device-reported: online/offline/shutting_down
  metadata        TEXT,
  -- Admin lifecycle state, distinct from the device-reported `status`:
  --   'active'      normal.
  --   'suspended'   operator removed it; still tombstoned so the device's next
  --                 heartbeat can be told (410) to drop its identity.
  --   'revoked_ack' the device acknowledged removal; safe to purge.
  state           TEXT NOT NULL DEFAULT 'active',
  suspended_at    TEXT
);

CREATE TABLE IF NOT EXISTS status_history (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  ts        TEXT NOT NULL,
  status    TEXT,
  metadata  TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id    TEXT UNIQUE NOT NULL,   -- device-generated uuid; enables idempotent resend
  device_id   TEXT NOT NULL,
  ts          TEXT NOT NULL,          -- device-supplied event time
  received_at TEXT NOT NULL,          -- server time the beat carrying it arrived
  type        TEXT NOT NULL,
  data        TEXT                    -- JSON of any extra fields (may be NULL/'{}')
);

-- Face-auth users, owned entirely by the server (server creates/assigns,
-- devices only read). Each user is assigned to one door, so
-- GET /devices/{id}/users is a lookup by the device's door_id.
CREATE TABLE IF NOT EXISTS users (
  badge_id         TEXT PRIMARY KEY,
  name             TEXT,
  permission_level TEXT NOT NULL DEFAULT 'User',
  faceprints       TEXT NOT NULL,     -- JSON blob
  door_id          INTEGER NOT NULL REFERENCES doors(id),
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_device ON status_history(device_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_devices_token  ON devices(token_hash);
CREATE INDEX IF NOT EXISTS idx_events_device  ON events(device_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_sites_customer ON sites(customer_id);
CREATE INDEX IF NOT EXISTS idx_doors_site     ON doors(site_id);
CREATE INDEX IF NOT EXISTS idx_users_door     ON users(door_id);
"""


def connect() -> sqlite3.Connection:
    """Open a connection to the configured database, creating its dir if needed."""
    directory = os.path.dirname(os.path.abspath(config.DB_PATH))
    os.makedirs(directory, exist_ok=True)

    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL lets the dashboard's 5s poll read while a device is writing a heartbeat.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# A default customer/site/door so a developer can open /new and generate a QR
# without creating anything first. Seeded only when the tables are empty, so it
# never duplicates or shadows real data.
_DEFAULT_CUSTOMER = "Demo Customer"
_DEFAULT_SITE = "Demo Site"
_DEFAULT_DOOR = "Demo Door"

def _seed_defaults(conn: sqlite3.Connection) -> None:
    """Insert the demo customer/site/door if there are no customers yet."""
    if not config.SEED_DEFAULTS:
        return
    if conn.execute("SELECT 1 FROM customers LIMIT 1").fetchone() is not None:
        return
    from server import timeutil

    now = timeutil.now_ts()
    cur = conn.execute(
        "INSERT INTO customers (name, created_at) VALUES (?, ?)",
        (_DEFAULT_CUSTOMER, now),
    )
    customer_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO sites (customer_id, name, created_at) VALUES (?, ?, ?)",
        (customer_id, _DEFAULT_SITE, now),
    )
    site_id = cur.lastrowid
    conn.execute(
        "INSERT INTO doors (site_id, name, created_at) VALUES (?, ?, ?)",
        (site_id, _DEFAULT_DOOR, now),
    )
    conn.commit()

def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        _seed_defaults(conn)
    finally:
        conn.close()


def get_db():
    """FastAPI dependency -- one connection per request, always closed."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
