"""SQLite access: connection helper, schema bootstrap, FastAPI dependency.

Plain sqlite3 -- no ORM. The schema is small enough that raw SQL is clearer
than a mapping layer, and it keeps the server's dependency list short.
"""

import os
import sqlite3

from server import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
  token      TEXT PRIMARY KEY,
  nonce      TEXT UNIQUE NOT NULL,
  tenant_id  TEXT NOT NULL,
  site_id    TEXT NOT NULL,
  door_id    TEXT NOT NULL,
  issued_at  TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at    TEXT,
  device_id  TEXT
);

CREATE TABLE IF NOT EXISTS devices (
  device_id     TEXT PRIMARY KEY,
  name          TEXT,
  tenant_id     TEXT,
  site_id       TEXT,
  door_id       TEXT,
  token_hash    TEXT NOT NULL,
  mac           TEXT,
  device_type   TEXT,
  fw_version    TEXT,
  app_version   TEXT,
  ip_address    TEXT,
  registered_at TEXT NOT NULL,
  last_seen_at  TEXT,
  status        TEXT,
  metadata      TEXT
);

CREATE TABLE IF NOT EXISTS status_history (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  ts        TEXT NOT NULL,
  status    TEXT,
  metadata  TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_device ON status_history(device_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_devices_token  ON devices(token_hash);
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


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_db():
    """FastAPI dependency -- one connection per request, always closed."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
