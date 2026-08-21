"""Stand-in for a real device: registers with a provisioning token, then heartbeats.

Lets the whole dashboard flow be exercised without a Raspberry Pi. The requests
it makes are exactly the ones the device will make once provisioning/client.py
exists, so this doubles as executable documentation of the contract.

Usage:
    # token straight from the dashboard / API response
    python -m server.tools.fake_device --token <provisioning_token>

    # or hand it the whole signed QR payload (it reads server_url from it)
    python -m server.tools.fake_device --payload-file payload.json

    # register only, don't heartbeat
    python -m server.tools.fake_device --token <tok> --once
"""

import argparse
import json
import random
import sys
import time
import uuid

import requests

DEFAULT_SERVER = "http://localhost:8000"
TIMEOUT = 10


def _mac() -> str:
    """Same derivation the device uses -- see db/remote_provider.get_mac_address()."""
    node = uuid.getnode()
    return ":".join(f"{(node >> shift) & 0xff:02x}" for shift in range(40, -8, -8))


def register(server_url: str, token: str, nonce=None) -> dict:
    response = requests.post(
        f"{server_url}/devices/register",
        json={
            "token": token,
            "nonce": nonce,
            "mac": _mac(),
            "device_type": "F455-fake",
            "fw_version": "6.1.0-fake",
            "app_version": "face-guard-poc",
        },
        timeout=TIMEOUT,
    )
    if not response.ok:
        raise SystemExit(f"register failed: HTTP {response.status_code} {response.text}")
    return response.json()


def post_status(server_url: str, creds: dict, started_at: float) -> None:
    response = requests.post(
        f"{server_url}/devices/{creds['device_id']}/status",
        headers={"Authorization": f"Bearer {creds['device_token']}"},
        json={
            "status": "online",
            "metadata": {
                "uptime_sec": int(time.time() - started_at),
                "app_version": "face-guard-poc",
                "rsid_py_version": "0.0.0-fake",
                "device_type": "F455-fake",
                "serial_port": "/dev/ttyACM0",
                "user_count": random.randint(40, 44),
                "camera_available": True,
                "relay_available": True,
                "session_active": random.random() < 0.2,
                "init_mode_active": False,
            },
        },
        timeout=TIMEOUT,
    )
    if not response.ok:
        print(f"  status failed: HTTP {response.status_code} {response.text}")
    else:
        print(f"  status ok  (server_time={response.json()['server_time']})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", help="provisioning token")
    parser.add_argument("--payload-file", help="file holding the signed QR payload JSON")
    parser.add_argument("--server", default=None, help=f"server base URL (default {DEFAULT_SERVER})")
    parser.add_argument("--interval", type=int, default=15, help="heartbeat period in seconds")
    parser.add_argument("--once", action="store_true", help="register, send one status, exit")
    args = parser.parse_args()

    token, nonce, server_url = args.token, None, args.server

    if args.payload_file:
        with open(args.payload_file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        token = token or payload.get("provisioning_token")
        nonce = payload.get("nonce")
        # The QR tells the device where to register -- prefer it over the flag.
        server_url = server_url or payload.get("server_url")

    if not token:
        parser.error("need --token or --payload-file")
    server_url = (server_url or DEFAULT_SERVER).rstrip("/")

    print(f"registering with {server_url} ...")
    creds = register(server_url, token, nonce)
    print(f"  device_id = {creds['device_id']}")
    print(f"  door      = {creds['door_id']} @ {creds['site_id']} ({creds['customer_id']})")

    interval = args.interval or creds.get("heartbeat_interval_sec", 30)
    started_at = time.time()

    post_status(server_url, creds, started_at)
    if args.once:
        return 0

    print(f"heartbeating every {interval}s -- Ctrl-C to stop")
    try:
        while True:
            time.sleep(interval)
            post_status(server_url, creds, started_at)
    except KeyboardInterrupt:
        print("\nstopped -- device should go offline on the dashboard shortly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
