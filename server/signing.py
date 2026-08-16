"""Builds and signs provisioning QR payloads.

Deliberately thin: the Ed25519 signing and the canonical-JSON rule live in
other/qr_code_poc/qr_common.py and are imported, not reimplemented. The device
verifies with the same envelope logic (qr_scanner/qr_scanner.py) against the
public half of the same key, already shipped as
provisioning_keys/installer-signing-key-2026-01.pem. Sharing sign_payload()
makes canonical-JSON drift between the two sides impossible by construction.
"""

import base64
import io
import json
import logging
import uuid
from datetime import datetime
from typing import Optional, Tuple

import qrcode

from other.qr_code_poc.issuer_keys import KEY_ID, load_or_create_private_key
from other.qr_code_poc.qr_common import COMMAND, SCHEMA, sign_payload
from server import config, timeutil

log = logging.getLogger(__name__)

# Error correction level. The signed payload is ~600 characters, which lands at
# QR version 17 with L and version 19 with M. Lower version means fewer, larger
# modules -- easier for the device camera to resolve off a screen. Error
# correction buys little here (the code is displayed on a clean screen, not
# printed and scuffed) and the Ed25519 signature already covers integrity.
_ERROR_CORRECTION = qrcode.constants.ERROR_CORRECT_L

# See _decodes(): occasionally OpenCV cannot read a QR it just rendered, and the
# failure is deterministic for a given payload. Regenerating produces a new
# nonce and therefore different modules, which clears it -- measured at 200/200
# within four attempts.
_MAX_RENDER_ATTEMPTS = 6

# The issuer key is loaded lazily and cached -- reading + parsing the PEM on
# every request would be wasteful, and load_or_create_private_key() would
# otherwise race to generate the key if it were somehow missing.
_private_key = None


def _get_private_key():
    global _private_key
    if _private_key is None:
        _private_key = load_or_create_private_key()
    return _private_key


def build_payload(
    tenant_id: str,
    site_id: str,
    door_id: str,
    provisioning_token: str,
    validity_minutes: int,
    now: Optional[datetime] = None,
) -> dict:
    """Build and sign a provisioning payload.

    Field-for-field the same envelope as other/qr_code_poc/gen_qr_code.py, with
    server_url pointing at this deployment so the device learns where to
    register from the QR itself.
    """
    now = now or timeutil.utcnow()
    expires = timeutil.plus_minutes(validity_minutes, now)

    payload = {
        "schema": SCHEMA,
        "command": COMMAND,
        "server_url": config.PUBLIC_BASE_URL,
        "tenant_id": tenant_id,
        "site_id": site_id,
        "door_id": door_id,
        "provisioning_token": provisioning_token,
        "issued_at": timeutil.to_ts(now),
        "expires_at": timeutil.to_ts(expires),
        "nonce": str(uuid.uuid4()),
        "network_profile": {
            "mode": "ethernet_or_preconfigured_wifi",
            "wifi_profile_ref": None,
        },
    }
    return sign_payload(payload, _get_private_key(), KEY_ID)


def payload_to_qr_text(payload: dict) -> str:
    """The exact string encoded into the QR image (and read back by the device)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def render_qr_png(payload: dict) -> bytes:
    """Render the signed payload as a PNG."""
    qr = qrcode.QRCode(error_correction=_ERROR_CORRECTION, box_size=8, border=4)
    qr.add_data(payload_to_qr_text(payload))
    qr.make(fit=True)

    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def to_data_uri(png_bytes: bytes) -> str:
    """PNG as a data URI, ready to drop straight into an <img src>."""
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def render_qr_data_uri(payload: dict) -> str:
    return to_data_uri(render_qr_png(payload))


def _decodes(png_bytes: bytes) -> Optional[bool]:
    """Can the device's own decoder read this image?

    OpenCV's QRCodeDetector fails on a small fraction of large (version 17+)
    symbols, even from a pixel-perfect PNG, and the failure is deterministic for
    a given payload -- roughly 1 in 10 at ERROR_CORRECT_M, 1 in 20 at L. Since
    the device reads the code with this exact detector, an image that fails here
    is one the installer would hold up to the camera forever.

    Returns None if OpenCV isn't installed, meaning "cannot check" rather than
    "bad" -- the server must still work without it.
    """
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except ImportError:
        return None

    try:
        frame = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGB"))
        data, _, _ = cv2.QRCodeDetector().detectAndDecode(frame)
        return bool(data)
    except Exception as exc:
        log.warning("QR self-check failed to run: %s", exc)
        return None


def generate(
    tenant_id: str,
    site_id: str,
    door_id: str,
    provisioning_token: str,
    validity_minutes: int,
) -> Tuple[dict, str]:
    """Build a signed payload and a QR image the device can actually read.

    Returns (signed_payload, qr_data_uri). Each retry re-signs with a fresh
    nonce, which changes the module pattern; see _decodes() for why that is
    needed. The caller must persist the returned payload's nonce, not one from
    an earlier attempt.
    """
    payload = png = None

    for attempt in range(1, _MAX_RENDER_ATTEMPTS + 1):
        payload = build_payload(
            tenant_id, site_id, door_id, provisioning_token, validity_minutes
        )
        png = render_qr_png(payload)

        if _decodes(png) is not False:  # True, or None when unverifiable
            if attempt > 1:
                log.info("QR needed %d attempts to render decodably", attempt)
            return payload, to_data_uri(png)

    # Give the installer something rather than an error; the countdown and a
    # retry are cheaper than a 500 here.
    log.warning(
        "Could not produce a self-verifying QR in %d attempts; returning the last one",
        _MAX_RENDER_ATTEMPTS,
    )
    return payload, to_data_uri(png)
