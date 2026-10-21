"""HTTP calls this device makes to the dashboard server.

Pairing and heartbeat, both blocking -- callers are responsible for keeping them off the
Qt UI thread. See server/README.md for the contract.
"""

import json
from datetime import datetime, timezone
from typing import Optional

import requests

import config
from observability.logging_setup import get_logger
from provisioning.identity import DeviceIdentity, get_mac_address
from provisioning.hardware_identity import get_serial_number
from provisioning import network
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
    # Join the QR's Wi-Fi BEFORE the POST below: a fresh Pi may have no other
    # route to the server, so doing this after would fail DNS every time.
    # A "local" profile (or APPLY_NETWORK_PROFILE off) is a no-op.
    network_profile = payload.get("networkProfile") or {"mode": "local"}
    try:
        network.apply(network_profile)
    except network.NetworkApplyError as exc:
        raise RegistrationError(f"Could not join network: {exc}") from exc
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
            door_id=str(gate_id),
            # Stored so the credentials survive a reboot (the server echoes
            # customer/site/door but not the network profile).
            network_profile=network_profile,
            # Per-door mode (FR-MODE-01) chosen when the installation QR was
            # created; empty means config.DEVICE_MODE stays in force.
            device_mode=str(data.get("device_mode") or ""), identity_version=2,
            installation_id=data["_id"], serial=serial, mac=mac,
            device_type=str(data.get("deviceType") or payload["deviceType"]),
            registered_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            heartbeat_interval_sec=interval,
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise RegistrationError(f"Invalid pair response: {exc}") from exc


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
