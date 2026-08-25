"""Per-device face-user store, backed by a single JSON file.

Simple JSON file for now (server-side user management is TBD); shaped as
{device_id: {badge_id: {name, permission_level, faceprints}}}, so each
device's slice is already in exactly the format the device's local
UserDatabase expects (see db/local_provider.py, db/remote_provider.py).
"""

import json
import logging
import os
import threading

from server import config

log = logging.getLogger("server.user_store")

_lock = threading.Lock()


def _store_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), config.USER_STORE_FILE)


def _load_all() -> dict:
    path = _store_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        log.error("Failed loading %s: %s", path, e)
        return {}


def _save_all(data: dict) -> None:
    path = _store_path()
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)

def get_for_device(device_id: str) -> dict:
    """Return {badge_id: user_data} for one device (empty dict if none)."""
    with _lock:
        data = _load_all()
    return data.get(device_id, {})

def set_for_device(device_id: str, users: dict) -> None:
    """Replace one device's whole user slice (dashboard "assign JSON" upload)."""
    with _lock:
        data = _load_all()
        data[device_id] = users
        _save_all(data)

def load_default_template() -> dict:
    """The flat {badge_id: user_data} every newly-registered device is seeded
    with (server/default_user_database.json). Missing/invalid -> {}."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.DEFAULT_USER_DB_FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log.error("Failed loading default template %s: %s", path, e)
        return {}
