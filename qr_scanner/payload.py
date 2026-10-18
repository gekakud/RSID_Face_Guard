"""Unsigned installation QR format, shared by the kiosk and standalone PoC."""

import json
from urllib.parse import urlsplit


def parse_payload(text):
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("QR content must be a JSON object")
    for field in ("installationId", "deviceType", "serverLink"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
    url = urlsplit(payload["serverLink"])
    if (url.scheme not in ("http", "https") or not url.hostname
            or url.username or url.password or url.query or url.fragment):
        raise ValueError("serverLink must be an HTTP(S) server URL without credentials, query or fragment")
    # Accessing port also rejects malformed/non-numeric/out-of-range ports.
    url.port
    return payload
