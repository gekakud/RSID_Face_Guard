"""Device binding: redeem a provisioning QR, persist credentials, heartbeat.

    identity.py   load/save the credentials this device got at registration
    client.py     the two HTTP calls (register, post_status)
    heartbeat.py  background thread that keeps the dashboard up to date
    binding.py    the flow both GUIs call when a QR is scanned

See server/README.md for the server side of the contract.
"""

from provisioning.identity import DeviceIdentity, load, save

__all__ = ["DeviceIdentity", "load", "save"]
