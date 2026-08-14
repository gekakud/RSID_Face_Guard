"""Generates (once) and loads a simulated Ed25519 issuer keypair for the POC.

In real life: the private key lives only on the provisioning/issuer server;
the public key (keyed by key_id) is distributed to every device ahead of
time. Here both files just live side by side in this folder for simplicity.
"""

import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

HERE = os.path.dirname(os.path.abspath(__file__))
PRIVATE_KEY_PATH = os.path.join(HERE, "issuer_private_key.pem")
PUBLIC_KEY_PATH = os.path.join(HERE, "issuer_public_key.pem")

KEY_ID = "installer-signing-key-2026-01"

def _generate_and_save():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    with open(PRIVATE_KEY_PATH, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    with open(PUBLIC_KEY_PATH, "wb") as f:
        f.write(public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
    return private_key, public_key

def load_or_create_private_key() -> Ed25519PrivateKey:
    """Issuer side: load the signing key (generate once if missing)."""
    if os.path.exists(PRIVATE_KEY_PATH):
        with open(PRIVATE_KEY_PATH, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    private_key, _ = _generate_and_save()
    return private_key

def load_or_create_public_key() -> Ed25519PublicKey:
    """Device side: load the verification key (generate the pair once if
    missing, so both sides agree from a fresh checkout)."""
    if not os.path.exists(PUBLIC_KEY_PATH):
        _generate_and_save()
    with open(PUBLIC_KEY_PATH, "rb") as f:
        return serialization.load_pem_public_key(f.read())