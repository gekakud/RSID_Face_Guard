"""Simulated issuer: generates a signed provisioning QR code (POC).

In real life this logic runs on the provisioning/issuer server (holds the
private key only); here it just runs locally against issuer_keys.py's
generated key pair for demonstration.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import qrcode
from issuer_keys import KEY_ID, load_or_create_private_key
from qr_common import COMMAND, SCHEMA, sign_payload

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
    }

    private_key = load_or_create_private_key()
    payload = sign_payload(payload, private_key, KEY_ID)

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
    print(f"Signed provisioning QR saved to {output_path}")
    print(json.dumps(payload, indent=2))

if __name__ == "__main__":
    generate_provisioning_qr(
        server_url="https://access.example.com",
        tenant_id="tenant_123",
        site_id="site_456",
        door_id="door_789",
        provisioning_token="opaque-one-time-token",
        output_path="device_000123.png",
    )