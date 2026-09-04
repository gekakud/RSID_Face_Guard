"""FastAPI app: provisioning QR issuance, device registration, status intake,
and the dashboard that displays it all.

Route map
    GET    /                      dashboard: device list (HTML)
    GET    /new                   dashboard: generate a provisioning QR (HTML)
    GET    /device/{id}           dashboard: device detail (HTML)

    POST   /devices/generate-qr   mint a one-time token + signed QR image
    POST   /devices/register      device redeems the token, gets its credentials
    POST   /devices/{id}/status   device heartbeat (Bearer device_token)
    GET    /devices/{id}/users    device: fetch its assigned face users (Bearer device_token)
    POST   /devices/{id}/users    dashboard: replace a device's assigned face users
    GET    /devices               list devices with derived online/offline
    GET    /devices/{id}          device detail + status history
    DELETE /devices/{id}          soft-delete (suspend) a device
    GET    /devices/{id}/events   recent device events, optionally filtered by type
    DELETE /devices/{id}/events   clear a device's stored event log
    GET    /tokens                pending (unused, unexpired) tokens
    GET    /healthz               unauthenticated liveness probe

    GET/POST /customers           customer CRUD (idempotent by name)
    GET/POST /sites?customer_id=  site CRUD (idempotent by name within customer)
    GET/POST /doors?site_id=      door CRUD (idempotent by name within site)

    All dashboard endpoints above are HTTP-Basic-authed via require_admin when
    ADMIN_USER/ADMIN_PASSWORD are set; device endpoints (register, status,
    users GET) are authed differently -- the provisioning token or the
    per-device Bearer device_token, respectively.
"""

import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)
from fastapi.templating import Jinja2Templates

from server import config, db, models, signing, timeutil, user_store

_HERE = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(_HERE, "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Face Guard Device Dashboard", version="0.1.0", lifespan=lifespan)


