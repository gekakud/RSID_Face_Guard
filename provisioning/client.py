"""HTTP calls this device makes to the dashboard server.

Two requests, both blocking -- callers are responsible for keeping them off the
Qt UI thread. See server/README.md for the contract.
"""

from typing import Optional

import requests

import config
from db.remote_provider import get_mac_address
from observability.logging_setup import get_logger
from provisioning import network
from provisioning.identity import DeviceIdentity

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


def register(payload: dict, device_type: Optional[str] = None) -> DeviceIdentity:
    """Redeem a scanned provisioning QR and return this device's credentials.

    `payload` is the verified QR payload straight out of QRScanner.scan(). The
    server URL comes from the payload rather than config: the QR is what tells
    a fresh device which deployment it belongs to.
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
