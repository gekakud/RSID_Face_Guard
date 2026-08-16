# Package marker. server/signing.py imports qr_common + issuer_keys from here
# so the server signs with exactly the same code (and key) the device verifies
# against -- see server/signing.py for why that matters.
