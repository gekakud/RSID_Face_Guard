"""Shared constants for the QR provisioning POC.

The envelope format matches qr_scanner/qr_scanner.py's expected schema
("acme.provisioning-qr.v1"). Signature verification is not implemented yet
on either side -- see qr_scanner/qr_scanner.py's module docstring TODO.
"""

SCHEMA = "acme.provisioning-qr.v1"
COMMAND = "provision_device"