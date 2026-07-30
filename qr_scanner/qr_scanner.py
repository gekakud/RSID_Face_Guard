"""
QR code scanning for the technician/maintenance "init mode" flow.

Wraps OpenCV's QRCodeDetector to detect + decode a QR code in a single RGB
frame (as produced by hardware.camera_preview.PreviewController), and
verifies an HMAC signature on the embedded JSON payload.

Ported from qr_code_poc/ (a standalone proof-of-concept). The signing
scheme (qr_common.py there) is duplicated here for now; qr_code_poc/ will
be relocated/removed once this module is the single source of truth.
"""

import hashlib
import hmac
import json
import logging
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger("face_guard")

# NOTE: placeholder secret, mirrors qr_code_poc/qr_common.py. Replace with a
# securely provisioned key before this is used for anything but simulation.
SECRET_KEY = b"my-random-pass"


def _create_signature(data: dict) -> str:
    canonical_data = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(SECRET_KEY, canonical_data, hashlib.sha256).hexdigest()


def verify_signature(data: dict) -> bool:
    """Return True if data['signature'] matches an HMAC over the rest of data."""
    if "signature" not in data:
        return False
    received_signature = data["signature"]
    payload_without_signature = {k: v for k, v in data.items() if k != "signature"}
    expected_signature = _create_signature(payload_without_signature)
    return hmac.compare_digest(expected_signature, received_signature)


class QRScanner:
    """Stateless-ish helper: feed it frames, get back verified payloads."""

    def __init__(self):
        self._detector = cv2.QRCodeDetector()

    def scan(self, frame: np.ndarray) -> Optional[dict]:
        """Detect + decode a QR code in frame and verify its signature.

        Args:
            frame: RGB (or BGR -- detection doesn't care) HxWx3 uint8 array.

        Returns:
            The parsed JSON payload (dict, signature verified) if a valid,
            signed QR code was found in the frame, else None.
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

        if not verify_signature(payload):
            log.warning("QR code detected but signature verification failed")
            return None

        log.info("QR code detected and verified: %s", payload)
        return payload