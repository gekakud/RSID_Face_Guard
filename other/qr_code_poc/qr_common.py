"""Shared constants + Ed25519 sign/verify helpers for the QR provisioning POC.

The envelope format matches qr_scanner/qr_scanner.py's expected schema
("acme.provisioning-qr.v1"). Signing/verification here mirrors what a real
device's qr_scanner would do -- see qr_scanner/qr_scanner.py for the
production copy of this logic.
"""

import base64
import json
from datetime import datetime, timezone
from typing import Dict

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

SCHEMA = "acme.provisioning-qr.v1"
COMMAND = "provision_device"

def _canonical_payload_bytes(payload: dict) -> bytes:
    """Canonical JSON of the payload WITHOUT its "signature" field -- this is
    exactly what gets signed / what verification re-derives and checks."""
    payload_without_signature = {k: v for k, v in payload.items() if k != "signature"}
    return json.dumps(
        payload_without_signature,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

def sign_payload(payload: dict, private_key: Ed25519PrivateKey, key_id: str) -> dict:
    """Return payload with a populated "signature" field (Ed25519, base64url)."""
    signature_bytes = private_key.sign(_canonical_payload_bytes(payload))
    signed = dict(payload)
    signed["signature"] = {
        "algorithm": "Ed25519",
        "key_id": key_id,
        "value": base64.urlsafe_b64encode(signature_bytes).decode("ascii"),
    }
    return signed

class VerificationResult:
    def __init__(self, valid: bool, reason: str = ""):
        self.valid = valid
        self.reason = reason

    def __bool__(self):
        return self.valid

    def __repr__(self):
        return f"VerificationResult(valid={self.valid}, reason={self.reason!r})"

def verify_payload(
    payload: dict,
    public_keys: Dict[str, Ed25519PublicKey],
    seen_nonces: set = None,
) -> VerificationResult:
    """Verify schema, Ed25519 signature, expiry, and (optionally) nonce replay.

    Args:
        payload: decoded QR JSON dict.
        public_keys: {key_id: Ed25519PublicKey} of trusted issuer keys.
        seen_nonces: optional mutable set used to reject replayed nonces --
            the nonce is added to the set only after every other check passes.
    """
    if payload.get("schema") != SCHEMA:
        return VerificationResult(False, f"unexpected schema: {payload.get('schema')!r}")

    sig = payload.get("signature")
    if not isinstance(sig, dict):
        return VerificationResult(False, "missing signature")

    if sig.get("algorithm") != "Ed25519":
        return VerificationResult(False, f"unsupported algorithm: {sig.get('algorithm')!r}")

    key_id = sig.get("key_id")
    public_key = public_keys.get(key_id)
    if public_key is None:
        return VerificationResult(False, f"unknown key_id: {key_id!r}")

    try:
        signature_bytes = base64.urlsafe_b64decode(sig.get("value", ""))
    except Exception:
        return VerificationResult(False, "malformed signature value")

    try:
        public_key.verify(signature_bytes, _canonical_payload_bytes(payload))
    except InvalidSignature:
        return VerificationResult(False, "invalid signature")

    expires_at = payload.get("expires_at")
    if expires_at:
        try:
            expires_dt = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return VerificationResult(False, f"malformed expires_at: {expires_at!r}")
        if datetime.now(timezone.utc) > expires_dt:
            return VerificationResult(False, "token expired")

    nonce = payload.get("nonce")
    if seen_nonces is not None and nonce is not None:
        if nonce in seen_nonces:
            return VerificationResult(False, "nonce already used (replay)")
        seen_nonces.add(nonce)

    return VerificationResult(True)