#!/bin/bash
#
# Remove a NetworkManager wifi connection profile so it can be re-provisioned
# from scratch (bind again via QR and watch network.apply() create/persist it
# fresh). Defaults to "faceguard-wifi", the fixed name network.apply() always
# uses (see provisioning/network.py CONNECTION_NAME).
#
# Usage:
#   sudo ./reset_wifi_profile.sh              # removes OnePlus 13
#   sudo ./reset_wifi_profile.sh Workport      # removes a specific profile

set -u

CON_NAME="${1:-OnePlus 13}"

if [[ $EUID -ne 0 ]]; then
    echo "Must run as root (sudo) -- nmcli connection management needs it." >&2
    exit 1
fi

if ! command -v nmcli >/dev/null 2>&1; then
    echo "nmcli not found -- this host is not managed by NetworkManager." >&2
    exit 1
fi

echo "Removing connection profile: $CON_NAME"

if nmcli -t -f NAME connection show | grep -Fxq "$CON_NAME"; then
    nmcli connection down "$CON_NAME" 2>/dev/null
    nmcli connection delete "$CON_NAME"
    echo "Deleted via nmcli."
else
    echo "No such connection known to nmcli (already gone, or never created)."
fi

# Belt-and-suspenders: nmcli delete should remove the keyfile too, but clean up
# any stray file directly in case something left it behind.
KEYFILE="/etc/NetworkManager/system-connections/${CON_NAME}.nmconnection"
if [[ -f "$KEYFILE" ]]; then
    rm -f "$KEYFILE"
    nmcli connection reload
    echo "Removed stray keyfile: $KEYFILE"
fi

echo
echo "Current connections:"
nmcli connection show
