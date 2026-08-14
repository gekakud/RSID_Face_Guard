"""
QR code scanning for the technician/maintenance "init mode" flow.

Wraps OpenCV's QRCodeDetector to detect + decode a QR code in a single RGB
frame (as produced by hardware.camera_preview.PreviewController), then
verifies the "Provisioning QR Envelope" signature/expiry/nonce before
trusting it.

Payload shape:
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

Trust model: the device only holds Ed25519 PUBLIC keys (never a private
key), loaded from config.PROVISIONING_PUBLIC_KEYS_DIR -- one PEM file per
trusted key_id, named "<key_id>.pem". Verification (signature, expiry,
nonce replay) happens entirely offline/locally; no network call is needed
to validate the QR's authenticity. See other/qr_code_poc/ for the issuer
(signing) side of this scheme, simulated for local testing.
"""

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization

import config

log = logging.getLogger("face_guard")

EXPECTED_SCHEMA = "acme.provisioning-qr.v1"

def _load_public_keys(directory: str) -> dict:
    """Load all "<key_id>.pem" files in directory into {key_id: public_key}."""
    keys = {}
    if not directory or not os.path.isdir(directory):
        log.warning("Provisioning public keys directory not found: %r", directory)
        return keys
    for filename in os.listdir(directory):
        if not filename.endswith(".pem"):
            continue
        key_id = filename[:-len(".pem")]
        path = os.path.join(directory, filename)
        try:
            with open(path, "rb") as f:
                keys[key_id] = serialization.load_pem_public_key(f.read())
        except Exception:
            log.exception("Failed loading provisioning public key: %s", path)
    return keys

class QRScanner:
    """Stateless-ish helper: feed it frames, get back verified payloads."""

    def __init__(self):
        self._detector = cv2.QRCodeDetector()
        self._public_keys = _load_public_keys(config.PROVISIONING_PUBLIC_KEYS_DIR)
        # Replay protection: nonces accepted this process lifetime. Resets on
        # restart -- acceptable since tokens are short-lived (expires_at).
        self._seen_nonces = set()

    def _canonical_payload_bytes(self, payload: dict) -> bytes:
        payload_without_signature = {k: v for k, v in payload.items() if k != "signature"}
        return json.dumps(
            payload_without_signature,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _verify(self, payload: dict) -> bool:
        if payload.get("schema") != EXPECTED_SCHEMA:
            log.warning("QR schema mismatch: %r", payload.get("schema"))
            return False

        sig = payload.get("signature")
        if not isinstance(sig, dict) or sig.get("algorithm") != "Ed25519":
            log.warning("QR missing/unsupported signature: %r", sig)
            return False

        key_id = sig.get("key_id")
        public_key = self._public_keys.get(key_id)
        if public_key is None:
            log.warning("QR signed by unknown key_id: %r", key_id)
            return False

        try:
            signature_bytes = base64.urlsafe_b64decode(sig.get("value", ""))
            public_key.verify(signature_bytes, self._canonical_payload_bytes(payload))
        except (InvalidSignature, Exception):
            log.warning("QR signature verification failed")
            return False

        expires_at = payload.get("expires_at")
        if expires_at:
            try:
                expires_dt = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                log.warning("QR malformed expires_at: %r", expires_at)
                return False
            if datetime.now(timezone.utc) > expires_dt:
                log.warning("QR token expired at %s", expires_at)
                return False

        nonce = payload.get("nonce")
        if nonce is not None:
            if nonce in self._seen_nonces:
                log.warning("QR nonce already used (replay): %r", nonce)
                return False
            self._seen_nonces.add(nonce)

        return True

    def scan(self, frame: np.ndarray) -> Optional[dict]:
        """Detect + decode a QR code in frame and verify it.

        Args:
            frame: RGB (or BGR -- detection doesn't care) HxWx3 uint8 array.

        Returns:
            The parsed JSON payload (dict) if a valid, signed, unexpired,
            non-replayed provisioning QR code was found in the frame, else
            None.
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

        if not self._verify(payload):
            return None

        log.info("QR code detected and verified: %s", payload)
        return payload