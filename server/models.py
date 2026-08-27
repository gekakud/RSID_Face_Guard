"""Request/response models.

These double as the API contract documented in server/README.md -- the device
side will send exactly these shapes.
"""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from server import config


# ------------------------------------------------------- network profile

class WifiCredentials(BaseModel):
    ssid: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class WifiNetworkProfile(BaseModel):
    """The device should join this Wi-Fi network to reach the server."""
    mode: Literal["wifi"] = "wifi"
    wifi: WifiCredentials


class LocalNetworkProfile(BaseModel):
    """The device is on a LAN cable and already has internet; nothing to apply."""
    mode: Literal["local"] = "local"


# Discriminated on "mode": wifi requires ssid+password, local requires nothing.
NetworkProfile = Union[WifiNetworkProfile, LocalNetworkProfile]


# --------------------------------------------------- customer/site/door CRUD

class CreateCustomerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class CreateSiteRequest(BaseModel):
    customer_id: int
    name: str = Field(min_length=1, max_length=64)


class CreateDoorRequest(BaseModel):
    site_id: int
    name: str = Field(min_length=1, max_length=64)


class Customer(BaseModel):
    id: int
    name: str


class Site(BaseModel):
    id: int
    customer_id: int
    name: str


class Door(BaseModel):
    id: int
    site_id: int
    name: str


# ---------------------------------------------------------------- dashboard ->

class GenerateQRRequest(BaseModel):
    # The human-readable names (as selected in the dashboard) are what get
    # signed into the QR and stored on the device -- see server/README.md.
    customer_id: str = Field(min_length=1, max_length=64)
    site_id: str = Field(min_length=1, max_length=64)
    door_id: str = Field(min_length=1, max_length=64)
    network_profile: NetworkProfile = Field(
        default_factory=LocalNetworkProfile,
        discriminator="mode",
    )
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
    customer_id: str
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
    customer_id: Optional[str] = None
    site_id: Optional[str] = None
    door_id: Optional[str] = None
    network_profile: Dict[str, Any] = Field(default_factory=dict)
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
    # Admin lifecycle: "active" | "suspended" | "revoked_ack". See server/db.py.
    state: str = "active"
    suspended_at: Optional[str] = None


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


class DeleteDeviceResponse(BaseModel):
    ok: bool = True
    device_id: str
    state: str          # "suspended" (awaiting the device to acknowledge)


class ClearEventsResponse(BaseModel):
    ok: bool = True
    device_id: str
    deleted: int        # number of event rows removed


class PendingToken(BaseModel):
    token: str
    customer_id: str
    site_id: str
    door_id: str
    issued_at: str
    expires_at: str
    expired: bool
