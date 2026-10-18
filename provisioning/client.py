"""HTTP calls this device makes to the dashboard server.

Two requests, both blocking -- callers are responsible for keeping them off the
Qt UI thread. See server/README.md for the contract.
"""

import json
from datetime import datetime, timezone
from typing import Optional

import requests

import config
from observability.logging_setup import get_logger
from provisioning import network
from provisioning.identity import DeviceIdentity, get_mac_address
from provisioning.hardware_identity import get_serial_number
from qr_scanner.payload import parse_payload

log = get_logger("provision")


class RegistrationError(Exception):
    """Registration was refused or unreachable. Message is shown on the kiosk."""

class DeviceRevokedError(Exception):
    """The server reported this device was removed (410). The caller must drop
    its identity and stop heartbeating."""


def _app_version() -> str:
    return getattr(config, "APP_VERSION", "face-guard")


def _fw_version() -> str:
    """Best-effort rsid_py version -- absent on a dev box without the SDK."""
    try:
        import rsid_py

        return getattr(rsid_py, "__version__", "unknown")
    except Exception:
        return "unknown"


def pair(payload: dict) -> DeviceIdentity:
    """Pair an installation without a signature or Authorization header.

    Render adds credentials for our existing database/status APIs. Other
    servers may omit those extensions; pairing then has no remote DB service.
    A timed-out POST is not automatically retried.
    """
    try:
        payload = parse_payload(json.dumps(payload))
        serial = get_serial_number()
    except ValueError as exc:
        raise RegistrationError(str(exc)) from exc
    server_url = payload["serverLink"].rstrip("/")
    mac = get_mac_address().upper()
    try:
        response = requests.post(
            f"{server_url}/installation/pair",
            json={"installationId": payload["installationId"], "mac": mac, "serial": serial},
            timeout=config.REMOTE_TIMEOUT_SEC,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise RegistrationError("Pairing server unreachable or timed out; scan again to recover") from exc
    if not 200 <= response.status_code < 300:
        try:
            detail = response.json().get("detail", "Pairing refused")
        except (ValueError, AttributeError):
            detail = "Pairing refused"
        raise RegistrationError(f"Pairing failed ({response.status_code}): {detail}")
    try:
        data = response.json()
        if not isinstance(data, dict) or data.get("status") != "paired":
            raise ValueError("Server did not confirm paired status")
        if data.get("_id") != payload["installationId"]:
            raise ValueError("Server returned a different installation ID")
        if not isinstance(data.get("device"), str) or not data["device"].strip():
            raise ValueError("Server returned no hardware device ID")
        token = data.get("device_token", "")
        if not isinstance(token, str):
            raise ValueError("Invalid device token in pair response")
        customer = data.get("customer") or {}
        gate = data.get("gate") or {}
        customer_id = customer.get("_id", "") if isinstance(customer, dict) else customer
        gate_id = gate.get("_id", "") if isinstance(gate, dict) else gate
        interval = int(data.get("heartbeat_interval_sec", config.HEARTBEAT_INTERVAL_SEC))
        if interval <= 0:
            raise ValueError("Invalid heartbeat interval")
        return DeviceIdentity(
            device_id=data["device"], device_token=token, server_url=server_url,
            customer_id=str(customer_id), site_id=str(data.get("venue") or ""),
            door_id=str(gate_id), network_profile={"mode": "local"},
            device_mode="face_only", identity_version=2,
            installation_id=data["_id"], serial=serial, mac=mac,
            device_type=str(data.get("deviceType") or payload["deviceType"]),
            registered_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            heartbeat_interval_sec=interval,
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise RegistrationError(f"Invalid pair response: {exc}") from exc


def register(payload: dict, device_type: Optional[str] = None) -> DeviceIdentity:
    """Legacy token registration, retained for old clients and compatibility tests.

    The production scanner and BindingManager now use installation pairing.
    """
    server_url = (payload.get("server_url") or "").rstrip("/")
    if not server_url:
        raise RegistrationError("QR contained no server_url")

    # If the QR carries a Wi-Fi profile (and APPLY_NETWORK_PROFILE is on), join
    # that network first -- a fresh device may have no other route to the
    # server. A "local" profile or the feature being off makes this a no-op.
    try:
        network.apply(payload.get("network_profile") or {})
    except network.NetworkApplyError as exc:
        raise RegistrationError(f"Could not join network: {exc}") from exc

    body = {
        "token": payload.get("provisioning_token"),
        "nonce": payload.get("nonce"),
        "mac": get_mac_address(),
        "device_type": str(device_type) if device_type is not None else None,
        "fw_version": _fw_version(),
        "app_version": _app_version(),
    }

    net_mode = (payload.get("network_profile") or {}).get("mode")
    log.info(
        "Registering with %s (door_id=%s, network=%s)",
        server_url, payload.get("door_id"), net_mode,
    )
    try:
        response = requests.post(
            f"{server_url}/devices/register",
            json=body,
            timeout=config.REMOTE_TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        raise RegistrationError(f"Server unreachable: {exc}") from exc

    if not response.ok:
        # Surface the server's reason (expired / already used) rather than a
        # bare status code -- it is the difference between "generate a new QR"
        # and "this device is already bound".
        detail = ""
        try:
            detail = response.json().get("detail", "")
        except ValueError:
            detail = response.text[:200]
        raise RegistrationError(f"Rejected ({response.status_code}): {detail}")

    data = response.json()
    identity = DeviceIdentity(
        device_id=data["device_id"],
        device_token=data["device_token"],
        server_url=server_url,
        customer_id=data.get("customer_id", ""),
        site_id=data.get("site_id", ""),
        door_id=data.get("door_id", ""),
        # The server echoes customer/site/door but not the network_profile, so
        # take that straight from the signed QR payload the device just verified.
        network_profile=payload.get("network_profile") or {},
        # Per-door mode (FR-MODE-01). Not in the signed QR: it arrives over the
        # registration response, authenticated by the one-time token.
        device_mode=data.get("device_mode", ""),
        registered_at=data.get("registered_at", ""),
        heartbeat_interval_sec=int(
            data.get("heartbeat_interval_sec", config.HEARTBEAT_INTERVAL_SEC)
        ),
    )
    log.info("Registered successfully: device_id=%s", identity.device_id)
    return identity


def post_status(identity: DeviceIdentity, status: str, metadata: dict) -> bool:
    """Send one heartbeat.

    Returns True on success, False on a transient failure (network/5xx) so the
    caller can back off and retry.

    Raises DeviceRevokedError on HTTP 410 -- the operator removed this device,
    and the caller must drop its identity rather than retry.
    """
    if not identity.device_token:
        return False
    try:
        response = requests.post(
            identity.status_url,
            headers={"Authorization": f"Bearer {identity.device_token}"},
            json={"status": status, "metadata": metadata},
            timeout=config.REMOTE_TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        log.warning("Heartbeat failed (network): %s", exc)
        return False

    if response.status_code == 410:
        # Tombstone response: this device was removed on the dashboard.
        log.warning("Server reports this device was removed (410)")
        raise DeviceRevokedError("Device was removed from the server")

    if not response.ok:
        log.warning("Heartbeat rejected: HTTP %s %s", response.status_code, response.text[:200])
        return False
    return True
