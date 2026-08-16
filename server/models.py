"""Request/response models.

These double as the API contract documented in server/README.md -- the device
side will send exactly these shapes.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from server import config


# ---------------------------------------------------------------- dashboard ->

class GenerateQRRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    site_id: str = Field(min_length=1, max_length=64)
    door_id: str = Field(min_length=1, max_length=64)
    validity_minutes: int = Field(default=config.DEFAULT_VALIDITY_MINUTES, ge=0, le=1440)


class GenerateQRResponse(BaseModel):
    token: str
    nonce: str
    issued_at: str
    expires_at: str
    payload: Dict[str, Any]      # the full signed QR payload, for debugging
    qr_png: str                  # "data:image/png;base64,..." -- drop into <img src>


# ------------------------------------------------------------------ device ->

class RegisterRequest(BaseModel):
    token: str = Field(min_length=1)
    nonce: Optional[str] = None          # cross-checked against the token row
    mac: Optional[str] = None
    device_type: Optional[str] = None
    fw_version: Optional[str] = None
    app_version: Optional[str] = None


class RegisterResponse(BaseModel):
    device_id: str
    device_token: str                    # returned once, never retrievable again
    heartbeat_interval_sec: int
    tenant_id: str
    site_id: str
    door_id: str
    registered_at: str


class StatusRequest(BaseModel):
    status: str = Field(default="online", max_length=64)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StatusResponse(BaseModel):
    ok: bool = True
    server_time: str


# ------------------------------------------------------------------ readouts

class DeviceSummary(BaseModel):
    device_id: str
    name: Optional[str] = None
    tenant_id: Optional[str] = None
    site_id: Optional[str] = None
    door_id: Optional[str] = None
    mac: Optional[str] = None
    device_type: Optional[str] = None
    fw_version: Optional[str] = None
    app_version: Optional[str] = None
    ip_address: Optional[str] = None
    registered_at: str
    last_seen_at: Optional[str] = None
    status: Optional[str] = None
    online: bool
    last_seen_age_sec: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StatusHistoryEntry(BaseModel):
    ts: str
    status: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class DeviceEvent(BaseModel):
    ts: str                              # device-supplied event time
    received_at: str                     # server time the beat arrived
    type: str
    data: Dict[str, Any] = Field(default_factory=dict)


class DeviceDetail(DeviceSummary):
    history: List[StatusHistoryEntry] = Field(default_factory=list)


class PendingToken(BaseModel):
    token: str
    tenant_id: str
    site_id: str
    door_id: str
    issued_at: str
    expires_at: str
    expired: bool
