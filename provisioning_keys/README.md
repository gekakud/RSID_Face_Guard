# Provisioning Keys

Ed25519 keys used to sign/verify the technician "init mode" provisioning QR codes.

- **Device needs:** `provisioning_keys/installer-signing-key-2026-01.pem` (public key only —
  this is what ships on every RPi). Loaded by `qr_scanner/qr_scanner.py` via
  `config.PROVISIONING_PUBLIC_KEYS_DIR`. File name must be `<key_id>.pem`,
  matching the QR payload's `signature.key_id`.

- **Server/issuer needs:** `other/qr_code_poc/issuer_private_key.pem` (private
  key only — this never leaves wherever you generate QR codes). Used by
  `other/qr_code_poc/gen_qr_code.py` to sign new QR payloads. Gitignored —
  never commit this file.

If you regenerate the issuer's keypair, re-copy the new
`other/qr_code_poc/issuer_public_key.pem` into this folder (renamed to
`<key_id>.pem`), or devices will reject all newly issued QR codes as
"invalid signature".

## Not encrypted, only signed

The QR payload is **plain, readable JSON** — any phone's QR scanner can
decode and display it in full (including `signature`). Ed25519 here
provides **authenticity + integrity, not confidentiality**:

- **Authenticity** — only whoever holds `issuer_private_key.pem` could have
  produced a signature that verifies against our public key. No one else
  can forge a "valid" payload.
- **Integrity** — if any field in the payload is edited after signing
  (e.g. `door_id`, `provisioning_token`), the signature no longer matches
  and verification fails.

Nothing in the payload is intended to be secret from whoever scans it.
