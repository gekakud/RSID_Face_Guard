"""
QR code scanning for the technician/maintenance "init mode" flow.

Wraps OpenCV's QRCodeDetector to detect + decode a QR code in a single RGB
frame (as produced by hardware.camera_preview.PreviewController).

Payload shape: the "Provisioning QR Envelope", e.g.:
{
  "schema": "acme.provisioning-qr.v1",
  "command": "provision_device",
  "server_url": "https://access.example.com",
  "tenant_id": "tenant_123",
  "site_id": "site_456",
  "door_id": "door_789",
  "provisioning_token": "opaque-one-time-token",
  "issued_at": "2026-07-27T15:00:00Z",
  "expires_at": "2026-07-27T15:10:00Z",
  "nonce": "b188...uuid",
  "network_profile": {
    "mode": "ethernet_or_preconfigured_wifi",
    "wifi_profile_ref": null
  },
  "signature": {
    "algorithm": "Ed25519",
    "key_id": "installer-signing-key-2026-01",
    "value": "base64url-signature"
  }
}

NOTE: signature verification is NOT implemented yet -- the "signature" field
is currently ignored. TODO: verify payload["signature"] (Ed25519, keyed by
"key_id") before treating a scanned payload as trusted, and reject expired
(expires_at) or replayed (nonce) tokens.
"""

import json
import logging
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger("face_guard")

EXPECTED_SCHEMA = "acme.provisioning-qr.v1"


class QRScanner:
    """Stateless-ish helper: feed it frames, get back decoded payloads."""

    def __init__(self):
        self._detector = cv2.QRCodeDetector()

    def scan(self, frame: np.ndarray) -> Optional[dict]:
        """Detect + decode a QR code in frame.

        Args:
            frame: RGB (or BGR -- detection doesn't care) HxWx3 uint8 array.

        Returns:
            The parsed JSON payload (dict) if a QR code with the expected
            provisioning schema was found in the frame, else None.

        NOTE: signature/expiry/nonce are not verified yet -- see module
        docstring TODO.
        """
        try:
            qr_data, points, _ = self._detector.detectAndDecode(frame)
        except Exception:
            log.exception("QR detection error")
            return None

        if not qr_data:
            return None

        try:
            payload = json.loads(qr_data)
        except (json.JSONDecodeError, TypeError):
            log.warning("QR code detected but content is not valid JSON: %r", qr_data)
            return None

        if not isinstance(payload, dict):
            return None

        if payload.get("schema") != EXPECTED_SCHEMA:
            log.warning("QR code detected but schema is not recognized: %r", payload.get("schema"))
            return None

        # TODO: verify payload["signature"] (Ed25519, keyed by "key_id"),
        # reject if payload["expires_at"] has passed, and reject replayed
        # payload["nonce"] values before trusting this payload.
        log.info("QR code detected: %s", payload)
        return payload