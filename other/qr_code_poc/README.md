# QR Provisioning POC

Local simulation of the technician "init mode" provisioning QR flow: an
**issuer** (server) signs a QR payload, and a **device** (RPi / this POC's
camera viewer) verifies it before trusting it. See
`provisioning_keys/README.md` for the general key-role explanation and the
signing-vs-encryption note.

- **Issuer side (this POC)** → `gen_qr_code.py` + `issuer_keys.py` + `qr_common.py`
- **Device side (production)** → `qr_scanner/qr_scanner.py` (loads keys from
  `config.PROVISIONING_PUBLIC_KEYS_DIR`, i.e. `provisioning_keys/`)
- **Device side (this POC, for quick local testing)** → `main.py` (opens a
  webcam window, shows `VALID`/`INVALID (reason)` per scanned QR)

## Setup

```bash
cd other/qr_code_poc
../../.venv/bin/pip install -r requirements.txt
```

## HOWTO: verify the flow locally

1. **Generate a signed QR** (issuer side). First run auto-generates the
   issuer's Ed25519 keypair (`issuer_private_key.pem`, `issuer_public_key.pem`)
   if missing:

   ```bash
   cd other/qr_code_poc
   ../../.venv/bin/python gen_qr_code.py
   ```

   This prints the full signed JSON payload and saves `device_000123.png`.

2. **Sync the public key to where the device/production scanner expects it**
   (only needed once, or whenever the issuer keypair is regenerated):

   ```bash
   cp issuer_public_key.pem ../../provisioning_keys/installer-signing-key-2026-01.pem
   ```

3. **Verify with a webcam (interactive, closest to real device behavior):**

   ```bash
   ../../.venv/bin/python main.py
   ```

   Click "Start Camera", hold `device_000123.png` up to the webcam (on a
   phone/monitor or printed) → the decoded panel shows `Status: VALID` (or
   `INVALID (<reason>)`, e.g. expired/tampered/unknown key).

4. **Verify headlessly (no webcam needed)** — quick way to sanity check a
   PNG against the production scanner logic directly:

   ```bash
   cd /home/geka/RSID_Face_Guard
   .venv/bin/python -c "
   from PIL import Image
   import numpy as np
   from qr_scanner import QRScanner
   img = np.array(Image.open('other/qr_code_poc/device_000123.png').convert('RGB'))
   result = QRScanner().scan(img)
   print(result)
   "
   ```

   Prints the verified payload dict, or `None` if detection/verification
   failed. Note: OpenCV's `cv2.QRCodeDetector` (used by `QRScanner.scan()`)
   can be finicky decoding a flat, high-contrast PNG directly off-screen —
   if you get `None` here but the webcam flow in step 3 works fine, that's
   just an OpenCV-detector quirk with that specific image, not a signature
   problem.

## Notes

- QR tokens are short-lived by design: `gen_qr_code.py`'s
  `generate_provisioning_qr(..., validity_minutes=10)` controls how long
  before `expires_at` rejects it. Increase this if you need a longer local
  testing window.
- Nonce replay protection is in-memory only (`QRScanner`/`main.py`'s
  `seen_nonces` set) — resets whenever the process restarts.
- The payload is **signed, not encrypted** — any generic QR scanner (e.g. a
  phone) can read its full contents; only `verify_payload()` /
  `QRScanner._verify()` add the authenticity/integrity check on top.