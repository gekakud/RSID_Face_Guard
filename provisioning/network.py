"""Apply a QR's network_profile on the device -- join Wi-Fi so a fresh,
not-yet-networked Pi can reach the dashboard server.

Only does anything when:
  - config.APPLY_NETWORK_PROFILE is True (off by default so it can never
    reconfigure a developer's laptop), and
  - the profile's mode is "wifi".

A "local" profile (device already on a LAN cable) is always a no-op.

The profile is created with a system-owned passphrase (psk-flags=0), and the
passphrase itself is set via `nmcli connection edit` fed on stdin -- never on
the argv (FR-LOG-04), and never by writing NM's keyfile directly (that file is
root:root 0600, unreadable to the non-root kiosk service). NetworkManager
persists it, so it survives a reboot and can be re-supplied on a roam/re-auth
with no secret agent running -- exactly like a network a desktop user saved. A
profile whose secret did not persist is rejected at bind time rather than
dropping the link an hour later.

Networking on Raspberry Pi OS Bookworm is managed by NetworkManager, so this
drives `nmcli`. If nmcli isn't present (a non-NetworkManager host), it logs and
returns without touching anything. Joining Wi-Fi needs polkit authorisation --
a systemd service has no logind session, so the stock NM rule does not cover it;
install deploy/50-faceguard-nm.rules (see docs/SETUP_RPI5.md 6.2). Failures are
logged, not raised, so a network hiccup never crashes the kiosk (registration
will simply fail next and surface a clear message).
"""

import shutil
import subprocess
import time

import config
from observability.logging_setup import get_logger

log = get_logger("provision")

CONNECTION_NAME = "faceguard-wifi"

# Make the kiosk profile win over any leftover hand-made profile for the same
# SSID (a user-created one is priority 0).
AUTOCONNECT_PRIORITY = "100"

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


def _store_psk(con_name, password):
    """Set the passphrase via `nmcli connection edit`, fed on stdin.

    Deliberately not `nmcli connection modify wifi-sec.psk <pw>`: that puts the
    secret on the argv, where any user can read it out of the process list
    (FR-LOG-04). Equally deliberately not a direct write of NM's keyfile: that
    file is root:root 0600, so the kiosk service (User=geka) cannot open it --
    polkit authorises D-Bus actions to NetworkManager, never raw file access.

    Letting NM itself persist the secret keeps it off the argv AND makes it
    system-owned (psk-flags=0 was set at `connection add`), so NetworkManager
    can re-authenticate after a roam or a reboot with no secret agent running.
    """
    script = f"set 802-11-wireless-security.psk {password}\nsave\nquit\n"
    try:
        result = subprocess.run(
            ["nmcli", "connection", "edit", con_name],
            input=script,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.SubprocessError as exc:
        raise NetworkApplyError(f"nmcli connection edit failed: {exc}") from exc

    if result.returncode != 0:
        raise NetworkApplyError(
            f"could not store passphrase for {con_name!r}: {result.stderr.strip()}"
        )


def _psk_is_stored(con_name):
    """True if the profile has a non-empty, system-owned passphrase on disk.

    This is the check whose absence let a terminal bind successfully and then
    fail to re-authenticate an hour later: connectivity alone proves nothing
    about whether the secret was persisted.
    """
    result = _run(
        ["--show-secrets", "-t", "-f",
         "802-11-wireless-security.psk,802-11-wireless-security.psk-flags",
         "connection", "show", con_name],
        timeout=10,
    )
    if result.returncode != 0:
        return False

    psk = flags = None
    for line in result.stdout.splitlines():
        key, _, value = line.partition(":")
        if key.endswith(".psk"):
            psk = value
        elif key.endswith(".psk-flags"):
            flags = value.strip()
    return bool(psk) and flags == "0"

def _radio_is_enabled() -> bool:
    result = _run(["radio", "wifi"], timeout=5)
    return result.returncode == 0 and result.stdout.strip().lower() == "enabled"


def _enable_radio() -> None:
    """Clear a Wi-Fi soft block so wlan0 leaves the "unavailable" state.

    A soft-blocked radio (common on a Pi imaged without a WLAN country) makes
    `connection up` fail with "device not available", which reads like a broken
    profile rather than a switched-off radio. Done via nmcli, not rfkill:
    /dev/rfkill is not writable by the non-root kiosk user, whereas the
    enable-disable-wifi action is granted in deploy/50-faceguard-nm.rules.
    """
    if _radio_is_enabled():
        return
    log.warning("Wi-Fi radio is disabled (soft block) -- enabling it via nmcli")
    result = _run(["radio", "wifi", "on"], timeout=10)
    if result.returncode != 0:
        raise NetworkApplyError(
            f"Wi-Fi radio is disabled and could not be enabled: {result.stderr.strip()}"
        )
    # The driver needs a moment to bring wlan0 out of "unavailable".
    deadline = time.time() + 10
    while time.time() < deadline:
        if _radio_is_enabled():
            log.info("Wi-Fi radio enabled")
            return
        time.sleep(0.5)
    raise NetworkApplyError("Wi-Fi radio did not come up after enabling it")


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

    # A soft-blocked radio would make `connection up` below fail with the
    # misleading "device not available"; clear it first.
    _enable_radio()

    log.info("Applying Wi-Fi profile: joining SSID %r via nmcli", ssid)

    # Replace any previous faceguard-wifi connection so re-provisioning to a new
    # network is clean rather than accumulating stale profiles.
    _run(["connection", "delete", CONNECTION_NAME], timeout=10)

    # Created without the passphrase: it goes into the keyfile below, so it
    # never reaches the process list. psk-flags=0 marks the secret system-owned,
    # which is what lets NetworkManager re-auth after a roam or reboot with no
    # secret agent running -- the bug that dropped the link an hour after bind.
    add = _run([
        "connection", "add",
        "type", "wifi",
        "con-name", CONNECTION_NAME,
        "ssid", ssid,
        "wifi-sec.key-mgmt", "wpa-psk",
        "wifi-sec.psk-flags", "0",
        "connection.autoconnect", "yes",
        "connection.autoconnect-priority", AUTOCONNECT_PRIORITY,
    ], timeout=15)
    if add.returncode != 0:
        raise NetworkApplyError(f"nmcli connection add failed: {add.stderr.strip()}")

    _store_psk(CONNECTION_NAME, password)

    up = _run(["connection", "up", CONNECTION_NAME], timeout=config.NETWORK_APPLY_TIMEOUT_SEC)
    if up.returncode != 0:
        # Password wrong / out of range / AP down all land here.
        raise NetworkApplyError(f"nmcli connection up failed: {up.stderr.strip()}")

    # Fail at bind time, in front of the technician, rather than silently
    # leaving a profile that cannot re-authenticate later.
    if not _psk_is_stored(CONNECTION_NAME):
        raise NetworkApplyError(
            f"joined SSID {ssid!r} but the passphrase was not persisted "
            f"system-owned -- the link would drop on the next re-auth"
        )

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