# =====================================================
# Helpers
# =====================================================

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _json_loads(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _client_ip(request: Request) -> Optional[str]:
    # Render (and any reverse proxy) puts the real client first in X-Forwarded-For.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _ingest_events(
    conn: sqlite3.Connection,
    device_id: str,
    device_events: Any,
    received_at: str,
) -> None:
    """Store events carried on a heartbeat, idempotently and bounded.

    Each event is keyed by its device-generated event_id, so a heartbeat that
    was delivered but whose response was lost (and therefore resent by the
    device) does not create duplicates -- INSERT OR IGNORE drops the repeat.
    After inserting, the per-device event log is trimmed to EVENTS_LIMIT rows.
    """
    if not isinstance(device_events, list) or not device_events:
        return

    inserted = False
    for ev in device_events:
        if not isinstance(ev, dict):
            continue
        event_id = ev.get("event_id")
        event_type = ev.get("type")
        if not event_id or not event_type:
            continue
        ts = ev.get("ts") or received_at
        # Everything that isn't a reserved key is context "data".
        data = {k: v for k, v in ev.items() if k not in ("event_id", "ts", "type")}
        conn.execute(
            """INSERT OR IGNORE INTO events
                   (event_id, device_id, ts, received_at, type, data)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                device_id,
                ts,
                received_at,
                event_type,
                json.dumps(data, separators=(",", ":")) if data else None,
            ),
        )
        inserted = True

    if inserted:
        conn.execute(
            """DELETE FROM events
                WHERE device_id = ?
                  AND id NOT IN (
                      SELECT id FROM events
                       WHERE device_id = ? ORDER BY id DESC LIMIT ?
                  )""",
            (device_id, device_id, config.EVENTS_LIMIT),
        )

def _device_summary(row: sqlite3.Row) -> models.DeviceSummary:
    """Row -> summary, deriving online/offline rather than storing it."""
    age = timeutil.age_seconds(row["last_seen_at"]) if row["last_seen_at"] else None
    return models.DeviceSummary(
        device_id=row["device_id"],
        name=row["name"],
        customer_id=row["customer_id"],
        site_id=row["site_id"],
        door_id=row["door_id"],
        network_profile=_json_loads(row["network_profile"]),
        mac=row["mac"],
        device_type=row["device_type"],
        fw_version=row["fw_version"],
        app_version=row["app_version"],
        ip_address=row["ip_address"],
        registered_at=row["registered_at"],
        last_seen_at=row["last_seen_at"],
        status=row["status"],
        online=age is not None and age <= config.HEARTBEAT_TIMEOUT_SEC,
        last_seen_age_sec=age,
        metadata=_json_loads(row["metadata"]),
        state=row["state"] if "state" in row.keys() else "active",
        suspended_at=row["suspended_at"] if "suspended_at" in row.keys() else None,
    )


# =====================================================
# Auth
# =====================================================

_basic = HTTPBasic(auto_error=False)
_bearer = HTTPBearer(auto_error=False)


def require_admin(credentials: Optional[HTTPBasicCredentials] = Depends(_basic)) -> None:
    """Dashboard-side auth. A no-op unless ADMIN_USER and ADMIN_PASSWORD are set,
    which keeps local development frictionless."""
    if not config.admin_auth_enabled():
        return

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Basic"},
    )
    if credentials is None:
        raise unauthorized

    # compare_digest on both halves so neither is a timing oracle.
    user_ok = secrets.compare_digest(credentials.username, config.ADMIN_USER)
    pass_ok = secrets.compare_digest(credentials.password, config.ADMIN_PASSWORD)
    if not (user_ok and pass_ok):
        raise unauthorized


def device_auth(
    device_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    conn: sqlite3.Connection = Depends(db.get_db),
) -> sqlite3.Row:
    """Device-side auth for /devices/{id}/status.

    Resolves the bearer token to a device and then checks that device is the one
    named in the path -- otherwise a valid device could post status on behalf of
    any other device.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    row = conn.execute(
        "SELECT * FROM devices WHERE token_hash = ?",
        (_hash_token(credentials.credentials),),
    ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid device token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if row["device_id"] != device_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not belong to this device",
        )
    return row


# =====================================================
# Provisioning
# =====================================================

@app.post(
    "/devices/generate-qr",
    response_model=models.GenerateQRResponse,
    dependencies=[Depends(require_admin)],
)
def generate_qr(
    body: models.GenerateQRRequest,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Mint a one-time provisioning token and return it as a signed QR image."""
    token = secrets.token_urlsafe(24)
    network_profile = body.network_profile.model_dump()
    # generate() may re-sign with a fresh nonce until it produces an image the
    # device's decoder can actually read, so persist the payload it returns --
    # not one built separately.
    payload, qr_data_uri = signing.generate(
        customer_id=body.customer_id,
        site_id=body.site_id,
        door_id=body.door_id,
        provisioning_token=token,
        validity_minutes=body.validity_minutes,
        network_profile=network_profile,
    )

    conn.execute(
        """INSERT INTO tokens
               (token, nonce, customer_id, site_id, door_id, network_profile,
                issued_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            token,
            payload["nonce"],
            body.customer_id,
            body.site_id,
            body.door_id,
            json.dumps(network_profile, separators=(",", ":")),
            payload["issued_at"],
            payload["expires_at"],
        ),
    )
    conn.commit()

    return models.GenerateQRResponse(
        token=token,
        nonce=payload["nonce"],
        issued_at=payload["issued_at"],
        expires_at=payload["expires_at"],
        payload=payload,
        qr_png=qr_data_uri,
    )


@app.post("/devices/register", response_model=models.RegisterResponse)
def register_device(
    body: models.RegisterRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Redeem a provisioning token and issue the device its long-lived credentials.

    Expiry is re-checked here even though the device already verified the signed
    expires_at -- a device could be replaying an old capture, so the server never
    delegates that decision.
    """
    row = conn.execute("SELECT * FROM tokens WHERE token = ?", (body.token,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown provisioning token")
    if row["used_at"] is not None:
        raise HTTPException(status_code=409, detail="Provisioning token already used")
    if timeutil.is_expired(row["expires_at"]):
        raise HTTPException(status_code=400, detail="Provisioning token expired")
    if body.nonce is not None and body.nonce != row["nonce"]:
        raise HTTPException(status_code=400, detail="Nonce does not match token")

    device_id = str(uuid.uuid4())
    device_token = secrets.token_urlsafe(32)
    now = timeutil.now_ts()

    conn.execute(
        """INSERT INTO devices
               (device_id, name, customer_id, site_id, door_id, network_profile,
                token_hash, mac, device_type, fw_version, app_version,
                ip_address, registered_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            device_id,
            row["door_id"],
            row["customer_id"],
            row["site_id"],
            row["door_id"],
            row["network_profile"],
            _hash_token(device_token),
            body.mac,
            body.device_type,
            body.fw_version,
            body.app_version,
            _client_ip(request),
            now,
        ),
    )
    # Burn the token in the same transaction as the insert, so a crash between
    # the two can't leave a redeemable token pointing at a live device.
    conn.execute(
        "UPDATE tokens SET used_at = ?, device_id = ? WHERE token = ?",
        (now, device_id, body.token),
    )
    conn.commit()

    # PoC seeding: every freshly-registered device starts with the server's
    # default user template, so its very first sync already has data instead
    # of needing a manual dashboard upload. Admin can replace it per-device
    # later via POST /devices/{id}/users.
    user_store.set_for_device(device_id, user_store.load_default_template())

    return models.RegisterResponse(
        device_id=device_id,
        device_token=device_token,
        heartbeat_interval_sec=config.HEARTBEAT_INTERVAL_SEC,
        customer_id=row["customer_id"],
        site_id=row["site_id"],
        door_id=row["door_id"],
        registered_at=now,
    )


@app.get("/devices/{device_id}/users")
def get_device_users(
    device_id: str,
    device: sqlite3.Row = Depends(device_auth),
):
    """Return this device's assigned face users, keyed by badge_id.

    Auth via the same bearer device_token as heartbeats (device_auth already
    checks the token belongs to device_id). Response shape matches the
    device's local user_database.json exactly, so RemoteUserDataProvider can
    write it straight into the local cache.
    """
    return user_store.get_for_device(device_id)

@app.post("/devices/{device_id}/users", dependencies=[Depends(require_admin)])
def assign_device_users(
    device_id: str,
    body: Dict[str, Any],
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Dashboard-side: assign/replace this device's whole user_database.json.

    PoC-level: whole-file replace, no per-user validation beyond "is a dict" --
    the dashboard's upload UI parses the JSON client-side already.
    """
    if conn.execute(
        "SELECT 1 FROM devices WHERE device_id = ?", (device_id,)
    ).fetchone() is None:
        raise HTTPException(status_code=404, detail="Unknown device")
    user_store.set_for_device(device_id, body)
    return {"ok": True, "device_id": device_id, "user_count": len(body)}


@app.post("/devices/{device_id}/status", response_model=models.StatusResponse)
def post_status(
    device_id: str,
    body: models.StatusRequest,
    request: Request,
    device: sqlite3.Row = Depends(device_auth),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Device heartbeat: refresh last_seen, replace metadata, append history.

    Events piggyback on metadata under the "events" key (see observability/
    events.py). They are pulled out into the events table and stripped from the
    stored metadata so the dashboard's "latest metadata" panel stays clean.
    """
    # If an operator removed this device, its row is kept as a tombstone so this
    # heartbeat can be told the device was revoked. Reply 410 Gone (the device
    # drops its identity on seeing it) and flip the row to revoked_ack so it can
    # be purged. Don't record the heartbeat.
    if device["state"] == "suspended":
        conn.execute(
            "UPDATE devices SET state = 'revoked_ack' WHERE device_id = ?",
            (device_id,),
        )
        conn.commit()
        raise HTTPException(status_code=410, detail="Device was removed")
    if device["state"] == "revoked_ack":
        # Device somehow beat again before being purged -- keep telling it.
        raise HTTPException(status_code=410, detail="Device was removed")

    now = timeutil.now_ts()

    metadata = dict(body.metadata)
    device_events = metadata.pop("events", [])
    metadata_json = json.dumps(metadata, separators=(",", ":"))

    conn.execute(
        """UPDATE devices
              SET last_seen_at = ?, status = ?, metadata = ?, ip_address = ?
            WHERE device_id = ?""",
        (now, body.status, metadata_json, _client_ip(request), device_id),
    )
    conn.execute(
        "INSERT INTO status_history (device_id, ts, status, metadata) VALUES (?, ?, ?, ?)",
        (device_id, now, body.status, metadata_json),
    )
    _ingest_events(conn, device_id, device_events, now)
    # Keep the history bounded -- a device heartbeating every 30s would otherwise
    # add ~2900 rows a day, forever.
    conn.execute(
        """DELETE FROM status_history
            WHERE device_id = ?
              AND id NOT IN (
                  SELECT id FROM status_history
                   WHERE device_id = ? ORDER BY id DESC LIMIT ?
              )""",
        (device_id, device_id, config.STATUS_HISTORY_LIMIT),
    )
    conn.commit()

    return models.StatusResponse(ok=True, server_time=now)


# =====================================================
# Customer / Site / Door management (dashboard dropdowns)
# =====================================================

def _get_or_create(
    conn: sqlite3.Connection, table: str, name: str, parent_col: Optional[str] = None,
    parent_val: Optional[int] = None,
) -> sqlite3.Row:
    """Insert a named record (idempotently) and return the resulting row.

    If a record with the same name (under the same parent) already exists, it is
    returned rather than erroring -- so the dashboard's "+ add" is safe to click
    twice and re-selecting an existing name just resolves to it.
    """
    now = timeutil.now_ts()
    if parent_col is not None:
        existing = conn.execute(
            f"SELECT * FROM {table} WHERE {parent_col} = ? AND name = ?",
            (parent_val, name),
        ).fetchone()
        if existing is not None:
            return existing
        cur = conn.execute(
            f"INSERT INTO {table} ({parent_col}, name, created_at) VALUES (?, ?, ?)",
            (parent_val, name, now),
        )
    else:
        existing = conn.execute(
            f"SELECT * FROM {table} WHERE name = ?", (name,)
        ).fetchone()
        if existing is not None:
            return existing
        cur = conn.execute(
            f"INSERT INTO {table} (name, created_at) VALUES (?, ?)", (name, now)
        )
    conn.commit()
    return conn.execute(
        f"SELECT * FROM {table} WHERE id = ?", (cur.lastrowid,)
    ).fetchone()

@app.get(
    "/customers",
    response_model=List[models.Customer],
    dependencies=[Depends(require_admin)],
)
def list_customers(conn: sqlite3.Connection = Depends(db.get_db)):
    rows = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
    return [models.Customer(id=r["id"], name=r["name"]) for r in rows]

@app.post(
    "/customers",
    response_model=models.Customer,
    dependencies=[Depends(require_admin)],
)
def create_customer(
    body: models.CreateCustomerRequest, conn: sqlite3.Connection = Depends(db.get_db)
):
    row = _get_or_create(conn, "customers", body.name.strip())
    return models.Customer(id=row["id"], name=row["name"])

@app.get(
    "/sites",
    response_model=List[models.Site],
    dependencies=[Depends(require_admin)],
)
def list_sites(customer_id: int, conn: sqlite3.Connection = Depends(db.get_db)):
    rows = conn.execute(
        "SELECT id, customer_id, name FROM sites WHERE customer_id = ? ORDER BY name",
        (customer_id,),
    ).fetchall()
    return [
        models.Site(id=r["id"], customer_id=r["customer_id"], name=r["name"])
        for r in rows
    ]

@app.post(
    "/sites",
    response_model=models.Site,
    dependencies=[Depends(require_admin)],
)
def create_site(
    body: models.CreateSiteRequest, conn: sqlite3.Connection = Depends(db.get_db)
):
    if conn.execute(
        "SELECT 1 FROM customers WHERE id = ?", (body.customer_id,)
    ).fetchone() is None:
        raise HTTPException(status_code=404, detail="Unknown customer")
    row = _get_or_create(
        conn, "sites", body.name.strip(), "customer_id", body.customer_id
    )
    return models.Site(id=row["id"], customer_id=row["customer_id"], name=row["name"])

@app.get(
    "/doors",
    response_model=List[models.Door],
    dependencies=[Depends(require_admin)],
)
def list_doors(site_id: int, conn: sqlite3.Connection = Depends(db.get_db)):
    rows = conn.execute(
        "SELECT id, site_id, name FROM doors WHERE site_id = ? ORDER BY name",
        (site_id,),
    ).fetchall()
    return [models.Door(id=r["id"], site_id=r["site_id"], name=r["name"]) for r in rows]

@app.post(
    "/doors",
    response_model=models.Door,
    dependencies=[Depends(require_admin)],
)
def create_door(
    body: models.CreateDoorRequest, conn: sqlite3.Connection = Depends(db.get_db)
):
    if conn.execute(
        "SELECT 1 FROM sites WHERE id = ?", (body.site_id,)
    ).fetchone() is None:
        raise HTTPException(status_code=404, detail="Unknown site")
    row = _get_or_create(conn, "doors", body.name.strip(), "site_id", body.site_id)
    return models.Door(id=row["id"], site_id=row["site_id"], name=row["name"])

# =====================================================
# Read APIs
# =====================================================

@app.get(
    "/devices",
    response_model=List[models.DeviceSummary],
    dependencies=[Depends(require_admin)],
)
def list_devices(conn: sqlite3.Connection = Depends(db.get_db)):
    # Sweep out devices that have acknowledged their removal before listing, so
    # the tombstone disappears once the device has actually dropped its identity.
    _purge_acknowledged(conn)
    rows = conn.execute(
        "SELECT * FROM devices ORDER BY last_seen_at DESC, registered_at DESC"
    ).fetchall()
    return [_device_summary(row) for row in rows]


@app.delete(
    "/devices/{device_id}",
    response_model=models.DeleteDeviceResponse,
    dependencies=[Depends(require_admin)],
)
def delete_device(device_id: str, conn: sqlite3.Connection = Depends(db.get_db)):
    """Remove a device: suspend it (soft delete).

    The row is kept as a tombstone rather than hard-deleted, so the device's
    next heartbeat can be answered with 410 and it can drop its own identity.
    Once the device acknowledges (its heartbeat flips it to revoked_ack) the
    tombstone is purged on the next device-list load. Idempotent.
    """
    row = conn.execute(
        "SELECT state FROM devices WHERE device_id = ?", (device_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown device")

    # Only move active -> suspended; leave an already-acknowledged removal alone.
    if row["state"] == "active":
        conn.execute(
            "UPDATE devices SET state = 'suspended', suspended_at = ? WHERE device_id = ?",
            (timeutil.now_ts(), device_id),
        )
        conn.commit()
        new_state = "suspended"
    else:
        new_state = row["state"]

    return models.DeleteDeviceResponse(ok=True, device_id=device_id, state=new_state)

def _purge_acknowledged(conn: sqlite3.Connection) -> None:
    """Hard-delete devices that have acknowledged removal, and their history."""
    acked = [
        r["device_id"]
        for r in conn.execute(
            "SELECT device_id FROM devices WHERE state = 'revoked_ack'"
        ).fetchall()
    ]
    if not acked:
        return
    for device_id in acked:
        conn.execute("DELETE FROM status_history WHERE device_id = ?", (device_id,))
        conn.execute("DELETE FROM events WHERE device_id = ?", (device_id,))
        conn.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
    conn.commit()

@app.get(
    "/tokens",
    response_model=List[models.PendingToken],
    dependencies=[Depends(require_admin)],
)
def list_pending_tokens(conn: sqlite3.Connection = Depends(db.get_db)):
    """Unredeemed tokens, so a half-finished enrollment is visible."""
    rows = conn.execute(
        "SELECT * FROM tokens WHERE used_at IS NULL ORDER BY issued_at DESC"
    ).fetchall()
    return [
        models.PendingToken(
            token=row["token"],
            customer_id=row["customer_id"],
            site_id=row["site_id"],
            door_id=row["door_id"],
            issued_at=row["issued_at"],
            expires_at=row["expires_at"],
            expired=timeutil.is_expired(row["expires_at"]),
        )
        for row in rows
    ]


@app.get(
    "/devices/{device_id}",
    response_model=models.DeviceDetail,
    dependencies=[Depends(require_admin)],
)
def get_device(device_id: str, conn: sqlite3.Connection = Depends(db.get_db)):
    row = conn.execute(
        "SELECT * FROM devices WHERE device_id = ?", (device_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown device")

    history = conn.execute(
        "SELECT ts, status, metadata FROM status_history WHERE device_id = ? ORDER BY id DESC LIMIT 50",
        (device_id,),
    ).fetchall()

    return models.DeviceDetail(
        **_device_summary(row).model_dump(),
        history=[
            models.StatusHistoryEntry(
                ts=h["ts"], status=h["status"], metadata=_json_loads(h["metadata"])
            )
            for h in history
        ],
    )


@app.get(
    "/devices/{device_id}/events",
    response_model=List[models.DeviceEvent],
    dependencies=[Depends(require_admin)],
)
def list_device_events(
    device_id: str,
    limit: int = 100,
    type: Optional[str] = None,
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Recent device events, newest first, optionally filtered by type."""
    limit = max(1, min(limit, config.EVENTS_LIMIT))
    if type:
        rows = conn.execute(
            """SELECT ts, received_at, type, data FROM events
                WHERE device_id = ? AND type = ? ORDER BY id DESC LIMIT ?""",
            (device_id, type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT ts, received_at, type, data FROM events
                WHERE device_id = ? ORDER BY id DESC LIMIT ?""",
            (device_id, limit),
        ).fetchall()
    return [
        models.DeviceEvent(
            ts=r["ts"],
            received_at=r["received_at"],
            type=r["type"],
            data=_json_loads(r["data"]),
        )
        for r in rows
    ]


@app.delete(
    "/devices/{device_id}/events",
    response_model=models.ClearEventsResponse,
    dependencies=[Depends(require_admin)],
)
def clear_device_events(
    device_id: str, conn: sqlite3.Connection = Depends(db.get_db)
):
    """Clear this device's stored event log.

    Removes only the `events` rows for this device -- the device row, its
    status history, and its assigned users are left untouched. Idempotent:
    clearing an already-empty log returns deleted=0. Because ingestion is keyed
    by device-generated event_id, cleared events are not re-created unless the
    device actually resends them on a later heartbeat.
    """
    if conn.execute(
        "SELECT 1 FROM devices WHERE device_id = ?", (device_id,)
    ).fetchone() is None:
        raise HTTPException(status_code=404, detail="Unknown device")

    cur = conn.execute("DELETE FROM events WHERE device_id = ?", (device_id,))
    conn.commit()
    return models.ClearEventsResponse(
        ok=True, device_id=device_id, deleted=cur.rowcount
    )

@app.get("/healthz")
def healthz():
    return {"ok": True, "server_time": timeutil.now_ts()}


# =====================================================
# Dashboard (HTML)
# =====================================================

@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def page_devices(request: Request):
    # The table body is rendered client-side from GET /devices so the initial
    # paint and the 5s refresh share one code path instead of two.
    return templates.TemplateResponse(
        request,
        "devices.html",
        {"heartbeat_timeout": config.HEARTBEAT_TIMEOUT_SEC},
    )


@app.get("/new", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def page_new_device(request: Request):
    return templates.TemplateResponse(
        request,
        "new.html",
        {"default_validity": config.DEFAULT_VALIDITY_MINUTES},
    )


@app.get(
    "/device/{device_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin)],
)
def page_device_detail(
    device_id: str, request: Request, conn: sqlite3.Connection = Depends(db.get_db)
):
    detail = get_device(device_id, conn)
    return templates.TemplateResponse(
        request, "device_detail.html", {"device": detail}
    )
