"""
Remote (cloud) user data provider.

Fetches user/faceprint records from the backend server by device MAC
address. The server is expected to return users already in the same
shape used by the local JSON cache (db/local_provider.py) --
{badge_id: {"name": ..., "permission_level": ..., "faceprints": {...}}}
-- either as a dict keyed by badge_id, or as a list of objects each
carrying their own "badge_id" field. Read-only: this provider never
writes back to the server.
"""

import uuid
from typing import Dict, Optional

import requests

from observability import events
from observability.logging_setup import get_logger

log = get_logger("db")

# Faceprints keys we require to be present before trusting a record enough
# to write it into the local auth DB -- catches a malformed/partial record
# without crashing the whole sync.
_REQUIRED_FACEPRINTS_KEYS = ("version", "features_type", "flags", "adaptive_descriptor_nomask")


def get_mac_address() -> str:
    """Return this device's MAC address, formatted as aa:bb:cc:dd:ee:ff."""
    mac = uuid.getnode()
    return ':'.join(f'{(mac >> ele) & 0xff:02x}' for ele in range(40, -8, -8))


def _is_valid_faceprints(faceprints) -> bool:
    if not isinstance(faceprints, dict):
        return False
    return all(k in faceprints for k in _REQUIRED_FACEPRINTS_KEYS)


class RemoteUserDataProvider:
    """Fetches the device's assigned users from the cloud server."""

    def __init__(self, server_url: str, timeout_sec: float = 10):
        self.server_url = server_url
        self.timeout_sec = timeout_sec

    def load_all(self) -> Dict[str, dict]:
        """Fetch users from the server. Returns {} on any failure."""
        payload = {"mac": get_mac_address()}
        log.info("Contacting server with MAC: %s", payload["mac"])

        try:
            response = requests.post(self.server_url, json=payload, timeout=self.timeout_sec)
        except Exception as e:
            log.error("Network error: %s", e)
            events.emit("db_sync_failed", reason="network_error", error=str(e))
            return {}

        if response.status_code != 200:
            log.error("Server returned: %s", response.status_code)
            log.error("Body: %s", response.text[:500])
            events.emit("db_sync_failed", reason="http_error", status=response.status_code)
            return {}

        try:
            data = response.json()
        except Exception:
            log.error("Invalid JSON from server")
            log.error("Body: %s", response.text[:500])
            events.emit("db_sync_failed", reason="invalid_json")
            return {}

        users = self._parse_users(data)
        log.info("Remote fetch complete. %d users retrieved.", len(users))
        events.emit("db_sync_ok", users=len(users))
        return users

    @staticmethod
    def _parse_users(data) -> Dict[str, dict]:
        """Accept either {badge_id: user_data} directly, or a list of
        user_data dicts each carrying their own "badge_id" field."""
        users: Dict[str, dict] = {}

        if isinstance(data, dict):
            # Could be {badge_id: user_data} or a wrapper like {"users": [...]}.
            candidates = data.get("users") if "users" in data else data
            if isinstance(candidates, list):
                entries = candidates
            elif isinstance(candidates, dict):
                for badge_id, user_data in candidates.items():
                    RemoteUserDataProvider._add_if_valid(users, str(badge_id), user_data)
                return users
            else:
                log.warning("Unexpected server payload shape: %s", type(data))
                return users
        elif isinstance(data, list):
            entries = data
        else:
            log.warning("Unexpected server payload type: %s", type(data))
            return users

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            badge_id = entry.get("badge_id") or entry.get("id")
            if not badge_id:
                continue
            RemoteUserDataProvider._add_if_valid(users, str(badge_id), entry)

        return users

    @staticmethod
    def _add_if_valid(users: Dict[str, dict], badge_id: str, user_data: dict):
        if not isinstance(user_data, dict):
            log.warning("Skipping badge_id %s: not an object", badge_id)
            return
        faceprints = user_data.get("faceprints")
        if not _is_valid_faceprints(faceprints):
            log.warning("Skipping badge_id %s: missing/invalid faceprints", badge_id)
            return
        users[badge_id] = {
            "name": user_data.get("name", ""),
            "permission_level": user_data.get("permission_level", "User"),
            "faceprints": faceprints,
        }