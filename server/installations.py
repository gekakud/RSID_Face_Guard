"""Unsigned installation pairing, alongside the legacy provisioning routes."""

import base64
import io
import json
import re
import secrets

import qrcode
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field

from server import config, db, models, timeutil, user_store


class CreateInstallation(BaseModel):
    customer_id: str = Field(min_length=1, max_length=64)
    site_id: str = Field(min_length=1, max_length=64)
    door_id: str = Field(min_length=1, max_length=64)
    deviceType: str = Field(default="Genie Door Camera Lite", min_length=1, max_length=128)
    # Per-door operating mode (FR-MODE-01). Carried through the QR so an
    # attendance terminal can be paired as time_registry, not just face_only.
    device_mode: models.DeviceMode = models.DEFAULT_DEVICE_MODE


class PairInstallation(BaseModel):
    installationId: str = Field(min_length=1, max_length=128)
    mac: str = Field(pattern=r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
    serial: str = Field(min_length=1, max_length=128)


def _summary(row):
    return {
        "_id": row["installation_id"], "deviceType": row["device_type"],
        "customer": {"_id": row["customer_id"], "name": row["customer_id"],
                     "serverType": "remote", "serverLink": config.PUBLIC_BASE_URL},
        "venue": row["site_id"],
        "gate": {"_id": row["door_id"], "name": row["door_id"], "status": True},
        "status": row["status"], "device": row["device_id"],
        "device_mode": row["device_mode"] or models.DEFAULT_DEVICE_MODE,
        "timeStamp": row["paired_at"] or row["created_at"],
    }


def install_routes(app, require_admin, hash_token):
    @app.post("/installation", status_code=201, dependencies=[Depends(require_admin)])
    def create_installation(body: CreateInstallation, conn=Depends(db.get_db)):
        installation_id = secrets.token_hex(12)
        now = timeutil.now_ts()
        with conn:
            conn.execute(
                """INSERT INTO installations
                (installation_id, customer_id, site_id, door_id, device_type, device_mode, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (installation_id, body.customer_id, body.site_id, body.door_id,
                 body.deviceType, body.device_mode, now),
            )
        payload = {"installationId": installation_id, "deviceType": body.deviceType,
                   "serverLink": config.PUBLIC_BASE_URL}
        output = io.BytesIO()
        qrcode.make(json.dumps(payload, separators=(",", ":"))).save(output, format="PNG")
        return {"_id": installation_id, "status": "pending", "payload": payload,
                "qr_png": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")}

    @app.get("/installation", dependencies=[Depends(require_admin)])
    def list_installations(conn=Depends(db.get_db)):
        return [_summary(row) for row in conn.execute("SELECT * FROM installations ORDER BY created_at DESC")]

    @app.post("/installation/pair")
    def pair_installation(body: PairInstallation, request: Request, conn=Depends(db.get_db)):
        serial = body.serial.strip().lower()
        if re.fullmatch(r"[0-9a-f]{1,16}", serial) and int(serial, 16):
            serial = serial.zfill(16)
        elif not re.fullmatch(r"dev-[a-z0-9_-]{1,124}", serial):
            raise HTTPException(422, "serial must be a nonzero Raspberry Pi serial or dev- identifier")
        mac = body.mac.upper()
        token = secrets.token_urlsafe(32)
        now = timeutil.now_ts()
        with conn:
            # Serialize contenders before reading; the unique serial index
            # independently prevents duplicate hardware rows.
            conn.execute("BEGIN IMMEDIATE")
            installation = conn.execute("SELECT * FROM installations WHERE installation_id = ?",
                                        (body.installationId,)).fetchone()
            if installation is None:
                raise HTTPException(404, "Installation not found; generate a QR on this server")
            hardware = conn.execute("SELECT * FROM devices WHERE serial = ?", (serial,)).fetchone()
            replay = installation["status"] == "paired"
            if installation["status"] not in ("pending", "paired"):
                raise HTTPException(409, "Installation is no longer active; generate a new QR")
            if replay and (hardware is None or hardware["device_id"] != installation["device_id"]
                           or hardware["mac"] != mac or hardware["state"] != "active"):
                raise HTTPException(409, "Installation is already paired or device was removed; generate a new QR")
            device_mode = installation["device_mode"] or models.DEFAULT_DEVICE_MODE
            device_id = hardware["device_id"] if hardware else secrets.token_hex(12)
            if not replay:
                conn.execute("UPDATE installations SET status = 'replaced' WHERE device_id = ? AND status = 'paired'",
                             (device_id,))
            conn.execute(
                """INSERT INTO devices
                (device_id, name, customer_id, site_id, door_id, device_mode,
                 token_hash, mac, serial, device_type, registered_at, ip_address)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                  name=excluded.name, customer_id=excluded.customer_id, site_id=excluded.site_id,
                  door_id=excluded.door_id, device_mode=excluded.device_mode,
                  token_hash=excluded.token_hash,
                  mac=excluded.mac, device_type=excluded.device_type,
                  registered_at=excluded.registered_at, ip_address=excluded.ip_address,
                  state='active', suspended_at=NULL, last_seen_at=NULL, status=NULL, metadata=NULL""",
                (device_id, installation["door_id"], installation["customer_id"],
                 installation["site_id"], installation["door_id"], device_mode,
                 hash_token(token), mac, serial, installation["device_type"], now,
                 request.client.host if request.client else None),
            )
            if not replay:
                # Temporary demo dataset; do not reset uploaded users on replay.
                user_store.set_for_device(device_id, user_store.load_default_template())
            conn.execute("UPDATE installations SET status='paired', device_id=?, paired_at=? WHERE installation_id=?",
                         (device_id, now, body.installationId))
        result = _summary(conn.execute("SELECT * FROM installations WHERE installation_id=?",
                                       (body.installationId,)).fetchone())
        # Render compatibility extensions for the existing users/status API.
        result.update(device_token=token, heartbeat_interval_sec=config.HEARTBEAT_INTERVAL_SEC,
                      device_mode=device_mode)
        return result

    @app.get("/installation/{installation_id}", dependencies=[Depends(require_admin)])
    def get_installation(installation_id: str, conn=Depends(db.get_db)):
        row = conn.execute("SELECT * FROM installations WHERE installation_id=?", (installation_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Installation not found")
        return _summary(row)
