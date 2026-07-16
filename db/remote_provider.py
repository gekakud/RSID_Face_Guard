"""
Remote (cloud) user data provider.

Fetches user/faceprint records from the backend server by device MAC
address, and converts them into the same {badge_id: user_data} shape
used by the local provider. Read-only: this provider never writes back
to the server.
"""

import uuid
from typing import Dict, Optional

import requests


def get_mac_address() -> str:
    """Return this device's MAC address, formatted as aa:bb:cc:dd:ee:ff."""
    mac = uuid.getnode()
    return ':'.join(f'{(mac >> ele) & 0xff:02x}' for ele in range(40, -8, -8))


def _embedding_to_faceprints(embedding) -> Optional[dict]:
    """Convert a raw server embedding list into RealSense faceprints structure."""
    if not isinstance(embedding, list):
        return None
    try:
        descriptor = [int(x) for x in embedding] + [2, 0, 0]
    except (ValueError, TypeError):
        return None

    return {
        "version": 9,
        "features_type": 0,
        "flags": 3,
        "adaptive_descriptor_nomask": descriptor,
        "adaptive_descriptor_withmask": [0] * 515,
        "enroll_descriptor": list(descriptor),
    }


class RemoteUserDataProvider:
    """Fetches the device's assigned users from the cloud server."""

    def __init__(self, server_url: str, timeout_sec: float = 10):
        self.server_url = server_url
        self.timeout_sec = timeout_sec

    def load_all(self) -> Dict[str, dict]:
        """Fetch users from the server. Returns {} on any failure."""
        payload = {"mac": get_mac_address()}
        print("🌍 Contacting server with MAC:", payload["mac"])

        try:
            response = requests.post(self.server_url, json=payload, timeout=self.timeout_sec)
        except Exception as e:
            print("❌ Network error:", e)
            return {}

        if response.status_code != 200:
            print("❌ Server returned:", response.status_code)
            print("❌ Body:", response.text[:500])
            return {}

        try:
            data = response.json()
        except Exception:
            print("❌ Invalid JSON from server")
            print("❌ Body:", response.text[:500])
            return {}

        remote_entries = self._extract_entries(data)
        if not remote_entries:
            return {}

        users: Dict[str, dict] = {}
        seen_ids = set()

        for entry in remote_entries:
            badge_raw = entry.get("badgeID")
            if not badge_raw:
                continue

            badge_id = str(badge_raw).strip()
            if badge_id in seen_ids:
                continue
            seen_ids.add(badge_id)

            embedding = entry.get("embedding")
            if not isinstance(embedding, list) or len(embedding) == 0:
                continue

            faceprints = _embedding_to_faceprints(embedding)
            if faceprints is None:
                print(f"⚠️ Skipping badgeID {badge_id}: bad embedding values")
                continue

            user_obj = entry.get("user", {}) or {}
            name = user_obj.get("name", "").strip()

            users[badge_id] = {
                "name": name,
                "permission_level": "User",
                "faceprints": faceprints,
            }

        print(f"✅ Remote fetch complete. {len(users)} users retrieved.")
        return users

    @staticmethod
    def _extract_entries(data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            entries = (
                data.get("ticketDeviceAccess") or
                (data.get("data") or {}).get("ticketDeviceAccess") or
                (data.get("result") or {}).get("ticketDeviceAccess")
            )
            if not entries:
                print("ℹ Could not find entries. Top-level keys:", list(data.keys())[:50])
            return entries
        print("ℹ Unexpected JSON type:", type(data))
        return None