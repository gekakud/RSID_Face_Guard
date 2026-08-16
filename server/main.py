"""FastAPI app: provisioning QR issuance, device registration, status intake,
and the dashboard that displays it all.

Route map
    GET  /                      dashboard: device list (HTML)
    GET  /new                   dashboard: generate a provisioning QR (HTML)
    GET  /device/{id}           dashboard: device detail (HTML)

    POST /devices/generate-qr   mint a one-time token + signed QR image
    POST /devices/register      device redeems the token, gets its credentials
    POST /devices/{id}/status   device heartbeat (Bearer device_token)
    GET  /devices               list devices with derived online/offline
    GET  /devices/{id}          device detail + status history
    GET  /tokens                pending (unused, unexpired) tokens
    GET  /healthz               unauthenticated liveness probe
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

from server import config, db, models, signing, timeutil

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


def _device_summary(row: sqlite3.Row) -> models.DeviceSummary:
    """Row -> summary, deriving online/offline rather than storing it."""
    age = timeutil.age_seconds(row["last_seen_at"]) if row["last_seen_at"] else None
    return models.DeviceSummary(
        device_id=row["device_id"],
        name=row["name"],
        tenant_id=row["tenant_id"],
        site_id=row["site_id"],
        door_id=row["door_id"],
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
    # generate() may re-sign with a fresh nonce until it produces an image the
    # device's decoder can actually read, so persist the payload it returns --
    # not one built separately.
    payload, qr_data_uri = signing.generate(
        tenant_id=body.tenant_id,
        site_id=body.site_id,
        door_id=body.door_id,
        provisioning_token=token,
        validity_minutes=body.validity_minutes,
    )

    conn.execute(
        """INSERT INTO tokens
               (token, nonce, tenant_id, site_id, door_id, issued_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            token,
            payload["nonce"],
            body.tenant_id,
            body.site_id,
            body.door_id,
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
               (device_id, name, tenant_id, site_id, door_id, token_hash, mac,
                device_type, fw_version, app_version, ip_address, registered_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            device_id,
            row["door_id"],
            row["tenant_id"],
            row["site_id"],
            row["door_id"],
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

    return models.RegisterResponse(
        device_id=device_id,
        device_token=device_token,
        heartbeat_interval_sec=config.HEARTBEAT_INTERVAL_SEC,
        tenant_id=row["tenant_id"],
        site_id=row["site_id"],
        door_id=row["door_id"],
    )


@app.post("/devices/{device_id}/status", response_model=models.StatusResponse)
def post_status(
    device_id: str,
    body: models.StatusRequest,
    request: Request,
    device: sqlite3.Row = Depends(device_auth),
    conn: sqlite3.Connection = Depends(db.get_db),
):
    """Device heartbeat: refresh last_seen, replace metadata, append history."""
    now = timeutil.now_ts()
    metadata_json = json.dumps(body.metadata, separators=(",", ":"))

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
# Read APIs
# =====================================================

@app.get(
    "/devices",
    response_model=List[models.DeviceSummary],
    dependencies=[Depends(require_admin)],
)
def list_devices(conn: sqlite3.Connection = Depends(db.get_db)):
    rows = conn.execute(
        "SELECT * FROM devices ORDER BY last_seen_at DESC, registered_at DESC"
    ).fetchall()
    return [_device_summary(row) for row in rows]


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
            tenant_id=row["tenant_id"],
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
