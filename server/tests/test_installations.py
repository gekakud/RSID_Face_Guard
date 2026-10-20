"""New QR → pair → persistent identity → existing users/status integration."""

import base64
import io
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from PIL import Image

from provisioning import client as device_client, identity as identity_store
from provisioning.binding import BindingManager
from qr_scanner import QRScanner
from server import db, user_store


def create(client, door="front"):
    response = client.post("/installation", json={"customer_id": "acme", "site_id": "hq", "door_id": door})
    assert response.status_code == 201
    return response.json()


def pair(client, installation, serial="000000001234abcd", mac="AA:BB:CC:DD:EE:FF"):
    return client.post("/installation/pair", json={"installationId": installation["_id"], "mac": mac, "serial": serial})


def test_qr_pair_database_status_and_revoke(client):
    installation = create(client)
    assert installation["status"] == "pending"
    png = base64.b64decode(installation["qr_png"].split(",")[1])
    frame = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
    payload = QRScanner().scan(frame)
    assert payload == {"installationId": installation["_id"], "deviceType": "Genie Door Camera Lite", "serverLink": "http://testserver"}
    response = pair(client, installation)
    assert response.status_code == 200
    result = response.json()
    assert result["_id"] == installation["_id"]
    assert result["device"] != result["_id"]
    assert result["device_mode"] == "card_and_face"   # server default
    headers = {"Authorization": "Bearer " + result["device_token"]}
    url = f"/devices/{result['device']}"
    assert client.get(url + "/users", headers=headers).json() == user_store.load_default_template()
    assert client.post(url + "/status", headers=headers, json={"status": "online"}).status_code == 200
    assert client.get(url).json()["serial"] == "000000001234abcd"
    client.delete(url)
    assert client.post(url + "/status", headers=headers, json={"status": "online"}).status_code == 410
    assert pair(client, installation).status_code == 409


def test_replay_rotates_token_preserves_users_and_rebind_reuses_hardware(client):
    first = create(client)
    original = pair(client, first).json()
    device_id = original["device"]
    user_store.set_for_device(device_id, {"custom": {"name": "kept"}})
    replay = pair(client, first).json()
    assert replay["device"] == device_id
    assert replay["device_token"] != original["device_token"]
    assert user_store.get_for_device(device_id) == {"custom": {"name": "kept"}}
    assert client.get(f"/devices/{device_id}/users", headers={"Authorization": "Bearer " + original["device_token"]}).status_code == 401
    second = create(client, "back")
    rebound = pair(client, second).json()
    assert rebound["device"] == device_id
    assert rebound["gate"]["_id"] == "back"
    assert len(client.get("/devices").json()) == 1
    assert client.get(f"/installation/{first['_id']}").json()["status"] == "replaced"
    assert pair(client, first).status_code == 409


def test_conflicts_validation_and_missing_installation(client):
    installation = create(client)
    assert pair(client, {"_id": "missing"}).status_code == 404
    assert pair(client, installation, serial="0000000000000000").status_code == 422
    assert pair(client, installation, mac="bad").status_code == 422
    assert pair(client, installation).status_code == 200
    assert pair(client, installation, serial="00000000ffffffff").status_code == 409
    assert pair(client, installation, mac="11:22:33:44:55:66").status_code == 409


