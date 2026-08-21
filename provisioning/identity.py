"""Persistent device identity -- what this device got back when it registered.

Written once at binding time and read on every boot. Holding the bearer token
means this file is a credential: config.DEVICE_IDENTITY_FILE is gitignored, and
it is written atomically (tmp + os.replace, same as db/local_provider.py) so a
power cut mid-write can't leave a half-written file that bricks the next boot.
"""

import json
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Optional

import config
from observability.logging_setup import get_logger

log = get_logger("provision")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def identity_path() -> str:
    """Absolute path to the identity file (config value may be relative)."""
    path = config.DEVICE_IDENTITY_FILE
    return path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)


@dataclass
class DeviceIdentity:
    device_id: str
    device_token: str
    server_url: str
    customer_id: str = ""
    site_id: str = ""
    door_id: str = ""
    # How this device was told to reach the server ("wifi" + ssid/password, or
    # "local"). Carried and stored for reference; applying it on the Pi is a
    # separate concern.
    network_profile: dict = field(default_factory=dict)
    registered_at: str = ""
    heartbeat_interval_sec: int = 30

    @property
    def status_url(self) -> str:
        return f"{self.server_url.rstrip('/')}/devices/{self.device_id}/status"


def load() -> Optional[DeviceIdentity]:
    """Return the saved identity, or None if this device isn't bound yet."""
    path = identity_path()
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        log.error("Failed reading device identity (%s): %s", path, exc)
        return None

    # Tolerate unknown keys so a newer server adding a field can't stop an
    # already-bound device from booting.
    known = {f.name for f in fields(DeviceIdentity)}
    filtered = {k: v for k, v in data.items() if k in known}

    try:
        return DeviceIdentity(**filtered)
    except TypeError as exc:
        log.error("Device identity at %s is malformed: %s", path, exc)
        return None


def save(identity: DeviceIdentity) -> bool:
    """Atomically persist the identity. Returns False on failure."""
    path = identity_path()
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(asdict(identity), handle, indent=2)
        os.replace(tmp_path, path)
        log.info(
            "Device identity saved: device_id=%s door_id=%s server_url=%s",
            identity.device_id, identity.door_id, identity.server_url,
        )
        return True
    except Exception as exc:
        log.error("Failed saving device identity (%s): %s", path, exc)
        return False


def clear() -> None:
    """Remove the saved identity (unbind). Used by tooling, not the normal flow."""
    try:
        os.remove(identity_path())
        log.info("Device identity cleared")
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.error("Failed clearing device identity: %s", exc)
