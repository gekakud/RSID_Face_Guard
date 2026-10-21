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
    # Validate the Wi-Fi block only when the QR carries one. Absent stays
    # absent (callers default to "local"), so local QRs parse unchanged.
    if payload.get("networkProfile") is not None:
        payload["networkProfile"] = _parse_network_profile(payload["networkProfile"])
    return payload


def _parse_network_profile(profile):
    """Validate the optional Wi-Fi block. Empty means "local" (LAN cable)."""
    if profile == {}:
        return {"mode": "local"}
    if not isinstance(profile, dict):
        raise ValueError("networkProfile must be a JSON object")
    mode = profile.get("mode", "local")
    if mode not in ("wifi", "local"):
        raise ValueError("networkProfile.mode must be 'wifi' or 'local'")
    if mode == "local":
        return {"mode": "local"}
    wifi = profile.get("wifi") or {}
    if not isinstance(wifi, dict):
        raise ValueError("networkProfile.wifi must be a JSON object")
    ssid, password = wifi.get("ssid"), wifi.get("password")
    for name, value in (("ssid", ssid), ("password", password)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"networkProfile.wifi.{name} must be a non-empty string")
    return {"mode": "wifi", "wifi": {"ssid": ssid, "password": password}}
