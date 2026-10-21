"""Request/response models.

These double as the API contract documented in server/README.md -- the device
side will send exactly these shapes.
"""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------- device mode

# The per-door operating mode handed to the device at registration (FR-MODE-01).
DeviceMode = Literal["card_only", "card_and_face", "face_only", "time_registry"]

DEFAULT_DEVICE_MODE = "card_and_face"


# ------------------------------------------------------- network profile

class WifiCredentials(BaseModel):
    ssid: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class WifiNetworkProfile(BaseModel):
    """The device should join this Wi-Fi network to reach the server."""
    mode: Literal["wifi"] = "wifi"
    wifi: WifiCredentials


class LocalNetworkProfile(BaseModel):
    """Device is already on the LAN (cable) -- nothing to apply."""
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

class StatusRequest(BaseModel):
    status: str = Field(default="online", max_length=64)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StatusResponse(BaseModel):
    ok: bool = True
    server_time: str


# ------------------------------------------------------------------ readouts

class DeviceSummary(BaseModel):
    device_id: str
    serial: Optional[str] = None
    name: Optional[str] = None
    customer_id: Optional[str] = None
    site_id: Optional[str] = None
    door_id: Optional[str] = None
    device_mode: Optional[str] = None
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
