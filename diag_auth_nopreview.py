#!/usr/bin/env python3
"""
Minimal diagnostic: run extract_faceprints_for_auth with NO preview running.
This tests whether auth works in isolation.
"""
import sys
import rsid_py

PORT = "/dev/ttyACM0"

print(f"rsid_py version: {rsid_py.__version__}")
print(f"Discovering device on {PORT}...")

try:
    device_type = rsid_py.discover_device_type(PORT)
    print(f"Device type: {device_type}")
except Exception as e:
    print(f"FAILED to discover device: {e}")
    sys.exit(1)

result = [None]

def on_fp_auth_result(status, new_prints):
    print(f"on_fp_auth_result: status={status}, has_prints={new_prints is not None}")
    result[0] = status

def on_hint(hint):
    print(f"on_hint: {hint}")

def on_faces(faces, timestamp):
    print(f"on_faces: {len(faces)} face(s) detected at t={timestamp}")

print("\nOpening FaceAuthenticator (context manager)...")
try:
    with rsid_py.FaceAuthenticator(PORT) as authenticator:
        print("Connected. Calling extract_faceprints_for_auth...")
        print(">>> STAND IN FRONT OF THE CAMERA NOW <<<")
        authenticator.extract_faceprints_for_auth(
            on_result=on_fp_auth_result,
            on_hint=on_hint,
            on_faces=on_faces,
        )
        print(f"\nFinal result: {result[0]}")
except Exception as e:
    print(f"Exception: {type(e).__name__}: {e}")

print("Done.")
