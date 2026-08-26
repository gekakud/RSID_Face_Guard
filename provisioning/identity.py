"""Persistent device identity -- what this device got back when it registered.

Written once at binding time and read on every boot. Holding the bearer token
means this file is a credential: config.DEVICE_IDENTITY_FILE is gitignored, and
it is written atomically (tmp + os.replace, same as db/local_provider.py) so a
power cut mid-write can't leave a half-written file that bricks the next boot.
"""

import json
import os
import uuid
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


def get_mac_address() -> str:
    """Return this device's MAC address, formatted as aa:bb:cc:dd:ee:ff.

    Only used at registration time (identifying info sent to the server
    alongside the provisioning token) -- day-to-day requests use the
    device_id/device_token issued at registration instead.
    """
    mac = uuid.getnode()
    return ':'.join(f'{(mac >> ele) & 0xff:02x}' for ele in range(40, -8, -8))


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

    @property
    def users_url(self) -> str:
        return f"{self.server_url.rstrip('/')}/devices/{self.device_id}/users"


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
    """Atomically persist the identity. Returns False on failure.

    The file holds the bearer token, so it is created owner-read/write only
    (0600) *before* any content is written, and the mode is re-asserted on the
    final path in case an earlier version left looser permissions (FR-PROV-09,
    FR-DATA-03).
    """
    path = identity_path()
    tmp_path = path + ".tmp"
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(identity), handle, indent=2)
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
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