def test_concurrent_claims_create_only_one_device(client):
    installation = create(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda serial: pair(client, installation, serial=serial),
                                  ["0000000011111111", "0000000022222222"]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(client.get("/devices").json()) == 1


def test_migration_keeps_existing_device_and_is_repeatable(client):
    conn = db.connect()
    try:
        conn.execute("INSERT INTO devices(device_id, token_hash, registered_at) VALUES ('legacy', 'hash', '2026-01-01T00:00:00Z')")
        conn.execute("DROP INDEX idx_devices_serial")
        conn.execute("ALTER TABLE devices DROP COLUMN serial")
        conn.commit()
    finally:
        conn.close()
    db.init_db()
    db.init_db()
    assert client.get("/devices/legacy").json()["device_id"] == "legacy"
    assert pair(client, create(client)).status_code == 200


def test_device_client_pairs_and_round_trips_identity(client, monkeypatch, tmp_path):
    installation = create(client)
    monkeypatch.setenv("DEVICE_SERIAL_OVERRIDE", "dev-win-test")
    monkeypatch.setattr(identity_store.config, "DEVICE_IDENTITY_FILE", str(tmp_path / "identity.json"))
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return client.post("/installation/pair", json=kwargs["json"])
    monkeypatch.setattr(device_client.requests, "post", post)
    identity = device_client.pair(installation["payload"])
    assert calls[0][0] == "http://testserver/installation/pair"
    assert set(calls[0][1]["json"]) == {"installationId", "mac", "serial"}
    assert "headers" not in calls[0][1]
    assert identity.device_mode == "card_and_face"
    assert identity.serial == "dev-win-test"
    assert identity_store.save(identity)
    assert identity_store.load() == identity


@pytest.mark.parametrize("result", [[], {"status": "pending"}, {"status": "paired", "_id": "wrong", "device": "id"}])
def test_device_rejects_incomplete_or_wrong_pair_result(client, monkeypatch, result):
    from unittest.mock import Mock
    monkeypatch.setenv("DEVICE_SERIAL_OVERRIDE", "dev-win-test")
    monkeypatch.setattr(device_client.requests, "post", lambda *a, **k: Mock(status_code=200, json=lambda: result))
    with pytest.raises(device_client.RegistrationError):
        device_client.pair(create(client)["payload"])


def test_failed_save_preserves_old_identity(client, monkeypatch):
    from provisioning.identity import DeviceIdentity
    old = DeviceIdentity("old", "old-token", "http://old")
    new = DeviceIdentity("new", "new-token", "http://new")
    manager = BindingManager()
    manager.identity = old
    monkeypatch.setattr(device_client, "pair", lambda payload: new)
    monkeypatch.setattr(identity_store, "save", lambda identity: False)
    monkeypatch.setattr(manager, "_start_heartbeat", lambda: None)
    results = []
    manager._bind({}, lambda ok, message: results.append(ok))
    assert results == [False]
    assert manager.identity == old


def test_creation_is_admin_protected_but_pairing_is_public(client, monkeypatch):
    from server import config
    monkeypatch.setattr(config, "ADMIN_USER", "admin")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "test-password")
    body = {"customer_id": "acme", "site_id": "hq", "door_id": "gate"}
    assert client.post("/installation", json=body).status_code == 401
    response = client.post("/installation", json=body, auth=("admin", "test-password"))
    assert response.status_code == 201
    assert pair(client, response.json()).status_code == 200
    assert client.get("/installation").status_code == 401


@pytest.mark.parametrize("status", [400, 409, 503, 302])
def test_device_pair_http_failures_do_not_persist(client, monkeypatch, tmp_path, status):
    from unittest.mock import Mock
    monkeypatch.setenv("DEVICE_SERIAL_OVERRIDE", "dev-win-test")
    monkeypatch.setattr(identity_store.config, "DEVICE_IDENTITY_FILE", str(tmp_path / "identity.json"))
    monkeypatch.setattr(device_client.requests, "post", lambda *a, **k: Mock(status_code=status, json=lambda: {"detail": "Test failure"}))
    manager = BindingManager()
    results = []
    manager._bind(create(client)["payload"], lambda ok, message: results.append(ok))
    assert results == [False]
    assert identity_store.load() is None


def test_duplicate_binding_requests_are_suppressed(monkeypatch):
    import threading
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def blocked_pair(payload):
        calls.append(payload)
        entered.set()
        assert release.wait(5)
        raise device_client.RegistrationError("test completion")
    monkeypatch.setattr(device_client, "pair", blocked_pair)
    manager = BindingManager()
    try:
        manager.bind_async({"first": True}, lambda *args: done.set())
        assert entered.wait(5)
        manager.bind_async({"second": True}, lambda *args: done.set())
    finally:
        release.set()
    assert done.wait(5)
    assert calls == [{"first": True}]


def test_external_response_without_token_does_not_start_heartbeat(client, monkeypatch):
    from unittest.mock import Mock
    installation = create(client)
    monkeypatch.setenv("DEVICE_SERIAL_OVERRIDE", "dev-win-test")
    result = {"_id": installation["_id"], "device": "hardware", "status": "paired"}
    monkeypatch.setattr(device_client.requests, "post", lambda *a, **k: Mock(status_code=200, json=lambda: result))
    identity = device_client.pair(installation["payload"])
    manager = BindingManager()
    manager.identity = identity
    manager._start_heartbeat()
    assert manager._heartbeat is None
    assert identity.device_token == ""


def test_production_scanner_rejects_legacy_signed_qr():
    """The retired signed envelope must not be accepted by the shipping scanner."""
    import json

    import qrcode

    legacy = {
        "schema": "faceguard.provisioning.v1", "command": "provision",
        "server_url": "http://testserver", "provisioning_token": "tok",
        "nonce": "abc123", "sig": "ZmFrZS1zaWduYXR1cmU=", "key_id": "k1",
    }
    image = qrcode.make(json.dumps(legacy)).convert("RGB")
    frame = np.array(image)
    assert QRScanner().scan(frame) is None
