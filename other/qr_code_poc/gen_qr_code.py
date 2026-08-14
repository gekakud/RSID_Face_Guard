import json
import uuid
from datetime import datetime, timedelta, timezone

import qrcode

from qr_common import COMMAND, SCHEMA


def generate_provisioning_qr(
    server_url: str,
    tenant_id: str,
    site_id: str,
    door_id: str,
    provisioning_token: str,
    output_path: str,
    validity_minutes: int = 10,
) -> None:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=validity_minutes)

    payload = {
        "schema": SCHEMA,
        "command": COMMAND,
        "server_url": server_url,
        "tenant_id": tenant_id,
        "site_id": site_id,
        "door_id": door_id,
        "provisioning_token": provisioning_token,
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nonce": str(uuid.uuid4()),
        "network_profile": {
            "mode": "ethernet_or_preconfigured_wifi",
            "wifi_profile_ref": None,
        },
        # Placeholder only -- not verified yet by qr_scanner/qr_scanner.py.
        # TODO: replace with a real Ed25519 signature once verification is
        # implemented.
        "signature": {
            "algorithm": "Ed25519",
            "key_id": "installer-signing-key-2026-01",
            "value": "base64url-signature",
        },
    }

    qr_content = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )

    qr.add_data(qr_content)
    qr.make(fit=True)

    image = qr.make_image(
        fill_color="black",
        back_color="white",
    )
    image.save(output_path)


if __name__ == "__main__":
    generate_provisioning_qr(
        server_url="https://access.example.com",
        tenant_id="tenant_123",
        site_id="site_456",
        door_id="door_789",
        provisioning_token="opaque-one-time-token",
        output_path="device_000123.png",
    )