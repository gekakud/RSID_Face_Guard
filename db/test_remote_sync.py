"""
Mock-server test for the remote DB sync flow (device_id/users endpoint,
bearer device_token auth). Spins up a local HTTP server returning a sample
payload in the agreed format, then exercises RemoteUserDataProvider.load_all()
and UserDatabase.sync_from_remote() end-to-end against a temp JSON file.

Run directly: .venv/bin/python -m db.test_remote_sync
"""

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from db.remote_provider import RemoteUserDataProvider
from db.user_database import UserDatabase
from provisioning.identity import DeviceIdentity

SAMPLE_FACEPRINTS = {
    "version": 9,
    "features_type": 0,
    "flags": 3,
    "adaptive_descriptor_nomask": [1, 2, 3],
    "adaptive_descriptor_withmask": [0, 0, 0],
    "enroll_descriptor": [1, 2, 3],
}

# Server response shape: dict keyed by badge_id, matching user_database.json.
SAMPLE_PAYLOAD = {
    "1001": {"name": "alice", "permission_level": "User", "faceprints": SAMPLE_FACEPRINTS},
    "1002": {"name": "bob", "permission_level": "Admin", "faceprints": SAMPLE_FACEPRINTS},
    # Malformed entry -- should be skipped, not crash the sync.
    "1003": {"name": "broken", "permission_level": "User", "faceprints": {"version": 9}},
}

EXPECTED_TOKEN = "test-device-token"

class _MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        if auth != f"Bearer {EXPECTED_TOKEN}":
            self.send_response(401)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(SAMPLE_PAYLOAD).encode())

    def log_message(self, *args):
        pass  # keep test output quiet

def _run_mock_server():
    server = HTTPServer(("127.0.0.1", 0), _MockHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port

def _make_identity(port: int) -> DeviceIdentity:
    return DeviceIdentity(
        device_id="test-device",
        device_token=EXPECTED_TOKEN,
        server_url=f"http://127.0.0.1:{port}",
    )

def test_remote_provider_load_all():
    server, port = _run_mock_server()
    try:
        provider = RemoteUserDataProvider(_make_identity(port))
        users = provider.load_all()
        assert set(users.keys()) == {"1001", "1002"}, f"unexpected keys: {users.keys()}"
        assert users["1001"]["name"] == "alice"
        assert users["1002"]["permission_level"] == "Admin"
    finally:
        server.shutdown()

def test_remote_provider_rejects_bad_token():
    server, port = _run_mock_server()
    try:
        bad_identity = DeviceIdentity(
            device_id="test-device", device_token="wrong-token",
            server_url=f"http://127.0.0.1:{port}",
        )
        users = RemoteUserDataProvider(bad_identity).load_all()
        assert users == {}
    finally:
        server.shutdown()

def test_remote_provider_no_identity():
    assert RemoteUserDataProvider(None).load_all() == {}

def test_user_database_sync_from_remote():
    server, port = _run_mock_server()
    tmp_dir = tempfile.mkdtemp()
    db_file = os.path.join(tmp_dir, "user_database.json")
    try:
        db = UserDatabase(db_file, identity=_make_identity(port))
        assert db.count() == 0  # nothing local yet

        updated = db.sync_from_remote()
        assert updated == 2
        assert db.count() == 2
        assert db.get_user("1001")["name"] == "alice"

        with open(db_file) as f:
            on_disk = json.load(f)
        assert set(on_disk.keys()) == {"1001", "1002"}
    finally:
        server.shutdown()

def test_full_replace_removes_revoked_user():
    """A badge present locally but absent from the next server response
    must be removed (server is the source of truth once remote is on)."""
    server, port = _run_mock_server()
    tmp_dir = tempfile.mkdtemp()
    db_file = os.path.join(tmp_dir, "user_database.json")
    try:
        db = UserDatabase(db_file, identity=_make_identity(port))
        db.set_user("9999", {"name": "stale", "permission_level": "User", "faceprints": SAMPLE_FACEPRINTS})
        assert db.get_user("9999") is not None

        db.sync_from_remote()
        assert db.get_user("9999") is None, "revoked/stale user should be removed after full-replace sync"
        assert db.count() == 2
    finally:
        server.shutdown()

if __name__ == "__main__":
    test_remote_provider_load_all()
    test_remote_provider_rejects_bad_token()
    test_remote_provider_no_identity()
    test_user_database_sync_from_remote()
    test_full_replace_removes_revoked_user()
    print("All remote sync tests passed.")