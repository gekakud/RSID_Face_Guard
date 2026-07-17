"""Standalone probe: verify rsid_py can talk to the RealSense ID F450 and report
what we need for integration (serial port, preview capture index, enrolled users).
Read-only — does not enroll, authenticate, or change device config.

Run:  python pyqt-app/f450_probe.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Ensure the bundled rsid.dll next to rsid_py*.pyd is found.
if hasattr(os, "add_dll_directory"):
    os.add_dll_directory(HERE)
sys.path.insert(0, HERE)

import rsid_py

print("rsid_py version:", rsid_py.__version__)

ports = rsid_py.discover_devices()
captures = rsid_py.discover_capture()
print("discover_devices (serial):", ports)
print("discover_capture (preview indices):", captures)

if not ports:
    print("No F450 serial device discovered. Is it connected / free?")
    sys.exit(1)

port = ports[0]
try:
    dev_type = rsid_py.discover_device_type(port)
    print("device type:", dev_type)
except Exception as e:
    print("discover_device_type failed:", e)

# FaceAuthenticator ctor may or may not take a device type across builds; try both.
try:
    auth = rsid_py.FaceAuthenticator()
except TypeError:
    auth = rsid_py.FaceAuthenticator(dev_type)

auth.connect(port)
print(f"connected to {port}")
try:
    n = auth.query_number_of_users()
    print("enrolled users:", n)
    print("user ids:", auth.query_user_ids())
    cfg = auth.query_device_config()
    print("device config:", cfg)
finally:
    auth.disconnect()
    print("disconnected")
