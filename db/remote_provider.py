"""
Remote (cloud) user data provider.

Fetches this device's assigned face users from the dashboard server at
GET {server_url}/devices/{device_id}/users, authenticated with the same
bearer device_token used for heartbeats (see provisioning/identity.py).
The response is already shaped exactly like the local JSON cache
(db/local_provider.py) -- {badge_id: {"name", "permission_level",
"faceprints"}} -- so callers can drop it straight into the local
UserDatabase. Read-only: this provider never writes back to the server.
"""

from typing import Dict, Optional

import requests

from observability import events
from observability.logging_setup import get_logger
from provisioning.identity import DeviceIdentity

log = get_logger("db")

# Faceprints keys we require to be present before trusting a record enough
# to write it into the local auth DB -- catches a malformed/partial record
# without crashing the whole sync.
_REQUIRED_FACEPRINTS_KEYS = ("version", "features_type", "flags", "adaptive_descriptor_nomask")


def _is_valid_faceprints(faceprints) -> bool:
    if not isinstance(faceprints, dict):
        return False
    return all(k in faceprints for k in _REQUIRED_FACEPRINTS_KEYS)


class RemoteUserDataProvider:
    """Fetches this device's assigned users from the dashboard server.

    Requires a bound device identity (device_id + device_token from
    provisioning/identity.py) -- an unbound device has no server to ask and
    load_all() simply returns {}.
    """

    def __init__(self, identity: Optional[DeviceIdentity], timeout_sec: float = 10):
        self.identity = identity
        self.timeout_sec = timeout_sec

    @property
    def users_url(self) -> Optional[str]:
        if self.identity is None:
            return None
        return f"{self.identity.server_url.rstrip('/')}/devices/{self.identity.device_id}/users"

    def load_all(self) -> Dict[str, dict]:
        """Fetch users from the server. Returns {} on any failure."""
        if self.identity is None:
            log.warning("No device identity bound yet -- skipping remote user sync")
            return {}

        try:
            response = requests.get(
                self.users_url,
                headers={"Authorization": f"Bearer {self.identity.device_token}"},
                timeout=self.timeout_sec,
            )
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

        users, skipped = self._parse_users(data)
        log.info("Remote fetch complete. %d users retrieved, %d skipped.", len(users), skipped)
        events.emit("db_sync_ok", users=len(users), skipped=skipped)
        if skipped:
            events.emit("db_sync_skipped_entries", count=skipped)
        return users

    @staticmethod
    def _parse_users(data):
        """Accept either {badge_id: user_data} directly, or a list of
        user_data dicts each carrying their own "badge_id" field.

        Returns (users, skipped_count).
        """
        users: Dict[str, dict] = {}
        skipped = [0]  # mutable counter, shared with _add_if_valid closures

        if isinstance(data, dict):
            # Could be {badge_id: user_data} or a wrapper like {"users": [...]}.
            candidates = data.get("users") if "users" in data else data
            if isinstance(candidates, list):
                entries = candidates
            elif isinstance(candidates, dict):
                for badge_id, user_data in candidates.items():
                    RemoteUserDataProvider._add_if_valid(users, skipped, str(badge_id), user_data)
                return users, skipped[0]
            else:
                log.error("Unexpected server payload shape: keys=%s", list(data.keys())[:20])
                events.emit("db_sync_failed", reason="unexpected_payload_shape")
                return users, skipped[0]
        elif isinstance(data, list):
            entries = data
        else:
            log.error("Unexpected server payload type: %s", type(data))
            events.emit("db_sync_failed", reason="unexpected_payload_type")
            return users, skipped[0]

        for entry in entries:
            if not isinstance(entry, dict):
                skipped[0] += 1
                continue
            badge_id = entry.get("badge_id") or entry.get("id")
            if not badge_id:
                log.warning("Skipping entry with no badge_id/id: %s", entry)
                skipped[0] += 1
                continue
            RemoteUserDataProvider._add_if_valid(users, skipped, str(badge_id), entry)

        return users, skipped[0]

    @staticmethod
    def _add_if_valid(users: Dict[str, dict], skipped: list, badge_id: str, user_data: dict):
        if not isinstance(user_data, dict):
            log.warning("Skipping badge_id %s: not an object", badge_id)
            skipped[0] += 1
            return
        faceprints = user_data.get("faceprints")
        if not _is_valid_faceprints(faceprints):
            log.warning("Skipping badge_id %s: missing/invalid faceprints", badge_id)
            events.emit("db_sync_invalid_record", badge_id=badge_id)
            skipped[0] += 1
            return
        users[badge_id] = {
            "name": user_data.get("name", ""),
            "permission_level": user_data.get("permission_level", "User"),
            "faceprints": faceprints,
        }