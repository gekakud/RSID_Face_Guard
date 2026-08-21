"""Apply a QR's network_profile on the device -- join Wi-Fi so a fresh,
not-yet-networked Pi can reach the dashboard server.

Only does anything when:
  - config.APPLY_NETWORK_PROFILE is True (off by default so it can never
    reconfigure a developer's laptop), and
  - the profile's mode is "wifi".

A "local" profile (device already on a LAN cable) is always a no-op.

Networking on Raspberry Pi OS Bookworm is managed by NetworkManager, so this
drives `nmcli`. If nmcli isn't present (a non-NetworkManager host), it logs and
returns without touching anything. Joining Wi-Fi typically needs root/polkit --
see server/README.md for the polkit rule; failures are logged, not raised, so a
network hiccup never crashes the kiosk (registration will simply fail next and
surface a clear message).
"""

import shutil
import subprocess
import time

import config
from observability.logging_setup import get_logger

log = get_logger("provision")

CONNECTION_NAME = "faceguard-wifi"

class NetworkApplyError(Exception):
    """Applying the network profile failed (Wi-Fi could not be joined)."""

def _have_nmcli() -> bool:
    return shutil.which("nmcli") is not None

def _run(args, timeout=None):
    """Run nmcli, returning CompletedProcess; never raises on non-zero exit."""
    return subprocess.run(
        ["nmcli", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )

def _is_connected() -> bool:
    """True if NetworkManager reports full connectivity."""
    try:
        result = _run(["-t", "-f", "STATE", "general", "status"], timeout=5)
        return result.returncode == 0 and "connected" in result.stdout.strip().lower()
    except Exception:
        return False

def apply(profile: dict) -> bool:
    """Apply a network_profile. Returns True if the device is (now) online.

    - No-op returning True for "local" mode or when the feature is disabled.
    - For "wifi": (re)creates a NetworkManager connection for the SSID and
      brings it up, then waits up to config.NETWORK_APPLY_TIMEOUT_SEC for
      connectivity.

    Raises NetworkApplyError only when Wi-Fi apply is attempted and fails, so
    the caller can decide whether to proceed (registration will need the link).
    """
    profile = profile or {}
    mode = profile.get("mode", "local")

    if mode != "wifi":
        log.info("Network profile mode=%s -- nothing to apply", mode)
        return True

    if not config.APPLY_NETWORK_PROFILE:
        log.info(
            "Wi-Fi profile present but APPLY_NETWORK_PROFILE is off -- "
            "not touching host networking (set it True on the Pi to enable)"
        )
        return True

    wifi = profile.get("wifi") or {}
    ssid = wifi.get("ssid")
    password = wifi.get("password")
    if not ssid or not password:
        raise NetworkApplyError("Wi-Fi profile missing ssid/password")

    if not _have_nmcli():
        raise NetworkApplyError(
            "nmcli not found -- host is not managed by NetworkManager"
        )

    log.info("Applying Wi-Fi profile: joining SSID %r via nmcli", ssid)

    # Replace any previous faceguard-wifi connection so re-provisioning to a new
    # network is clean rather than accumulating stale profiles.
    _run(["connection", "delete", CONNECTION_NAME], timeout=10)

    add = _run([
        "connection", "add",
        "type", "wifi",
        "con-name", CONNECTION_NAME,
        "ssid", ssid,
        "wifi-sec.key-mgmt", "wpa-psk",
        "wifi-sec.psk", password,
    ], timeout=15)
    if add.returncode != 0:
        raise NetworkApplyError(f"nmcli connection add failed: {add.stderr.strip()}")

    up = _run(["connection", "up", CONNECTION_NAME], timeout=config.NETWORK_APPLY_TIMEOUT_SEC)
    if up.returncode != 0:
        # Password wrong / out of range / AP down all land here.
        raise NetworkApplyError(f"nmcli connection up failed: {up.stderr.strip()}")

    # `connection up` usually blocks until associated, but wait for full
    # connectivity (DHCP + a default route) before declaring success.
    deadline = time.time() + config.NETWORK_APPLY_TIMEOUT_SEC
    while time.time() < deadline:
        if _is_connected():
            log.info("Wi-Fi connected: SSID %r", ssid)
            return True
        time.sleep(1)

    raise NetworkApplyError(
        f"joined SSID {ssid!r} but no internet connectivity within "
        f"{config.NETWORK_APPLY_TIMEOUT_SEC}s"
    )